from __future__ import annotations

import csv
import io
import json
import os
import time
from datetime import datetime, timezone

import pytest

from qtop import cli
from qtop.export import EXPORT_COLUMNS, emit_jobs
from qtop.models import Job, JobState


def _jobs() -> list[Job]:
    return [
        Job(
            job_id="123",
            name="train",
            user="alice",
            state=JobState.RUNNING,
            raw_state="r",
            queue="all.q@node01",
            slots=4,
            priority=0.5,
            submit_time=datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc),
            start_time=datetime(2026, 5, 16, 12, 5, tzinfo=timezone.utc),
            mem_used_bytes=1024,
            mem_efficiency=50.0,
            cpu_efficiency=75.0,
        ),
        Job(
            job_id="124",
            name="pending",
            user="bob",
            state=JobState.WAITING,
            raw_state="qw",
            queue=None,
            slots=1,
            priority=0.0,
            submit_time=None,
            start_time=None,
        ),
    ]


def test_json_output_has_stable_fields_and_preserves_none():
    out = io.StringIO()
    emit_jobs(_jobs(), "json", out)

    data = json.loads(out.getvalue())
    assert list(data[0].keys()) == list(EXPORT_COLUMNS)
    assert len(data) == 2
    assert data[0]["state"] == "running"
    assert data[0]["submit_time"] == "2026-05-16T12:00:00+00:00"
    assert data[0]["start_time"] == "2026-05-16T12:05:00+00:00"
    assert data[1]["queue"] is None
    assert data[1]["start_time"] is None


def test_csv_output_has_stable_header_and_empty_none_values():
    out = io.StringIO()
    emit_jobs(_jobs(), "csv", out)

    rows = list(csv.DictReader(io.StringIO(out.getvalue())))
    assert out.getvalue().splitlines()[0].split(",") == list(EXPORT_COLUMNS)
    assert len(rows) == 2
    assert rows[0]["state"] == "running"
    assert rows[0]["submit_time"] == "2026-05-16T12:00:00+00:00"
    assert rows[1]["queue"] == ""
    assert rows[1]["start_time"] == ""


def test_naive_datetimes_are_treated_as_local_time(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset() is required to exercise local timezone conversion")

    old_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "UTC-02")
    time.tzset()
    try:
        job = _jobs()[0]
        job.submit_time = datetime(2026, 5, 16, 12, 0)

        out = io.StringIO()
        emit_jobs([job], "json", out)

        data = json.loads(out.getvalue())
        assert data[0]["submit_time"] == "2026-05-16T10:00:00+00:00"
    finally:
        if old_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", old_tz)
        time.tzset()


def test_demo_json_export_is_valid_json(capsys):
    rc = cli.main(["--export", "json", "--demo"])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data
    assert set(EXPORT_COLUMNS).issubset(data[0])


def test_demo_csv_export_prints_expected_header(capsys):
    rc = cli.main(["--export", "csv", "--demo"])

    assert rc == 0
    first_line = capsys.readouterr().out.splitlines()[0]
    assert first_line.split(",") == list(EXPORT_COLUMNS)


def test_export_path_does_not_import_app(monkeypatch):
    real_import = __import__

    def fail_on_app_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "qtop.app" or (level == 1 and name == "app"):
            raise AssertionError("export path imported qtop.app")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fail_on_app_import)

    assert cli.main(["--export", "json", "--demo"]) == 0
