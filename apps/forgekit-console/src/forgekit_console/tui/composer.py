"""Composer — a Claude-Code-style input BAR with secondary info BELOW it.

Spec = the Claude Code terminal composer:

    ──────────────────────────────────────────  ← top rule (light grey, full width)
     > (your input)                              ← the input row (marker + input ONLY)
    ──────────────────────────────────────────  ← bottom rule (light grey, full width)
     ▶▶ operator · /help · / palette · ^C quit    ← #hint   (secondary, BELOW the bar)
       (mode pill / palette list also live below)

Key points (Claude fidelity):

* The input bar is bounded by a **light/near-white** top + bottom rule (``$input-rule``)
  so it reads clearly as the input — full width, no side box.
* NOTHING sits ABOVE the bar. The mode indicator (``#modepill``) and the shortcut /
  mode-switch line (``#hint``) are SECONDARY and live BELOW the bar. In the default
  idle (operator, empty input) state only the ``#hint`` mode line shows; while the
  user is typing it is reduced; when the slash palette is open the palette takes the
  space. The app drives that visibility.
* The slash palette (``#palette``) is a separate flat surface below — no left rule.

It owns no command logic — the app drives ``#modepill`` / ``#hint`` / palette / focus.
"""

from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from . import theme
from .palette import CommandPalette
from .prompt_area import PromptArea


class Composer(Vertical):
    """Input bar (light rules) + secondary mode/hint/palette BELOW it."""

    DEFAULT_CSS = """
    Composer {
        height: auto;
        margin: 1 0 0 0;   /* a clear gap above; FULL width (no side inset) */
        background: $background;
        padding: 0;
    }
    /* THE input bar — bounded by a light/near-white top + bottom rule (Claude). */
    Composer #composer-input-shell {
        height: auto;
        background: $background;
        border-top: solid $input-rule;
        border-bottom: solid $input-rule;
        padding: 0 1;
    }
    Composer #marker {
        width: auto;
        padding: 0 1 0 0;
        color: $accent;
    }
    /* multiline: auto height (1 row for one line, grows with real newlines). */
    Composer #prompt {
        width: 1fr;
        border: none;
        background: $background;
        height: auto;
        padding: 0;
    }
    /* secondary — BELOW the bar. mode pill (agent/palette only) then the hint line. */
    Composer #modepill {
        height: auto;
        padding: 0 0 0 1;
    }
    Composer #hint {
        height: auto;
        padding: 0 0 0 1;
        color: $text-muted;
    }
    """

    def compose(self):
        # the input BAR first — full-width rule strip, marker `>` + input only.
        with Horizontal(id="composer-input-shell"):
            yield Static(f"[{theme.ACCENT_PRIMARY}]>[/{theme.ACCENT_PRIMARY}]", id="marker")
            yield PromptArea(id="prompt")
        # SECONDARY rows — BELOW the bar (never above it).
        yield Static(id="modepill")   # mode indicator (agent/palette); hidden in idle operator
        yield Static(id="hint")       # the shortcut / mode-switch line (idle only)
        # the slash palette — a separate flat surface below (no left rule).
        yield CommandPalette(id="palette")


__all__ = ("Composer",)
