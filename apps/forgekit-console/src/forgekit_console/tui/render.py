"""Pure render helpers for the TUI — return plain strings/markup, no textual.

Keeping these as pure string builders means the welcome banner, agent-pane
lines, and status-pane lines are unit-testable without a terminal, and the
textual widgets in :mod:`app` just feed these strings in.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from ..models import MODE_OPERATOR, AgentInfo, HelpSection, StatusSummary

BRAND = "forgekit"
TAGLINE = "operator console"

# Textual/Rich console-markup colours per alert level.
_LEVEL_STYLE = {"info": "dim", "warn": "yellow", "error": "bold red"}


def welcome_banner(repo_root: str, profile: str) -> Tuple[str, ...]:
    """The first content block — compact, reads top→down above results."""

    return (
        "[dim]운영자 콘솔. 위 입력창에 명령을 치면 결과가 여기 아래로 쌓입니다.[/dim]",
        "[dim]일반 텍스트(자유 입력)는 아직 live submit 에 연결되지 않았습니다.[/dim]",
        "",
        "[b]quick[/b]  "
        "[orange3]/help[/orange3] 도움말 · [orange3]/status[/orange3] 운영요약 · "
        "[orange3]/agents[/orange3] 에이전트 · [orange3]/doctor[/orange3] 진단 · "
        "[orange3]/layout[/orange3] 보기전환",
        "[dim]`/` 팔레트 · Tab 자동완성 · F1 도움말 · ^C 종료[/dim]",
        "",
    )


def status_pill(summary: StatusSummary) -> str:
    """A single compact operator line — secondary, not the star of the screen."""

    if not summary.available:
        return "[yellow]●[/yellow] [dim]status unavailable[/dim]"
    if any(a.level == "error" for a in summary.alerts):
        dot = "[red]●[/red]"
    elif any(a.level == "warn" for a in summary.alerts):
        dot = "[yellow]●[/yellow]"
    else:
        dot = "[green]●[/green]"
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
        return "[orange1]●[/orange1] [dim]operator[/dim]"
    if mode == "palette":
        return "[cyan]●[/cyan] [dim]palette[/dim]"
    if mode.startswith("agent:"):
        agent_id = mode.split(":", 1)[1]
        label = agent_id
        for a in agents:
            if a.agent_id == agent_id:
                label = a.label
                break
        return f"[dark_orange]●[/dark_orange] [b]{label}[/b]"
    return f"[dim]●[/dim] {mode}"


def hint_line(*, palette_open: bool = False, help_open: bool = False, in_agent: bool = False) -> str:
    """Contextual one-line shortcut hint (replaces the thick footer)."""

    if help_open:
        return "[dim]Esc[/dim] 닫기   [dim]↑/↓[/dim] 스크롤"
    if palette_open:
        return "[dim]Tab · ↑/↓[/dim] 순환   [dim]Enter[/dim] 실행   [dim]Esc[/dim] 닫기"
    base = (
        "[dim]/[/dim] palette   [dim]Tab[/dim] 완성   [dim]F1[/dim] help   "
        "[dim]/layout[/dim] 보기   [dim]^L[/dim] clear   [dim]^C[/dim] quit"
    )
    if in_agent:
        return "[dim]Esc[/dim] operator   " + base
    return base


def help_inline(sections: Sequence[HelpSection]) -> Tuple[str, ...]:
    """Inline help document — reads in the flow (no modal). Sections stacked."""

    tabs = "  ·  ".join(f"[b]{s.title}[/b]" for s in sections)
    lines = [
        f"[b orange1]forgekit help[/b orange1]   [dim]{tabs}[/dim]   [dim](Esc 닫기)[/dim]",
        "",
    ]
    for section in sections:
        lines.append(f"[b orange3]▸ {section.title}[/b orange3]")
        lines.extend(f"  {line}" for line in section.lines)
        lines.append("")
    return tuple(lines)


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
            out.append(f"[reverse] ▸ /{c.name} [/reverse] [dim]{c.summary}[/dim]")
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
        return f"[reverse dark_orange] AGENT · {label} [/reverse dark_orange]"
    if mode == "palette":
        return "[reverse cyan] PALETTE [/reverse cyan]"
    return f"[reverse] {mode.upper()} [/reverse]"


def help_sections(commands: Sequence, agents: Sequence[AgentInfo]) -> Tuple[HelpSection, ...]:
    """Build the tabbed help overlay content. Pure — the widget renders it."""

    overview = HelpSection("Help", (
        f"[b]{BRAND}[/b] — {TAGLINE}",
        "",
        "forgekit 는 yule runtime/harness/doctor surface 위의 운영자 콘솔입니다.",
        "하단 입력창에 슬래시 명령을 입력하거나, 에이전트 모드로 진입하세요.",
        "일반 텍스트(자유 입력)는 아직 live submit 에 연결되지 않았습니다.",
        "",
        "Esc 로 이 도움말을 닫습니다.",
    ))
    general = HelpSection("General", (
        "[b]키[/b]",
        "  /            command palette 열기",
        "  Tab          autocomplete / 다음 후보",
        "  Shift+Tab    이전 후보",
        "  ↑ / ↓        후보 순환",
        "  Enter        선택/입력 실행",
        "  Esc          palette 닫기 · agent 모드 해제",
        "  F1           도움말        ^L 로그 지우기",
        "  ^R 상태 새로고침            ^C 종료",
        "",
        "[b]모드[/b]",
        "  OPERATOR     기본 모드",
        "  AGENT·<name> 에이전트 모드(현재 stub)",
    ))
    cmd_lines = ["[b]slash commands[/b]", ""]
    for c in commands:
        cmd_lines.append(f"  [b]/{c.name}[/b]  [dim]{c.summary}[/dim]")
    custom = HelpSection("Commands", tuple(cmd_lines))
    agent_lines = ["[b]agent modes[/b]", "", "에이전트 모드 진입(stub):"]
    for a in agents:
        if a.enter_command:
            agent_lines.append(f"  [b]{a.enter_command}[/b]  [dim]{a.label} — {a.description}[/dim]")
    agent_lines.append("")
    agent_lines.append("예: /pm-agent · /backend-agent · /security-agent · /ops-observer")
    agents_sec = HelpSection("Agents", tuple(agent_lines))
    return (overview, general, custom, agents_sec)


def result_block(title: str, lines: Sequence[str]) -> Tuple[str, ...]:
    """Frame a command result for the center log."""

    header = f"[b cyan]» {title}[/b cyan]" if title else "[b cyan]»[/b cyan]"
    return (header, *lines, "")


__all__ = (
    "BRAND", "TAGLINE",
    "welcome_banner", "agent_pane_lines", "status_pane_lines",
    "palette_lines", "palette_panel_lines", "mode_badge", "mode_pill",
    "status_pill", "hint_line", "help_sections", "help_inline",
    "result_block",
)
