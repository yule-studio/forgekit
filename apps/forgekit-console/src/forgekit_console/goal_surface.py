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


def usage_lines() -> Tuple[str, ...]:
    return (
        "`/goal` — 장기 목표 control plane (forgekit_goal)",
        "  /goal [list]          등록된 goal 목록",
        "  /goal new <제목>       새 goal(draft) 생성",
        "  /goal show <id>       goal 상세(status/packets/evidence)",
        "  /goal activate <id>   draft/blocked -> active",
        "  /goal evidence <id>   evidence(append-only) 목록",
        "  tick(수집→제안→evidence)은 runtime daemon 에서 실행 (GW4)",
    )


__all__ = (
    "goal_list_lines", "goal_show_lines", "goal_evidence_lines",
    "apply_new", "apply_activate", "usage_lines",
)
