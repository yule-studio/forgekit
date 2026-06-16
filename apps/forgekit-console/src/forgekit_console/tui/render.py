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
        f"[b orange1]{BRAND}[/b orange1] [dim]v{version}[/dim]",
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
        return "[yellow]status unavailable[/yellow] [dim]· /doctor[/dim]"
    alerts = [a for a in summary.alerts if a.level in ("warn", "error")]
    if not alerts:
        return "[dim]ready · /status[/dim]"
    labels = ", ".join(a.message.split(" ")[0] for a in alerts[:3])
    word = "issue" if len(alerts) == 1 else "issues"
    return f"[yellow]{len(alerts)} {word}[/yellow]: {labels} [dim]· /doctor[/dim]"


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


def help_in_transcript(sections: Sequence[HelpSection], active: int) -> Tuple[str, ...]:
    """Help rendered INTO the transcript — Claude-Code style, scannable.

    Not a modal/panel/accordion: this returns full-width lines the transcript
    appends inline (with a rule above/below so the block reads as one unit). A
    tab strip marks the active tab; only the active tab's content is shown so the
    block stays scannable. Tab switches the active tab, Esc closes it. The
    composer stays docked at the bottom throughout.
    """

    if not sections:
        return ("[dim]no help[/dim]",)
    active = max(0, min(active, len(sections) - 1))
    chips = []
    for i, s in enumerate(sections):
        chips.append(f"[reverse] {s.title} [/reverse]" if i == active else f"[dim]{s.title}[/dim]")
    return (
        "[dim]" + "─" * 8 + "[/dim] [b orange1]forgekit help[/b orange1]   " + "  ".join(chips),
        "[dim]Tab 탭 전환 · Esc 닫기 · 입력창은 그대로 열려 있습니다[/dim]",
        "",
        *sections[active].lines,
        "[dim]" + "─" * 24 + "[/dim]",
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
    """Build the help tabs — short, scannable. Order: Help · General · Commands · Agents.

    ``General`` is the default-open tab (see :func:`default_help_tab`).
    """

    help_tab = HelpSection("Help", (
        "[b]빠른 사용 흐름[/b]",
        "  1. 입력창에 `/` 를 치면 명령 목록(palette)이 열립니다.",
        "  2. Tab 으로 자동완성, Enter 로 실행.",
        "  3. 결과는 아래 본문에 위→아래로 쌓입니다.",
        "  4. `/status` 운영 요약 · `/doctor` 진단 · `/exit` 종료.",
    ))
    general = HelpSection("General", (
        "forgekit — 운영자 콘솔 (provider-agnostic).",
        "입력창에 슬래시 명령을 치거나 에이전트 모드로 들어갑니다.",
        "일반 텍스트는 아직 live submit 에 연결되지 않았습니다.",
        "",
        "[b]단축키[/b]",
        "  /  palette     Tab  자동완성     Enter  실행",
        "  ←/→  탭         Esc  닫기/복귀     F1  help",
        "  ^L  clear      ^R  refresh       ^C  quit",
    ))
    cmd_lines = ["[b]commands[/b]", ""]
    for c in commands:
        cmd_lines.append(f"  [b]/{c.name}[/b]  [dim]{c.summary}[/dim]")
    commands_tab = HelpSection("Commands", tuple(cmd_lines))
    agents_tab = HelpSection("Agents", (
        "[b]operator mode[/b]  기본 모드 — 콘솔 명령으로 운영 표면을 봅니다.",
        "[b]agent mode[/b]     에이전트 모드(stub) — 진입 시 상단에 표시됩니다.",
        "",
        "진입 예: " + " · ".join(a.enter_command for a in agents if a.enter_command) or "(없음)",
        "",
        "Esc 로 operator 모드로 돌아옵니다.",
    ))
    return (help_tab, general, commands_tab, agents_tab)


def result_block(title: str, lines: Sequence[str]) -> Tuple[str, ...]:
    """Frame a command result for the center log."""

    header = f"[b cyan]» {title}[/b cyan]" if title else "[b cyan]»[/b cyan]"
    return (header, *lines, "")


__all__ = (
    "BRAND", "TAGLINE",
    "welcome_banner", "intro_meta_lines", "issue_line", "agent_pane_lines",
    "status_pane_lines",
    "palette_lines", "palette_panel_lines", "mode_badge", "mode_pill",
    "status_pill", "hint_line", "help_sections",
    "help_in_transcript", "default_help_tab",
    "result_block",
)
