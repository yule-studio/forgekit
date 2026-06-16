"""Transcript — the chat-first main area where command output stacks.

Like Claude Code's transcript: command echoes, results, and agent output stack
top→down. ``/help`` is NOT rendered here — it is a separate view
(:class:`tui.help_panel.HelpPanel`) that the :class:`tui.main_panel.MainPanel`
switches to, so opening or switching help never appends anything to this log. The
inline composer follows this content the whole time.

Layout note: the log is ``height: auto`` (it grows with its content) so a short
session leaves the composer near the top with empty space below — the
session-following inline feel. It is capped by a ``max-height`` so a very long
session scrolls instead of unbounded growth; the enclosing
:class:`tui.session_flow.SessionFlow` scroll keeps the newest line + the composer
in view.

This is a thin :class:`RichLog` wrapper; it owns no help/view state anymore.
"""

from __future__ import annotations

from typing import Sequence

from textual.widgets import RichLog

from . import render


class Transcript(RichLog):
    """Auto-height chat-first log. Help is a separate view, not appended here."""

    DEFAULT_CSS = """
    Transcript {
        width: 1fr;
        height: auto;
        max-height: 80vh;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("markup", True)
        kwargs.setdefault("wrap", True)
        kwargs.setdefault("highlight", False)
        kwargs.setdefault("auto_scroll", True)
        super().__init__(**kwargs)

    def write_lines(self, lines: Sequence[str]) -> None:
        for line in lines:
            self.write(line)

    def write_echo(self, raw: str) -> None:
        """Echo the submitted input as a quiet transcript turn."""

        self.write(f"[dim]›[/dim] {raw}")

    def write_result(self, title: str, lines: Sequence[str]) -> None:
        self.write_lines(render.result_block(title, lines))


__all__ = ("Transcript",)
