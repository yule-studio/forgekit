"""F13 — 수집된 evidence → 부서 분류 (design / planning / engineering / multi-dept).

사용자 정책 (2026-05-12):
- 단일 부서 영향 → 그 부서 채널 게시
- 다중 부서 영향 → ``#운영-리서치`` thread + 부서 채널 양쪽

회의 트리거 규칙:
- ``single`` → dept channel only
- ``multi`` → multi-dept thread + 양쪽 채널
- ``execution_required`` → ``#업무-접수``
- ``approval_required`` → ``#승인-대기``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

from .source_catalog import host_to_roles


DEPARTMENTS: Tuple[str, ...] = ("planning", "design", "engineering")


# 역할 → 부서 매핑 (사용자 design 기반)
ROLE_TO_DEPT: Mapping[str, str] = {
    "tech-lead": "engineering",      # 다부서 영향 가능 — multi 판정 후보
    "backend-engineer": "engineering",
    "frontend-engineer": "engineering",
    "qa-engineer": "engineering",
    "devops-engineer": "engineering",
    "ai-engineer": "engineering",
    "product-designer": "design",
    "planning": "planning",  # 향후 planner role
}


# 다부서 영향 키워드 (제목/요약에 포함되면 multi-dept 후보 점수 +1)
MULTI_DEPT_KEYWORDS: Tuple[str, ...] = (
    "architecture", "accessibility", "wcag",
    "design-system", "design system", "tokens",
    "security advisory", "cve",
    "breaking change", "migration",
    "release-notes", "release notes",
    "platform", "infrastructure",
)


@dataclass(frozen=True)
class DeptClassification:
    """한 evidence 의 부서 라우팅 verdict.

    ``primary``: 가장 강한 부서 (단일 부서면 여기만 게시)
    ``affected``: 영향받는 모든 부서 (multi-dept 시 thread 트리거)
    ``meeting_trigger``: True → ``#운영-리서치`` thread 생성
    """

    primary: str
    affected: Tuple[str, ...]
    meeting_trigger: bool
    rationale: str


def _detect_multi_dept_signal(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    return any(kw in text for kw in MULTI_DEPT_KEYWORDS)


def classify_evidence(
    *,
    host: str,
    title: str = "",
    summary: str = "",
    primary_role: Optional[str] = None,
) -> DeptClassification:
    """주어진 evidence 를 부서별로 라우팅.

    Strategy:
      1. ``host`` → role 매칭 (카탈로그 기준)
      2. 매칭된 role 들 → 부서 집합
      3. 단일 부서 + multi-dept signal 없음 → single 라우팅
      4. 다중 부서 OR multi-dept keyword OR primary_role 이 tech-lead → multi 라우팅
    """

    roles = host_to_roles(host)
    if primary_role and primary_role not in roles:
        roles = (primary_role, *roles)

    departments = []
    seen: set[str] = set()
    for role in roles:
        dept = ROLE_TO_DEPT.get(role)
        if dept and dept not in seen:
            departments.append(dept)
            seen.add(dept)

    if not departments:
        # 카탈로그 외 / 알 수 없는 host — engineering 으로 기본 라우팅
        return DeptClassification(
            primary="engineering",
            affected=("engineering",),
            meeting_trigger=False,
            rationale=f"unknown host '{host}' — default engineering",
        )

    multi_signal = _detect_multi_dept_signal(title, summary)
    is_tech_lead_source = "tech-lead" in roles

    meeting_trigger = (
        len(departments) >= 2
        or multi_signal
        or is_tech_lead_source
    )

    if meeting_trigger:
        primary = departments[0]
        rationale_parts = [f"primary={primary}"]
        if len(departments) >= 2:
            rationale_parts.append(f"depts={departments}")
        if multi_signal:
            rationale_parts.append("multi-dept keyword")
        if is_tech_lead_source:
            rationale_parts.append("tech-lead source")
        return DeptClassification(
            primary=primary,
            affected=tuple(departments),
            meeting_trigger=True,
            rationale=" / ".join(rationale_parts),
        )

    return DeptClassification(
        primary=departments[0],
        affected=(departments[0],),
        meeting_trigger=False,
        rationale=f"single dept {departments[0]} (host={host})",
    )


__all__ = (
    "DEPARTMENTS",
    "DeptClassification",
    "MULTI_DEPT_KEYWORDS",
    "ROLE_TO_DEPT",
    "classify_evidence",
)
