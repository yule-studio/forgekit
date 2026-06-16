"""Session flow — the top-aligned vertical scroll that holds the live session.

This is the container that makes the console read like Claude Code: a single
TOP-ALIGNED vertical flow of

    issue line → active content (transcript XOR help view) → inline composer → hint

where the active content is ``height: auto`` and the composer renders IMMEDIATELY
AFTER it. When the session is short the composer sits near the top with EMPTY
space below (exactly the Claude screenshot); as the transcript grows, the content
pushes the composer down and this :class:`~textual.containers.VerticalScroll`
provides the overflow, auto-scrolling so the newest output + the composer stay in
view.

The intro header is intentionally kept OUTSIDE this scroll (in the app's
``compose``) as a fixed top banner; only the live session (issue/content/composer/
hint) scrolls. :meth:`follow_tail` is called by the app after new input/output so
the composer is kept visible.
"""

from __future__ import annotations

from textual.containers import VerticalScroll


class SessionFlow(VerticalScroll):
    """Top-aligned scroll holding issue → content → composer → hint inline."""

    DEFAULT_CSS = """
    SessionFlow {
        width: 1fr;
        height: 1fr;
        /* top-aligned: children stack from the top, NOT pinned to the bottom */
        align-vertical: top;
        scrollbar-size-vertical: 1;
    }
    """

    def follow_tail(self) -> None:
        """Scroll so the tail of the flow (newest content + composer) stays in view.

        Called after a new transcript entry / command output so the inline
        composer is kept visible as the session grows — the "typing at the end of
        the current session" feel.
        """

        # animate=False keeps it snappy + deterministic for pilot tests.
        self.scroll_end(animate=False)


__all__ = ("SessionFlow",)
