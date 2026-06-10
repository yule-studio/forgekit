"""F13 부서별 자동 이슈 수집 — RSS 크롤러 + dept feed + meeting trigger.

사용자 design (2026-05-12):
- 부서 채널 = 이슈 큐 (read-only feed, GeekNews 카드).
- `#운영-리서치` = 다중 부서 영향 시 meeting thread.
- `#업무-접수` = 실행 요청 입구.
- 분류 4종: design / planning / engineering / multi-dept.
"""

from .dedup_ledger import DigestDedupLedger
from .dept_router import DeptClassification, DEPARTMENTS, classify_evidence
from .formatter import DigestCard, format_card
from .source_catalog import (
    AuthoritativeSource,
    ROLE_SOURCE_CATALOG,
    sources_for_role,
)


__all__ = (
    "AuthoritativeSource",
    "DEPARTMENTS",
    "DigestCard",
    "DigestDedupLedger",
    "DeptClassification",
    "ROLE_SOURCE_CATALOG",
    "classify_evidence",
    "format_card",
    "sources_for_role",
)
