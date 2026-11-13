"""Pipeline detail screen: summary, steps, step log (searchable, live-following) and test results."""

from __future__ import annotations

import re
import webbrowser

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Log, Static
from textual.worker import get_current_worker

from .bitbucket import BitbucketClient, BitbucketError, CommitInfo, Pipeline, Repo, Step, TestReport
from .ui import STATUS_STYLE, fmt_duration, fmt_when, status_text

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
MAX_LOG_LINES = 50_000
STATUS_POLL_SECONDS = 5
LOG_POLL_SECONDS = 3


def clean_log_text(text: str) -> str:
    """Strip ANSI colour codes and collapse carriage-return progress output."""
    text = ANSI_RE.sub("", text)
    if "\r" in text:
        text = "\n".join(line.rsplit("\r", 1)[-1] if line.strip("\r") else ""
                         for line in text.replace("\r\n", "\n").split("\n"))
    return text


class StepItem(ListItem):
    def __init__(self, step: Step) -> None:
        super().__init__()
        self.step = step
        self.label = Label(self.render_label())

    def compose(self) -> ComposeResult:
        yield self.label

    def render_label(self) -> Text:
        s = self.step
        icon, style = STATUS_STYLE.get(s.status, ("?", "white"))
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(f"{icon} ", style=style)
        text.append(s.name)
        dur = fmt_duration(s.elapsed_seconds())
        if dur:
            text.append(f"  {dur}", style="dim")
        if s.manual and s.state in ("PENDING", "READY"):
            text.append("  manual", style="magenta")
        return text

    def set_step(self, step: Step) -> None:
        self.step = step
        self.label.update(self.render_label())


class DetailScreen(Screen):
    CSS = """
    #summary { padding: 0 1; height: auto; border-bottom: solid $primary-background; }
    #left { width: 34%; min-width: 30; max-width: 60; border-right: solid $primary-background; }
    #steps-heading, #log-title { text-style: bold; padding: 0 1; }
    #step-list { height: auto; max-height: 50%; }
    #step-info { height: 1fr; padding: 1 1 0 1; }
    #right { padding: 0 0 0 1; }
    #tests { height: auto; max-height: 40%; padding: 0 1 1 0; border-bottom: solid $primary-background; }
    #log { height: 1fr; }
    #search.hidden, #tests.hidden { display: none; }
    """

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("slash", "search", "Search log"),
        Binding("n", "next_match", "Next", show=False),
        Binding("N", "prev_match", "Prev", show=False),
        Binding("f", "toggle_follow", "Follow"),
        Binding("r", "reload", "Refresh"),
        Binding("o", "open_browser", "Open in browser"),
        Binding("tab", "focus_next", "Steps/Log", show=False),
    ]

    def __init__(self, client: BitbucketClient, repo: Repo, pipeline: Pipeline) -> None:
        super().__init__()
        self.client = client
        self.repo = repo
        self.pipeline = pipeline
        self.commit: CommitInfo | None = None
        self.steps: list[Step] = []
        self.step_items: dict[str, StepItem] = {}
        self.current: Step | None = None
        self.log_offset = 0
        self.log_lines: list[str] = []
        self.log_partial = ""          # trailing text without a newline yet
        self.follow = True
        self.search_term = ""
        self.matches: list[int] = []
        self.match_index = -1
        self.tests_for: str | None = None

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.render_summary(), id="summary")
        with Horizontal():
            with Vertical(id="left"):
                yield Static("Steps", id="steps-heading")
                yield ListView(id="step-list")
                with VerticalScroll(id="step-info"):
                    yield Static("", id="step-info-text")
            with Vertical(id="right"):
                with VerticalScroll(id="tests", classes="hidden"):
                    yield Static("", id="tests-text")
                yield Static("Log", id="log-title")
                yield Log(id="log", max_lines=MAX_LOG_LINES, auto_scroll=True)
                yield Input(placeholder="Search the log… (Enter to find, n/N next/previous, Esc to close)",
                            id="search", classes="hidden")
        yield Footer()

    def on_mount(self) -> None:
        self.app.sub_title = f"{self.repo.full_name} · pipeline #{self.pipeline.build_number}"
        self.query_one("#step-list", ListView).focus()
        self.load_detail(initial=True)
        self.set_interval(STATUS_POLL_SECONDS, self.poll_status)
        self.set_interval(LOG_POLL_SECONDS, self.poll_log)
        self.set_interval(1, self.tick)

    def on_unmount(self) -> None:
        self.app.sub_title = self.app.workspace

    # -- rendering -----------------------------------------------------------

    def render_summary(self) -> Group:
        p = self.pipeline
        line1 = Text()
        line1.append_text(status_text(p.status))
        line1.append(f"   #{p.build_number}", style="bold")
        if p.pr_id:
            line1.append(f"   PR #{p.pr_id}", style="bold magenta")
            if p.pr_title:
                line1.append(f" {p.pr_title}")
            if p.pr_source and p.pr_destination:
                line1.append(f"   {p.pr_source} → {p.pr_destination}", style="cyan")
        elif p.ref_name:
            kind = (p.ref_type or "branch").replace("named_", "")
            line1.append(f"   {kind} ", style="dim")
            line1.append(p.ref_name, style="cyan")
        if p.commit:
            line1.append("   commit ", style="dim")
            line1.append(p.commit, style="yellow")

        line2 = Text(no_wrap=True, overflow="ellipsis")
        if self.commit:
            line2.append(self.commit.message.splitlines()[0] if self.commit.message else "(no message)")
            if self.commit.author:
                line2.append(f"  — {self.commit.author}", style="dim")
        else:
            line2.append("Loading commit…", style="dim")

        bits = []
        who = p.creator or ""
        how = (p.trigger or "").lower()
        if who or how:
            bits.append(f"triggered by {who} ({how})" if who and how else f"triggered by {who or how}")
        if p.selector:
            bits.append(f"pipeline {p.selector}")
        if p.created_on:
            bits.append(f"started {fmt_when(p.created_on)}")
        if p.elapsed_seconds() is not None:
            bits.append(f"duration {fmt_duration(p.elapsed_seconds())}")
        if p.build_seconds_used:
            bits.append(f"build time {fmt_duration(p.build_seconds_used)}")
        if p.run_number and p.run_number > 1:
            bits.append(f"re-run {p.run_number}")
        line3 = Text(" · ".join(bits), style="dim")
        return Group(line1, line2, line3)

    def render_step_info(self, step: Step | None) -> Group | Text:
        if step is None:
            return Text("")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column()
        grid.add_row("Status", status_text(step.status))
        if step.started_on:
            grid.add_row("Started", fmt_when(step.started_on))
        if step.elapsed_seconds() is not None:
            grid.add_row("Duration", fmt_duration(step.elapsed_seconds()))
        if step.image:
            grid.add_row("Image", step.image)
        grid.add_row("Trigger", "manual" if step.manual else "automatic")
        if step.max_time_minutes:
            grid.add_row("Max time", f"{step.max_time_minutes} min")
        if step.build_seconds_used:
            grid.add_row("Build time", fmt_duration(step.build_seconds_used))
        if step.run_number and step.run_number > 1:
            grid.add_row("Run", str(step.run_number))
        parts: list = [grid]
        script = [c for c in step.commands if c.phase == "script"]
        if script:
            parts.append(Text("\nCommands", style="bold"))
            for i, c in enumerate(script, 1):
                cmd = c.command or c.name
                t = Text(f"{i:>2}  ", style="dim")
                t.append(cmd.strip())
                parts.append(t)
        return Group(*parts)

    def render_tests(self, report: TestReport) -> Group:
        head = Text()
        head.append("Tests  ", style="bold")
        head.append(f"{report.passed} passed", style="green")
        if report.failed:
            head.append(f" · {report.failed} failed", style="bold red")
        if report.errored:
            head.append(f" · {report.errored} error{'' if report.errored == 1 else 's'}", style="bold red")
        if report.skipped:
            head.append(f" · {report.skipped} skipped", style="yellow")
        head.append(f" · {report.total} total", style="dim")
        parts: list = [head]
        for f in report.failures:
            t = Text("✘ ", style="red")
            t.append(f.name, style="bold")
            parts.append(t)
            if f.reason:
                reason = f.reason.strip().splitlines()
                more = f" (+{len(reason) - 3} lines)" if len(reason) > 3 else ""
                parts.append(Text("   " + "\n   ".join(reason[:3]) + more, style="dim"))
        shown = len(report.failures)
        if shown and shown < report.failed + report.errored:
            parts.append(Text(f"…and {report.failed + report.errored - shown} more", style="dim"))
        return Group(*parts)

    def update_log_title(self) -> None:
        step = self.current
        title = Text()
        title.append("Log", style="bold")
        if step:
            title.append(f" · {step.name}")
        if step and step.is_running:
            title.append("  ● following" if self.follow else "  paused (f to follow)",
                         style="cyan" if self.follow else "yellow")
        if self.search_term:
            if self.matches:
                title.append(f"   '{self.search_term}' {self.match_index + 1}/{len(self.matches)}",
                             style="magenta")
            else:
                title.append(f"   '{self.search_term}' not found", style="red")
        if step and not self.log_lines and not self.log_partial:
            if step.state in ("PENDING", "READY"):
                title.append("   step hasn't started yet", style="dim")
            elif step.state == "COMPLETED" and step.result == "NOT_RUN":
                title.append("   step didn't run", style="dim")
            else:
                title.append("   no log output", style="dim")
        self.query_one("#log-title", Static).update(title)

    # -- loading (background threads) ----------------------------------------

    @work(thread=True, exclusive=True, group="detail")
    def load_detail(self, initial: bool = False) -> None:
        try:
            pipeline = self.client.get_pipeline(self.repo.full_name, self.pipeline.uuid)
            steps = self.client.pipeline_steps(self.repo.full_name, self.pipeline.uuid)
        except Exception as exc:
            self.app.call_from_thread(self.notify, f"Couldn't load pipeline: {exc}", severity="error")
            return
        commit = self.commit
        if commit is None and pipeline.commit_hash:
            try:
                commit = self.client.get_commit(self.repo.full_name, pipeline.commit_hash)
            except Exception:
                commit = None
        if get_current_worker().is_cancelled:
            return
        self.app.call_from_thread(self.set_detail, pipeline, steps, commit, initial)

    @work(thread=True, exclusive=True, group="log")
    def load_log(self, step: Step, reset: bool) -> None:
        offset = 0 if reset else self.log_offset
        try:
            text, new_offset, replaced = self.client.step_log(
                self.repo.full_name, self.pipeline.uuid, step.uuid, offset)
        except Exception as exc:
            self.app.call_from_thread(self.notify, f"Couldn't load log: {exc}", severity="warning")
            return
        if get_current_worker().is_cancelled:
            return
        self.app.call_from_thread(self.add_log_text, step, text, new_offset, reset or replaced)

    @work(thread=True, exclusive=True, group="tests")
    def load_tests(self, step: Step) -> None:
        try:
            report = self.client.test_report(self.repo.full_name, self.pipeline.uuid, step.uuid)
        except Exception:
            report = None
        if get_current_worker().is_cancelled:
            return
        self.app.call_from_thread(self.set_tests, step, report)

    # -- UI updates (main thread) --------------------------------------------

    def set_detail(self, pipeline: Pipeline, steps: list[Step], commit: CommitInfo | None,
                   initial: bool) -> None:
        was_running = {s.uuid for s in self.steps if s.is_running}
        self.pipeline = pipeline
        self.commit = commit
        self.query_one("#summary", Static).update(self.render_summary())

        list_view = self.query_one("#step-list", ListView)
        same_steps = [s.uuid for s in steps] == [s.uuid for s in self.steps]
        self.steps = steps
        if same_steps and self.step_items:
            for s in steps:
                self.step_items[s.uuid].set_step(s)
        else:
            highlighted = self.current.uuid if self.current else None
            list_view.clear()
            self.step_items = {s.uuid: StepItem(s) for s in steps}
            list_view.extend(self.step_items.values())
            if highlighted in self.step_items:
                list_view.index = [s.uuid for s in steps].index(highlighted)

        if not steps:
            self.query_one("#step-info-text", Static).update(Text("This pipeline has no steps.", style="dim"))
            return

        if initial:
            # Open the step most likely to be interesting.
            choice = (next((s for s in steps if s.failed), None)
                      or next((s for s in steps if s.is_running), None)
                      or next((s for s in reversed(steps) if s.state == "COMPLETED"), None)
                      or steps[0])
            list_view.index = steps.index(choice)
            self.select_step(choice)
        elif self.current:
            fresh = next((s for s in steps if s.uuid == self.current.uuid), None)
            if fresh:
                just_finished = fresh.uuid in was_running and not fresh.is_running
                self.current = fresh
                self.query_one("#step-info-text", Static).update(self.render_step_info(fresh))
                if just_finished:
                    self.load_log(fresh, reset=False)   # grab the last of the output
                    self.load_tests(fresh)
                self.update_log_title()

    def select_step(self, step: Step) -> None:
        if self.current and self.current.uuid == step.uuid:
            return
        self.current = step
        self.log_lines = []
        self.log_partial = ""
        self.log_offset = 0
        self.matches = []
        self.match_index = -1
        log = self.query_one("#log", Log)
        log.clear()
        log.auto_scroll = self.follow
        self.query_one("#step-info-text", Static).update(self.render_step_info(step))
        self.query_one("#tests").add_class("hidden")
        self.tests_for = None
        self.update_log_title()
        if step.state not in ("PENDING", "READY"):
            self.load_log(step, reset=True)
        if step.state == "COMPLETED":
            self.load_tests(step)

    def add_log_text(self, step: Step, text: str, new_offset: int, replace: bool) -> None:
        if self.current is None or step.uuid != self.current.uuid:
            return
        log = self.query_one("#log", Log)
        if replace:
            log.clear()
            self.log_lines = []
            self.log_partial = ""
        self.log_offset = new_offset
        if not text:
            self.update_log_title()
            return
        text = self.log_partial + clean_log_text(text)
        lines = text.split("\n")
        self.log_partial = lines.pop()  # may be "" if text ended with a newline
        if lines:
            self.log_lines.extend(lines)
            if len(self.log_lines) > MAX_LOG_LINES:
                self.log_lines = self.log_lines[-MAX_LOG_LINES:]
            log.auto_scroll = self.follow and not self.search_term
            log.write_lines(lines)
        if self.search_term:
            self.recompute_matches(keep_position=True)
        self.update_log_title()

    def set_tests(self, step: Step, report: TestReport | None) -> None:
        if self.current is None or step.uuid != self.current.uuid:
            return
        tests = self.query_one("#tests")
        if report is None:
            tests.add_class("hidden")
            return
        self.tests_for = step.uuid
        self.query_one("#tests-text", Static).update(self.render_tests(report))
        tests.remove_class("hidden")

    # -- search --------------------------------------------------------------

    def recompute_matches(self, keep_position: bool = False) -> None:
        term = self.search_term.lower()
        self.matches = [i for i, line in enumerate(self.log_lines) if term in line.lower()] if term else []
        if not keep_position:
            self.match_index = -1

    def jump_to_match(self, delta: int) -> None:
        if not self.matches:
            self.update_log_title()
            return
        log = self.query_one("#log", Log)
        log.auto_scroll = False
        if self.match_index == -1:
            # First jump: the first match at or below the current scroll position.
            top = int(log.scroll_y)
            after = [i for i, line in enumerate(self.matches) if line >= top]
            self.match_index = after[0] if after and delta > 0 else (len(self.matches) - 1 if delta < 0 else 0)
        else:
            self.match_index = (self.match_index + delta) % len(self.matches)
        line = self.matches[self.match_index]
        # Put the matching line a few rows from the top so there's context above it.
        log.scroll_to(y=max(0, line - 3), animate=False)
        self.update_log_title()

    # -- timers --------------------------------------------------------------

    def poll_status(self) -> None:
        if self.pipeline.is_running:
            self.load_detail()

    def poll_log(self) -> None:
        if self.follow and self.current and self.current.is_running:
            self.load_log(self.current, reset=False)

    def tick(self) -> None:
        if self.pipeline.is_running:
            self.query_one("#summary", Static).update(self.render_summary())
            for item in self.step_items.values():
                if item.step.is_running:
                    item.set_step(item.step)
            if self.current and self.current.is_running:
                self.query_one("#step-info-text", Static).update(self.render_step_info(self.current))

    # -- events --------------------------------------------------------------

    @on(ListView.Highlighted, "#step-list")
    def step_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, StepItem):
            self.select_step(event.item.step)

    @on(ListView.Selected, "#step-list")
    def step_selected(self) -> None:
        self.query_one("#log", Log).focus()

    @on(Input.Submitted, "#search")
    def search_submitted(self, event: Input.Submitted) -> None:
        term = event.value.strip()
        if term != self.search_term:
            self.search_term = term
            self.recompute_matches()
        self.close_search()  # hand focus to the log so n/N work straight away
        if term:
            self.jump_to_match(+1)

    # -- actions -------------------------------------------------------------

    def close_search(self) -> None:
        search = self.query_one("#search", Input)
        search.add_class("hidden")
        self.query_one("#log", Log).focus()

    def action_back(self) -> None:
        search = self.query_one("#search", Input)
        if not search.has_class("hidden"):
            self.close_search()
            return
        if self.search_term:
            self.search_term = ""
            self.matches = []
            self.match_index = -1
            search.value = ""
            self.update_log_title()
            return
        self.app.pop_screen()

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.remove_class("hidden")
        search.focus()

    def action_next_match(self) -> None:
        self.jump_to_match(+1)

    def action_prev_match(self) -> None:
        self.jump_to_match(-1)

    def action_toggle_follow(self) -> None:
        self.follow = not self.follow
        log = self.query_one("#log", Log)
        log.auto_scroll = self.follow
        if self.follow:
            log.scroll_end(animate=False)
            if self.current and self.current.is_running:
                self.load_log(self.current, reset=False)
        self.notify("Following the log" if self.follow else "Stopped following the log", timeout=2)
        self.update_log_title()

    def action_reload(self) -> None:
        self.notify("Refreshing…", timeout=2)
        self.load_detail()
        if self.current:
            step = self.current
            self.current = None   # force select_step to reload log & tests
            self.select_step(step)

    def action_open_browser(self) -> None:
        url = f"https://bitbucket.org/{self.repo.full_name}/pipelines/results/{self.pipeline.build_number}"
        if self.current and self.current.uuid:
            url += f"/steps/{self.current.uuid}"
        webbrowser.open(url)
