"""Composer — a Claude-Code-style input BAR (full-width rule bar + hint + palette).

Spec = the Claude Code terminal composer: a full-width input strip bounded by a
TOP and BOTTOM horizontal rule (no side box, no inset), with the ``>`` prompt + the
input inside it, the shortcut hints on a row BELOW the bar, and the slash command
list as a separate surface below that. Top→bottom:

    transcript ……………………………………………  (the main panel, above)
    ─────────────────────────────────────────   ← #composer-input-shell top rule
     > (your input)                              ← the BAR (marker + input ONLY)
    ─────────────────────────────────────────   ← bottom rule (full width)
     /help · / palette · Tab 완성 · ^C quit       ← #hint   (OUTSIDE the bar)
     ▎ /help   /harness   …                       ← #palette (separate surface, below)

Notes:

* In the default **operator** state there is NO mode row above the bar (the app
  hides ``#modepill``); the prompt bar is the first thing read, like Claude. The
  mode indicator only appears for agent / palette states.
* The bar is full width (top+bottom rules span the screen), not an inset rounded
  box — that is what reads as the Claude input bar.
* Hints live OUTSIDE the bar; the palette is its own surface below — never inside
  the input, never bleeding into the transcript.
* In the help/tab view the whole composer is hidden by the app.

It owns no command logic — the app drives ``#modepill`` / ``#hint`` / palette / focus.
"""

from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from . import theme
from .palette import CommandPalette


class Composer(Vertical):
    """Input region: (mode row, hidden in idle) · input BAR · hint row · palette."""

    DEFAULT_CSS = """
    Composer {
        height: auto;
        margin: 1 0 0 0;   /* a clear gap above; FULL width (no side inset) */
        background: $background;
        padding: 0;
    }
    /* mode indicator — its own row above the bar. Hidden by the app in the default
       operator state (shown only for agent / palette states). */
    Composer #modepill {
        height: auto;
        padding: 0 0 0 1;
    }
    /* THE input bar — a full-width strip bounded by a top + bottom rule (Claude).
       No side borders, no inset: the rules span the whole width. */
    Composer #composer-input-shell {
        height: auto;
        background: $background;
        border-top: solid $brand-border;
        border-bottom: solid $brand-border;
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
        background: $background;
        height: 1;
        padding: 0;
    }
    /* shortcut hints — their own row BELOW the bar (outside it). */
    Composer #hint {
        height: 1;
        padding: 0 0 0 1;
        color: $text-muted;
    }
    """

    def compose(self):
        # mode row (hidden in idle by the app) — above the bar.
        yield Static(id="modepill")
        # the input BAR — full-width top+bottom rule strip, marker `>` + input only.
        with Horizontal(id="composer-input-shell"):
            yield Static(f"[{theme.ACCENT_PRIMARY}]>[/{theme.ACCENT_PRIMARY}]", id="marker")
            yield Input(placeholder="", id="prompt")
        # hint row — outside the bar, below it.
        yield Static(id="hint")
        # the slash palette — a separate compact surface below everything.
        yield CommandPalette(id="palette")


__all__ = ("Composer",)
