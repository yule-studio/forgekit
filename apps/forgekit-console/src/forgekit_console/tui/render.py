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
        f"[{_WARN}]● setup-required[/{_WARN}] [dim]primary provider 미설정 — "
        f"`/provider set <id>` 또는 `/provider preset <…>-brain` (자동 ollama 안 함)[/dim]"
    )


def no_live_banner(primary: str = "") -> str:
    """Issue-line banner when a primary IS set but no live-submit-capable provider exists.

    Honest: the brain is CONFIGURED (not setup-required) — but claude/codex are
    unsupported_in_console, so there is no console live path until a live provider is
    linked. NOT "설정 안 됨"."""

    who = f" (primary={primary})" if primary else ""
    return (
        f"[{_WARN}]● configured · no live-submit[/{_WARN}] [dim]primary 설정됨{who} — "
        f"live 경로(gemini/ollama) 미연결. `/provider link gemini|ollama`[/dim]"
    )


def setup_explanation_lines() -> Tuple[str, ...]:
    """The copyable operator-facing setup/provider explanation (transcript + /copy).

    Distinct from :func:`setup_required_banner` (the short status PILL on the issue line):
    this is the readable BLOCK that lands in the transcript / plain-text store, so the
    operator can `/copy` the guidance — not a UI-only decoration."""

    return (
        "[b]setup-required[/b] — primary provider 가 아직 설정되지 않았습니다.",
        "ForgeKit 은 operator 가 정한 primary provider 로 동작합니다 — 자동으로 ollama 를 쓰지 않습니다.",
        "다음 단계:",
        "  • `/provider set <id>`  (claude · codex · gemini · ollama 중 하나)",
        "  • `/doctor` 로 환경 점검 · `/provider list` 로 후보 확인",
        "  • 로컬 ollama 로 시작하려면: `/provider set ollama`",
    )


def submit_held_line(mode_label: str, action: str) -> str:
    """Shown when the runtime mode HOLDS an action (e.g. approval-wait) — the submit
    is not sent live; the operator is told why + what to do."""

    return (
        f"[{_WARN}]⏸ {mode_label} — 행동 보류(hold)[/{_WARN}] "
        f"[dim]{action}[/dim]"
    )


def runtime_mode_line(
    label: str, policy_mode: str, usage_mode: str, approval: str, *, loop: bool,
    awaiting: int = 0, budget_ratio: Optional[float] = None,
) -> str:
    """A compact one-line summary of the current runtime mode + its real posture.

    Shown on the operator surface (issue line / mode change) so Shift+Tab is never
    "just a label" — the resolved routing / usage / approval / loop are visible.

    Operator-cockpit badges (GW5) extend the line with the two control-plane facts an
    operator otherwise had to POLL for: ``awaiting`` (goals parked in awaiting_approval —
    a real count from the goal store) and ``budget_ratio`` (today's token spend ÷ budget,
    real from the usage ledger). Both default OFF so existing callers/tests are unchanged;
    the awaiting badge is warn-coloured + carries the action pointer so it can't be missed,
    and the budget badge turns warn at ≥90%. Numbers only — never a fabricated posture.
    """

    loop_s = "loop on" if loop else "loop off"
    line = (
        f"[{_ACCENT}]◆[/{_ACCENT}] [b]{label}[/b] "
        f"[dim]· routing {policy_mode} · usage {usage_mode} · approval {approval} · {loop_s}[/dim]"
    )
    if budget_ratio is not None:
        pct = int(budget_ratio * 100)
        if budget_ratio >= 0.9:
            line += f" [{_WARN}]· budget {pct}%[/{_WARN}]"
        else:
            line += f" [dim]· budget {pct}%[/dim]"
    if awaiting > 0:
        word = "승인대기"
        line += f" [{_WARN}]· {awaiting} {word} (/goal awaiting)[/{_WARN}]"
    return line


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


def mode_switch_flash(label: str) -> str:
    """The transient '▶▶ <mode> mode on' confirmation shown in the live status zone on a
    Shift+Tab switch (ephemeral — replaces in place, never appended to the transcript)."""

    return f"[{_ACCENT}]▶▶[/{_ACCENT}] [b]{label}[/b] mode on"


def hint_line(
    *,
    palette_open: bool = False,
    help_open: bool = False,
    in_agent: bool = False,
    typing: bool = False,
    mode_label: str = "",
) -> str:
    """The secondary mode/shortcut line shown BELOW the input bar (Claude-style).

    Claude shows a quiet mode line under the input (``▶▶ auto mode on …``) only at idle.
    So: while TYPING (non-empty input) this line is empty — the input is the star and
    clutter drops. When the palette is open the palette owns the space (also empty here).
    At idle it shows the CURRENT runtime mode + key shortcuts with a small accent ``▶▶``
    marker — so Shift+Tab is reflected HERE (not a fixed "operator" string), and this line
    doubles as the persistent live mode indicator.
    """

    if typing or palette_open:
        return ""  # reduce clutter while typing / let the palette own the space
    marker = f"[{_ACCENT}]▶▶[/{_ACCENT}]"
    if help_open:
        return f"{marker} [dim]Tab 탭 전환 · Esc 로 닫기[/dim]"
    if in_agent:
        return f"{marker} [dim]Esc 로 operator · /help · ^C quit[/dim]"
    mode = (mode_label or "operator").strip()
    return f"{marker} [b]{mode}[/b] [dim]mode · /help · / palette · ^C quit[/dim]"


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


def selection_copy_lines(inline: bool) -> Tuple[str, ...]:
    """Mode-aware select & copy guidance — HONEST about what actually works per UI mode.

    The selection path is a real behavioural fact of the run mode (verified in
    :mod:`tui.ui_mode`): inline runs with ``mouse=False`` so the TERMINAL owns drag-select
    (native selection + the terminal's own copy); full-screen captures the mouse so the APP
    owns selection (in-app drag + ``Ctrl+C``) and a plain terminal drag is blocked. ``/copy``
    works in BOTH modes (plain-text → OS clipboard). The selection highlight is the brand
    desaturated-cyan (``accent-dim``) at a measured 4.75:1 contrast (see
    ``test_tui_selection_contrast``). Pure → unit-testable without a terminal."""

    common = (
        "  /copy            마지막 응답 복사 (= /copy last)",
        "  /copy turn <n>   n 번째 턴(질문+응답)   ·   /copy block <n>  n 번째 블록",
        "  /copy all        전체 복사   ·   /copy paste <id>  보존된 large paste",
        "  [dim]plain-text 로 OS clipboard 에 실제 복사 (pbcopy/xclip) — 빈 내용은 실패로 정직 표기.[/dim]",
        "  [dim]선택 하이라이트 = brand accent-dim (대비 4.75:1).[/dim]",
    )
    if inline:
        return (
            "[b]선택 · 복사 (select & copy)[/b]  [dim]— inline 모드 (현재)[/dim]",
            "  드래그로 [b]터미널 native 선택[/b] → 터미널 복사 (마우스 캡처 안 함)",
            *common,
        )
    return (
        "[b]선택 · 복사 (select & copy)[/b]  [dim]— full-screen 모드 (현재)[/dim]",
        "  앱 내 [b]드래그 선택 → Ctrl+C[/b] 로 복사 (마우스 캡처)",
        "  [dim]일반 터미널 드래그는 막힘 — iTerm2 Option+드래그 / 기타 Shift+드래그로 native 우회.[/dim]",
        *common,
    )


def help_sections(
    commands: Sequence, agents: Sequence[AgentInfo], *, inline: bool = False,
) -> Tuple[HelpSection, ...]:
    """Build the help tabs — short, scannable. Order: Help · General · Commands · Agents.

    ``General`` is the default-open tab (see :func:`default_help_tab`). ``inline`` selects
    the mode-aware select/copy guidance (see :func:`selection_copy_lines`).
    """

    help_tab = HelpSection("Help", (
        "[b]forgekit help[/b] — 이 화면 사용법.",
        "",
        "  Tab        탭 전환 (Help · General · Commands · Agents)",
        "  Esc        transcript 로 돌아가기",
        "  F1         help 토글",
        "",
        "탭은 제자리에서 바뀝니다 — 본문에 쌓이지 않습니다.",
        "",
        *selection_copy_lines(inline),
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


def armory_intake_lines(pairs, results, *, detail_id="") -> Tuple[str, ...]:
    """Render the Armory intake — evaluated external candidates by disposition / detail.

    ``pairs`` = (ArmoryCandidate, AdoptionReview) tuples; ``results`` = parallel
    AdoptionResult tuple (adopt_candidate output). ``detail_id`` shows one candidate's
    8축 + 3축 review; else a verdict-bucket summary.
    """

    _V = {"adopt-now": _OK, "collect-first": _WARN, "hold": _ERR}
    by_id = {c.id: (c, rv) for c, rv in pairs}
    res_by_id = {r.candidate_id: r for r in results}

    if detail_id:
        item = by_id.get(detail_id)
        if not item:
            return (f"[{_ERR}]후보 '{detail_id}' 없음[/{_ERR}]",)
        c, rv = item
        res = res_by_id.get(detail_id)
        disp = res.disposition if res else rv.disposition()
        tag = _V.get(disp, _MUTED)
        lines = [
            f"[b {_ACCENT}]» armory intake · {c.name}[/b {_ACCENT}]  [dim]({c.kind})[/dim]",
            f"  verdict: [{tag}]{disp}[/{tag}]"
            + (f"  ·  [{_OK}]adopted spec[/{_OK}]" if res and res.adopted else "")
            + f"   [dim]{c.source_ref}[/dim]",
            f"  현재 pain: {rv.current_pain}",
            f"  기대 효과: {rv.expected_benefit}",
            f"  기존 중복: {rv.overlap_with_existing}",
            f"  운영 비용: {rv.operational_cost}",
            f"  유지 리스크: {rv.maintenance_risk}",
            f"  provider/runtime: {rv.provider_runtime_fit}",
            f"  governance/security: {rv.governance_security_impact}",
            f"  도입 시점 사유: {rv.adopt_timing_reason}",
            "  3축 검토:",
        ]
        for a in rv.axis_reviews:
            atag = _V.get(a.position, _MUTED)
            lines.append(f"    - {a.axis}({a.reviewer}): [{atag}]{a.position}[/{atag}] — {a.rationale}")
        if res and not res.adopted and res.reasons:
            lines.append(f"  [dim]비활성 사유: {res.reasons[0]}[/dim]")
        return tuple(lines)

    buckets = {"adopt-now": [], "collect-first": [], "hold": []}
    for r in results:
        buckets.setdefault(r.disposition, []).append(r)
    adopted = [r.candidate_id for r in results if r.adopted]
    lines = [
        f"[b {_ACCENT}]» armory intake — 외부 후보 도입 검토[/b {_ACCENT}]",
        f"  adopt-now {len(buckets['adopt-now'])} · collect-first {len(buckets['collect-first'])} · hold {len(buckets['hold'])}"
        f"  [dim](총 {len(results)}건 · 8축 artifact + PM/tech-lead/specialist 3축)[/dim]",
        f"  adopted spec(=카탈로그 등록, 미설치): {', '.join(adopted) or '(없음)'}",
    ]
    for v, tag in (("adopt-now", _OK), ("collect-first", _WARN), ("hold", _ERR)):
        if not buckets[v]:
            continue
        lines.append(f"  [b][{tag}]{v}[/{tag}][/b]")
        for r in buckets[v]:
            nm = by_id.get(r.candidate_id, (None, None))[0]
            label = nm.name if nm else r.candidate_id
            lines.append(f"    [{tag}]•[/{tag}] {label} [dim]({r.candidate_id})[/dim]")
    lines.append("  [dim]adopted=카탈로그 등록(available) ≠ equipped/installed · collect-first=근거만 누적 · `/armory <id>`[/dim]")
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


def process_feed_lines(events: Sequence) -> Tuple[str, ...]:
    """Render process events as compact timeline lines (Claude tone, ForgeKit verbs).

    The ACTIVE (running) step stands out — a bold accent ``▸`` marker + a NON-dim label —
    so the operator sees "what's happening now" at a glance; finished steps stay quiet
    (a dim ``•`` + dim label). The dot colour is the severity. Running → ``…``; blocked/error
    → ``— <reason>``; done → quiet. Duration only shows when it was MEASURED (no fake ~1s).
    The active distinction is purely ``status``-driven — never a fake spinner/typing."""

    from . import process_events as pe

    out = []
    for ev in events:
        detail = f" [dim]{ev.detail}[/dim]" if ev.detail else ""
        dur = f" [dim]({ev.duration_ms / 1000:.1f}s)[/dim]" if ev.duration_ms else ""
        if ev.status == pe.ST_RUNNING:
            # active step: bold accent marker + bright label (the "now" line).
            out.append(
                f"[b {_ACCENT}]▸[/b {_ACCENT}] [{_ACCENT}]{ev.label}[/{_ACCENT}]"
                f"{detail}{dur} [dim]…[/dim]")
            continue
        dot = {pe.SEV_WARN: _WARN, pe.SEV_ERROR: _ERR}.get(ev.severity, _ACCENT)
        if ev.status == pe.ST_BLOCKED:
            tail = f" [{_WARN}]— {ev.detail or 'blocked'}[/{_WARN}]"
            detail = ""  # the reason is in the tail
        elif ev.status == pe.ST_FAILED:
            tail = f" [{_ERR}]— {ev.detail or 'error'}[/{_ERR}]"
            detail = ""
        else:
            tail = ""
        out.append(f"[{dot}]•[/{dot}] [dim]{ev.label}[/dim]{detail}{dur}{tail}")
    return tuple(out)


# The assistant response marker — magenta ● (the "kit" half of the brand), completing the
# transcript turn vocabulary alongside the cyan `›` you-marker and the `»` command-result
# header. A free-text LLM response otherwise had NO role marker (body straight after the
# echo), so a turn's response start was hard to scan. Magenta keeps it distinct from the
# cyan status dots / you-marker.
RESPONSE_MARKER = f"[b {_ACCENT2}]●[/b {_ACCENT2}]"


def mark_response_chunks(chunks: Sequence[Sequence[str]]) -> Tuple[Tuple[str, ...], ...]:
    """Prefix the FIRST non-empty response line (across all chunks) with the assistant
    marker, so a free-text response reads as a distinct turn. Pure: exactly one line is
    marked; leading blank lines are left untouched; chunk shape is preserved."""

    out = []
    marked = False
    for chunk in chunks:
        new = []
        for ln in chunk:
            if not marked and (ln or "").strip():
                new.append(f"{RESPONSE_MARKER} {ln}")
                marked = True
            else:
                new.append(ln)
        out.append(tuple(new))
    return tuple(out)


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
    "setup_required_banner", "setup_explanation_lines", "runtime_mode_line", "submit_held_line",
    "issue_line", "agent_pane_lines",
    "status_pane_lines",
    "palette_lines", "palette_panel_lines", "mode_badge", "mode_pill",
    "status_pill", "hint_line", "help_sections", "selection_copy_lines",
    "help_panel_document", "help_tab_strip", "help_body", "default_help_tab",
    "handoff_summary_lines", "loop_summary_lines", "auto_decision_lines", "source_status_lines", "discovery_lines", "armory_intake_lines", "video_watch_lines", "self_improve_lines", "security_drill_lines", "autopilot_lines", "design_status_lines", "result_block", "chunk_result_lines", "process_feed_lines",
    "RESPONSE_MARKER", "mark_response_chunks",
)
