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
from . import intro_state, keymap, render, theme
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
        submit_service=None,
        config: Optional[dict] = None,
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
        # Free-text live-submit: resolves a provider (config or zero-config local
        # ollama) and submits. Injected in tests (fake transport); real on disk.
        if submit_service is None:
            from ..chat.service import build_default_service

            submit_service = build_default_service()
        self._submit_service = submit_service
        # Runtime MODE + provider posture (WT1): the on-disk config decides setup
        # readiness; the runtime mode × main-provider profile resolves a concrete
        # EffectivePolicy. Shift+Tab cycles the mode → a REAL policy change.
        from ..chat.service import load_config
        from ..policy import runtime_mode as _rm
        from ..policy.setup_state import resolve_setup_state

        self._config = load_config() if config is None else config
        self._setup = resolve_setup_state(self._config)
        self._runtime_mode = _rm.DEFAULT_MODE
        self._effective_policy = None
        self._recompute_policy()

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
        self._sync_intro()  # empty + idle → wide hero art; working state → compact
        self.query_one("#prompt", Input).focus()
        self._follow_tail()

    # --- input / palette ----------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._suppress_refilter:
            self._suppress_refilter = False
            self._refresh_chrome()  # keep the below-bar hint in sync (typing vs idle)
            return
        self._palette = palette_state.refilter(event.value, self.context.commands)
        self._render_palette()
        self._refresh_chrome()  # typing reduces the secondary mode line below the bar
        self._sync_intro()  # typing (or opening the palette) → fold the hero to compact

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
        else:
            # idle (no palette / no help) → Shift+Tab cycles the RUNTIME MODE, which
            # recomputes the EffectivePolicy (real routing/usage/approval change).
            self._cycle_runtime_mode(1)

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
        self._sync_intro()  # opening the palette folds a fresh hero to compact

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
        if not parsed.is_slash:
            # FREE TEXT → live provider submit (NOT the slash command path).
            self._submit_free_text(raw)
            return
        if parsed.name == "mode":
            # /mode renders the LIVE runtime-mode posture (app state, not pure router).
            self._show_mode_surface()
            return
        result = route(parsed, self.context)
        if result.kind == KIND_QUIT:
            self.exit()
            return
        if result.kind == KIND_CLEAR:
            self._transcript.clear()
            self._main.show_transcript()
            return
        if result.kind in (KIND_HELP, KIND_LAYOUT):
            # /about · /welcome route here with title "about" → hero + About tab.
            self._open_help(about=(result.title == "about"))
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
        # transcript now has content → fold the hero to the compact working header.
        self._sync_intro()
        # keep the inline composer in view as the session grows (Claude-style tail)
        self._follow_tail()

    # --- free-text live submit ----------------------------------------------

    def _submit_free_text(self, raw: str) -> None:
        """Echo the user message, then submit to the resolved provider in a worker.

        The submit (HTTP / resolution) runs OFF the event loop so an LLM call never
        freezes the UI; the result is appended back on the main thread. Free text is
        a strictly separate path from slash commands.
        """

        text = raw.strip()
        if not text:
            return
        self._transcript.write_echo(text)  # the user's message
        self._sync_intro()  # transcript now has content → compact working header
        self._follow_tail()
        self.run_worker(
            lambda: self._submit_blocking(text), thread=True, group="submit", exclusive=False
        )

    def _submit_blocking(self, text: str) -> None:
        result = self._submit_service.submit(text)  # blocking IO (worker thread)
        self.call_from_thread(self._on_submit_result, result)

    def _on_submit_result(self, result) -> None:
        log = self._transcript
        for line in result.to_lines():
            log.write(line)
        if not result.ok:
            self._record_submit_failure(result)
        self._follow_tail()
        try:
            self.query_one("#prompt", Input).focus()
        except Exception:  # noqa: BLE001 - prompt may be transiently unavailable
            pass

    def _record_submit_failure(self, result) -> None:
        """A non-live submit (no provider / auth / unsupported / transport) → escalation."""

        from ..lifecycle.failure_escalation import FailureSignature, KIND_DEPENDENCY

        signature = FailureSignature(
            KIND_DEPENDENCY, f"submit:{result.category}", result.provider_id or "free-text"
        )
        outcome = self._escalator.record_failure(
            signature, symptom=result.text, evidence=result.receipt(),
            attempted_fix=result.next_action,
        )
        self._surface_escalation(outcome)

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

        from .ansi_icon import render as ar

        report = render_readiness_report()
        if report.true_raster_ready:
            return  # real raster — nothing blocked
        # An UNSAFE/INVALID/missing ANSI asset is a distinct, actionable cause: the
        # non-raster default could not use the ANSI icon, so name it in the signature.
        if report.ansi_status in (ar.STATUS_UNSAFE, ar.STATUS_INVALID, ar.STATUS_NO_ASSET):
            reason = f"ansi-{report.ansi_status}"
        elif not report.lib_ok:
            reason = "lib-unavailable"
        elif "no known" in (report.capability_reason or ""):
            reason = "terminal-no-graphics"
        else:
            reason = "no-true-raster"
        signature = FailureSignature(KIND_RENDERER, reason, report.avatar_backend)
        outcome = self._escalator.record_failure(
            signature,
            symptom=f"avatar/brand 가 fallback({report.avatar_backend})로 반복 렌더됨",
            evidence=(
                f"cap={report.capability_reason} · lib_ok={report.lib_ok} · "
                f"backend={report.lib_backend} · ansi={report.ansi_status}/{report.ansi_theme}"
            ),
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

    def _open_help(self, *, about: bool = False) -> None:
        # Switch the whole main area to the dedicated help view (transcript hidden).
        # Nothing is appended to the transcript — opening/switching tabs re-renders
        # the help panel in place. Claude-style: the composer BAR is HIDDEN in the
        # help/tab view (the Esc/Tab guidance lives in the help body), so help reads
        # as its own mode rather than "help with an input bar still stuck below".
        # `about=True` (/about, /welcome) jumps to the About tab AND shows the wide
        # hero art in the header (the 56-col art's proper home).
        self._close_palette()
        self._main.show_help(
            self.context.commands, self.context.agents,
            focus_title="About" if about else None,
        )
        self.query_one("#composer", Composer).display = False
        self._refresh_chrome()
        self._sync_intro()  # About → hero; plain /help → compact

    def _close_help(self) -> None:
        # Switch back to the transcript exactly as it was (nothing left behind) and
        # restore the composer bar + focus the input.
        self._main.show_transcript()
        self.query_one("#composer", Composer).display = True
        self._refresh_chrome()
        self._sync_intro()  # leaving About/help → recompute hero vs compact
        self.query_one("#prompt", Input).focus()

    @property
    def _flow(self) -> SessionFlow:
        return self.query_one("#flow", SessionFlow)

    def _follow_tail(self) -> None:
        """Scroll the session flow so the inline composer stays in view."""

        # call_after_refresh so the layout (new content height) is settled first.
        self.call_after_refresh(self._flow.follow_tail)

    @property
    def _intro(self) -> IntroHeader:
        return self.query_one("#intro", IntroHeader)

    def _about_open(self) -> bool:
        """True when the help view is open ON the About tab (drives the hero)."""

        if not self._main.help_open:
            return False
        sections = render.help_sections(self.context.commands, self.context.agents)
        idx = self._main.help_panel.active_tab
        return 0 <= idx < len(sections) and sections[idx].title == "About"

    def _sync_intro(self) -> None:
        """Recompute the intro mode (hero vs compact) from the live state + env.

        Hero on a fresh, idle, empty session and on the /about surface; compact the
        moment real work starts (typing, palette, agent mode, or transcript content)
        — the "big first impression, small while working" rule.
        """

        try:
            intro = self._intro
        except Exception:  # noqa: BLE001 - intro not mounted yet
            return
        mode = intro_state.resolve_intro_mode(
            hero_available=intro.hero_available(),
            transcript_empty=len(self._transcript.lines) == 0,
            typing=bool((self._prompt_value() or "").strip()),
            palette_open=self._palette.is_open,
            in_agent=self.mode != MODE_OPERATOR,
            help_open=self._main.help_open,
            about_open=self._about_open(),
        )
        intro.set_mode(mode)

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
        if self._setup.blocked:
            # no provider configured → forgekit can't run; surface setup-required.
            self.query_one("#issue", Static).update(render.setup_required_banner())
            return
        pol = self._effective_policy
        if pol is not None:
            self.query_one("#issue", Static).update(
                render.runtime_mode_line(
                    pol.mode_label, pol.provider_policy_mode, pol.usage.usage_mode,
                    pol.approval, loop=pol.background_loop,
                )
            )
            return
        summary = self.context.load_operator()
        self.query_one("#issue", Static).update(render.issue_line(summary))

    # --- runtime mode (Shift+Tab → real policy change) ----------------------

    def _recompute_policy(self) -> None:
        """Resolve the EffectivePolicy from the current mode × main-provider profile."""

        from ..policy import runtime_mode as rm

        profile = self._setup.profile if self._setup else None
        if profile is None:
            self._effective_policy = None
            return
        self._effective_policy = rm.resolve_effective_policy(profile, self._runtime_mode)

    def _cycle_runtime_mode(self, direction: int = 1) -> None:
        """Shift+Tab: advance the runtime mode and recompute the real policy."""

        from ..policy import runtime_mode as rm

        if self._setup.blocked:
            self._transcript.write(
                "[dim]provider 미설정 — 모드 전환 전에 setup 이 필요합니다 (`/doctor`).[/dim]"
            )
            self._sync_intro()
            self._follow_tail()
            return
        self._runtime_mode = rm.cycle_mode(self._runtime_mode, direction)
        self._recompute_policy()
        pol = self._effective_policy
        if pol is not None:
            self._transcript.write(
                render.runtime_mode_line(
                    pol.mode_label, pol.provider_policy_mode, pol.usage.usage_mode,
                    pol.approval, loop=pol.background_loop,
                )
            )
        self._refresh_issue()
        self._refresh_chrome()
        self._sync_intro()
        self._follow_tail()

    def _show_mode_surface(self) -> None:
        """/mode — the live runtime-mode table + the resolved EffectivePolicy."""

        from ..policy import runtime_mode as rm

        log = self._transcript
        log.write_echo("/mode")
        if self._setup.blocked:
            log.write_result("mode", (
                "[dim]provider 미설정 — 모드는 provider 설정 후 적용됩니다.[/dim]",
                *(f"  - {a}" for a in self._setup.next_actions),
            ))
            self._sync_intro()
            self._follow_tail()
            return
        pol = self._effective_policy
        lines = [
            f"현재 모드: [b]{pol.mode_label}[/b]  (Shift+Tab 으로 순환)",
            f"  main provider : {pol.main_provider}",
            f"  routing       : {pol.provider_policy_mode}  → chat slot = {pol.routing_target()}",
            f"  usage         : {pol.usage.usage_mode} (reserve {pol.usage.reserve})",
            f"  autonomy      : {pol.autonomy}",
            f"  approval      : {pol.approval}",
            f"  background    : {'on' if pol.background_loop else 'off'}",
            f"  budget        : {pol.budget_posture}",
            "",
            "사용 가능한 모드:",
        ]
        for m in rm.all_modes():
            mark = "●" if m.id == pol.mode_id else "○"
            lines.append(f"  {mark} [b]{m.label:<13}[/b] [dim]{m.purpose}[/dim]")
        log.write_result("mode", tuple(lines))
        self._sync_intro()
        self._follow_tail()

    def _refresh_chrome(self) -> None:
        mode = "palette" if self._palette.is_open else self.mode
        # Both the mode pill and the hint are SECONDARY rows BELOW the input bar
        # (Claude-style). The mode pill only appears for agent / palette states; in
        # the default operator state it is hidden. The hint is the bottom mode line.
        modepill = self.query_one("#modepill", Static)
        show_mode = self._palette.is_open or self.mode != MODE_OPERATOR
        modepill.display = show_mode
        if show_mode:
            modepill.update(render.mode_pill(mode, self.context.agents))
        typing = bool((self._prompt_value() or "").strip())
        self.query_one("#hint", Static).update(
            render.hint_line(
                palette_open=self._palette.is_open,
                help_open=self._main.help_open,
                in_agent=self.mode != MODE_OPERATOR,
                typing=typing,
            )
        )

    def _prompt_value(self) -> str:
        try:
            return self.query_one("#prompt", Input).value
        except Exception:  # noqa: BLE001 - prompt not mounted yet
            return ""


__all__ = ("ForgekitConsoleApp",)
