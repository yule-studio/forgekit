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

    Claude-style: brand + version on top, then provider/profile, then the repo
    path — a few quiet lines. Pure so it's unit-testable without a terminal.
    """

    return (
        f"{theme.wordmark(BRAND)} [dim]v{version}[/dim]",
        f"[dim]{TAGLINE}[/dim]",
        f"[dim]provider[/dim] {provider}   [dim]profile[/dim] {profile}",
        f"[dim]{repo}[/dim]",
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


def hint_line(*, palette_open: bool = False, help_open: bool = False, in_agent: bool = False) -> str:
    """Contextual one-line shortcut hint (replaces the thick footer)."""

    if help_open:
        return "[dim]Tab 탭 · Esc 닫기[/dim]"
    if palette_open:
        return "[dim]Tab 순환 · Enter 실행 · Esc 닫기[/dim]"
    if in_agent:
        return "[dim]/help · Esc operator · ^C quit[/dim]"
    return "[dim]/help · / palette · Tab 완성 · ^C quit[/dim]"


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

    if not sections:
        return ("[dim]no help[/dim]",)
    active = max(0, min(active, len(sections) - 1))
    chips = []
    for i, s in enumerate(sections):
        chips.append(
            f"[reverse {_ACCENT}] {s.title} [/reverse {_ACCENT}]"
            if i == active
            else f"[dim]{s.title}[/dim]"
        )
    return (
        theme.wordmark("forgekit") + " [dim]help[/dim]   " + "  ".join(chips),
        "[dim]Tab 탭 전환 · Esc 로 transcript 로 돌아갑니다 · 입력창은 그대로 열려 있습니다[/dim]",
        "[dim]" + "─" * 48 + "[/dim]",
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
    """Palette overlay body: candidates with the selected row highlighted."""

    if not commands:
        return ("[dim]일치하는 명령이 없습니다[/dim]",)
    out = []
    for i, c in enumerate(commands):
        if i == selected:
            out.append(f"[reverse {_ACCENT}] ▸ /{c.name} [/reverse {_ACCENT}] [dim]{c.summary}[/dim]")
        else:
            out.append(f"   [b]/{c.name}[/b] [dim]{c.summary}[/dim]")
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
        "[dim]일반 텍스트 입력은 아직 stub 입니다 (live submit 범위 밖).[/dim]",
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
    return (help_tab, general, commands_tab, agents_tab)


def result_block(title: str, lines: Sequence[str]) -> Tuple[str, ...]:
    """Frame a command result for the center log."""

    header = f"[b {_ACCENT}]» {title}[/b {_ACCENT}]" if title else f"[b {_ACCENT}]»[/b {_ACCENT}]"
    return (header, *lines, "")


__all__ = (
    "BRAND", "TAGLINE",
    "welcome_banner", "intro_meta_lines", "issue_line", "agent_pane_lines",
    "status_pane_lines",
    "palette_lines", "palette_panel_lines", "mode_badge", "mode_pill",
    "status_pill", "hint_line", "help_sections",
    "help_panel_document", "default_help_tab",
    "result_block",
)
