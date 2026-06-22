"""Lane readiness gate — "실행 전에 무엇이 확정돼야 하는가" as an enforced, visible artifact.

The decision-lane has the artifacts (PMBrief / MeetingRecord / TechLeadDecision /
EngineerHandoff) and their validators; this composes them into the ONE question an
operator and the runtime both ask before a specialist runs: *given what exists so far,
which preconditions are confirmed, which are still missing, and is execution permitted?*

It hard-encodes the chain order so the governance is real, not decorative:

* **no PM brief → the tech-lead lane is not executable** (and says so) — a specialist
  cannot look ready off a missing product artifact;
* **no signed tech-lead decision → specialist execution is impossible**;
* only a valid brief → meeting → signed decision → valid handoff yields ``executable``.

``executable`` agrees with :func:`can_engineer_start` by construction (both require the
same signed decision + valid handoff). Pure — no I/O; the log/surface layers persist and
render this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .schemas import (
    CONDITIONAL,
    SIGNED_OFF,
    EngineerHandoff,
    MeetingRecord,
    PMBrief,
    TechLeadDecision,
)
from .validators import (
    validate_handoff,
    validate_meeting,
    validate_pm_brief,
    validate_tech_lead_decision,
)

# ordered lane stages — index = how far the chain has been confirmed
STAGE_NO_PM_BRIEF = "no_pm_brief"
STAGE_MEETING_PENDING = "meeting_pending"
STAGE_DECISION_PENDING = "decision_pending"
STAGE_HANDOFF_PENDING = "handoff_pending"
STAGE_EXECUTABLE = "executable"
STAGE_ORDER: Tuple[str, ...] = (
    STAGE_NO_PM_BRIEF, STAGE_MEETING_PENDING, STAGE_DECISION_PENDING,
    STAGE_HANDOFF_PENDING, STAGE_EXECUTABLE,
)


@dataclass(frozen=True)
class LaneReadiness:
    """What's confirmed, what's missing, and whether a specialist may execute — the
    operator-visible precondition artifact."""

    stage: str
    executable: bool
    confirmed: Tuple[str, ...] = ()      # preconditions already locked (in order)
    missing: Tuple[str, ...] = ()        # what must still be confirmed before execution
    blocking: Tuple[str, ...] = ()       # validator violations on a present-but-invalid artifact
    next_action: str = ""

    def progress(self) -> str:
        try:
            return f"{STAGE_ORDER.index(self.stage) + 1}/{len(STAGE_ORDER)}"
        except ValueError:
            return "-"

    def to_dict(self) -> dict:
        return {"stage": self.stage, "executable": self.executable,
                "confirmed": list(self.confirmed), "missing": list(self.missing),
                "blocking": list(self.blocking), "next_action": self.next_action,
                "progress": self.progress()}

    def lines(self) -> Tuple[str, ...]:
        out = [
            f"lane readiness — {self.stage} ({self.progress()}) · "
            + ("실행 가능" if self.executable else "실행 불가"),
        ]
        for c in self.confirmed:
            out.append(f"  ✓ {c}")
        for m in self.missing:
            out.append(f"  ☐ 필요: {m}")
        for b in self.blocking:
            out.append(f"  ✗ 보완: {b}")
        if self.next_action:
            out.append(f"  → 다음: {self.next_action}")
        return tuple(out)


def assess_lane_readiness(
    *,
    brief: Optional[PMBrief] = None,
    meeting: Optional[MeetingRecord] = None,
    decision: Optional[TechLeadDecision] = None,
    handoff: Optional[EngineerHandoff] = None,
) -> LaneReadiness:
    """Compute the readiness from whatever artifacts exist. Enforces the chain order."""

    confirmed = []

    # 1. PM artifact — without it the tech-lead lane is NOT executable.
    if brief is None:
        return LaneReadiness(
            STAGE_NO_PM_BRIEF, False, (),
            ("PM brief — 문제 / 사용자가치 / acceptance / 성공지표",), (),
            "PM 이 brief 를 확정해야 tech-lead lane 진입 가능 (PM artifact 없이는 실행 불가)")
    bviol = validate_pm_brief(brief)
    if bviol:
        return LaneReadiness(
            STAGE_NO_PM_BRIEF, False, (), ("유효한 PM brief",), bviol, "PM brief 보완")
    confirmed.append(f"PM brief: {brief.topic}")

    # 2. real meeting (no rubber-stamp consensus).
    if meeting is None:
        return LaneReadiness(
            STAGE_MEETING_PENDING, False, tuple(confirmed),
            ("실재 design meeting — ≥2 역할 · 반대/우려",), (), "design meeting 소집")
    mviol = validate_meeting(meeting)
    if mviol:
        return LaneReadiness(
            STAGE_MEETING_PENDING, False, tuple(confirmed),
            ("유효한 meeting",), mviol, "meeting 보완 (rubber-stamp 금지)")
    confirmed.append(f"meeting: {meeting.meeting_id} ({len(meeting.participants)} 참석)")

    # 3. signed tech-lead decision — without it the specialist CANNOT execute.
    if decision is None:
        return LaneReadiness(
            STAGE_DECISION_PENDING, False, tuple(confirmed),
            ("tech-lead decision — design system+convention+stack+tradeoff+approval, 서명",), (),
            "tech-lead 서명 필요 — decision 없이는 specialist 실행 불가")
    dviol = validate_tech_lead_decision(decision)
    if dviol or decision.status not in (SIGNED_OFF, CONDITIONAL):
        block = tuple(dviol) or (f"decision status={decision.status} (미서명)",)
        return LaneReadiness(
            STAGE_DECISION_PENDING, False, tuple(confirmed),
            ("서명된 tech-lead decision",), block, "tech-lead 서명/보완")
    confirmed.append(
        f"tech-lead decision: {decision.decision_id} ({decision.status}/{decision.approval_level})")

    # 4. single-executor handoff.
    if handoff is None:
        return LaneReadiness(
            STAGE_HANDOFF_PENDING, False, tuple(confirmed),
            ("engineer handoff — 단일 executor · scope · test 전략",), (),
            "tech-lead → engineer handoff 발행")
    hviol = validate_handoff(handoff, decision)
    if hviol:
        return LaneReadiness(
            STAGE_HANDOFF_PENDING, False, tuple(confirmed),
            ("유효한 handoff",), hviol, "handoff 보완 (단일 engineer executor)")
    confirmed.append(f"handoff: {handoff.handoff_id} → {handoff.executor_role}")

    # 5. all preconditions confirmed → executable.
    return LaneReadiness(
        STAGE_EXECUTABLE, True, tuple(confirmed), (), (),
        "specialist 실행 인가 — 모든 precondition 확정, execution gate 통과 가능")


__all__ = (
    "STAGE_NO_PM_BRIEF", "STAGE_MEETING_PENDING", "STAGE_DECISION_PENDING",
    "STAGE_HANDOFF_PENDING", "STAGE_EXECUTABLE", "STAGE_ORDER",
    "LaneReadiness", "assess_lane_readiness",
)
