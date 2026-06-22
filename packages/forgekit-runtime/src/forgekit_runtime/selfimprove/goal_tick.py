"""Goal-tick (GW4-A) — connect a long-term Goal to bounded self-improvement.

This is the loop that makes ForgeKit *self-managing*: one tick reads a goal, runs
the existing bounded self-improvement pass (``run_self_improvement``: observe →
classify → packetize → route), then **links the proposed packets to the goal and
writes evidence back to it**. The goal becomes the durable memory of "what did
ForgeKit notice and propose toward this objective".

Honest, bounded posture (no fake autonomy — see ``docs/forgekit-goal-roadmap.md``
GW4 + ``control-plane-architecture.md`` §6):

- A tick **executes nothing**. It observes, proposes, links, and records. Any
  mutation stays behind the *existing* approval chain in
  ``forgekit_runtime.autopilot.chain`` (GW3: ``run_internal_chain`` +
  ``can_specialist_execute``) — this module does NOT re-implement or bypass it.
- If a tick surfaces any RISKY/BLOCKED packet, an ACTIVE goal moves to
  ``awaiting_approval`` (operator decision). SAFE-only ticks leave the goal
  ACTIVE (safe class is auto-OK *within* the chain, still not executed here).
- A tick NEVER marks a goal ``done`` — that needs verified execution evidence,
  which only the gated execution path can produce.

GW4-B seam (declared, not built here): bridging an approved
``RepoImprovementPacket`` into the autopilot execution chain
(``RepoImprovementPacket`` → ``RepoFinding`` → ``run_internal_chain`` →
gated ``validate_execution``). Until that lands, approved packets are routed and
recorded, not auto-executed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Sequence, Tuple

from forgekit_goal import Goal, GoalStatus
from forgekit_goal import transitions

from . import packet as P
from .loop import SelfImprovementResult, route_packet, run_self_improvement


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def packet_id(pkt: P.RepoImprovementPacket) -> str:
    """A stable content-derived id so the same finding links once, not N times.

    ``RepoImprovementPacket`` is content-based (no id field); we hash the parts
    that identify the work (finding + area + risk) so re-running a tick that
    re-discovers the same gap reuses the id (Goal.link_packet dedups it).
    """

    blob = f"{pkt.finding}\x00{pkt.affected_area}\x00{pkt.risk}".encode("utf-8")
    return "packet-" + hashlib.sha1(blob).hexdigest()[:12]


@dataclass(frozen=True)
class GoalTickResult:
    goal: Goal
    result: SelfImprovementResult
    routes: Tuple[Tuple[str, str, str], ...] = ()  # (packet_id, risk, route_text)
    proposed: int = 0
    approval_waiting: int = 0  # risky + blocked count

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal.id,
            "goal_status": self.goal.status.value,
            "proposed": self.proposed,
            "approval_waiting": self.approval_waiting,
            "routes": [
                {"packet_id": pid, "risk": risk, "route": route}
                for pid, risk, route in self.routes
            ],
        }


def tick_goal(
    goal: Goal,
    repo_root,
    *,
    signals: Sequence = (),
    limit: int = 10,
    now: Callable[[], str] = _utcnow,
) -> GoalTickResult:
    """Run one bounded self-improvement tick *for* a goal. Executes nothing.

    Links each proposed packet to the goal, appends a ``proposal`` evidence
    record per packet, and — if any RISKY/BLOCKED packet appears — moves an
    ACTIVE goal to ``awaiting_approval``. Returns the updated goal + result.
    """

    si = run_self_improvement(repo_root, signals=signals, limit=limit)

    g = goal
    routes: List[Tuple[str, str, str]] = []
    for pkt in si.packets:
        pid = packet_id(pkt)
        route = route_packet(pkt)
        g = g.link_packet(pid, now=now)
        g = g.add_evidence(
            "proposal",
            f"[{pkt.risk}] {pkt.finding} -> {route}",
            ref=pid,
            now=now,
        )
        routes.append((pid, pkt.risk, route))

    waiting = len(si.risky) + len(si.blocked)
    # Reflect operator-approval-wait on the goal when risky/blocked work appears.
    # Only a legal ACTIVE -> awaiting_approval move; never forces an illegal one,
    # and NEVER advances to done (that needs verified execution evidence).
    if waiting > 0 and transitions.can_transition(g.status, GoalStatus.AWAITING_APPROVAL):
        g = transitions.apply(g, GoalStatus.AWAITING_APPROVAL, now=now)

    return GoalTickResult(
        goal=g,
        result=si,
        routes=tuple(routes),
        proposed=len(si.packets),
        approval_waiting=waiting,
    )


__all__ = ("GoalTickResult", "tick_goal", "packet_id")
