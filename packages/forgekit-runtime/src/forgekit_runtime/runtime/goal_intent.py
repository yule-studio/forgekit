"""Goal-intent decomposition (autopilot exec core) — seed a goal's FIRST packet.

The goal-scheduler packetizes an ACTIVE goal from bounded self-improvement discovery
(``run_self_improvement``: repo TODO/large-file gaps). But a goal whose work is NOT a
repo gap — "build feature X", "complete lane Y" — discovers nothing, so the scheduler's
``if not si.packets: continue`` left it stuck at ``packets: 0 / evidence: 0`` forever.

This module is that missing seed: it turns the goal's own intent (its title) into the
**next real decision-lane step** as a ``RepoImprovementPacket``, so the scheduler always
has at least one packet to link. It does NOT fabricate a design — it asks the existing
``decision_lane.assess_lane_readiness`` what the goal actually needs next and packetizes
THAT. For a bare goal the honest answer is "a PM brief must be written before tech-lead /
specialist work", so the seed packet is **risky** → the goal parks at ``awaiting_approval``
(operator/PM design input needed). No fake packet, no fake execution: the runtime only
surfaces the real next step in the goal → PM brief → tech-lead decision → specialist chain.

Owner: ``packages/forgekit-runtime/runtime``. Reuses ``decision_lane.readiness`` (the
chain stages) + ``selfimprove.packet`` (the packet contract). Executes nothing.
"""

from __future__ import annotations

from typing import List


def intent_packets(goal_title: str) -> List["object"]:
    """Derive the goal's next decision-lane step as ``RepoImprovementPacket``(s).

    Returns ``[]`` for an empty title. For a goal with no prior artifacts the lane is at
    ``no_pm_brief``; the seed packet is the PM-brief step, risk=RISKY so it surfaces at
    ``awaiting_approval`` (honest: design decisions need operator/PM input, never auto-run).
    """

    from ..decision_lane import readiness as R
    from ..selfimprove import packet as P

    title = (goal_title or "").strip()
    if not title:
        return []

    # Ask the EXISTING decision lane what this goal needs next (no artifacts yet → no_pm_brief).
    rd = R.assess_lane_readiness()
    stage = rd.stage
    next_action = rd.next_actions[0] if getattr(rd, "next_actions", ()) else "PM brief 작성"
    chain = " → ".join(R.STAGE_ORDER)

    # The first step (PM brief / design decision) needs operator/PM input — risky by nature
    # (it is a design decision, not an auto-applicable code change). So it parks the goal at
    # awaiting_approval rather than entering the safe auto-exec queue. This is the honest
    # "operator input still required" signal — not a fake executable packet.
    pkt = P.RepoImprovementPacket(
        finding=f"PM brief 작성: {title}",
        why_it_matters=(
            f"goal 을 실행하려면 결정 레인({chain})을 통과해야 한다. 현재 stage={stage} — "
            f"{next_action}. PM brief 가 확정돼야 tech-lead decision → specialist packet 으로 전개된다."),
        affected_area="planning/decision",
        risk=P.RISK_RISKY,                 # design decision → approval-wait (operator/PM), never auto-run
        proposed_change="PM brief 확정 → design meeting → tech-lead 서명 → specialist handoff",
        confidence=0.6,
        approval_needed=True,
        recommended_owner="product-manager",
        source_origin="goal-intent",
        user_discomfort="goal 만 기록되고 분해/실행이 시작되지 않음(packets: 0)",
    )
    return [pkt]


__all__ = ("intent_packets",)
