"""Per-role policy used by the runtime Recall and Decide stages.

Phase 3B introduces a tiny RolePolicy registry so Recall can pick a
role-shaped memory filter and Decide can later use the policy notes
to pick role-specific actions. Kept deliberately small — the goal is
just enough structure to feed Recall's role-aware lookup without a
full DI framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence, Tuple


@dataclass(frozen=True)
class RolePolicy:
    """How one role wants to look at memory / source candidates.

    ``role_id`` is the canonical id (e.g. ``"engineering-agent/tech-lead"``
    or just ``"gateway"``).
    ``memory_role_filter`` is the value to pass to
    :func:`memory.search.search`'s ``role`` kwarg — a string the indexer
    used at write-time. ``None`` means "no role filter".
    ``preferred_source_kinds`` is an ordered list (most-preferred first)
    of memory ``source_kind`` values; the runtime issues a search per
    kind and merges results in order so the primary signal wins ties.
    ``description`` is a short human-readable summary for the gateway
    to surface in clarification messages.
    """

    role_id: str
    short_name: str
    memory_role_filter: Optional[str] = None
    preferred_source_kinds: Tuple[str, ...] = field(default_factory=tuple)
    preferred_note_kinds: Tuple[str, ...] = field(default_factory=tuple)
    description: str = ""


_GATEWAY = RolePolicy(
    role_id="gateway",
    short_name="gateway",
    memory_role_filter=None,
    preferred_source_kinds=("session", "discord"),
    preferred_note_kinds=("decision", "summary"),
    description="운영 매니저 — 새 작업/기존 작업/상태/승인을 분류하고 흐름을 조율한다.",
)

_TECH_LEAD = RolePolicy(
    role_id="engineering-agent/tech-lead",
    short_name="tech-lead",
    memory_role_filter="tech-lead",
    preferred_source_kinds=("session", "obsidian", "discord"),
    preferred_note_kinds=("decision", "synthesis", "plan"),
    description="팀장 — 작업 분해, 역할별 의견 종합, 충돌 조정, 합의안과 승인 문서 초안.",
)

_AI_ENGINEER = RolePolicy(
    role_id="engineering-agent/ai-engineer",
    short_name="ai-engineer",
    memory_role_filter="ai-engineer",
    preferred_source_kinds=("obsidian", "session", "research_pack"),
    preferred_note_kinds=("research", "evaluation", "decision"),
    description="LLM/RAG/memory/prompt/model/evaluation 관점 판단.",
)

_BACKEND_ENGINEER = RolePolicy(
    role_id="engineering-agent/backend-engineer",
    short_name="backend-engineer",
    memory_role_filter="backend-engineer",
    preferred_source_kinds=("session", "code", "obsidian"),
    preferred_note_kinds=("decision", "design", "incident"),
    description="persistence/API/queue/state/reliability 관점 판단.",
)

_FRONTEND_ENGINEER = RolePolicy(
    role_id="engineering-agent/frontend-engineer",
    short_name="frontend-engineer",
    memory_role_filter="frontend-engineer",
    preferred_source_kinds=("discord", "obsidian", "session"),
    preferred_note_kinds=("ux", "design", "decision"),
    description="Discord/Obsidian 표시 UX, 사용자 피드백 루프, UI 구조 관점 판단.",
)

_PRODUCT_DESIGNER = RolePolicy(
    role_id="engineering-agent/product-designer",
    short_name="product-designer",
    memory_role_filter="product-designer",
    preferred_source_kinds=("obsidian", "discord", "session"),
    preferred_note_kinds=("ux", "research", "decision"),
    description="사용자 의도, 운영 플로우, 정보 구조, 승인 UX 관점 판단.",
)

_QA_ENGINEER = RolePolicy(
    role_id="engineering-agent/qa-engineer",
    short_name="qa-engineer",
    memory_role_filter="qa-engineer",
    preferred_source_kinds=("session", "code", "discord"),
    preferred_note_kinds=("test", "incident", "decision"),
    description="실패 케이스, 회귀 테스트, smoke test, acceptance criteria 관점 판단.",
)

_DEVOPS_ENGINEER = RolePolicy(
    role_id="engineering-agent/devops-engineer",
    short_name="devops-engineer",
    memory_role_filter="devops-engineer",
    preferred_source_kinds=("session", "code", "obsidian"),
    preferred_note_kinds=("incident", "deploy", "decision"),
    description="env/실행 프로세스/supervisor/배포/모니터링/장애 복구 관점 판단.",
)


_DEFAULT_POLICY = RolePolicy(
    role_id="*",
    short_name="default",
    memory_role_filter=None,
    description="알 수 없는 role — gateway-equivalent 기본 정책.",
)


_ROLE_POLICIES: Dict[str, RolePolicy] = {
    policy.role_id: policy
    for policy in (
        _GATEWAY,
        _TECH_LEAD,
        _AI_ENGINEER,
        _BACKEND_ENGINEER,
        _FRONTEND_ENGINEER,
        _PRODUCT_DESIGNER,
        _QA_ENGINEER,
        _DEVOPS_ENGINEER,
    )
}


def role_policy_for(role_id: str) -> RolePolicy:
    """Return the canonical RolePolicy for *role_id*.

    Accepts either fully-qualified ``engineering-agent/<short>`` or the
    short form. Unknown roles fall back to the safe gateway-equivalent
    default so the runtime never crashes for a typo.
    """

    if not role_id:
        return _DEFAULT_POLICY
    if role_id in _ROLE_POLICIES:
        return _ROLE_POLICIES[role_id]
    short_lookup = f"engineering-agent/{role_id}"
    if short_lookup in _ROLE_POLICIES:
        return _ROLE_POLICIES[short_lookup]
    if role_id == _GATEWAY.role_id:
        return _GATEWAY
    return _DEFAULT_POLICY


def all_role_policies() -> Sequence[RolePolicy]:
    """Stable ordered list — handy for tests and for the gateway when
    it wants to enumerate role descriptions."""

    return tuple(_ROLE_POLICIES.values())


__all__ = (
    "RolePolicy",
    "role_policy_for",
    "all_role_policies",
)
