"""Regenerate ``autopilot-intent.txt`` — an intent goal seeds its FIRST packet via the tick.

Deterministic + hermetic: a real ``GoalStore`` in a tempdir + the REAL ``GoalSchedulerTicker``
with INJECTED empty discovery (simulating a feature/intent goal with no repo gap). Shows the
"packets: 0 → first packet + evidence + awaiting_approval" transition that closes the stuck
case — driven by the actual runtime tick, not a hand-written record. Executes nothing
(scheduler only links proposal evidence; the seed is risky → never auto-run).

Run: ``python apps/forgekit-console/examples/goal/_regen_autopilot_intent.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
for _rel in ("packages/forgekit-goal/src", "packages/forgekit-runtime/src",
             "packages/forgekit-config/src", "packages/nexus/src", "packages/armory/src",
             "packages/hephaistos/src", "apps/forgekit-console/src"):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_goal import Goal, GoalStatus, GoalStore, transitions
from forgekit_runtime.runtime.goal_scheduler_tick import GoalSchedulerTicker
from forgekit_runtime.selfimprove.loop import SelfImprovementResult
from forgekit_console.goal_surface import goal_show_lines

OUT = Path(__file__).resolve().parent / "autopilot-intent.txt"


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    env = {"FORGEKIT_HOME": str(tmp)}
    store = GoalStore(env=env)

    # an operator-activated INTENT goal (work is not a repo gap)
    goal = transitions.apply(
        Goal.create("외부 결제 연동 기능을 설계하고 구현한다", mode="auto"), GoalStatus.ACTIVE)
    store.save(goal)

    lines = ["# AUTOPILOT exec core — intent goal seeds its FIRST packet (no longer stuck at 0)"]
    b = store.get(goal.id)
    lines.append(f"before tick: {b.id} [{b.status.value}]  "
                 f"packets: {len(b.packets)}  children: {len(b.children)}  evidence: {len(b.evidence)}")
    lines.append("  (operator 가 goal 만 활성화 — 추가 prompt 없음. discovery 는 repo gap 0)")

    # the REAL scheduler tick with empty discovery → intent seed fires
    ticker = GoalSchedulerTicker(repo_root=tmp, env=env, store=store,
                                 discover=lambda _r: SelfImprovementResult(packets=[]))
    out = ticker.tick(1)
    lines.append("")
    lines.append(f"runtime tick → {out.summary}  (waiting={out.waiting})")

    a = store.get(goal.id)
    lines.append("")
    lines.append(f"after tick:  {a.id} [{a.status.value}]  "
                 f"packets: {len(a.packets)}  children: {len(a.children)}  evidence: {len(a.evidence)}")
    for e in a.evidence:
        lines.append(f"  evidence[{e.kind}] {e.summary}  (ref={e.ref})")

    lines.append("")
    lines.append("# /goal show (consistent surface — same store)")
    for ln in goal_show_lines(env, goal.id):
        lines.append("  " + ln.replace("[b]", "").replace("[/b]", ""))

    lines.append("")
    lines.append("# 자동 vs operator 입력 (정직)")
    lines.append("# - 자동: activate 된 goal → tick 이 첫 packet + proposal evidence 생성, 상태 surface")
    lines.append("# - operator 입력 필요: PM brief/design 결정(risky seed → awaiting_approval), 그리고 risky packet approve")
    lines.append("# - 실행 0: 이 tick 은 packet 을 링크/기록만 — 물리 실행은 exec tick(safe-class) + 승인 게이트")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
