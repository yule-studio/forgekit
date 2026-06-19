"""Forgekit console data models — pure, stdlib-only.

These dataclasses are the contract between the console's pure core (command
parsing/routing, status shaping) and the TUI layer. Keeping them free of any
textual / IO import means the whole core is unit-testable without a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence, Tuple

# Result kinds the router emits; the TUI decides how to present each.
KIND_INFO = "info"
KIND_ERROR = "error"
KIND_AGENT_MODE = "agent_mode"
KIND_HELP = "help"        # open the inline help surface (no log output)
KIND_LAYOUT = "layout"    # toggle the layout mode (focus <-> dashboard)
KIND_QUIT = "quit"
KIND_CLEAR = "clear"

# Console interaction modes (surfaced in the footer / input chrome).
MODE_OPERATOR = "operator"

# Layout modes — focus is the content-first default; dashboard surfaces the
# operator rail. The app holds the active one; `/layout` toggles.
LAYOUT_FOCUS = "focus"
LAYOUT_DASHBOARD = "dashboard"


def agent_mode(agent_id: str) -> str:
    """The mode string for an entered agent (e.g. ``agent:product-agent``)."""

    return f"agent:{agent_id}"

# Alert levels for the status pane.
LEVEL_INFO = "info"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"


@dataclass(frozen=True)
class AgentInfo:
    """One agent in the quick list / registry."""

    agent_id: str
    label: str
    description: str
    enter_command: str  # slash command that enters this agent's mode (or "")
    status: str = "idle"


@dataclass(frozen=True)
class Alert:
    level: str  # LEVEL_*
    message: str


@dataclass(frozen=True)
class StatusSection:
    title: str
    lines: Tuple[str, ...] = ()


@dataclass(frozen=True)
class HelpSection:
    """One tab/section of the help overlay."""

    title: str
    lines: Tuple[str, ...] = ()


@dataclass(frozen=True)
class StatusSummary:
    """A read-only status view shaped for a pane / command output."""

    title: str
    sections: Tuple[StatusSection, ...] = ()
    alerts: Tuple[Alert, ...] = ()
    next_actions: Tuple[str, ...] = ()
    available: bool = True
    error: str = ""

    def flat_lines(self) -> Tuple[str, ...]:
        """Flatten sections + alerts + next-actions into display lines."""

        out: list[str] = []
        if not self.available:
            out.append(f"[unavailable] {self.error or 'surface not reachable'}")
        for section in self.sections:
            out.append(f"## {section.title}")
            out.extend(f"  {line}" for line in section.lines)
        if self.alerts:
            out.append("## alerts")
            out.extend(f"  [{a.level}] {a.message}" for a in self.alerts)
        if self.next_actions:
            out.append("## what to do next")
            out.extend(f"  - {a}" for a in self.next_actions)
        return tuple(out)


@dataclass(frozen=True)
class ParsedInput:
    """A parsed input line from the console prompt."""

    raw: str
    is_slash: bool
    name: str = ""              # command name without the leading slash, lowercased
    args: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandResult:
    """The router's output for one input line."""

    kind: str  # KIND_*
    title: str = ""
    lines: Tuple[str, ...] = ()
    alerts: Tuple[Alert, ...] = ()

    @classmethod
    def info(cls, title: str, lines: Sequence[str] = ()) -> "CommandResult":
        return cls(kind=KIND_INFO, title=title, lines=tuple(lines))

    @classmethod
    def error(cls, title: str, lines: Sequence[str] = ()) -> "CommandResult":
        return cls(kind=KIND_ERROR, title=title, lines=tuple(lines))


__all__ = (
    "KIND_INFO", "KIND_ERROR", "KIND_AGENT_MODE", "KIND_HELP", "KIND_LAYOUT",
    "KIND_QUIT", "KIND_CLEAR",
    "LEVEL_INFO", "LEVEL_WARN", "LEVEL_ERROR",
    "MODE_OPERATOR", "LAYOUT_FOCUS", "LAYOUT_DASHBOARD", "agent_mode",
    "AgentInfo", "Alert", "StatusSection", "StatusSummary", "HelpSection",
    "ParsedInput", "CommandResult",
)
