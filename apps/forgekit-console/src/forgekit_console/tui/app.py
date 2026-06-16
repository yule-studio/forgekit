"""Forgekit operator console — the Textual application (cockpit frame).

Layout (top→bottom): topbar · body(agents | center log | status) · command
palette overlay · prompt row(mode badge + input) · footer.

The app stays thin: parsing/routing/status-shaping/palette-state live in the pure
core (``commands`` / ``data``), avatar + render strings in ``tui.render`` /
``tui.avatar``, and the palette/help surfaces in their own widgets. This module
only wires those into textual and owns the live widget state (mode, palette).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, RichLog, Static

from ..commands import palette as palette_state
from ..commands.parser import parse_input
from ..commands.registry import load_agents
from ..commands.router import ConsoleContext, build_default_context, route
from ..models import (
    KIND_AGENT_MODE,
    KIND_CLEAR,
    KIND_HELP,
    KIND_QUIT,
    MODE_OPERATOR,
)
from . import avatar, keymap, render
from .help_view import HelpScreen
from .palette import CommandPalette

_STATUS_COMMANDS = {"status", "harness", "runtime", "doctor"}


class ForgekitConsoleApp(App):
    """The operator cockpit. One screen, always-on, bottom-input centric."""

    CSS = """
    Screen { layout: vertical; }
    #topbar { height: 1; background: $boost; color: $text; padding: 0 1; }
    #body { height: 1fr; }
    #agents { width: 24; border-right: solid $panel-darken-2; padding: 0 1; }
    #center { width: 1fr; padding: 0 1; }
    #status { width: 42; border-left: solid $panel-darken-2; padding: 0 1; }
    #promptrow { height: 3; background: $panel; border-top: tall $accent; }
    #modebadge { width: auto; padding: 1 1 0 1; }
    #prompt { border: none; background: $panel; }
    """

    BINDINGS = [
        Binding(key, action, desc) for key, action, desc in keymap.ACTION_BINDINGS
    ] + [
        # input-cooperating keys — priority so the app sees them before the Input.
        Binding("tab", "palette_next", "next", show=False, priority=True),
        Binding("shift+tab", "palette_prev", "prev", show=False, priority=True),
        Binding("down", "palette_next", "next", show=False, priority=True),
        Binding("up", "palette_prev", "prev", show=False, priority=True),
        Binding("escape", "dismiss_overlay", "dismiss", show=False, priority=True),
    ]

    def __init__(self, *, repo_root: Path, context: Optional[ConsoleContext] = None) -> None:
        super().__init__()
        self.repo_root = Path(repo_root)
        self.context = context or build_default_context(self.repo_root)
        self.context.profile = self.context.profile or "operator"
        self.mode = MODE_OPERATOR
        self._palette = palette_state.CLOSED
        self._suppress_refilter = False

    # --- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(self._topbar_text(), id="topbar")
        with Horizontal(id="body"):
            yield Static(id="agents")
            with Vertical(id="center"):
                yield RichLog(id="log", markup=True, wrap=True, highlight=False)
            yield Static(id="status")
        yield CommandPalette(id="palette")
        with Horizontal(id="promptrow"):
            yield Static(id="modebadge")
            yield Input(placeholder="명령 입력 — `/` 팔레트 · Tab 자동완성 · F1 도움말", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.title = render.BRAND
        self.sub_title = render.TAGLINE
        self.query_one("#agents", Static).update(
            "\n".join(render.agent_pane_lines(load_agents()))
        )
        log = self.query_one("#log", RichLog)
        for line in avatar.render_avatar():
            log.write(line)
        log.write("")
        for line in render.welcome_banner(str(self.repo_root), self.context.profile):
            log.write(line)
        self._refresh_status()
        self._refresh_mode_badge()
        self.query_one("#prompt", Input).focus()

    def _topbar_text(self) -> str:
        return f"[b orange1]{render.BRAND}[/b orange1] · {render.TAGLINE} · [dim]{self.repo_root}[/dim]"

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
        if not raw.strip():
            return
        self._execute(raw)

    def action_palette_next(self) -> None:
        self._cycle(1)

    def action_palette_prev(self) -> None:
        self._cycle(-1)

    def action_dismiss_overlay(self) -> None:
        # App-level priority binding fires before a modal's own escape, so pop a
        # pushed overlay (e.g. help) here first.
        if len(self.screen_stack) > 1:
            self.pop_screen()
            return
        if self._palette.is_open:
            self._close_palette()
            return
        if self.mode != MODE_OPERATOR:
            self.mode = MODE_OPERATOR
            self._refresh_mode_badge()
            self.query_one("#log", RichLog).write("[dim]› operator 모드로 복귀[/dim]")

    def _cycle(self, direction: int) -> None:
        if not self._palette.is_open:
            return
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
        self._refresh_mode_badge()

    def _close_palette(self) -> None:
        self._palette = palette_state.close()
        self.query_one("#palette", CommandPalette).hide()
        self._refresh_mode_badge()

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
        if result.kind == KIND_HELP:
            self.action_open_help()
            return
        log = self.query_one("#log", RichLog)
        log.write(f"[dim]›[/dim] {raw}")
        for line in render.result_block(result.title, result.lines):
            log.write(line)
        if result.kind == KIND_AGENT_MODE:
            # router titles agent results as "agent:<id>" — already a mode string.
            self.mode = result.title
            self._refresh_mode_badge()
        if parsed.name in _STATUS_COMMANDS:
            self._refresh_status()

    # --- actions ------------------------------------------------------------

    def action_open_help(self) -> None:
        self.push_screen(HelpScreen(self.context.commands, self.context.agents))

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    def action_refresh_status(self) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        summary = self.context.load_operator()
        self.query_one("#status", Static).update(
            "\n".join(render.status_pane_lines(summary))
        )

    def _refresh_mode_badge(self) -> None:
        mode = "palette" if self._palette.is_open else self.mode
        self.query_one("#modebadge", Static).update(
            render.mode_badge(mode, self.context.agents)
        )


__all__ = ("ForgekitConsoleApp",)
