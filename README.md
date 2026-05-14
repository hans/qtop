# qtop

Terminal TUI for monitoring SGE (Sun Grid Engine) cluster jobs — htop-style
panels for jobs, slot utilization, and per-job CPU/memory efficiency.

`qtop` is two things in one package:

1. **The TUI** (`qtop`) — interactive monitor with sortable tables,
   filtering, color-coded states, and job-deletion confirmation.
2. **A reusable Python API** (`from qtop import SGEClient`) so other
   scripts — cron jobs, dashboards, notifiers — can poll the same
   underlying data without standing up a TUI.

## Install

```sh
uv sync               # creates .venv and installs the package
uv sync --extra dev   # also installs pytest / pytest-textual-snapshot
```

This makes `qtop` available on `PATH` inside the venv (`uv run qtop`).

## Run

```sh
uv run qtop                       # monitor your own jobs
uv run qtop --all                 # monitor all users
uv run qtop --user alice          # filter to a specific user
uv run qtop --interval 5          # refresh every 5s (default 10)
uv run qtop --demo                # synthetic data; no SGE cluster needed
```

`qtop` exits non-zero with a clear error if `qstat` is not on PATH and
`--demo` was not passed.

### Key bindings

| Key   | Action                                  |
|-------|-----------------------------------------|
| `q`   | quit                                    |
| `r`   | refresh now                             |
| `/`   | filter by name / user / state           |
| `s`   | cycle sort column                       |
| click | (column header) sort by that column     |
| `d`   | delete selected job (confirmation `y/N`)|
| `u`   | toggle all-users / my-jobs              |
| `e`   | toggle efficiency panel                 |
| Enter | open / close detail panel               |
| Esc   | close detail panel / cancel             |

### Color scheme (dark terminals)

- **green** — running jobs, ≥75% efficiency
- **yellow** — waiting jobs, 25–74% efficiency
- **red** — error jobs, <25% efficiency
- **dim** — held / suspended

### Efficiency

The efficiency panel shows running jobs only.

- **Memory** efficiency = `mem_used / (slots * h_vmem) * 100`. SGE
  reserves `h_vmem` *per slot*, so a `-pe smp 4 -l h_vmem=8G` job has
  32G reserved, not 8G — multi-slot jobs would otherwise show >100%
  efficiency when they're actually under-using their reservation.
  Sourced from `qstat -ext -r -xml`.
- **CPU** efficiency = `(Δcpu_seconds / (Δt · requested_slots)) * 100`.
  SGE only exposes cumulative CPU time, so `qtop` keeps a small rolling
  history per job and computes the delta across each refresh interval.
  The first time a job appears, CPU efficiency shows `---`; from the
  second poll onward it shows a percentage. >100% means the job is
  using more cores than it asked for.

## Using qtop as a library

The data layer lives on `SGEClient` and `DemoClient`. Both have the
same interface, so consumers can use `DemoClient` for testing without
a cluster.

```python
from qtop import SGEClient

client = SGEClient()
jobs   = client.fetch_jobs(user="*")
hosts  = client.fetch_hosts()
summary = client.fetch_summary(jobs, hosts)

for j in jobs:
    if j.cpu_efficiency is not None and j.cpu_efficiency > 150:
        print(f"OVERUSE {j.job_id} {j.user}: {j.cpu_efficiency:.0f}%")
```

The rolling CPU history is owned by the client, so call `fetch_jobs()`
at least twice (with a delay) to populate `cpu_efficiency`.

See `examples/notify_overuse.py` for a working consumer that flags
jobs exceeding their requested slots:

```sh
uv run python examples/notify_overuse.py --demo --threshold 120 --gap 5
```

### Public API surface

- `SGEClient`, `DemoClient` — data sources, same interface
- `Job`, `Host`, `ClusterSummary`, `JobState` — dataclasses / enum
- `parse_qstat_xml`, `parse_qhost_xml`, `parse_qstat_j` — pure parsers
  for working with captured fixture data
- `parse_memory`, `format_bytes`, `qstat_available` — small utilities

## Tests

```sh
uv run python -m pytest
```

53 tests cover XML parsing (multiple SGE-version conventions, missing
fields, malformed input), the rolling CPU-history efficiency
computation, DemoClient compatibility with the parser, and the CLI's
argparse / qstat-missing paths.

Textual rendering (color thresholds, panel layout, resize behavior) is
exercised via headless `App.run_test()` but not visually asserted —
`uv run qtop --demo` in a real terminal is the way to confirm
appearance.

## Dependencies

- Python ≥3.11
- [`textual`](https://textual.textualize.io/) for the TUI
- stdlib `xml.etree.ElementTree` for parsing — no extra XML deps
