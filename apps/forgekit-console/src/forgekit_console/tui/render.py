"""Pure render helpers for the TUI — return plain strings/markup, no textual.

Keeping these as pure string builders means the welcome banner, agent-pane
lines, and status-pane lines are unit-testable without a terminal, and the
textual widgets in :mod:`app` just feed these strings in.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from ..models import MODE_OPERATOR, AgentInfo, HelpSection, StatusSummary
from . import theme

BRAND = "forgekit"
TAGLINE = "operator console"

# Brand markup tokens (cyan/magenta accents on black) — see tui.theme.
_ACCENT = theme.ACCENT_PRIMARY
_ACCENT2 = theme.ACCENT_SECONDARY
_MUTED = theme.MUTED
_WARN = theme.WARNING
_OK = theme.SUCCESS
_ERR = theme.ERROR

# Rich console-markup colours per alert level (brand-tuned).
_LEVEL_STYLE = {"info": "dim", "warn": _WARN, "error": f"bold {_ERR}"}


def welcome_banner(repo_root: str, profile: str) -> Tuple[str, ...]:
    """The first content block — one quiet line. Detail lives in /help and /status."""

    return (
        "[dim]`/help` 로 시작 · `/` 명령 팔레트 · `/status` 운영 요약[/dim]",
        "",
    )


def intro_meta_lines(
    *,
    repo: str,
    version: str,
    profile: str = "operator",
    provider: str = "—",
) -> Tuple[str, ...]:
    """The right-hand meta column of the intro (beside the avatar).

    Claude-style: a SHORT product header — brand+version, then provider·profile,
    then the repo path. Three quiet lines (the redundant "operator console" tagline
    line is dropped so the intro reads fast and the issue line sits closer). Pure so
    it's unit-testable without a terminal.
    """

    return (
        f"{theme.wordmark(BRAND)} [dim]v{version}[/dim]",
        f"[dim]provider[/dim] {provider}  [dim]·[/dim]  [dim]profile[/dim] {profile}",
        f"[dim]{repo}[/dim]",
    )


def renderer_debug_line(diag) -> str:
    """A small dim diagnostic line: the REAL backend + POLICY state per surface.

    Only shown when ``FORGEKIT_DEBUG_RENDERERS`` is set. Each side shows the
    realized backend with its policy state — ``true-raster`` (a real pixel image,
    TGP/Sixel), ``managed-fallback`` (a deliberate clean stand-in: brand badge /
    wordmark / opt-in portrait), or ``hard-fallback`` (degraded to bare text). So
    the operator never mistakes a managed fallback for a real raster. ``cap`` is the
    capability guess; ``lib`` separates "textual-image importable" (and which
    backend it WOULD use) from the realized result. Pure: takes a
    :class:`tui.image_renderer.RendererDiagnostics`-shaped object, returns markup.
    """

    def side(label: str, backend: str, policy: str) -> str:
        return f"{label}={backend} ({policy})"

    if diag.lib_ok:
        lib = f"lib=ok:{diag.lib_backend}"  # importable + which backend it WOULD use
    else:
        reason = diag.lib_reason
        if len(reason) > 48:
            reason = reason[:47] + "…"
        lib = f"lib=✗ {reason}"

    parts = [
        side("avatar", diag.avatar_backend, diag.avatar_policy),
        side("brand", diag.brand_backend, diag.brand_policy),
        f"ansi={getattr(diag, 'ansi_status', '?')}/{getattr(diag, 'ansi_theme', '?')}",
        f"cap={diag.capability_reason}",
        lib,
    ]
    return f"[dim]renderers · {' · '.join(parts)}[/dim]"


def blocked_banner() -> str:
    """The issue-line banner shown when a repeated failure crossed the threshold."""

    return (
        f"[{_ERR}]● blocked[/{_ERR}] [dim]반복 실패가 에스컬레이션됨 — `/blocked` 로 "
        f"원인·대안·다음 단계 확인[/dim]"
    )


def setup_required_banner() -> str:
    """Issue-line banner when no provider is configured (forgekit can't run yet)."""

    return (
        f"[{_WARN}]● setup-required[/{_WARN}] [dim]main provider 미설정 — "
        f"`/doctor` 로 점검, config 의 `main_provider` 설정 (또는 로컬 ollama)[/dim]"
    )


def submit_held_line(mode_label: str, action: str) -> str:
    """Shown when the runtime mode HOLDS an action (e.g. approval-wait) — the submit
    is not sent live; the operator is told why + what to do."""

    return (
        f"[{_WARN}]⏸ {mode_label} — 행동 보류(hold)[/{_WARN}] "
        f"[dim]{action}[/dim]"
    )


def runtime_mode_line(
    label: str, policy_mode: str, usage_mode: str, approval: str, *, loop: bool
) -> str:
    """A compact one-line summary of the current runtime mode + its real posture.

    Shown on the operator surface (issue line / mode change) so Shift+Tab is never
    "just a label" — the resolved routing / usage / approval / loop are visible.
    """

    loop_s = "loop on" if loop else "loop off"
    return (
        f"[{_ACCENT}]◆[/{_ACCENT}] [b]{label}[/b] "
        f"[dim]· routing {policy_mode} · usage {usage_mode} · approval {approval} · {loop_s}[/dim]"
    )


def issue_line(summary: StatusSummary) -> str:
    """The compact setup/status line under the intro — text-first, one line.

    Quiet by default ("ready") so the input flow leads; surfaces a count +
    pointer when there's something to look at, Claude-style.
    """

    if not summary.available:
        return f"[{_WARN}]status unavailable[/{_WARN}] [dim]· /doctor[/dim]"
    alerts = [a for a in summary.alerts if a.level in ("warn", "error")]
    if not alerts:
        return "[dim]ready · /status[/dim]"
    labels = ", ".join(a.message.split(" ")[0] for a in alerts[:3])
    word = "issue" if len(alerts) == 1 else "issues"
    return f"[{_WARN}]{len(alerts)} {word}[/{_WARN}]: {labels} [dim]· /doctor[/dim]"


def status_pill(summary: StatusSummary) -> str:
    """A single compact operator line — secondary, not the star of the screen."""

    if not summary.available:
        return f"[{_WARN}]●[/{_WARN}] [dim]status unavailable[/dim]"
    if any(a.level == "error" for a in summary.alerts):
        dot = f"[{_ERR}]●[/{_ERR}]"
    elif any(a.level == "warn" for a in summary.alerts):
        dot = f"[{_WARN}]●[/{_WARN}]"
    else:
        dot = f"[{_OK}]●[/{_OK}]"
    parts = []
    for section in summary.sections[:3]:
        first = section.lines[0] if section.lines else ""
        if len(first) > 30:
            first = first[:29] + "…"
        parts.append(f"[dim]{section.title}[/dim] {first}")
    body = "   ·   ".join(parts) if parts else "ready"
    return f"{dot} {body}"


def mode_pill(mode: str, agents: Sequence[AgentInfo] = ()) -> str:
    """A restrained mode indicator for the input row (replaces the heavy badge)."""

    if mode in (MODE_OPERATOR, "") or not mode:
        return f"[{_ACCENT}]●[/{_ACCENT}] [dim]operator[/dim]"
    if mode == "palette":
        return f"[{_ACCENT2}]●[/{_ACCENT2}] [dim]palette[/dim]"
    if mode.startswith("agent:"):
        agent_id = mode.split(":", 1)[1]
        label = agent_id
        for a in agents:
            if a.agent_id == agent_id:
                label = a.label
                break
        return f"[{_ACCENT2}]●[/{_ACCENT2}] [b]{label}[/b]"
    return f"[dim]●[/dim] {mode}"


def hint_line(
    *,
    palette_open: bool = False,
    help_open: bool = False,
    in_agent: bool = False,
    typing: bool = False,
) -> str:
    """The secondary mode/shortcut line shown BELOW the input bar (Claude-style).

    Claude shows a quiet mode line under the input (``▶▶ auto mode on …``) only at
    idle. So: while TYPING (non-empty input) this line is empty — the input is the
    star and clutter drops. When the palette is open the palette owns the space
    (also empty here). At idle it shows the mode + key shortcuts with a small accent
    ``▶▶`` marker, like Claude's bottom mode line.
    """

    if typing or palette_open:
        return ""  # reduce clutter while typing / let the palette own the space
    marker = f"[{_ACCENT}]▶▶[/{_ACCENT}]"
    if help_open:
        return f"{marker} [dim]Tab 탭 전환 · Esc 로 닫기[/dim]"
    if in_agent:
        return f"{marker} [dim]Esc 로 operator · /help · ^C quit[/dim]"
    return f"{marker} [dim]operator · /help · / palette · ^C quit[/dim]"


def default_help_tab(sections: Sequence[HelpSection]) -> int:
    """Index of the default-open help tab (``General``), else 0."""

    for i, s in enumerate(sections):
        if s.title == "General":
            return i
    return 0


def help_panel_document(sections: Sequence[HelpSection], active: int) -> Tuple[str, ...]:
    """The help VIEW document for the active tab — Claude-Code style, scannable.

    This is NOT appended into the transcript. It is the full body the dedicated
    help panel (:class:`tui.help_panel.HelpPanel`) renders when the main area is
    switched to the help view. The panel re-renders this *in place* on Tab (the
    active tab changes) — nothing ever accumulates. A top tab strip marks the
    active tab; only that tab's content is shown so the view stays scannable.
    Esc switches the main area back to the transcript; the composer stays docked
    at the bottom throughout.
    """

    # Text fallback (non-TUI / tests): tab strip then body. The full-width blue
    # divider between them is drawn by the HelpPanel widget's CSS (border-bottom),
    # not by a fixed-length string, so it spans the real terminal width.
    if not sections:
        return ("[dim]no help[/dim]",)
    return (help_tab_strip(sections, active), *help_body(sections, active))


def help_tab_strip(sections: Sequence[HelpSection], active: int) -> str:
    """The help TAB ROW — read first, no 'forgekit help' branding (Claude-style).

    The active tab is bold brand-cyan (a POINT use of accent), inactive tabs are
    muted. ``Help`` is the first tab, so the strip reads ``Help  General  …`` as
    the top hierarchy of the help screen.
    """

    if not sections:
        return "[dim]no help[/dim]"
    active = max(0, min(active, len(sections) - 1))
    chips = []
    for i, s in enumerate(sections):
        chips.append(
            f"[b {_ACCENT}]{s.title}[/b {_ACCENT}]"
            if i == active
            else f"[{_MUTED}]{s.title}[/{_MUTED}]"
        )
    return "   ".join(chips)


def help_body(sections: Sequence[HelpSection], active: int) -> Tuple[str, ...]:
    """The active help tab's body — the Esc/Tab hint then the tab's content lines."""

    if not sections:
        return ("[dim]no help[/dim]",)
    active = max(0, min(active, len(sections) - 1))
    return (
        "[dim]Tab 탭 전환 · Esc 로 닫기[/dim]",
        "",
        *sections[active].lines,
    )


def agent_pane_lines(agents: Sequence[AgentInfo]) -> Tuple[str, ...]:
    """Left-pane agent quick list."""

    lines = ["[b]agents[/b]", ""]
    for agent in agents:
        marker = "●" if agent.enter_command else "○"
        lines.append(f"{marker} {agent.label}")
        lines.append(f"   [dim]{agent.status}[/dim]")
    return tuple(lines)


def status_pane_lines(summary: StatusSummary) -> Tuple[str, ...]:
    """Right-pane condensed status + alerts + next actions."""

    lines = [f"[b]{summary.title}[/b]", ""]
    if not summary.available:
        lines.append(f"[yellow]unavailable[/yellow]: {summary.error}")
    for section in summary.sections:
        lines.append(f"[b]{section.title}[/b]")
        lines.extend(f"  {line}" for line in section.lines)
        lines.append("")
    if summary.alerts:
        lines.append("[b]alerts[/b]")
        for alert in summary.alerts:
            style = _LEVEL_STYLE.get(alert.level, "dim")
            lines.append(f"  [{style}]{alert.level}[/{style}] {alert.message}")
        lines.append("")
    if summary.next_actions:
        lines.append("[b]what to do next[/b]")
        for action in summary.next_actions:
            lines.append(f"  - {action}")
    return tuple(lines)


def palette_lines(commands: Sequence) -> Tuple[str, ...]:
    """Slash palette candidates (name + summary)."""

    if not commands:
        return ()
    return tuple(f"/{c.name} — {c.summary}" for c in commands)


def palette_panel_lines(commands: Sequence, selected: int = -1) -> Tuple[str, ...]:
    """Palette body — a FLAT 2-column list (command · summary), Claude-style.

    No left rule / side bar and no reverse-cyan block: the selected row is just the
    command in bold accent (others bold foreground), separated by whitespace +
    alignment. Keeps the list clean and easy to scan.
    """

    if not commands:
        return ("[dim]일치하는 명령이 없습니다[/dim]",)
    out = []
    for i, c in enumerate(commands):
        name = f"/{c.name}"
        if i == selected:
            out.append(f"  [b {_ACCENT}]{name:<16}[/b {_ACCENT}] [dim]{c.summary}[/dim]")
        else:
            out.append(f"  [b]{name:<16}[/b] [dim]{c.summary}[/dim]")
    return tuple(out)


def mode_badge(mode: str, agents: Sequence[AgentInfo] = ()) -> str:
    """A coloured badge for the current console mode (footer / input chrome)."""

    if mode == MODE_OPERATOR or not mode:
        return "[reverse] OPERATOR [/reverse]"
    if mode.startswith("agent:"):
        agent_id = mode.split(":", 1)[1]
        label = agent_id
        for a in agents:
            if a.agent_id == agent_id:
                label = a.label
                break
        return f"[reverse {_ACCENT2}] AGENT · {label} [/reverse {_ACCENT2}]"
    if mode == "palette":
        return f"[reverse {_ACCENT2}] PALETTE [/reverse {_ACCENT2}]"
    return f"[reverse] {mode.upper()} [/reverse]"


def help_sections(commands: Sequence, agents: Sequence[AgentInfo]) -> Tuple[HelpSection, ...]:
    """Build the help tabs — short, scannable. Order: Help · General · Commands · Agents.

    ``General`` is the default-open tab (see :func:`default_help_tab`).
    """

    help_tab = HelpSection("Help", (
        "[b]forgekit help[/b] — 이 화면 사용법.",
        "",
        "  Tab        탭 전환 (Help · General · Commands · Agents)",
        "  Esc        transcript 로 돌아가기",
        "  F1         help 토글",
        "",
        "탭은 제자리에서 바뀝니다 — 본문에 쌓이지 않습니다.",
    ))
    general = HelpSection("General", (
        "[b]forgekit[/b] — provider-agnostic 운영자 콘솔.",
        "슬래시 명령으로 운영 표면을 보거나 에이전트 모드로 들어갑니다.",
        "",
        "[b]단축키[/b]",
        "  /          명령 팔레트 열기",
        "  Tab        자동완성 · 다음 후보",
        "  Enter      실행",
        "  ↑ / ↓      후보 순환",
        "  Esc        help 닫기 · palette 닫기 · operator 복귀",
        "  F1         help",
        "  ^L         clear     ^R  refresh     ^C  quit",
        "",
        "[dim]일반 텍스트는 provider 로 live-submit 됩니다 (provider 없으면 setup 안내).[/dim]",
    ))
    cmd_lines = ["[b]commands[/b]  — `/` 로 시작하면 자동완성됩니다.", ""]
    for c in commands:
        cmd_lines.append(f"  [b]/{c.name:<14}[/b] [dim]{c.summary}[/dim]")
    commands_tab = HelpSection("Commands", tuple(cmd_lines))
    enter_examples = " · ".join(a.enter_command for a in agents if a.enter_command) or "(없음)"
    agents_tab = HelpSection("Agents", (
        "[b]operator[/b]  기본 모드 — 콘솔 명령으로 운영 표면을 봅니다.",
        "[b]agent[/b]     에이전트 모드(stub) — 진입하면 상단 pill 에 표시됩니다.",
        "",
        "진입 예: " + enter_examples,
        "Esc 로 operator 모드로 돌아옵니다.",
        "",
        "전체 레지스트리는 `/agents` 로 봅니다.",
    ))
    about = HelpSection("About", (
        f"{theme.wordmark('forgekit')} — provider-agnostic 운영자 콘솔.",
        "",
        "위 와이드 hero 아트는 첫 진입(빈 세션)과 이 /about 화면에서 보이고,",
        "작업을 시작하면 상단은 작은 compact 헤더로 접힙니다 (Claude 스타일).",
        "",
        "[b]intro 모드[/b]",
        "  hero      큰 아트 (첫인상 · /about · /welcome)",
        "  compact   작은 헤더 (typing · palette · transcript 있음)",
        "  override  FORGEKIT_INTRO_MODE=hero|compact|auto · FORGEKIT_HERO_ART=on|off|auto",
        "",
        "[dim]Esc 로 닫고 작업을 계속하면 헤더가 compact 로 접힙니다.[/dim]",
    ))
    return (help_tab, general, commands_tab, agents_tab, about)


def source_status_lines(registry) -> Tuple[str, ...]:
    """Render the source registry — LIVE (free-first) vs PLANNED (no fake live)."""

    lines = [f"[b {_ACCENT}]» source registry[/b {_ACCENT}]", "  [b]live (no-cost first)[/b]"]
    for c in registry.cost_ordered_live():
        s = c.spec
        lines.append(f"    [{_OK}]●[/{_OK}] {s.id:<12} [dim]{s.cost_class}·{s.ingest_method}·{s.trust_level}[/dim]")
    lines.append(f"  [b]planned (미연결 — fake-live 아님)[/b]")
    for c in registry.planned():
        s = c.spec
        lines.append(f"    [{_WARN}]○[/{_WARN}] {s.id:<12} [dim]{s.status} — {s.legal_note}[/dim]")
    return tuple(lines)


def design_status_lines(source, packet) -> Tuple[str, ...]:
    """Render the restricted design source status — access state, roles, packet."""

    state_color = {"ok": _OK, "blocked": _ERR, "missing": _WARN}.get(source.access_state, _MUTED)
    lines = [
        f"[b {_ACCENT}]» design source (restricted)[/b {_ACCENT}]",
        f"  source : {source.source_id} [dim]({source.source_type})[/dim]",
        f"  access : [{state_color}]{source.access_state}[/{state_color}]"
        + (" [dim]— design_source_blocked: macOS TCC, fake-read 없음[/dim]"
           if source.access_state == "blocked" else ""),
        f"  roles  : {', '.join(source.allowed_roles)} [dim](그 외는 projection)[/dim]",
        f"  packet : access_state={packet.access_state} · publishable={packet.publishable}",
    ]
    if source.access_state != "ok":
        lines.append("  [dim]raw 미접근 → packet 은 honest scaffold. operator 가 Full Disk Access 부여 또는 export 제공.[/dim]")
    return tuple(lines)


def autopilot_lines(result) -> Tuple[str, ...]:
    """Render a repo-autopilot cycle — allowlist/halt, executed (1 executor), proposed."""

    if result.blocked_repo:
        return (
            f"[b {_ACCENT}]» repo-autopilot[/b {_ACCENT}]",
            f"  [{_ERR}]repo 거부[/{_ERR}] [dim]{result.halt_reason}[/dim]",
        )
    if result.halted:
        return (
            f"[b {_ACCENT}]» repo-autopilot — {result.repo}[/b {_ACCENT}]",
            f"  [{_WARN}]halted[/{_WARN}] [dim]{result.halt_reason}[/dim]",
        )
    lines = [
        f"[b {_ACCENT}]» repo-autopilot — {result.repo}[/b {_ACCENT}]",
        f"  실행(safe, 내부승인): {len(result.executed)} · 제안(user/operator 필요): {len(result.proposed)}",
        "  [b]executed (한 번에 한 executor)[/b]",
    ]
    for e in result.executed[:5]:
        lines.append(f"    [{_OK}]●[/{_OK}] {e['executor']:<10} {e['finding'][:44]} [dim](verified)[/dim]")
    for p in result.proposed[:4]:
        cls = p.get("decision_class") or p.get("queued_for", "queued")
        lines.append(f"    [{_WARN}]⏸[/{_WARN}] {p['finding'][:44]} [dim]→ {cls}[/dim]")
    lines.append(f"  [dim]executor log: {' '.join(result.executor_log) or '—'}[/dim]")
    lines.append("  [dim]safe-class만 내부승인(PM→gateway→tech-lead)으로 실행 · risky→user · restricted→runbook[/dim]")
    return tuple(lines)


def security_drill_lines(packet) -> Tuple[str, ...]:
    """Render a red/blue drill packet — plan-only / blocked, never auto-executed."""

    t = packet.target
    if packet.status == "blocked":
        return (
            f"[b {_ACCENT}]» red/blue drill[/b {_ACCENT}]",
            f"  [{_ERR}]blocked[/{_ERR}] [dim]{packet.refusal_reason}[/dim]",
            f"  [dim]대상 '{t.id}' 은 allowlist 의 격리된 내 자산이 아님 — 공용/3rd-party 금지[/dim]",
        )
    lines = [
        f"[b {_ACCENT}]» red/blue drill — {t.id} ({t.kind})[/b {_ACCENT}]",
        f"  status: [{_WARN}]{packet.status}[/{_WARN}] · dry_run={packet.attack_plan.dry_run} · "
        f"approval 필요={packet.requires_approval}",
        "  [b]red 계획(plan-only, 읽기 점검)[/b]",
    ]
    for h in packet.attack_plan.hypotheses:
        lines.append(f"    · {h}")
    lines.append("  [b]blue 방어 runbook[/b]")
    for h in packet.defense_runbook.hardening[:3]:
        lines.append(f"    · {h}")
    lines.append(f"  [dim]active 드릴은 operator 승인 후에만 — 지금은 실행되지 않음. 내 자산만.[/dim]")
    return tuple(lines)


def self_improve_lines(result) -> Tuple[str, ...]:
    """Render a self-improvement scan — packets by risk class (no execution)."""

    lines = [
        f"[b {_ACCENT}]» self-improvement (bounded)[/b {_ACCENT}]",
        f"  packets: {len(result.packets)} · safe {len(result.safe)} · "
        f"risky {len(result.risky)} · blocked {len(result.blocked)}",
    ]
    for p in result.packets[:6]:
        tag = {"safe": _OK, "risky": _WARN, "blocked": _ERR}.get(p.risk, _MUTED)
        lines.append(f"    [{tag}]{p.risk:<7}[/{tag}] {p.finding[:54]}")
        lines.append(f"        [dim]불편: {p.user_discomfort} · owner {p.recommended_owner}[/dim]")
    lines.append("  [dim]safe 만 승인 체계 내 자동 가능 · risky→approval-wait · blocked→runbook (자동 실행 없음)[/dim]")
    return tuple(lines)


def discovery_lines(result) -> Tuple[str, ...]:
    """Render an idea-discovery result — bundle + gap map + top idea briefs."""

    gm = result.gap_map
    lines = [
        f"[b {_ACCENT}]» idea-discovery[/b {_ACCENT}]",
        f"  참고 신호: {len(result.reference_bundle.items)}개  ·  경쟁: {len(gm.competitors)}  ·  gap: {len(gm.gaps)}",
    ]
    if result.self_improve_signals:
        lines.append(f"  [{_WARN}]forgekit 자체 개선 신호 {len(result.self_improve_signals)}개[/{_WARN}] [dim]→ self-improvement 로 분기 가능[/dim]")
    lines.append("  [b]아이디어 브리프[/b]")
    for b in result.idea_briefs[:3]:
        lines.append(f"    [{_OK}]●[/{_OK}] {b.title}  [dim](score {b.score})[/dim]")
        lines.append(f"        차별화: {b.differentiation.hypothesis}")
        lines.append(f"        실험: {b.next_experiment.experiment}")
    if result.idea_briefs:
        lines.append("  [dim]상위 브리프는 `/pm-agent` 핸드오프로 승격 가능[/dim]")
    return tuple(lines)


def video_watch_lines(result) -> Tuple[str, ...]:
    """Render a video-watch ingest result — live summary or honest reference_only."""

    if result.status == "reference_only":
        return (
            f"[b {_ACCENT}]» video-watch[/b {_ACCENT}]",
            f"  [{_WARN}]reference_only[/{_WARN}] [dim]{result.note}[/dim]",
            f"  [dim]ref: {result.reference.get('link','')}[/dim]",
        )
    lines = [
        f"[b {_ACCENT}]» video-watch (저비용 ingest)[/b {_ACCENT}]",
        f"  요약: {result.summary}",
        f"  [dim]{result.note}[/dim]",
    ]
    for b in result.ideas[:3]:
        lines.append(f"    [{_OK}]●[/{_OK}] {b.title} [dim](score {b.score})[/dim]")
    return tuple(lines)


def auto_decision_lines(decision) -> Tuple[str, ...]:
    """Render an auto orchestration decision (recommend / switch-safe / escalate)."""

    lines = [
        f"[b {_ACCENT}]» auto orchestration[/b {_ACCENT}]",
        f"  분류: [b]{decision.recommended_mode}[/b]  ·  {decision.decision}",
        f"  이유: {decision.reason}",
    ]
    if decision.switched:
        lines.append(f"  [{_OK}]→ 모드 전환됨[/{_OK}]")
    if decision.requires_operator:
        lines.append(f"  [{_WARN}]⏸ gated 모드 — 자동 전환 안 함, operator 승인 필요[/{_WARN}]")
    return tuple(lines)


def loop_summary_lines(result, *, note: str = "") -> Tuple[str, ...]:
    """Render a bounded always-on LoopResult — phases, handoffs, runbooks, wait state.

    Makes the bounded autonomy visible: observe→classify→packet→handoff→wait, with
    privileged areas turned into runbooks (never executed). Pure (duck-typed)."""

    lines = [
        f"[b {_ACCENT}]» always-on (bounded) — 관측→분류→패킷→handoff→대기[/b {_ACCENT}]",
    ]
    if note:
        lines.append(f"  [dim]{note}[/dim]")
    # compact phase trace grouped by iteration
    by_iter: dict = {}
    for s in result.steps:
        by_iter.setdefault(s.iteration, []).append(s.phase)
    for it, phases in by_iter.items():
        lines.append(f"  [{it}] " + " → ".join(phases))
    lines.append("")
    lines.append(f"  packet/handoff : {len(result.handoffs)}개")
    if result.runbooks:
        lines.append(f"  [{_WARN}]runbook(권한 없음): {len(result.runbooks)}개[/{_WARN}]")
        for n in result.runbooks:
            lines.append(f"    ⏸ {n.title}  [dim]→ {n.area} runbook (operator 승인 필요)[/dim]")
    if result.waiting:
        lines.append(f"  [{_WARN}]상태: operator 응답/승인 대기[/{_WARN}] [dim]— 실행은 사람 승인 후[/dim]")
    lines.append(f"  [dim]정지: {result.halt_reason} · destructive/deploy 는 구조적으로 차단(execute phase 없음)[/dim]")
    return tuple(lines)


def handoff_summary_lines(handoff) -> Tuple[str, ...]:
    """Render a PM→gateway→tech-lead Handoff for the transcript (duck-typed).

    Shows the shaped goal, how many implied features the PM added, the per-role task
    split (ready vs blocked), and — honestly — the blocked areas needing an operator
    + a runbook. Pure: reads attributes, returns markup lines.
    """

    packet = handoff.packet
    goal = getattr(packet, "user_goal", "") or "(목표 미파악)"
    implied = getattr(packet, "implied_features", ()) or ()
    questions = getattr(packet, "decision_questions", ()) or ()
    split = handoff.split
    lines = [
        f"[b {_ACCENT}]» PM intake → tech-lead handoff[/b {_ACCENT}]",
        f"  goal       : {goal}",
        f"  보강(implied): {len(implied)}개 자동 발견  ·  결정질문: {len(questions)}개",
        "",
        "  [b]role split[/b] (tech-lead):",
    ]
    for t in split.tasks:
        if t.state == "blocked":
            lines.append(
                f"    [{_WARN}]⏸ {t.role_label:<10}[/{_WARN}] {t.title} "
                f"[dim]— BLOCKED: {t.blocked_reason}[/dim]"
            )
        else:
            lines.append(f"    [{_OK}]●[/{_OK}] {t.role_label:<10} {t.title}")
    if handoff.has_blocked:
        lines.append("")
        lines.append(
            f"  [{_WARN}]권한 없는 영역 {len(split.blocked)}개[/{_WARN}] "
            f"[dim]— operator 승인 + Terraform/ops runbook 필요 (가짜 실행 없음).[/dim]"
        )
    lines.append("")
    lines.append("  [dim]trace: " + " → ".join(
        f"{t.author_role}" for t in handoff.trace
    ) + "[/dim]")
    return tuple(lines)


def result_block(title: str, lines: Sequence[str]) -> Tuple[str, ...]:
    """Frame a command result for the center log."""

    header = f"[b {_ACCENT}]» {title}[/b {_ACCENT}]" if title else f"[b {_ACCENT}]»[/b {_ACCENT}]"
    return (header, *lines, "")


def chunk_result_lines(lines: Sequence[str], max_lines: int = 3) -> Tuple[Tuple[str, ...], ...]:
    """Group response lines into small reveal chunks for progressive rendering.

    A chunk ends at a blank line (paragraph boundary) or after ``max_lines`` lines,
    whichever comes first — so the body is revealed paragraph-by-paragraph (long
    paragraphs sub-chunked), NOT one giant frame. Pure: drives the timer reveal in
    the app but is unit-testable on its own.
    """

    chunks = []
    cur = []
    for ln in lines:
        cur.append(ln)
        if ln.strip() == "" or len(cur) >= max_lines:
            chunks.append(tuple(cur))
            cur = []
    if cur:
        chunks.append(tuple(cur))
    return tuple(chunks)


__all__ = (
    "BRAND", "TAGLINE",
    "welcome_banner", "intro_meta_lines", "renderer_debug_line", "blocked_banner",
    "setup_required_banner", "runtime_mode_line", "submit_held_line",
    "issue_line", "agent_pane_lines",
    "status_pane_lines",
    "palette_lines", "palette_panel_lines", "mode_badge", "mode_pill",
    "status_pill", "hint_line", "help_sections",
    "help_panel_document", "help_tab_strip", "help_body", "default_help_tab",
    "handoff_summary_lines", "loop_summary_lines", "auto_decision_lines", "source_status_lines", "discovery_lines", "video_watch_lines", "self_improve_lines", "security_drill_lines", "autopilot_lines", "design_status_lines", "result_block", "chunk_result_lines",
)
