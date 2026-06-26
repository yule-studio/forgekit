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
goal and its accumulated packets/evidence; the mutations (``/goal new`` /
``/goal plan`` / ``/goal activate`` / ``/goal approve`` / ``/goal deny``) are pure
store writes (``plan`` decomposes into child goals via ``forgekit_goal.planning``,
which executes nothing). ``/goal progress`` renders the planning layer's derived
progress + next continuation action read-only.
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence, Tuple

from forgekit_goal import Goal, GoalStatus, GoalStore, planning, transitions


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
    st = _store(env)
    g = st.get((gid or "").strip())
    if g is None:
        return (f"goal {gid!r} 없음 — `/goal list` 로 확인",)
    out = [
        f"{g.id}  [{g.status.value}]  {g.title}",
        f"  intent: {g.intent or '-'}",
        f"  mode: {g.mode or 'inherit'}   packets: {len(g.packets)}   "
        f"children: {len(g.children)}   evidence: {len(g.evidence)}",
    ]
    if g.parent_id:
        out.append(f"  parent: {g.parent_id}")
    if g.children:
        kids = [st.get(c) for c in g.children]
        prog = planning.progress(g, [k for k in kids if k is not None])
        out.append(f"  plan: {prog.summary}")
        out.append("  steps:")
        for cid, kid in zip(g.children, kids):
            if kid is None:
                out.append(f"    - {cid}  (없음)")
            else:
                out.append(f"    - {kid.id}  [{kid.status.value}]  {kid.title}")
    if g.packets:
        out.append("  linked packets: " + ", ".join(g.packets))
    # persisted stuck reason so the operator sees WHY a goal is blocked (append-only)
    reason = planning.blocked_reason(g)
    if reason and planning.is_stuck(g):
        out.append(f"  막힘 사유: {reason}")
    if g.evidence:
        out.append("  recent evidence:")
        for e in g.evidence[-5:]:
            ref = f"  ({e.ref})" if e.ref else ""
            out.append(f"    - [{e.kind}] {e.summary}{ref}")
    return tuple(out)


def progress_lines(env: Optional[Mapping[str, str]], gid: str) -> Tuple[str, ...]:
    """Render a goal's progress + the single next action (continuation)."""

    st = _store(env)
    g = st.get((gid or "").strip())
    if g is None:
        return (f"goal {gid!r} 없음 — `/goal list` 로 확인",)
    kids = [k for k in (st.get(c) for c in g.children) if k is not None]
    prog = planning.progress(g, kids)
    pct = int(round(prog.ratio * 100))
    out = [
        f"{g.id}  [{g.status.value}]  {g.title}",
        f"  진척: {prog.done_steps}/{prog.total_steps} ({pct}%)  · {prog.summary}",
    ]
    if g.children:
        action = planning.continuation_action(g, kids)
        out.append(f"  다음: {action.kind} — {action.reason}")
    elif prog.next_step_id:
        out.append(f"  다음 packet: {prog.next_step_id}")
    # approval-needed vs autonomous-safe split (leaf goals' pending work)
    dispo = planning.approval_disposition(g)
    if dispo == planning.NEEDS_APPROVAL:
        out.append("  승인 필요: pending packet 에 risky/blocked 포함 — operator 승인 후 실행")
    elif dispo == planning.AUTONOMOUS_SAFE:
        out.append("  자율 안전: pending packet 전부 safe — exec tick 이 자동 실행")
    # persisted stuck/blocked reason (survives restarts, append-only)
    if planning.is_stuck(g):
        out.append(f"  ⚠️ 막힘: {planning.blocked_reason(g) or '게이트 차단'}  (replan/escalate 대상)")
    if prog.complete:
        out.append("  ✅ 모든 step 완료 — goal 종료 가능(evidence-gated)")
    return tuple(out)


def govern_lines(env: Optional[Mapping[str, str]], gid: str) -> Tuple[str, ...]:
    """`/goal govern <id>` — the goal's design-chain readiness (PM→tech-lead→specialist).

    Reads the goal's governance log (keyed by goal.id) via the runtime binding and renders
    the lane-readiness ladder + the next required design artifact. Honest: a governance-
    required goal whose chain is incomplete shows '설계 진행 중' and what's still missing —
    specialist execution stays blocked until executable (설계 없는 구현 금지)."""

    g = _store(env).get((gid or "").strip())
    if g is None:
        return (f"goal {gid!r} 없음 — `/goal list` 로 확인",)
    try:
        from forgekit_runtime.runtime import goal_governance as gov
    except Exception as e:  # noqa: BLE001 - runtime optional; surface must not crash
        return (f"goal governance 미가용: {e}",)
    return gov.governance_lines(g, env=env)


def goal_evidence_lines(env: Optional[Mapping[str, str]], gid: str) -> Tuple[str, ...]:
    g = _store(env).get((gid or "").strip())
    if g is None:
        return (f"goal {gid!r} 없음",)
    if not g.evidence:
        return (f"{g.id}: evidence 없음",)
    nexus_notes = sum(1 for e in g.evidence if e.kind == _MIRROR_KIND)
    head = f"{g.id} evidence ({len(g.evidence)}"
    head += f", Nexus 기록 {nexus_notes}건)" if nexus_notes else ")"
    out = [head + ":"]
    for e in g.evidence:
        ref = f"  ({e.ref})" if e.ref else ""
        marker = " ⮕nexus" if e.kind == _MIRROR_KIND else ""
        out.append(f"  - {e.ts}  [{e.kind}]{marker} {e.summary}{ref}")
    if not nexus_notes:
        out.append("  (`/goal publish <id>` 로 Nexus evidence 축에 실제 artifact 로 기록)")
    return tuple(out)


# --- goal evidence → Nexus artifact bridge ----------------------------------
# Goal progression records append-only EvidenceRecords (proposal/execution/decision…),
# but those live only in the goal store — `children/evidence: 0`-looking from the Nexus
# side. `publish` MIRRORS each not-yet-mirrored record into the Nexus evidence axis as an
# authored note carrying the fixed schema (goal_id/lane/packet_id/role/status/created_at/
# evidence_path), then appends a `nexus-note` EvidenceRecord whose ref IS the note path — so
# `/goal evidence` grows with REAL artifacts. Idempotent (re-publish only mirrors new
# records) and honest (no vault → no write, never a fake path).
_MIRROR_KIND = "nexus-note"
_PUBLISHABLE = ("proposal", "execution", "verification", "decision", "observation", "plan")

# evidence kind → canonical role id (drives the authored note's colour/visibility).
_KIND_ROLE = {
    "proposal": "product-manager", "plan": "tech-lead",
    "execution": "platform-runtime-engineer", "verification": "qa-engineer",
    "decision": "gateway", "observation": "user-researcher",
}
_SRC_TS_MARK = "src_ts="


def _mirrored_src_ts(goal) -> set:
    """ts values of source records already mirrored (parsed from nexus-note summaries)."""

    out = set()
    for e in goal.evidence:
        if e.kind == _MIRROR_KIND and _SRC_TS_MARK in (e.summary or ""):
            out.add(e.summary.split(_SRC_TS_MARK, 1)[1].split()[0])
    return out


def apply_publish_evidence(
    env: Optional[Mapping[str, str]],
    gid: str,
    config: Optional[Mapping] = None,
) -> Tuple[bool, Tuple[str, ...]]:
    """Mirror a goal's evidence records into the Nexus evidence axis (real artifacts).

    Returns ``(ok, lines)``. No connected vault → honest failure (no fake write). Already
    fully mirrored → ok with a "nothing new" line. Each newly written note carries the
    fixed evidence schema and links back; the goal gains a ``nexus-note`` record per note."""

    from nexus.vault import EvidenceMeta, LANE_GOAL, write_evidence_note

    st = _store(env)
    g = st.get((gid or "").strip())
    if g is None:
        return False, (f"goal {gid!r} 없음 — `/goal list` 로 확인",)

    try:
        from hephaistos.nexus_read import nexus_root
    except Exception:  # noqa: BLE001
        nexus_root = lambda *a, **k: None  # noqa: E731
    root = nexus_root(env, config)
    if not root:
        return False, ("Nexus vault 미연결 — `/nexus set <path>` 후 publish 가능 (fake-write 안 함).",)

    already = _mirrored_src_ts(g)
    pending = [(i, e) for i, e in enumerate(g.evidence)
               if e.kind in _PUBLISHABLE and e.ts not in already]
    if not pending:
        return True, (f"{g.id}: 새로 mirror 할 evidence 없음 (이미 {len(_mirrored_src_ts(g))}건 Nexus 기록).",)

    written: list = []
    g2 = g
    for i, e in pending:
        role = _KIND_ROLE.get(e.kind, "knowledge-engineer")
        packet_id = e.ref if (e.ref and e.kind in ("proposal", "execution", "verification")) else ""
        meta = EvidenceMeta(goal_id=g.id, lane=LANE_GOAL, packet_id=packet_id, role=role,
                            source="goal-progression", status=g.status.value, created_at=e.ts)
        res = write_evidence_note(
            meta, root, title=f"{g.title} · {e.kind}", summary=e.summary,
            slug=f"{e.kind}-{i:03d}",
            sections=[("source record", f"[{e.kind}] {e.summary}"
                       + (f" (ref={e.ref})" if e.ref else ""))])
        if not res:
            continue
        path, _m = res
        g2 = g2.add_evidence(
            _MIRROR_KIND,
            f"Nexus evidence note [{e.kind}] · {_SRC_TS_MARK}{e.ts}",
            ref=str(path))
        written.append((e.kind, path))

    if not written:
        return False, (f"{g.id}: Nexus 쓰기 실패 (권한/경로 확인) — fake-write 안 함.",)
    st.save(g2)
    out = [f"{g.id}: Nexus evidence {len(written)}건 기록 (lane=goal, schema 고정):"]
    for kind, path in written:
        out.append(f"  - [{kind}] {path}")
    out.append(f"  evidence 총 {len(g2.evidence)}건 (mirror 포함) — `/goal evidence {g.id}` 로 확인")
    out.append("  schema: goal_id/lane/packet_id/role/status/created_at/evidence_path (frontmatter)")
    return True, tuple(out)


def apply_new(env: Optional[Mapping[str, str]], title: str) -> Tuple[bool, str]:
    title = (title or "").strip()
    if not title:
        return False, "제목이 필요합니다 — `/goal new <제목>`"
    g = Goal.create(title)
    _store(env).save(g)
    return True, f"goal 생성: {g.id}  [{g.status.value}]  {g.title}"


def apply_plan(
    env: Optional[Mapping[str, str]],
    gid: str,
    step_tokens: Sequence[str],
) -> Tuple[bool, Tuple[str, ...]]:
    """Decompose a goal into ordered child-goal steps. Operator-driven; safe.

    Steps are given after the id, separated by ``|`` (so a step title may contain
    spaces): ``/goal plan <id> 스키마 설계 | 마이그레이션 | 회귀 테스트``. This only
    creates plan records (child goals + a ``plan`` evidence entry on the parent) and
    persists them — it executes nothing. The runtime continuation tick later sequences
    the children (the parent must be ``active`` for that to proceed)."""

    st = _store(env)
    g = st.get((gid or "").strip())
    if g is None:
        return False, (f"goal {gid!r} 없음 — `/goal list` 로 확인",)
    raw = " ".join(step_tokens or ())
    titles = [t.strip() for t in raw.split("|") if t.strip()]
    if not titles:
        return False, (
            "step 이 필요합니다 — `/goal plan <id> step1 | step2 | step3`",
            "각 step 은 `|` 로 구분 (제목에 공백 가능).",
        )
    if g.children:
        return False, (
            f"{g.id} 는 이미 {len(g.children)} step 으로 분해됨 — `/goal show {g.id}` 로 확인",
            "재분해는 중복 plan 을 만들 수 있어 막습니다.",
        )
    steps = [planning.PlanStep(title=t) for t in titles]
    parent2, children = planning.decompose(g, steps)
    for child in children:
        st.save(child)
    st.save(parent2)
    out = [f"{parent2.id} 분해: {len(children)} step (child goal 생성, draft)"]
    for child in children:
        out.append(f"  - {child.id}  [{child.status.value}]  {child.title}")
    out.append(f"  parent 활성화: `/goal activate {parent2.id}` → continuation tick 이 순차 진행")
    return True, tuple(out)


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
# Honest execution boundary: approving records the operator decision, then runs the GW4-B
# execution bridge (``forgekit_runtime.selfimprove.execute_approved_packet``, now merged).
# The bridge runs the REAL gate (chain + decision-lane + validate_execution) and persists
# execution+verification evidence ITSELF — so this surface RELOADS the authoritative goal
# afterward rather than overwriting it, and renders the bridge's real outcome (executed /
# blocked / awaiting / error). Never a fabricated "executed". The bridge is still looked up
# lazily so the surface also works if it is ever absent (honest "실행 대기").

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


_BRIDGE_ABSENT = "absent"     # GW4-B not deployed
_BRIDGE_FAILED = "failed"     # bridge raised (must not corrupt the decision)


def _run_execute_bridge(goal, env):
    """Run the GW4-B execution bridge if merged. Returns ``(state, payload)``:

    * ``(_BRIDGE_ABSENT, None)`` — bridge not deployed (pre-GW4-B);
    * ``(_BRIDGE_FAILED, "<msg>")`` — bridge raised;
    * ``("ok", ExecuteOutcome)`` — bridge ran (it persisted its own decision/execution/
      verification evidence on executed/blocked — the caller must RELOAD, not overwrite).

    Lazy, defensive import: the surface owns no execution logic and works whether or not
    the bridge exists, lighting up automatically once it lands."""

    fn = None
    try:  # canonical home (gw1 GW4-B)
        from forgekit_runtime.selfimprove import execute_approved_packet as fn  # type: ignore
    except Exception:  # noqa: BLE001
        try:
            from forgekit_runtime.decision_lane import execute_approved_packet as fn  # type: ignore
        except Exception:  # noqa: BLE001
            fn = None
    if fn is None:
        return _BRIDGE_ABSENT, None
    try:
        return "ok", fn(goal, env=env)
    except Exception as exc:  # noqa: BLE001 - bridge failure must not corrupt the decision
        return _BRIDGE_FAILED, str(exc)


def _outcome_tail(state: str, payload) -> str:
    """Render the REAL execution state for the operator — never a fake "executed"."""

    if state == _BRIDGE_ABSENT:
        return "승인됨(실행 대기 — GW4-B 실행 bridge 미배포)"
    if state == _BRIDGE_FAILED:
        return f"승인됨(실행 bridge 오류: {payload} — 실행 미수행)"
    o = payload  # ExecuteOutcome
    kind = getattr(o, "outcome", "")
    if kind == "executed":
        who = getattr(o, "executor_id", "") or "executor"
        return f"실행됨(safe·게이트 통과 · execution+verification 기록 · executor={who})"
    if kind == "blocked":
        reasons = ", ".join(getattr(o, "reasons", ()) or ()) or getattr(o, "action_class", "gate")
        return f"실행 거부(게이트: {reasons}) — decision 기록, 가짜 실행 없음"
    if kind == "awaiting":
        return "승인됨(실행 가능한 packet 없음 — 실행 대기)"
    # error / unknown
    detail = getattr(o, "detail", "") or "실행 불가"
    return f"승인됨(실행 불가: {detail})"


def apply_approve(env: Optional[Mapping[str, str]], gid: str, note: str = "") -> Tuple[bool, str]:
    """Approve an awaiting goal: ``awaiting_approval -> active`` + decision evidence,
    then run the GW4-B execution bridge and surface its REAL outcome.

    Persist order matters: the decision is saved FIRST (so it survives even when the
    bridge is absent / errors / writes nothing), then the bridge runs and persists its
    own execution+verification evidence. We RELOAD the authoritative goal afterward
    instead of re-saving a stale copy — otherwise the bridge's real execution evidence
    would be overwritten (a "fake status"). No fabricated execution, ever."""

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
    st.save(g2)                                  # decision persisted unconditionally
    state, payload = _run_execute_bridge(g2, env)
    final = st.get(g2.id) or g2                  # bridge may have written richer evidence
    return True, f"{final.id} 승인 -> {final.status.value}  · {_outcome_tail(state, payload)}"


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
        "  /goal show <id>       goal 상세(status/packets/children/evidence)",
        "  /goal plan <id> s1 | s2 | s3   큰 goal 을 하위 step(child goal)으로 분해",
        "  /goal progress <id>   진척(step/packet) + 다음 action(continuation)",
        "  /goal activate <id>   draft/blocked -> active",
        "  /goal evidence <id>   evidence(append-only) 목록 (Nexus 기록 표시)",
        "  /goal publish <id>    goal evidence 를 Nexus evidence 축에 authored note 로 기록(연결 vault)",
        "  /goal awaiting        승인 대기(awaiting_approval) goal + linked packets",
        "  /goal approve <id> [메모]  승인 -> active + decision evidence (실행은 GW4-B)",
        "  /goal deny <id> [메모]     거부 -> blocked + decision evidence",
        "  tick(수집→제안→evidence)은 runtime daemon 에서 실행 (GW4)",
    )


__all__ = (
    "goal_list_lines", "goal_show_lines", "goal_evidence_lines", "govern_lines",
    "progress_lines", "awaiting_lines", "apply_new", "apply_plan",
    "apply_activate", "apply_approve", "apply_deny", "apply_publish_evidence",
    "usage_lines",
)
