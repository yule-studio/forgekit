"""Review feedback intake and re-routing for engineering-agent.

PR 리뷰 코멘트, GitHub Copilot 코멘트, 외부 에이전트 의견을 입력으로 받아
적절한 멤버 역할에 다시 분배하고, 수정 결과를 thread로 회신할 때 쓰는 메시지를
구성하는 모듈. Dispatcher와 별도 경로로 동작하며, 기존 WorkflowSession과
같은 thread_id에 연결돼 새 review_cycle만 증가시킨다.

설계 원칙:
- 입력 source 종류는 source 필드로 평면화 (github_pr_review, github_copilot, external_agent, user)
- 라우팅은 categories(우선) → file_paths(보조) → fallback tech-lead 순서
- 단일 executor 원칙은 그대로 유지: 응답하는 역할은 한 번에 하나
- 레퍼런스 회수가 필요한 카테고리에선 reference_sources를 함께 반환
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Optional, Sequence

# Re-export council types so review-loop callers can branch on the
# execution_review decision without depending on agents.council directly.
# Source of truth: docs/engineering-role-council-runtime.md §6 +
# agents/council.py.
from .council import (  # noqa: F401 — re-export
    ExecutionReviewDecision,
    ExecutionReview,
    CIBucketStatus,
    RoleCouncilRecheck,
    RoleCouncilRecheckStatus,
)


class ReviewSource(str, Enum):
    GITHUB_PR_REVIEW = "github_pr_review"
    GITHUB_COPILOT = "github_copilot"
    EXTERNAL_AGENT = "external_agent"
    USER = "user"


class ReviewSeverity(str, Enum):
    BLOCKING = "blocking"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NIT = "nit"


REVIEW_CATEGORY_DESIGN = ("ui", "ux", "layout", "copy", "branding", "design", "visual")
REVIEW_CATEGORY_QA = ("test", "coverage", "qa", "regression", "edge-case")
REVIEW_CATEGORY_ARCHITECTURE = ("architecture", "system", "scalability", "refactor", "design-system")
REVIEW_CATEGORY_BACKEND = ("backend", "api", "data", "model", "auth", "migration", "server")
REVIEW_CATEGORY_FRONTEND = ("frontend", "component", "page", "interaction", "client", "react")

REFERENCE_GAP_KEYWORDS_FLOW = ("flow", "흐름", "단계", "navigation")
REFERENCE_GAP_KEYWORDS_COPY = ("copy", "카피", "문구", "메시지", "tone", "헤드라인", "후크")
REFERENCE_GAP_KEYWORDS_VISUAL = ("visual", "비주얼", "디자인", "color", "typography", "스타일")
REFERENCE_GAP_KEYWORDS_PERSUASION = ("conversion", "설득력", "cta", "광고", "ad", "캠페인")

REFERENCE_SOURCES_FLOW = ("Mobbin", "Page Flows")
REFERENCE_SOURCES_VISUAL = ("Awwwards", "Behance", "Notefolio", "Pinterest Trends")
REFERENCE_SOURCES_COPY = ("Really Good Emails", "Page Flows")
REFERENCE_SOURCES_PERSUASION = ("Meta Ad Library", "TikTok Creative Center", "Google Trends")


@dataclass(frozen=True)
class ReviewFeedback:
    """Normalized feedback record across PR review / Copilot / external sources."""

    feedback_id: str
    source: ReviewSource
    submitted_at: datetime
    summary: str
    body: str = ""
    target_session_id: Optional[str] = None
    target_pr_url: Optional[str] = None
    target_issue_url: Optional[str] = None
    target_thread_id: Optional[int] = None
    file_paths: Sequence[str] = ()
    severity: ReviewSeverity = ReviewSeverity.MEDIUM
    categories: Sequence[str] = ()
    references_user: Sequence[str] = ()
    author: Optional[str] = None


@dataclass(frozen=True)
class ReviewRouting:
    """Routing decision for a single ReviewFeedback record."""

    feedback_id: str
    primary_role: str
    supporting_roles: Sequence[str]
    reasons: Sequence[str]
    reference_needed: bool
    reference_sources: Sequence[str]
    reference_gaps: Sequence[str] = ()


def to_payload(feedback: ReviewFeedback) -> Mapping[str, object]:
    """Serialize a ReviewFeedback to a JSON-friendly dict (for workflow state)."""

    return {
        "feedback_id": feedback.feedback_id,
        "source": feedback.source.value,
        "submitted_at": feedback.submitted_at.isoformat(),
        "summary": feedback.summary,
        "body": feedback.body,
        "target_session_id": feedback.target_session_id,
        "target_pr_url": feedback.target_pr_url,
        "target_issue_url": feedback.target_issue_url,
        "target_thread_id": feedback.target_thread_id,
        "file_paths": list(feedback.file_paths),
        "severity": feedback.severity.value,
        "categories": list(feedback.categories),
        "references_user": list(feedback.references_user),
        "author": feedback.author,
    }


def from_payload(payload: Mapping[str, object]) -> ReviewFeedback:
    return ReviewFeedback(
        feedback_id=str(payload["feedback_id"]),
        source=ReviewSource(str(payload.get("source") or ReviewSource.USER.value)),
        submitted_at=datetime.fromisoformat(str(payload["submitted_at"])),
        summary=str(payload.get("summary") or ""),
        body=str(payload.get("body") or ""),
        target_session_id=_optional_str(payload.get("target_session_id")),
        target_pr_url=_optional_str(payload.get("target_pr_url")),
        target_issue_url=_optional_str(payload.get("target_issue_url")),
        target_thread_id=_optional_int(payload.get("target_thread_id")),
        file_paths=tuple(str(item) for item in payload.get("file_paths") or ()),
        severity=ReviewSeverity(str(payload.get("severity") or ReviewSeverity.MEDIUM.value)),
        categories=tuple(str(item) for item in payload.get("categories") or ()),
        references_user=tuple(str(item) for item in payload.get("references_user") or ()),
        author=_optional_str(payload.get("author")),
    )


def route_review_feedback(feedback: ReviewFeedback) -> ReviewRouting:
    """Decide which role addresses this feedback, with optional reference fetch."""

    haystack = " ".join([feedback.summary, feedback.body, *feedback.categories]).lower()
    paths_haystack = " ".join(feedback.file_paths).lower()

    primary_role = "tech-lead"
    reasons: list[str] = []
    supporting: list[str] = []

    has_design = _matches(haystack, REVIEW_CATEGORY_DESIGN)
    has_qa = _matches(haystack, REVIEW_CATEGORY_QA)
    has_architecture = _matches(haystack, REVIEW_CATEGORY_ARCHITECTURE)
    has_backend = _matches(haystack, REVIEW_CATEGORY_BACKEND)
    has_frontend = _matches(haystack, REVIEW_CATEGORY_FRONTEND)

    paths_match_frontend = _matches(paths_haystack, REVIEW_CATEGORY_FRONTEND + ("css", "tsx", "jsx", "html"))
    paths_match_backend = _matches(paths_haystack, REVIEW_CATEGORY_BACKEND + ("/api/", "service", "controller"))

    if has_architecture and feedback.severity in (ReviewSeverity.BLOCKING, ReviewSeverity.HIGH):
        primary_role = "tech-lead"
        reasons.append("architecture/system level concern")
        supporting = ["backend-engineer", "frontend-engineer"]
    elif has_design:
        primary_role = "product-designer"
        reasons.append("design/copy/UX feedback")
        supporting = ["frontend-engineer"]
    elif has_qa:
        primary_role = "qa-engineer"
        reasons.append("test/coverage gap")
        supporting = ["backend-engineer", "frontend-engineer"]
    elif has_backend or paths_match_backend:
        primary_role = "backend-engineer"
        reasons.append("backend/data/api signal")
        supporting = ["qa-engineer"]
    elif has_frontend or paths_match_frontend:
        primary_role = "frontend-engineer"
        reasons.append("frontend/component signal")
        supporting = ["product-designer", "qa-engineer"]
    else:
        primary_role = "tech-lead"
        reasons.append("ambiguous feedback — tech-lead triage")

    reference_gaps, reference_sources = _detect_reference_gaps(haystack)
    reference_needed = bool(reference_gaps) or has_design

    if reference_needed and not reference_sources:
        reference_sources = REFERENCE_SOURCES_VISUAL

    if feedback.severity == ReviewSeverity.NIT and primary_role != "tech-lead":
        reasons.append("nit severity — fix optional")

    return ReviewRouting(
        feedback_id=feedback.feedback_id,
        primary_role=primary_role,
        supporting_roles=tuple(supporting),
        reasons=tuple(reasons),
        reference_needed=reference_needed,
        reference_sources=tuple(reference_sources),
        reference_gaps=tuple(reference_gaps),
    )


def format_review_intake_message(
    feedback: ReviewFeedback,
    routing: ReviewRouting,
    *,
    session_id: str,
    review_cycle: int,
) -> str:
    """Discord/PR thread 첫 회신: 누가 받고 무엇을 볼지 안내."""

    lines: list[str] = []
    lines.append(f"**리뷰 피드백 수신 (cycle {review_cycle})**")
    lines.append(f"- 세션: `{session_id}`")
    lines.append(f"- 출처: {feedback.source.value} / 심각도: {feedback.severity.value}")
    if feedback.author:
        lines.append(f"- 작성자: {feedback.author}")
    lines.append("")
    lines.append(f"**요약**\n{feedback.summary}")
    if feedback.body:
        lines.append("")
        lines.append("**본문**")
        lines.append(feedback.body)
    lines.append("")
    lines.append("**재분배**")
    lines.append(f"- 담당 역할: `{routing.primary_role}`")
    if routing.supporting_roles:
        lines.append(f"- 지원 역할: {', '.join(f'`{role}`' for role in routing.supporting_roles)}")
    if routing.reasons:
        lines.append(f"- 라우팅 사유: {'; '.join(routing.reasons)}")
    if feedback.file_paths:
        lines.append(f"- 영향 파일: {', '.join(f'`{path}`' for path in list(feedback.file_paths)[:5])}")
    if routing.reference_needed:
        lines.append("")
        lines.append("**레퍼런스 회수 필요**")
        if routing.reference_gaps:
            lines.append(f"- 부족한 측면: {', '.join(routing.reference_gaps)}")
        if routing.reference_sources:
            lines.append(
                f"- 추천 소스: {', '.join(routing.reference_sources)}"
            )
    return "\n".join(lines)


def format_review_reply_message(
    feedback: ReviewFeedback,
    routing: ReviewRouting,
    *,
    session_id: str,
    review_cycle: int,
    applied: Sequence[str],
    proposed: Sequence[str] = (),
    remaining: Sequence[str] = (),
    references_used: Sequence[Mapping[str, str]] = (),
) -> str:
    """피드백 처리 후 thread에 올릴 회신 — 적용/제안/남은 이슈/레퍼런스."""

    lines: list[str] = []
    lines.append(f"**리뷰 회신 (cycle {review_cycle})**")
    lines.append(f"- 세션: `{session_id}`")
    lines.append(f"- 담당: `{routing.primary_role}`")
    lines.append(f"- 원 피드백: {feedback.summary}")

    lines.append("")
    if applied:
        lines.append("**적용한 수정**")
        for item in applied:
            lines.append(f"- {item}")
    else:
        lines.append("**적용한 수정**: 없음 (제안만 정리)")

    if proposed:
        lines.append("")
        lines.append("**추가 제안**")
        for item in proposed:
            lines.append(f"- {item}")

    if remaining:
        lines.append("")
        lines.append("**남은 이슈**")
        for item in remaining:
            lines.append(f"- {item}")

    if references_used:
        lines.append("")
        lines.append("**참고한 레퍼런스**")
        for ref in references_used:
            label = ref.get("title") or ref.get("source") or ""
            url = ref.get("url") or ""
            if url:
                lines.append(f"- {label} — {url}")
            else:
                lines.append(f"- {label}")
    elif routing.reference_needed and routing.reference_sources:
        lines.append("")
        lines.append(
            f"**참고 권장 소스**: {', '.join(routing.reference_sources)} "
            "(약관상 자동 수집 금지 — 사용자 제공 또는 수동 참고)"
        )

    return "\n".join(lines)


def _matches(haystack: str, keywords: Sequence[str]) -> bool:
    return any(keyword in haystack for keyword in keywords)


def _detect_reference_gaps(haystack: str) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    sources: list[str] = []
    if _matches(haystack, REFERENCE_GAP_KEYWORDS_FLOW):
        gaps.append("UX 플로우")
        sources.extend(REFERENCE_SOURCES_FLOW)
    if _matches(haystack, REFERENCE_GAP_KEYWORDS_COPY):
        gaps.append("카피 훅")
        sources.extend(REFERENCE_SOURCES_COPY)
    if _matches(haystack, REFERENCE_GAP_KEYWORDS_VISUAL):
        gaps.append("비주얼 완성도")
        sources.extend(REFERENCE_SOURCES_VISUAL)
    if _matches(haystack, REFERENCE_GAP_KEYWORDS_PERSUASION):
        gaps.append("설득력")
        sources.extend(REFERENCE_SOURCES_PERSUASION)

    seen: list[str] = []
    for source in sources:
        if source not in seen:
            seen.append(source)
    return gaps, seen


def _optional_str(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
