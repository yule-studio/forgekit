"""Help panel — a dedicated in-app help VIEW (not a transcript block, not a modal).

This is the key Claude-Code fix. The old console *appended* the help document
into the scrolling transcript, so opening help (and switching tabs) accumulated
duplicate blocks. Instead, help is now a separate widget that the main area
switches TO: when ``/help`` (or F1) fires, :class:`tui.main_panel.MainPanel`
hides the transcript and shows this panel; Esc switches back. The user feels
"I'm now on the help screen", and nothing about help is ever left behind in the
transcript.

The panel owns one piece of state — the active tab index — and re-renders the
**same** widget in place when the tab changes (Tab key). It never appends. The
body content is the pure :func:`tui.render.help_panel_document` document for the
active tab; a top tab strip (Help · General · Commands · Agents) marks which tab
is shown. No accordion, no OS modal — it's an in-app panel inside the main area.
"""

from __future__ import annotations

from textual.widgets import Static

from . import render


class HelpPanel(Static):
    """In-app help view. Tab switches the active tab IN PLACE (re-render, never append)."""

    DEFAULT_CSS = """
    HelpPanel {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
        overflow-y: auto;
    }
    """

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("markup", True)
        super().__init__(**kwargs)
        self._commands = ()
        self._agents = ()
        self._active = 0

    @property
    def active_tab(self) -> int:
        return self._active

    def set_registry(self, commands, agents) -> None:
        """Bind the command/agent registries used to build the tab documents."""

        self._commands = commands
        self._agents = agents

    def open_default(self, commands, agents) -> None:
        """Open the help view on the default tab (General) and render it."""

        self.set_registry(commands, agents)
        sections = render.help_sections(commands, agents)
        self._active = render.default_help_tab(sections)
        self._render_active()

    def switch_tab(self, direction: int) -> None:
        """Move the active tab by *direction* and re-render the SAME widget in place."""

        sections = render.help_sections(self._commands, self._agents)
        if not sections:
            return
        self._active = (self._active + direction) % len(sections)
        self._render_active()

    def _render_active(self) -> None:
        sections = render.help_sections(self._commands, self._agents)
        document = render.help_panel_document(sections, self._active)
        # update() REPLACES the content — this is what makes tab-switch in-place
        # instead of an accumulating append.
        self.update("\n".join(document))


__all__ = ("HelpPanel",)
