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
    # runtime readiness — the JOINED honest view: goal continuity + declared live transport +
    # the single binding constraint on autonomous progress (the daemon DRIVES goals AND needs a
    # live lane for LLM work; the status must show both, not hide either). Falls back to the
    # bare goal-continuity lines if the provider join is unavailable (best-effort).
    try:
        from .readiness import readiness_lines
        lines.extend(readiness_lines(env=env))
    except Exception:  # noqa: BLE001 - a join failure must never blind the daemon surface
        from .goal_status import goal_continuity_lines
        lines.extend(goal_continuity_lines(env=env))
    # provider/runtime continuity — which provider lane EACH PAST TICK routed through + budget
    # (brain vs actual transport vs fallback), read-only over the durable tick ledger. This is
    # the per-tick HISTORY trail; readiness above is the current declared/binding view.
    lines.extend(provider_lane_lines(env=env))
    lines.append("  [dim]제어: CLI `forgekit runtime serve|once|status|stop` · 콘솔 `/daemon stop`(kill-switch)[/dim]")
    return tuple(lines)


def provider_lane_lines(*, env: Optional[Mapping[str, str]] = None,
                        recent: int = 5) -> Tuple[str, ...]:
    """`/daemon` (and `/runtime`) — recent per-tick provider lane + budget continuity."""

    from . import tick_ledger as TL

    records = TL.read_tick_records(env=env, limit=recent)
    return TL.tick_ledger_lines(records)


def request_stop(*, env: Optional[Mapping[str, str]] = None) -> Tuple[bool, str]:
    """`/daemon stop` — set the kill-switch; a running serve loop exits next tick."""

    ok = hb.request_kill(env=env)
    if not ok:
        return False, "kill-switch 파일 write 실패 (FORGEKIT_HOME/state 권한 확인)"
    return True, "kill-switch SET — 실행 중인 `forgekit runtime serve` 는 다음 tick 에 정지합니다."


__all__ = ("daemon_state", "daemon_status_lines", "request_stop", "provider_lane_lines")
