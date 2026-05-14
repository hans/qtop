"""Behaviour tests for the rolling CPU-history efficiency computation.

This is the only piece of stateful logic on SGEClient, so the tests
inject a controllable clock via the now_fn seam.
"""

import pytest

from qtop.client import SGEClient
from qtop.models import Job, JobState


def _job(job_id="J1", state=JobState.RUNNING, slots=4,
         cpu=None, mem_used=None, h_vmem=None) -> Job:
    return Job(
        job_id=job_id,
        name="x",
        user="u",
        state=state,
        raw_state="r" if state is JobState.RUNNING else "qw",
        slots=slots,
        cpu_seconds=cpu,
        mem_used_bytes=mem_used,
        h_vmem_bytes=h_vmem,
    )


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float):
        self.t += dt


class TestCPUEfficiency:
    def test_first_poll_yields_none(self):
        clock = FakeClock()
        client = SGEClient(now_fn=clock)
        j = _job(cpu=100.0, slots=4)
        client._update_efficiency([j])
        assert j.cpu_efficiency is None
        assert "J1" in client._cpu_history
        assert len(client._cpu_history["J1"]) == 1

    def test_second_poll_computes_percent(self):
        clock = FakeClock()
        client = SGEClient(now_fn=clock)
        j1 = _job(cpu=100.0, slots=4)
        client._update_efficiency([j1])
        clock.advance(10.0)
        j2 = _job(cpu=108.0, slots=4)  # +8 cpu-seconds over 10s with 4 slots
        client._update_efficiency([j2])
        # 8 / (10 * 4) = 0.2 → 20%
        assert j2.cpu_efficiency == pytest.approx(20.0)

    def test_third_poll_zero_delta_is_zero_percent(self):
        clock = FakeClock()
        client = SGEClient(now_fn=clock)
        client._update_efficiency([_job(cpu=100.0)])
        clock.advance(10.0)
        client._update_efficiency([_job(cpu=108.0)])
        clock.advance(10.0)
        j3 = _job(cpu=108.0)  # no progress
        client._update_efficiency([j3])
        assert j3.cpu_efficiency == pytest.approx(0.0)

    def test_zero_slots_does_not_divide(self):
        clock = FakeClock()
        client = SGEClient(now_fn=clock)
        client._update_efficiency([_job(cpu=10.0, slots=0)])
        clock.advance(10.0)
        j = _job(cpu=20.0, slots=0)
        client._update_efficiency([j])
        assert j.cpu_efficiency is None  # no crash, no value

    def test_evicts_disappeared_jobs(self):
        clock = FakeClock()
        client = SGEClient(now_fn=clock)
        client._update_efficiency([_job("A", cpu=10.0), _job("B", cpu=20.0)])
        assert set(client._cpu_history) == {"A", "B"}
        clock.advance(10.0)
        # only A reports this time → B should be evicted
        client._update_efficiency([_job("A", cpu=15.0)])
        assert set(client._cpu_history) == {"A"}

    def test_non_running_state_not_tracked(self):
        clock = FakeClock()
        client = SGEClient(now_fn=clock)
        j = _job(state=JobState.WAITING, cpu=None)
        client._update_efficiency([j])
        assert "J1" not in client._cpu_history
        assert j.cpu_efficiency is None

    def test_overuse_can_exceed_100(self):
        """An underspecified job using >1.0 cores per slot should report >100%."""
        clock = FakeClock()
        client = SGEClient(now_fn=clock)
        client._update_efficiency([_job(cpu=0.0, slots=1)])
        clock.advance(10.0)
        # 18 cpu-seconds over 10s on 1 slot = 180%
        j = _job(cpu=18.0, slots=1)
        client._update_efficiency([j])
        assert j.cpu_efficiency == pytest.approx(180.0)


class TestMemEfficiency:
    """`h_vmem` is per-slot in SGE; efficiency divides by `slots * h_vmem`."""

    def test_single_slot_uses_raw_h_vmem(self):
        client = SGEClient()
        j = _job(slots=1, mem_used=4 * 1024 ** 3, h_vmem=8 * 1024 ** 3)
        client._update_efficiency([j])
        assert j.mem_efficiency == pytest.approx(50.0)  # 4 / (1*8) = 50%

    def test_multi_slot_multiplies_request(self):
        """The bug-fix case: 4-slot job with 8G per slot is 32G total."""
        client = SGEClient()
        # 16 GiB total used out of 32 GiB reserved → 50%
        j = _job(slots=4, mem_used=16 * 1024 ** 3, h_vmem=8 * 1024 ** 3)
        client._update_efficiency([j])
        assert j.mem_efficiency == pytest.approx(50.0)
        # The old (broken) calculation would have produced 200% here.

    def test_none_when_request_missing(self):
        client = SGEClient()
        j = _job(mem_used=4 * 1024 ** 3, h_vmem=None)
        client._update_efficiency([j])
        assert j.mem_efficiency is None

    def test_none_when_used_missing(self):
        client = SGEClient()
        j = _job(mem_used=None, h_vmem=8 * 1024 ** 3)
        client._update_efficiency([j])
        assert j.mem_efficiency is None
