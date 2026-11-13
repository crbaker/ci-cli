<div align="center">

# 🚦 ci-cli

**Your Bitbucket pipelines, live in the terminal.**

Browse every repository in a Bitbucket Cloud workspace, see what's green and what's on fire, and dig into any step's log without opening a browser.

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![Textual](https://img.shields.io/badge/built%20with-Textual-5A4FCF)
![Bitbucket Cloud](https://img.shields.io/badge/Bitbucket-Cloud-0052CC?logo=bitbucket&logoColor=white)
![Version](https://img.shields.io/badge/version-0.1.0-informational)

[Quick start](#-quick-start) · [Configure](#-configure) · [Run](#%EF%B8%8F-run) · [Keys](#%EF%B8%8F-keys) · [Pipeline details](#-pipeline-details) · [Standalone build](#-build-a-standalone-executable)

</div>

---

```text
┌─ Bitbucket Pipelines ─────────────────────────────────── my-workspace ─┐
│ ✔ api-gateway        │ my-workspace/web-frontend                       │
│ ✘ billing-service    │  #   Status        Branch   Commit   Duration   │
│ ● data-pipeline      │  412 ● running     main     a1b2c3d  2m14s      │
│ ▶ web-frontend ✘     │  411 ✘ failed      fix/nav  9f8e7d6  4m02s      │
│ ✔ infra-terraform    │  410 ✔ successful  main     5c4b3a2  3m47s      │
│ ‖ mobile-app         │                                                 │
│                      │ Steps of #412                                   │
│                      │  Build & test    ✔ successful  1m31s            │
│                      │  Deploy staging  ● running     0m43s            │
└──────────────────────┴─────────────────────────────────────────────────┘
```

## ✨ Highlights

- 🗂️ **Whole workspace at a glance.** Every repo, each tagged with the status of its latest pipeline.
- 🔍 **Drill down fast.** Repo → recent pipelines → steps → live log, all from the keyboard.
- 📜 **Live logs.** Running steps stream new output as it arrives, with search (`/`, `n`, `N`).
- 🧪 **Test reports.** Passed / failed / errored / skipped counts, plus each failing test and its error.
- 🔐 **Keychain-backed secrets.** Your API key lives in the OS credential store, never in a file.
- 🪶 **Light on the API.** After startup, only repos with a running pipeline are re-checked.
- 📦 **Ships without Python.** Build a standalone bundle with PyInstaller.

### Status legend

| Icon | Meaning |
|:---:|---|
| ✔ | Successful |
| ✘ | Failed or errored |
| ● | Running |
| ○ | Pending or ready |
| ‖ | Paused or halted |
| ■ | Stopped or expired |
| – | Not run |
| ▶ | The repository whose pipelines are being shown |

---

## 🚀 Quick start

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

ci-cli login   # one-time setup
ci-cli         # 🎉
```

---

## 🔑 Configure

Run `ci-cli login` once. It asks for your API key (without echoing it), your username and your default workspace, checks them with Bitbucket, and stores them:

| What | Where it's stored |
|---|---|
| 🔐 **API key** | The OS credential store: the login Keychain on macOS, Secret Service (GNOME Keyring or KWallet) on Linux, Credential Manager on Windows. Never written to a file. |
| 📝 **Workspace** and **username** | `~/.config/ci-cli/config.toml` (or `$XDG_CONFIG_HOME/ci-cli/config.toml`), created with `600` permissions. |

If you start `ci-cli` with nothing stored, it offers to run the setup. `ci-cli logout` removes the stored key and deletes the config file. Run `ci-cli login` again to replace any of the details.

### Which username?

It depends on the kind of key:

| Key type | Username |
|---|---|
| Atlassian API token | Your Atlassian account email |
| App password | Your Bitbucket username |
| Workspace, project or repository access token | Leave blank (the key is sent as a Bearer token) |

> [!IMPORTANT]
> The token needs the `repository` (read) and `pipeline` (read) scopes. To discover workspaces automatically it also needs `account` (read). Workspace access tokens can't list workspaces, so enter the workspace slug when you use one.

### Overriding the stored values

Options and environment variables take precedence over the stored values, which is handy in CI or anywhere without a keychain:

| Option | Variable |
|---|---|
| `--api-key` | `BITBUCKET_API_KEY` |
| `--workspace` / `-w` | `BITBUCKET_WORKSPACE` |
| `--username` / `-u` | `BITBUCKET_USERNAME` (or `BITBUCKET_EMAIL`) |

An option beats its variable, and both beat the stored value. When the key comes from an option or variable, the stored username isn't used with it, because it belongs to the stored key.

> [!TIP]
> Moving from environment variables? `ci-cli login` offers to store the key from `BITBUCKET_API_KEY`. Remove the variable from your shell profile afterwards, or it will keep overriding the keychain.

---

## ▶️ Run

```sh
ci-cli                       # start the app
ci-cli -w my-workspace       # choose the workspace
ci-cli -n 10                 # show the last 10 pipelines instead of 3
ci-cli -i 10                 # auto-refresh every 10s
ci-cli --limit 50            # only list the 50 most recently updated repos
ci-cli -h                    # full help
```

Repositories are listed down the left. Choose one with the arrow keys and press <kbd>Enter</kbd> to see its recent pipelines on the right; the steps of the highlighted pipeline appear underneath.

---

## ⌨️ Keys

| Key | Action |
|---|---|
| <kbd>↑</kbd> / <kbd>↓</kbd> | Move through the repository list (or pipelines table) |
| <kbd>Enter</kbd> | Show pipelines for the highlighted repository |
| <kbd>Tab</kbd> / <kbd>Shift</kbd>+<kbd>Tab</kbd> | Switch between the repository list and the pipelines table |
| <kbd>/</kbd> | Filter repositories by name. <kbd>Enter</kbd> returns to the list, <kbd>Esc</kbd> clears the filter |
| <kbd>r</kbd> | Reload the repositories and the pipelines being shown |
| <kbd>o</kbd> | Open the repository's pipelines (or the highlighted pipeline) in your web browser |
| <kbd>q</kbd> | Quit (<kbd>Ctrl</kbd>+<kbd>C</kbd> also quits, even while typing in the filter) |

---

## 🔬 Pipeline details

Tab into the pipelines table, highlight a pipeline and press <kbd>Enter</kbd> to open its detail screen:

- 📋 **Summary:** status, branch or pull request (source → destination), commit message and author, who triggered it and how, which pipeline definition ran, duration, build minutes used and re-run number.
- 🪜 **Steps** (left): each step's status and duration, with manual steps marked. The highlighted step's details are shown underneath: image, trigger, max time, build time and the script commands it runs.
- 📜 **Log** (right): the highlighted step's log, with colour codes stripped. For a running step it follows new output live.
- 🧪 **Tests:** if the step published test reports, a summary of passed, failed, errored and skipped tests appears above the log, with each failing test and its error message.

The screen opens on the step you're most likely to want: the failed step, otherwise the running step, otherwise the last one that finished.

| Key | Action |
|---|---|
| <kbd>↑</kbd> / <kbd>↓</kbd> | Choose a step (its log loads straight away) |
| <kbd>Enter</kbd> / <kbd>Tab</kbd> | Move into the log to scroll it (<kbd>PgUp</kbd>/<kbd>PgDn</kbd>, <kbd>Home</kbd>/<kbd>End</kbd>) |
| <kbd>/</kbd> | Search the log. <kbd>Enter</kbd> jumps to the first match, then <kbd>n</kbd> / <kbd>N</kbd> for next and previous |
| <kbd>f</kbd> | Turn live following of a running step's log on or off |
| <kbd>r</kbd> | Reload the pipeline, steps, log and tests |
| <kbd>o</kbd> | Open this pipeline step in your web browser |
| <kbd>Esc</kbd> | Clear the search, or go back to the repository list |

---

## 🔄 Refreshing

The pipelines on the right refresh every `--interval` seconds, and the durations of running pipelines and steps tick every second. Status icons in the list are loaded once at startup. After that, only repos whose latest pipeline is still running are re-checked, which keeps API usage low with many repositories. Press <kbd>r</kbd> to reload everything.

---

## 📦 Build a standalone executable

[PyInstaller](https://pyinstaller.org) bundles the app and its Python runtime into a folder, so it runs on machines without Python:

```sh
pip install -e '.[build]'
pyinstaller --noconfirm --clean ci-cli.spec
./dist/ci-cli/ci-cli -h
```

The result is the `dist/ci-cli/` folder (about 45 MB): the `ci-cli` executable plus an `_internal/` folder it needs beside it. Ship or install the whole folder, and put a symlink to `ci-cli` on your `PATH` to run it from anywhere. `build/` and `dist/` are generated output and can be deleted.

> [!NOTE]
> PyInstaller doesn't cross-compile: the build runs only on the OS and CPU architecture it was built on, so build on each platform you want to ship.

<details>
<summary><b>Why a one-folder build?</b></summary>

<br>

A single-file executable unpacks its ~900 bundled files into a new temporary folder on every launch, and macOS scans each newly seen native library before loading it, so every start took about 6 seconds. The folder is unpacked once: the first launch still takes a few seconds while macOS scans it, and later launches take about **0.2 seconds**.

</details>

<details>
<summary><b>What the spec file does</b></summary>

<br>

- It gathers all of Textual, Rich and Pygments, because they import widgets, lexers and data files at runtime, which PyInstaller's import scanning can't see.
- It bundles keyring's backends and package metadata, because keyring finds its backends through entry points. Without them the executable couldn't use the system keychain.
- `packaging/launcher.py` is the entry script, because PyInstaller can't start from `ci_cli/main.py` directly (it uses package-relative imports).

</details>

---

<div align="center">

Built with 🐍 [Textual](https://textual.textualize.io) · [Rich](https://rich.readthedocs.io) · [Typer](https://typer.tiangolo.com)

</div>
