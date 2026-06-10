"""F13 — 역할별 권위 source 카탈로그 (사용자 명시, 2026-05-12).

부서 자동 디지스트 가 fetch 하는 source 의 단일 진실. 카탈로그 외 host 는
`crawler` / `dept_router` / `formatter` 모두 거부 — governance test 로 가드.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence, Tuple


@dataclass(frozen=True)
class AuthoritativeSource:
    """카탈로그의 한 row.

    ``host``: bare domain (e.g. ``mdn.dev`` — robots/rate-limit 비교 키)
    ``feed_url``: RSS/Atom 또는 GitHub release feed URL
    ``kind``: ``rss`` / ``atom`` / ``github_release`` / ``html_list``
    ``dept_hint``: digest 분류기가 1차로 사용하는 부서 hint (design / planning / engineering / multi)
    ``trust``: 0..1 — 카탈로그 내 상대 신뢰도
    """

    host: str
    feed_url: str
    kind: str
    dept_hint: str
    trust: float = 0.9


# ---------------------------------------------------------------------------
# 사용자 정책 매트릭스 (2026-05-12)
# ---------------------------------------------------------------------------


ROLE_SOURCE_CATALOG: Mapping[str, Tuple[AuthoritativeSource, ...]] = {
    "tech-lead": (
        AuthoritativeSource("infoq.com", "https://feed.infoq.com/architecture-design/", "rss", "engineering", 0.92),
        AuthoritativeSource("github.blog", "https://github.blog/engineering.atom", "atom", "engineering", 0.95),
        AuthoritativeSource("martinfowler.com", "https://martinfowler.com/feed.atom", "atom", "engineering", 0.95),
        AuthoritativeSource("cncf.io", "https://www.cncf.io/feed/", "rss", "engineering", 0.85),
    ),
    "product-designer": (
        AuthoritativeSource("developer.apple.com", "https://developer.apple.com/news/rss/news.rss", "rss", "design", 0.95),
        AuthoritativeSource("material.io", "https://material.io/blog/feed.xml", "atom", "design", 0.9),
        AuthoritativeSource("w3.org", "https://www.w3.org/blog/news/feed/atom", "atom", "design", 0.9),
    ),
    "frontend-engineer": (
        AuthoritativeSource("developer.mozilla.org", "https://developer.mozilla.org/en-US/blog/rss.xml", "atom", "engineering", 0.95),
        AuthoritativeSource("web.dev", "https://web.dev/feed.xml", "atom", "engineering", 0.95),
        AuthoritativeSource("react.dev", "facebook/react", "github_release", "engineering", 0.92),
        AuthoritativeSource("typescriptlang.org", "microsoft/TypeScript", "github_release", "engineering", 0.92),
        AuthoritativeSource("vuejs.org", "vuejs/core", "github_release", "engineering", 0.9),
    ),
    "backend-engineer": (
        AuthoritativeSource("postgresql.org", "https://www.postgresql.org/news/rss.xml", "rss", "engineering", 0.95),
        AuthoritativeSource("owasp.org", "https://owasp.org/news/feed.xml", "rss", "engineering", 0.95),
        AuthoritativeSource("fastapi.tiangolo.com", "tiangolo/fastapi", "github_release", "engineering", 0.9),
        AuthoritativeSource("spring.io", "spring-projects/spring-security", "github_release", "engineering", 0.92),
        AuthoritativeSource("docs.sqlalchemy.org", "sqlalchemy/sqlalchemy", "github_release", "engineering", 0.88),
    ),
    "qa-engineer": (
        AuthoritativeSource("playwright.dev", "microsoft/playwright", "github_release", "engineering", 0.95),
        AuthoritativeSource("testing-library.com", "testing-library/dom-testing-library", "github_release", "engineering", 0.9),
        AuthoritativeSource("docs.cypress.io", "cypress-io/cypress", "github_release", "engineering", 0.9),
        AuthoritativeSource("vitest.dev", "vitest-dev/vitest", "github_release", "engineering", 0.88),
    ),
    "ai-engineer": (
        AuthoritativeSource("openai.com", "openai/openai-python", "github_release", "engineering", 0.92),
        AuthoritativeSource("huggingface.co", "huggingface/transformers", "github_release", "engineering", 0.92),
        AuthoritativeSource("anthropic.com", "anthropics/anthropic-sdk-python", "github_release", "engineering", 0.92),
    ),
    "devops-engineer": (
        AuthoritativeSource("docs.docker.com", "docker/docker-ce", "github_release", "engineering", 0.92),
        AuthoritativeSource("kubernetes.io", "kubernetes/kubernetes", "github_release", "engineering", 0.95),
        AuthoritativeSource("docs.github.com", "https://github.blog/changelog/feed/", "rss", "engineering", 0.95),
        AuthoritativeSource("terraform.io", "hashicorp/terraform", "github_release", "engineering", 0.9),
        AuthoritativeSource("prometheus.io", "prometheus/prometheus", "github_release", "engineering", 0.88),
    ),
}


def sources_for_role(role: str) -> Tuple[AuthoritativeSource, ...]:
    """Return the authoritative source catalog for ``role``.

    Unknown role → empty tuple (caller decides: skip vs raise).
    """

    return ROLE_SOURCE_CATALOG.get(role, ())


def all_allowed_hosts() -> frozenset[str]:
    """카탈로그 전 host 집합 — crawler.allow-list 가드."""

    hosts: set[str] = set()
    for sources in ROLE_SOURCE_CATALOG.values():
        for src in sources:
            hosts.add(src.host)
    return frozenset(hosts)


def host_to_roles(host: str) -> Tuple[str, ...]:
    """주어진 host 가 어떤 역할의 카탈로그에 속하는지 — 멀티-role 매칭 OK."""

    matched: list = []
    for role, sources in ROLE_SOURCE_CATALOG.items():
        if any(src.host == host for src in sources):
            matched.append(role)
    return tuple(matched)


__all__ = (
    "AuthoritativeSource",
    "ROLE_SOURCE_CATALOG",
    "all_allowed_hosts",
    "host_to_roles",
    "sources_for_role",
)
