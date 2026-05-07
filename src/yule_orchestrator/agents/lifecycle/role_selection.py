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


def _build_fallback_selection(text: str) -> RoleSelection:
    """Return the fallback :class:`RoleSelection` for *text*.

    Phase 4 will expand this with vague_engineering / vague_ai_research
    / vague_product / vague_infra branches; today it returns the legacy
    "always-on quartet" for any non-empty prompt and a tech-lead-only
    selection when the prompt is empty. The :attr:`fallback_policy`
    field is populated in both branches so the supervisor / docs can
    explain *which* fallback fired.
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

    # Non-empty but no keyword hit — keep the historical "always-on
    # quartet" until Phase 4 narrows the bucket. The fallback_policy
    # tag explains which branch fired so docs / status can describe it.
    selected = _FALLBACK_SELECTED
    excluded = tuple(r for r in ALL_ENGINEERING_ROLES if r not in selected)
    fallback_reason = "fallback (no keyword match in rule bank)"
    reasons = {role: fallback_reason for role in selected}
    participation = {
        role: PARTICIPATION_EXCLUDED for role in ALL_ENGINEERING_ROLES
    }
    participation[ROLE_TECH_LEAD] = PARTICIPATION_REQUIRED
    reviewer: list[str] = []
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
