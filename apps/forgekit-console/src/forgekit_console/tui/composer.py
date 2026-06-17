"""Composer — the SESSION-FOLLOWING input BAR + a SEPARATE slash palette below it.

Claude-Code-style input. Three things are kept visually + structurally distinct:

    transcript ……………………………………………  (the main panel, above)
    ╭───────────────────────────────────────╮
    │ › ● operator                            │   ← #composer-shell (the BAR)
    │   /help · / palette · Tab · ^C quit     │      #inputrow (CLEAN input) + #hint
    ╰───────────────────────────────────────╯
      ▎ palette  3  · Tab 완성 · Esc 닫기      ← #palette (SEPARATE surface, below)
      ▎  ▸ /help   …                              compact, auto-height, NOT a box
      ▎    /harness …

* The **bar** (``#composer-shell``) is a self-contained, bordered input area — the
  input row + a quiet sub-hint row. It reads as ONE independent bar separated from
  the transcript by a top margin (more than a thin rule, lighter than a heavy box).
* The **slash palette** is NOT inside the input box: it is a SEPARATE surface
  rendered *below* the shell (a sibling), connected by a left accent rule but its
  own compact, auto-height strip. ``/`` lives as typed text in the input; the
  command LIST never renders inside the text box and never bleeds into the
  transcript. ``/help`` is a separate full-VIEW switch (:class:`tui.main_panel`).

The composer follows the session flow (NO ``dock``; ``height: auto``) so a short
session leaves it near the top and a growing transcript pushes it down. It owns no
command logic — the app drives it (focus, value, palette show/hide, mode pill,
hint). Pure render strings come from :mod:`tui.render`.
"""

from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from . import theme
from .palette import CommandPalette


class Composer(Vertical):
    """The input BAR (bordered shell) + a separate slash palette surface below it."""

    DEFAULT_CSS = """
    Composer {
        /* NO dock + no border on the outer wrapper — it just stacks the bar and the
           separate palette surface, flowing inline after the active content. */
        height: auto;
        margin: 1 1 0 1;   /* a clear gap from the transcript above */
        background: $background;
        padding: 0;
    }
    /* the BAR: a self-contained, bordered input area (input row + sub-hint). */
    Composer #composer-shell {
        height: auto;
        background: $background;
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
    /* the sub-hint row — quiet shortcuts, part of the bar (always visible). */
    Composer #hint {
        height: 1;
        padding: 0;
        color: $text-muted;
    }
    """

    def compose(self):
        # the BAR — a bordered shell holding the input row + the sub-hint row.
        with Vertical(id="composer-shell"):
            with Horizontal(id="inputrow"):
                yield Static(f"[{theme.ACCENT_PRIMARY}]›[/{theme.ACCENT_PRIMARY}]", id="marker")
                yield Static(id="modepill")
                # NO in-field guidance text — the input stays clean (Claude-style).
                # All hints (`/help`, `/ palette`, Tab, quit) live in the #hint row
                # below, set by the app from tui.render.hint_line.
                yield Input(placeholder="", id="prompt")
            yield Static(id="hint")
        # the slash palette — a SEPARATE compact surface BELOW the bar (not inside
        # the input box, not in the transcript). Hidden until a slash is typed.
        yield CommandPalette(id="palette")


__all__ = ("Composer",)
