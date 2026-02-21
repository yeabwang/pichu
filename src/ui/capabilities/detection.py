from __future__ import annotations

import os
import sys

from rich.console import Console


def is_interactive_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def is_dumb_terminal() -> bool:
    return os.environ.get("TERM", "").strip().lower() == "dumb"


def is_color_supported() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if is_dumb_terminal():
        return False

    colorterm = os.environ.get("COLORTERM", "").strip()
    if colorterm:
        return True

    term = os.environ.get("TERM", "").strip().lower()
    if not term:
        return False
    return any(token in term for token in ("color", "ansi", "xterm", "screen", "tmux", "vt100", "linux", "cygwin"))


def is_console_terminal(console: Console) -> bool:
    return bool(getattr(console, "is_terminal", False))


def is_input_interactive(console: Console) -> bool:
    return is_console_terminal(console) and is_interactive_tty()
