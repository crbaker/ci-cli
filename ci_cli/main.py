"""ci-cli: browse Bitbucket repositories and their pipelines from the terminal."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel

from . import credentials
from .bitbucket import BitbucketClient, BitbucketError
from .credentials import CredentialStoreError

app = typer.Typer(
    add_completion=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()
err_console = Console(stderr=True)

CONN = "Connection"
DISPLAY = "Display"
REFRESH = "Refresh"


def fail(message: str, hint: str | None = None, code: int = 1) -> typer.Exit:
    """Print an error panel to stderr and return an Exit to raise."""
    body = f"[red]{message}[/]"
    if hint:
        body += f"\n\n{hint}"
    err_console.print(Panel(body, title="[bold red]Error[/]", border_style="red", expand=False))
    return typer.Exit(code)


def resolve_workspace(client: BitbucketClient, workspace: str | None) -> str:
    if workspace:
        return workspace
    hint = (
        "Set it explicitly instead:\n"
        "  [bold]ci-cli login[/]   or   [bold]ci-cli -w <workspace-slug>[/]\n"
        "[dim]The slug is the first part of your repo URLs: bitbucket.org/<workspace-slug>/<repo>[/]"
    )
    try:
        with console.status("Finding workspaces…"):
            workspaces = client.list_workspaces()
    except BitbucketError as exc:
        raise fail(f"Couldn't discover your workspace automatically ({exc}).", hint, 2)
    except Exception as exc:  # network errors etc.
        raise fail(f"Couldn't reach Bitbucket: {exc}")
    if not workspaces:
        raise fail("No workspaces found for this API key.", hint, 2)
    if len(workspaces) > 1:
        console.print(
            f"[yellow]Multiple workspaces available ({', '.join(workspaces)}); "
            f"using [bold]{workspaces[0]}[/]. Use --workspace to choose.[/]"
        )
    return workspaces[0]


@app.callback(invoke_without_command=True)
def browse(
    ctx: typer.Context,
    workspace: Annotated[
        str | None,
        typer.Option(
            "--workspace", "-w",
            envvar="BITBUCKET_WORKSPACE",
            help="Workspace slug. Auto-detected if omitted.",
            rich_help_panel=CONN,
            show_default=False,
        ),
    ] = None,
    api_key: Annotated[
        str,
        typer.Option(
            "--api-key",
            envvar="BITBUCKET_API_KEY",
            help="Bitbucket API key / access token.",
            rich_help_panel=CONN,
            show_default=False,
        ),
    ] = "",
    username: Annotated[
        str | None,
        typer.Option(
            "--username", "-u",
            envvar=["BITBUCKET_USERNAME", "BITBUCKET_EMAIL"],
            help="Atlassian email (for Atlassian API tokens) or username (for app passwords). "
                 "Leave unset for access tokens.",
            rich_help_panel=CONN,
            show_default=False,
        ),
    ] = None,
    pipelines: Annotated[
        int,
        typer.Option("--pipelines", "-n", min=1, max=100,
                     help="Recent pipelines to show for the selected repository.",
                     rich_help_panel=DISPLAY),
    ] = 3,
    branches: Annotated[
        int,
        typer.Option("--branches", "-b", min=1, max=100,
                     help="Most recently updated branches to show, with their latest commit.",
                     rich_help_panel=DISPLAY),
    ] = 10,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", min=1,
                     help="Only list the N most recently updated repositories.",
                     rich_help_panel=DISPLAY, show_default=False),
    ] = None,
    interval: Annotated[
        int,
        typer.Option("--interval", "-i", min=5,
                     help="Seconds between automatic refreshes.", rich_help_panel=REFRESH),
    ] = 30,
    workers: Annotated[
        int,
        typer.Option("--workers", min=1, max=32,
                     help="Parallel API requests.", rich_help_panel=REFRESH),
    ] = 8,
) -> None:
    """
    Browse [bold]Bitbucket[/] repositories and their pipelines.

    Repositories are listed on the left. Use [bold]↑/↓[/] to choose one and press
    [bold]Enter[/] to see its pipelines. Press [bold]/[/] to filter, [bold]r[/] to refresh,
    [bold]o[/] to open in the browser and [bold]q[/] to quit.

    Run [bold]ci-cli login[/] once to store your credentials: the API key goes in
    the OS keychain and the workspace and username in a config file. Options and
    [cyan]BITBUCKET_*[/] environment variables override the stored values.
    """
    if ctx.invoked_subcommand is not None:
        return

    api_key, username, workspace = resolve_credentials(api_key, username, workspace)
    client = BitbucketClient(api_key, username=username)
    ws = resolve_workspace(client, workspace)

    from .app import CIApp

    with terminal_title(APP_NAME):
        CIApp(client, ws, pipelines_per_repo=pipelines, interval=interval,
              workers=workers, limit=limit, branches_per_repo=branches).run()


APP_NAME = "Bitbucket Pipelines"


@contextmanager
def terminal_title(title: str) -> Iterator[None]:
    """Show `title` in the terminal's window or tab while the block runs.

    Uses the xterm escape codes, which Warp, iTerm2, Terminal.app and most
    others understand: save the current title on the terminal's title stack,
    set ours (OSC 0), then restore the saved one when the app exits. Terminals
    without a title stack ignore the save and restore codes.
    """
    out = sys.__stdout__
    if out is None or not out.isatty():
        yield
        return
    out.write(f"\x1b[22;0t\x1b]0;{title}\x07")
    out.flush()
    try:
        yield
    finally:
        out.write("\x1b[23;0t")
        out.flush()


NO_CREDENTIALS_HINT = (
    "Run [bold]ci-cli login[/] to store them, or set [bold]BITBUCKET_API_KEY[/] "
    "(or pass [bold]--api-key[/])."
)
KEYCHAIN_HINT = (
    "Use the [bold]BITBUCKET_API_KEY[/] environment variable (or [bold]--api-key[/]) instead."
)


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def resolve_credentials(
    api_key: str, username: str | None, workspace: str | None
) -> tuple[str, str | None, str | None]:
    """Fill in anything not given as an option or env var from the stored settings.

    Order of precedence: option, then environment variable (both handled by Typer),
    then the keychain and config file. Offers first-run setup if nothing is found.
    """
    try:
        config = credentials.load_config()
    except RuntimeError as exc:
        raise fail(str(exc), "Fix or delete the file, then run [bold]ci-cli login[/].")
    workspace = workspace or config.workspace

    if api_key:
        # The key came from an option or env var. Don't pair it with a stored
        # username, which belongs to the stored key and would switch it to basic auth.
        return api_key, username, workspace

    try:
        stored_key = credentials.get_api_key()
    except CredentialStoreError as exc:
        raise fail(f"Couldn't read the API key from the system keychain: {exc}", KEYCHAIN_HINT)

    if stored_key:
        return stored_key, username or config.username, workspace

    if not is_interactive():
        raise fail("No Bitbucket credentials found.", NO_CREDENTIALS_HINT, 2)
    console.print("[yellow]No Bitbucket credentials found.[/]")
    if not typer.confirm("Set them up now?", default=True):
        raise fail("No Bitbucket credentials found.", NO_CREDENTIALS_HINT, 2)
    result = run_login(workspace=workspace, username=username)
    console.print()
    return result


def run_login(
    workspace: str | None = None, username: str | None = None, verify: bool = True
) -> tuple[str, str | None, str | None]:
    """Prompt for credentials, check them against Bitbucket and store them."""
    try:
        config = credentials.load_config()
    except RuntimeError:
        config = credentials.Config()  # overwritten below

    console.print(Panel(
        "Your API key is stored in the system keychain. "
        "The workspace and username are stored in\n"
        f"[cyan]{credentials.config_path()}[/]\n\n"
        "[dim]Atlassian API token: enter your Atlassian account email as the username.\n"
        "App password: enter your Bitbucket username.\n"
        "Workspace, project or repository access token: leave the username blank.[/]",
        title="[bold]Bitbucket login[/]", border_style="cyan", expand=False,
    ))

    env_key = os.environ.get("BITBUCKET_API_KEY", "").strip()
    api_key = ""
    if env_key and typer.confirm("Use the API key from BITBUCKET_API_KEY?", default=True):
        api_key = env_key
    while not api_key:
        api_key = typer.prompt("API key", hide_input=True, default="", show_default=False).strip()

    if username is None:
        default = config.username or os.environ.get("BITBUCKET_USERNAME") \
            or os.environ.get("BITBUCKET_EMAIL") or ""
        username = typer.prompt(
            "Username or email (blank for an access token)",
            default=default, show_default=bool(default),
        ).strip()
    username = username or None

    if workspace is None:
        default = config.workspace or os.environ.get("BITBUCKET_WORKSPACE") or ""
        workspace = typer.prompt(
            "Workspace slug (blank to auto-detect)",
            default=default, show_default=bool(default),
        ).strip()
    workspace = workspace or None

    if verify:
        workspace = verify_login(api_key, username, workspace)

    try:
        credentials.set_api_key(api_key)
    except CredentialStoreError as exc:
        raise fail(f"Couldn't save the API key to the system keychain: {exc}", KEYCHAIN_HINT)
    path = credentials.save_config(credentials.Config(workspace=workspace, username=username))

    console.print("[green]✓[/] API key saved to the system keychain.")
    console.print(f"[green]✓[/] Settings saved to [cyan]{path}[/].")
    if env_key:
        console.print(
            "[yellow]BITBUCKET_API_KEY is still set in your environment and takes precedence "
            "over the stored key. Remove it from your shell profile to use the keychain.[/]"
        )
    return api_key, username, workspace


def verify_login(api_key: str, username: str | None, workspace: str | None) -> str | None:
    """Check the credentials work. Returns the workspace, auto-detected if not given."""
    client = BitbucketClient(api_key, username=username)
    try:
        with console.status("Checking credentials with Bitbucket…"):
            if workspace:
                client.list_repositories(workspace, limit=1)
            else:
                workspaces = client.list_workspaces()
    except Exception as exc:  # noqa: BLE001 - API, network errors etc.
        err_console.print(f"[red]Bitbucket rejected these details: {exc}[/]")
        if not workspace:
            err_console.print(
                "[dim]Workspace access tokens can't list workspaces. "
                "Enter the workspace slug to check against a specific workspace.[/]"
            )
        if not typer.confirm("Save them anyway?", default=False):
            raise typer.Exit(1)
        return workspace

    if workspace:
        console.print(f"[green]✓[/] Credentials work for workspace [bold]{workspace}[/].")
        return workspace
    if not workspaces:
        console.print("[yellow]The credentials work, but no workspaces were found.[/]")
        return None
    if len(workspaces) == 1:
        chosen = workspaces[0]
    else:
        for i, ws in enumerate(workspaces, 1):
            console.print(f"  [bold]{i}[/]. {ws}")
        chosen = None
        while chosen is None:
            answer = typer.prompt("Default workspace (number or slug)", default="1").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(workspaces):
                chosen = workspaces[int(answer) - 1]
            elif answer in workspaces:
                chosen = answer
            else:
                err_console.print(f"[red]Choose 1-{len(workspaces)} or one of the slugs above.[/]")
    console.print(f"[green]✓[/] Credentials work. Default workspace: [bold]{chosen}[/].")
    return chosen


@app.command()
def login(
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", "-w", show_default=False,
                     help="Default workspace slug. Prompted for if omitted."),
    ] = None,
    username: Annotated[
        str | None,
        typer.Option("--username", "-u", show_default=False,
                     help="Atlassian email or username. Prompted for if omitted."),
    ] = None,
    verify: Annotated[
        bool,
        typer.Option("--verify/--no-verify", help="Check the credentials with Bitbucket first."),
    ] = True,
) -> None:
    """
    Store your Bitbucket credentials.

    The API key is prompted for without echoing and saved in the system keychain.
    The workspace and username are saved in [cyan]~/.config/ci-cli/config.toml[/].
    """
    if not is_interactive():
        raise fail("ci-cli login needs an interactive terminal.")
    run_login(workspace=workspace, username=username, verify=verify)


@app.command()
def logout() -> None:
    """Remove the stored API key from the keychain and delete the config file."""
    try:
        removed_key = credentials.delete_api_key()
    except CredentialStoreError as exc:
        raise fail(f"Couldn't remove the API key from the system keychain: {exc}")
    removed_config = credentials.delete_config()

    if removed_key:
        console.print("[green]✓[/] Removed the API key from the system keychain.")
    if removed_config:
        console.print(f"[green]✓[/] Deleted [cyan]{credentials.config_path()}[/].")
    if not (removed_key or removed_config):
        console.print("Nothing to remove: no stored credentials were found.")
    if os.environ.get("BITBUCKET_API_KEY"):
        console.print("[yellow]BITBUCKET_API_KEY is still set in your environment.[/]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
