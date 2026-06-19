"""Key bindings + hints — pure data, no textual.

The app builds its ``BINDINGS`` from :data:`ACTION_BINDINGS` and the footer / help
overlay read :data:`KEY_HINTS`. Keeping this declarative keeps the keymap in one
place and lets a test assert the contract without a terminal. (Tab / arrows / Esc
are handled in the app's key handler rather than as global bindings so they can
cooperate with the focused input; they appear here only as hints.)
"""

from __future__ import annotations

from typing import Tuple

# (key, action_name, description) — registered as textual global bindings.
ACTION_BINDINGS: Tuple[Tuple[str, str, str], ...] = (
    ("ctrl+c", "quit", "Quit"),
    ("ctrl+l", "clear_log", "Clear"),
    ("ctrl+r", "refresh_status", "Refresh"),
    ("f1", "open_help", "Help"),
)

# (key, description) — shown in the footer hint line / help overlay. Includes the
# input-cooperating keys handled in the app's on_key.
KEY_HINTS: Tuple[Tuple[str, str], ...] = (
    ("/", "command palette"),
    ("Tab", "autocomplete / next"),
    ("Shift+Tab", "previous"),
    ("↑/↓", "cycle candidates"),
    ("Enter", "run"),
    ("^J", "newline (multiline)"),
    ("Esc", "close palette / exit agent"),
    ("F1", "help"),
    ("^L", "clear"),
    ("^R", "refresh"),
    ("^C", "quit"),
)


def footer_hint() -> str:
    """One-line hint string for the input chrome."""

    return "  ".join(f"{key} {desc}" for key, desc in KEY_HINTS)


__all__ = ("ACTION_BINDINGS", "KEY_HINTS", "footer_hint")
