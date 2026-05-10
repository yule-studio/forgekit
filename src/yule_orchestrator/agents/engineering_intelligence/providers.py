"""Live provider seam for the engineering_intelligence collector.

The existing :class:`SourceCollectorAdapter` Protocol in :mod:`.collector`
takes a :class:`SourceEntry` and returns items. That covers the
"adapter" half — but production needs to know *how* to fetch each
source: an RSS feed gets one URL, a sitemap gets a different parse,
GitHub releases hit the API. This module owns the *transport spec*
half so a future runtime can dispatch the right code path without
re-deriving it from the registry every time.

Design choices:

  * The spec is pure data — no transport code lives here. The actual
    HTTP/API client lives in a follow-up module (``providers_live.py``)
    that downstream G6 wires up.
  * Every spec carries its parser hint (``parser="rss"`` / ``"atom"``
    / ``"sitemap"`` / ``"html_list"`` / ``"github_releases"``) so the
    fetcher knows which decoder to invoke.
  * :func:`provider_spec_for` is the single dispatch point. New
    sources don't need bespoke wiring — they pick a transport at
    registry time via ``collection_mode``.

Strict offline. Absolutely no urllib / requests / socket import here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Mapping, Optional, Protocol, Sequence, Tuple

from .models import (
    CollectionMode,
    EngineeringKnowledgeItem,
    SourceEntry,
    SourceKind,
)


# ---------------------------------------------------------------------------
# Provider transport classification
# ---------------------------------------------------------------------------


class ProviderTransport(str, Enum):
    """How a source's bytes arrive at the runtime.

    Mapped 1:1 from :class:`CollectionMode` so the registry stays the
    single source of truth — the transport is derivable from the mode
    plus a couple of URL heuristics (atom vs rss; github releases
    sitting under ``releases.atom``).
    """

    RSS = "rss"
    ATOM = "atom"
    SITEMAP = "sitemap"
    HTML_LIST = "html_list"
    HTML_DETAIL = "html_detail"
    GITHUB_RELEASES_ATOM = "github_releases_atom"
    GITHUB_API_REPO_ACTIVITY = "github_api_repo_activity"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# Spec dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveProviderSpec:
    """Recipe a future fetcher uses to pull from one source.

    Carries no secrets — just the public URL + headers seed. The
    fetcher layer is responsible for adding ``Authorization: Bearer …``
    when the env supplies tokens. Per-source rate limits are stored
    here so the operator can tune them without touching the registry.
    """

    source_id: str
    transport: ProviderTransport
    endpoint: str
    parser: str
    rate_limit_per_minute: int = 30
    requested_user_agent: str = "yule-engineering-intelligence/0.1 (+contact: operator)"
    request_headers_seed: Mapping[str, str] = field(default_factory=dict)
    requires_auth_env: Tuple[str, ...] = ()
    notes: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "source_id": self.source_id,
            "transport": self.transport.value,
            "endpoint": self.endpoint,
            "parser": self.parser,
            "rate_limit_per_minute": int(self.rate_limit_per_minute),
            "user_agent": self.requested_user_agent,
            "request_headers_seed": dict(self.request_headers_seed),
            "requires_auth_env": list(self.requires_auth_env),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Live provider Protocol — what a future runtime impl must satisfy
# ---------------------------------------------------------------------------


class LiveSourceFetcher(Protocol):
    """Protocol the future fetcher implementations satisfy.

    Each impl reads ``LiveProviderSpec.endpoint`` over the right
    transport, parses the bytes per ``parser``, and yields
    :class:`EngineeringKnowledgeItem` candidates. Implementations
    must:

      * Block at most a few seconds per call (the orchestrator runs
        adapters sequentially; long fetches stall the tick).
      * Never raise on transport errors — return an empty tuple and
        let the orchestrator's ``warn`` log it so the scheduler can
        record the failure and back off.
      * Never reproduce full source body — only summary + URL +
        light snippets that fit the renderer's content policy.
    """

    def __call__(
        self,
        spec: LiveProviderSpec,
        *,
        source: SourceEntry,
    ) -> Tuple[EngineeringKnowledgeItem, ...]:
        ...


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _looks_like_atom(url: str) -> bool:
    return ".atom" in url or url.endswith("/atom")


def _looks_like_github_releases(url: str) -> bool:
    return "github.com/" in url and "/releases" in url


def provider_spec_for(source: SourceEntry) -> LiveProviderSpec:
    """Build the :class:`LiveProviderSpec` for *source*.

    Heuristic but deterministic:

      * ``MANUAL`` → manual transport (no fetch).
      * ``GITHUB_API`` → repo activity (issues/releases) via REST API.
      * ``RSS`` + ``.atom`` URL → ATOM (parser distinguishes, transport
        is the same fetch).
      * ``RSS`` + GitHub releases URL → GITHUB_RELEASES_ATOM bucket
        so the fetcher can fast-path version cleanup.
      * ``SITEMAP`` → SITEMAP transport with sitemap parser.
      * ``HTML_LIST`` / ``HTML_DETAIL`` map to themselves.

    Rate limits default conservatively — operators bump them up after
    a manual probe.
    """

    base_seed: dict[str, str] = {}

    mode = source.collection_mode
    url = source.base_url

    if mode is CollectionMode.MANUAL:
        return LiveProviderSpec(
            source_id=source.source_id,
            transport=ProviderTransport.MANUAL,
            endpoint=url,
            parser="manual",
            rate_limit_per_minute=0,
            request_headers_seed=base_seed,
            notes=(
                "MANUAL — operator must register knowledge items by hand."
            ),
        )

    if mode is CollectionMode.GITHUB_API:
        return LiveProviderSpec(
            source_id=source.source_id,
            transport=ProviderTransport.GITHUB_API_REPO_ACTIVITY,
            endpoint=url,
            parser="github_api",
            rate_limit_per_minute=30,
            request_headers_seed={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            requires_auth_env=("YULE_GITHUB_APP_ID",),
            notes=(
                "Backed by the existing GitHub App client; private repo "
                "endpoints fail closed without the App env triple."
            ),
        )

    if mode is CollectionMode.RSS and _looks_like_github_releases(url):
        return LiveProviderSpec(
            source_id=source.source_id,
            transport=ProviderTransport.GITHUB_RELEASES_ATOM,
            endpoint=url,
            parser="atom",
            rate_limit_per_minute=20,
            request_headers_seed={"Accept": "application/atom+xml"},
            notes=(
                "GitHub releases atom feed — keep cadence ≤ 20/min to "
                "stay well under the unauthenticated rate limit."
            ),
        )

    if mode is CollectionMode.RSS:
        transport = (
            ProviderTransport.ATOM if _looks_like_atom(url) else ProviderTransport.RSS
        )
        return LiveProviderSpec(
            source_id=source.source_id,
            transport=transport,
            endpoint=url,
            parser=transport.value,
            rate_limit_per_minute=30,
            request_headers_seed={
                "Accept": "application/rss+xml, application/atom+xml;q=0.9",
            },
        )

    if mode is CollectionMode.SITEMAP:
        return LiveProviderSpec(
            source_id=source.source_id,
            transport=ProviderTransport.SITEMAP,
            endpoint=url,
            parser="sitemap",
            rate_limit_per_minute=10,
            request_headers_seed={"Accept": "application/xml"},
            notes=(
                "Sitemap walks are heavier — keep cadence low, diff "
                "against previous lastmod to skip unchanged sub-pages."
            ),
        )

    if mode is CollectionMode.HTML_LIST:
        return LiveProviderSpec(
            source_id=source.source_id,
            transport=ProviderTransport.HTML_LIST,
            endpoint=url,
            parser="html_list",
            rate_limit_per_minute=15,
            request_headers_seed={"Accept": "text/html"},
            notes=(
                "Index-page scrape. Parser must respect the source's "
                "content_policy — link + summary only."
            ),
        )

    # HTML_DETAIL fallback — used by ad-hoc crawl-once entries.
    return LiveProviderSpec(
        source_id=source.source_id,
        transport=ProviderTransport.HTML_DETAIL,
        endpoint=url,
        parser="html_detail",
        rate_limit_per_minute=5,
        request_headers_seed={"Accept": "text/html"},
        notes=(
            "Detail-page fetch is the slowest path — call only when "
            "list/sitemap mode missed the item."
        ),
    )


# ---------------------------------------------------------------------------
# Convenience: precompute specs for an entire role
# ---------------------------------------------------------------------------


def specs_for_role(role_id: str) -> Tuple[LiveProviderSpec, ...]:
    """Bundle every source spec for *role_id* in registry order."""

    from .source_registry import role_sources

    return tuple(provider_spec_for(s) for s in role_sources(role_id))


# ---------------------------------------------------------------------------
# Dry-run "stub" fetcher — useful for CI smoke and developer sanity
# ---------------------------------------------------------------------------


@dataclass
class StubLiveSourceFetcher:
    """Records every spec it was called with, returns empty.

    Mirrors :class:`FakeSourceCollectorAdapter` but at the spec level.
    Useful when verifying that a future scheduler wiring picked the
    expected transport per source without standing up a real HTTP
    client.
    """

    seen: List[LiveProviderSpec] = field(default_factory=list)

    def __call__(
        self,
        spec: LiveProviderSpec,
        *,
        source: SourceEntry,
    ) -> Tuple[EngineeringKnowledgeItem, ...]:
        self.seen.append(spec)
        return ()


# ---------------------------------------------------------------------------
# Deterministic fixture-based fake — used by the provider registry seed
# ---------------------------------------------------------------------------


class FakeKnowledgeProvider:
    """Fixture-based fake fetcher — returns items keyed on source_id.

    Sits between :class:`StubLiveSourceFetcher` (records, returns empty)
    and a real live provider (network). Tests / dev / cost-safe
    operator runs use this so the registry resolves to a *fetcher
    that produces predictable items* even when no live impl is
    wired.

    The contract intentionally matches :class:`LiveSourceFetcher`:
    the orchestrator tick code calls one Protocol regardless of
    whether bytes came from disk, fixture, or HTTP.
    """

    def __init__(
        self,
        payload: Optional[Mapping[str, Sequence[EngineeringKnowledgeItem]]] = None,
    ) -> None:
        self._payload: dict[str, Tuple[EngineeringKnowledgeItem, ...]] = {
            source_id: tuple(items)
            for source_id, items in (payload or {}).items()
        }
        self.calls: List[Tuple[str, ProviderTransport]] = []

    def with_fixture(
        self,
        source_id: str,
        items: Sequence[EngineeringKnowledgeItem],
    ) -> "FakeKnowledgeProvider":
        """Add / replace the fixture for *source_id*. Returns self."""

        self._payload[source_id] = tuple(items)
        return self

    def __call__(
        self,
        spec: LiveProviderSpec,
        *,
        source: SourceEntry,
    ) -> Tuple[EngineeringKnowledgeItem, ...]:
        self.calls.append((source.source_id, spec.transport))
        return self._payload.get(source.source_id, ())


__all__ = [
    "FakeKnowledgeProvider",
    "LiveProviderSpec",
    "LiveSourceFetcher",
    "ProviderTransport",
    "StubLiveSourceFetcher",
    "provider_spec_for",
    "specs_for_role",
]
