"""Deterministic mock research collector (extracted from collector.py).

The mock collector returns canned, role-aware hits so tests run without a
network and operators can preview the collector contract before paying for
a search API. Split out of ``collector.py`` so that file keeps a thin core
surface; the canned bucket data + ``MockSearchCollector`` live here.

Import direction is one-way: this module imports the collector *core*
(base interface / query type + ``compute_confidence`` / ``short_role`` /
``parse_github_url``) and the pack types. The core re-exports
``MockSearchCollector`` (and ``_MockHit`` / ``_MOCK_BUCKETS``) for its
public surface and the factory wiring — collector core → mock is the
legal direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence, Tuple

from .pack import ResearchAttachment, ResearchSource, SourceType
from .collector import (
    CollectorQuery,
    ResearchCollector,
    compute_confidence,
    parse_github_url,
    short_role,
)


@dataclass(frozen=True)
class _MockHit:
    title: str
    url: str
    domain: str
    snippet: str
    source_type: SourceType
    why_relevant: str
    risk_or_limit: Optional[str] = None
    thumbnail_url: Optional[str] = None


# Canned per-role hit sets. The mock cycles through these (modulated by the
# query) so different prompts get a different first hit, but the same prompt
# always returns the same ordering — handy for tests and debugging.
_MOCK_BUCKETS: Mapping[str, Tuple[_MockHit, ...]] = {
    "tech-lead": (
        _MockHit(
            title="ADR template — architecture decision record",
            url="https://github.com/joelparkerhenderson/architecture-decision-record",
            domain="github.com",
            snippet="Record context, decision, consequence — base ADR template.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="작업 분해와 결정 기록 양식을 그대로 차용 가능",
        ),
        _MockHit(
            title="A Philosophy of Software Design — talk notes",
            url="https://blog.acolyer.org/2018/09/04/a-philosophy-of-software-design/",
            domain="blog.acolyer.org",
            snippet="Module 분해와 의존 순서 결정에 대한 정리 노트.",
            source_type=SourceType.COMMUNITY_SIGNAL,
            why_relevant="작업 순서 결정 시 가독성/모듈성 trade-off 참고",
            risk_or_limit="블로그 요약본 — 원문 검증 필요",
        ),
        _MockHit(
            title="GitHub Issue: 기존 hero 회귀 추적",
            url="https://github.com/example/example/issues/42",
            domain="github.com",
            snippet="Issue body — hero 카피 변경 후 모바일 그리드 깨짐 보고.",
            source_type=SourceType.GITHUB_ISSUE,
            why_relevant="과거 회귀 패턴 — 같은 영역 변경 시 재현 위험",
        ),
    ),
    "product-designer": (
        _MockHit(
            title="Mobbin — landing hero patterns",
            url="https://mobbin.com/discover/landing-page",
            domain="mobbin.com",
            snippet="실제 출시된 모바일 앱의 랜딩 hero 섹션 캡처 모음.",
            source_type=SourceType.DESIGN_REFERENCE,
            why_relevant="hero 카피·CTA 배치 패턴 차용 후보 — Mobbin 스크린숏 가이드",
            risk_or_limit="Mobbin 약관: 직접 scraping 금지, OG/검색 결과 metadata만 사용",
            thumbnail_url="https://mobbin.com/static/preview/landing.png",
        ),
        _MockHit(
            title="Behance — 브랜딩 hero 컬렉션",
            url="https://www.behance.net/search/projects/landing%20hero",
            domain="behance.net",
            snippet="Behance에서 큐레이션된 hero 시안 큐레이션.",
            source_type=SourceType.DESIGN_REFERENCE,
            why_relevant="다양한 브랜드 톤 비교 — 단순 복제 금지, 차용 패턴만 정리",
            thumbnail_url="https://www.behance.net/preview/hero.jpg",
        ),
        _MockHit(
            title="Awwwards — Site of the Day (landing 카테고리)",
            url="https://www.awwwards.com/websites/landing-page/",
            domain="awwwards.com",
            snippet="Awwwards 큐레이션 — 인터랙션·애니메이션 레퍼런스.",
            source_type=SourceType.DESIGN_REFERENCE,
            why_relevant="모바일/데스크톱 전환 인터랙션 검토 후보",
            thumbnail_url="https://www.awwwards.com/preview/landing.jpg",
        ),
        _MockHit(
            title="Notefolio — 한국 디자이너 hero 시안",
            url="https://notefolio.net/categories/branding",
            domain="notefolio.net",
            snippet="Notefolio — 지역 감성 톤 참고용 포트폴리오 큐레이션.",
            source_type=SourceType.DESIGN_REFERENCE,
            why_relevant="한국 사용자 톤 검토에 적합 — 직접 scraping 대신 사용자 제공 링크 권장",
            risk_or_limit="Notefolio 약관: 자동 수집 민감 — 메타데이터만 보존",
        ),
    ),
    "backend-engineer": (
        _MockHit(
            title="FastAPI — Security 가이드",
            url="https://fastapi.tiangolo.com/tutorial/security/",
            domain="fastapi.tiangolo.com",
            snippet="OAuth2 / API key 인증 권장 패턴 공식 문서.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="인증/권한 변경 시 공식 권장 패턴 따라 위험 최소화",
        ),
        _MockHit(
            title="PostgreSQL — Concurrency Control",
            url="https://www.postgresql.org/docs/current/mvcc.html",
            domain="postgresql.org",
            snippet="PostgreSQL MVCC 락 정책 — 마이그레이션 잠금 위험 점검용.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="schema 변경 시 동시 작업 충돌 점검 근거",
        ),
        _MockHit(
            title="OWASP — Authentication Cheat Sheet",
            url="https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html",
            domain="cheatsheetseries.owasp.org",
            snippet="OWASP 인증 보안 권장 항목.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="email 인증 / 토큰 저장 정책의 보안 기준",
        ),
    ),
    "frontend-engineer": (
        _MockHit(
            title="MDN — Accessibility · ARIA roles",
            url="https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA",
            domain="developer.mozilla.org",
            snippet="ARIA role / state / property 표준 정의.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="hero CTA 접근성 점검 — role/aria-label 적용 기준",
        ),
        _MockHit(
            title="web.dev — Performance & Accessibility",
            url="https://web.dev/learn/accessibility/",
            domain="web.dev",
            snippet="web.dev 학습 트랙 — 접근성 / 성능 best practice.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="모바일 hero 렌더링 성능 점검 가이드",
        ),
        _MockHit(
            title="React — Components & Composition",
            url="https://react.dev/learn",
            domain="react.dev",
            snippet="React 공식 문서 — 컴포넌트 분해 권장 패턴.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="hero 컴포넌트 props/상태 분리 기준",
        ),
    ),
    "qa-engineer": (
        _MockHit(
            title="Playwright — Best Practices",
            url="https://playwright.dev/docs/best-practices",
            domain="playwright.dev",
            snippet="Playwright e2e 작성 권장 패턴 (locator/wait/visual).",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="hero 회귀 e2e 시나리오 작성 기준",
        ),
        _MockHit(
            title="Testing Library — Guiding Principles",
            url="https://testing-library.com/docs/guiding-principles",
            domain="testing-library.com",
            snippet="사용자 관점 테스트 작성 원칙.",
            source_type=SourceType.OFFICIAL_DOCS,
            why_relevant="hero CTA 접근성 단위 테스트 작성 근거",
        ),
        _MockHit(
            title="GitHub Issue: 기존 hero 회귀 누적",
            url="https://github.com/example/example/issues/42",
            domain="github.com",
            snippet="과거 hero 회귀 사례 누적 — 회귀 시나리오 입력으로 활용.",
            source_type=SourceType.GITHUB_ISSUE,
            why_relevant="회귀 케이스 우선순위 결정",
        ),
    ),
}


class MockSearchCollector(ResearchCollector):
    """Deterministic role-aware canned collector.

    Returns ``min(max_results, len(_MOCK_BUCKETS[role]))`` hits drawn from
    the role's bucket. The first hit is rotated based on a stable hash of
    the query so the same prompt always sees the same first hit, but
    different prompts see different first hits — useful for showing
    operators that the collector is "alive" without ever leaving the
    process.
    """

    name = "mock"

    def search(self, query: CollectorQuery) -> Sequence[ResearchSource]:
        bucket = _MOCK_BUCKETS.get(short_role(query.role), ())
        if not bucket:
            return ()
        offset = (abs(hash(query.query)) if query.query else 0) % len(bucket)
        ordered = bucket[offset:] + bucket[:offset]
        capped = ordered[: max(1, query.max_results)]
        collected_at = datetime.utcnow()
        return tuple(
            self._hit_to_source(hit, query=query, collected_at=collected_at)
            for hit in capped
        )

    @staticmethod
    def _hit_to_source(
        hit: _MockHit,
        *,
        query: CollectorQuery,
        collected_at: datetime,
    ) -> ResearchSource:
        attachments: Tuple[ResearchAttachment, ...] = ()
        if hit.thumbnail_url:
            attachments = (
                ResearchAttachment(
                    kind="image",
                    url=hit.thumbnail_url,
                    description="thumbnail (metadata only — 이미지 원본 저장 안 함)",
                ),
            )
        gh_meta = parse_github_url(hit.url)
        extra: dict[str, Any] = {
            "domain": hit.domain,
            "snippet": hit.snippet,
            "thumbnail_url": hit.thumbnail_url,
            "query": query.query,
            "provider": "mock",
        }
        if gh_meta is not None:
            extra["github"] = dict(gh_meta)
        # Mock hits have curated metadata so we score with high signal.
        confidence = compute_confidence(
            source_type=hit.source_type,
            role=query.role,
            has_url=bool(hit.url),
            has_snippet=bool(hit.snippet),
            has_thumbnail=bool(hit.thumbnail_url),
        )
        return ResearchSource(
            source_type=hit.source_type,
            source_url=hit.url,
            title=hit.title,
            summary=hit.snippet,
            collected_by_role=query.role,
            why_relevant=hit.why_relevant,
            risk_or_limit=hit.risk_or_limit,
            collected_at=collected_at,
            confidence=confidence,
            attachments=attachments,
            extra=extra,
        )
