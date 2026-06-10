"""Tech-lead synthesis — consensus / todos / open_research aggregation.

Extracted verbatim from :mod:`deliberation` (behavior-preserving split).
This module owns the **synthesis axis**: the :class:`TechLeadSynthesis`
contract, its ``session.extra`` serde, the deterministic ``synthesize``
aggregation (which also generates the ``open_research`` follow-ups), and
the synthesis renderer plus synthesis-only helpers.

It depends one-way on :mod:`deliberation` for the shared role-take
dataclasses and small helpers (``_short_role`` / ``source_type`` /
``ROLE_RESEARCH_PROFILES`` / memory hit helpers / ``_bullet_block``).
``deliberation`` re-exports the public symbols defined here so existing
importers keep resolving unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple

from .research.pack import ResearchPack
from .workflow_state import WorkflowSession

from .deliberation import (
    ROLE_RESEARCH_PROFILES,
    BackendEngineerTake,
    FrontendEngineerTake,
    ProductDesignerTake,
    QaEngineerTake,
    RoleTake,
    TechLeadOpening,
    _bullet_block,
    _session_approved,
    _short_role,
    memory_hint_for_role,
    memory_hits_by,
    source_type,
)


@dataclass(frozen=True)
class TechLeadSynthesis:
    """thread 마지막 tech-lead 종합."""

    consensus: str
    todos: Sequence[str] = field(default_factory=tuple)
    open_research: Sequence[str] = field(default_factory=tuple)
    user_decisions_needed: Sequence[str] = field(default_factory=tuple)
    approval_required: bool = False
    approval_reason: Optional[str] = None


SYNTHESIS_PERSIST_VERSION = 1


def synthesis_to_dict(synthesis: TechLeadSynthesis) -> dict:
    """Serialize a :class:`TechLeadSynthesis` for ``session.extra``.

    The ``v`` key lets future readers branch on schema changes; current
    consumers read ``v=1``.
    """

    return {
        "v": SYNTHESIS_PERSIST_VERSION,
        "consensus": synthesis.consensus,
        "todos": list(synthesis.todos),
        "open_research": list(synthesis.open_research),
        "user_decisions_needed": list(synthesis.user_decisions_needed),
        "approval_required": bool(synthesis.approval_required),
        "approval_reason": synthesis.approval_reason,
    }


def synthesis_from_dict(data: Mapping[str, Any]) -> TechLeadSynthesis:
    """Reverse :func:`synthesis_to_dict` — best-effort reconstruction.

    Missing/malformed lists fall back to empty tuples. ``consensus`` is
    coerced to ``str`` so a malformed payload still produces a usable
    synthesis (the export will simply have an empty consensus block).
    """

    consensus = data.get("consensus")
    consensus_text = str(consensus) if consensus is not None else ""
    return TechLeadSynthesis(
        consensus=consensus_text,
        todos=tuple(_synthesis_str_list(data.get("todos"))),
        open_research=tuple(_synthesis_str_list(data.get("open_research"))),
        user_decisions_needed=tuple(
            _synthesis_str_list(data.get("user_decisions_needed"))
        ),
        approval_required=bool(data.get("approval_required")),
        approval_reason=_synthesis_optional_str(data.get("approval_reason")),
    )


def _synthesis_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(item) for item in value if item is not None]
    except TypeError:
        return []


def _synthesis_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def synthesize(
    session: WorkflowSession,
    role_takes: Sequence[RoleTake],
    *,
    research_pack: Optional[ResearchPack] = None,
    memory_context: Sequence["RetrievedMemory"] = (),
) -> TechLeadSynthesis:
    """Produce the final tech-lead synthesis from collected role takes.

    Pure-deterministic for MVP. A future iteration may delegate to an LLM
    runner; the dataclass shape is the contract either way.

    When ``memory_context`` is provided, prior decision/policy memories
    are folded into the consensus prefix and open_research follow-ups so
    the synthesis visibly references past notes — the seam the gateway
    forum hook and synthesize_thread now feed.
    """

    todos: list[str] = []
    open_research: list[str] = []
    user_decisions: list[str] = []

    for take in role_takes:
        todos.extend(_todos_from_take(take))
        if isinstance(take, TechLeadOpening):
            user_decisions.extend(take.decisions_needed)

    # Reference gaps → open research.
    if research_pack is None or not research_pack.urls:
        open_research.append("자료 reference 보강 필요 — 현재 thread에 첨부된 링크 없음")
    if research_pack is not None and 0 < len(research_pack.urls) < 3:
        open_research.append(
            "권장 reference 3건 이상이 아직 모이지 않았습니다 — 추가 자료 수집 권장"
        )

    # Per-role research profile gaps: if a role spoke but its profile's
    # top type was missing, surface a follow-up.
    if research_pack is not None and research_pack.sources:
        for take in role_takes:
            short = _short_role(getattr(take, "role", ""))
            profile = ROLE_RESEARCH_PROFILES.get(short)
            if not profile:
                continue
            available = {source_type(s) for s in research_pack.sources}
            top_type = profile[0]
            if top_type not in available:
                open_research.append(
                    f"{short} 우선 자료 유형({top_type})이 비어 있음 — 보강 권장"
                )

    # Memory-driven follow-ups: surface up to 3 prior decision hits so
    # multiple past decisions don't collapse into one quote, plus a
    # relevant policy hit for open_research. Distinct decision titles
    # also escalate to user_decisions_needed so the operator can break
    # the tie.
    decision_hits = memory_hits_by(memory_context, kind="decision", limit=3)
    for hit in decision_hits:
        cid = (getattr(hit, "citation_id", "") or "").strip()
        title = (getattr(hit, "title", "") or "").strip() or "(제목 없음)"
        label = f"[{cid}] {title}" if cid else title
        todos.append(f"이전 결정({label}) 재확인")
    if len(decision_hits) >= 2:
        distinct_titles = {h.title for h in decision_hits if h.title}
        if len(distinct_titles) >= 2:
            joined = " / ".join(
                f"[{(h.citation_id or '').strip() or 'm?'}] {h.title}"
                for h in decision_hits
            )
            user_decisions.append(
                f"기억된 결정 다중 검토 필요: {joined}"
            )
    policy_memory_hit = memory_hint_for_role(memory_context, source="policy")
    if policy_memory_hit:
        open_research.append(
            f"관련 정책({policy_memory_hit}) 검토 후 합의안 확정"
        )

    approval_required = bool(session.write_requested) and not _session_approved(session)
    approval_reason = (
        session.write_blocked_reason
        if approval_required and session.write_blocked_reason
        else (
            "쓰기 작업 승인이 필요합니다."
            if approval_required
            else None
        )
    )

    consensus = _consensus_summary(session, role_takes)
    if decision_hits:
        first = decision_hits[0]
        cid = (getattr(first, "citation_id", "") or "").strip()
        title = (getattr(first, "title", "") or "").strip() or "(제목 없음)"
        prefix_label = f"[{cid}] {title}" if cid else title
        consensus = f"기억된 결정({prefix_label}) 맥락에서: {consensus}"
    return TechLeadSynthesis(
        consensus=consensus,
        todos=tuple(_dedup_keep_order(todos)),
        open_research=tuple(_dedup_keep_order(open_research)),
        user_decisions_needed=tuple(_dedup_keep_order(user_decisions)),
        approval_required=approval_required,
        approval_reason=approval_reason,
    )


def render_synthesis(synth: TechLeadSynthesis) -> str:
    lines: list[str] = ["**[tech-lead 종합]**"]
    lines.append(f"합의안: {synth.consensus}")
    lines.append(_bullet_block("해야 할 일", synth.todos))
    lines.append(_bullet_block("더 조사할 것", synth.open_research))
    lines.append(_bullet_block("사용자 결정 필요", synth.user_decisions_needed))
    if synth.approval_required:
        reason = synth.approval_reason or "쓰기 승인 필요"
        lines.append(f"승인 필요: yes — {reason}")
    else:
        lines.append("승인 필요: no")
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Synthesis-only helpers
# ---------------------------------------------------------------------------


def _todos_from_take(take: RoleTake) -> list[str]:
    short = _short_role(getattr(take, "role", "") or "")
    items: list[str] = []

    # next_actions is the new uniform source for todos.
    next_actions = getattr(take, "next_actions", None) or ()
    items.extend(f"[{short}] {a}" for a in next_actions if a)

    if items:
        return items

    # Backward-compatible fallback for runners that return a take built
    # from older field set without next_actions.
    if isinstance(take, TechLeadOpening):
        return [f"[tech-lead] {b}" for b in take.task_breakdown]
    if isinstance(take, ProductDesignerTake):
        if take.ux_direction:
            return [f"[product-designer] {take.ux_direction}"]
        return []
    if isinstance(take, BackendEngineerTake):
        items_legacy: list[str] = []
        if take.data_impact:
            items_legacy.append(f"[backend-engineer] data — {take.data_impact}")
        if take.api_impact:
            items_legacy.append(f"[backend-engineer] api — {take.api_impact}")
        return items_legacy
    if isinstance(take, FrontendEngineerTake):
        if take.user_flow:
            return [f"[frontend-engineer] flow — {take.user_flow}"]
        return []
    if isinstance(take, QaEngineerTake):
        return [f"[qa-engineer] {ac}" for ac in take.acceptance_criteria]
    return []


def _consensus_summary(session: WorkflowSession, takes: Sequence[RoleTake]) -> str:
    role_names = [_short_role(getattr(take, "role", "")) for take in takes]
    role_text = ", ".join(r for r in role_names if r) or "tech-lead"
    return (
        f"{session.task_type} 작업에 {role_text}가 참여해 검토했습니다 — "
        f"실행 후보 `{session.executor_role or 'tech-lead'}`가 결정 사항을 반영해 진행."
    )


def _dedup_keep_order(items: Sequence[str]) -> Tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        text = (item or "").strip()
        if text and text not in seen:
            seen[text] = None
    return tuple(seen.keys())
