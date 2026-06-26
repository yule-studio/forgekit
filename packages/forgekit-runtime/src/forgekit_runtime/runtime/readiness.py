"""Runtime readiness — the ONE honest "can the always-on loop make progress?" verdict.

The bounded daemon, the goal-continuity status, and the provider live-transport state each
have their own surface, but an operator running ForgeKit unattended needs them JOINED: is the
serve loop running, are there goals to advance, do any need approval, and — crucially — is
there a **live transport** for work that needs an LLM, or will packets just accumulate?

This module joins three real sources (no fake state):
- daemon heartbeat (:mod:`.heartbeat`) — running / stopped / kill-pending;
- goal continuity (:mod:`.goal_status`) — active / awaiting-approval / blocked / last work;
- declared live transport (:mod:`forgekit_provider.policy.routing`) — which work slots
  (default_chat / execution / research) resolve to a console-live-capable provider.

Honesty rails:
- **no fake-live**: without a probe the transport is "declared live lane (미검증)" — a
  capable-but-unverified path, NEVER asserted as live-right-now. A probe (``available``) can
  upgrade it to verified. Implicit local fallback is reflected only when actually enabled.
- the verdict names the *binding constraint* on autonomous progress (setup / no-goals /
  awaiting-operator / no-live-lane / progressing) — it never invents progress.
- unattended is platform-honest: macOS LaunchAgent suspends on clamshell sleep; Linux/systemd
  is the 1st-class 24h path. We state the caveat, never claim "24h" blindly.

Pure given (heartbeat file, goal store, provider config) → unit-testable with a tempdir.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Tuple

from . import goal_status as gs
from . import heartbeat as hb
from . import surface as _surface

# work slots whose live transport actually carries LLM-backed work the loop may need.
WORK_SLOTS: Tuple[str, ...] = ("default_chat", "execution", "research")

# readiness verdicts — the single binding constraint on autonomous progress.
RD_SETUP_REQUIRED = "setup_required"        # no provider brain configured
RD_IDLE_NO_GOALS = "idle_no_goals"          # nothing to advance — give it a goal
RD_AWAITING_OPERATOR = "awaiting_operator"  # goal(s) parked at awaiting_approval
RD_NO_LIVE_LANE = "no_live_lane"            # active goals but no live transport for LLM work
RD_PROGRESSING = "progressing"              # active goals + a live lane → loop can advance
RD_VERDICTS = (RD_SETUP_REQUIRED, RD_IDLE_NO_GOALS, RD_AWAITING_OPERATOR,
               RD_NO_LIVE_LANE, RD_PROGRESSING)


@dataclass(frozen=True)
class SlotTransport:
    """One work slot's declared→actual transport (honest live-capable, not verified-live)."""

    slot: str
    declared: str
    actual: str
    live_capable: bool          # console live-submit capable (transport), NOT "live right now"
    verified: Optional[bool]    # True/False from a probe, None = unprobed (declared only)
    reason: str = ""

    def to_dict(self) -> dict:
        return {"slot": self.slot, "declared": self.declared, "actual": self.actual,
                "live_capable": self.live_capable, "verified": self.verified,
                "reason": self.reason}


@dataclass(frozen=True)
class RuntimeReadiness:
    """The joined, honest snapshot of whether the always-on loop can make progress."""

    daemon_state: str                              # alive / stopped / kill-pending
    provider_configured: bool
    active_goals: int = 0
    awaiting_approval: int = 0
    blocked_goals: int = 0
    goals_available: bool = True                    # goal store readable
    last_work: str = ""                            # most recent execution/verification summary
    last_work_goal: str = ""
    slots: Tuple[SlotTransport, ...] = ()
    has_live_lane: bool = False                    # any work slot is live-capable
    live_verified: bool = False                    # any work slot probe-VERIFIED live
    verdict: str = RD_IDLE_NO_GOALS
    next_action: str = ""
    unattended_note: str = ""

    @property
    def loop_running(self) -> bool:
        return self.daemon_state == "alive"

    def to_dict(self) -> dict:
        return {
            "daemon_state": self.daemon_state, "provider_configured": self.provider_configured,
            "active_goals": self.active_goals, "awaiting_approval": self.awaiting_approval,
            "blocked_goals": self.blocked_goals, "goals_available": self.goals_available,
            "last_work": self.last_work, "last_work_goal": self.last_work_goal,
            "slots": [s.to_dict() for s in self.slots],
            "has_live_lane": self.has_live_lane, "live_verified": self.live_verified,
            "verdict": self.verdict, "next_action": self.next_action,
            "unattended_note": self.unattended_note,
        }


def _unattended_note(platform: str) -> str:
    """Platform-honest 24h caveat (no over-claim of unattended stability)."""

    if platform == "darwin":
        return ("macOS: LaunchAgent 는 clamshell(닫힘) sleep 시 suspend — 진짜 24h 는 "
                "`caffeinate -s` 또는 Linux/systemd 권장 (`forgekit runtime install-unit`)")
    return ("Linux/systemd: `--user` 유닛은 로그아웃 시 정지 가능 — 24h 는 lingering "
            "(`loginctl enable-linger`) 권장 (`forgekit runtime install-unit`)")


def _transport_slots(parsed, *, available: Optional[Callable[[str], bool]] = None,
                     live_map: Optional[Mapping[str, Optional[bool]]] = None
                     ) -> Tuple[SlotTransport, ...]:
    """Resolve the work slots → declared/actual transport. ``available`` (a probe) gates by
    real reachability; ``live_map`` carries verified results. Without either, slots are
    declared-capable only (honest: unverified)."""

    from forgekit_provider.policy import routing as rt

    out = []
    lm = dict(live_map or {})
    for slot in WORK_SLOTS:
        res = rt.resolve_routing(parsed, slot, available=available)
        out.append(SlotTransport(
            slot=slot, declared=res.declared_provider, actual=res.actual_provider,
            live_capable=res.is_live_capable,
            verified=lm.get(res.actual_provider),
            reason=res.reason))
    return tuple(out)


def assess_runtime_readiness(
    *,
    env: Optional[Mapping[str, str]] = None,
    config: Optional[Mapping] = None,
    store=None,
    available: Optional[Callable[[str], bool]] = None,
    live_map: Optional[Mapping[str, Optional[bool]]] = None,
    platform: Optional[str] = None,
) -> RuntimeReadiness:
    """Join daemon + goal + transport into one honest readiness verdict.

    ``config`` defaults to the persisted brain config; ``available``/``live_map`` are optional
    probe inputs (absent → declared live lane, unverified — never faked as live)."""

    plat = platform or sys.platform
    daemon_state = _surface.daemon_state(env=env)

    # provider transport (config-derived; honest declared-vs-verified) ----------------------
    if config is None:
        try:
            from forgekit_provider.policy import provider_ops as ops
            config = ops.load_raw_config(env=env)
        except Exception:  # noqa: BLE001 - no config readable → treat as unconfigured
            config = {}
    from forgekit_provider.policy import provider_config as pc
    parsed = pc.load_provider_config(config or {})
    provider_configured = bool(parsed.primary_provider)
    slots = _transport_slots(parsed, available=available, live_map=live_map) if provider_configured else ()
    has_live_lane = any(s.live_capable for s in slots)
    live_verified = any(s.live_capable and s.verified is True for s in slots)

    # goal continuity --------------------------------------------------------------------
    cont = gs.goal_continuity_status(env=env, store=store)
    active = cont.active if cont.available else 0
    awaiting = cont.awaiting_approval if cont.available else 0
    blocked = cont.blocked if cont.available else 0

    # verdict: the single binding constraint on AUTONOMOUS progress (priority order) -------
    if not provider_configured:
        verdict = RD_SETUP_REQUIRED
        action = "`/setup apply` 로 4-provider 브레인을 구성하세요 (primary + live lane)."
    elif awaiting > 0:
        verdict = RD_AWAITING_OPERATOR
        action = f"{awaiting} goal awaiting_approval — `/goal awaiting` → `/goal approve <id>`."
    elif active == 0:
        verdict = RD_IDLE_NO_GOALS
        action = "`/goal new <제목>` → `/goal activate <id>` 로 진행할 목표를 주세요."
    elif not has_live_lane:
        verdict = RD_NO_LIVE_LANE
        action = ("active goal 은 있으나 work slot 에 live transport 없음 — gemini key/ollama 데몬을 "
                  "연결(`/provider connect`)해야 LLM 작업이 진행됩니다 (packet 은 누적, fake-live 아님).")
    else:
        verdict = RD_PROGRESSING
        action = "active goal + live lane 확보 — serve 가 safe packet 을 자동 진행합니다."

    # if the verdict allows progress but the daemon is not running, lead with starting it.
    if daemon_state == "stopped" and verdict in (RD_PROGRESSING, RD_NO_LIVE_LANE, RD_AWAITING_OPERATOR):
        action = "데몬 미가동 — `forgekit runtime serve --interval 300 --max-ticks N` 로 시작 후: " + action
    elif daemon_state == "kill-pending":
        action = "kill-switch SET (다음 tick 정지). 계속하려면 재시작 필요. " + action

    return RuntimeReadiness(
        daemon_state=daemon_state, provider_configured=provider_configured,
        active_goals=active, awaiting_approval=awaiting, blocked_goals=blocked,
        goals_available=cont.available, last_work=cont.last_work, last_work_goal=cont.last_work_goal,
        slots=slots, has_live_lane=has_live_lane, live_verified=live_verified,
        verdict=verdict, next_action=action, unattended_note=_unattended_note(plat))


def _slot_glyph(s: SlotTransport) -> str:
    if s.verified is True:
        return "●"          # probe-verified live now
    if s.verified is False:
        return "○"          # probed and NOT live now
    return "◐" if s.live_capable else "·"   # declared-capable(미검증) / not live-capable


def readiness_lines(*, env: Optional[Mapping[str, str]] = None,
                    config: Optional[Mapping] = None, store=None,
                    available: Optional[Callable[[str], bool]] = None,
                    live_map: Optional[Mapping[str, Optional[bool]]] = None,
                    platform: Optional[str] = None) -> Tuple[str, ...]:
    """Operator-visible runtime readiness block for the `/daemon` and `/setup` surfaces."""

    r = assess_runtime_readiness(env=env, config=config, store=store, available=available,
                                 live_map=live_map, platform=platform)
    goal_word = (f"active {r.active_goals} · awaiting {r.awaiting_approval} · blocked {r.blocked_goals}"
                 if r.goals_available else "goal store 없음")
    lines = [f"  readiness : [b]{r.verdict}[/b]  (daemon={r.daemon_state} · {goal_word})"]
    if r.provider_configured:
        verified = " · 검증됨" if r.live_verified else " · 미검증(probe 안 함)"
        lane = (f"live lane 있음{verified}" if r.has_live_lane else "live lane 없음 (routing-only)")
        lines.append(f"      transport: {lane}")
        for s in r.slots:
            tag = ("live-capable" if s.live_capable else "routing-only")
            lines.append(f"        {_slot_glyph(s)} {s.slot:<12} {s.declared}→{s.actual} ({tag})")
    if r.awaiting_approval:
        lines.append(f"      ⚠ action-needed: {r.awaiting_approval} goal awaiting_approval — `/goal awaiting` → `/goal approve <id>`")
    if r.last_work:
        lines.append(f"      last work: {r.last_work}" + (f" ({r.last_work_goal})" if r.last_work_goal else ""))
    lines.append(f"      다음: {r.next_action}")
    lines.append(f"      [dim]{r.unattended_note}[/dim]")
    return tuple(lines)


__all__ = (
    "WORK_SLOTS", "RD_SETUP_REQUIRED", "RD_IDLE_NO_GOALS", "RD_AWAITING_OPERATOR",
    "RD_NO_LIVE_LANE", "RD_PROGRESSING", "RD_VERDICTS",
    "SlotTransport", "RuntimeReadiness", "assess_runtime_readiness", "readiness_lines",
)
