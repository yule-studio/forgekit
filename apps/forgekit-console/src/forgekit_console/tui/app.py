"""Forgekit operator console — the Textual application (content-first flow).

Reading flow, top→bottom: context header · operator status pill · thin input row
· inline palette · inline help · main log (+ optional dashboard rail) · contextual
hint. Input sits near the top; command results, help, and palette expand inline
below it so the screen reads as one downward document — not a multi-pane cockpit.

The app stays thin: parsing/routing/palette-state live in the pure ``commands``
core, all rendering strings in ``tui.render`` / ``tui.avatar``, CSS in
``tui.styles``, and the palette/help inline surfaces in their own widgets. This
module owns only the live widget state (mode, layout, palette, help).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Input, RichLog, Static

from ..commands import palette as palette_state
from ..commands.parser import parse_input
from ..commands.router import ConsoleContext, build_default_context, route
from ..models import (
    KIND_AGENT_MODE,
    KIND_CLEAR,
    KIND_HELP,
    KIND_LAYOUT,
    KIND_QUIT,
    LAYOUT_DASHBOARD,
    LAYOUT_FOCUS,
    MODE_OPERATOR,
)
from . import avatar, keymap, render
from .help_view import InlineHelp
from .palette import CommandPalette
from .styles import SCREEN_CSS

_STATUS_COMMANDS = {"status", "harness", "runtime", "doctor"}


class ForgekitConsoleApp(App):
    """The operator console — one screen, content-first reading flow."""

    CSS = SCREEN_CSS

    BINDINGS = [
        Binding(key, action, desc) for key, action, desc in keymap.ACTION_BINDINGS
    ] + [
        Binding("tab", "palette_next", "next", show=False, priority=True),
        Binding("shift+tab", "palette_prev", "prev", show=False, priority=True),
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
        self.layout_mode = LAYOUT_FOCUS
        self._palette = palette_state.CLOSED
        self._suppress_refilter = False

    # --- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        yield Static(id="statuspill")
        with Horizontal(id="inputrow"):
            yield Static(id="modepill")
            yield Input(placeholder="명령 입력 — `/` 팔레트 · Tab 자동완성 · F1 도움말", id="prompt")
        yield CommandPalette(id="palette")
        yield InlineHelp(id="help")
        with Horizontal(id="body"):
            yield RichLog(id="log", markup=True, wrap=True, highlight=False)
            yield Static(id="rail")
        yield Static(id="hint")

    def on_mount(self) -> None:
        self.title = render.BRAND
        self.sub_title = render.TAGLINE
        self.query_one("#header", Static).update(self._header_text())
        log = self.query_one("#log", RichLog)
        for line in avatar.mini_brandmark():  # small crisp mark — never a raster
            log.write(line)
        log.write("")
        for line in render.welcome_banner(str(self.repo_root), self.context.profile):
            log.write(line)
        self._refresh_status()
        self._refresh_chrome()
        self.query_one("#prompt", Input).focus()

    def _header_text(self) -> str:
        return (
            f"[b orange1]forge[/b orange1][b orange3]kit[/b orange3] "
            f"[dim]▸ {render.TAGLINE}  ·  {self.repo_root}  ·  view {self.layout_mode}[/dim]"
        )

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

    def action_palette_next(self) -> None:
        self._cycle_or_scroll(1)

    def action_palette_prev(self) -> None:
        self._cycle_or_scroll(-1)

    def action_dismiss(self) -> None:
        if self.query_one("#help", InlineHelp).is_open:
            self._close_help()
            return
        if self._palette.is_open:
            self._close_palette()
            return
        if self.mode != MODE_OPERATOR:
            self.mode = MODE_OPERATOR
            self._refresh_chrome()
            self.query_one("#log", RichLog).write("[dim]› operator 모드로 복귀[/dim]")

    def _cycle_or_scroll(self, direction: int) -> None:
        if self._palette.is_open:
            self._palette = palette_state.cycle(self._palette, direction)
            text = palette_state.completion_text(self._palette)
            if text is not None:
                self._suppress_refilter = True
                prompt = self.query_one("#prompt", Input)
                prompt.value = text
                prompt.cursor_position = len(text)
            self._render_palette()
            return
        help_view = self.query_one("#help", InlineHelp)
        if help_view.is_open:  # arrows scroll the inline help when it's open
            (help_view.scroll_down if direction > 0 else help_view.scroll_up)()

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
        if result.kind == KIND_HELP:
            self.action_open_help()
            return
        if result.kind == KIND_LAYOUT:
            self._toggle_layout()
            return
        log = self.query_one("#log", RichLog)
        log.write(f"[dim]›[/dim] {raw}")
        for line in render.result_block(result.title, result.lines):
            log.write(line)
        if result.kind == KIND_AGENT_MODE:
            self.mode = result.title  # router titles agent results as "agent:<id>"
            self._refresh_chrome()
        if parsed.name in _STATUS_COMMANDS:
            self._refresh_status()

    # --- help / layout / actions -------------------------------------------

    def action_open_help(self) -> None:
        help_view = self.query_one("#help", InlineHelp)
        if help_view.is_open:
            self._close_help()
            return
        self._close_palette()
        help_view.open(self.context.commands, self.context.agents)
        self._refresh_chrome()

    def _close_help(self) -> None:
        self.query_one("#help", InlineHelp).close()
        self._refresh_chrome()

    def _toggle_layout(self) -> None:
        self.layout_mode = (
            LAYOUT_DASHBOARD if self.layout_mode == LAYOUT_FOCUS else LAYOUT_FOCUS
        )
        rail = self.query_one("#rail", Static)
        rail.set_class(self.layout_mode == LAYOUT_DASHBOARD, "-show")
        self.query_one("#header", Static).update(self._header_text())
        self._refresh_status()
        self.query_one("#log", RichLog).write(f"[dim]› view: {self.layout_mode}[/dim]")

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    def action_refresh_status(self) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        summary = self.context.load_operator()
        self.query_one("#statuspill", Static).update(render.status_pill(summary))
        if self.layout_mode == LAYOUT_DASHBOARD:
            self.query_one("#rail", Static).update(
                "\n".join(render.status_pane_lines(summary))
            )

    def _refresh_chrome(self) -> None:
        mode = "palette" if self._palette.is_open else self.mode
        self.query_one("#modepill", Static).update(render.mode_pill(mode, self.context.agents))
        self.query_one("#hint", Static).update(
            render.hint_line(
                palette_open=self._palette.is_open,
                help_open=self.query_one("#help", InlineHelp).is_open,
                in_agent=self.mode != MODE_OPERATOR,
            )
        )


__all__ = ("ForgekitConsoleApp",)
