"""Role selection for the engineering research lifecycle.

Tech-lead reviews the user's task on intake and picks the *minimum*
set of roles that should participate in research, deliberation, and
the work-report. Without this gate the gateway used to fan out every
request to all six member roles, producing shallow generic answers
and burning the forum-research budget on roles that had nothing
relevant to say. This module picks roles deterministically with a
written reason per role, so the supervisor / status diagnostic can
explain *why* a role is on (or off) the active list.

Selection sources (highest priority first):

1. ``user_explicit`` — the user named specific roles in the prompt
   ("backend-engineer 관점", "ai-engineer / qa-engineer 시각에서"…).
   tech-lead is always added on top, even when not named.
2. ``tech_lead_rule`` — keyword-based scoring against per-role
   research keyword banks (see ``_RULE_KEYWORDS``). Backed by tie-
   break order from
   :data:`yule_orchestrator.agents.coding.authorization._DEFAULT_PARTICIPANT_PRIORITY`.
3. ``fallback`` — when neither path produced a usable set, fall
   back to ``tech-lead + ai-engineer + backend-engineer + qa-engineer``
   (the historical "always-on research quartet") so the gateway
   never silently drops to zero participants.

This module does *not* mutate sessions itself — call
:func:`apply_role_selection_to_extra` to fold the selection into a
``session.extra`` mapping the workflow store can persist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from ..coding.authorization import (
    _DEFAULT_PARTICIPANT_PRIORITY,
    _EXECUTOR_CANDIDATE_ROLES,
)
from ..role_profiles import (
    PARTICIPATING_LEVELS,
    PARTICIPATION_EXCLUDED,
    PARTICIPATION_OPTIONAL,
    PARTICIPATION_PRIMARY,
    PARTICIPATION_REQUIRED,
    PARTICIPATION_REVIEWER,
    all_role_profiles,
)


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------


ROLE_TECH_LEAD: str = "tech-lead"

#: Every engineering-agent role id the selector considers (tech-lead +
#: the executor pool from coding_authorization).
ALL_ENGINEERING_ROLES: Tuple[str, ...] = (ROLE_TECH_LEAD,) + _EXECUTOR_CANDIDATE_ROLES


SOURCE_USER_EXPLICIT: str = "user_explicit"
SOURCE_TECH_LEAD_RULE: str = "tech_lead_rule"
SOURCE_FALLBACK: str = "fallback"


# Fallback policy ids surfaced via :attr:`RoleSelection.fallback_policy`
# so the supervisor / status response can describe *which* fallback
# fired ("no keyword match — vague_engineering team"). The selector
# routes vague prompts to a narrow set instead of the historical
# always-on quartet so unrelated roles don't auto-join.
FALLBACK_EMPTY_PROMPT: str = "empty_prompt"
FALLBACK_VAGUE_ENGINEERING: str = "vague_engineering"
FALLBACK_VAGUE_AI_RESEARCH: str = "vague_ai_research"
FALLBACK_VAGUE_PRODUCT: str = "vague_product"
FALLBACK_VAGUE_INFRA: str = "vague_infra"
FALLBACK_VAGUE_RESEARCH_ONLY: str = "vague_research_only"
# Legacy "always-on quartet" — kept as the safety-net default when no
# narrower policy fires. Phase 4 narrows the surface but never returns
# zero participants.
FALLBACK_LEGACY_QUARTET: str = "legacy_quartet"


# Historical "always-on" research quartet — used as the fallback when
# the user's prompt is empty / vague and no keyword bank fires. Keeps
# the gateway from silently producing a zero-participant session.
_FALLBACK_SELECTED: Tuple[str, ...] = (
    ROLE_TECH_LEAD,
    "ai-engineer",
    "backend-engineer",
    "qa-engineer",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleSelection:
    """Result of :func:`recommend_active_roles`.

    Backwards-compatible contract — ``selected_roles`` /
    ``excluded_roles`` / ``required_roles`` / ``optional_roles`` /
    ``reason_by_role`` / ``selection_source`` keep their pre-Phase-3
    semantics so existing consumers (member bot gating, status /
    work_report rendering, research budget) don't need a migration.

    Phase 3 adds the participation-level surface in additive fields:

    - ``participation_by_role`` — role id → :data:`PARTICIPATION_LEVELS`
      bucket name. Always populated; ``selected_roles`` is the same
      view filtered to participating buckets.
    - ``primary_roles`` / ``reviewer_roles`` / ``optional_roles_v2`` —
      pre-bucketed views for callers that just want one tier.
    - ``matched_keywords_by_role`` — keyword bank hits per role; the
      selector feeds these into ``reason_by_role`` and the supervisor
      can show them verbatim ("rule bank: kubernetes, helm, ingress").
    - ``fallback_policy`` — populated when ``selection_source`` is
      ``fallback``. Names which narrow fallback fired (vague_infra,
      vague_engineering, …) so docs and ops UI can explain the choice.

    Defaults are empty containers so old test fixtures that build a
    bare ``RoleSelection(...)`` keep passing.
    """

    selected_roles: Tuple[str, ...]
    excluded_roles: Tuple[str, ...]
    required_roles: Tuple[str, ...]
    optional_roles: Tuple[str, ...]
    reason_by_role: Mapping[str, str]
    selection_source: str
    # Phase 3 additive surface ↓
    participation_by_role: Mapping[str, str] = field(default_factory=dict)
    primary_roles: Tuple[str, ...] = ()
    reviewer_roles: Tuple[str, ...] = ()
    optional_roles_v2: Tuple[str, ...] = ()
    matched_keywords_by_role: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    fallback_policy: Optional[str] = None


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


# Substring → canonical role id. Order matters — longer/more specific
# matchers come first so "ai-engineer" doesn't get swallowed by a
# bare "ai" pattern further down. The list is ASCII + Korean
# variations the team has actually used in operator chat.
_USER_EXPLICIT_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("tech-lead", "tech-lead"),
    ("tech lead", "tech-lead"),
    ("테크리드", "tech-lead"),
    ("테크 리드", "tech-lead"),
    ("ai-engineer", "ai-engineer"),
    ("ai engineer", "ai-engineer"),
    ("ai 엔지니어", "ai-engineer"),
    ("ai엔지니어", "ai-engineer"),
    ("ai 관점", "ai-engineer"),
    ("backend-engineer", "backend-engineer"),
    ("backend engineer", "backend-engineer"),
    ("백엔드 엔지니어", "backend-engineer"),
    ("백엔드엔지니어", "backend-engineer"),
    ("백엔드", "backend-engineer"),
    ("frontend-engineer", "frontend-engineer"),
    ("frontend engineer", "frontend-engineer"),
    ("프론트엔드 엔지니어", "frontend-engineer"),
    ("프론트엔드", "frontend-engineer"),
    ("qa-engineer", "qa-engineer"),
    ("qa engineer", "qa-engineer"),
    ("qa 엔지니어", "qa-engineer"),
    ("devops-engineer", "devops-engineer"),
    ("devops engineer", "devops-engineer"),
    ("데브옵스 엔지니어", "devops-engineer"),
    ("데브옵스", "devops-engineer"),
    ("product-designer", "product-designer"),
    ("product designer", "product-designer"),
    ("프로덕트 디자이너", "product-designer"),
    ("ux 디자이너", "product-designer"),
    ("ui 디자이너", "product-designer"),
)


def _detect_explicit_roles(prompt: str) -> Tuple[str, ...]:
    """Return roles named directly in *prompt* (deduplicated, in the
    order the patterns first hit). Empty tuple when the prompt
    doesn't name any role explicitly."""

    if not prompt:
        return ()
    lowered = prompt.lower()
    found: list[str] = []
    seen: set[str] = set()
    for pattern, role in _USER_EXPLICIT_PATTERNS:
        # Match against lowered text; the patterns themselves are
        # already lowercase. Korean substrings are unaffected by
        # `.lower()` so the same call covers both alphabets.
        if pattern in lowered:
            if role not in seen:
                seen.add(role)
                found.append(role)
    return tuple(found)


# Per-role keyword banks for the tech-lead rule-based fallback. These
# are research-shaped concerns (what the role would actually want to
# *investigate*) rather than executor-shaped scope, so they can
# legitimately differ from agent.json's executor_priority bank.
# Hits are additive; tie-break uses _DEFAULT_PARTICIPANT_PRIORITY.
_RULE_KEYWORDS: Mapping[str, Tuple[str, ...]] = {
    "ai-engineer": (
        "ai",
        "ml",
        "llm",
        "rag",
        "agent",
        "memory",
        "embedding",
        "vector",
        "prompt",
        "프롬프트",
        "에이전트",
        "추론",
        "학습",
        "강화학습",
        "하네스",
        "harness",
    ),
    "backend-engineer": (
        "api",
        "rest",
        "grpc",
        "endpoint",
        "database",
        "schema",
        "auth",
        "spring",
        "django",
        "fastapi",
        "백엔드",
        "인증",
        "권한",
        "결제",
        "멱등",
        "트랜잭션",
        # Infrastructure-shape keywords — backend joins k8s / container /
        # service-mesh / orchestration discussions because runtime
        # contracts (health checks, DB connection pools, auth gateways,
        # service-to-service calls) are backend's responsibility even
        # when devops drives the cluster setup.
        "k8s",
        "kubernetes",
        "쿠버네티스",
        "cluster",
        "클러스터",
        "container",
        "컨테이너",
        "orchestration",
        "오케스트레이션",
        "helm",
        "ingress",
        "service mesh",
        "service-mesh",
    ),
    "frontend-engineer": (
        "ui",
        "react",
        "vue",
        "css",
        "page",
        "component",
        "프론트엔드",
        "화면",
        "컴포넌트",
        "접근성",
        "accessibility",
        "랜딩",
        "hero",
    ),
    "product-designer": (
        "design",
        "wireframe",
        "copy",
        "carousel",
        "디자인",
        "카피",
        "인터페이스",
        "사용자 흐름",
        "ux",
        "랜딩",
    ),
    "qa-engineer": (
        "test",
        "regression",
        "qa",
        "acceptance",
        "회귀",
        "테스트",
        "품질",
        "검증",
        "재현",
    ),
    "devops-engineer": (
        "deploy",
        "deployment",
        "ci",
        "cd",
        "docker",
        "k8s",
        "kubernetes",
        "쿠버네티스",
        "cluster",
        "클러스터",
        "container",
        "컨테이너",
        "orchestration",
        "오케스트레이션",
        "helm",
        "ingress",
        "service mesh",
        "service-mesh",
        "monitoring",
        "supervisor",
        "supervisord",
        "운영",
        "배포",
        "모니터링",
        "로그",
        "observability",
        "env",
    ),
}


def _rule_score(role: str, prompt_lower: str) -> int:
    keywords = _RULE_KEYWORDS.get(role, ())
    return sum(1 for kw in keywords if kw in prompt_lower)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def recommend_active_roles(
    *,
    user_prompt: str,
    hint_role_sequence: Sequence[str] = (),
    department_dir: Optional[Path] = None,  # noqa: ARG001 - reserved for profile-based scoring
) -> RoleSelection:
    """Pick the active research roles for *user_prompt*.

    *hint_role_sequence* is an optional caller-supplied sequence
    (e.g. ``session.role_sequence`` on a continuation) that augments
    the rule-based scoring — roles in the hint sequence are bumped
    even when their keyword bank doesn't fire, but never override the
    user's explicit mention.
    """

    text = (user_prompt or "").strip()

    # 1. user explicit override
    explicit = _detect_explicit_roles(text) if text else ()
    if explicit:
        ordered: list[str] = [ROLE_TECH_LEAD]
        for role in explicit:
            if role != ROLE_TECH_LEAD and role not in ordered:
                ordered.append(role)
        selected = tuple(ordered)
        excluded = tuple(r for r in ALL_ENGINEERING_ROLES if r not in selected)
        reasons: dict[str, str] = {
            ROLE_TECH_LEAD: "tech-lead always included",
        }
        for role in explicit:
            if role == ROLE_TECH_LEAD:
                reasons[role] = "tech-lead always included (also user-named)"
            else:
                reasons[role] = "user explicit mention"
        return RoleSelection(
            selected_roles=selected,
            excluded_roles=excluded,
            required_roles=selected,
            optional_roles=(),
            reason_by_role=reasons,
            selection_source=SOURCE_USER_EXPLICIT,
        )

    # 2. tech-lead rule
    if text:
        prompt_lower = text.lower()
        scored: list[tuple[int, str]] = []
        hint_set = {r for r in hint_role_sequence if r in _EXECUTOR_CANDIDATE_ROLES}
        for role in _EXECUTOR_CANDIDATE_ROLES:
            score = _rule_score(role, prompt_lower)
            # Soft hint bump — a role that has no keyword hit but is
            # carried over from a continuation session still gets a
            # +1 so it appears in the selection. This lets a research
            # restart on the same session keep its team intact.
            if role in hint_set:
                score += 1
            if score > 0:
                scored.append((score, role))
        if scored:
            priority_map = {r: i for i, r in enumerate(_DEFAULT_PARTICIPANT_PRIORITY)}
            scored.sort(key=lambda t: (-t[0], priority_map.get(t[1], 99)))
            ordered = [ROLE_TECH_LEAD] + [r for _, r in scored]
            seen: set[str] = set()
            uniq: list[str] = []
            for r in ordered:
                if r not in seen:
                    seen.add(r)
                    uniq.append(r)
            selected = tuple(uniq)
            excluded = tuple(r for r in ALL_ENGINEERING_ROLES if r not in selected)
            reasons = {ROLE_TECH_LEAD: "tech-lead always included"}
            for score, role in scored:
                reasons[role] = f"rule bank score {score}"
            return RoleSelection(
                selected_roles=selected,
                excluded_roles=excluded,
                required_roles=(ROLE_TECH_LEAD,),
                optional_roles=(),
                reason_by_role=reasons,
                selection_source=SOURCE_TECH_LEAD_RULE,
            )

    # 3. fallback
    selected = _FALLBACK_SELECTED
    excluded = tuple(r for r in ALL_ENGINEERING_ROLES if r not in selected)
    fallback_reason = (
        "fallback (empty prompt)"
        if not text
        else "fallback (no keyword match in rule bank)"
    )
    reasons = {role: fallback_reason for role in selected}
    return RoleSelection(
        selected_roles=selected,
        excluded_roles=excluded,
        required_roles=(ROLE_TECH_LEAD,),
        optional_roles=(),
        reason_by_role=reasons,
        selection_source=SOURCE_FALLBACK,
    )


def apply_role_selection_to_extra(
    extra: Optional[Mapping[str, Any]],
    selection: RoleSelection,
) -> dict:
    """Return a copy of *extra* with the selection metadata folded in.

    Stores under the following keys on ``session.extra`` so the
    runtime / status / forum publisher can read off a single source
    of truth:

      * ``active_research_roles`` — list of selected role ids.
      * ``excluded_research_roles`` — list of complement role ids.
      * ``role_selection_source`` — string from the SOURCE_* set.
      * ``role_selection_reasons`` — mapping role → reason string.

    Existing extras (research_pack, coding_proposal, etc.) are
    preserved. Pass ``None`` to start from a fresh dict.
    """

    new_extra: dict = dict(extra or {})
    new_extra["active_research_roles"] = list(selection.selected_roles)
    new_extra["excluded_research_roles"] = list(selection.excluded_roles)
    new_extra["role_selection_source"] = selection.selection_source
    new_extra["role_selection_reasons"] = dict(selection.reason_by_role)
    # Phase 3 additive surfaces. Always written so older sessions
    # rehydrated through this helper pick up the new keys deterministically.
    new_extra["role_participation"] = dict(selection.participation_by_role)
    new_extra["role_selection_primary"] = list(selection.primary_roles)
    new_extra["role_selection_reviewer"] = list(selection.reviewer_roles)
    new_extra["role_selection_optional"] = list(selection.optional_roles_v2)
    if selection.matched_keywords_by_role:
        new_extra["role_selection_keywords"] = {
            role: list(words)
            for role, words in selection.matched_keywords_by_role.items()
        }
    if selection.fallback_policy is not None:
        new_extra["role_selection_fallback_policy"] = selection.fallback_policy
    return new_extra


def active_roles_from_extra(extra: Optional[Mapping[str, Any]]) -> Tuple[str, ...]:
    """Read back the active role list from a session's extras.

    Returns an empty tuple when no selection has been recorded so
    callers can fall back to legacy behaviour (``DEFAULT_RESEARCH_ROLE_SEQUENCE``)
    without forcing a migration of every existing session.
    """

    if not extra:
        return ()
    raw = extra.get("active_research_roles")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(role) for role in raw if isinstance(role, str) and role)
