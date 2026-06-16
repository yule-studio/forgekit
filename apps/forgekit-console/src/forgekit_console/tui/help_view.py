"""Help overlay — a tabbed modal screen (Help / General / Commands / Agents).

The content is built by the pure :func:`render.help_sections`; this module only
arranges it into a textual ``ModalScreen`` with one tab per section. Esc (or the
footer) dismisses it back to the console.
"""

from __future__ import annotations

from typing import Sequence

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, TabbedContent, TabPane

from ..models import AgentInfo
from . import render


class HelpScreen(ModalScreen):
    """Full help surface as a centered modal with section tabs."""

    BINDINGS = [("escape", "dismiss", "Close"), ("f1", "dismiss", "Close")]

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > #help-box {
        width: 84;
        max-width: 96%;
        height: 30;
        max-height: 90%;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    HelpScreen #help-title { text-style: bold; color: $accent; padding-bottom: 1; }
    HelpScreen TabPane { padding: 1 0; }
    """

    def __init__(self, commands: Sequence, agents: Sequence[AgentInfo]) -> None:
        super().__init__()
        self._sections = render.help_sections(commands, agents)

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(f"{render.BRAND} · help", id="help-title")
            with TabbedContent():
                for section in self._sections:
                    with TabPane(section.title):
                        yield Static("\n".join(section.lines))

    def action_dismiss(self) -> None:
        self.dismiss()


__all__ = ("HelpScreen",)
