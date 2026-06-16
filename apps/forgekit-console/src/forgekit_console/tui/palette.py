"""Command palette widget — the bottom overlay shown while typing a slash.

A thin Static-backed surface that renders a pure :class:`PaletteState`. It owns
no logic: the app drives it via :meth:`show` / :meth:`hide`, and the rendering
comes from :func:`render.palette_panel_lines`. Hidden by default; the app toggles
``display`` so it only appears when a slash command is being typed.
"""

from __future__ import annotations

from textual.widgets import Static

from ..commands.palette import PaletteState
from . import render


class CommandPalette(Static):
    """Bottom command-palette overlay (separate surface above the input)."""

    DEFAULT_CSS = """
    CommandPalette {
        display: none;
        height: auto;
        max-height: 10;
        background: $panel;
        border: round $accent;
        padding: 0 1;
        margin: 0 1;
    }
    CommandPalette.-open { display: block; }
    """

    def show(self, state: PaletteState) -> None:
        count = len(state.matches)
        header = f"[b cyan]command palette[/b cyan] [dim]({count})[/dim]  [dim]Tab 완성 · ↑/↓ 순환 · Esc 닫기[/dim]"
        body = render.palette_panel_lines(state.matches, state.index)
        self.update("\n".join((header, "", *body)))
        self.add_class("-open")

    def hide(self) -> None:
        self.remove_class("-open")
        self.update("")


__all__ = ("CommandPalette",)
