# ci-cli

A terminal app for browsing the repositories in a Bitbucket Cloud workspace and their pipelines.

Repositories are listed down the left, each with an icon for the status of its latest pipeline. Choose one with the arrow keys and press Enter to see its recent pipelines on the right. The steps of the highlighted pipeline appear underneath.

## Install

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Build a standalone executable

[PyInstaller](https://pyinstaller.org) bundles the app and its Python runtime into one file, so it runs on machines without Python:

```sh
pip install -e '.[build]'
pyinstaller --noconfirm --clean ci-cli.spec
./dist/ci-cli -h
```

The result is `dist/ci-cli` (about 21 MB). PyInstaller doesn't cross-compile: the executable runs only on the OS and CPU architecture it was built on, so build on each platform you want to ship. `build/` and `dist/` are generated output and can be deleted.

The spec file gathers all of Textual, Rich and Pygments because they import widgets, lexers and data files at runtime, which PyInstaller's import scanning can't see. It also bundles keyring's backends and package metadata, because keyring finds its backends through entry points. Without them the executable couldn't use the system keychain. `packaging/launcher.py` is the entry script, because PyInstaller can't start from `ci_cli/main.py` directly (it uses package-relative imports).

## Configure

Run `ci-cli login` once. It asks for your API key (without echoing it), your username and your default workspace, checks them with Bitbucket, and stores them:

- The **API key** goes in the operating system's credential store: the login Keychain on macOS, Secret Service (GNOME Keyring or KWallet) on Linux, Credential Manager on Windows. It is never written to a file.
- The **workspace** and **username** go in `~/.config/ci-cli/config.toml` (or `$XDG_CONFIG_HOME/ci-cli/config.toml`), created with `600` permissions.

If you start `ci-cli` with nothing stored, it offers to run the setup. `ci-cli logout` removes the stored key and deletes the config file. Run `ci-cli login` again to replace any of the details.

What to enter for the username depends on the kind of key:

| Key type | Username |
|---|---|
| Atlassian API token | Your Atlassian account email |
| App password | Your Bitbucket username |
| Workspace, project or repository access token | Leave blank (the key is sent as a Bearer token) |

The token needs the `repository` (read) and `pipeline` (read) scopes. To discover workspaces automatically it also needs `account` (read). Workspace access tokens can't list workspaces, so enter the workspace slug when you use one.

### Overriding the stored values

Options and environment variables take precedence over the stored values, which is useful in CI or other places without a keychain:

| Option | Variable |
|---|---|
| `--api-key` | `BITBUCKET_API_KEY` |
| `--workspace` / `-w` | `BITBUCKET_WORKSPACE` |
| `--username` / `-u` | `BITBUCKET_USERNAME` (or `BITBUCKET_EMAIL`) |

An option beats its variable, and both beat the stored value. When the key comes from an option or variable, the stored username isn't used with it, because it belongs to the stored key. If you're moving from environment variables, `ci-cli login` offers to store the key from `BITBUCKET_API_KEY`. Remove the variable from your shell profile afterwards, or it will keep overriding the keychain.

## Run

```sh
ci-cli                       # start the app
ci-cli -w my-workspace       # choose the workspace
ci-cli -n 10                 # show the last 10 pipelines instead of 3
ci-cli -i 10                 # auto-refresh every 10s
ci-cli --limit 50            # only list the 50 most recently updated repos
ci-cli -h                    # full help
```

## Keys

| Key | Action |
|---|---|
| ↑ / ↓ | Move through the repository list (or pipelines table) |
| Enter | Show pipelines for the highlighted repository |
| Tab / Shift+Tab | Switch between the repository list and the pipelines table |
| / | Filter repositories by name. Enter returns to the list, Esc clears the filter |
| r | Reload the repositories and the pipelines being shown |
| o | Open the repository's pipelines (or the highlighted pipeline) in your web browser |
| q | Quit (Ctrl+C also quits, even while typing in the filter) |

In the list, ▶ marks the repository whose pipelines are being shown.

## Pipeline details

Tab into the pipelines table, highlight a pipeline and press Enter to open its detail screen:

- **Summary:** status, branch or pull request (source → destination), commit message and author, who triggered it and how, which pipeline definition ran, duration, build minutes used and re-run number.
- **Steps** (left): each step's status and duration, with manual steps marked. The highlighted step's details are shown underneath: image, trigger, max time, build time and the script commands it runs.
- **Log** (right): the highlighted step's log, with colour codes stripped. For a running step it follows new output live.
- **Tests:** if the step published test reports, a summary of passed, failed, errored and skipped tests appears above the log, with each failing test and its error message.

The screen opens on the step you're most likely to want: the failed step, otherwise the running step, otherwise the last one that finished.

| Key | Action |
|---|---|
| ↑ / ↓ | Choose a step (its log loads straight away) |
| Enter / Tab | Move into the log to scroll it (PgUp/PgDn, Home/End) |
| / | Search the log. Enter jumps to the first match, then n / N for next and previous |
| f | Turn live following of a running step's log on or off |
| r | Reload the pipeline, steps, log and tests |
| o | Open this pipeline step in your web browser |
| Esc | Clear the search, or go back to the repository list |

## Refreshing

The pipelines on the right refresh every `--interval` seconds, and the durations of running pipelines and steps tick every second. Status icons in the list are loaded once at startup. After that, only repos whose latest pipeline is still running are re-checked, which keeps API usage low with many repositories. Press `r` to reload everything.
