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
    """The first-screen banner + quick commands."""

    return (
        f"[b orange1]{BRAND}[/b orange1] — {TAGLINE}",
        f"[dim]repo:[/dim] {repo_root}   [dim]profile:[/dim] {profile}",
        "",
        "운영자 콘솔. live submit 은 아직 연결되지 않았습니다 (stub).",
        "",
        "[b]quick commands[/b]",
        "  /help     도움말 오버레이       /status   운영 대시보드",
        "  /agents   에이전트 목록          /doctor   환경 진단",
        "  /runtime  runtime status        /harness  harness 요약",
        "  /quit     종료",
        "",
        "[dim]하단 입력창에 `/` 를 치면 command palette 가 열립니다 · Tab 자동완성 · F1 도움말[/dim]",
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
    "palette_lines", "palette_panel_lines", "mode_badge", "help_sections",
    "result_block",
)
