"""Main panel — the mutually-exclusive content area (transcript XOR help view).

The console's main area is a small state machine: it shows EITHER the scrolling
:class:`tui.transcript.Transcript` OR the :class:`tui.help_panel.HelpPanel`,
never both, and never overlaid. A textual :class:`ContentSwitcher` holds the two
children and shows exactly one by id.

This replaces the old "append help into the transcript" behaviour:

* ``show_help`` → switch the visible child to the help panel (transcript hidden).
  The user is now looking at the help screen.
* ``show_transcript`` → switch back to the transcript, exactly as it was. Nothing
  about help is left behind in the transcript — help lives in its own widget.

It is an in-app panel, not a modal: no new screen is pushed, so the composer
stays in the inline session flow right below it and the screen stack length
stays 1.

Layout note: the panel is ``height: auto`` (it sizes to its active child's
content), NOT ``1fr``. That is what lets the composer render IMMEDIATELY AFTER the
content instead of being shoved to the viewport bottom by a flex-filled main
area. The enclosing :class:`tui.session_flow.SessionFlow` scroll provides the
overflow handling as the transcript grows.
"""

from __future__ import annotations

from textual.widgets import ContentSwitcher

from .help_panel import HelpPanel
from .transcript import Transcript

_TRANSCRIPT_ID = "transcript"
_HELP_ID = "help"


class MainPanel(ContentSwitcher):
    """Switches the main area between the transcript and the help view (XOR).

    ``height: auto`` — sizes to the active child so the composer follows the
    content inline (see module docstring).
    """

    DEFAULT_CSS = """
    MainPanel {
        width: 1fr;
        height: auto;
        /* never own a scroll/gutter — SessionFlow is the sole scroll owner. height:auto
           means this should never overflow, but zero the gutter so it can NEVER draw an
           internal-pane scrollbar even transiently (structural, not a CSS cover-up). */
        scrollbar-size-vertical: 0;
        overflow-y: hidden;
    }
    """

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("initial", _TRANSCRIPT_ID)
        super().__init__(**kwargs)

    def compose(self):
        yield Transcript(id=_TRANSCRIPT_ID)
        yield HelpPanel(id=_HELP_ID)

    # --- views --------------------------------------------------------------

    @property
    def transcript(self) -> Transcript:
        return self.get_child_by_id(_TRANSCRIPT_ID, Transcript)

    @property
    def help_panel(self) -> HelpPanel:
        return self.get_child_by_id(_HELP_ID, HelpPanel)

    @property
    def help_open(self) -> bool:
        return self.current == _HELP_ID

    def show_help(self, commands, agents, *, focus_title: str = None) -> None:
        """Switch the whole main area to the help view (transcript hidden).

        ``focus_title`` (e.g. "About") opens directly on that tab; otherwise the
        default (General) tab.
        """

        self.help_panel.open_default(commands, agents)
        if focus_title:
            self.help_panel.focus_tab(focus_title)
        self.current = _HELP_ID

    def switch_help_tab(self, direction: int) -> None:
        """Switch the active help tab IN PLACE (only meaningful while help is open)."""

        if self.help_open:
            self.help_panel.switch_tab(direction)

    def show_transcript(self) -> None:
        """Switch back to the transcript, exactly as it was (nothing appended)."""

        self.current = _TRANSCRIPT_ID


__all__ = ("MainPanel",)
