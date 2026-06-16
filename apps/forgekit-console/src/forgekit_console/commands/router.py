"""Command router — maps a parsed input to a :class:`CommandResult`. Pure.

The router holds no IO: status surfaces are reached through zero-arg loader
callables on the :class:`ConsoleContext`, which :func:`build_default_context`
binds to the real (best-effort) ``status_loader`` functions and tests replace
with fakes. So routing logic is fully unit-testable without a runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Tuple

from ..models import (
    KIND_AGENT_MODE,
    KIND_CLEAR,
    KIND_HELP,
    KIND_QUIT,
    CommandResult,
    StatusSummary,
)
from .registry import (
    H_AGENT_ENTER,
    H_AGENTS,
    H_CLEAR,
    H_DOCTOR,
    H_HARNESS,
    H_HELP,
    H_QUIT,
    H_RUNTIME,
    H_STATUS,
    find_agent,
    find_command,
    load_agents,
    load_commands,
)

StatusLoader = Callable[[], StatusSummary]


def _unavailable(title: str) -> StatusSummary:
    return StatusSummary(title=title, available=False, error="loader not configured")


@dataclass
class ConsoleContext:
    """Everything the router needs — registries + zero-arg status loaders."""

    repo_root: Path
    agent_id: str = "engineering-agent"
    profile: str = "operator"
    agents: Tuple = field(default_factory=load_agents)
    commands: Tuple = field(default_factory=load_commands)
    load_operator: StatusLoader = lambda: _unavailable("operator dashboard")
    load_runtime: StatusLoader = lambda: _unavailable("runtime status")
    load_doctor: StatusLoader = lambda: _unavailable("doctor")


def build_default_context(repo_root: Path, *, agent_id: str = "engineering-agent") -> ConsoleContext:
    """Bind the real best-effort loaders to *repo_root*."""

    from ..data import status_loader as sl

    root = Path(repo_root)
    return ConsoleContext(
        repo_root=root,
        agent_id=agent_id,
        load_operator=lambda: sl.load_operator_summary(root),
        load_runtime=lambda: sl.load_runtime_summary(root),
        load_doctor=lambda: sl.load_doctor_summary(root, agent_id),
    )


def _summary_to_result(summary: StatusSummary) -> CommandResult:
    kind = "info" if summary.available else "error"
    return CommandResult(
        kind=kind,
        title=summary.title,
        lines=summary.flat_lines(),
        alerts=summary.alerts,
    )


def route(parsed, ctx: ConsoleContext) -> CommandResult:
    """Route a :class:`ParsedInput` to a :class:`CommandResult`."""

    if not parsed.is_slash:
        if not (parsed.raw or "").strip():
            return CommandResult.info("", ())
        return CommandResult.info(
            "free text",
            (
                "일반 텍스트 입력은 아직 연결되지 않았습니다 (live submit 범위 밖).",
                "슬래시 명령을 쓰세요 — `/help` 로 목록을 봅니다.",
            ),
        )
    if not parsed.name:
        return CommandResult.info("", ("`/` 뒤에 명령을 입력하세요 — `/help`.",))

    cmd = find_command(parsed.name, ctx.commands)
    if cmd is None:
        return CommandResult.error(
            f"unknown command: /{parsed.name}",
            ("`/help` 로 사용 가능한 명령을 확인하세요.",),
        )

    handler = cmd.handler
    if handler == H_HELP:
        return _help_result(ctx)
    if handler == H_AGENTS:
        return _agents_result(ctx)
    if handler == H_STATUS or handler == H_HARNESS:
        return _summary_to_result(ctx.load_operator())
    if handler == H_RUNTIME:
        return _summary_to_result(ctx.load_runtime())
    if handler == H_DOCTOR:
        return _summary_to_result(ctx.load_doctor())
    if handler == H_AGENT_ENTER:
        return _agent_enter_result(cmd, ctx)
    if handler == H_QUIT:
        return CommandResult(kind=KIND_QUIT, title="quit", lines=("콘솔을 종료합니다…",))
    if handler == H_CLEAR:
        return CommandResult(kind=KIND_CLEAR, title="clear")
    return CommandResult.error(f"no handler for /{parsed.name}")


def _help_result(ctx: ConsoleContext) -> CommandResult:
    # KIND_HELP signals the TUI to open the help overlay. Lines are kept as a
    # text fallback for non-TUI / test consumers.
    lines = ["사용 가능한 명령:"]
    for cmd in ctx.commands:
        lines.append(f"  /{cmd.name:<16} {cmd.summary}")
    lines.append("")
    lines.append("일반 텍스트는 아직 echo/stub 입니다 (live submit 범위 밖).")
    return CommandResult(kind=KIND_HELP, title="help", lines=tuple(lines))


def _agents_result(ctx: ConsoleContext) -> CommandResult:
    lines = ["에이전트 레지스트리:"]
    for agent in ctx.agents:
        enter = f"  ({agent.enter_command})" if agent.enter_command else ""
        lines.append(f"  • {agent.label:<14} [{agent.status}] — {agent.description}{enter}")
    return CommandResult.info("agents", tuple(lines))


def _agent_enter_result(cmd, ctx: ConsoleContext) -> CommandResult:
    agent = find_agent(cmd.agent_id, ctx.agents)
    if agent is None:
        return CommandResult.error(f"unknown agent: {cmd.agent_id}")
    lines = [
        f"▶ {agent.label} 에이전트 모드 진입 (stub)",
        f"  {agent.description}",
        "",
        "이 모드는 1차 콘솔 프레임의 stub 입니다 — live submit 은 아직 연결 안 됨.",
        "추천 다음 행동:",
        "  - `/status` 로 현재 운영 상태 확인",
        "  - `/doctor` 로 환경 점검",
    ]
    # Ops Observer 는 관측 역할이므로 운영 대시보드 alert 를 바로 곁들인다.
    if agent.agent_id == "ops-observer":
        summary = ctx.load_operator()
        if summary.alerts:
            lines.append("")
            lines.append("현재 alerts:")
            lines.extend(f"  [{a.level}] {a.message}" for a in summary.alerts)
    return CommandResult(kind=KIND_AGENT_MODE, title=f"agent:{agent.agent_id}", lines=tuple(lines))


__all__ = ("ConsoleContext", "build_default_context", "route")
