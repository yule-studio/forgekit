"""Inline help surface — a full-width document that expands in the reading flow.

Replaces the old centered modal. ``/help`` (or F1) opens this slim panel under
the input; it shows the help sections stacked as a scrollable document (no popup,
no focus steal), and Esc collapses it back to the flow. Content comes from the
pure :func:`render.help_inline`.
"""

from __future__ import annotations

from typing import Sequence

from textual.containers import VerticalScroll
from textual.widgets import Static

from ..models import AgentInfo
from . import render


class InlineHelp(VerticalScroll):
    """A scrollable, full-width inline help document (hidden until opened)."""

    DEFAULT_CSS = """
    InlineHelp {
        display: none;
        height: auto;
        max-height: 16;
        padding: 0 2;
        background: $surface;
        border-left: thick $accent;
    }
    InlineHelp.-open { display: block; }
    """

    def open(self, commands: Sequence, agents: Sequence[AgentInfo]) -> None:
        lines = render.help_inline(render.help_sections(commands, agents))
        # rebuild the single Static child with the document content
        self.remove_children()
        self.mount(Static("\n".join(lines)))
        self.add_class("-open")

    def close(self) -> None:
        self.remove_class("-open")
        self.remove_children()

    @property
    def is_open(self) -> bool:
        return self.has_class("-open")


__all__ = ("InlineHelp",)
