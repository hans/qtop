import pytest

from qtop.client import parse_qhost_xml, parse_qstat_j, parse_qstat_xml
from qtop.models import JobState


class TestParseQstatExtR:
    def test_returns_all_jobs(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_ext_r.xml"))
        assert {j.job_id for j in jobs} == {"5001", "5002", "5010", "5011"}

    def test_running_job_fields(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_ext_r.xml"))
        j = next(j for j in jobs if j.job_id == "5001")
        assert j.name == "train_alpha"
        assert j.user == "alice"
        assert j.state is JobState.RUNNING
        assert j.slots == 4
        assert j.queue == "all.q@node01"
        assert j.h_vmem_bytes == 8 * 1024 ** 3
        assert j.mem_free_bytes == 4 * 1024 ** 3
        assert j.cpu_seconds == pytest.approx(1234.5)
        assert j.mem_used_bytes == pytest.approx(int(5.5 * 1024 ** 3), rel=0.01)
        assert j.maxvmem_bytes == pytest.approx(int(6.25 * 1024 ** 3), rel=0.01)
        assert j.submit_time is not None
        assert j.start_time is not None

    def test_waiting_job_has_no_usage(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_ext_r.xml"))
        j = next(j for j in jobs if j.job_id == "5010")
        assert j.state is JobState.WAITING
        assert j.cpu_seconds is None
        assert j.mem_used_bytes is None
        assert j.start_time is None

    def test_error_job_state(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_ext_r.xml"))
        j = next(j for j in jobs if j.job_id == "5011")
        assert j.state is JobState.ERROR


class TestParseQstatLegacy:
    """Older SGE: <job_number> instead of <JB_job_number>, no usage block."""

    def test_parses_both_tag_conventions(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_legacy.xml"))
        assert {j.job_id for j in jobs} == {"9001", "9002"}
        j = next(j for j in jobs if j.job_id == "9001")
        assert j.name == "legacy_run"
        assert j.user == "erin"
        assert j.slots == 2

    def test_usage_fields_none_when_absent(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_legacy.xml"))
        for j in jobs:
            assert j.cpu_seconds is None
            assert j.mem_used_bytes is None


class TestParseQstatEmptyAndMalformed:
    def test_empty_returns_empty_list(self, load_fixture):
        assert parse_qstat_xml(load_fixture("qstat_empty.xml")) == []

    def test_blank_string_returns_empty_list(self):
        assert parse_qstat_xml("") == []
        assert parse_qstat_xml("   ") == []

    def test_malformed_raises_runtime_error(self, load_fixture):
        with pytest.raises(RuntimeError, match="parse"):
            parse_qstat_xml(load_fixture("qstat_malformed.xml"))


class TestParseQstatResilience:
    """Missing fields must not crash; we want .get()-with-defaults throughout."""

    def test_job_with_only_id_parses(self, load_fixture):
        jobs = parse_qstat_xml(load_fixture("qstat_missing_fields.xml"))
        assert len(jobs) == 1
        j = jobs[0]
        assert j.job_id == "7777"
        # missing slots → default 1; missing name/user → empty strings
        assert j.slots == 1
        assert j.name == ""
        assert j.user == ""
        assert j.h_vmem_bytes is None


class TestParseQhost:
    def test_returns_three_hosts_skipping_global(self, load_fixture):
        hosts = parse_qhost_xml(load_fixture("qhost.xml"))
        assert {h.name for h in hosts} == {"node01", "node02", "gpu01"}

    def test_host_fields(self, load_fixture):
        hosts = parse_qhost_xml(load_fixture("qhost.xml"))
        n01 = next(h for h in hosts if h.name == "node01")
        assert n01.ncpu == 32
        assert n01.load == pytest.approx(0.55)
        assert n01.mem_total_bytes == 128 * 1024 ** 3
        assert n01.mem_used_bytes == 72 * 1024 ** 3

    def test_unparseable_load_becomes_none(self, load_fixture):
        # gpu01 has load_avg "-"
        hosts = parse_qhost_xml(load_fixture("qhost.xml"))
        gpu = next(h for h in hosts if h.name == "gpu01")
        assert gpu.load is None


class TestParseQstatJ:
    def test_key_value_lines_become_dict(self, load_fixture):
        d = parse_qstat_j(load_fixture("qstat_j_12345.txt"))
        assert d["job_number"] == "12345"
        assert d["owner"] == "alice"
        assert d["job_name"] == "train_alpha"
        assert d["project"] == "deep-learning"

    def test_continuation_lines_join(self, load_fixture):
        d = parse_qstat_j(load_fixture("qstat_j_12345.txt"))
        # env_list spans two lines via leading whitespace
        assert "PATH=/usr/bin:/bin" in d["env_list"]
        assert "HOME=/home/alice" in d["env_list"]

    def test_lines_without_colon_ignored(self):
        d = parse_qstat_j("===========\nkey: value\nnocolon line\n")
        assert d == {"key": "value"}
