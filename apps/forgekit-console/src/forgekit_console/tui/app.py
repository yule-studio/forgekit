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
        notifier=None,
        usage_ledger_path=None,
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
        self._mode_pinned = False  # True once the operator explicitly sets the mode
        self._effective_policy = None
        self._recompute_policy()
        # Operator notifications (WT4): desktop is opt-in (FORGEKIT_NOTIFY, same as the
        # escalator) so the console never spams; the inbox record is always durable.
        if notifier is None:
            from ..lifecycle.failure_escalation import notify_enabled
            from ..notify.service import NotificationService

            notifier = NotificationService(desktop_enabled=notify_enabled())
        self._notifier = notifier
        # Token usage ledger (WT2): append-only JSONL SSoT. Feeds the WT1 submit gate's
        # budget snapshot (real throttle teeth) + /usage + budget alerts.
        from ..usage import new_session_id, usage_ledger_path as _ulp

        self._session_id = new_session_id()
        self._usage_ledger_path = usage_ledger_path if usage_ledger_path is not None else _ulp()
        self._budget_alerted = 0.0  # highest budget threshold already alerted (no spam)

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
            from ..policy import runtime_mode as _rm

            if self.mode == "agent:product-agent":
                # In PM mode, free text is a PRODUCT ASK → run the intake→handoff
                # path (structured packet + role split), NOT a raw live submit.
                self._run_pm_intake(raw)
                return
            if self._runtime_mode == _rm.MODE_IDEA_DISCOVERY:
                self._run_idea_discovery(raw)
                return
            if self._runtime_mode == _rm.MODE_VIDEO_WATCH:
                self._run_video_watch(raw)
                return
            # FREE TEXT → live provider submit (NOT the slash command path).
            self._submit_free_text(raw)
            return
        if parsed.name == "mode":
            # /mode renders the LIVE runtime-mode posture (app state, not pure router).
            self._show_mode_surface()
            return
        if parsed.name == "always-on":
            self._run_always_on_cycle()
            return
        if parsed.name == "auto":
            self._run_auto(raw)
            return
        if parsed.name == "sources":
            self._show_sources()
            return
        if parsed.name == "self-improve":
            self._run_self_improve()
            return
        if parsed.name == "red-blue":
            self._run_red_blue(raw)
            return
        if parsed.name == "autopilot":
            self._run_autopilot(raw)
            return
        if parsed.name == "digest":
            self._show_digest()
            return
        if parsed.name == "design":
            self._show_design()
            return
        if parsed.name == "usage":
            self._show_usage()
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
        # RUNTIME-TEETH (WT1): enforce the EffectivePolicy via the chat policy gate
        # BEFORE any provider call. hold-all (approval-wait) / budget throttle → no
        # provider call; the held result is rendered. Logic lives in chat.policy_gate.
        from ..chat.policy_gate import evaluate_gate

        ctx = self._build_submit_context()
        decision = evaluate_gate(ctx)
        if not decision.allowed:
            result = decision.held_result(ctx.runtime_mode)
            for line in result.to_lines():
                self._transcript.write(line)
            self._record_usage(result)  # held/throttled events are in the ledger too
            self._follow_tail()
            return
        self._follow_tail()
        self.run_worker(
            lambda: self._submit_blocking(text, ctx), thread=True, group="submit", exclusive=False
        )

    def _build_submit_context(self):
        """Build the submit policy context from the live runtime state (WT1)."""

        from ..chat.policy_gate import SubmitContext

        pol = self._effective_policy
        label = getattr(pol, "mode_label", "") if pol is not None else ""
        return SubmitContext(runtime_mode=label, effective_policy=pol,
                             usage=self._usage_snapshot())

    def _usage_snapshot(self):
        """Today's spent tokens (from the ledger) vs the config budget → gate teeth (WT2)."""

        from ..chat.policy_gate import UsageSnapshot
        from ..usage import budget_from_config, read_events, rollup, today

        try:
            rows = read_events(path=self._usage_ledger_path, day=today())
            spent = rollup(rows).total_tokens
        except Exception:  # noqa: BLE001 - ledger read must never break submit
            spent = 0
        return UsageSnapshot(spent_tokens=spent, budget_tokens=budget_from_config(self._config))

    def _record_usage(self, result) -> None:
        """Append a usage event for a submit result (WT2 ledger)."""

        from ..usage import UsageEvent, append_event, now_ts

        ev = UsageEvent(
            ts=now_ts(), session_id=self._session_id, mode=result.runtime_mode,
            provider=result.provider_id, model=result.model, category=result.category,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            total_tokens=result.total_tokens, usage_basis=result.usage_basis,
            success=result.ok, throttled=result.throttled,
        )
        append_event(ev, path=self._usage_ledger_path)
        self._check_budget_alert()

    def _check_budget_alert(self) -> None:
        """Budget threshold crossing → inbox + console (≥2 surfaces). No spam (track last)."""

        from ..usage import budget_from_config, evaluate_budget, read_events, rollup, today, alert_message

        budget = budget_from_config(self._config)
        if budget <= 0:
            return
        spent = rollup(read_events(path=self._usage_ledger_path, day=today())).total_tokens
        state = evaluate_budget(spent, budget)
        if not state.crossed or state.highest_crossed <= self._budget_alerted:
            return
        self._budget_alerted = state.highest_crossed
        msg = alert_message(state)
        # surface 1: console
        self._transcript.write(f"[{theme.WARNING}]⚠ budget {int(state.ratio*100)}%[/{theme.WARNING}] [dim]{msg}[/dim]")
        # surface 2: operator inbox (+ desktop when FORGEKIT_NOTIFY)
        try:
            from ..notify.events import EVENT_INFO_REQUIRED, NotificationEvent

            self._notifier.notify(NotificationEvent(
                EVENT_INFO_REQUIRED, "forgekit budget 임계 도달",
                why=f"오늘 토큰 {int(state.ratio*100)}% 사용", action=msg, source="budget"))
        except Exception:  # noqa: BLE001
            pass

    def _run_idea_discovery(self, raw: str) -> None:
        """Idea-discovery mode: free text (+ repo-local signals) → briefs, top→handoff hint."""

        from ..discovery import run_idea_discovery
        from ..sources import RepoLocalCollector

        text = raw.strip()
        if not text:
            return
        log = self._transcript
        log.write_echo(text)
        # seed signals = operator ask + a few offline repo-local signals (free)
        seeds = [s for s in text.replace("\n", ". ").split(".") if len(s.strip()) >= 6] or [text]
        try:
            seeds += RepoLocalCollector(self.repo_root).collect(limit=3)
        except Exception:  # noqa: BLE001
            pass
        result = run_idea_discovery(seeds, title="idea-discovery")
        for line in render.discovery_lines(result):
            log.write(line)
        self._sync_intro()
        self._follow_tail()

    def _run_video_watch(self, raw: str) -> None:
        """Video-watch mode: free text = transcript/notes (or a bare link → reference_only)."""

        from ..discovery import summarize_ingest
        from ..discovery.video_watch import VideoIngest

        text = raw.strip()
        if not text:
            return
        log = self._transcript
        log.write_echo(text)
        # a bare URL → link only (honest reference_only); otherwise treat as transcript/notes
        is_link = text.startswith("http") and " " not in text
        ingest = VideoIngest(link=text) if is_link else VideoIngest(notes=text)
        result = summarize_ingest(ingest)
        for line in render.video_watch_lines(result):
            log.write(line)
        self._sync_intro()
        self._follow_tail()

    def _show_usage(self) -> None:
        """`/usage` — today rollup (provider/mode, live vs estimate) + budget + report files."""

        from ..usage import (budget_from_config, evaluate_budget, read_events, rollup,
                             today, to_txt, top_by_tokens, write_reports)

        log = self._transcript
        log.write_echo("/usage")
        rows = read_events(path=self._usage_ledger_path, day=today())
        roll = rollup(rows, scope=f"today({today()})")
        for line in to_txt(roll, top_by_tokens(rows, limit=3)).splitlines():
            log.write(f"[dim]{line}[/dim]" if line.startswith("  ") else line)
        budget = budget_from_config(self._config)
        if budget > 0:
            st = evaluate_budget(roll.total_tokens, budget)
            log.write(f"  [dim]budget: {st.spent}/{st.budget}tok ({int(st.ratio*100)}%)"
                      + (" — 초과" if st.over else "") + "[/dim]")
        else:
            log.write("  [dim]budget: 미설정(config 의 daily_token_budget) — unbounded[/dim]")
        # also persist regenerable reports next to the ledger evidence
        try:
            out = self.repo_root / "runs" / "forgekit" / "usage"
            paths = write_reports(roll, out, top=top_by_tokens(rows, limit=5))
            if paths:
                log.write(f"[dim]↳ report: {paths[0].parent}/ (txt/md/json)[/dim]")
        except Exception:  # noqa: BLE001
            pass
        self._sync_intro()
        self._follow_tail()

    def _show_design(self) -> None:
        """`/design` — restricted design source status + packet (honest blocked, no fake-read)."""

        from ..design import build_reference_packet, register_design_backup

        log = self._transcript
        log.write_echo("/design")
        source = register_design_backup()
        packet = build_reference_packet(source)
        for line in render.design_status_lines(source, packet):
            log.write(line)
        self._sync_intro()
        self._follow_tail()

    def _show_digest(self) -> None:
        """`/digest` — run an autopilot cycle on forgekit + show the operator digest."""

        from ..autopilot import AutopilotOrchestrator, RepoFinding, observe_repo
        from ..autopilot.execution import build_operator_digest

        log = self._transcript
        log.write_echo("/digest")
        findings = observe_repo("forgekit", self.repo_root, ui_discomfort=["UI spacing 마찰"])
        findings += [RepoFinding("forgekit", "auth 대규모 rewrite", kind="gap"),
                     RepoFinding("forgekit", "프로덕션 배포", kind="ops")]
        risk = lambda f: "blocked" if "배포" in f.finding else ("risky" if "rewrite" in f.finding else "safe")
        from ..autopilot import BoundedMutator

        res = AutopilotOrchestrator(mutator=BoundedMutator(self.repo_root)).run_cycle(
            "forgekit", findings, risk_of=risk)
        digest = build_operator_digest([res])
        for line in digest.lines():
            log.write(f"[dim]{line}[/dim]" if line.startswith("-") or line.startswith("주의") else line)
        self._sync_intro()
        self._follow_tail()

    def _run_autopilot(self, raw: str) -> None:
        """`/autopilot <repo>` — one bounded repo-autopilot cycle (internal approval chain).

        Repo must be on the allowlist; safe-class findings execute one-executor-at-a-time
        (internal-approved, no user); risky/restricted are proposed-only."""

        from ..autopilot import AutopilotOrchestrator, RepoFinding
        from ..sources import RepoLocalCollector

        repo = raw.strip()
        if repo.startswith("/autopilot"):
            repo = repo[len("/autopilot"):].strip()
        repo = repo or "forgekit"
        log = self._transcript
        log.write_echo(raw.strip())
        # observe: derive findings from a repo-local scan (offline)
        findings = []
        try:
            for it in RepoLocalCollector(self.repo_root).collect(limit=6):
                kind = "docs" if "TODO" in it.title else "gap"
                findings.append(RepoFinding(repo, it.title, kind=kind))
        except Exception:  # noqa: BLE001
            pass
        findings = findings or [RepoFinding(repo, "docs 보강 필요", kind="docs")]
        # WT3: a real bounded mutator → safe-class findings perform an actual verified
        # write (note under runs/) — NOT a no-op. risky/restricted stay proposed/blocked.
        from ..autopilot import BoundedMutator

        mutator = BoundedMutator(self.repo_root)
        result = AutopilotOrchestrator(mutator=mutator).run_cycle(
            repo, findings, risk_of=lambda f: "safe")
        for line in render.autopilot_lines(result):
            log.write(line)
        self._sync_intro()
        self._follow_tail()

    def _run_red_blue(self, raw: str) -> None:
        """`/red-blue <target>` — build a PLAN-ONLY drill for an allowlisted own asset.

        Never executes: the console only ever builds a dry-run plan (+ defense runbook).
        Non-allowlisted / public / third-party targets are BLOCKED. An active drill needs
        a separate explicit operator approval path (not auto-triggered here)."""

        from ..security import build_drill

        target = raw.strip()
        if target.startswith("/red-blue"):
            target = target[len("/red-blue"):].strip()
        target = target or "k3s-isolated"  # default to the isolated own k3s namespace
        log = self._transcript
        log.write_echo(raw.strip())
        packet = build_drill(target, approved=False)  # console NEVER approves active
        for line in render.security_drill_lines(packet):
            log.write(line)
        self._sync_intro()
        self._follow_tail()

    def _run_self_improve(self) -> None:
        """`/self-improve` — scan repo for gaps → risk-classified packets (no execution)."""

        from ..selfimprove import run_self_improvement

        log = self._transcript
        log.write_echo("/self-improve")
        result = run_self_improvement(self.repo_root, limit=8)
        for line in render.self_improve_lines(result):
            log.write(line)
        self._sync_intro()
        self._follow_tail()

    def _show_sources(self) -> None:
        """`/sources` — the source registry status (live free-first vs planned). No network."""

        from ..sources import default_registry

        log = self._transcript
        log.write_echo("/sources")
        registry = default_registry(self.repo_root)
        for line in render.source_status_lines(registry):
            log.write(line)
        self._sync_intro()
        self._follow_tail()

    def _run_auto(self, raw: str) -> None:
        """`/auto <ask>` — classify → recommend, and safe-switch the mode if allowed.

        Never overrides an explicit operator pin; never auto-switches INTO a gated
        mode (red-blue / approval-wait) — those are recommend-only. Reason is shown.
        """

        from ..policy.auto_mode import auto_switch_safe

        ask = raw.strip()
        if ask.startswith("/auto"):
            ask = ask[len("/auto"):].strip()
        log = self._transcript
        log.write_echo(raw.strip())
        decision = auto_switch_safe(ask, current_mode=self._runtime_mode,
                                    operator_pinned=self._mode_pinned)
        for line in render.auto_decision_lines(decision):
            log.write(line)
        if decision.switched:
            self._runtime_mode = decision.recommended_mode  # auto switch (NOT a pin)
            self._recompute_policy()
            self._refresh_issue()
            self._refresh_chrome()
        self._sync_intro()
        self._follow_tail()

    def _run_always_on_cycle(self) -> None:
        """Run ONE bounded always-on cycle (observe→classify→packet→handoff→wait).

        Bounded autonomy: privileged areas (deploy/IAM/infra/secret) become runbooks
        + an operator-wait, never an execution. Repeated waits surface to escalation.
        (A live project scanner is not wired yet, so this runs on a representative
        finding set — labelled honestly — to exercise the real loop + runbook path.)
        """

        from ..runtime.loop import BoundedRuntimeLoop, Finding, CAT_DESIGN, CAT_INFRA, AUTONOMY_BOUNDED

        log = self._transcript
        log.write_echo("/always-on")
        findings = [
            Finding("bkurs-fe", "디자인/간격(spacing) 보강 필요 — UX 미완성", category=CAT_DESIGN),
            Finding("bkurs-be", "운영/인프라 배포 준비 부족 (deploy apply 권한 필요)",
                    category=CAT_INFRA, privileged=True),
        ]
        loop = BoundedRuntimeLoop(autonomy=AUTONOMY_BOUNDED, max_iterations=10,
                                  escalator=self._escalator)
        result = loop.run(findings)
        for line in render.loop_summary_lines(
            result, note="대표 finding 셋 기반 bounded 데모 사이클 — 실제 프로젝트 스캐너 연결은 후속.",
        ):
            log.write(line)
        if result.escalated:
            log.write("[dim]↳ operator inbox/ledger 에 대기 상태 기록됨[/dim]")
        # WT4: a bounded loop parked on a privileged area → notify the operator
        # (inbox always; desktop when FORGEKIT_NOTIFY is on). Action-oriented payload.
        if result.waiting:
            from ..notify.events import EVENT_ACCESS_REQUIRED, NotificationEvent

            out = self._notifier.notify(NotificationEvent(
                EVENT_ACCESS_REQUIRED,
                title="forgekit always-on: operator 승인 필요",
                why=f"권한 없는 영역 {result.blocked_count}개에서 멈춤 (runbook 생성됨)",
                action="runbook 확인 후 `#승인-대기` 에서 승인/거부",
                options=("승인", "거부", "보류"), source="always-on",
            ))
            log.write(
                f"[dim]↳ operator 알림: inbox={'기록' if out.inbox_written else '실패'} · "
                f"desktop={out.channel if out.desktop_delivered else 'off(FORGEKIT_NOTIFY)'}[/dim]"
            )
        self._sync_intro()
        self._follow_tail()

    def _run_pm_intake(self, raw: str) -> None:
        """PM (product-agent) mode: a raw product ask → structured handoff packet.

        Reuses the product-intake engine to find missing requirements, splits the
        packet across roles (tech-lead), writes evidence JSON, and surfaces blocked
        (no-permission) areas honestly. No raw live submit here.
        """

        text = raw.strip()
        if not text:
            return
        self._transcript.write_echo(text)
        from ..handoff import run_handoff
        from ..handoff.evidence import write_handoff_evidence

        handoff = run_handoff(text, project="")
        for line in render.handoff_summary_lines(handoff):
            self._transcript.write(line)
        path = write_handoff_evidence(handoff, self.repo_root)
        if path is not None:
            self._transcript.write(f"[dim]↳ evidence: {path}[/dim]")
        # WT5: also write an AUTHORED vault note (who/role/handoff phase metadata).
        note_path = self._write_handoff_note(handoff)
        if note_path is not None:
            self._transcript.write(f"[dim]↳ vault note (tech-lead, authored): {note_path}[/dim]")
        self._sync_intro()
        self._follow_tail()

    def _write_handoff_note(self, handoff):
        """Best-effort: persist an authored vault note for the handoff (WT5)."""

        try:
            from datetime import date
            from ..vault.note import note_from_handoff, write_note

            content = note_from_handoff(handoff, created_at=date.today().isoformat())
            return write_note(content, self.repo_root, "runs/forgekit/vault/handoff-note.md")
        except Exception:  # noqa: BLE001 - vault write is best-effort, never fatal
            return None

    def _submit_blocking(self, text: str, ctx=None) -> None:
        # The context carries the EffectivePolicy → the service re-enforces the gate
        # (routing target / approval / budget) + records usage. Real teeth, not display.
        result = self._submit_service.submit(text, context=ctx)
        self.call_from_thread(self._on_submit_result, result)

    def _on_submit_result(self, result) -> None:
        log = self._transcript
        for line in result.to_lines():
            log.write(line)
        # WT2: record the usage event (live or estimate basis) to the ledger.
        self._record_usage(result)
        # held/throttled are intentional POLICY decisions, not failures → no escalation.
        if not result.ok and not result.held:
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
        self._mode_pinned = True  # explicit operator action → auto won't override
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
