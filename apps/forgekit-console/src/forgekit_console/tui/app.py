"""Forgekit operator console — Claude-Code-style chat-first layout.

Top→bottom, TOP-ALIGNED: a small real-image avatar + brand/meta intro (fixed
banner) · then a single session flow of one quiet issue line · the main panel (a
transcript XOR help-view state machine, ``height: auto``) · a **session-following
inline composer** (mode pill + input + inline palette) that renders IMMEDIATELY
AFTER the active content · a one-line hint. When the session is short the composer
sits near the top with empty space below; as the transcript grows the content
pushes the composer down and the :class:`tui.session_flow.SessionFlow` scroll
keeps it in view. The user reads + types "at the end of the current session",
exactly like Claude Code — the composer is NOT docked to the viewport bottom.

``/help`` (and F1) does NOT append into the transcript — it switches the whole
main area to a dedicated help VIEW (transcript hidden). The composer still sits
right BELOW the help view (active content). Esc switches back to the transcript
exactly as it was. The view switch lives in :class:`tui.main_panel.MainPanel`.

Pure logic (parse/route/palette-state/intro/help strings + image-renderer
selection) lives in the core/helpers; this module owns the live widget state
(mode, palette) and the wiring between them.
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
    KIND_ERROR,
    KIND_HELP,
    KIND_LAYOUT,
    KIND_QUIT,
    MODE_OPERATOR,
)
from . import keymap, render, theme
from .composer import Composer
from .header import IntroHeader
from .main_panel import MainPanel
from .palette import CommandPalette
from .session_flow import SessionFlow
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

    def __init__(
        self,
        *,
        repo_root: Path,
        context: Optional[ConsoleContext] = None,
        escalator=None,
    ) -> None:
        super().__init__()
        self.repo_root = Path(repo_root)
        self.context = context or build_default_context(self.repo_root)
        self.context.profile = self.context.profile or "operator"
        self.mode = MODE_OPERATOR
        self._palette = palette_state.CLOSED
        self._suppress_refilter = False
        # Repeated-failure escalation: same blocked signature past the threshold
        # (default 3) auto-surfaces a mini-RCA instead of failing silently. Injected
        # in tests (tempdir paths / fake notifier); built from env in production.
        if escalator is None:
            from ..lifecycle.failure_escalation import FailureEscalator

            escalator = FailureEscalator()
        self._escalator = escalator
        self._blocked = False

    def get_css_variables(self) -> dict:
        # Merge the forgekit brand tokens into the global variable scope so the
        # screen CSS AND every widget's DEFAULT_CSS resolve $accent / $brand-border
        # / $text etc. against the cyan/magenta-on-black palette (tui.theme).
        variables = super().get_css_variables()
        variables.update(theme.css_variables())
        return variables

    # --- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        # top (fixed banner): small real-image avatar + brand/version/provider/...
        yield IntroHeader(
            repo=str(self.repo_root),
            version=__version__,
            profile=self.context.profile,
            id="intro",
        )
        # the live session is a single TOP-ALIGNED vertical flow that scrolls:
        #   issue line → active content (transcript XOR help) → inline composer → hint.
        # The composer renders right after the content (height: auto), so a short
        # session leaves it near the top with empty space below; as content grows
        # the flow scrolls to keep the composer in view.
        with SessionFlow(id="flow"):
            # one quiet issue line under the intro
            yield Static(id="issue")
            # main area — a transcript XOR help-view state machine (mutually exclusive)
            yield MainPanel(id="main")
            # session-following inline composer BAR — input row + inline palette
            # (opens below the input) + the sub-hint row, all inside one bar.
            yield Composer(id="composer")

    def on_mount(self) -> None:
        self.title = render.BRAND
        self.sub_title = render.TAGLINE
        # Claude-style idle: the transcript starts EMPTY (no pre-filled welcome
        # banner). The same `/help · / palette · …` guidance lives in the composer
        # hint row, so the welcome line was redundant and showed as a stray band.
        self._refresh_issue()
        self._refresh_chrome()
        self.query_one("#prompt", Input).focus()
        self._follow_tail()

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
        elif self._main.help_open:
            self._main.switch_help_tab(1)

    def action_prev(self) -> None:
        if self._palette.is_open:
            self._cycle(-1)
        elif self._main.help_open:
            self._main.switch_help_tab(-1)

    def action_palette_next(self) -> None:
        if self._palette.is_open:
            self._cycle(1)

    def action_palette_prev(self) -> None:
        if self._palette.is_open:
            self._cycle(-1)

    def action_dismiss(self) -> None:
        if self._main.help_open:
            self._close_help()
            return
        if self._palette.is_open:
            self._close_palette()
            return
        if self.mode != MODE_OPERATOR:
            self.mode = MODE_OPERATOR
            self._refresh_chrome()
            self._transcript.write("[dim]› operator 모드로 복귀[/dim]")

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
            self._transcript.clear()
            self._main.show_transcript()
            return
        if result.kind in (KIND_HELP, KIND_LAYOUT):
            self._open_help()  # /layout also routes to help in this flow
            return
        log = self._transcript
        log.write_echo(raw)
        log.write_result(result.title, result.lines)
        if result.kind == KIND_AGENT_MODE:
            self.mode = result.title  # router titles agent results as "agent:<id>"
            self._refresh_chrome()
        if result.kind == KIND_ERROR:
            self._record_failure(parsed, result)
        if (parsed.name or "") == "render":
            self._observe_render()  # repeated blocked render → escalate w/ alternatives
        if parsed.name in _STATUS_COMMANDS:
            self._refresh_issue()
        # keep the inline composer in view as the session grows (Claude-style tail)
        self._follow_tail()

    def _record_failure(self, parsed, result) -> None:
        """Feed a failed command into the escalator; surface advisory or full RCA."""

        from ..lifecycle.failure_escalation import FailureSignature, KIND_COMMAND, KIND_STATUS_SURFACE

        kind = KIND_STATUS_SURFACE if (parsed.name or "") in _STATUS_COMMANDS else KIND_COMMAND
        signature = FailureSignature(kind, result.title or "error", parsed.name or "")
        outcome = self._escalator.record_failure(
            signature, symptom=result.title or "", evidence="\n".join(result.lines)
        )
        self._surface_escalation(outcome)

    def _observe_render(self) -> None:
        """Treat a repeated NON-true-raster render as a blocked UI/render issue.

        The operator ran ``/render`` and it is still a fallback. That is fine ONCE
        (managed fallback is intentional), but if the same render limitation persists
        across repeated checks it should not stay silent — past the threshold it
        escalates with render-specific alternatives (a graphics terminal / Python
        3.10+) and a blocked banner, so "why won't the real image show" is answered.
        """

        from ..lifecycle.failure_escalation import FailureSignature, KIND_RENDERER
        from .render_readiness import render_readiness_report

        report = render_readiness_report()
        if report.true_raster_ready:
            return  # real raster — nothing blocked
        if not report.lib_ok:
            reason = "lib-unavailable"
        elif "no known" in (report.capability_reason or ""):
            reason = "terminal-no-graphics"
        else:
            reason = "no-true-raster"
        signature = FailureSignature(KIND_RENDERER, reason, report.avatar_backend)
        outcome = self._escalator.record_failure(
            signature,
            symptom=f"avatar/brand 가 fallback({report.avatar_backend})로 반복 렌더됨",
            evidence=f"cap={report.capability_reason} · lib_ok={report.lib_ok} · backend={report.lib_backend}",
            attempted_fix="prime_image_backend (앱 시작 전 early probe)",
        )
        self._surface_escalation(outcome)

    def _surface_escalation(self, outcome) -> None:
        """Surface an escalation outcome: advisory below threshold, full RCA + blocked
        banner once it crosses it — never a silent repeated failure."""

        log = self._transcript
        if outcome.escalated and outcome.report is not None:
            for line in outcome.report.to_lines():
                log.write(line)
            self._blocked = True
            self._refresh_issue()  # flip the issue line to a blocked banner
        else:
            log.write(outcome.advisory)

    # --- help (a VIEW SWITCH, not a transcript append; composer stays visible) --

    def action_open_help(self) -> None:
        if self._main.help_open:
            self._close_help()
        else:
            self._open_help()

    def _open_help(self) -> None:
        # Switch the whole main area to the dedicated help view (transcript hidden).
        # Nothing is appended to the transcript — opening/switching tabs re-renders
        # the help panel in place. Claude-style: the composer BAR is HIDDEN in the
        # help/tab view (the Esc/Tab guidance lives in the help body), so help reads
        # as its own mode rather than "help with an input bar still stuck below".
        self._close_palette()
        self._main.show_help(self.context.commands, self.context.agents)
        self.query_one("#composer", Composer).display = False
        self._refresh_chrome()

    def _close_help(self) -> None:
        # Switch back to the transcript exactly as it was (nothing left behind) and
        # restore the composer bar + focus the input.
        self._main.show_transcript()
        self.query_one("#composer", Composer).display = True
        self._refresh_chrome()
        self.query_one("#prompt", Input).focus()

    @property
    def _flow(self) -> SessionFlow:
        return self.query_one("#flow", SessionFlow)

    def _follow_tail(self) -> None:
        """Scroll the session flow so the inline composer stays in view."""

        # call_after_refresh so the layout (new content height) is settled first.
        self.call_after_refresh(self._flow.follow_tail)

    @property
    def _main(self) -> MainPanel:
        return self.query_one("#main", MainPanel)

    @property
    def _transcript(self) -> Transcript:
        return self._main.transcript

    # --- chrome / status ----------------------------------------------------

    def action_clear_log(self) -> None:
        self._transcript.clear()
        self._main.show_transcript()

    def action_refresh_status(self) -> None:
        self._refresh_issue()

    def _refresh_issue(self) -> None:
        if self._blocked:
            # a repeated failure crossed the threshold — surface a blocked banner
            # over the normal status line so it can't be missed (details: `/blocked`).
            self.query_one("#issue", Static).update(render.blocked_banner())
            return
        summary = self.context.load_operator()
        self.query_one("#issue", Static).update(render.issue_line(summary))

    def _refresh_chrome(self) -> None:
        mode = "palette" if self._palette.is_open else self.mode
        # Claude-style idle: NO mode row above the input in the default operator
        # state. The mode pill only shows for agent / palette states.
        modepill = self.query_one("#modepill", Static)
        show_mode = self._palette.is_open or self.mode != MODE_OPERATOR
        modepill.display = show_mode
        if show_mode:
            modepill.update(render.mode_pill(mode, self.context.agents))
        self.query_one("#hint", Static).update(
            render.hint_line(
                palette_open=self._palette.is_open,
                help_open=self._main.help_open,
                in_agent=self.mode != MODE_OPERATOR,
            )
        )


__all__ = ("ForgekitConsoleApp",)
