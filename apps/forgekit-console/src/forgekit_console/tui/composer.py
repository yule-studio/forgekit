"""Composer — the SESSION-FOLLOWING inline chat BAR (input row + hint + palette).

This is the Claude-Code-style input bar. It is **part of the top-aligned session
flow**, not a footer docked to the viewport bottom: it renders IMMEDIATELY AFTER
the active content (transcript OR help view) and follows it (a short session leaves
it near the top; as the transcript grows it is pushed down, the enclosing
:class:`tui.session_flow.SessionFlow` scroll keeping it in view). It carries NO
``dock`` and is ``height: auto`` — that is what makes it flow inline.

**Bar layout (top→bottom):**

    ┌──────────────────────────────────────────────┐
    │ › [mode]  명령 입력 …                          │   #inputrow
    │   /help     (slash 입력 시 입력행 바로 아래)    │   #palette  (opens here)
    │ /help · Tab 완성 · Esc · ^C quit               │   #subhint
    └──────────────────────────────────────────────┘

The bar reads as ONE contained area — a neat rounded rule (not a heavy/full box,
not a single thin separator) with a small top margin separating it from the
transcript, and the input row as the star. The slash palette opens **directly
below the input row, inside the bar** (composer expansion), so it reads as part of
the input flow — never as a floating dialog or a transcript entry. ``/help`` is a
separate full-VIEW switch (:class:`tui.main_panel.MainPanel`), distinct from this
inline palette.

The widget owns no command logic; the app drives it (focus, value, palette
show/hide, mode pill, hint). Pure render strings come from :mod:`tui.render`.
"""

from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from . import theme
from .palette import CommandPalette


class Composer(Vertical):
    """Inline session-following input BAR: input row · inline palette · hint row."""

    DEFAULT_CSS = """
    Composer {
        /* NO dock — the bar flows inline right after the active content. */
        height: auto;
        /* a small gap above separates the bar from the transcript (not flush). */
        margin: 1 1 0 1;
        background: $background;
        /* a neat rounded rule makes the whole thing read as ONE input BAR — more
           than a thin separator, lighter than a heavy box (Claude restraint). */
        border: round $brand-border;
        padding: 0 1;
    }
    Composer #inputrow {
        height: 1;
        padding: 0;
    }
    Composer #marker {
        width: auto;
        padding: 0 1 0 0;
        color: $accent;
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
    /* the always-visible sub-hint row at the foot of the bar (quiet, muted). */
    Composer #hint {
        height: auto;
        padding: 0;
        color: $text-muted;
    }
    """

    def compose(self):
        # top: the input row (prompt marker + mode pill + input) — the star.
        with Horizontal(id="inputrow"):
            yield Static(f"[{theme.ACCENT_PRIMARY}]›[/{theme.ACCENT_PRIMARY}]", id="marker")
            yield Static(id="modepill")
            yield Input(placeholder="명령 입력 — `/help` 로 시작", id="prompt")
        # middle: the slash palette opens DIRECTLY BELOW the input row, inside the
        # bar (composer expansion) — not above it, not in the transcript.
        yield CommandPalette(id="palette")
        # bottom: the always-visible sub-hint row (kept in the bar, not a stray line).
        yield Static(id="hint")


__all__ = ("Composer",)
