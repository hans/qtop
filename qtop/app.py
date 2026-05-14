"""Textual TUI for qtop.

Library choice: Textual. DataTable + Footer + ModalScreen give us a
clean multi-panel layout with keyboard navigation, sortable columns,
and modal dialogs out of the box. The CSS-like styling makes the
dark-terminal color scheme straightforward.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static

from .client import SGEClient
from .models import ClusterSummary, Job, JobState, format_bytes


# --------------------------------------------------------------------------- #
# Cell rendering helpers
# --------------------------------------------------------------------------- #


_STATE_STYLE = {
    JobState.RUNNING: "green",
    JobState.WAITING: "yellow",
    JobState.ERROR: "red bold",
    JobState.HELD: "dim",
    JobState.SUSPENDED: "dim",
    JobState.TRANSFERRING: "cyan",
    JobState.DELETING: "red",
    JobState.UNKNOWN: "",
}


def _state_cell(job: Job) -> Text:
    return Text(job.raw_state or job.state.value, style=_STATE_STYLE.get(job.state, ""))


def _efficiency_cell(pct: float | None) -> Text:
    if pct is None:
        return Text("---", style="dim")
    if pct >= 75:
        style = "green"
    elif pct >= 25:
        style = "yellow"
    else:
        style = "red"
    return Text(f"{pct:>5.1f}%", style=style)


def _fmt_time(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%m-%d %H:%M")


def _fmt_elapsed(start: datetime | None) -> str:
    if start is None:
        return "-"
    delta = datetime.now() - start
    secs = int(delta.total_seconds())
    if secs < 0:
        return "-"
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


# --------------------------------------------------------------------------- #
# Custom widgets
# --------------------------------------------------------------------------- #


class SummaryBar(Static):
    """One-line summary: cluster stats + countdown + status."""

    DEFAULT_CSS = """
    SummaryBar {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }
    """

    def render_summary(self, summary: ClusterSummary, *, user: str, demo: bool,
                       countdown: int, filter_text: str, sort_col: str) -> None:
        bits = []
        if demo:
            bits.append("[bold magenta]DEMO[/]")
        bits.append(f"user=[b]{user}[/b]")
        bits.append(
            f"jobs [green]{summary.running}r[/] / "
            f"[yellow]{summary.waiting}w[/] / "
            f"[red]{summary.error}e[/] "
            f"({summary.total_jobs})"
        )
        bits.append(f"slots {summary.slots_used}/{summary.slots_total}")
        bits.append(f"{summary.nodes} nodes")
        bits.append(f"sort=[i]{sort_col}[/i]")
        if filter_text:
            bits.append(f"filter=[i]/{filter_text}/[/i]")
        if summary.status:
            bits.append(f"[red]! {summary.status}[/]")
        bits.append(f"refresh in {countdown}s")
        self.update(" · ".join(bits))


class JobsTable(DataTable):
    DEFAULT_CSS = """
    JobsTable {
        height: 1fr;
        min-height: 5;
    }
    """


class EfficiencyTable(DataTable):
    DEFAULT_CSS = """
    EfficiencyTable {
        height: 12;
        border-top: solid $panel;
    }
    EfficiencyTable.-hidden {
        display: none;
    }
    """


class DetailPanel(VerticalScroll):
    """Bottom panel; hidden by default. Enter populates+shows, Esc hides."""

    DEFAULT_CSS = """
    DetailPanel {
        height: 15;
        display: none;
        border: solid $primary;
        padding: 0 1;
    }
    DetailPanel.-visible {
        display: block;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._body = Static("", id="detail-body")

    def compose(self) -> ComposeResult:
        yield self._body

    def show_detail(self, job_id: str, detail: dict[str, str]) -> None:
        lines = [f"[b]Job {job_id}[/b]"]
        for k, v in detail.items():
            lines.append(f"  [cyan]{k:24s}[/cyan] {v}")
        self._body.update("\n".join(lines))
        self.add_class("-visible")

    def hide(self) -> None:
        self.remove_class("-visible")

    @property
    def is_visible(self) -> bool:
        return self.has_class("-visible")


# --------------------------------------------------------------------------- #
# Modal screens
# --------------------------------------------------------------------------- #


class FilterScreen(ModalScreen[str]):
    DEFAULT_CSS = """
    FilterScreen {
        align: center middle;
    }
    FilterScreen > Container {
        width: 60;
        height: 5;
        border: thick $accent;
        background: $panel;
        padding: 0 1;
    }
    FilterScreen Input {
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, initial: str = ""):
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("Filter (name / user / state). Empty clears.")
            yield Input(value=self._initial, placeholder="substring…", id="filter-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value or "")

    def action_cancel(self) -> None:
        self.dismiss(self._initial)


class ConfirmDelete(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmDelete {
        align: center middle;
    }
    ConfirmDelete > Container {
        width: 60;
        height: 7;
        border: thick $error;
        background: $panel;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "No"),
        Binding("enter", "cancel", "No"),  # default to No on Enter
    ]

    def __init__(self, job_id: str, job_name: str):
        super().__init__()
        self._job_id = job_id
        self._job_name = job_name

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(
                f"[b red]Delete job {self._job_id}[/] ([i]{self._job_name}[/])?\n\n"
                "  [b]y[/b] = yes,  [b]n[/b] / Esc / Enter = no"
            )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #


_SORT_COLUMNS = [
    ("state",       lambda j: (j.state.value, j.job_id)),
    ("job_id",      lambda j: (int(j.job_id) if j.job_id.isdigit() else 0, j.job_id)),
    ("user",        lambda j: (j.user, j.job_id)),
    ("name",        lambda j: (j.name, j.job_id)),
    ("slots",       lambda j: (-j.slots, j.job_id)),
    ("submit_time", lambda j: (j.submit_time or datetime.min, j.job_id)),
    ("cpu_eff",     lambda j: (-(j.cpu_efficiency or -1), j.job_id)),
    ("mem_eff",     lambda j: (-(j.mem_efficiency or -1), j.job_id)),
]


class QtopApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("slash", "filter", "Filter"),
        Binding("s", "sort", "Sort"),
        Binding("d", "delete", "Delete"),
        Binding("u", "toggle_user", "All/Mine"),
        Binding("e", "toggle_eff", "Eff panel"),
        # Enter is intercepted via DataTable.RowSelected (see on_data_table_row_selected);
        # listed here only so the Footer shows "enter Detail" for discoverability.
        Binding("enter", "toggle_detail", "Detail", show=True, priority=False),
        Binding("escape", "close_detail", "Close detail", show=False),
    ]

    def __init__(self, *, client: SGEClient, user: str, interval: float,
                 demo: bool = False, **kwargs: Any):
        super().__init__(**kwargs)
        self.client = client
        self.user = user
        self._home_user = user if user != "*" else (os.environ.get("USER") or "*")
        self.interval = max(1.0, float(interval))
        self.demo = demo
        self._sort_idx = 0
        self._filter_text = ""
        self._jobs: list[Job] = []
        self._summary = ClusterSummary()
        self._countdown = int(self.interval)

    # ---- compose / mount ----

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SummaryBar(id="summary")
        yield JobsTable(id="jobs")
        yield EfficiencyTable(id="eff")
        yield DetailPanel(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"qtop{' (demo)' if self.demo else ''}"

        jt = self.query_one("#jobs", DataTable)
        jt.add_columns(
            "Job ID", "Name", "User", "State", "Queue", "Slots", "Submit", "Elapsed"
        )
        jt.cursor_type = "row"
        jt.zebra_stripes = True
        jt.fixed_columns = 1

        et = self.query_one("#eff", DataTable)
        et.add_columns(
            "Job ID", "Name", "CPU eff", "Mem used", "Mem req", "Mem eff"
        )
        et.cursor_type = "row"
        et.zebra_stripes = True

        # Initial render + start the refresh loop
        self._render_summary()
        self._trigger_fetch()
        self.set_interval(self.interval, self._trigger_fetch)
        self.set_interval(1.0, self._tick)

    # ---- refresh worker ----

    def _trigger_fetch(self) -> None:
        self._countdown = int(self.interval)
        self.run_worker(self._fetch, exclusive=True, thread=True, group="fetch")

    def _fetch(self) -> None:
        try:
            jobs = self.client.fetch_jobs(self.user)
            hosts = self.client.fetch_hosts()
            summary = self.client.fetch_summary(jobs, hosts)
            summary.status = getattr(self.client, "last_status", None)
        except RuntimeError as e:
            jobs = self._jobs
            summary = ClusterSummary(
                total_jobs=self._summary.total_jobs,
                running=self._summary.running,
                waiting=self._summary.waiting,
                error=self._summary.error,
                slots_used=self._summary.slots_used,
                slots_total=self._summary.slots_total,
                nodes=self._summary.nodes,
                status=str(e),
            )
        self.call_from_thread(self._apply_data, jobs, summary)

    def _apply_data(self, jobs: list[Job], summary: ClusterSummary) -> None:
        self._jobs = jobs
        self._summary = summary
        self._render_summary()
        self._render_jobs()
        self._render_efficiency()

    def _tick(self) -> None:
        if self._countdown > 0:
            self._countdown -= 1
        self._render_summary()

    # ---- rendering ----

    def _render_summary(self) -> None:
        bar = self.query_one("#summary", SummaryBar)
        bar.render_summary(
            self._summary,
            user=self.user,
            demo=self.demo,
            countdown=max(0, self._countdown),
            filter_text=self._filter_text,
            sort_col=_SORT_COLUMNS[self._sort_idx][0],
        )

    def _filtered_sorted_jobs(self) -> list[Job]:
        jobs = list(self._jobs)
        if self._filter_text:
            needle = self._filter_text.lower()
            jobs = [
                j for j in jobs
                if needle in j.name.lower()
                or needle in j.user.lower()
                or needle in j.raw_state.lower()
                or needle in j.state.value.lower()
                or needle in j.job_id.lower()
            ]
        key = _SORT_COLUMNS[self._sort_idx][1]
        jobs.sort(key=key)
        return jobs

    def _render_jobs(self) -> None:
        jt = self.query_one("#jobs", DataTable)
        # Preserve cursor on the same job_id if possible
        prev_id = self._current_job_id(jt)
        jt.clear()
        target_row = None
        for j in self._filtered_sorted_jobs():
            # Some SGE variants don't expose JB_submission_time in XML at all
            # (only in qstat -j). Fall back to start_time so the column is
            # still informative for running jobs.
            jt.add_row(
                j.job_id,
                j.name,
                j.user,
                _state_cell(j),
                j.queue or "-",
                str(j.slots),
                _fmt_time(j.submit_time or j.start_time),
                _fmt_elapsed(j.start_time),
                key=j.job_id,
            )
            if j.job_id == prev_id:
                target_row = j.job_id
        if target_row is not None:
            # KeyError if the row was filtered out; we just leave the cursor at 0.
            try:
                jt.move_cursor(row=jt.get_row_index(target_row))
            except KeyError:
                pass

    def _render_efficiency(self) -> None:
        et = self.query_one("#eff", DataTable)
        et.clear()
        running = [j for j in self._jobs if j.state is JobState.RUNNING]
        # sort: worst-utilizing first
        running.sort(key=lambda j: (j.cpu_efficiency if j.cpu_efficiency is not None else 1e9))
        for j in running:
            et.add_row(
                j.job_id,
                j.name,
                _efficiency_cell(j.cpu_efficiency),
                format_bytes(j.mem_used_bytes),
                format_bytes(j.mem_request_bytes),
                _efficiency_cell(j.mem_efficiency),
                key=j.job_id,
            )

    def _current_job_id(self, jt: DataTable) -> str | None:
        if jt.row_count == 0:
            return None
        try:
            row_key, _ = jt.coordinate_to_cell_key(jt.cursor_coordinate)
        except Exception:
            return None
        return str(row_key.value) if row_key and row_key.value is not None else None

    # ---- actions ----

    def action_refresh(self) -> None:
        self._trigger_fetch()

    def action_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(_SORT_COLUMNS)
        self._render_jobs()
        self._render_summary()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a jobs-table row → toggle the detail panel for that job."""
        if event.data_table.id != "jobs":
            return
        self.action_toggle_detail()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Click-on-column-header wires to the same sort action (spec)."""
        if event.data_table.id != "jobs":
            return
        # Map column index → sort column.
        # Jobs columns: 0=Job ID, 1=Name, 2=User, 3=State, 4=Queue, 5=Slots, 6=Submit, 7=Elapsed
        mapping = {
            0: "job_id", 1: "name", 2: "user", 3: "state",
            5: "slots", 6: "submit_time",
        }
        target = mapping.get(event.column_index)
        if target is None:
            return
        for i, (name, _) in enumerate(_SORT_COLUMNS):
            if name == target:
                self._sort_idx = i
                self._render_jobs()
                self._render_summary()
                return

    def action_filter(self) -> None:
        def _apply(text: str | None) -> None:
            self._filter_text = text or ""
            self._render_jobs()
            self._render_summary()
        self.push_screen(FilterScreen(self._filter_text), _apply)

    def action_toggle_user(self) -> None:
        self.user = "*" if self.user != "*" else self._home_user
        self._trigger_fetch()

    def action_toggle_eff(self) -> None:
        et = self.query_one("#eff", EfficiencyTable)
        et.toggle_class("-hidden")

    def action_toggle_detail(self) -> None:
        detail = self.query_one("#detail", DetailPanel)
        if detail.is_visible:
            detail.hide()
            return
        jt = self.query_one("#jobs", DataTable)
        job_id = self._current_job_id(jt)
        if not job_id:
            return
        self.run_worker(
            lambda jid=job_id: self._load_detail(jid),
            thread=True, group="detail", exclusive=True,
        )

    def _load_detail(self, job_id: str) -> None:
        try:
            detail = self.client.fetch_job_detail(job_id)
        except RuntimeError as e:
            detail = {"error": str(e)}
        self.call_from_thread(self._show_detail, job_id, detail)

    def _show_detail(self, job_id: str, detail: dict[str, str]) -> None:
        self.query_one("#detail", DetailPanel).show_detail(job_id, detail)

    def action_close_detail(self) -> None:
        self.query_one("#detail", DetailPanel).hide()

    def action_delete(self) -> None:
        jt = self.query_one("#jobs", DataTable)
        job_id = self._current_job_id(jt)
        if not job_id:
            return
        job = next((j for j in self._jobs if j.job_id == job_id), None)
        if not job:
            return

        def _on_confirm(ok: bool | None) -> None:
            if not ok:
                return
            self.run_worker(
                lambda: self._do_delete(job.job_id),
                thread=True, group="delete", exclusive=True,
            )

        self.push_screen(ConfirmDelete(job.job_id, job.name), _on_confirm)

    def _do_delete(self, job_id: str) -> None:
        ok, msg = self.client.delete_job(job_id)
        # Trigger refresh on the UI thread regardless of outcome
        self.call_from_thread(self._after_delete, job_id, ok, msg)

    def _after_delete(self, job_id: str, ok: bool, msg: str) -> None:
        if not ok:
            self._summary.status = f"qdel {job_id}: {msg}"
            self._render_summary()
        self._trigger_fetch()
