"""Forgekit operator console — the Textual application (cockpit frame).

Layout: header (brand/repo/profile) · left agents pane · center log · right status
pane · bottom input + slash palette · footer key hints.

The app is intentionally thin: parsing/routing/status-shaping live in the pure
core (``commands`` / ``data``), and the string rendering lives in
:mod:`tui.render`. This module only wires those into textual widgets. textual is
imported at module load, so importing this module requires textual installed —
the entrypoint (:mod:`app.main`) guards that and degrades gracefully.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, RichLog, Static

from ..commands.parser import palette_matches, parse_input
from ..commands.registry import load_agents
from ..commands.router import ConsoleContext, build_default_context, route
from ..models import KIND_CLEAR, KIND_QUIT
from . import render


class ForgekitConsoleApp(App):
    """The operator cockpit. One screen, always-on."""

    CSS = """
    Screen { layout: vertical; }
    #topbar { height: 3; background: $boost; color: $text; padding: 0 1; border-bottom: solid $accent; }
    #body { height: 1fr; }
    #agents { width: 24; border-right: solid $panel-darken-2; padding: 0 1; }
    #center { width: 1fr; padding: 0 1; }
    #status { width: 40; border-left: solid $panel-darken-2; padding: 0 1; }
    #palette { height: auto; max-height: 8; color: $text-muted; padding: 0 1; }
    #prompt { border: tall $accent; }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_log", "Clear"),
        ("ctrl+r", "refresh_status", "Refresh"),
    ]

    def __init__(self, *, repo_root: Path, context: Optional[ConsoleContext] = None) -> None:
        super().__init__()
        self.repo_root = Path(repo_root)
        self.context = context or build_default_context(self.repo_root)
        self.context.profile = self.context.profile or "operator"

    # --- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(self._topbar_text(), id="topbar")
        with Horizontal(id="body"):
            yield Static(id="agents")
            with Vertical(id="center"):
                yield RichLog(id="log", markup=True, wrap=True, highlight=False)
            yield Static(id="status")
        yield Static(id="palette")
        yield Input(placeholder="명령 입력 — `/` 로 팔레트, `/help` 로 도움말", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.title = render.BRAND
        self.sub_title = render.TAGLINE
        self.query_one("#agents", Static).update(
            "\n".join(render.agent_pane_lines(load_agents()))
        )
        log = self.query_one("#log", RichLog)
        for line in render.welcome_banner(str(self.repo_root), self.context.profile):
            log.write(line)
        self._refresh_status()
        self.query_one("#prompt", Input).focus()

    def _topbar_text(self) -> str:
        return f"[b]{render.BRAND}[/b] · {render.TAGLINE} · [dim]{self.repo_root}[/dim]"

    # --- events -------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        matches = palette_matches(event.value, self.context.commands)
        palette = self.query_one("#palette", Static)
        palette.update("\n".join(render.palette_lines(matches)))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value
        self.query_one("#prompt", Input).value = ""
        self.query_one("#palette", Static).update("")
        if not raw.strip():
            return
        parsed = parse_input(raw)
        result = route(parsed, self.context)
        if result.kind == KIND_QUIT:
            self.exit()
            return
        log = self.query_one("#log", RichLog)
        if result.kind == KIND_CLEAR:
            log.clear()
            return
        # echo the input, then the framed result
        log.write(f"[dim]›[/dim] {raw}")
        for line in render.result_block(result.title, result.lines):
            log.write(line)
        # status-affecting commands refresh the right pane
        if parsed.name in {"status", "harness", "runtime", "doctor"}:
            self._refresh_status()

    # --- actions ------------------------------------------------------------

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    def action_refresh_status(self) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        summary = self.context.load_operator()
        self.query_one("#status", Static).update(
            "\n".join(render.status_pane_lines(summary))
        )


__all__ = ("ForgekitConsoleApp",)
