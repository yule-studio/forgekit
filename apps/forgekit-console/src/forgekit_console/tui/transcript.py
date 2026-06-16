"""Transcript — the chat-first main area where command output stacks.

Like Claude Code's transcript: command echoes, results, and agent output stack
top→down in one scrolling area. ``/help`` is NOT rendered here — it is a separate
view (:class:`tui.help_panel.HelpPanel`) that the :class:`tui.main_panel.MainPanel`
switches to, so opening or switching help never appends anything to this log.
The composer stays docked at the bottom the whole time.

This is a thin :class:`RichLog` wrapper; it owns no help/view state anymore.
"""

from __future__ import annotations

from typing import Sequence

from textual.widgets import RichLog

from . import render


class Transcript(RichLog):
    """Scrolling chat-first log. Help is a separate view, not appended here."""

    DEFAULT_CSS = """
    Transcript {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("markup", True)
        kwargs.setdefault("wrap", True)
        kwargs.setdefault("highlight", False)
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
