"""Composer — a Claude-Code-style input BAR with the slash palette ABOVE it.

Spec = the Claude Code terminal composer. The palette opens UPWARD (above the
input), exactly like Claude — the hand stays on the input bar and the candidates
float just above it:

     skills                                       ← #palette  (opens ABOVE the bar,
     /help    이 콘솔의 명령 목록                       only while typing a slash)
     /copy    마지막 응답 클립보드 복사
    ──────────────────────────────────────────  ← top rule (light grey, full width)
     > /he                                        ← the input row (marker + input ONLY)
    ──────────────────────────────────────────  ← bottom rule (light grey, full width)
     ▶▶ operator · /help · / palette · ^C quit    ← #hint   (secondary, BELOW the bar)

Key points (Claude fidelity):

* The slash palette (``#palette``) is the FIRST child — it renders ABOVE the input
  bar so choosing a command happens right above where the cursor is, and the
  candidates never push down as new content the user has to scroll to.
* The input bar is bounded by a **light/near-white** top + bottom rule (``$input-rule``)
  so it reads clearly as the input — full width, no side box.
* The mode indicator (``#modepill``) and the shortcut / mode-switch line (``#hint``)
  are SECONDARY and live BELOW the bar. In idle (operator, empty input) only the
  ``#hint`` mode line shows; while typing it is reduced. The app drives visibility.

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
        # the slash palette FIRST — it opens ABOVE the input bar (Claude-style upward
        # palette). Hidden until a slash is typed; never pushes content below the bar.
        yield CommandPalette(id="palette")
        # the input BAR — full-width rule strip, marker `>` + input only.
        with Horizontal(id="composer-input-shell"):
            yield Static(f"[{theme.ACCENT_PRIMARY}]>[/{theme.ACCENT_PRIMARY}]", id="marker")
            yield PromptArea(id="prompt")
        # SECONDARY rows — BELOW the bar (never above it).
        yield Static(id="modepill")   # mode indicator (agent/palette); hidden in idle operator
        yield Static(id="hint")       # the shortcut / mode-switch line (idle only)


__all__ = ("Composer",)
