"""Live provider registry + env factory (F5 / #92).

본 모듈은 5 역할 (backend / frontend / qa / devops / tech-lead) 의 기본
:class:`LiveSource` 카탈로그를 정의하고, ``build_live_provider_registry_from_env``
팩토리로 env 기반 활성/비활성 + allow-list 확장을 처리한다.

env 키:
  * ``YULE_RESEARCH_LIVE_ENABLED`` — default ``false``. ``true`` 일 때만
    provider 가 실제 fetch 호출.
  * ``YULE_RESEARCH_LIVE_ALLOW_HOSTS`` — comma-separated host list.
    카탈로그 기본값 외 추가 host 를 allow-list 에 편입. 비어 있으면 카탈
    로그 default 만 활성.
  * ``YULE_RESEARCH_LIVE_RATE_LIMIT_PER_SEC`` — provider rate-limit 한도.
    default 1.0. 0 이하 값은 무시 (default 적용).
  * ``YULE_RESEARCH_LIVE_DISABLE_HOSTS`` — comma-separated host list.
    카탈로그 기본 host 중 사용 안 할 것 명시. 운영자가 특정 source 만 끌
    수 있게 함.
  * ``YULE_RESEARCH_LIVE_KINDS`` — comma-separated kind list. 활성 kind
    제한. 비어있으면 모두 활성 (``rss,atom,github_release``).

env OFF 또는 카탈로그가 비면 :func:`LiveProviderRegistry.ingest_all` 은
빈 튜플을 반환 (mock fallback 으로 호출자 자연스럽게 이행).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence, Tuple

from . import (
    KIND_ATOM,
    KIND_GITHUB_RELEASE,
    KIND_RSS,
    LiveEvidence,
    LiveProvider,
    LiveSource,
)
from .github_release import GithubReleaseProvider, ReleaseFetcher
from .rss_atom import HttpFetcher, RssAtomProvider


# ---------------------------------------------------------------------------
# Default role-source catalog
# ---------------------------------------------------------------------------

# (role, host, kind, url) 튜플 시퀀스. url 은 RSS/Atom 의 경우 feed url,
# github_release 의 경우 ``"owner/repo"`` slug.
_DEFAULT_CATALOG: Tuple[Tuple[str, str, str, str], ...] = (
    # backend
    ("backend-engineer", "fastapi.tiangolo.com", KIND_GITHUB_RELEASE, "tiangolo/fastapi"),
    ("backend-engineer", "docs.sqlalchemy.org", KIND_GITHUB_RELEASE, "sqlalchemy/sqlalchemy"),
    ("backend-engineer", "python.org", KIND_ATOM, "https://www.python.org/dev/peps/peps.rss/"),
    ("backend-engineer", "owasp.org", KIND_RSS, "https://owasp.org/news/feed.xml"),
    # frontend
    ("frontend-engineer", "developer.mozilla.org", KIND_ATOM, "https://developer.mozilla.org/en-US/blog/rss.xml"),
    ("frontend-engineer", "react.dev", KIND_GITHUB_RELEASE, "facebook/react"),
    ("frontend-engineer", "vuejs.org", KIND_GITHUB_RELEASE, "vuejs/core"),
    ("frontend-engineer", "typescriptlang.org", KIND_GITHUB_RELEASE, "microsoft/TypeScript"),
    # qa
    ("qa-engineer", "playwright.dev", KIND_GITHUB_RELEASE, "microsoft/playwright"),
    ("qa-engineer", "docs.cypress.io", KIND_GITHUB_RELEASE, "cypress-io/cypress"),
    ("qa-engineer", "vitest.dev", KIND_GITHUB_RELEASE, "vitest-dev/vitest"),
    # devops
    ("devops-engineer", "docs.github.com", KIND_ATOM, "https://github.blog/feed/"),
    ("devops-engineer", "kubernetes.io", KIND_GITHUB_RELEASE, "kubernetes/kubernetes"),
    ("devops-engineer", "prometheus.io", KIND_GITHUB_RELEASE, "prometheus/prometheus"),
    # tech-lead
    ("tech-lead", "github.blog", KIND_RSS, "https://github.blog/feed/"),
    ("tech-lead", "cncf.io", KIND_RSS, "https://www.cncf.io/feed/"),
)


@dataclass(frozen=True)
class CatalogEntry:
    role: str
    source: LiveSource


def default_role_source_catalog(
    *,
    extra_allow_hosts: Sequence[str] = (),
    disable_hosts: Sequence[str] = (),
    allowed_kinds: Sequence[str] = (),
    rate_limit_per_sec: float = 1.0,
) -> Tuple[CatalogEntry, ...]:
    """기본 카탈로그 → :class:`CatalogEntry` 튜플.

    * ``extra_allow_hosts`` — 카탈로그 외 host 도 allow-list 에 편입 (현재
      구현은 단순 통과만; 운영자가 같은 호스트로 카탈로그를 직접 등록할
      때 ``allow_listed=True`` 로 표시할 때만 의미가 있다).
    * ``disable_hosts`` — 매칭 host 는 catalog 에서 제거.
    * ``allowed_kinds`` — 빈 시퀀스면 전부, 아니면 매칭 kind 만 남김.
    * ``rate_limit_per_sec`` — 카탈로그 entry 전체에 적용할 rate-limit.
    """

    disable_set = {h.lower().strip() for h in disable_hosts if h}
    allow_extra = {h.lower().strip() for h in extra_allow_hosts if h}
    kind_set = {k.lower().strip() for k in allowed_kinds if k}

    out: list[CatalogEntry] = []
    for role, host, kind, url in _DEFAULT_CATALOG:
        h = host.lower()
        if h in disable_set:
            continue
        if kind_set and kind not in kind_set:
            continue
        out.append(
            CatalogEntry(
                role=role,
                source=LiveSource(
                    host=h,
                    kind=kind,
                    allow_listed=True,
                    robots_compliant=True,
                    rate_limit_per_sec=rate_limit_per_sec,
                    url=url,
                ),
            )
        )
    # extra_allow_hosts 는 현재 catalog 외 host 메타 등록만 노출.
    # 실제 fetch 는 caller 가 별도 LiveSource 를 만들어 추가해야 한다.
    for h in sorted(allow_extra):
        if any(e.source.host == h for e in out):
            continue
        # 카탈로그에 등록 안 된 extra host 는 default kind=rss + allow_listed
        # 로 등록만 해 두고, url 은 비워둔다. caller 가 url 을 세팅해야 fetch
        # 가 가능.
        out.append(
            CatalogEntry(
                role="tech-lead",
                source=LiveSource(
                    host=h,
                    kind=KIND_RSS,
                    allow_listed=True,
                    robots_compliant=True,
                    rate_limit_per_sec=rate_limit_per_sec,
                    url="",
                ),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveProviderRegistry:
    """Live provider 묶음. ``ingest_all`` 은 모든 provider 의 결과를 합친다.

    env OFF / provider 비활성 시 빈 튜플 반환 — caller (research loop /
    workflow) 는 mock fallback 으로 자연스럽게 이행한다.
    """

    providers: Tuple[LiveProvider, ...]
    env_enabled: bool
    rate_limit_per_sec: float

    def ingest_all(self) -> Tuple[LiveEvidence, ...]:
        if not self.env_enabled:
            return ()
        out: list[LiveEvidence] = []
        for p in self.providers:
            try:
                out.extend(p.ingest())
            except Exception:  # noqa: BLE001 - provider 격리
                continue
        return tuple(out)


# ---------------------------------------------------------------------------
# Env factory
# ---------------------------------------------------------------------------


def build_live_provider_registry_from_env(
    env: Mapping[str, str],
    *,
    http_fetch: Optional[HttpFetcher] = None,
    release_fetch: Optional[ReleaseFetcher] = None,
) -> LiveProviderRegistry:
    """env mapping 으로부터 :class:`LiveProviderRegistry` 를 구성.

    env OFF (default) 시 ``providers`` 는 빈 튜플로 만들어 mock fallback
    경로가 자연스럽게 활성화되도록 한다.

    ``http_fetch`` / ``release_fetch`` 는 외부 transport. 둘 다 None 이면
    env 가 ON 이어도 실제 fetch 는 일어나지 않는다 — caller 가 명시적
    으로 주입했을 때만 외부 호출 가능.
    """

    enabled = _bool_env(env.get("YULE_RESEARCH_LIVE_ENABLED"), default=False)
    rate_limit = _float_env(
        env.get("YULE_RESEARCH_LIVE_RATE_LIMIT_PER_SEC"),
        default=1.0,
    )
    if rate_limit <= 0:
        rate_limit = 1.0

    extra = _csv(env.get("YULE_RESEARCH_LIVE_ALLOW_HOSTS"))
    disable = _csv(env.get("YULE_RESEARCH_LIVE_DISABLE_HOSTS"))
    kinds = _csv(env.get("YULE_RESEARCH_LIVE_KINDS"))

    catalog = default_role_source_catalog(
        extra_allow_hosts=extra,
        disable_hosts=disable,
        allowed_kinds=kinds,
        rate_limit_per_sec=rate_limit,
    )

    rss_sources = tuple(
        e.source for e in catalog if e.source.kind in (KIND_RSS, KIND_ATOM)
    )
    release_sources = tuple(
        e.source for e in catalog if e.source.kind == KIND_GITHUB_RELEASE
    )

    providers: list[LiveProvider] = []
    if rss_sources:
        providers.append(
            RssAtomProvider(
                sources=rss_sources,
                http_fetch=http_fetch,
                env_enabled=enabled,
            )
        )
    if release_sources:
        providers.append(
            GithubReleaseProvider(
                sources=release_sources,
                release_fetch=release_fetch,
                env_enabled=enabled,
            )
        )

    return LiveProviderRegistry(
        providers=tuple(providers),
        env_enabled=enabled,
        rate_limit_per_sec=rate_limit,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bool_env(value: Optional[str], *, default: bool) -> bool:
    if value is None:
        return default
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off", ""):
        return False
    return default


def _float_env(value: Optional[str], *, default: float) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        return float(value.strip())
    except ValueError:
        return default


def _csv(value: Optional[str]) -> Tuple[str, ...]:
    if not value:
        return ()
    return tuple(s.strip() for s in value.split(",") if s.strip())


__all__ = (
    "CatalogEntry",
    "LiveProviderRegistry",
    "build_live_provider_registry_from_env",
    "default_role_source_catalog",
)
