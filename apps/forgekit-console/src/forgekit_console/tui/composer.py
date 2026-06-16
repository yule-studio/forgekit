"""Composer — the fixed bottom chat input (mode pill + input + inline palette).

This is the key Claude-Code fix: the composer is **always visible**, docked to
the bottom of the screen, and never hidden — not even when ``/help`` switches the
main area to the help view. A prominent top border/rule above the input makes the
chat bar read as a distinct, separate bar (like Claude Code's input box). The
slash palette opens inline just above the input (still inside the composer), so
it reads as part of the input flow rather than a floating dialog.

The widget owns no command logic; the app drives it (focus, value, palette
show/hide, mode pill). Pure render strings come from :mod:`tui.render`.
"""

from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from .palette import CommandPalette


class Composer(Vertical):
    """Fixed bottom composer: inline palette (top) + mode pill + input (bottom)."""

    DEFAULT_CSS = """
    Composer {
        dock: bottom;
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
