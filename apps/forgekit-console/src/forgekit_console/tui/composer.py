"""Composer — a Claude-style input region with FOUR separated rows.

The input BOX holds ONLY the prompt marker + the actual input. Everything else —
the mode indicator and the shortcut hints — lives OUTSIDE the box, and the slash
palette opens as its own surface below it. Top→bottom:

    transcript ……………………………………………  (the main panel, above)
      ● operator                              ← #modepill   (meta, OUTSIDE the box)
    ╭───────────────────────────────────────╮
    │ ›  (your input)                         │ ← #composer-input-shell  (the BOX: marker + input ONLY)
    ╰───────────────────────────────────────╯
      /help · / palette · Tab 완성 · ^C quit    ← #hint       (hints, OUTSIDE the box)
      ▎ /help   /harness   …                    ← #palette    (slash list, BELOW the box)

Why this anatomy: previously the mode pill and the hint text sat INSIDE the
bordered shell, so the input "box" looked like it was full of mode/help/Tab/quit
text instead of being a clean input. Now:

* ``#modepill`` (mode) is a row ABOVE the box.
* ``#composer-input-shell`` is the input BAR — a filled, rounded box (background +
  border contrast so it clearly reads as "the input bar") containing ONLY the accent
  ``›`` marker and the input. No placeholder clutter; ``/`` is just typed text.
* ``#hint`` (``/help · / palette · Tab · ^C quit``) is a row BELOW the box.
* ``#palette`` is a separate compact surface below the hint — never inside the box,
  never in the transcript (see :class:`tui.palette.CommandPalette`).

The composer follows the session flow (NO ``dock``; ``height: auto``). It owns no
command logic — the app drives ``#modepill`` / ``#hint`` / palette / focus. The
mode + hint strings come from :mod:`tui.render`.
"""

from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from . import theme
from .palette import CommandPalette


class Composer(Vertical):
    """Input region: mode row · input BOX (marker+input only) · hint row · palette."""

    DEFAULT_CSS = """
    Composer {
        height: auto;
        margin: 1 1 0 1;   /* a clear gap from the transcript above */
        background: $background;
        padding: 0;
    }
    /* mode indicator — its own row ABOVE the input box (outside it). */
    Composer #modepill {
        height: 1;
        padding: 0 0 0 2;
    }
    /* THE input bar — a filled, rounded box with marker + input ONLY. The
       background/border contrast makes it read clearly as "the input bar". */
    Composer #composer-input-shell {
        height: 1;                       /* +round border = a 3-row bar */
        background: $surface;
        border: round $accent-dim;
        padding: 0 1;
    }
    Composer #marker {
        width: auto;
        padding: 0 1 0 0;
        color: $accent;
    }
    Composer #prompt {
        width: 1fr;
        border: none;
        background: $surface;            /* match the shell — one clean bar */
        height: 1;
        padding: 0;
    }
    /* shortcut hints — their own row BELOW the input box (outside it). */
    Composer #hint {
        height: 1;
        padding: 0 0 0 2;
        color: $text-muted;
    }
    """

    def compose(self):
        # meta row: mode indicator — OUTSIDE the input box, above it.
        yield Static(id="modepill")
        # the input BOX — ONLY the accent marker + the actual input (clean).
        with Horizontal(id="composer-input-shell"):
            yield Static(f"[{theme.ACCENT_PRIMARY}]›[/{theme.ACCENT_PRIMARY}]", id="marker")
            yield Input(placeholder="", id="prompt")
        # hint row: shortcuts — OUTSIDE the input box, below it.
        yield Static(id="hint")
        # the slash palette — a separate compact surface BELOW everything.
        yield CommandPalette(id="palette")


__all__ = ("Composer",)
