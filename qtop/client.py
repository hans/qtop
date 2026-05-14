"""SGE data-fetching API.

This module is the public API for qtop. Both the TUI and any external
script (e.g. a cron-driven notifier) use SGEClient or DemoClient.

SGE state lives here, not in the UI: SGE only emits cumulative CPU
seconds, so per-interval CPU efficiency requires a rolling history of
(timestamp, cpu_seconds) per job. SGEClient owns that history, so any
consumer that polls fetch_jobs() at intervals gets cpu_efficiency for
free.
"""

from __future__ import annotations

import random
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timedelta
from typing import Callable

from .models import (
    ClusterSummary,
    Host,
    Job,
    JobState,
    parse_memory,
)


def _findtext(elem: ET.Element | None, tags: tuple[str, ...], default: str | None = None) -> str | None:
    """Look up the first matching child text from a list of candidate tags.

    SGE XML varies between versions (e.g. JB_job_number vs job_number), so
    every lookup tries multiple tag conventions before giving up.
    """
    if elem is None:
        return default
    for tag in tags:
        v = elem.findtext(tag)
        if v is not None and v != "":
            return v
    return default


def _findint(elem: ET.Element | None, tags: tuple[str, ...], default: int = 0) -> int:
    v = _findtext(elem, tags)
    if v is None:
        return default
    try:
        return int(float(v))
    except ValueError:
        return default


def _findfloat(elem: ET.Element | None, tags: tuple[str, ...]) -> float | None:
    v = _findtext(elem, tags)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_sge_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    # SGE emits ISO-ish like 2026-05-14T10:00:00 or with .000 suffix
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _job_from_xml(jl: ET.Element) -> Job:
    job_id = _findtext(jl, ("JB_job_number", "job_number"), "?") or "?"
    name = _findtext(jl, ("JB_name", "name"), "") or ""
    user = _findtext(jl, ("JB_owner", "owner"), "") or ""
    raw_state = _findtext(jl, ("state",), "") or jl.get("state", "") or ""
    state = JobState.from_sge(raw_state)
    queue = _findtext(jl, ("queue_name",))
    slots = _findint(jl, ("slots",), 1)
    submit_time = _parse_sge_datetime(_findtext(jl, ("JB_submission_time", "submission_time")))
    start_time = _parse_sge_datetime(_findtext(jl, ("JAT_start_time", "start_time")))
    priority = _findfloat(jl, ("JAT_prio", "JB_priority", "priority")) or 0.0

    # Requested resources from -r: <hard_request name="h_vmem">8G</hard_request>
    h_vmem_bytes = None
    mem_free_bytes = None
    for hr in jl.findall("hard_request"):
        rname = (hr.get("name") or "").lower()
        if rname == "h_vmem":
            h_vmem_bytes = parse_memory(hr.text)
        elif rname == "mem_free":
            mem_free_bytes = parse_memory(hr.text)

    # Actual usage from -ext: <JAT_scaled_usage_list><scaled><UA_name>cpu</UA_name><UA_value>123.4</UA_value></scaled>...
    cpu_seconds: float | None = None
    mem_used_bytes: int | None = None
    maxvmem_bytes: int | None = None
    usage_container = jl.find("JAT_scaled_usage_list")
    if usage_container is None:
        usage_container = jl.find("usage")
    if usage_container is not None:
        for entry in list(usage_container):
            # entry can be <scaled>...</scaled> or attribute-form <usage name="cpu">123</usage>
            uname = (
                _findtext(entry, ("UA_name", "name"))
                or entry.get("name")
                or entry.tag
            )
            uvalue = (
                _findtext(entry, ("UA_value", "value"))
                or entry.get("value")
                or entry.text
            )
            if not uname or uvalue is None:
                continue
            uname = uname.lower()
            if uname == "cpu":
                try:
                    cpu_seconds = float(uvalue)
                except ValueError:
                    pass
            elif uname == "mem":
                # SGE 'mem' usage is GB-seconds historically; vmem is a size.
                # We treat 'vmem' / 'maxvmem' as bytes-of-RAM-used, and use
                # whichever is present. Fall back to 'mem' parsed as bytes
                # if nothing else is available.
                if mem_used_bytes is None:
                    mem_used_bytes = parse_memory(uvalue)
            elif uname == "vmem":
                mem_used_bytes = parse_memory(uvalue)
            elif uname == "maxvmem":
                maxvmem_bytes = parse_memory(uvalue)

    return Job(
        job_id=job_id,
        name=name,
        user=user,
        state=state,
        raw_state=raw_state,
        queue=queue,
        slots=slots,
        submit_time=submit_time,
        start_time=start_time,
        priority=priority,
        h_vmem_bytes=h_vmem_bytes,
        mem_free_bytes=mem_free_bytes,
        cpu_seconds=cpu_seconds,
        mem_used_bytes=mem_used_bytes,
        maxvmem_bytes=maxvmem_bytes,
    )


def parse_qstat_xml(xml_text: str) -> list[Job]:
    """Parse `qstat -xml` (with or without -ext -r) output into Job objects.

    Returns [] for empty job lists. Raises RuntimeError if the XML cannot
    be parsed at all.
    """
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"failed to parse qstat XML: {e}") from e
    jobs: list[Job] = []
    # job_list elements can appear under queue_info or job_info (or nested
    # arbitrarily depending on SGE version) — collect them all.
    for jl in root.iter("job_list"):
        jobs.append(_job_from_xml(jl))
    return jobs


def parse_qhost_xml(xml_text: str) -> list[Host]:
    """Parse `qhost -xml` output into Host objects. Skips 'global' pseudo-host."""
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"failed to parse qhost XML: {e}") from e
    hosts: list[Host] = []
    for h in root.iter("host"):
        name = h.get("name") or ""
        if not name or name == "global":
            continue
        arch = ""
        ncpu = 0
        load: float | None = None
        mem_total: int | None = None
        mem_used: int | None = None
        for hv in h.findall("hostvalue"):
            n = hv.get("name") or ""
            v = hv.text or ""
            if n == "arch_string":
                arch = v
            elif n == "num_proc":
                try:
                    ncpu = int(float(v))
                except ValueError:
                    pass
            elif n == "load_avg":
                try:
                    load = float(v)
                except ValueError:
                    pass
            elif n == "mem_total":
                mem_total = parse_memory(v)
            elif n == "mem_used":
                mem_used = parse_memory(v)
        hosts.append(Host(
            name=name, arch=arch, ncpu=ncpu, load=load,
            mem_total_bytes=mem_total, mem_used_bytes=mem_used,
        ))
    return hosts


def parse_qstat_j(text: str) -> dict[str, str]:
    """Parse plain-text `qstat -j <id>` output into a dict.

    Each line is `key: value`. Continuation lines (starting with whitespace)
    are joined onto the previous value. Lines without ':' are ignored.
    """
    result: dict[str, str] = {}
    last_key: str | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0].isspace() and last_key is not None:
            result[last_key] = result[last_key] + " " + line.strip()
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        result[key] = value
        last_key = key
    return result


class SGEClient:
    """Public API for fetching SGE cluster state.

    Maintains a small rolling history of (timestamp, cpu_seconds) per job
    so that fetch_jobs() can compute cpu_efficiency across the interval
    between successive calls.

    The first call for any given job leaves cpu_efficiency=None; subsequent
    calls populate it. History is keyed by job_id and jobs that disappear
    from a poll are evicted.
    """

    def __init__(self, history_size: int = 2, now_fn: Callable[[], float] = time.monotonic):
        self._history_size = history_size
        self._cpu_history: dict[str, deque[tuple[float, float]]] = {}
        self._now_fn = now_fn

    # ---- public API ----

    def fetch_jobs(self, user: str = "*") -> list[Job]:
        """Run qstat, parse, compute efficiencies. Raises RuntimeError on failure."""
        xml = self._run_qstat(user)
        jobs = parse_qstat_xml(xml)
        self._update_efficiency(jobs)
        return jobs

    def fetch_hosts(self) -> list[Host]:
        xml = self._run(["qhost", "-xml"])
        return parse_qhost_xml(xml)

    def fetch_summary(self, jobs: list[Job], hosts: list[Host]) -> ClusterSummary:
        running = sum(1 for j in jobs if j.state is JobState.RUNNING)
        error = sum(1 for j in jobs if j.state is JobState.ERROR)
        waiting = sum(
            1 for j in jobs
            if j.state in (JobState.WAITING, JobState.HELD, JobState.TRANSFERRING)
        )
        slots_used = sum(j.slots for j in jobs if j.state is JobState.RUNNING)
        slots_total = sum(h.ncpu for h in hosts)
        return ClusterSummary(
            total_jobs=len(jobs),
            running=running,
            waiting=waiting,
            error=error,
            slots_used=slots_used,
            slots_total=slots_total,
            nodes=len(hosts),
        )

    def fetch_job_detail(self, job_id: str) -> dict[str, str]:
        text = self._run(["qstat", "-j", str(job_id)])
        return parse_qstat_j(text)

    def delete_job(self, job_id: str) -> tuple[bool, str]:
        try:
            res = subprocess.run(
                ["qdel", str(job_id)],
                capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            return False, "qdel not found on PATH"
        except subprocess.TimeoutExpired:
            return False, "qdel timed out"
        if res.returncode != 0:
            return False, (res.stderr or res.stdout).strip()
        return True, (res.stdout or "").strip()

    # ---- stateful efficiency computation ----

    def _update_efficiency(self, jobs: list[Job]) -> None:
        now = self._now_fn()
        seen: set[str] = set()
        for j in jobs:
            seen.add(j.job_id)
            # mem efficiency: pure ratio, no history needed
            req = j.mem_request_bytes
            if j.mem_used_bytes is not None and req is not None and req > 0:
                j.mem_efficiency = j.mem_used_bytes / req * 100.0
            else:
                j.mem_efficiency = None

            if j.cpu_seconds is None or j.state is not JobState.RUNNING:
                # don't bother tracking history for non-running jobs
                continue

            hist = self._cpu_history.setdefault(j.job_id, deque(maxlen=self._history_size))
            if hist:
                prev_t, prev_cpu = hist[-1]
                dt = now - prev_t
                dcpu = j.cpu_seconds - prev_cpu
                if dt > 0 and j.slots > 0:
                    j.cpu_efficiency = max(0.0, dcpu / (dt * j.slots) * 100.0)
            hist.append((now, j.cpu_seconds))

        # evict jobs that disappeared
        for stale_id in list(self._cpu_history.keys()):
            if stale_id not in seen:
                del self._cpu_history[stale_id]

    # ---- subprocess plumbing ----

    def _run_qstat(self, user: str) -> str:
        """Run qstat with the preferred verbose flags, falling back if unsupported."""
        # Preferred: extended usage + resource requests in one call
        try:
            return self._run(["qstat", "-ext", "-r", "-xml", "-u", user])
        except RuntimeError:
            pass
        # Fall back to plain XML (no usage / requests)
        return self._run(["qstat", "-xml", "-u", user])

    def _run(self, cmd: list[str]) -> str:
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"{cmd[0]} not found on PATH") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"{' '.join(cmd)} timed out") from e
        if res.returncode != 0:
            raise RuntimeError(
                f"{' '.join(cmd)} exited {res.returncode}: {res.stderr.strip()}"
            )
        return res.stdout


# --------------------------------------------------------------------------- #
# DemoClient
# --------------------------------------------------------------------------- #


_DEMO_USERS = ["alice", "bob", "carol", "dave", "erin"]
_DEMO_QUEUES = ["all.q@node01", "all.q@node02", "gpu.q@gpu01", "long.q@node03"]


class _DemoJobSpec:
    __slots__ = ("job_id", "name", "user", "state", "slots", "h_vmem",
                 "cpu_per_sec", "mem_used", "profile", "submit_offset",
                 "start_offset")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class DemoClient(SGEClient):
    """Drop-in replacement that generates plausible synthetic SGE state.

    Designed to exercise every UI code path: running/waiting/error jobs,
    and an efficiency mix of well-behaved / mem-hog / underspecified.
    Each fetch_jobs() call advances simulated cpu_seconds for running jobs
    so cpu_efficiency stabilises after one tick.
    """

    def __init__(self, history_size: int = 2, now_fn: Callable[[], float] | None = None,
                 seed: int = 42):
        # the DemoClient drives time itself via _sim_clock so cpu deltas are
        # deterministic and don't depend on real wall-clock between calls.
        self._sim_clock = 0.0
        super().__init__(history_size=history_size, now_fn=self._tick_clock)
        if now_fn is not None:
            self._now_fn = now_fn
        self._rng = random.Random(seed)
        self._tick_count = 0
        self._specs = self._build_specs()
        self._deleted: set[str] = set()

    def _tick_clock(self) -> float:
        return self._sim_clock

    def _build_specs(self) -> list[_DemoJobSpec]:
        rng = self._rng
        specs: list[_DemoJobSpec] = []
        # well-behaved: ~80% CPU eff, ~70% mem eff
        for i in range(4):
            slots = rng.choice([1, 2, 4, 8])
            h_vmem = rng.choice([4, 8, 16]) * 1024 ** 3
            specs.append(_DemoJobSpec(
                job_id=str(1001 + i),
                name=f"train_{i:02d}",
                user=rng.choice(_DEMO_USERS),
                state="r",
                slots=slots,
                h_vmem=h_vmem,
                cpu_per_sec=0.8 * slots,
                mem_used=int(0.7 * h_vmem),
                profile="well",
                submit_offset=3600 + i * 60,
                start_offset=3300 + i * 60,
            ))
        # mem-hogs: low CPU eff, high mem eff (>95% of request, sometimes over)
        for i in range(2):
            slots = rng.choice([2, 4])
            h_vmem = 8 * 1024 ** 3
            specs.append(_DemoJobSpec(
                job_id=str(1101 + i),
                name=f"memhog_{i:02d}",
                user=rng.choice(_DEMO_USERS),
                state="r",
                slots=slots,
                h_vmem=h_vmem,
                cpu_per_sec=0.15 * slots,
                mem_used=int((0.95 + 0.05 * i) * h_vmem),
                profile="memhog",
                submit_offset=7200,
                start_offset=7000,
            ))
        # underspecified: high CPU eff (>100% of slots), low mem
        for i in range(2):
            slots = 1
            h_vmem = 2 * 1024 ** 3
            specs.append(_DemoJobSpec(
                job_id=str(1201 + i),
                name=f"underspec_{i:02d}",
                user=rng.choice(_DEMO_USERS),
                state="r",
                slots=slots,
                h_vmem=h_vmem,
                cpu_per_sec=1.8 * slots,  # using 1.8x what was requested
                mem_used=int(0.2 * h_vmem),
                profile="underspec",
                submit_offset=1800,
                start_offset=1700,
            ))
        # waiting jobs (no usage)
        for i in range(3):
            slots = rng.choice([4, 8, 16])
            specs.append(_DemoJobSpec(
                job_id=str(1301 + i),
                name=f"pending_{i:02d}",
                user=rng.choice(_DEMO_USERS),
                state="qw",
                slots=slots,
                h_vmem=8 * 1024 ** 3,
                cpu_per_sec=0.0,
                mem_used=0,
                profile="waiting",
                submit_offset=300 + i * 60,
                start_offset=0,
            ))
        # error jobs
        specs.append(_DemoJobSpec(
            job_id="1401",
            name="failing_job",
            user=rng.choice(_DEMO_USERS),
            state="Eqw",
            slots=2,
            h_vmem=4 * 1024 ** 3,
            cpu_per_sec=0.0,
            mem_used=0,
            profile="error",
            submit_offset=120,
            start_offset=0,
        ))
        # held
        specs.append(_DemoJobSpec(
            job_id="1402",
            name="held_job",
            user=rng.choice(_DEMO_USERS),
            state="hqw",
            slots=1,
            h_vmem=2 * 1024 ** 3,
            cpu_per_sec=0.0,
            mem_used=0,
            profile="held",
            submit_offset=60,
            start_offset=0,
        ))
        return specs

    # ---- override the network/subprocess methods ----

    def fetch_jobs(self, user: str = "*") -> list[Job]:
        # Advance the simulation clock by ~10s on each call so cpu_seconds
        # progresses deterministically between fetches.
        interval = 10.0
        self._sim_clock += interval
        self._tick_count += 1

        now = datetime.now()
        jobs: list[Job] = []
        for spec in self._specs:
            if spec.job_id in self._deleted:
                continue
            if user not in ("*", "", spec.user) and user != spec.user:
                continue
            cpu_seconds = (
                spec.cpu_per_sec * self._sim_clock if spec.state == "r" else None
            )
            mem_used_bytes = spec.mem_used if spec.state == "r" else None
            jobs.append(Job(
                job_id=spec.job_id,
                name=spec.name,
                user=spec.user,
                state=JobState.from_sge(spec.state),
                raw_state=spec.state,
                queue=self._rng.choice(_DEMO_QUEUES) if spec.state == "r" else None,
                slots=spec.slots,
                submit_time=now - timedelta(seconds=spec.submit_offset),
                start_time=(
                    now - timedelta(seconds=spec.start_offset)
                    if spec.state == "r" else None
                ),
                priority=0.5,
                h_vmem_bytes=spec.h_vmem,
                mem_free_bytes=None,
                cpu_seconds=cpu_seconds,
                mem_used_bytes=mem_used_bytes,
                maxvmem_bytes=mem_used_bytes,
            ))
        self._update_efficiency(jobs)
        return jobs

    def fetch_hosts(self) -> list[Host]:
        return [
            Host(name="node01", arch="lx-amd64", ncpu=32, load=0.55,
                 mem_total_bytes=128 * 1024 ** 3, mem_used_bytes=72 * 1024 ** 3),
            Host(name="node02", arch="lx-amd64", ncpu=32, load=0.91,
                 mem_total_bytes=128 * 1024 ** 3, mem_used_bytes=98 * 1024 ** 3),
            Host(name="node03", arch="lx-amd64", ncpu=64, load=0.30,
                 mem_total_bytes=256 * 1024 ** 3, mem_used_bytes=40 * 1024 ** 3),
            Host(name="gpu01", arch="lx-amd64", ncpu=16, load=0.75,
                 mem_total_bytes=256 * 1024 ** 3, mem_used_bytes=180 * 1024 ** 3),
        ]

    def fetch_job_detail(self, job_id: str) -> dict[str, str]:
        for spec in self._specs:
            if spec.job_id == job_id:
                return {
                    "job_number": spec.job_id,
                    "job_name": spec.name,
                    "owner": spec.user,
                    "state": spec.state,
                    "slots": str(spec.slots),
                    "hard_resource_list": f"h_vmem={spec.h_vmem // 1024**3}G",
                    "profile": spec.profile,
                    "cpu_per_sec": f"{spec.cpu_per_sec:.2f}",
                    "mem_used": f"{spec.mem_used / 1024**3:.2f}G",
                }
        return {"error": f"job {job_id} not found"}

    def delete_job(self, job_id: str) -> tuple[bool, str]:
        if any(s.job_id == job_id for s in self._specs):
            self._deleted.add(job_id)
            return True, f"demo: job {job_id} marked as deleted"
        return False, f"job {job_id} not found"


def qstat_available() -> bool:
    """Return True if the qstat binary is on PATH."""
    return shutil.which("qstat") is not None
