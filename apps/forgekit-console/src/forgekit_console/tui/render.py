"""Pure render helpers for the TUI — return plain strings/markup, no textual.

Keeping these as pure string builders means the welcome banner, agent-pane
lines, and status-pane lines are unit-testable without a terminal, and the
textual widgets in :mod:`app` just feed these strings in.
"""

from __future__ import annotations

from typing import Sequence, Tuple

from ..models import AgentInfo, StatusSummary

BRAND = "forgekit"
TAGLINE = "operator console"

# Textual/Rich console-markup colours per alert level.
_LEVEL_STYLE = {"info": "dim", "warn": "yellow", "error": "bold red"}


def welcome_banner(repo_root: str, profile: str) -> Tuple[str, ...]:
    """The first-screen banner + quick commands."""

    return (
        f"[b]{BRAND}[/b] — {TAGLINE}",
        f"repo: {repo_root}",
        f"profile: {profile}",
        "",
        "운영자 콘솔 프레임 (1차). live submit 은 아직 연결되지 않았습니다.",
        "",
        "quick commands:",
        "  /help     명령 목록            /status   운영 대시보드",
        "  /agents   에이전트 목록         /doctor   환경 진단",
        "  /runtime  runtime status       /harness  harness 요약",
        "  /quit     종료",
        "",
        "프롬프트에 `/` 를 입력하면 명령 팔레트가 열립니다.",
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


def result_block(title: str, lines: Sequence[str]) -> Tuple[str, ...]:
    """Frame a command result for the center log."""

    header = f"[b cyan]» {title}[/b cyan]" if title else "[b cyan]»[/b cyan]"
    return (header, *lines, "")


__all__ = (
    "BRAND", "TAGLINE",
    "welcome_banner", "agent_pane_lines", "status_pane_lines",
    "palette_lines", "result_block",
)
