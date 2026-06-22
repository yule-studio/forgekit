"""/goal operator surface (GW5) — thin render/CRUD over ``forgekit_goal``.

Console stays a *surface* (ownership §3.1): this module only renders goals and
applies small store mutations (create / activate) by calling the ``forgekit_goal``
core. It owns NO goal logic — the model, transitions, and persistence live in the
package. Goals are read from / written to the same store the runtime uses
(``<FORGEKIT_HOME>/goals``), so what the operator sees here is what the goal-tick
(GW4) writes.

The autonomous tick (collect → propose → evidence) is the runtime's job
(``forgekit_runtime.selfimprove.goal_tick``, GW4) — this surface intentionally
does NOT run it inline (keeps the command router pure / IO-light). It shows the
goal and its accumulated packets/evidence; ``/goal new`` / ``/goal activate`` are
the only mutations, both pure store writes.
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple

from forgekit_goal import Goal, GoalStatus, GoalStore, transitions


def _store(env: Optional[Mapping[str, str]]) -> GoalStore:
    return GoalStore(env=env)


def goal_list_lines(env: Optional[Mapping[str, str]]) -> Tuple[str, ...]:
    goals = _store(env).load_all()
    if not goals:
        return ("등록된 goal 없음 — `/goal new <제목>` 으로 생성",)
    out = [f"{len(goals)} goal (updated 최신순):"]
    for g in goals:
        out.append(f"  {g.id}  [{g.status.value}]  {g.title}")
    return tuple(out)


def goal_show_lines(env: Optional[Mapping[str, str]], gid: str) -> Tuple[str, ...]:
    g = _store(env).get((gid or "").strip())
    if g is None:
        return (f"goal {gid!r} 없음 — `/goal list` 로 확인",)
    out = [
        f"{g.id}  [{g.status.value}]  {g.title}",
        f"  intent: {g.intent or '-'}",
        f"  mode: {g.mode or 'inherit'}   packets: {len(g.packets)}   "
        f"children: {len(g.children)}   evidence: {len(g.evidence)}",
    ]
    if g.packets:
        out.append("  linked packets: " + ", ".join(g.packets))
    if g.evidence:
        out.append("  recent evidence:")
        for e in g.evidence[-5:]:
            ref = f"  ({e.ref})" if e.ref else ""
            out.append(f"    - [{e.kind}] {e.summary}{ref}")
    return tuple(out)


def goal_evidence_lines(env: Optional[Mapping[str, str]], gid: str) -> Tuple[str, ...]:
    g = _store(env).get((gid or "").strip())
    if g is None:
        return (f"goal {gid!r} 없음",)
    if not g.evidence:
        return (f"{g.id}: evidence 없음",)
    out = [f"{g.id} evidence ({len(g.evidence)}):"]
    for e in g.evidence:
        ref = f"  ({e.ref})" if e.ref else ""
        out.append(f"  - {e.ts}  [{e.kind}] {e.summary}{ref}")
    return tuple(out)


def apply_new(env: Optional[Mapping[str, str]], title: str) -> Tuple[bool, str]:
    title = (title or "").strip()
    if not title:
        return False, "제목이 필요합니다 — `/goal new <제목>`"
    g = Goal.create(title)
    _store(env).save(g)
    return True, f"goal 생성: {g.id}  [{g.status.value}]  {g.title}"


def apply_activate(env: Optional[Mapping[str, str]], gid: str) -> Tuple[bool, str]:
    st = _store(env)
    g = st.get((gid or "").strip())
    if g is None:
        return False, f"goal {gid!r} 없음"
    try:
        g2 = transitions.apply(g, GoalStatus.ACTIVE)
    except transitions.InvalidTransition as exc:
        return False, str(exc)
    st.save(g2)
    return True, f"{g2.id} -> {g2.status.value}"


# --- in-console approve / deny (operator cockpit, GW4 gap) -------------------
# Goals the runtime parks in ``awaiting_approval`` (a risky/restricted packet needs the
# operator) surface here so the operator can decide IN the console — the last operator
# cockpit parity gap. The decision is a LEGAL status transition + an append-only
# ``decision`` evidence record; the surface owns no goal logic (ownership §3.1).
#
# Honest execution boundary: approving records the operator decision and (if the GW4-B
# execution bridge from gw1 is merged) triggers it. Until that bridge exists, approve is
# "승인됨(실행 대기)" — never a fake "executed". The bridge is looked up lazily so it lights
# up automatically once gw1 lands it, with no change here.

def awaiting_lines(env: Optional[Mapping[str, str]]) -> Tuple[str, ...]:
    """List goals in ``awaiting_approval`` + their linked packets + the action hint."""

    goals = [g for g in _store(env).load_all() if g.status == GoalStatus.AWAITING_APPROVAL]
    if not goals:
        return ("승인 대기 goal 없음 — runtime 이 risky/restricted packet 을 만들면 "
                "awaiting_approval 로 여기 모입니다.",)
    out = [f"{len(goals)} goal 승인 대기:"]
    for g in goals:
        out.append(f"  {g.id}  {g.title}")
        if g.packets:
            out.append("    linked packets: " + ", ".join(g.packets))
        out.append(f"    승인: `/goal approve {g.id} [메모]`   거부: `/goal deny {g.id} [메모]`")
    return tuple(out)


def _decision_summary(decision: str, note: str) -> str:
    note = (note or "").strip()
    return f"operator {decision}" + (f": {note}" if note else "")


def _try_execute_bridge(goal, env) -> Tuple[bool, str]:
    """Call the GW4-B execution bridge (gw1) if it is merged; else honest no-op.

    Lazy, defensive import so this surface works before the bridge exists AND lights up
    automatically once it lands — without owning any execution logic itself."""

    fn = None
    try:  # canonical home once gw1 GW4-B merges
        from forgekit_runtime.selfimprove import execute_approved_packet as fn  # type: ignore
    except Exception:  # noqa: BLE001
        try:
            from forgekit_runtime.decision_lane import execute_approved_packet as fn  # type: ignore
        except Exception:  # noqa: BLE001
            fn = None
    if fn is None:
        return False, ""
    try:
        result = fn(goal, env=env)
        return True, f"실행 bridge 호출됨: {result}"
    except Exception as exc:  # noqa: BLE001 - bridge failure must not corrupt the decision
        return False, f"실행 bridge 오류: {exc}"


def apply_approve(env: Optional[Mapping[str, str]], gid: str, note: str = "") -> Tuple[bool, str]:
    """Approve an awaiting goal: ``awaiting_approval -> active`` + decision evidence.

    Attempts the GW4-B execution bridge; if absent, returns "승인됨(실행 대기)" honestly."""

    st = _store(env)
    g = st.get((gid or "").strip())
    if g is None:
        return False, f"goal {gid!r} 없음 — `/goal awaiting` 로 확인"
    if g.status != GoalStatus.AWAITING_APPROVAL:
        return False, (f"{g.id} 는 승인 대기 아님(현재 {g.status.value}) — "
                       "approve 는 awaiting_approval goal 만 대상")
    try:
        g2 = transitions.apply(g, GoalStatus.ACTIVE)
    except transitions.InvalidTransition as exc:
        return False, str(exc)
    g2 = g2.add_evidence("decision", _decision_summary("승인", note))
    executed, bridge_note = _try_execute_bridge(g2, env)
    if executed:
        g2 = g2.add_evidence("execution", bridge_note)
    st.save(g2)
    tail = bridge_note if executed else "승인됨(실행 대기 — GW4-B 실행 bridge 미연결)"
    return True, f"{g2.id} 승인 -> {g2.status.value}  · {tail}"


def apply_deny(env: Optional[Mapping[str, str]], gid: str, note: str = "") -> Tuple[bool, str]:
    """Deny an awaiting goal: ``awaiting_approval -> blocked`` + decision evidence."""

    st = _store(env)
    g = st.get((gid or "").strip())
    if g is None:
        return False, f"goal {gid!r} 없음 — `/goal awaiting` 로 확인"
    if g.status != GoalStatus.AWAITING_APPROVAL:
        return False, (f"{g.id} 는 승인 대기 아님(현재 {g.status.value}) — "
                       "deny 는 awaiting_approval goal 만 대상")
    try:
        g2 = transitions.apply(g, GoalStatus.BLOCKED)
    except transitions.InvalidTransition as exc:
        return False, str(exc)
    g2 = g2.add_evidence("decision", _decision_summary("거부", note))
    st.save(g2)
    return True, f"{g2.id} 거부 -> {g2.status.value}  · 거부됨(blocked, 재검토 가능)"


def usage_lines() -> Tuple[str, ...]:
    return (
        "`/goal` — 장기 목표 control plane (forgekit_goal)",
        "  /goal [list]          등록된 goal 목록",
        "  /goal new <제목>       새 goal(draft) 생성",
        "  /goal show <id>       goal 상세(status/packets/evidence)",
        "  /goal activate <id>   draft/blocked -> active",
        "  /goal evidence <id>   evidence(append-only) 목록",
        "  /goal awaiting        승인 대기(awaiting_approval) goal + linked packets",
        "  /goal approve <id> [메모]  승인 -> active + decision evidence (실행은 GW4-B)",
        "  /goal deny <id> [메모]     거부 -> blocked + decision evidence",
        "  tick(수집→제안→evidence)은 runtime daemon 에서 실행 (GW4)",
    )


__all__ = (
    "goal_list_lines", "goal_show_lines", "goal_evidence_lines",
    "awaiting_lines", "apply_new", "apply_activate",
    "apply_approve", "apply_deny", "usage_lines",
)
