"""Help document widget — full-width inline help shown in the main content area.

Not a modal, not a side panel, not an accordion: when open it occupies the main
content region (the log hides), showing one active tab in full so "what's open"
is obvious. The app owns the active-tab index and calls :meth:`show`; the body
comes from the pure :func:`render.help_document`.
"""

from __future__ import annotations

from typing import Sequence

from textual.widgets import Static

from ..models import AgentInfo
from . import render


class HelpDocument(Static):
    """Full-width inline help (hidden until ``/help`` / F1)."""

    DEFAULT_CSS = """
    HelpDocument {
        display: none;
        width: 1fr;
        height: 1fr;
        padding: 1 1 0 1;
    }
    HelpDocument.-open { display: block; }
    """

    def show(self, commands: Sequence, agents: Sequence[AgentInfo], active: int) -> None:
        sections = render.help_sections(commands, agents)
        self.update("\n".join(render.help_document(sections, active)))
        self.add_class("-open")

    def hide(self) -> None:
        self.remove_class("-open")
        self.update("")

    @property
    def is_open(self) -> bool:
        return self.has_class("-open")


__all__ = ("HelpDocument",)
