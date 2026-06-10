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
   :data:`yule_engineering.agents.coding.authorization._DEFAULT_PARTICIPANT_PRIORITY`.
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
# A-M7.5: surface for "전체 팀 관점" / "all roles" override so the
# routing summary can show the operator that fan-out was an explicit
# user request, not the system silently fanning out.
SOURCE_USER_ALL_TEAM: str = "user_all_team"


# A-M7.5: Korean + English phrases that opt-in to a full-team review.
# Substring match against a normalised prompt — these MUST be explicit
# in the user's words; the system never auto-expands to all roles.
_ALL_TEAM_PHRASES: Tuple[str, ...] = (
    "전체 팀",
    "전 직군",
    "모든 관점",
    "모든 역할",
    "전체 관점",
    "전체팀",
    "all roles",
    "all team",
    "every role",
    "전 직군 리뷰",
    "전체 팀 관점",
)


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


def _build_explicit_pattern_index() -> Tuple[Tuple[str, str], ...]:
    """Project ``RoleProfile.explicit_patterns`` into the substring-match
    index the selector uses. Sorted longer-first so ``"ai-engineer"``
    beats ``"ai"`` when both would match — same contract the legacy
    inline literal had, just sourced from the role registry now.
    """

    pairs: list[Tuple[str, str]] = []
    for role_id, profile in all_role_profiles().items():
        for pattern in profile.explicit_patterns:
            cleaned = (pattern or "").strip().lower()
            if cleaned:
                pairs.append((cleaned, role_id))
    # Sort longer first so a more specific pattern claims the match
    # before a shorter substring (e.g. "ai-engineer" > "ai").
    pairs.sort(key=lambda pair: -len(pair[0]))
    return tuple(pairs)


def _detect_explicit_roles(prompt: str) -> Tuple[str, ...]:
    """Return roles named directly in *prompt* (deduplicated, in the
    order the patterns first hit). Empty tuple when the prompt
    doesn't name any role explicitly. Reads explicit_patterns off the
    role profile registry so adding a new alias is a one-profile edit.
    """

    if not prompt:
        return ()
    lowered = prompt.lower()
    found: list[str] = []
    seen: set[str] = set()
    for pattern, role in _build_explicit_pattern_index():
        if pattern in lowered:
            if role not in seen:
                seen.add(role)
                found.append(role)
    return tuple(found)


def _rule_keywords(role: str) -> Tuple[str, ...]:
    """Read activation_keywords off the role profile registry.

    Source of truth shifted to ``RoleProfile.activation_keywords`` —
    extending a domain (k8s, RAG, design system) is now a one-profile
    edit instead of editing the selector keyword bank in two places.
    Returns ``()`` for unknown roles so the caller's score loop is
    safe.
    """

    profiles = all_role_profiles()
    profile = profiles.get(role)
    if profile is None:
        return ()
    return tuple(kw.lower() for kw in profile.activation_keywords if kw)


def _rule_score(role: str, prompt_lower: str) -> int:
    return sum(1 for kw in _rule_keywords(role) if kw in prompt_lower)


def _matched_keywords(role: str, prompt_lower: str) -> Tuple[str, ...]:
    """Return the keywords that fired for *role*. Used by the selector
    to populate ``RoleSelection.matched_keywords_by_role`` so status
    surfaces can show *which* signals carried the role (not just the
    raw count).
    """

    return tuple(kw for kw in _rule_keywords(role) if kw in prompt_lower)


def _participation_for_score(score: int, *, top_score: int) -> str:
    """Map a numeric keyword-bank score to a participation level.

    Buckets:
    - score 0  → excluded (handled by caller, not here)
    - score == top_score and top_score >= 2 → primary
    - score >= 2 (but below top) → primary if it's the lone hit-list
      otherwise reviewer
    - score == 1 → reviewer
    - score < 0 (defensive) → optional
    """

    if score <= 0:
        return PARTICIPATION_EXCLUDED
    if score >= top_score and top_score >= 2:
        return PARTICIPATION_PRIMARY
    if score >= 2:
        return PARTICIPATION_REVIEWER
    return PARTICIPATION_REVIEWER


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

    # 0. A-M7.5 — explicit "전체 팀 / all roles" opt-in always wins.
    # The system never auto-fans-out; only the user's words can.
    if text and _detect_all_team_request(text):
        selected = (ROLE_TECH_LEAD,) + tuple(
            r for r in _EXECUTOR_CANDIDATE_ROLES if r != ROLE_TECH_LEAD
        )
        excluded: Tuple[str, ...] = ()
        participation: dict[str, str] = {
            ROLE_TECH_LEAD: PARTICIPATION_REQUIRED,
        }
        for role in selected:
            if role == ROLE_TECH_LEAD:
                continue
            participation[role] = PARTICIPATION_PRIMARY
        reasons = {
            ROLE_TECH_LEAD: "tech-lead always included",
        }
        for role in selected:
            if role != ROLE_TECH_LEAD:
                reasons[role] = "user requested all-team review"
        return RoleSelection(
            selected_roles=selected,
            excluded_roles=excluded,
            required_roles=(ROLE_TECH_LEAD,),
            optional_roles=(),
            reason_by_role=reasons,
            selection_source=SOURCE_USER_ALL_TEAM,
            participation_by_role=participation,
            primary_roles=tuple(
                r for r in selected if r != ROLE_TECH_LEAD
            ),
            reviewer_roles=(),
            optional_roles_v2=(),
            matched_keywords_by_role={},
            fallback_policy=None,
        )

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
        # Participation: tech-lead is required, user-named non-tech-lead
        # roles are primary (the user explicitly asked for their take).
        participation: dict[str, str] = {
            role: PARTICIPATION_EXCLUDED for role in ALL_ENGINEERING_ROLES
        }
        participation[ROLE_TECH_LEAD] = PARTICIPATION_REQUIRED
        primary: list[str] = []
        for role in explicit:
            if role == ROLE_TECH_LEAD:
                continue
            participation[role] = PARTICIPATION_PRIMARY
            primary.append(role)
        return RoleSelection(
            selected_roles=selected,
            excluded_roles=excluded,
            required_roles=selected,
            optional_roles=(),
            reason_by_role=reasons,
            selection_source=SOURCE_USER_EXPLICIT,
            participation_by_role=participation,
            primary_roles=tuple(primary),
            reviewer_roles=(),
            optional_roles_v2=(),
            matched_keywords_by_role={},
            fallback_policy=None,
        )

    # 2. tech-lead rule — score every member role against
    # ``RoleProfile.activation_keywords``. Track the matched keyword
    # list per role so the status surface can show signals, not just
    # counts.
    if text:
        prompt_lower = text.lower()
        scored: list[tuple[int, str]] = []
        matched_kw: dict[str, Tuple[str, ...]] = {}
        hint_set = {r for r in hint_role_sequence if r in _EXECUTOR_CANDIDATE_ROLES}
        for role in _EXECUTOR_CANDIDATE_ROLES:
            score = _rule_score(role, prompt_lower)
            kws = _matched_keywords(role, prompt_lower)
            # Soft hint bump — a role that has no keyword hit but is
            # carried over from a continuation session still gets a
            # +1 so it appears in the selection. This lets a research
            # restart on the same session keep its team intact.
            if role in hint_set:
                score += 1
            if score > 0:
                scored.append((score, role))
                matched_kw[role] = kws
        if scored:
            priority_map = {r: i for i, r in enumerate(_DEFAULT_PARTICIPANT_PRIORITY)}
            scored.sort(key=lambda t: (-t[0], priority_map.get(t[1], 99)))
            top_score = scored[0][0]
            ordered = [ROLE_TECH_LEAD] + [r for _, r in scored]
            seen: set[str] = set()
            uniq: list[str] = []
            for r in ordered:
                if r not in seen:
                    seen.add(r)
                    uniq.append(r)
            selected = tuple(uniq)
            excluded = tuple(r for r in ALL_ENGINEERING_ROLES if r not in selected)
            participation = {
                role: PARTICIPATION_EXCLUDED for role in ALL_ENGINEERING_ROLES
            }
            participation[ROLE_TECH_LEAD] = PARTICIPATION_REQUIRED
            reasons = {ROLE_TECH_LEAD: "tech-lead always included"}
            primary = []
            reviewer: list[str] = []
            for score, role in scored:
                level = _participation_for_score(score, top_score=top_score)
                participation[role] = level
                hits = matched_kw.get(role, ())
                hit_text = ", ".join(hits[:6]) if hits else "(no direct hit)"
                reasons[role] = f"rule bank score {score} · {level} · {hit_text}"
                if level == PARTICIPATION_PRIMARY:
                    primary.append(role)
                elif level == PARTICIPATION_REVIEWER:
                    reviewer.append(role)
            return RoleSelection(
                selected_roles=selected,
                excluded_roles=excluded,
                required_roles=(ROLE_TECH_LEAD,),
                optional_roles=(),
                reason_by_role=reasons,
                selection_source=SOURCE_TECH_LEAD_RULE,
                participation_by_role=participation,
                primary_roles=tuple(primary),
                reviewer_roles=tuple(reviewer),
                optional_roles_v2=(),
                matched_keywords_by_role=matched_kw,
                fallback_policy=None,
            )

    # 3. fallback — Phase 4 narrows this further; for now keep the
    # legacy quartet as the safety-net default and tag it explicitly
    # so the supervisor can read the policy id.
    return _build_fallback_selection(text)


# Phase 4 narrow fallback hint vocabularies. These fire ONLY when no
# profile activation_keyword scored — they're a coarse domain pivot
# for vague prompts ("개발 관련해서 봐줘") that don't contain any
# specific signal a profile already covers. Words listed here are
# intentionally NOT in profile activation_keywords (otherwise the
# rule branch would have caught them first).
_VAGUE_INFRA_HINTS: Tuple[str, ...] = (
    "서버",
    "server",
    "프로덕션",
    "production",
    "실서비스",
    "운영체제",
    "production env",
    "스테이징",
)
_VAGUE_AI_RESEARCH_HINTS: Tuple[str, ...] = (
    "기계학습",
    "ml 모델",
    "지식 베이스",
    "데이터셋",
    "dataset",
)
_VAGUE_PRODUCT_HINTS: Tuple[str, ...] = (
    "사용자 경험",
    "사용성 검토",
    "온보딩 흐름",
    "퍼소나",
    "유저 흐름",
)
_VAGUE_ENGINEERING_HINTS: Tuple[str, ...] = (
    "개발",
    "코드",
    "code",
    "feature",
    "기능",
    "리팩",
    "리팩터",
    "버그",
    "결함",
)


# Mapping fallback_policy → (selected_roles, primary, reviewer). Used
# by ``_build_fallback_selection`` so the policy and the role list
# stay co-located. tech-lead is always REQUIRED implicitly.
_FALLBACK_POLICY_TEAMS: Mapping[str, Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]] = {
    # policy → (selected (excluding tech-lead), primary roles, reviewer roles)
    FALLBACK_VAGUE_INFRA: (
        ("devops-engineer", "backend-engineer"),
        ("devops-engineer",),
        ("backend-engineer",),
    ),
    FALLBACK_VAGUE_AI_RESEARCH: (
        ("ai-engineer", "backend-engineer"),
        ("ai-engineer",),
        ("backend-engineer",),
    ),
    FALLBACK_VAGUE_PRODUCT: (
        ("product-designer", "frontend-engineer"),
        ("product-designer",),
        ("frontend-engineer",),
    ),
    FALLBACK_VAGUE_ENGINEERING: (
        ("backend-engineer", "qa-engineer"),
        ("backend-engineer",),
        ("qa-engineer",),
    ),
}


def _classify_vague_fallback(prompt_lower: str) -> Optional[str]:
    """Map a no-match prompt to a narrow fallback policy id, or ``None``.

    Order matters: more specific buckets win when hint vocabularies
    overlap. Today they don't share any tokens by design — keeping the
    order documents intent ("infra hint takes precedence over generic
    engineering hint" if we ever extend the vocab).
    """

    if any(h in prompt_lower for h in _VAGUE_INFRA_HINTS):
        return FALLBACK_VAGUE_INFRA
    if any(h in prompt_lower for h in _VAGUE_AI_RESEARCH_HINTS):
        return FALLBACK_VAGUE_AI_RESEARCH
    if any(h in prompt_lower for h in _VAGUE_PRODUCT_HINTS):
        return FALLBACK_VAGUE_PRODUCT
    if any(h in prompt_lower for h in _VAGUE_ENGINEERING_HINTS):
        return FALLBACK_VAGUE_ENGINEERING
    return None


def _build_fallback_selection(text: str) -> RoleSelection:
    """Return the fallback :class:`RoleSelection` for *text*.

    Layered policy (first match wins):

    1. Empty prompt → ``empty_prompt`` policy, tech-lead only.
    2. Vague hint → ``vague_infra`` / ``vague_ai_research`` /
       ``vague_product`` / ``vague_engineering`` — picks a focused
       2-role pair per :data:`_FALLBACK_POLICY_TEAMS` so unrelated
       roles don't auto-join a domain-shaped vague prompt.
    3. Otherwise → ``legacy_quartet`` (tech-lead + ai + backend + qa)
       so the gateway never produces a zero-participant session.
    """

    if not text:
        selected = (ROLE_TECH_LEAD,)
        excluded = tuple(r for r in ALL_ENGINEERING_ROLES if r not in selected)
        participation = {
            role: PARTICIPATION_EXCLUDED for role in ALL_ENGINEERING_ROLES
        }
        participation[ROLE_TECH_LEAD] = PARTICIPATION_REQUIRED
        return RoleSelection(
            selected_roles=selected,
            excluded_roles=excluded,
            required_roles=(ROLE_TECH_LEAD,),
            optional_roles=(),
            reason_by_role={ROLE_TECH_LEAD: "fallback (empty prompt)"},
            selection_source=SOURCE_FALLBACK,
            participation_by_role=participation,
            primary_roles=(),
            reviewer_roles=(),
            optional_roles_v2=(),
            matched_keywords_by_role={},
            fallback_policy=FALLBACK_EMPTY_PROMPT,
        )

    prompt_lower = text.lower()
    vague_policy = _classify_vague_fallback(prompt_lower)
    if vague_policy is not None:
        team, primary_team, reviewer_team = _FALLBACK_POLICY_TEAMS[vague_policy]
        selected = (ROLE_TECH_LEAD,) + team
        excluded = tuple(r for r in ALL_ENGINEERING_ROLES if r not in selected)
        participation = {
            role: PARTICIPATION_EXCLUDED for role in ALL_ENGINEERING_ROLES
        }
        participation[ROLE_TECH_LEAD] = PARTICIPATION_REQUIRED
        for role in primary_team:
            participation[role] = PARTICIPATION_PRIMARY
        for role in reviewer_team:
            if participation.get(role) != PARTICIPATION_PRIMARY:
                participation[role] = PARTICIPATION_REVIEWER
        reasons = {ROLE_TECH_LEAD: f"fallback ({vague_policy}) — tech-lead always required"}
        for role in primary_team:
            reasons[role] = f"fallback ({vague_policy}) — vague-domain primary"
        for role in reviewer_team:
            if role not in primary_team:
                reasons[role] = f"fallback ({vague_policy}) — vague-domain reviewer"
        return RoleSelection(
            selected_roles=selected,
            excluded_roles=excluded,
            required_roles=(ROLE_TECH_LEAD,),
            optional_roles=(),
            reason_by_role=reasons,
            selection_source=SOURCE_FALLBACK,
            participation_by_role=participation,
            primary_roles=tuple(primary_team),
            reviewer_roles=tuple(r for r in reviewer_team if r not in primary_team),
            optional_roles_v2=(),
            matched_keywords_by_role={},
            fallback_policy=vague_policy,
        )

    # Non-empty + no domain hint → keep the legacy quartet as the
    # conservative safety net so the gateway never returns zero
    # participants when the user just said "안녕하세요".
    selected = _FALLBACK_SELECTED
    excluded = tuple(r for r in ALL_ENGINEERING_ROLES if r not in selected)
    fallback_reason = "fallback (no keyword match in rule bank)"
    reasons = {role: fallback_reason for role in selected}
    participation = {
        role: PARTICIPATION_EXCLUDED for role in ALL_ENGINEERING_ROLES
    }
    participation[ROLE_TECH_LEAD] = PARTICIPATION_REQUIRED
    reviewer = []
    for role in selected:
        if role == ROLE_TECH_LEAD:
            continue
        participation[role] = PARTICIPATION_REVIEWER
        reviewer.append(role)
    return RoleSelection(
        selected_roles=selected,
        excluded_roles=excluded,
        required_roles=(ROLE_TECH_LEAD,),
        optional_roles=(),
        reason_by_role=reasons,
        selection_source=SOURCE_FALLBACK,
        participation_by_role=participation,
        primary_roles=(),
        reviewer_roles=tuple(reviewer),
        optional_roles_v2=(),
        matched_keywords_by_role={},
        fallback_policy=FALLBACK_LEGACY_QUARTET,
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


# ---------------------------------------------------------------------------
# A-M7.5 — single source of truth for "who is allowed to speak"
# ---------------------------------------------------------------------------


def _detect_all_team_request(prompt: str) -> bool:
    """Whether *prompt* explicitly opts in to a full-team review.

    Detection is intentionally **strict** — only literal phrases in
    :data:`_ALL_TEAM_PHRASES` count. The system must never silently
    fan out to all roles based on a vague signal; the user has to
    say it.
    """

    if not prompt:
        return False
    lowered = prompt.lower()
    return any(phrase in lowered for phrase in _ALL_TEAM_PHRASES)


def get_effective_active_roles(
    session: Any,
    *,
    fallback_role_sequence: bool = True,
    minimum_fallback: Tuple[str, ...] = (ROLE_TECH_LEAD,),
) -> Tuple[str, ...]:
    """A-M7.5 — canonical active-roles resolver shared by every code path.

    Resolution order, first non-empty wins:

      1. ``session.extra['active_research_roles']`` — the role-selection
         output. Authoritative when present.
      2. ``session.role_sequence`` — legacy ordered hint, only when
         *fallback_role_sequence* is True.
      3. *minimum_fallback* — defaults to ``("tech-lead",)`` so the
         worst-case response is "tech-lead triages alone", never
         "fan out to every role".

    tech-lead is implicitly added to the front of any non-empty
    result so the synthesis runner always has a closer.

    Used by:

      * :func:`engineering_team_runtime.deliberation_research_role_sequence`
      * team-turn legacy path (``build_turn_plan`` / ``next_pending_turn``
        / ``handle_team_turn_message``)
      * standalone synthesis runner's degrade/fallback scan
      * forum Obsidian handoff routing summary
    """

    # 1. session.extra['active_research_roles']
    extra = getattr(session, "extra", None) if session is not None else None
    persisted = active_roles_from_extra(extra)
    if persisted:
        return _ensure_tech_lead_first(persisted)

    # 2. legacy role_sequence hint
    if fallback_role_sequence and session is not None:
        seq = tuple(getattr(session, "role_sequence", ()) or ())
        if seq:
            return _ensure_tech_lead_first(
                tuple(str(role) for role in seq if isinstance(role, str) and role)
            )

    # 3. minimum fallback — never zero participants, never all roles.
    return _ensure_tech_lead_first(tuple(minimum_fallback))


def _ensure_tech_lead_first(roles: Sequence[str]) -> Tuple[str, ...]:
    """Return *roles* with tech-lead at index 0 and dedup preserved.

    Empty input collapses to ``(tech-lead,)`` so downstream callers
    never see a zero-participant tuple — the synthesis runner needs
    at least one closer.
    """

    cleaned: list[str] = []
    seen: set[str] = set()
    for role in roles:
        short = (role or "").split("/", 1)[-1].strip()
        if not short or short in seen:
            continue
        cleaned.append(short)
        seen.add(short)
    if not cleaned:
        return (ROLE_TECH_LEAD,)
    if ROLE_TECH_LEAD in cleaned:
        cleaned.remove(ROLE_TECH_LEAD)
    return (ROLE_TECH_LEAD,) + tuple(cleaned)



# ---------------------------------------------------------------------------
# A-M7.5 — tech-lead routing summary for the operations-research kickoff
# ---------------------------------------------------------------------------


def format_routing_summary(
    selection: RoleSelection,
    *,
    request_label: Optional[str] = None,
) -> str:
    """Render a 3–5 line tech-lead summary of who participates and why.

    Used at thread kickoff (gateway / tech-lead first message) so the
    operator sees the routing decision in plain Korean. The format
    follows the spec example — request label, selected roles, waiting
    roles, add-instruction, next step.

    Empty / fallback selections produce a "tech-lead triage 만 진행"
    line so the user knows the system didn't silently fan out.
    """

    selected = tuple(selection.selected_roles or ())
    excluded = tuple(selection.excluded_roles or ())
    primary = tuple(selection.primary_roles or ())

    lines: list[str] = []
    label = (request_label or "").strip()
    if label:
        lines.append(f"이번 요청은 {label} 성격으로 봤어요.")

    if selection.selection_source == SOURCE_USER_ALL_TEAM:
        lines.append("참여 역할: 전체 팀 (사용자가 명시적으로 요청)")
    elif selected:
        lines.append("참여 역할: " + ", ".join(selected))
    else:
        lines.append("참여 역할: tech-lead 만 (triage)")

    if excluded:
        lines.append(
            "대기 역할: "
            + ", ".join(excluded)
            + " — 이번 답변의 직접 실행 표면이 아니라 대기합니다."
        )

    lines.append(
        "추가로 부르고 싶은 역할이 있으면 “QA도 참여시켜” / “전체 팀 관점으로 봐줘” 처럼 말해 주세요."
    )

    next_steps: list[str] = []
    for role in primary:
        if role == ROLE_TECH_LEAD:
            continue
        next_steps.append(f"{role} 가 자기 관점으로 정리")
    if not next_steps:
        next_steps.append("tech-lead 가 토의 안건을 잡고 진행")
    else:
        next_steps.append("tech-lead 가 토의 안건과 합의 초안을 정리")
    lines.append("다음 단계: " + " · ".join(next_steps) + ".")

    return "\n".join(lines)
