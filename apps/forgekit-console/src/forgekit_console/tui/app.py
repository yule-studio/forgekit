"""Forgekit operator console — Claude-like vertical flow.

Top→bottom: compact intro (small avatar + brand/version/profile/repo) · one-line
setup/status issue line · thin input row · inline palette · main content (the log,
or the full-width inline help document when open) · one-line hint. The user reads
straight down; ``/help`` opens a full-width help doc in the content area (not a
modal), Esc returns to the log.

Pure logic (parse/route/palette-state/intro/help strings) lives in the core; this
module owns the live widget state (mode, palette, help tab) and wiring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Input, RichLog, Static

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
from . import intro as intro_mod
from . import keymap, render
from .help_document import HelpDocument
from .palette import CommandPalette
from .styles import SCREEN_CSS

_STATUS_COMMANDS = {"status", "harness", "runtime", "doctor"}


class ForgekitConsoleApp(App):
    """The operator console — compact intro, then a single reading flow."""

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
        yield Static(id="intro")
        yield Static(id="issue")
        with Horizontal(id="inputrow"):
            yield Static(id="modepill")
            yield Input(placeholder="명령 입력 — `/help` 로 시작", id="prompt")
        yield CommandPalette(id="palette")
        with Container(id="content"):
            yield RichLog(id="log", markup=True, wrap=True, highlight=False)
            yield HelpDocument(id="helpdoc")
        yield Static(id="hint")

    def on_mount(self) -> None:
        self.title = render.BRAND
        self.sub_title = render.TAGLINE
        self.query_one("#intro", Static).update("\n".join(intro_mod.intro_lines(
            repo=str(self.repo_root), version=__version__, profile=self.context.profile,
        )))
        log = self.query_one("#log", RichLog)
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
        elif self._helpdoc.is_open:
            self._switch_help_tab(1)

    def action_prev(self) -> None:
        if self._palette.is_open:
            self._cycle(-1)
        elif self._helpdoc.is_open:
            self._switch_help_tab(-1)

    def action_palette_next(self) -> None:
        if self._palette.is_open:
            self._cycle(1)

    def action_palette_prev(self) -> None:
        if self._palette.is_open:
            self._cycle(-1)

    def action_dismiss(self) -> None:
        if self._helpdoc.is_open:
            self._close_help()
            return
        if self._palette.is_open:
            self._close_palette()
            return
        if self.mode != MODE_OPERATOR:
            self.mode = MODE_OPERATOR
            self._refresh_chrome()
            self.query_one("#log", RichLog).write("[dim]› operator 모드로 복귀[/dim]")

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
            self.query_one("#log", RichLog).clear()
            return
        if result.kind in (KIND_HELP, KIND_LAYOUT):
            self._open_help()  # /layout also routes to help in this flow
            return
        log = self.query_one("#log", RichLog)
        log.write(f"[dim]›[/dim] {raw}")
        for line in render.result_block(result.title, result.lines):
            log.write(line)
        if result.kind == KIND_AGENT_MODE:
            self.mode = result.title  # router titles agent results as "agent:<id>"
            self._refresh_chrome()
        if parsed.name in _STATUS_COMMANDS:
            self._refresh_issue()

    # --- help (full-width inline, swaps with the log) -----------------------

    def action_open_help(self) -> None:
        if self._helpdoc.is_open:
            self._close_help()
        else:
            self._open_help()

    def _open_help(self) -> None:
        self._close_palette()
        self._help_tab = render.default_help_tab(
            render.help_sections(self.context.commands, self.context.agents)
        )
        self._helpdoc.show(self.context.commands, self.context.agents, self._help_tab)
        self.query_one("#log", RichLog).display = False
        self._refresh_chrome()

    def _close_help(self) -> None:
        self._helpdoc.hide()
        self.query_one("#log", RichLog).display = True
        self._refresh_chrome()

    def _switch_help_tab(self, direction: int) -> None:
        n = len(render.help_sections(self.context.commands, self.context.agents))
        self._help_tab = (self._help_tab + direction) % n
        self._helpdoc.show(self.context.commands, self.context.agents, self._help_tab)

    @property
    def _helpdoc(self) -> HelpDocument:
        return self.query_one("#helpdoc", HelpDocument)

    # --- chrome / status ----------------------------------------------------

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

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
                help_open=self._helpdoc.is_open,
                in_agent=self.mode != MODE_OPERATOR,
            )
        )


__all__ = ("ForgekitConsoleApp",)
