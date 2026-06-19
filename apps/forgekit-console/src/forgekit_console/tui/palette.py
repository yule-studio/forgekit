"""Command palette — a compact slash-command surface DIRECTLY BELOW the input bar.

A SEPARATE surface, not a popup box and NOT part of the input text box, and NOT part
of the transcript: it is the child of :class:`tui.composer.Composer` placed right
AFTER the input bar, so it renders flush UNDER the input box (gap ≈ 0 rows) inside the
bottom-docked composer zone. It filters as you type a slash. It is ``height: auto`` and
**caps the rows it renders** (``MAX_ROWS``) so a long match list never grows an inner
scrollbar — the enclosing :class:`tui.session_flow.SessionFlow` stays the single scroll
owner. It owns no logic — the app drives it via :meth:`show` / :meth:`hide` from a pure
:class:`PaletteState`.
"""

from __future__ import annotations

from textual.widgets import Static

from ..commands.palette import PaletteState
from . import render

# Cap the rows so the palette never needs its own scroll (single-scroll-owner rule).
# A few more matches than this just show a "+N more — keep typing" hint instead.
MAX_ROWS = 8


class CommandPalette(Static):
    """Compact slash-command list, a SEPARATE surface ABOVE the composer bar."""

    DEFAULT_CSS = """
    CommandPalette {
        display: none;
        /* a FLAT list (Claude): no left rule / side bar — rows separated by
           whitespace + alignment only. auto-height; rows are CAPPED in show() so
           it never needs an inner scrollbar (SessionFlow owns scroll). padding-top 0
           so the list sits FLUSH directly under the input bar (gap ≈ 0 rows). */
        height: auto;
        overflow-y: hidden;
        scrollbar-size-vertical: 0;
        padding: 0 1 0 1;
        color: $text;
        background: $background;
    }
    CommandPalette.-open { display: block; }
    """

    def show(self, state: PaletteState) -> None:
        count = len(state.matches)
        # Keep the SELECTED row in the visible window even when matches exceed the cap,
        # so cycling never scrolls a row out of sight (no inner scroll, window slides).
        matches, base, hidden = _window(state.matches, state.index, MAX_ROWS)
        rows = render.palette_panel_lines(matches, state.index - base)
        head = f"[dim]{count} commands · Tab/↑↓ 선택 · Enter 실행 · Esc 닫기[/dim]"
        more = (f"[dim]  … +{hidden} more — 계속 입력해 좁히기[/dim]",) if hidden else ()
        # header on top (furthest from the bar), candidates below it nearest the input.
        self.update("\n".join((head, *rows, *more)))
        self.add_class("-open")

    def hide(self) -> None:
        self.remove_class("-open")
        self.update("")


def _window(matches, index: int, cap: int):
    """Return (visible_matches, base_offset, hidden_count) — a cap-sized window that
    always contains ``index``. Pure; keeps the selected row visible without scroll."""

    total = len(matches)
    if total <= cap:
        return matches, 0, 0
    base = 0 if index < 0 else min(max(0, index - cap + 1), total - cap)
    return matches[base : base + cap], base, total - cap


__all__ = ("CommandPalette", "MAX_ROWS")
