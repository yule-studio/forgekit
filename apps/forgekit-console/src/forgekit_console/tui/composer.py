"""Composer — the SESSION-FOLLOWING inline chat input (mode pill + input + palette).

This is the key Claude-Code fix. The composer is **part of the top-aligned
session flow**, not a footer docked to the viewport bottom. It renders IMMEDIATELY
AFTER the active content (transcript OR help view) and follows it: a short session
leaves it near the top with empty space below; as the transcript grows it is
pushed down (the enclosing :class:`tui.session_flow.SessionFlow` scroll keeps it
in view). It is ``height: auto`` and carries NO ``dock`` — that is what makes it
flow inline instead of pinning to the bottom.

A prominent top border/rule above the input still makes the chat bar read as a
distinct, bordered bar (like Claude Code's input box) — it is just inline, not
docked. The slash palette opens inline just above the input (still inside the
composer), so it reads as part of the input flow rather than a floating dialog.

The widget owns no command logic; the app drives it (focus, value, palette
show/hide, mode pill). Pure render strings come from :mod:`tui.render`.
"""

from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from .palette import CommandPalette


class Composer(Vertical):
    """Inline session-following composer: palette (top) + mode pill + input (bottom)."""

    DEFAULT_CSS = """
    Composer {
        /* NO dock — the composer flows inline right after the active content. */
        height: auto;
        background: $background;
        /* prominent rule above the input so the chat bar reads as a distinct bar */
        border-top: heavy $accent;
        padding: 0;
    }
    Composer #inputrow {
        height: 1;
        padding: 0 1;
    }
    Composer #modepill {
        width: auto;
        padding: 0 1 0 0;
    }
    Composer #prompt {
        border: none;
        background: $background;
        height: 1;
        padding: 0;
    }
    """

    def compose(self):
        yield CommandPalette(id="palette")
        with Horizontal(id="inputrow"):
            yield Static(id="modepill")
            yield Input(placeholder="명령 입력 — `/help` 로 시작", id="prompt")


__all__ = ("Composer",)
