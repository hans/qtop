from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class JobState(str, Enum):
    RUNNING = "running"
    WAITING = "waiting"
    ERROR = "error"
    HELD = "held"
    SUSPENDED = "suspended"
    TRANSFERRING = "transferring"
    DELETING = "deleting"
    UNKNOWN = "unknown"

    @classmethod
    def from_sge(cls, code: str | None) -> "JobState":
        if not code:
            return cls.UNKNOWN
        c = code.strip()
        if "E" in c:
            return cls.ERROR
        if "h" in c:
            return cls.HELD
        if "d" in c:
            return cls.DELETING
        if "t" in c or "T" in c:
            return cls.TRANSFERRING
        if "s" in c or "S" in c:
            return cls.SUSPENDED
        if "r" in c:
            return cls.RUNNING
        if "q" in c or "w" in c:
            return cls.WAITING
        return cls.UNKNOWN


@dataclass
class Job:
    job_id: str
    name: str
    user: str
    state: JobState
    raw_state: str
    queue: str | None = None
    slots: int = 1
    submit_time: datetime | None = None
    start_time: datetime | None = None
    priority: float = 0.0
    h_vmem_bytes: int | None = None
    mem_free_bytes: int | None = None
    cpu_seconds: float | None = None
    mem_used_bytes: int | None = None
    maxvmem_bytes: int | None = None
    # Some SGE variants emit <mem_usage> in cumulative GiB-seconds rather than
    # an instantaneous size. Stored raw here; SGEClient converts it to
    # mem_used_bytes (running average) after start_time is merged in.
    mem_usage_gb_seconds: float | None = None
    cpu_efficiency: float | None = None
    mem_efficiency: float | None = None

    @property
    def is_running(self) -> bool:
        return self.state is JobState.RUNNING

    @property
    def mem_request_bytes(self) -> int | None:
        # h_vmem takes priority over mem_free for "requested" memory display
        return self.h_vmem_bytes if self.h_vmem_bytes is not None else self.mem_free_bytes


@dataclass
class Host:
    name: str
    arch: str = ""
    ncpu: int = 0
    load: float | None = None
    mem_total_bytes: int | None = None
    mem_used_bytes: int | None = None


@dataclass
class ClusterSummary:
    total_jobs: int = 0
    running: int = 0
    waiting: int = 0
    error: int = 0
    slots_used: int = 0
    slots_total: int = 0
    nodes: int = 0
    status: str | None = None


_MEM_SUFFIXES = {
    "K": 1024,
    "M": 1024 ** 2,
    "G": 1024 ** 3,
    "T": 1024 ** 4,
    "P": 1024 ** 5,
}


def parse_memory(value: str | int | float | None) -> int | None:
    """Parse SGE memory string ('8.500G', '512M', '1024K', '1024') -> bytes.

    Returns None for empty / 'N/A' / 'NONE' / unparseable input. SGE uses
    binary (1024-based) suffixes here, even though some tools also emit
    'Gi' explicitly; we accept either form.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    s = value.strip()
    if not s or s.upper() in {"N/A", "NONE", "INFINITY", "-"}:
        return None
    # Strip an optional trailing 'i' for binary suffixes (Gi, Mi, etc.)
    suffix = s[-1].upper()
    if suffix == "I" and len(s) >= 2:
        suffix = s[-2].upper()
        body = s[:-2]
    elif suffix in _MEM_SUFFIXES:
        body = s[:-1]
    else:
        suffix = ""
        body = s
    try:
        n = float(body)
    except ValueError:
        return None
    multiplier = _MEM_SUFFIXES.get(suffix, 1)
    return int(n * multiplier)


def format_bytes(b: int | None) -> str:
    """Inverse of parse_memory, for display. Returns a compact human form."""
    if b is None:
        return "-"
    if b < 1024:
        return f"{b}B"
    for suffix, mult in (("T", 1024 ** 4), ("G", 1024 ** 3), ("M", 1024 ** 2), ("K", 1024)):
        if b >= mult:
            return f"{b / mult:.1f}{suffix}"
    return f"{b}B"
