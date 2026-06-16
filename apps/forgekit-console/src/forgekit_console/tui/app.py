"""Forgekit operator console — Claude-Code-style chat-first layout.

Top→bottom: a small real-image avatar + brand/meta intro · one quiet issue line ·
the transcript (chat-first main area where command results AND ``/help`` stack) ·
a **fixed bottom composer** (mode pill + input + inline palette) that is ALWAYS
visible · a one-line hint. The user reads straight down and types at the bottom,
exactly like Claude Code. ``/help`` renders INTO the transcript (not a modal);
the composer never disappears.

Pure logic (parse/route/palette-state/intro/help strings + image-renderer
selection) lives in the core/helpers; this module owns the live widget state
(mode, palette, help tab) and the wiring between them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, Static

from .. import __version__
from ..commands import palette as palette_state
from ..commands.parser import parse_input
from ..commands.router import ConsoleContext, build_default_context, route
from ..models import (
    KIND_AGENT_MODE,
    KIND_CLEAR,
    KIND_HELP,
    KIND_LAYOUT,
    KIND_QUIT,
    MODE_OPERATOR,
)
from . import keymap, render
from .composer import Composer
from .header import IntroHeader
from .palette import CommandPalette
from .styles import SCREEN_CSS
from .transcript import Transcript

_STATUS_COMMANDS = {"status", "harness", "runtime", "doctor"}


class ForgekitConsoleApp(App):
    """The operator console — small avatar intro, transcript, fixed composer."""

    CSS = SCREEN_CSS

    BINDINGS = [
        Binding(key, action, desc) for key, action, desc in keymap.ACTION_BINDINGS
    ] + [
        Binding("tab", "next", "next", show=False, priority=True),
        Binding("shift+tab", "prev", "prev", show=False, priority=True),
        Binding("down", "palette_next", "next", show=False, priority=True),
        Binding("up", "palette_prev", "prev", show=False, priority=True),
        Binding("escape", "dismiss", "dismiss", show=False, priority=True),
    ]

    def __init__(self, *, repo_root: Path, context: Optional[ConsoleContext] = None) -> None:
        super().__init__()
        self.repo_root = Path(repo_root)
        self.context = context or build_default_context(self.repo_root)
        self.context.profile = self.context.profile or "operator"
        self.mode = MODE_OPERATOR
        self._palette = palette_state.CLOSED
        self._help_tab = 0
        self._suppress_refilter = False

    # --- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        # top: small real-image avatar + brand/version/provider/profile/repo
        yield IntroHeader(
            repo=str(self.repo_root),
            version=__version__,
            profile=self.context.profile,
            id="intro",
        )
        # one quiet issue line under the intro
        yield Static(id="issue")
        # chat-first main area — results AND help stack here
        yield Transcript(id="log")
        # one-line hint just above the fixed composer
        yield Static(id="hint")
        # fixed bottom composer (always visible — palette inline, mode pill, input)
        yield Composer(id="composer")

    def on_mount(self) -> None:
        self.title = render.BRAND
        self.sub_title = render.TAGLINE
        log = self.query_one("#log", Transcript)
        for line in render.welcome_banner(str(self.repo_root), self.context.profile):
            log.write(line)
        self._refresh_issue()
        self._refresh_chrome()
        self.query_one("#prompt", Input).focus()

    # --- input / palette ----------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._suppress_refilter:
            self._suppress_refilter = False
            return
        self._palette = palette_state.refilter(event.value, self.context.commands)
        self._render_palette()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value
        selected = palette_state.selected(self._palette)
        self._close_palette()
        self._reset_input()
        if selected is not None:
            raw = f"/{selected.name}"
        if raw.strip():
            self._execute(raw)

    def action_next(self) -> None:
        if self._palette.is_open:
            self._cycle(1)
        elif self._transcript.help_open:
            self._switch_help_tab(1)

    def action_prev(self) -> None:
        if self._palette.is_open:
            self._cycle(-1)
        elif self._transcript.help_open:
            self._switch_help_tab(-1)

    def action_palette_next(self) -> None:
        if self._palette.is_open:
            self._cycle(1)

    def action_palette_prev(self) -> None:
        if self._palette.is_open:
            self._cycle(-1)

    def action_dismiss(self) -> None:
        if self._transcript.help_open:
            self._close_help()
            return
        if self._palette.is_open:
            self._close_palette()
            return
        if self.mode != MODE_OPERATOR:
            self.mode = MODE_OPERATOR
            self._refresh_chrome()
            self.query_one("#log", Transcript).write("[dim]› operator 모드로 복귀[/dim]")

    def _cycle(self, direction: int) -> None:
        self._palette = palette_state.cycle(self._palette, direction)
        text = palette_state.completion_text(self._palette)
        if text is not None:
            self._suppress_refilter = True
            prompt = self.query_one("#prompt", Input)
            prompt.value = text
            prompt.cursor_position = len(text)
        self._render_palette()

    def _render_palette(self) -> None:
        widget = self.query_one("#palette", CommandPalette)
        if self._palette.is_open:
            widget.show(self._palette)
        else:
            widget.hide()
        self._refresh_chrome()

    def _close_palette(self) -> None:
        self._palette = palette_state.close()
        self.query_one("#palette", CommandPalette).hide()
        self._refresh_chrome()

    def _reset_input(self) -> None:
        self._suppress_refilter = True
        self.query_one("#prompt", Input).value = ""

    # --- command execution --------------------------------------------------

    def _execute(self, raw: str) -> None:
        parsed = parse_input(raw)
        result = route(parsed, self.context)
        if result.kind == KIND_QUIT:
            self.exit()
            return
        if result.kind == KIND_CLEAR:
            self.query_one("#log", Transcript).clear()
            self._transcript.close_help()
            return
        if result.kind in (KIND_HELP, KIND_LAYOUT):
            self._open_help()  # /layout also routes to help in this flow
            return
        log = self.query_one("#log", Transcript)
        log.write_echo(raw)
        log.write_result(result.title, result.lines)
        if result.kind == KIND_AGENT_MODE:
            self.mode = result.title  # router titles agent results as "agent:<id>"
            self._refresh_chrome()
        if parsed.name in _STATUS_COMMANDS:
            self._refresh_issue()

    # --- help (rendered INTO the transcript; composer stays visible) --------

    def action_open_help(self) -> None:
        if self._transcript.help_open:
            self._close_help()
        else:
            self._open_help()

    def _open_help(self) -> None:
        self._close_palette()
        self._help_tab = render.default_help_tab(
            render.help_sections(self.context.commands, self.context.agents)
        )
        self._transcript.open_help(self.context.commands, self.context.agents, self._help_tab)
        self._refresh_chrome()

    def _close_help(self) -> None:
        self._transcript.close_help()
        self._refresh_chrome()

    def _switch_help_tab(self, direction: int) -> None:
        n = len(render.help_sections(self.context.commands, self.context.agents))
        self._help_tab = (self._help_tab + direction) % n
        self._transcript.rerender_help(self.context.commands, self.context.agents, self._help_tab)

    @property
    def _transcript(self) -> Transcript:
        return self.query_one("#log", Transcript)

    # --- chrome / status ----------------------------------------------------

    def action_clear_log(self) -> None:
        self.query_one("#log", Transcript).clear()
        self._transcript.close_help()

    def action_refresh_status(self) -> None:
        self._refresh_issue()

    def _refresh_issue(self) -> None:
        summary = self.context.load_operator()
        self.query_one("#issue", Static).update(render.issue_line(summary))

    def _refresh_chrome(self) -> None:
        mode = "palette" if self._palette.is_open else self.mode
        self.query_one("#modepill", Static).update(render.mode_pill(mode, self.context.agents))
        self.query_one("#hint", Static).update(
            render.hint_line(
                palette_open=self._palette.is_open,
                help_open=self._transcript.help_open,
                in_agent=self.mode != MODE_OPERATOR,
            )
        )


__all__ = ("ForgekitConsoleApp",)
