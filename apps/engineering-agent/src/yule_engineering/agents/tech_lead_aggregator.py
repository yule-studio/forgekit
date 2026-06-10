"""Tech-lead aggregation policy + helpers.

The tech-lead's job at the end of a deliberation is to consolidate
each role's take into a single executable conclusion. Phase 6 lifts
that bookkeeping out of ad-hoc logic in the runtime / forum hook so
both the supervisor's summary and the work_report builder can call
the same primitives.

Public surface:

- :func:`build_tech_lead_summary_context` — collect per-role decision
  notes + forbidden-action vetoes + selection metadata into one dict
  the synthesis prompt / deterministic fallback both consume.
- :func:`aggregate_role_outputs` — given role takes (deterministic or
  rendered) plus a :class:`RoleSelection`, produce a structured
  :class:`AggregateResult` with consensus / open_questions /
  conflicts / next_actions / approval_required / executor_required.

Aggregator is intentionally pure-Python and IO-free so tests can drive
it with synthetic role takes; the production runtime feeds in real
:class:`RoleTake` instances and the result lands on session.extra so
status / Obsidian export can re-render without recomputing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple

from .role_profiles import (
    PARTICIPATION_EXCLUDED,
    PARTICIPATION_PRIMARY,
    PARTICIPATION_REQUIRED,
    PARTICIPATION_REVIEWER,
    forbidden_actions_for_role,
    get_role_profile,
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleAggregateNote:
    """One role's contribution to the aggregate.

    Slim — only the fields the aggregator actually needs to merge.
    The full role take stays on session.extra for downstream rendering.
    """

    role: str
    perspective: str = ""
    risks: Tuple[str, ...] = ()
    next_actions: Tuple[str, ...] = ()
    decisions: Tuple[str, ...] = ()
    forbidden_violations: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AggregateResult:
    """Aggregated conclusion from the tech-lead synthesis pass."""

    consensus: str
    todos: Tuple[str, ...] = ()
    open_questions: Tuple[str, ...] = ()
    next_actions: Tuple[str, ...] = ()
    risks: Tuple[str, ...] = ()
    conflicts: Tuple[str, ...] = ()
    selected_roles: Tuple[str, ...] = ()
    excluded_roles: Tuple[str, ...] = ()
    excluded_reasons: Mapping[str, str] = field(default_factory=dict)
    requires_user_decision: bool = False
    requires_executor: bool = False
    notes: str = ""


# ---------------------------------------------------------------------------
# Coercion helpers — accept role takes in several shapes
# ---------------------------------------------------------------------------


def _coerce_role_note(value: Any) -> Optional[RoleAggregateNote]:
    """Best-effort coercion of *value* to :class:`RoleAggregateNote`.

    Accepts:
    - :class:`RoleAggregateNote` (passthrough)
    - dataclass-like objects with ``role`` + ``risks`` / ``next_actions``
      attributes (real :class:`RoleTake` instances from deliberation)
    - dict-shaped notes with the same keys
    Returns ``None`` when *value* doesn't carry a role id so the
    caller can drop unrelated entries silently.
    """

    if value is None:
        return None
    if isinstance(value, RoleAggregateNote):
        return value
    role = (
        getattr(value, "role", None)
        if not isinstance(value, Mapping)
        else value.get("role")
    )
    if not role:
        return None

    def _read(name: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)

    short_role = str(role).split("/", 1)[-1].strip() or str(role)

    perspective = (_read("perspective") or "").strip() if isinstance(_read("perspective"), str) else ""
    risks = tuple(_clean_str_iter(_read("risks") or ()))
    next_actions = tuple(_clean_str_iter(_read("next_actions") or ()))
    decisions = tuple(_clean_str_iter(_read("decisions_needed") or _read("decisions") or ()))

    return RoleAggregateNote(
        role=short_role,
        perspective=perspective,
        risks=risks,
        next_actions=next_actions,
        decisions=decisions,
    )


def _clean_str_iter(value: Any) -> Sequence[str]:
    if value is None:
        return ()
    if isinstance(value, str):
        # Treat a bare string as a single-line entry. Empty / whitespace
        # produces an empty list so the aggregator doesn't render
        # "**리스크**: ()".
        cleaned = value.strip()
        return (cleaned,) if cleaned else ()
    out: list[str] = []
    try:
        iterator = iter(value)
    except TypeError:
        return ()
    for entry in iterator:
        if entry is None:
            continue
        text = str(entry).strip()
        if text:
            out.append(text)
    return out


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def build_tech_lead_summary_context(
    *,
    role_notes: Sequence[Any],
    selection: Any = None,
    canonical_prompt: str = "",
) -> dict:
    """Bundle the inputs the synthesis prompt needs.

    Returns a JSON-friendly dict so it can be embedded in an LLM
    prompt or printed deterministically. Always populates ``role_notes``
    (coerced via :func:`_coerce_role_note`), ``selected_roles``,
    ``excluded_roles``, and ``forbidden_actions_by_role`` so the
    tech-lead has every veto surface in one place.
    """

    notes = [
        coerced
        for coerced in (_coerce_role_note(n) for n in role_notes or ())
        if coerced is not None
    ]
    selected = tuple(getattr(selection, "selected_roles", ()) or ())
    excluded = tuple(getattr(selection, "excluded_roles", ()) or ())
    excluded_reasons: dict = {}
    reasons = getattr(selection, "reason_by_role", None) or {}
    if isinstance(reasons, Mapping):
        for role in excluded:
            if role in reasons:
                excluded_reasons[role] = str(reasons[role])

    forbidden: dict = {}
    for role in selected:
        items = forbidden_actions_for_role(role)
        if items:
            forbidden[role] = list(items)

    return {
        "canonical_prompt": (canonical_prompt or "").strip(),
        "role_notes": [
            {
                "role": note.role,
                "perspective": note.perspective,
                "risks": list(note.risks),
                "next_actions": list(note.next_actions),
                "decisions": list(note.decisions),
            }
            for note in notes
        ],
        "selected_roles": list(selected),
        "excluded_roles": list(excluded),
        "excluded_reasons": excluded_reasons,
        "forbidden_actions_by_role": forbidden,
        "fallback_policy": getattr(selection, "fallback_policy", None),
    }


def aggregate_role_outputs(
    *,
    role_notes: Sequence[Any],
    selection: Any = None,
    canonical_prompt: str = "",
    research_only: bool = False,
) -> AggregateResult:
    """Merge per-role notes into one :class:`AggregateResult`.

    Aggregation rules:

    1. ``consensus`` is built by joining the primary role(s)' perspectives
       (when known) plus the canonical prompt summary. When no role
       provided a perspective, fall back to the canonical prompt + a
       generic "역할별 검토 결과를 정리했어요." stub so the aggregate
       is never empty.
    2. ``risks`` / ``next_actions`` are the deduplicated union of all
       roles' contributions, in role-order.
    3. ``open_questions`` collects role decisions that begin with
       interrogative tokens ("어떤", "어떻게", "?", "고민", "검토 필요")
       so the tech-lead can surface them as user-decision items.
    4. ``conflicts`` is the union of forbidden_violations the
       aggregator detected (Phase 6 — currently always empty since
       deterministic role takes don't violate their own profile, but
       reserved for the LLM-backed path).
    5. ``requires_user_decision`` is True when any role emits a
       decision item containing 사용자 / 결정 / 승인.
    6. ``requires_executor`` is True only when at least one role's
       next_actions explicitly mention "구현" / "수정" / "execute" AND
       ``research_only`` is False — research-only requests must never
       silently flip to coding.
    """

    notes = [
        coerced
        for coerced in (_coerce_role_note(n) for n in role_notes or ())
        if coerced is not None
    ]
    selected = tuple(getattr(selection, "selected_roles", ()) or ())
    excluded = tuple(getattr(selection, "excluded_roles", ()) or ())
    excluded_reasons: dict = {}
    reasons = getattr(selection, "reason_by_role", None) or {}
    if isinstance(reasons, Mapping):
        for role in excluded:
            if role in reasons:
                excluded_reasons[role] = str(reasons[role])

    perspectives = [n.perspective for n in notes if n.perspective]
    consensus_bits: list[str] = []
    cp = (canonical_prompt or "").strip()
    if cp:
        consensus_bits.append(cp)
    if perspectives:
        # Cap the perspective fragment so a verbose role doesn't push
        # the consensus past the Discord-friendly 280-char limit; the
        # full perspective stays in role_notes for downstream rendering.
        first = perspectives[0]
        if len(first) > 180:
            first = first[:177].rstrip() + "…"
        consensus_bits.append(f"역할별 take: {first}")
    consensus = " · ".join(consensus_bits) or "역할별 검토 결과를 정리했어요."

    risks = _dedupe(
        item for note in notes for item in note.risks
    )
    next_actions = _dedupe(
        item for note in notes for item in note.next_actions
    )
    raw_decisions = [
        item for note in notes for item in note.decisions
    ]
    open_questions = tuple(
        item
        for item in _dedupe(raw_decisions)
        if _looks_like_open_question(item)
    )
    todos = tuple(
        item
        for item in next_actions
        if not _looks_like_open_question(item)
    )

    requires_user_decision = any(
        _looks_like_user_decision(item)
        for item in (*raw_decisions, *next_actions)
    )

    requires_executor = (
        not research_only
        and any(_looks_like_executor_action(item) for item in next_actions)
    )

    notes_text = ""
    if research_only:
        notes_text = (
            "research-only — 코드 수정은 본 round에서 진행하지 않습니다. "
            "별도 승인 단계로 전환해야 implementation으로 이어집니다."
        )

    conflicts = _detect_conflicts(notes, selected_roles=selected)

    return AggregateResult(
        consensus=consensus,
        todos=tuple(todos),
        open_questions=open_questions,
        next_actions=next_actions,
        risks=risks,
        conflicts=conflicts,
        selected_roles=selected,
        excluded_roles=excluded,
        excluded_reasons=excluded_reasons,
        requires_user_decision=requires_user_decision,
        requires_executor=requires_executor,
        notes=notes_text,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dedupe(items) -> Tuple[str, ...]:
    seen: dict[str, None] = {}
    for raw in items:
        text = (raw or "").strip()
        if not text:
            continue
        if text in seen:
            continue
        seen[text] = None
    return tuple(seen.keys())


_OPEN_QUESTION_TOKENS = (
    "어떤",
    "어떻게",
    "필요",
    "고민",
    "결정 필요",
    "확인 필요",
    "검토 필요",
    "?",
)


_USER_DECISION_TOKENS = (
    "사용자 결정",
    "사용자 승인",
    "사용자 의사결정",
    "승인 필요",
    "operator 결정",
)


_EXECUTOR_ACTION_TOKENS = (
    "구현",
    "수정",
    "implement",
    "execute",
    "코드 수정",
    "patch",
    "deploy",
)


def _looks_like_open_question(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return False
    return any(token in cleaned for token in (t.lower() for t in _OPEN_QUESTION_TOKENS))


def _looks_like_user_decision(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return False
    return any(token in cleaned for token in (t.lower() for t in _USER_DECISION_TOKENS))


def _looks_like_executor_action(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return False
    return any(token in cleaned for token in (t.lower() for t in _EXECUTOR_ACTION_TOKENS))


def _detect_conflicts(
    notes: Sequence[RoleAggregateNote], *, selected_roles: Sequence[str]
) -> Tuple[str, ...]:
    """Cross-role conflict detector.

    Today flags two cases:
    - Two roles disagree on whether the same area needs change (one
      role's next_actions says "구현", another's risks list says
      "구현 보류" / "수정 위험").
    - A role's note triggers another role's forbidden_actions list
      via direct substring overlap (LLM-backed path, kept as the
      structural hook so the deterministic path stays empty).
    """

    conflicts: list[str] = []
    actions_text = " · ".join(
        f"{n.role}: {action}" for n in notes for action in n.next_actions
    ).lower()
    risks_text = " · ".join(
        f"{n.role}: {risk}" for n in notes for risk in n.risks
    ).lower()
    if "구현" in actions_text and "구현 보류" in risks_text:
        conflicts.append(
            "한 역할은 구현을 제안하고 다른 역할은 구현 보류를 권하고 있어요. "
            "tech-lead가 우선순위를 결정해야 합니다."
        )
    if "수정" in actions_text and "수정 위험" in risks_text:
        conflicts.append(
            "한 역할은 수정을 제안하고 다른 역할은 수정 위험을 표시했어요. "
            "tech-lead 가 합의안을 정리해야 합니다."
        )

    # Forbidden hook — present so future LLM aggregator can flag a
    # role's note that overlaps another role's forbidden_actions.
    for note in notes:
        if note.forbidden_violations:
            for violation in note.forbidden_violations:
                conflicts.append(
                    f"`{note.role}` 역할 답변이 다른 역할의 forbidden 리스트와 충돌: {violation}"
                )

    return tuple(conflicts)


__all__ = (
    "AggregateResult",
    "RoleAggregateNote",
    "aggregate_role_outputs",
    "build_tech_lead_summary_context",
)
