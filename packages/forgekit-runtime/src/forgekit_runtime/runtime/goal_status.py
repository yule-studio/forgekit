"""Goal-continuity status — operator-visible view of what the always-on runtime is doing.

The serve loop physically advances ACTIVE goals (``goal_exec_tick.GoalExecTicker``), but the
runtime status surface only showed the daemon heartbeat — an operator could not see, from the
runtime, whether goals are *progressing*, how many are *awaiting approval* (action-needed), or
what was *last executed*. This module reads the real ``GoalStore`` and answers exactly that, so
``forgekit runtime status`` / ``/daemon`` surface the goal-driven continuity honestly.

Honesty rails:
- reads the REAL goal store; no goal package / no store → an honest "store 없음" status (no fake).
- ``awaiting_approval`` goals are surfaced as **action-needed** (the operator decides via
  ``/goal approve``); ``blocked`` separately. We never invent progress.
- "last executed" is the most recent ``execution``/``verification`` evidence across goals —
  what the bounded loop ACTUALLY did, not a projection.

``forgekit_goal`` is imported lazily so the runtime stays importable standalone. Pure given a
store → unit-testable with a tempdir store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Tuple

# evidence kinds that represent real runtime work done toward a goal.
_WORK_KINDS = ("execution", "verification")


@dataclass(frozen=True)
class GoalContinuityStatus:
    """Honest snapshot of goal-driven continuity for the runtime status surface."""

    available: bool = True                 # goal store readable (else honest "no store")
    total: int = 0
    active: int = 0                        # ACTIVE — the serve loop auto-advances these
    awaiting_approval: int = 0             # action-needed: operator /goal approve
    blocked: int = 0
    done: int = 0
    last_work: str = ""                    # most recent execution/verification summary (trimmed)
    last_work_goal: str = ""               # the goal id that work belongs to
    reason: str = ""

    def to_dict(self) -> dict:
        return {"available": self.available, "total": self.total, "active": self.active,
                "awaiting_approval": self.awaiting_approval, "blocked": self.blocked,
                "done": self.done, "last_work": self.last_work,
                "last_work_goal": self.last_work_goal, "reason": self.reason}


def goal_continuity_status(*, env: Optional[Mapping[str, str]] = None,
                           store=None) -> GoalContinuityStatus:
    """Read the goal store and summarise goal-driven continuity (honest; no fake)."""

    if store is None:
        try:
            from forgekit_goal import GoalStore  # lazy / best-effort
        except Exception:  # noqa: BLE001 - goal package absent
            return GoalContinuityStatus(available=False, reason="goal 패키지 없음")
        store = GoalStore(env=env)
    try:
        from forgekit_goal import GoalStatus
        goals = store.load_all()
    except Exception:  # noqa: BLE001 - store unreadable → honest unavailable
        return GoalContinuityStatus(available=False, reason="goal store 읽기 불가")

    counts = {GoalStatus.ACTIVE: 0, GoalStatus.AWAITING_APPROVAL: 0,
              GoalStatus.BLOCKED: 0, GoalStatus.DONE: 0}
    last_ts = ""
    last_work = ""
    last_goal = ""
    for g in goals:
        if g.status in counts:
            counts[g.status] += 1
        for ev in g.evidence:
            if ev.kind in _WORK_KINDS and ev.ts >= last_ts:   # ISO ts → lexicographic max = newest
                last_ts, last_work, last_goal = ev.ts, ev.summary, g.id
    return GoalContinuityStatus(
        available=True, total=len(goals),
        active=counts[GoalStatus.ACTIVE], awaiting_approval=counts[GoalStatus.AWAITING_APPROVAL],
        blocked=counts[GoalStatus.BLOCKED], done=counts[GoalStatus.DONE],
        last_work=(last_work[:80] if last_work else ""), last_work_goal=last_goal,
        reason="ok")


def goal_continuity_lines(*, env: Optional[Mapping[str, str]] = None, store=None) -> Tuple[str, ...]:
    """Operator-visible goal-continuity lines for the runtime/daemon status surface."""

    st = goal_continuity_status(env=env, store=store)
    if not st.available:
        return (f"  goal-loop : (goal store 없음 — {st.reason})",)
    if st.total == 0:
        return ("  goal-loop : 활성 goal 없음 (`/goal new <제목>` → `/goal activate <id>` 로 시작)",)
    head = (f"  goal-loop : active {st.active} (serve 가 자동 진행) · "
            f"awaiting {st.awaiting_approval} (operator 승인 필요) · "
            f"blocked {st.blocked} · done {st.done}")
    lines: List[str] = [head]
    if st.awaiting_approval:
        lines.append(f"      ⚠ action-needed: {st.awaiting_approval} goal awaiting_approval — `/goal awaiting` → `/goal approve <id>`")
    if st.last_work:
        lines.append(f"      last work: {st.last_work}" + (f" ({st.last_work_goal})" if st.last_work_goal else ""))
    elif st.active:
        lines.append("      (아직 실행 evidence 없음 — 다음 serve tick 에서 safe packet 진행)")
    return tuple(lines)


__all__ = ("GoalContinuityStatus", "goal_continuity_status", "goal_continuity_lines")
