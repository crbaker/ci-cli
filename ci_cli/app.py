"""Interactive Textual UI: repositories on the left, pipelines for the chosen repo on the right."""

from __future__ import annotations

import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Footer, Header, Input, Label, ListItem, ListView, Static
from textual.worker import get_current_worker

from .bitbucket import Branch, BitbucketClient, BitbucketError, Pipeline, Repo, Step

from .ui import STATUS_STYLE, fmt_duration, fmt_when, shorten, status_text


class RepoItem(ListItem):
    """A list row for one repository, with the latest pipeline's status icon."""

    def __init__(self, repo: Repo) -> None:
        super().__init__()
        self.repo = repo
        self.shown = False  # True when this repo's pipelines are on the right
        self.label = Label(self.render_label())

    def compose(self) -> ComposeResult:
        yield self.label

    def render_label(self) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        repo = self.repo
        if self.shown:
            text.append("▶ ", style="bold yellow")
        elif repo.pipelines_error:
            text.append("– ", style="dim")
        elif repo.pipelines:
            icon, style = STATUS_STYLE.get(repo.pipelines[0].status, ("?", "white"))
            text.append(f"{icon} ", style=style)
        else:
            text.append("  ")
        text.append(repo.name, style="bold" if self.shown else "")
        if self.shown and repo.pipelines:
            icon, style = STATUS_STYLE.get(repo.pipelines[0].status, ("?", "white"))
            text.append(f" {icon}", style=style)
        return text

    def refresh_label(self) -> None:
        self.label.update(self.render_label())


class CIApp(App):
    TITLE = "Bitbucket Pipelines"
    CSS = """
    #sidebar {
        width: 22%;
        min-width: 22;
        max-width: 36;
        border-right: solid $primary-background;
    }
    #filter { margin: 0 0 0 0; }
    #filter.hidden { display: none; }
    #repo-list { height: 1fr; }
    #repo-count { color: $text-muted; padding: 0 1; }
    #detail { padding: 0 1; }
    #repo-title { text-style: bold; padding: 1 0 0 0; }
    #repo-meta { color: $text-muted; padding: 0 0 1 0; }
    #pipelines { height: auto; max-height: 40%; }
    #steps-title { text-style: bold; padding: 1 0 0 0; }
    #steps { height: 1fr; }
    #branches-title { text-style: bold; padding: 1 0 0 0; }
    #branches { height: auto; max-height: 30%; }
    #placeholder { color: $text-muted; padding: 2 2; }
    .hidden { display: none; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
        Binding("slash", "filter", "Filter"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
        Binding("r", "reload", "Refresh"),
        Binding("o", "open_browser", "Open in browser"),
        Binding("tab", "focus_next", "Switch pane", show=False),
    ]

    def __init__(
        self,
        client: BitbucketClient,
        workspace: str,
        pipelines_per_repo: int = 3,
        interval: int = 30,
        workers: int = 8,
        limit: int | None = None,
        branches_per_repo: int = 10,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self.count = pipelines_per_repo
        self.interval = interval
        self.max_workers = workers
        self.limit = limit
        self.branch_count = branches_per_repo
        self.branches: list[Branch] = []
        self.repos: list[Repo] = []
        self.items: dict[str, RepoItem] = {}
        self.selected: Repo | None = None
        self.pipelines: list[Pipeline] = []
        self.steps: list[Step] = []
        self.steps_for: str | None = None  # pipeline uuid the steps belong to
        self.last_refresh: datetime | None = None
        self.sub_title = workspace

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Input(placeholder="Filter repositories…", id="filter", classes="hidden")
                yield ListView(id="repo-list")
                yield Static("Loading repositories…", id="repo-count")
            with Vertical(id="detail"):
                yield Static(
                    "Use ↑/↓ to choose a repository and press Enter to see its pipelines.",
                    id="placeholder",
                )
                yield Static("", id="repo-title", classes="hidden")
                yield Static("", id="repo-meta", classes="hidden")
                yield DataTable(id="pipelines", cursor_type="row", zebra_stripes=True, classes="hidden")
                yield Static("Steps", id="steps-title", classes="hidden")
                yield DataTable(id="steps", cursor_type="none", classes="hidden")
                yield Static("Recent commits by branch", id="branches-title", classes="hidden")
                yield DataTable(id="branches", cursor_type="none", zebra_stripes=True, classes="hidden")
        yield Footer()

    def on_mount(self) -> None:
        pipelines = self.query_one("#pipelines", DataTable)
        pipelines.add_columns("#", "Status", "Branch", "Commit", "Triggered by", "Started", "Duration", "Steps")
        steps = self.query_one("#steps", DataTable)
        steps.add_columns("Step", "Status", "Duration")
        branches = self.query_one("#branches", DataTable)
        branches.add_columns("Branch", "Commit", "Message", "Author", "Date")
        self.query_one("#repo-list", ListView).focus()
        self.load_repos()
        self.set_interval(1, self.tick)
        self.set_interval(self.interval, self.periodic_refresh)

    # -- data loading (background threads) -----------------------------------

    @work(thread=True, exclusive=True, group="repos")
    def load_repos(self) -> None:
        try:
            repos = self.client.list_repositories(self.workspace, limit=self.limit)
        except Exception as exc:
            self.call_from_thread(self.show_error, f"Couldn't load repositories: {exc}")
            return
        self.call_from_thread(self.set_repos, repos)
        self.fetch_latest_statuses(repos)

    def fetch_latest_statuses(self, repos: list[Repo]) -> None:
        """Fetch each repo's latest pipeline (for the list icons). Runs in a worker thread."""
        worker = get_current_worker()

        def one(repo: Repo) -> None:
            if worker.is_cancelled:
                return
            try:
                repo.pipelines = self.client.recent_pipelines(repo.full_name, 1)
                repo.pipelines_error = None
            except BitbucketError as exc:
                repo.pipelines = []
                repo.pipelines_error = "pipelines not enabled" if str(exc).startswith("404") else str(exc)
            self.call_from_thread(self.update_repo_item, repo)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            list(pool.map(one, repos))
        self.call_from_thread(self.statuses_loaded)

    @work(thread=True, exclusive=True, group="statuses")
    def refresh_running_statuses(self) -> None:
        """Re-check only repos whose latest pipeline is still running (cheap periodic refresh)."""
        running = [r for r in self.repos if r.pipelines and r.pipelines[0].is_running]
        if running:
            self.fetch_latest_statuses(running)

    @work(thread=True, exclusive=True, group="pipelines")
    def load_pipelines(self, repo: Repo) -> None:
        try:
            pipelines = self.client.recent_pipelines(repo.full_name, self.count)
            for p in pipelines:
                if p.is_running:
                    try:
                        self.client.fill_step_progress(repo.full_name, p)
                    except BitbucketError:
                        pass
            error = None
        except BitbucketError as exc:
            pipelines = []
            error = "Pipelines are not enabled for this repository." if str(exc).startswith("404") else str(exc)
        if get_current_worker().is_cancelled:
            return
        self.call_from_thread(self.set_pipelines, repo, pipelines, error)

    @work(thread=True, exclusive=True, group="branches")
    def load_branches(self, repo: Repo) -> None:
        try:
            branches = self.client.recent_branches(repo.full_name, self.branch_count)
            error = None
        except BitbucketError as exc:
            branches = []
            error = str(exc)
        if get_current_worker().is_cancelled:
            return
        self.call_from_thread(self.set_branches, repo, branches, error)

    @work(thread=True, exclusive=True, group="steps")
    def load_steps(self, repo: Repo, pipeline: Pipeline) -> None:
        try:
            steps = self.client.pipeline_steps(repo.full_name, pipeline.uuid)
        except BitbucketError:
            steps = []
        if get_current_worker().is_cancelled:
            return
        self.call_from_thread(self.set_steps, pipeline, steps)

    # -- UI updates (main thread) --------------------------------------------

    def show_error(self, message: str) -> None:
        self.query_one("#repo-count", Static).update(Text(message, style="red"))
        self.notify(message, severity="error", timeout=10)

    def set_repos(self, repos: list[Repo]) -> None:
        self.repos = repos
        self.items = {}
        self.apply_filter()
        self.query_one("#repo-count", Static).update(f"{len(repos)} repositories · loading statuses…")

    def apply_filter(self) -> None:
        term = self.query_one("#filter", Input).value.strip().lower()
        list_view = self.query_one("#repo-list", ListView)
        list_view.clear()
        shown = [r for r in self.repos if term in r.name.lower()] if term else self.repos
        items = []
        for repo in shown:
            item = RepoItem(repo)
            item.shown = self.selected is not None and repo.full_name == self.selected.full_name
            item.refresh_label()
            self.items[repo.full_name] = item
            items.append(item)
        list_view.extend(items)
        if items:
            list_view.index = 0
        count = self.query_one("#repo-count", Static)
        if term:
            count.update(f"{len(shown)} of {len(self.repos)} repositories match '{term}'")
        elif self.repos:
            count.update(f"{len(self.repos)} repositories")

    def update_repo_item(self, repo: Repo) -> None:
        item = self.items.get(repo.full_name)
        if item is not None and item.is_mounted:
            item.refresh_label()

    def statuses_loaded(self) -> None:
        self.last_refresh = datetime.now()
        if not self.query_one("#filter", Input).value:
            self.query_one("#repo-count", Static).update(
                f"{len(self.repos)} repositories · updated {self.last_refresh:%H:%M:%S}"
            )

    def show_repo(self, repo: Repo) -> None:
        for item in self.items.values():
            if item.shown and item.is_mounted:
                item.shown = False
                item.refresh_label()
        item = self.items.get(repo.full_name)
        if item is not None:
            item.shown = True
            item.refresh_label()
        self.selected = repo
        self.pipelines = []
        self.steps = []
        self.steps_for = None
        self.query_one("#placeholder").add_class("hidden")
        self.branches = []
        for wid in ("#repo-title", "#repo-meta", "#pipelines", "#steps-title", "#steps",
                    "#branches-title", "#branches"):
            self.query_one(wid).remove_class("hidden")
        self.query_one("#repo-title", Static).update(repo.full_name)
        self.query_one("#repo-meta", Static).update("Loading pipelines…")
        self.query_one("#pipelines", DataTable).clear()
        self.query_one("#steps", DataTable).clear()
        self.query_one("#branches-title", Static).update("Recent commits by branch")
        branches = self.query_one("#branches", DataTable)
        branches.clear()
        branches.add_row(Text("loading…", style="dim"), "", "", "", "")
        self.load_pipelines(repo)
        self.load_branches(repo)

    def set_pipelines(self, repo: Repo, pipelines: list[Pipeline], error: str | None) -> None:
        if repo is not self.selected:
            return
        self.pipelines = pipelines
        if pipelines:
            # Keep the sidebar icon in step with what we just fetched.
            repo.pipelines = pipelines[:1]
            repo.pipelines_error = None
            self.update_repo_item(repo)
        meta = self.query_one("#repo-meta", Static)
        if error:
            meta.update(Text(error, style="yellow"))
        elif not pipelines:
            meta.update("No pipelines have run for this repository yet.")
        else:
            meta.update(
                f"Last {len(pipelines)} pipelines · updated {datetime.now():%H:%M:%S} · "
                f"auto-refresh every {self.interval}s · Tab then Enter on a pipeline for details"
            )
        self.render_pipelines()
        table = self.query_one("#pipelines", DataTable)
        if pipelines and table.cursor_row is not None:
            self.load_steps(repo, pipelines[min(table.cursor_row, len(pipelines) - 1)])
        elif not pipelines:
            self.query_one("#steps", DataTable).clear()

    def pipeline_row(self, p: Pipeline) -> list:
        steps = f"{p.steps_done}/{p.steps_total}" if p.is_running and p.steps_total else ""
        return [
            Text(f"#{p.build_number}", style="bold"),
            status_text(p.status),
            shorten(p.ref_name or "", 22),
            p.commit or "",
            shorten(p.creator or (p.trigger or "").lower(), 16),
            fmt_when(p.created_on),
            fmt_duration(p.elapsed_seconds()),
            Text(steps, style="cyan"),
        ]

    def render_pipelines(self) -> None:
        table = self.query_one("#pipelines", DataTable)
        rows = [self.pipeline_row(p) for p in self.pipelines]
        if table.row_count == len(rows) and rows:
            # Update in place so the cursor (and the steps panel) don't jump about.
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    table.update_cell_at(Coordinate(r, c), value)
            return
        cursor = table.cursor_row
        table.clear()
        for row in rows:
            table.add_row(*row)
        if rows and cursor is not None:
            table.move_cursor(row=min(cursor, len(rows) - 1), animate=False)

    def set_steps(self, pipeline: Pipeline, steps: list[Step]) -> None:
        if pipeline not in self.pipelines:
            return
        self.steps = steps
        self.steps_for = pipeline.uuid
        self.query_one("#steps-title", Static).update(f"Steps of #{pipeline.build_number}")
        self.render_steps()

    def render_steps(self) -> None:
        table = self.query_one("#steps", DataTable)
        rows = [[s.name, status_text(s.status), fmt_duration(s.elapsed_seconds())] for s in self.steps]
        if table.row_count == len(rows) and rows:
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    table.update_cell_at(Coordinate(r, c), value)
            return
        table.clear()
        if not rows:
            table.add_row(Text("no steps", style="dim"), "", "")
        for row in rows:
            table.add_row(*row)

    def set_branches(self, repo: Repo, branches: list[Branch], error: str | None) -> None:
        if repo is not self.selected:
            return
        self.branches = branches
        table = self.query_one("#branches", DataTable)
        table.clear()
        if error:
            table.add_row(Text(f"Couldn't load branches: {error}", style="yellow"), "", "", "", "")
            return
        if not branches:
            table.add_row(Text("no branches", style="dim"), "", "", "", "")
            return
        self.query_one("#branches-title", Static).update(
            f"Recent commits by branch · {len(branches)} most recently updated "
            f"branch{'es' if len(branches) != 1 else ''}"
        )
        for b in branches:
            c = b.commit
            author = (c.author or "").split(" <")[0]  # drop the email from raw "Name <email>"
            table.add_row(
                Text(shorten(b.name, 28), style="bold"),
                c.hash[:7],
                shorten(c.message.splitlines()[0] if c.message else "", 60),
                shorten(author, 20),
                fmt_when(c.date),
            )

    # -- timers --------------------------------------------------------------

    def tick(self) -> None:
        """Keep durations of running pipelines/steps ticking between API refreshes."""
        if any(p.is_running for p in self.pipelines):
            self.render_pipelines()
        if any(s.status == "RUNNING" for s in self.steps):
            self.render_steps()

    def periodic_refresh(self) -> None:
        if self.selected is not None:
            self.load_pipelines(self.selected)
            self.load_branches(self.selected)
        self.refresh_running_statuses()

    # -- events --------------------------------------------------------------

    @on(ListView.Selected, "#repo-list")
    def repo_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, RepoItem):
            self.show_repo(event.item.repo)

    @on(DataTable.RowHighlighted, "#pipelines")
    def pipeline_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self.selected is None or not (0 <= event.cursor_row < len(self.pipelines)):
            return
        pipeline = self.pipelines[event.cursor_row]
        if pipeline.uuid != self.steps_for:
            self.load_steps(self.selected, pipeline)

    @on(DataTable.RowSelected, "#pipelines")
    def pipeline_selected(self, event: DataTable.RowSelected) -> None:
        if self.selected is None or not (0 <= event.cursor_row < len(self.pipelines)):
            return
        from .detail import DetailScreen

        self.push_screen(DetailScreen(self.client, self.selected, self.pipelines[event.cursor_row]))

    @on(Input.Changed, "#filter")
    def filter_changed(self) -> None:
        self.apply_filter()

    @on(Input.Submitted, "#filter")
    def filter_submitted(self) -> None:
        self.query_one("#repo-list", ListView).focus()

    # -- actions -------------------------------------------------------------

    def action_filter(self) -> None:
        filt = self.query_one("#filter", Input)
        filt.remove_class("hidden")
        filt.focus()

    def action_clear_filter(self) -> None:
        filt = self.query_one("#filter", Input)
        if filt.value:
            filt.value = ""
        filt.add_class("hidden")
        self.query_one("#repo-list", ListView).focus()

    def action_reload(self) -> None:
        self.notify("Refreshing…", timeout=2)
        self.load_repos()
        if self.selected is not None:
            self.load_pipelines(self.selected)
            self.load_branches(self.selected)

    def action_open_browser(self) -> None:
        repo = self.selected
        list_view = self.query_one("#repo-list", ListView)
        if self.focused is list_view and isinstance(list_view.highlighted_child, RepoItem):
            repo = list_view.highlighted_child.repo
        if repo is None:
            return
        url = f"https://bitbucket.org/{repo.full_name}/pipelines"
        table = self.query_one("#pipelines", DataTable)
        if repo is self.selected and self.focused is table and self.pipelines:
            p = self.pipelines[min(table.cursor_row, len(self.pipelines) - 1)]
            url += f"/results/{p.build_number}"
        webbrowser.open(url)
