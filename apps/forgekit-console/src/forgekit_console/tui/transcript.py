"""Transcript — the chat-first main area where everything stacks.

Like Claude Code's transcript: command echoes, results, agent output, and the
``/help`` document all stack top→down in one scrolling area. ``/help`` is NOT a
modal or side panel — it's appended into this same transcript and Esc removes it.
The composer stays docked at the bottom the whole time.

The widget is a thin :class:`RichLog` wrapper plus a marker so the app can remove
an open help block on Esc without clearing the rest of the transcript.
"""

from __future__ import annotations

from typing import Sequence

from textual.widgets import RichLog

from . import render


class Transcript(RichLog):
    """Scrolling chat-first log. Help renders inline here, not in a modal."""

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
        self._help_open = False

    @property
    def help_open(self) -> bool:
        return self._help_open

    def write_lines(self, lines: Sequence[str]) -> None:
        for line in lines:
            self.write(line)

    def write_echo(self, raw: str) -> None:
        """Echo the submitted input as a quiet transcript turn."""

        self.write(f"[dim]›[/dim] {raw}")

    def write_result(self, title: str, lines: Sequence[str]) -> None:
        self.write_lines(render.result_block(title, lines))

    def open_help(self, commands, agents, active: int) -> None:
        """Append the full-width help document into the transcript."""

        sections = render.help_sections(commands, agents)
        self.write_lines(render.help_in_transcript(sections, active))
        self._help_open = True

    def close_help(self) -> None:
        """Mark help closed and note it in the transcript (no destructive clear)."""

        if self._help_open:
            self.write("[dim]— help 닫힘 —[/dim]")
            self._help_open = False

    def rerender_help(self, commands, agents, active: int) -> None:
        """Switch the active help tab by appending the newly-active tab."""

        sections = render.help_sections(commands, agents)
        self.write_lines(render.help_in_transcript(sections, active))


__all__ = ("Transcript",)
