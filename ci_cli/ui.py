"""Shared display helpers for the Textual screens."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text

STATUS_STYLE = {
    "SUCCESSFUL": ("✔", "bold green"),
    "FAILED": ("✘", "bold red"),
    "ERROR": ("✘", "bold red"),
    "STOPPED": ("■", "yellow"),
    "EXPIRED": ("■", "dim"),
    "NOT_RUN": ("–", "dim"),
    "RUNNING": ("●", "bold cyan"),
    "PAUSED": ("‖", "magenta"),
    "HALTED": ("‖", "magenta"),
    "PENDING": ("○", "blue"),
    "READY": ("○", "blue"),
}


def status_text(status: str, with_label: bool = True) -> Text:
    icon, style = STATUS_STYLE.get(status, ("?", "white"))
    return Text(f"{icon} {status.lower().replace('_', ' ')}" if with_label else icon, style=style)


def fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    m, s = divmod(max(seconds, 0), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def shorten(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 1] + "…"


def fmt_when(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.astimezone().strftime("%d %b %Y %H:%M")
