"""Coding Agent Authorization MVP — proposal model + role recommender.

The engineering-agent gateway needs a small, deterministic surface that
turns a user's "이 작업 코딩으로 진행해줘" request into a structured
proposal Tech Lead can show the user before any code is written. This
module is the pure-Python core: it loads role profiles from
``agents/engineering-agent/<role>/agent.json``, scores each role
against the user request using the role's
``default_executor_priority`` keyword bank, and produces a
:class:`CodingAuthorizationProposal` with executor / review /
participant role assignments + write/forbidden scope.

Design choices:

- No I/O beyond loading the role profile JSON files (cached).
- No Discord/network. The Discord layer wraps this module to render
  the proposal and ask the user for approval.
- Tech-lead always lands as a reviewer so the gateway's "단일 executor
  + tech-lead 합의" contract from CLAUDE.md stays intact.
- When two roles tie, the order in
  :data:`_DEFAULT_PARTICIPANT_PRIORITY` breaks the tie deterministically
  so tests have a stable result.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Role catalogue
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[3]
DEPARTMENT_DIR = REPO_ROOT / "agents" / "engineering-agent"


# All role ids participating in the executor selection. ``tech-lead`` is
# excluded from the executor pool (it's always reviewer); ``gateway``
# isn't a member role at all.
_EXECUTOR_CANDIDATE_ROLES: Tuple[str, ...] = (
    "backend-engineer",
    "frontend-engineer",
    "ai-engineer",
    "devops-engineer",
    "qa-engineer",
    "product-designer",
)


# Tie-break order — left wins on equal scores. Backend-first because
# auth/database/Spring Security cases are the highest-blast-radius and
# we want them to land deterministically when a request mentions both
# (e.g. "API와 UI를 동시에 손봐줘").
_DEFAULT_PARTICIPANT_PRIORITY: Tuple[str, ...] = (
    "backend-engineer",
    "ai-engineer",
    "devops-engineer",
    "frontend-engineer",
    "qa-engineer",
    "product-designer",
)


# Score weights for the keyword bank in each role's default_executor_priority.
_WEIGHT_HIGH = 3.0
_WEIGHT_MEDIUM = 1.5
_WEIGHT_LOW = -1.0  # Discourage instead of reward.


# Lightweight role profile cache. Populated lazily; tests can reset via
# ``reset_role_profile_cache()`` to inject fakes.
_ROLE_PROFILE_CACHE: dict[str, Mapping[str, object]] = {}


def reset_role_profile_cache() -> None:
    """Clear the on-process role profile cache. Tests can call this when
    they need to swap in a temporary department layout."""

    _ROLE_PROFILE_CACHE.clear()


def load_role_profile(role: str, *, department_dir: Optional[Path] = None) -> Mapping[str, object]:
    """Read ``agents/engineering-agent/<role>/agent.json``.

    Cached after the first read. Pass ``department_dir`` when running
    tests against a fixture tree.
    """

    cache_key = f"{department_dir}::{role}"
    cached = _ROLE_PROFILE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    base = department_dir or DEPARTMENT_DIR
    path = base / role / "agent.json"
    raw = path.read_text(encoding="utf-8")
    profile = json.loads(raw)
    _ROLE_PROFILE_CACHE[cache_key] = profile
    return profile


# ---------------------------------------------------------------------------
# Public model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodingAuthorizationProposal:
    """Tech-lead's authorization proposal for a coding job.

    The Discord gateway renders this for the user; on approval the
    fields land on a :class:`CodingJob` (next commit) that an executor
    role can run with.
    """

    session_id: Optional[str]
    user_request: str
    executor_role: str
    review_roles: Tuple[str, ...]
    participant_roles: Tuple[str, ...]
    write_scope: Tuple[str, ...]
    forbidden_scope: Tuple[str, ...]
    reason: str
    safety_rules: Tuple[str, ...]
    approval_required: bool = True
    metadata: Mapping[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Recommender
# ---------------------------------------------------------------------------


_DEFAULT_SAFETY_RULES: Tuple[str, ...] = (
    "사용자 승인 phrase가 도착하기 전 어떤 production write도 시작하지 않는다",
    "수정 전 요약된 계획을 먼저 사용자에게 보여 준다",
    "secret / .env / 운영 자격 증명에 접근하지 않는다",
    "git reset --hard / git push --force / 자동 deploy 같은 destructive 명령을 실행하지 않는다",
    "write_scope 밖의 파일을 수정하지 않는다",
    "변경 전후 관련 단위/통합 테스트를 실행하고 결과를 보고한다",
)


def recommend_authorization(
    *,
    user_request: str,
    session_id: Optional[str] = None,
    department_dir: Optional[Path] = None,
    role_profile_loader: Optional[
        Mapping[str, Mapping[str, object]]
    ] = None,
) -> CodingAuthorizationProposal:
    """Pick executor + review + participant roles for *user_request*.

    Pipeline:
      1. Load each candidate role's profile.
      2. Score each role using its ``default_executor_priority``
         keyword bank (high=+3, medium=+1.5, low=−1) against the
         normalised request text.
      3. Pick the top scorer as ``executor_role``. Ties are broken by
         :data:`_DEFAULT_PARTICIPANT_PRIORITY`.
      4. Roles whose ``default_reviewer_priority.high`` mentions a
         relevant area are added to ``review_roles``. tech-lead is
         always a reviewer (single-executor + tech-lead consensus).
      5. ``write_scope`` / ``forbidden_scope`` come from the executor
         role's profile.
    """

    request_text = (user_request or "").strip()
    if not request_text:
        # Empty request → fall back to tech-lead as gateway/coordinator
        # so the caller can ask the user for more detail.
        return _fallback_proposal(
            user_request=request_text,
            session_id=session_id,
            reason="user request is empty — escalating to tech-lead for clarification",
        )

    normalised = request_text.lower()

    profiles: dict[str, Mapping[str, object]] = {}
    if role_profile_loader is not None:
        for role, profile in role_profile_loader.items():
            profiles[role] = profile
    else:
        for role in _EXECUTOR_CANDIDATE_ROLES + ("tech-lead",):
            try:
                profiles[role] = load_role_profile(
                    role, department_dir=department_dir
                )
            except FileNotFoundError:
                # A role without a profile cannot be considered — skip
                # silently so partial layouts (e.g. test fixtures) work.
                continue

    scored: list[tuple[float, int, str]] = []
    for role in _EXECUTOR_CANDIDATE_ROLES:
        profile = profiles.get(role)
        if profile is None:
            continue
        score = _score_executor(profile, normalised)
        tie_break = _DEFAULT_PARTICIPANT_PRIORITY.index(role)
        scored.append((score, tie_break, role))

    if not scored:
        return _fallback_proposal(
            user_request=request_text,
            session_id=session_id,
            reason="no executor candidates available",
        )

    # Sort by score desc, tie-break asc.
    scored.sort(key=lambda item: (-item[0], item[1]))
    top_score, _, top_role = scored[0]

    if top_score <= 0:
        # No keyword matched — likely an under-specified request. Still
        # return a tech-lead-led fallback so the gateway asks the user
        # to specify the area instead of silently picking backend.
        return _fallback_proposal(
            user_request=request_text,
            session_id=session_id,
            reason=(
                "no domain keyword matched any role — tech-lead should clarify "
                "with the user (frontend / backend / ai / devops 중 어느 영역인지)"
            ),
        )

    executor_profile = profiles[top_role]
    write_scope = tuple(_string_list(executor_profile.get("write_scope_candidates", ())))
    forbidden_scope = tuple(_string_list(executor_profile.get("forbidden_scope", ())))

    review_roles = _resolve_review_roles(
        executor_role=top_role,
        profiles=profiles,
        normalised=normalised,
    )
    participant_roles = _resolve_participant_roles(
        executor_role=top_role,
        review_roles=review_roles,
        profiles=profiles,
        normalised=normalised,
        scored=scored,
    )

    domain_focus = str(executor_profile.get("domain_focus", "")).strip()
    reason = _format_reason(
        executor_role=top_role,
        domain_focus=domain_focus,
        score=top_score,
        normalised=normalised,
        executor_profile=executor_profile,
    )

    return CodingAuthorizationProposal(
        session_id=session_id,
        user_request=request_text,
        executor_role=top_role,
        review_roles=review_roles,
        participant_roles=participant_roles,
        write_scope=write_scope,
        forbidden_scope=forbidden_scope,
        reason=reason,
        safety_rules=_DEFAULT_SAFETY_RULES,
        approval_required=True,
        metadata={
            "executor_score": top_score,
            "scored_roles": tuple(
                {"role": role, "score": score} for score, _, role in scored
            ),
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _score_executor(profile: Mapping[str, object], normalised_request: str) -> float:
    """Score a role's executor fit against *normalised_request*.

    Sums weighted hits from ``default_executor_priority`` buckets. We
    compare against a lowercased haystack so the role-side keywords can
    use either Korean or English without the caller normalising twice.
    """

    bucket = profile.get("default_executor_priority")
    if not isinstance(bucket, Mapping):
        return 0.0
    score = 0.0
    score += _WEIGHT_HIGH * _count_hits(_string_list(bucket.get("high", ())), normalised_request)
    score += _WEIGHT_MEDIUM * _count_hits(_string_list(bucket.get("medium", ())), normalised_request)
    score += _WEIGHT_LOW * _count_hits(_string_list(bucket.get("low", ())), normalised_request)
    return score


def _count_hits(keywords: Sequence[str], haystack: str) -> int:
    return sum(1 for kw in keywords if kw and kw.lower() in haystack)


def _resolve_review_roles(
    *,
    executor_role: str,
    profiles: Mapping[str, Mapping[str, object]],
    normalised: str,
) -> Tuple[str, ...]:
    """Always include tech-lead. Add other roles whose reviewer high-
    bucket keywords match the request, except the executor role itself.
    """

    review: list[str] = []
    if "tech-lead" in profiles:
        review.append("tech-lead")
    # qa-engineer is a default reviewer for every executor — single
    # executor + qa review is the policy from the role profile.
    if "qa-engineer" in profiles and "qa-engineer" != executor_role:
        review.append("qa-engineer")

    for role in _EXECUTOR_CANDIDATE_ROLES:
        if role == executor_role or role in review:
            continue
        profile = profiles.get(role)
        if profile is None:
            continue
        bucket = profile.get("default_reviewer_priority")
        if not isinstance(bucket, Mapping):
            continue
        if _count_hits(_string_list(bucket.get("high", ())), normalised) > 0:
            review.append(role)

    # Stable order: tech-lead first, then by participant priority.
    review_set = set(review)
    ordered = ["tech-lead"] if "tech-lead" in review_set else []
    for role in _DEFAULT_PARTICIPANT_PRIORITY:
        if role in review_set and role not in ordered:
            ordered.append(role)
    return tuple(ordered)


def _resolve_participant_roles(
    *,
    executor_role: str,
    review_roles: Tuple[str, ...],
    profiles: Mapping[str, Mapping[str, object]],
    normalised: str,
    scored: Sequence[Tuple[float, int, str]],
) -> Tuple[str, ...]:
    """Roles that ought to chime in even if they don't review.

    Includes the executor itself + any role with a positive executor
    score (so a 'frontend + backend 동시 검토' request gets both into
    participant_roles even if frontend wins as executor).
    """

    participants: list[str] = [executor_role]
    for role in review_roles:
        if role not in participants:
            participants.append(role)
    for score, _tie, role in scored:
        if score > 0 and role not in participants:
            participants.append(role)
    return tuple(participants)


def _fallback_proposal(
    *,
    user_request: str,
    session_id: Optional[str],
    reason: str,
) -> CodingAuthorizationProposal:
    """Tech-lead-led proposal for under-specified or empty requests."""

    return CodingAuthorizationProposal(
        session_id=session_id,
        user_request=user_request,
        executor_role="tech-lead",
        review_roles=("tech-lead",),
        participant_roles=("tech-lead",),
        write_scope=(),
        forbidden_scope=(
            "production 코드 직접 변경",
            "secret / .env 접근",
            "사용자 승인 없는 destructive command",
        ),
        reason=reason,
        safety_rules=_DEFAULT_SAFETY_RULES,
        approval_required=True,
        metadata={"fallback": True},
    )


def _format_reason(
    *,
    executor_role: str,
    domain_focus: str,
    score: float,
    normalised: str,
    executor_profile: Mapping[str, object],
) -> str:
    """One-sentence rationale showing which role keywords matched."""

    bucket = executor_profile.get("default_executor_priority")
    high_keywords = (
        _string_list(bucket.get("high", ())) if isinstance(bucket, Mapping) else ()
    )
    matched = [kw for kw in high_keywords if kw and kw.lower() in normalised]
    if matched:
        sample = ", ".join(matched[:5])
        return (
            f"{executor_role}: '{domain_focus or executor_role}' 영역의 핵심 키워드 "
            f"({sample})가 요청에 포함되어 executor로 추천 (score={score:.1f})"
        )
    return (
        f"{executor_role}: 도메인 매칭 점수 {score:.1f}로 가장 높음 "
        f"({domain_focus or executor_role})"
    )


def _string_list(value: object) -> Tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if item is not None)
    return ()


# ---------------------------------------------------------------------------
# Discord-friendly rendering
# ---------------------------------------------------------------------------


def format_authorization_message(proposal: CodingAuthorizationProposal) -> str:
    """Render *proposal* as a short Korean message the gateway can post.

    Kept separate from the recommender so callers can tailor the
    presentation per surface (Discord vs. CLI vs. unit-test snapshot).
    """

    lines = [
        "**[engineering-agent] 코딩 권한 제안**",
        "",
        f"요청: {proposal.user_request}",
        f"executor: `{proposal.executor_role}`",
    ]
    if proposal.review_roles:
        lines.append("reviewers: " + ", ".join(f"`{r}`" for r in proposal.review_roles))
    if proposal.participant_roles:
        lines.append(
            "participants: " + ", ".join(f"`{r}`" for r in proposal.participant_roles)
        )
    if proposal.write_scope:
        lines.append("")
        lines.append("**write scope**")
        for scope in proposal.write_scope:
            lines.append(f"- {scope}")
    if proposal.forbidden_scope:
        lines.append("")
        lines.append("**forbidden scope**")
        for scope in proposal.forbidden_scope:
            lines.append(f"- {scope}")
    lines.append("")
    lines.append(f"이유: {proposal.reason}")
    if proposal.safety_rules:
        lines.append("")
        lines.append("**safety rules**")
        for rule in proposal.safety_rules:
            lines.append(f"- {rule}")
    if proposal.approval_required:
        lines.append("")
        lines.append(
            "이 권한을 그대로 진행하려면 `수정 승인` / `이대로 구현 진행` / `구현 시작`이라고 답해 주세요."
        )
    return "\n".join(lines)


__all__ = (
    "CodingAuthorizationProposal",
    "format_authorization_message",
    "load_role_profile",
    "recommend_authorization",
    "reset_role_profile_cache",
)
