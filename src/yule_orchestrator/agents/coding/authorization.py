"""Coding Agent Authorization MVP — proposal model + role recommender.

The engineering-agent gateway needs a small, deterministic surface that
turns a user's "이 작업 코딩으로 진행해줘" request into a structured
proposal Tech Lead can show the user before any code is written. This
module is the pure-Python core: it loads role profiles from
``agents/engineering-agent/<role>/manifest.json``, scores each role
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


REPO_ROOT = Path(__file__).resolve().parents[4]
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
    """Read ``agents/engineering-agent/<role>/manifest.json``.

    Cached after the first read. Pass ``department_dir`` when running
    tests against a fixture tree.
    """

    cache_key = f"{department_dir}::{role}"
    cached = _ROLE_PROFILE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    base = department_dir or DEPARTMENT_DIR
    path = base / role / "manifest.json"
    raw = path.read_text(encoding="utf-8")
    profile = json.loads(raw)
    _ROLE_PROFILE_CACHE[cache_key] = profile
    return profile


# ---------------------------------------------------------------------------
# Public model
# ---------------------------------------------------------------------------


LIFECYCLE_MODE_IMPLEMENTATION = "implementation"
LIFECYCLE_MODE_RESEARCH_ONLY = "research_only"


@dataclass(frozen=True)
class CodingAuthorizationProposal:
    """Tech-lead's authorization proposal for a coding job.

    The Discord gateway renders this for the user; on approval the
    fields land on a :class:`CodingJob` (next commit) that an executor
    role can run with.

    ``lifecycle_mode`` distinguishes implementation work (default —
    pick an executor, request approval) from research-only requests
    where no code will be written. Research-only proposals leave
    ``executor_role`` empty and surface ``research_leads`` instead so
    the gateway shows "조사 중심 역할" rather than "실행 후보".
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
    lifecycle_mode: str = LIFECYCLE_MODE_IMPLEMENTATION
    research_leads: Tuple[str, ...] = ()


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


_RESEARCH_ONLY_SAFETY_RULES: Tuple[str, ...] = (
    "조사 단계에서는 코드/문서/설정 어떤 파일도 수정하지 않는다",
    "secret / .env / 운영 자격 증명에 접근하지 않는다",
    "조사 결과는 research_pack에 기록하고 출처(URL 또는 레퍼런스)를 함께 남긴다",
    "구현이 필요해지면 사용자가 '수정 권한 제안' 또는 '구현 진행'을 요청할 때까지 기다린다",
)


# Phrases that flip a request from implementation into research-only.
# Mirrors RESEARCH_ONLY_PHRASES on the discord side but lives here so
# the recommender stays usable from CLI / tests without importing the
# discord layer.
_RESEARCH_ONLY_PHRASES: Tuple[str, ...] = (
    "코드 수정 없이",
    "코드 수정없이",
    "코드 수정은 없",
    "코드 수정하지 말",
    "코드 수정 하지 말",
    "코드 수정 금지",
    "수정 없이 자료",
    "수정하지 말고 리서치",
    "수정 하지 말고 리서치",
    "수정하지 말고 조사",
    "자료 수집이 목표",
    "자료수집이 목표",
    "자료 수집만",
    "자료수집만",
    "리서치만 해",
    "리서치만 정리",
    "조사만 해",
    "조사만 정리",
    "조사해줘",
    "조사 해줘",
    "리서치해줘",
    "리서치 해줘",
    "정리까지만",
    "정리 까지만",
    "코딩 하지 말고",
    "코딩하지 말고",
    "no code change",
    "research only",
    "research-only",
)


def _is_research_only_text(normalised_request: str) -> bool:
    if not normalised_request:
        return False
    flat = " ".join(normalised_request.split())
    return any(phrase in flat for phrase in _RESEARCH_ONLY_PHRASES)


def _research_leads_from_scored(
    scored: Sequence[Tuple[float, int, str]],
    *,
    user_request: str,
    limit: int = 3,
) -> Tuple[str, ...]:
    """Pick the top scoring roles as research_leads.

    The executor scoring uses each role's ``default_executor_priority``
    bucket which is *coding*-flavoured (Spring Security, Docker, RAG)
    and doesn't reliably cover infrastructure research keywords like
    "k8s" or "ingress". For research-only intent we therefore consult
    the role-selection rule bank as well — that one knows the live
    Kubernetes / RAG / dashboard regression set from Phase 3 — and
    union the two views together so devops + backend lead a k8s
    research request even when no executor keyword fired.
    """

    leads: list[str] = []
    for score, _, role in scored:
        if score > 0 and role not in leads:
            leads.append(role)

    try:
        # Lazy import to avoid the role_selection ↔ coding.authorization
        # module-load cycle (role_selection already pulls
        # _DEFAULT_PARTICIPANT_PRIORITY from this module).
        from ..lifecycle.role_selection import recommend_active_roles
    except Exception:  # noqa: BLE001 - degrade silently if unavailable
        recommend_active_roles = None  # type: ignore[assignment]

    if recommend_active_roles is not None:
        try:
            selection = recommend_active_roles(user_prompt=user_request)
        except Exception:  # noqa: BLE001
            selection = None
        if selection is not None:
            for role in selection.selected_roles:
                # tech-lead always opens the chain elsewhere, so don't
                # double-count it as a research_lead. Keep the user's
                # active research roles even when the executor scorer
                # didn't surface them.
                if role == "tech-lead":
                    continue
                if role not in leads:
                    leads.append(role)

    if not leads:
        return ("backend-engineer",)
    return tuple(leads[:limit])


def _research_only_proposal(
    *,
    user_request: str,
    session_id: Optional[str],
    research_leads: Tuple[str, ...],
    scored: Sequence[Tuple[float, int, str]],
) -> CodingAuthorizationProposal:
    """Authorization proposal for a research-only request.

    No executor is selected, write_scope is empty, approval is *not*
    required (the user explicitly said no code change yet). The Discord
    gateway renders ``research_leads`` instead of ``executor_role``.
    """

    leads_for_reason = ", ".join(research_leads) if research_leads else "tech-lead"
    reason = (
        f"research-only 요청: 조사 중심 역할 {leads_for_reason} 가 자료를 수집한다. "
        "구현이 필요하면 사용자가 별도로 '수정 권한 제안'을 요청해야 한다."
    )
    return CodingAuthorizationProposal(
        session_id=session_id,
        user_request=user_request,
        executor_role="",
        review_roles=("tech-lead",),
        participant_roles=("tech-lead",) + tuple(
            role for role in research_leads if role != "tech-lead"
        ),
        write_scope=(),
        forbidden_scope=(
            "코드/문서/설정 파일 수정",
            "secret / .env 접근",
            "사용자 승인 없는 destructive command",
        ),
        reason=reason,
        safety_rules=_RESEARCH_ONLY_SAFETY_RULES,
        approval_required=False,
        metadata={
            "lifecycle_mode": LIFECYCLE_MODE_RESEARCH_ONLY,
            "research_leads": list(research_leads),
            "scored_roles": tuple(
                {"role": role, "score": score} for score, _, role in scored
            ),
        },
        lifecycle_mode=LIFECYCLE_MODE_RESEARCH_ONLY,
        research_leads=research_leads,
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

    # Research-only intent short-circuits executor selection. We still
    # keep the scored ranking so Tech Lead can highlight which roles
    # should lead the investigation.
    if _is_research_only_text(normalised):
        leads = _research_leads_from_scored(scored, user_request=request_text)
        return _research_only_proposal(
            user_request=request_text,
            session_id=session_id,
            research_leads=leads,
            scored=scored,
        )

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
    Research-only proposals get a different header + body — no
    executor pick, no write scope, and a softer follow-up phrase that
    asks the user to opt in to implementation explicitly.
    """

    if proposal.lifecycle_mode == LIFECYCLE_MODE_RESEARCH_ONLY:
        return _format_research_only_message(proposal)

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


def _format_research_only_message(proposal: CodingAuthorizationProposal) -> str:
    leads = proposal.research_leads or ("tech-lead",)
    leads_text = ", ".join(f"`{r}`" for r in leads)
    lines = [
        "**[engineering-agent] 조사 단계 — 코드 수정은 하지 않습니다**",
        "",
        f"요청: {proposal.user_request}",
        f"조사 중심 역할: {leads_text}",
    ]
    if proposal.review_roles:
        lines.append(
            "검토 역할: " + ", ".join(f"`{r}`" for r in proposal.review_roles)
        )
    if proposal.forbidden_scope:
        lines.append("")
        lines.append("**조사 단계 금지 사항**")
        for scope in proposal.forbidden_scope:
            lines.append(f"- {scope}")
    lines.append("")
    lines.append(f"이유: {proposal.reason}")
    if proposal.safety_rules:
        lines.append("")
        lines.append("**safety rules**")
        for rule in proposal.safety_rules:
            lines.append(f"- {rule}")
    lines.append("")
    lines.append(
        "조사 결과를 보고 실제 구현이 필요해지면 `수정 권한 제안` 또는 `구현 진행`이라고 답해 주세요."
    )
    return "\n".join(lines)


def proposal_from_dict(payload: Mapping[str, object]) -> CodingAuthorizationProposal:
    """Re-hydrate a :class:`CodingAuthorizationProposal` from session.extra.

    The Discord coding gate persists proposals via ``to_dict`` (mirror in
    ``discord/engineering_channel_router/session_persistence.py``). Both
    the Discord chat approval path AND the ``/engineer_approve`` slash
    command need to rebuild the proposal to derive the executor coding_job,
    so the factory lives in the agents layer rather than duplicated per
    caller.

    Defaults are deliberate:
    - ``executor_role`` falls back to ``tech-lead`` for implementation
      mode (matches the recommender's default reviewer-as-executor stub)
      and to empty string for research-only (no executor by contract).
    - ``approval_required`` defaults True because dropping an unset value
      to False would unsafely loosen the user-approval gate.
    """

    lifecycle_mode = str(payload.get("lifecycle_mode") or LIFECYCLE_MODE_IMPLEMENTATION)
    raw_executor = payload.get("executor_role")
    if lifecycle_mode == LIFECYCLE_MODE_RESEARCH_ONLY:
        executor_role = str(raw_executor or "")
    else:
        executor_role = str(raw_executor or "tech-lead")
    return CodingAuthorizationProposal(
        session_id=payload.get("session_id"),
        user_request=str(payload.get("user_request") or ""),
        executor_role=executor_role,
        review_roles=tuple(payload.get("review_roles") or ()),
        participant_roles=tuple(payload.get("participant_roles") or ()),
        write_scope=tuple(payload.get("write_scope") or ()),
        forbidden_scope=tuple(payload.get("forbidden_scope") or ()),
        reason=str(payload.get("reason") or ""),
        safety_rules=tuple(payload.get("safety_rules") or ()),
        approval_required=bool(payload.get("approval_required", True)),
        metadata=dict(payload.get("metadata") or {}),
        lifecycle_mode=lifecycle_mode,
        research_leads=tuple(payload.get("research_leads") or ()),
    )


__all__ = (
    "CodingAuthorizationProposal",
    "LIFECYCLE_MODE_IMPLEMENTATION",
    "LIFECYCLE_MODE_RESEARCH_ONLY",
    "format_authorization_message",
    "load_role_profile",
    "proposal_from_dict",
    "recommend_authorization",
    "reset_role_profile_cache",
)
