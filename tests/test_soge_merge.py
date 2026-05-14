"""Tests for the SoGE (arc.liv.ac.uk) schema and the two-call merge.

This SGE fork has two surprises addressed in client.py:

  1. `-ext -r -xml` exposes usage as inline tags <cpu_usage>, <mem_usage>
     on <job_list> rather than a <JAT_scaled_usage_list> block.
  2. `-ext -r -xml` DROPS <JAT_start_time>. To get both usage and elapsed
     we run both qstat calls and merge them.
"""

from datetime import datetime, timedelta

import pytest

from qtop.client import (
    _merge_ext_into_base,
    _populate_mem_used_from_gb_seconds,
    parse_qstat_xml,
)
from qtop.models import JobState


class TestParseSoGEBase:
    """Plain `qstat -xml` on this fork: state + start_time, no usage."""

    def test_jobs_and_start_times(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_xml_soge.xml"))
        assert {j.job_id for j in jobs} == {"916592", "807314"}
        j = next(j for j in jobs if j.job_id == "916592")
        assert j.start_time == datetime(2026, 5, 14, 9, 58, 24)
        assert j.slots == 4
        assert j.queue == "pia-batch.q@pia"
        # No usage in the base call
        assert j.cpu_seconds is None
        assert j.mem_usage_gb_seconds is None

    def test_dr_state_recognised_as_deleting(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_xml_soge.xml"))
        j = next(j for j in jobs if j.job_id == "807314")
        assert j.state is JobState.DELETING


class TestParseSoGEExt:
    """`qstat -ext -r -xml` on this fork: inline usage, hard_request, NO start_time."""

    def test_inline_cpu_usage_populates_cpu_seconds(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_ext_r_soge.xml"))
        j = next(j for j in jobs if j.job_id == "916592")
        assert j.cpu_seconds == pytest.approx(19014.0)

    def test_inline_mem_usage_populates_gb_seconds(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_ext_r_soge.xml"))
        j = next(j for j in jobs if j.job_id == "916592")
        # mem_usage is GiB-seconds, NOT bytes — stored raw, converted later
        assert j.mem_usage_gb_seconds == pytest.approx(234829.08516)
        assert j.mem_used_bytes is None  # not derived yet

    def test_hard_requests_parsed(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_ext_r_soge.xml"))
        j = next(j for j in jobs if j.job_id == "916592")
        assert j.h_vmem_bytes == 8 * 1024 ** 3
        j2 = next(j for j in jobs if j.job_id == "807314")
        assert j2.h_vmem_bytes == 32000 * 1024 ** 2  # 32000M

    def test_start_time_absent(self, load_fixture):
        """The extended call doesn't include JAT_start_time on this fork."""
        jobs = parse_qstat_xml(load_fixture("qstat_ext_r_soge.xml"))
        for j in jobs:
            assert j.start_time is None


class TestMerge:
    def test_merge_copies_usage_and_requests(self, load_fixture):
        base = parse_qstat_xml(load_fixture("qstat_xml_soge.xml"))
        ext = parse_qstat_xml(load_fixture("qstat_ext_r_soge.xml"))
        _merge_ext_into_base(base, {j.job_id: j for j in ext})
        j = next(j for j in base if j.job_id == "916592")
        # Came from base
        assert j.start_time == datetime(2026, 5, 14, 9, 58, 24)
        # Came from ext
        assert j.cpu_seconds == pytest.approx(19014.0)
        assert j.mem_usage_gb_seconds == pytest.approx(234829.08516)
        assert j.h_vmem_bytes == 8 * 1024 ** 3

    def test_merge_no_match_leaves_base_alone(self):
        from qtop.models import Job

        base = [Job(job_id="A", name="a", user="u", state=JobState.RUNNING,
                    raw_state="r", slots=1)]
        _merge_ext_into_base(base, {})  # nothing to merge
        assert base[0].cpu_seconds is None


class TestMemUsedFromGbSeconds:
    def test_converts_to_running_average(self):
        from qtop.models import Job

        # 100 GiB-seconds over 10 seconds → average 10 GiB → 10 * 1024^3 bytes
        j = Job(job_id="X", name="x", user="u", state=JobState.RUNNING,
                raw_state="r", slots=1,
                start_time=datetime(2026, 5, 14, 10, 0, 0),
                mem_usage_gb_seconds=100.0)
        now = datetime(2026, 5, 14, 10, 0, 10)
        _populate_mem_used_from_gb_seconds([j], now)
        assert j.mem_used_bytes == 10 * 1024 ** 3

    def test_skipped_when_already_set(self):
        from qtop.models import Job

        j = Job(job_id="X", name="x", user="u", state=JobState.RUNNING,
                raw_state="r", slots=1,
                start_time=datetime(2026, 5, 14, 10, 0, 0),
                mem_usage_gb_seconds=100.0,
                mem_used_bytes=999)  # direct vmem wins
        now = datetime(2026, 5, 14, 10, 0, 10)
        _populate_mem_used_from_gb_seconds([j], now)
        assert j.mem_used_bytes == 999

    def test_skipped_when_no_start_time(self):
        from qtop.models import Job

        j = Job(job_id="X", name="x", user="u", state=JobState.RUNNING,
                raw_state="r", slots=1,
                start_time=None,
                mem_usage_gb_seconds=100.0)
        _populate_mem_used_from_gb_seconds([j], datetime.now())
        assert j.mem_used_bytes is None


class TestDatetimeFormats:
    def test_iso_format(self):
        from qtop.client import _parse_sge_datetime
        assert _parse_sge_datetime("2026-05-14T10:00:00") == datetime(2026, 5, 14, 10, 0, 0)

    def test_qstat_j_format(self):
        from qtop.client import _parse_sge_datetime
        assert _parse_sge_datetime("Mon Apr 20 13:40:30 2026") == datetime(2026, 4, 20, 13, 40, 30)

    def test_epoch_seconds(self):
        from qtop.client import _parse_sge_datetime
        # 1716234567 = 2024-05-20 19:09:27 UTC, but we use fromtimestamp (local)
        result = _parse_sge_datetime("1716234567")
        assert result is not None
        assert result.year == 2024

    def test_epoch_milliseconds(self):
        from qtop.client import _parse_sge_datetime
        result = _parse_sge_datetime("1716234567000")
        assert result is not None
        assert result.year == 2024

    def test_dash_returns_none(self):
        from qtop.client import _parse_sge_datetime
        assert _parse_sge_datetime("-") is None
        assert _parse_sge_datetime("0") is None
        assert _parse_sge_datetime("NONE") is None
