"""Small stdout exporters keep qtop useful in shell pipelines."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from typing import TextIO

from .models import Job


EXPORT_COLUMNS = (
    "job_id",
    "user",
    "state",
    "name",
    "queue",
    "slots",
    "priority",
    "submit_time",
    "start_time",
    "mem_used",
    "mem_efficiency",
    "cpu_efficiency",
)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def job_to_dict(job: Job) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "user": job.user,
        "state": job.state.value,
        "name": job.name,
        "queue": job.queue,
        "slots": job.slots,
        "priority": job.priority,
        "submit_time": _iso_utc(job.submit_time),
        "start_time": _iso_utc(job.start_time),
        "mem_used": job.mem_used_bytes,
        "mem_efficiency": job.mem_efficiency,
        "cpu_efficiency": job.cpu_efficiency,
    }


def emit_jobs(jobs: list[Job], fmt: str, out: TextIO) -> None:
    rows = [job_to_dict(job) for job in jobs]
    if fmt == "json":
        json.dump(rows, out, indent=2)
        out.write("\n")
        return

    if fmt == "csv":
        writer = csv.DictWriter(out, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(_csv_row(row) for row in rows)
        return

    raise ValueError(f"unsupported export format: {fmt}")


def _csv_row(row: dict[str, object]) -> dict[str, object]:
    return {
        key: "" if value is None else value
        for key, value in row.items()
    }
