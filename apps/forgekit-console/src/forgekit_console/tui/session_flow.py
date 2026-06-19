"""Session flow — the single vertical scroll region that holds the reading flow.

This is the container that makes the console read like Claude Code: a ``1fr`` region
that fills the space between the intro banner and the DOCKED composer, holding only
the reading flow:

    issue line → active content (transcript XOR help view)

The composer is NOT inside it — it is docked below (see the app ``compose``), so the
input bar stays pinned at the viewport bottom and the conversation scrolls ABOVE it,
exactly like a terminal session. This is the **single scroll owner** of the app; the
transcript/help/palette never own their own vertical scroll.

The scrollbar GUTTER is hidden (``scrollbar-size-vertical: 0``) so the flow reads as a
continuous terminal session, NOT a boxed inner pane — but it is still genuinely
scrollable (``overflow-y: auto`` is inherited; ``allow_vertical_scroll`` stays True),
and :meth:`follow_tail` keeps the newest line in view. The intro header sits OUTSIDE
this region (fixed top banner).
"""

from __future__ import annotations

from textual.containers import VerticalScroll


class SessionFlow(VerticalScroll):
    """The single 1fr scroll region (issue + transcript/help). Composer is docked below."""

    DEFAULT_CSS = """
    SessionFlow {
        width: 1fr;
        height: 1fr;
        /* top-aligned: the reading flow stacks from the top of the region */
        align-vertical: top;
        /* NO scrollbar gutter — reads as a terminal session, not a boxed pane.
           Still the sole scroll owner (overflow auto; allow_vertical_scroll stays). */
        scrollbar-size-vertical: 0;
        padding: 0;
    }
    """

    def follow_tail(self) -> None:
        """Scroll so the tail of the reading flow (newest output) stays in view.

        Called after new transcript output — and after the palette opens (which shrinks
        this region from the bottom) — so the latest line stays visible just above the
        docked composer. animate=False keeps it snappy + deterministic for pilot tests.
        """

        self.scroll_end(animate=False)


__all__ = ("SessionFlow",)
