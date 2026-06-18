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

from textual.containers import Vertical
from textual.widgets import Static

from . import render


class HelpPanel(Vertical):
    """In-app help view. Tab switches the active tab IN PLACE (re-render, never append).

    Two stacked parts: a ``#help-tabs`` strip (read first — ``Help General …``, no
    'forgekit help' branding) with a **full-width thin cyan divider** under it
    (``border-bottom: solid $accent``), then the ``#help-body`` for the active tab.
    """

    DEFAULT_CSS = """
    HelpPanel {
        width: 1fr;
        height: auto;
        overflow-y: hidden;
        scrollbar-size-vertical: 0;
        padding: 1 2;
    }
    HelpPanel #help-tabs {
        height: auto;
        /* the full-width thin cyan divider sits right under the tab row */
        border-bottom: solid $accent;
        padding: 0 0 1 0;
    }
    HelpPanel #help-body {
        height: auto;
        padding: 1 0 0 0;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._commands = ()
        self._agents = ()
        self._active = 0

    def compose(self):
        yield Static(id="help-tabs", markup=True)
        yield Static(id="help-body", markup=True)

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

    def focus_tab(self, title: str) -> None:
        """Jump the active tab to the section whose title matches *title* (if any)."""

        sections = render.help_sections(self._commands, self._agents)
        for i, section in enumerate(sections):
            if section.title == title:
                self._active = i
                self._render_active()
                return

    def switch_tab(self, direction: int) -> None:
        """Move the active tab by *direction* and re-render the SAME widget in place."""

        sections = render.help_sections(self._commands, self._agents)
        if not sections:
            return
        self._active = (self._active + direction) % len(sections)
        self._render_active()

    def _render_active(self) -> None:
        sections = render.help_sections(self._commands, self._agents)
        # update() REPLACES the content of each part — tab-switch is in-place, never
        # an accumulating append. The tab strip + the active body are separate so the
        # cyan divider (CSS border) sits cleanly between them.
        self.query_one("#help-tabs", Static).update(render.help_tab_strip(sections, self._active))
        self.query_one("#help-body", Static).update(
            "\n".join(render.help_body(sections, self._active))
        )


__all__ = ("HelpPanel",)
