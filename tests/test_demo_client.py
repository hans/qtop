"""Guards the dev-without-cluster path."""

from qtop import DemoClient
from qtop.models import Job, JobState


class TestDemoClient:
    def test_fetch_jobs_returns_job_instances(self):
        client = DemoClient()
        jobs = client.fetch_jobs()
        assert jobs
        for j in jobs:
            assert isinstance(j, Job)

    def test_mix_of_states(self):
        client = DemoClient()
        jobs = client.fetch_jobs()
        states = {j.state for j in jobs}
        assert JobState.RUNNING in states
        assert JobState.WAITING in states
        assert JobState.ERROR in states
        assert JobState.HELD in states

    def test_cpu_efficiency_populated_after_two_polls(self):
        client = DemoClient()
        client.fetch_jobs()
        jobs = client.fetch_jobs()
        running = [j for j in jobs if j.state is JobState.RUNNING]
        assert running
        assert any(j.cpu_efficiency is not None for j in running)

    def test_demo_includes_all_three_profiles(self):
        """Mix must include well-behaved, mem-hog, and underspecified jobs.

        Asserted via second-poll efficiency ranges.
        """
        client = DemoClient()
        client.fetch_jobs()
        jobs = client.fetch_jobs()
        running = [j for j in jobs if j.state is JobState.RUNNING]
        # well-behaved: ~80% cpu_eff, ~70% mem_eff
        assert any(
            j.cpu_efficiency and 60 <= j.cpu_efficiency <= 95
            and j.mem_efficiency and 60 <= j.mem_efficiency <= 80
            for j in running
        )
        # mem-hog: low cpu, high mem
        assert any(
            j.cpu_efficiency is not None and j.cpu_efficiency < 25
            and j.mem_efficiency and j.mem_efficiency >= 90
            for j in running
        )
        # underspecified: cpu_eff > 100%
        assert any(
            j.cpu_efficiency and j.cpu_efficiency > 100
            for j in running
        )

    def test_fetch_hosts_returns_hosts(self):
        client = DemoClient()
        hosts = client.fetch_hosts()
        assert hosts
        assert all(h.ncpu > 0 for h in hosts)

    def test_fetch_summary_consistent(self):
        client = DemoClient()
        jobs = client.fetch_jobs()
        hosts = client.fetch_hosts()
        summary = client.fetch_summary(jobs, hosts)
        assert summary.total_jobs == len(jobs)
        assert summary.nodes == len(hosts)
        assert summary.running + summary.waiting + summary.error <= summary.total_jobs

    def test_delete_job_evicts_from_subsequent_fetches(self):
        client = DemoClient()
        jobs = client.fetch_jobs()
        victim = jobs[0]
        ok, _ = client.delete_job(victim.job_id)
        assert ok
        jobs2 = client.fetch_jobs()
        assert victim.job_id not in {j.job_id for j in jobs2}

    def test_user_filter(self):
        client = DemoClient()
        all_jobs = client.fetch_jobs("*")
        users = list({j.user for j in all_jobs})
        chosen = users[0]
        client2 = DemoClient()
        filtered = client2.fetch_jobs(chosen)
        assert all(j.user == chosen for j in filtered)
