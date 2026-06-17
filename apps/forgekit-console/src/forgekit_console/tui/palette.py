"""Command palette — a compact slash-command surface BELOW the composer bar.

A SEPARATE surface, not a popup box and NOT part of the input text box: it is a
sibling rendered just under the :class:`tui.composer.Composer` bar, connected by a
thin left accent rule, that filters as you type a slash. It is ``height: auto`` so
a few matches stay small, with a ``max-height`` cap + scroll so a long list never
swells into a giant boxed area. It owns no logic — the app drives it via
:meth:`show` / :meth:`hide` from a pure :class:`PaletteState`.
"""

from __future__ import annotations

from textual.widgets import Static

from ..commands.palette import PaletteState
from . import render


class CommandPalette(Static):
    """Compact slash-command list, a SEPARATE surface below the composer bar."""

    DEFAULT_CSS = """
    CommandPalette {
        display: none;
        /* auto-height so few matches stay small; capped + scroll so a long list
           never becomes a giant box. A slight left inset + accent rule connects it
           under the bar without being inside the input box. */
        height: auto;
        max-height: 8;
        overflow-y: auto;
        scrollbar-size-vertical: 1;
        margin: 0 0 0 1;
        padding: 0 1;
        color: $text;
        background: $background;
        border-left: solid $accent;  /* thin brand-cyan rule — connects to the bar */
    }
    CommandPalette.-open { display: block; }
    """

    def show(self, state: PaletteState) -> None:
        count = len(state.matches)
        # one compact header line, then the candidate rows (compact spacing).
        header = f"[dim]▎palette[/dim] [b]{count}[/b] [dim]· Tab · ↑/↓ · Esc[/dim]"
        body = render.palette_panel_lines(state.matches, state.index)
        self.update("\n".join((header, *body)))
        self.add_class("-open")

    def hide(self) -> None:
        self.remove_class("-open")
        self.update("")


__all__ = ("CommandPalette",)
