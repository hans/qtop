"""CLI entry point for qtop."""

from __future__ import annotations

import argparse
import os
import sys

from .client import DemoClient, SGEClient, qstat_available


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qtop",
        description="Terminal TUI for monitoring SGE (Sun Grid Engine) cluster jobs.",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--user", "-u",
        help="Filter jobs to this user (default: $USER). Use '*' for all users.",
    )
    g.add_argument(
        "--all", "-a",
        action="store_true",
        help="Show jobs from all users (equivalent to --user '*').",
    )
    p.add_argument(
        "--interval", "-i",
        type=float, default=10.0,
        help="Auto-refresh interval in seconds (default: 10).",
    )
    p.add_argument(
        "--demo",
        action="store_true",
        help="Run with synthetic data, no SGE cluster required.",
    )
    p.add_argument(
        "--export",
        choices=("json", "csv"),
        help="Print jobs to stdout in the chosen format and exit (no TUI).",
    )
    return p


def resolve_user(args: argparse.Namespace) -> str:
    if args.all:
        return "*"
    if args.user:
        return args.user
    return os.environ.get("USER", "*")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    user = resolve_user(args)
    if args.demo and not args.user and not args.all:
        user = "*"

    if args.demo:
        client = DemoClient()
    else:
        if not qstat_available():
            print(
                "qtop: qstat not found on PATH. "
                "Run with --demo to launch with synthetic data.",
                file=sys.stderr,
            )
            return 1
        client = SGEClient()

    if args.export:
        from .export import emit_jobs

        try:
            jobs = client.fetch_jobs(user=user)
        except RuntimeError as exc:
            print(f"qtop: {exc}", file=sys.stderr)
            return 1
        emit_jobs(jobs, args.export, sys.stdout)
        return 0

    # Lazy import so --help / error paths don't require Textual.
    from .app import QtopApp

    app = QtopApp(client=client, user=user, interval=args.interval, demo=args.demo)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
