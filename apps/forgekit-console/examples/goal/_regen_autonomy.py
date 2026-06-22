"""Regenerate ``autonomy.txt`` — AUTONOMY decisions: collect→decompose→approval→replan.

Deterministic + hermetic: drives ``forgekit_goal.planning`` decisions in memory with a
fixed clock + fixed ids. Shows the autonomous goal-execution core's reasoning — how a big
goal is decomposed by area, how pending work splits into approval-needed vs autonomous-safe,
and how a stuck goal is replanned (bounded retry) then escalated with the reason persisted.
Executes nothing (no repo, no store, no git).

Run: ``python apps/forgekit-console/examples/goal/_regen_autonomy.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
for _rel in ("packages/forgekit-goal/src", "packages/forgekit-config/src"):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_goal import Goal, GoalStatus, planning, transitions

OUT = Path(__file__).resolve().parent / "autonomy.txt"


def _clock():
    n = {"i": 0}

    def now() -> str:
        n["i"] += 1
        return f"2026-06-22T09:00:{n['i']:02d}+00:00"

    return now


def _ids():
    n = {"i": 0}

    def nid() -> str:
        n["i"] += 1
        return f"goal-area{n['i']:02d}"

    return nid


def main() -> None:
    now, nid = _clock(), _ids()
    lines = ["# AUTONOMY goal-execution core — collect → decompose → approval split → replan"]

    # 1) discovery collected work spanning several areas → "big" → autonomous decompose
    discovered = [("docs", "콘솔 도움말 개선"), ("tests", "회귀 케이스 추가"),
                  ("docs", "오타 수정"), ("refactor", "긴 함수 분리")]
    lines.append(f"discovered {len(discovered)} finding(s) across "
                 f"{len({a for a, _ in discovered})} area(s)")
    lines.append(f"is_big_goal -> {planning.is_big_goal(discovered)} (≥2 areas → decompose)")
    steps = planning.derive_plan_steps(discovered)
    lines.append("derive_plan_steps (one child per area):")
    for s in steps:
        lines.append(f"  - {s.title}: {s.intent}")

    parent = transitions.apply(
        Goal.create("ForgeKit 자기개선", goal_id="goal-parent01", now=now),
        GoalStatus.ACTIVE, now=now)
    parent, children = planning.decompose(parent, steps, now=now, new_id=nid)
    lines.append(f"decompose -> parent {parent.id} + {len(children)} child goal:")
    for c in children:
        lines.append(f"  {c.id}  [{c.status.value}]  {c.title}")

    # 2) approval split on a leaf child's pending work
    lines.append("")
    lines.append("# approval-needed vs autonomous-safe (a child's pending packets)")
    safe_child = children[0].link_packet("p-safe", now=now).add_evidence(
        planning.EV_PROPOSAL, "[safe] 콘솔 도움말 개선 -> tech-lead ready", ref="p-safe", now=now)
    risky_child = children[1].link_packet("p-risky", now=now).add_evidence(
        planning.EV_PROPOSAL, "[risky] auth 권한 변경 -> approval-wait", ref="p-risky", now=now)
    lines.append(f"  {safe_child.title}: {planning.approval_disposition(safe_child)} "
                 "(exec tick 자동 실행 가능)")
    lines.append(f"  {risky_child.title}: {planning.approval_disposition(risky_child)} "
                 "(operator 승인 전까지 실행 안 함)")

    # 3) a stuck child → bounded replan RETRY → ESCALATE with reason persisted
    lines.append("")
    lines.append("# replan policy (stuck = gate-blocked packet, nothing pending)")
    stuck = safe_child.add_evidence(
        planning.EV_DECISION, "gate refused: scope creep beyond signed level",
        ref="p-safe", now=now)
    lines.append(f"  is_stuck -> {planning.is_stuck(stuck)}")
    d1 = planning.replan(stuck, max_attempts=1)
    lines.append(f"  replan #1 -> {d1.action}: unlink {list(d1.unlink)}  ({d1.reason})")
    # apply RETRY: unlink the dead packet + record the attempt
    retried = stuck
    for pid in d1.unlink:
        retried = retried.unlink_packet(pid, now=now)
    retried = retried.add_evidence(planning.EV_REPLAN, d1.reason, now=now)
    lines.append(f"  after RETRY: packets={list(retried.packets)} stuck={planning.is_stuck(retried)} "
                 "(dead approach abandoned, re-drivable)")
    # re-block (discovery proposed the same blocked work again) → attempts exhausted → escalate
    reblocked = retried.link_packet("p-safe", now=now).add_evidence(
        planning.EV_PROPOSAL, "[safe] 콘솔 도움말 개선 -> tech-lead ready", ref="p-safe", now=now)
    reblocked = reblocked.add_evidence(
        planning.EV_DECISION, "gate refused again", ref="p-safe", now=now)
    d2 = planning.replan(reblocked, max_attempts=1)
    lines.append(f"  replan #2 -> {d2.action}  ({d2.reason})")
    lines.append(f"  blocked_reason (persisted) -> {planning.blocked_reason(reblocked)!r}")

    lines.append("")
    lines.append("# honest boundary: autonomy decides shape + next move; it executes NOTHING.")
    lines.append("# decompose/derive = plan records, approval split keeps risky work behind the")
    lines.append("# operator, replan NEVER re-runs a gate-refused packet — it abandons (bounded")
    lines.append("# retry) then escalates with the reason persisted. done stays evidence-gated.")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
