"""Always-on daemon operator surface — render the REAL heartbeat for the console.

The bounded daemon (:mod:`runtime.daemon`) writes a heartbeat JSON each tick
(status / tick / ts / pid / note) and honors a kill-switch file
(:mod:`runtime.heartbeat`). ``forgekit runtime status`` (CLI) already reads it; this
module renders the SAME real state into console lines so the TUI ``/daemon`` surface
is not blind to the daemon. Pure (reads the file via heartbeat helpers, formats
strings) → unit-testable without a running loop.

It never fabricates liveness: no heartbeat file → ``stopped`` with a start hint; a
set kill-switch → ``kill-pending`` (the running serve exits next tick).
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple

from . import heartbeat as hb


def daemon_state(*, env: Optional[Mapping[str, str]] = None) -> str:
    """Derived operator state: kill-pending > alive(running/idle) > stopped."""

    if hb.is_killed(env=env):
        return "kill-pending"
    beat = hb.read_heartbeat(env=env)
    return "alive" if beat.alive else "stopped"


def daemon_status_lines(*, env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """`/daemon` — the real always-on daemon heartbeat (state / tick / last_tick / pid)."""

    beat = hb.read_heartbeat(env=env)
    killed = hb.is_killed(env=env)
    state = daemon_state(env=env)
    lines = [
        f"forgekit always-on daemon — {state}",
        f"  status    : {beat.status} · tick {beat.tick}" + (f" · pid {beat.pid}" if beat.pid else ""),
        f"  last tick : {beat.ts or '-'}",
        f"  last note : {beat.note or '-'}",
        f"  kill-switch: {'SET — serve 는 다음 tick 에 정지' if killed else 'clear'}",
    ]
    if beat.status == hb.STATUS_STOPPED and not beat.ts:
        lines.append("  (데몬 미가동 — `forgekit runtime serve --interval 300 --max-ticks N` 로 bounded 시작)")
    elif beat.tick > 0:
        # honest resume hint: the next serve continues tick numbering from here (launchd KeepAlive 등).
        lines.append(f"  resume    : 다음 serve 는 tick {beat.tick + 1} 부터 이어집니다 (resume on; cooldown 연속성 유지)")
    # goal-driven continuity visibility — what the serve loop is actually progressing + what
    # needs operator approval (the daemon DRIVES goals; the status must show it, not hide it).
    from .goal_status import goal_continuity_lines
    lines.extend(goal_continuity_lines(env=env))
    lines.append("  [dim]제어: CLI `forgekit runtime serve|once|status|stop` · 콘솔 `/daemon stop`(kill-switch)[/dim]")
    return tuple(lines)


def request_stop(*, env: Optional[Mapping[str, str]] = None) -> Tuple[bool, str]:
    """`/daemon stop` — set the kill-switch; a running serve loop exits next tick."""

    ok = hb.request_kill(env=env)
    if not ok:
        return False, "kill-switch 파일 write 실패 (FORGEKIT_HOME/state 권한 확인)"
    return True, "kill-switch SET — 실행 중인 `forgekit runtime serve` 는 다음 tick 에 정지합니다."


__all__ = ("daemon_state", "daemon_status_lines", "request_stop")
