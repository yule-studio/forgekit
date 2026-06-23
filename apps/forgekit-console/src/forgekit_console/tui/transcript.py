"""Transcript — the chat-first main area where command output stacks.

Like Claude Code's transcript: command echoes, results, and agent output stack
top→down. ``/help`` is NOT rendered here — it is a separate view
(:class:`tui.help_panel.HelpPanel`) that the :class:`tui.main_panel.MainPanel`
switches to, so opening or switching help never appends anything to this log. The
inline composer follows this content the whole time.

Layout note (scroll ownership): the log is ``height: auto`` (grows with its content)
and ``overflow-y: hidden`` so it NEVER owns its own vertical scroll. The single scroll
owner is the enclosing :class:`tui.session_flow.SessionFlow` (``1fr``) — the whole
session moves as one flow (Claude-Code feel), not a nested inner box. The composer
follows this content; ``SessionFlow.follow_tail`` keeps the newest line + composer in
view.

This is a thin :class:`RichLog` wrapper; it owns no help/view/scroll state anymore.
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
        overflow-y: hidden;
        scrollbar-size-vertical: 0;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("markup", True)
        kwargs.setdefault("wrap", True)
        kwargs.setdefault("highlight", False)
        # NOT auto_scroll: the Transcript no longer owns scroll — SessionFlow does.
        kwargs.setdefault("auto_scroll", False)
        super().__init__(**kwargs)

    def write_lines(self, lines: Sequence[str]) -> None:
        for line in lines:
            self.write(line)

    def begin_turn(self) -> None:
        """Insert a blank separator before a NEW turn (only when content exists).

        Gives each user→response turn vertical breathing room so the session reads as
        a stack of turns (Claude cadence), not a wall of tightly-packed lines.
        """

        if self.lines:
            self.write("")

    def write_echo(self, raw: str) -> None:
        """Echo the submitted input as a quiet transcript turn — bold you-marker head +
        dim indented continuation. Formatting lives in :func:`render.you_echo_lines`
        (pure, unit-tested); this just writes the lines."""

        self.write_lines(render.you_echo_lines(raw))

    def write_result(self, title: str, lines: Sequence[str]) -> None:
        self.write_lines(render.result_block(title, lines))


__all__ = ("Transcript",)
