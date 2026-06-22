"""Regenerate ``planning.txt`` — GW-EXEC decomposition + continuation evidence.

Deterministic + hermetic: builds Goal objects in memory with a fixed clock and a
fixed child-id generator, exercises ``forgekit_goal.planning`` (decompose →
progress → continuation), and writes the rendered transcript. Executes nothing
(no repo, no store) — decomposition only creates plan records, and progress /
continuation are derived from append-only evidence.

Run: ``python apps/forgekit-console/examples/goal/_regen_planning.py``
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

OUT = Path(__file__).resolve().parent / "planning.txt"


def _clock():
    n = {"i": 0}

    def now() -> str:
        n["i"] += 1
        return f"2026-06-22T07:00:{n['i']:02d}+00:00"

    return now


def _ids():
    n = {"i": 0}

    def nid() -> str:
        n["i"] += 1
        return f"goal-step{n['i']:02d}"

    return nid


def _complete(child: Goal, pid: str, now) -> Goal:
    """Mark a child as really executed+verified (what the gated exec tick writes)."""

    c = child.link_packet(pid, now=now).add_evidence(
        planning.EV_PROPOSAL, f"[safe] {pid} -> route", ref=pid, now=now)
    c = transitions.apply(c, GoalStatus.ACTIVE, now=now)
    c = c.add_evidence(planning.EV_EXECUTION, "bounded write + commit", ref=pid, now=now)
    c = c.add_evidence(planning.EV_VERIFICATION, "re-read verified", ref=pid, now=now)
    return transitions.apply(c, GoalStatus.DONE, now=now)


def main() -> None:
    now, nid = _clock(), _ids()
    lines = ["# GW-EXEC goal planning evidence — decomposition + continuation (executes nothing)"]

    # 1) decompose a big goal into an ordered plan of child goals
    parent = transitions.apply(
        Goal.create("DB 스토리지 마이그레이션", goal_id="goal-parent01", now=now),
        GoalStatus.ACTIVE, now=now)
    steps = [planning.PlanStep("스키마 설계"), planning.PlanStep("마이그레이션 작성"),
             planning.PlanStep("회귀 테스트")]
    parent, children = planning.decompose(parent, steps, now=now, new_id=nid)
    lines.append(f"goal {parent.id} status={parent.status.value}")
    lines.append(f"decompose -> {len(children)} child step(s):")
    for c in children:
        lines.append(f"  {c.id}  [{c.status.value}]  {c.title}")
    plan_ev = [e for e in parent.evidence if e.kind == planning.EV_PLAN][0]
    lines.append(f"parent plan evidence: {plan_ev.summary}")

    # 2) first step still draft → continuation ADVANCEs it (sequential)
    act = planning.continuation_action(parent, list(children))
    prog = planning.progress(parent, list(children))
    lines.append("")
    lines.append(f"progress: {prog.summary}  ({prog.done_steps}/{prog.total_steps})")
    lines.append(f"continuation: {act.kind} -> {act.target_id}  ({act.reason})")

    # 3) first step really executed+verified → it rolls up to done; cursor moves on
    children = list(children)
    children[0] = _complete(children[0], "packet-aaa", now)
    act = planning.continuation_action(parent, children)
    prog = planning.progress(parent, children)
    lines.append("")
    lines.append("# step 1 executed+verified (gated exec tick wrote execution+verification)")
    lines.append(f"progress: {prog.summary}  ({prog.done_steps}/{prog.total_steps})")
    lines.append(f"continuation: {act.kind} -> {act.target_id}  ({act.reason})")

    # 4) all steps done → continuation COMPLETEs the parent (evidence-gated)
    children[1] = _complete(children[1], "packet-bbb", now)
    children[2] = _complete(children[2], "packet-ccc", now)
    act = planning.continuation_action(parent, children)
    prog = planning.progress(parent, children)
    lines.append("")
    lines.append("# every step done with real evidence")
    lines.append(f"progress: {prog.summary}  ({prog.done_steps}/{prog.total_steps})  complete={prog.complete}")
    lines.append(f"continuation: {act.kind} -> {act.target_id}  ({act.reason})")
    parent_done = transitions.apply(
        parent.add_evidence(planning.EV_VERIFICATION, "all step done — goal complete", now=now),
        GoalStatus.DONE, now=now)
    lines.append(f"parent {parent_done.id} -> {parent_done.status.value}  (long-term goal CLOSED)")

    lines.append("")
    lines.append("# honest boundary: planning creates plan records + derives progress from")
    lines.append("# append-only evidence. It executes nothing. A child closes only with real")
    lines.append("# execution+verification evidence (no fake-green); a blocked step surfaces as")
    lines.append("# REPLAN (operator), never an auto-advance — the approval chain is not bypassed.")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
