#!/usr/bin/env python3
"""Detect SGE jobs that are using more CPU than their requested slots.

Two polls are made within a single invocation (sleeping between them) so
that cpu_efficiency is populated. _cpu_history is in-memory on the
SGEClient, so persisting state across cron invocations is left to the
consumer if true cross-run state is needed.

Usage:
    python examples/notify_overuse.py [--demo] [--threshold PCT] [--gap SEC]
"""

from __future__ import annotations

import argparse
import sys
import time

from qtop import DemoClient, SGEClient, qstat_available


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true",
                        help="Use DemoClient instead of real qstat.")
    parser.add_argument("--threshold", type=float, default=150.0,
                        help="cpu_efficiency %% above which to report (default 150).")
    parser.add_argument("--gap", type=float, default=30.0,
                        help="Seconds between polls (default 30).")
    parser.add_argument("--user", default="*",
                        help="User filter (default '*' = all).")
    args = parser.parse_args()

    if args.demo:
        client = DemoClient()
    elif not qstat_available():
        print("qstat not on PATH; use --demo to test.", file=sys.stderr)
        return 1
    else:
        client = SGEClient()

    # First poll seeds the cpu_history; we don't expect cpu_efficiency yet.
    client.fetch_jobs(args.user)
    time.sleep(args.gap)
    jobs = client.fetch_jobs(args.user)

    flagged = [
        j for j in jobs
        if j.cpu_efficiency is not None and j.cpu_efficiency > args.threshold
    ]
    if not flagged:
        print(f"OK: no jobs exceeding {args.threshold:.0f}% of requested slots.")
        return 0

    print(f"Found {len(flagged)} job(s) exceeding {args.threshold:.0f}% CPU efficiency:")
    for j in flagged:
        print(
            f"  OVERUSE job={j.job_id} user={j.user} name={j.name} "
            f"slots={j.slots} cpu_eff={j.cpu_efficiency:.0f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
