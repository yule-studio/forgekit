"""Composer — a Claude-Code-style input BAR with the slash palette DIRECTLY BELOW it.

Spec = the Claude Code terminal composer. The composer is a BOTTOM-DOCKED zone (the
transcript fills the space above it). When a slash is typed the command list opens
flush UNDER the input box — instantly visible, no scroll — exactly like Claude:

    ──────────────────────────────────────────  ← top rule (light grey, full width)
     > /he                                        ← the input row (marker + input ONLY)
    ──────────────────────────────────────────  ← bottom rule (light grey, full width)
     /help    이 콘솔의 명령 목록                       ← #palette  (DIRECTLY below the bar,
     /hephaistos  Hephaistos skill-forge 상태          only while typing a slash)
     ▶▶ operator · /help · / palette · ^C quit    ← #hint   (secondary, below the palette)

Key points (Claude fidelity):

* The input bar (``#composer-input-shell``) is FIRST; the slash palette (``#palette``)
  is the next child so it opens DIRECTLY BELOW the input bar (gap ≈ 0 rows). The whole
  composer is bottom-docked (see the app layout: SessionFlow ``1fr`` above, composer
  ``auto`` below), so the palette is part of the COMMAND-ENTRY ZONE — never the
  transcript — and is always on-screen the instant `/` is pressed.
* The input bar is bounded by a **light/near-white** top + bottom rule (``$input-rule``)
  so it reads clearly as the input — full width, no side box.
* The mode indicator (``#modepill``) and the shortcut / mode-switch line (``#hint``)
  are SECONDARY and live below the palette. The app drives their visibility.

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
        background: transparent;
        padding: 0;
        scrollbar-size-vertical: 0;   /* the composer never owns scroll — no gutter */
    }
    /* THE input bar — bounded by a light/near-white top + bottom rule (Claude). */
    Composer #composer-input-shell {
        height: auto;
        background: transparent;
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
        background: transparent;
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
        # the input BAR FIRST — full-width rule strip, marker `>` + input only.
        with Horizontal(id="composer-input-shell"):
            yield Static(f"[{theme.ACCENT_PRIMARY}]>[/{theme.ACCENT_PRIMARY}]", id="marker")
            yield PromptArea(id="prompt")
        # the slash palette DIRECTLY BELOW the input bar (Claude-style): the command
        # list opens flush under the input box, inside the bottom-docked composer zone,
        # so it is instantly visible without any scroll. It is NOT part of the transcript.
        yield CommandPalette(id="palette")
        # SECONDARY rows — below the palette.
        yield Static(id="modepill")   # mode indicator (agent); hidden in idle operator
        yield Static(id="hint")       # the shortcut / mode-switch line (idle only)


__all__ = ("Composer",)
