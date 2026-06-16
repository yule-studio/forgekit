"""Command palette — a thin inline surface shown while typing a slash.

Not a popup box: it sits directly under the input as a slim, borderless strip
that filters as you type, so it reads as part of the input flow rather than a
floating dialog. It owns no logic — the app drives it via :meth:`show` /
:meth:`hide` from a pure :class:`PaletteState`.
"""

from __future__ import annotations

from textual.widgets import Static

from ..commands.palette import PaletteState
from . import render


class CommandPalette(Static):
    """Slim inline command palette (separate surface, but in the reading flow)."""

    DEFAULT_CSS = """
    CommandPalette {
        display: none;
        height: auto;
        max-height: 9;
        padding: 0 2;
        color: $text;
        background: $surface;
        border-left: thick $accent;
    }
    CommandPalette.-open { display: block; }
    """

    def show(self, state: PaletteState) -> None:
        count = len(state.matches)
        header = (
            f"[dim]palette[/dim] [b]{count}[/b] "
            f"[dim]· Tab 완성 · ↑/↓ 순환 · Esc 닫기[/dim]"
        )
        body = render.palette_panel_lines(state.matches, state.index)
        self.update("\n".join((header, *body)))
        self.add_class("-open")

    def hide(self) -> None:
        self.remove_class("-open")
        self.update("")


__all__ = ("CommandPalette",)
