"""Live research providers — F5 / #92.

본 모듈은 외부 read-only source 를 정규화된 :class:`LiveEvidence` 로
ingest 하기 위한 **공용 dataclass** 와 ``LiveProvider`` 프로토콜을
정의한다. 실제 fetch 로직(:mod:`rss_atom`, :mod:`github_release`) 은
서브모듈이 담당한다.

설계 원칙:
- I/O 호출은 **항상** ``YULE_RESEARCH_LIVE_ENABLED=true`` + allow-list
  ``host`` + ``robots_compliant=True`` 3 가지가 동시에 충족돼야 한다.
- ``YULE_RESEARCH_LIVE_ENABLED`` default 는 ``false`` — 운영자가 명시적
  으로 켜기 전까지 모든 provider 는 mock fallback (빈 결과 또는 fake
  evidence) 으로 동작한다.
- 본 패키지의 어떤 함수도 PasteGuard 를 통과하지 않은 raw HTML 본문을
  외부 채널로 내보내지 않는다. ingest 결과는 항상 정규화된 메타데이터
  + redacted summary 만 노출.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional, Protocol, Sequence, Tuple


# ---------------------------------------------------------------------------
# Source kind 상수
# ---------------------------------------------------------------------------

KIND_RSS: str = "rss"
KIND_ATOM: str = "atom"
KIND_GITHUB_RELEASE: str = "github_release"
KIND_SITEMAP: str = "sitemap"

ALL_KINDS: Tuple[str, ...] = (
    KIND_RSS,
    KIND_ATOM,
    KIND_GITHUB_RELEASE,
    KIND_SITEMAP,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveSource:
    """외부 source 메타데이터.

    * ``host`` — 정규화된 hostname (소문자, no scheme/port). allow-list
      매칭 키. 예: ``"docs.github.com"``.
    * ``kind`` — :data:`ALL_KINDS` 중 하나.
    * ``allow_listed`` — 운영자가 명시 등록한 host 인지. False 면 fetch
      금지 + trust 페널티 (-3).
    * ``robots_compliant`` — robots.txt Disallow 와 충돌하지 않는지.
      False 면 fetch 금지 + trust 페널티 (-5).
    * ``rate_limit_per_sec`` — provider 가 자체 제한할 초당 호출 한도.
      governance 가드의 하한선이며 1 이하 값을 권장 (Hard rail).
    """

    host: str
    kind: str
    allow_listed: bool = True
    robots_compliant: bool = True
    rate_limit_per_sec: float = 1.0
    url: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ALL_KINDS:
            raise ValueError(
                f"LiveSource.kind invalid: {self.kind!r} not in {ALL_KINDS}"
            )
        if self.rate_limit_per_sec <= 0:
            raise ValueError(
                "LiveSource.rate_limit_per_sec must be > 0 "
                f"(got {self.rate_limit_per_sec!r})"
            )


@dataclass(frozen=True)
class LiveEvidence:
    """정규화된 외부 자료 단위 (한 entry / release).

    * ``source`` — 출처 :class:`LiveSource`.
    * ``title`` — entry 제목 (이미 PasteGuard 통과 가정).
    * ``url`` — 자료 URL.
    * ``summary`` — 짧은 요약. raw HTML 금지 — plain text + redacted.
    * ``published_at`` — entry 발행 시각 (없으면 None).
    * ``tags`` — entry 태그/카테고리.
    * ``extra`` — provider 별 부가 메타 (release version, feed lang …).
    """

    source: LiveSource
    title: str
    url: str
    summary: str = ""
    published_at: Optional[datetime] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)
    extra: Mapping[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


class LiveProvider(Protocol):
    """Live ingest provider 공용 인터페이스.

    구현체는 :meth:`ingest` 가 호출 시점의 source 목록에서 evidence 를
    수집해 반환해야 한다. env OFF / allow-list 위반 / robots 위반 시에는
    빈 튜플을 반환 (예외 X) — 호출자는 mock fallback 으로 자연스럽게
    이행된다.
    """

    name: str

    def ingest(self) -> Tuple[LiveEvidence, ...]:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Re-export
# ---------------------------------------------------------------------------

from .registry import (  # noqa: E402 - 순환 회피용 후행 import
    build_live_provider_registry_from_env,
    LiveProviderRegistry,
    default_role_source_catalog,
)
from .rss_atom import RssAtomProvider  # noqa: E402
from .github_release import GithubReleaseProvider  # noqa: E402


__all__ = (
    "ALL_KINDS",
    "KIND_ATOM",
    "KIND_GITHUB_RELEASE",
    "KIND_RSS",
    "KIND_SITEMAP",
    "GithubReleaseProvider",
    "LiveEvidence",
    "LiveProvider",
    "LiveProviderRegistry",
    "LiveSource",
    "RssAtomProvider",
    "build_live_provider_registry_from_env",
    "default_role_source_catalog",
)
