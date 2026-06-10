"""Per-provider research adapters extracted from ``collector.py``.

This module holds the **provider adapter** layer of the autonomous
research collector — split out of ``collector.py`` so that file keeps a
thin core orchestration surface (config, query building, the collection
loop, outcome flow) and the per-provider fetch/parse logic lives here.

Cohesive groups in this module:

- **Live provider skeletons** — :class:`TavilySearchCollector` and
  :class:`BraveSearchCollector` (each handles one search provider's
  request shape + auth + response parsing).
- **Generic result coercion** — :func:`_result_dict_to_source` plus the
  field-name extractors (``_first_string`` / ``_first_thumbnail`` /
  ``_first_provider_score``) that tolerate cross-provider field naming.
- **Domain → SourceType classification** — :func:`_classify_remote_source_type`
  and :func:`extract_domain` (network-free, URL/domain only).
- **Multi-provider composite** — :class:`MultiProviderCollector` plus its
  dedupe/rank helpers (``_with_provider_rank`` / ``_normalize_url`` /
  ``_dedupe_sources``), and the env-driven factory (:func:`build_collector`
  / :func:`_build_auto_collector`) that wires the right adapter(s) up.
- **HTTP helpers** — ``_http_get_json`` / ``_http_post_json`` (only used
  by the live skeletons; never exercised in tests).

Import direction is one-way: this module imports the collector *core*
(interface base classes, config, budget, constants, mock/noop collectors,
``compute_confidence`` / ``short_role`` / ``parse_github_url``). The core
re-exports the symbols here for its public surface and to wire the
factory — collector core → providers is the legal call direction.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence, Tuple

from .pack import ResearchAttachment, ResearchSource, SourceType
from .collector import (
    DEFAULT_AUTO_PROVIDERS,
    DEFAULT_ROLE_PROVIDER_POLICY,
    ENV_BRAVE_API_KEY,
    ENV_TAVILY_API_KEY,
    PROVIDER_BRAVE,
    PROVIDER_TAVILY,
    BudgetTracker,
    CollectorConfig,
    CollectorError,
    CollectorQuery,
    MockSearchCollector,
    NoOpCollector,
    ProviderUnavailable,
    ResearchCollector,
    compute_confidence,
    parse_github_url,
    short_role,
)


# ---------------------------------------------------------------------------
# Provider skeletons (Tavily / Brave) — never invoked in tests
# ---------------------------------------------------------------------------


class TavilySearchCollector(ResearchCollector):
    """Skeleton Tavily collector — used when api_key is set.

    Calls ``https://api.tavily.com/search``. Tests don't exercise this
    path because :func:`build_collector` falls back to mock when keys
    are missing.
    """

    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, *, api_key: str, timeout_seconds: int = 10) -> None:
        if not api_key:
            raise ProviderUnavailable("tavily provider requires an api_key")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def search(self, query: CollectorQuery) -> Sequence[ResearchSource]:
        payload = {
            "api_key": self.api_key,
            "query": query.query,
            "max_results": max(1, query.max_results),
        }
        try:
            data = _http_post_json(
                self.endpoint, payload=payload, timeout_seconds=self.timeout_seconds
            )
        except Exception as exc:  # noqa: BLE001 - surface as collector error
            raise CollectorError(f"tavily search failed: {exc}") from exc
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return ()
        collected_at = datetime.utcnow()
        return tuple(
            _result_dict_to_source(
                item, query=query, collected_at=collected_at, provider="tavily"
            )
            for item in results
            if isinstance(item, dict)
        )


class BraveSearchCollector(ResearchCollector):
    """Skeleton Brave Search collector. Auth via ``X-Subscription-Token`` header."""

    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, *, api_key: str, timeout_seconds: int = 10) -> None:
        if not api_key:
            raise ProviderUnavailable("brave provider requires an api_key")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def search(self, query: CollectorQuery) -> Sequence[ResearchSource]:
        url = self.endpoint + "?" + urllib.parse.urlencode(
            {"q": query.query, "count": max(1, query.max_results)}
        )
        try:
            data = _http_get_json(
                url,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raise CollectorError(f"brave search failed: {exc}") from exc
        web = data.get("web") if isinstance(data, dict) else None
        results = web.get("results") if isinstance(web, dict) else None
        if not isinstance(results, list):
            return ()
        collected_at = datetime.utcnow()
        return tuple(
            _result_dict_to_source(
                item, query=query, collected_at=collected_at, provider="brave"
            )
            for item in results
            if isinstance(item, dict)
        )


_TITLE_KEYS = ("title", "name", "headline", "heading")
_URL_KEYS = ("url", "link", "href", "web_url")
_SNIPPET_KEYS = ("snippet", "description", "content", "summary", "body", "excerpt")
_THUMBNAIL_KEYS = ("thumbnail", "image", "image_url", "favicon", "thumb")
_SCORE_KEYS = ("score", "relevance", "relevance_score", "confidence")


def _first_string(item: Mapping[str, Any], keys: Sequence[str]) -> str:
    """Return the first non-empty string under any of *keys* (or empty)."""

    for key in keys:
        value = item.get(key) if isinstance(item, Mapping) else None
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return ""


def _first_thumbnail(item: Mapping[str, Any]) -> Optional[str]:
    """Robustly extract a thumbnail URL from various provider shapes.

    Handles plain strings, ``{"src": ...}``, ``{"url": ...}``, and
    ``[{"url": ...}, ...]`` lists. Returns ``None`` if nothing usable.
    """

    for key in _THUMBNAIL_KEYS:
        value = item.get(key) if isinstance(item, Mapping) else None
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        elif isinstance(value, Mapping):
            for sub in ("src", "url", "href"):
                sub_value = value.get(sub)
                if isinstance(sub_value, str) and sub_value.strip():
                    return sub_value.strip()
        elif isinstance(value, (list, tuple)) and value:
            for entry in value:
                if isinstance(entry, str) and entry.strip():
                    return entry.strip()
                if isinstance(entry, Mapping):
                    for sub in ("src", "url", "href"):
                        sub_value = entry.get(sub)
                        if isinstance(sub_value, str) and sub_value.strip():
                            return sub_value.strip()
    return None


def _first_provider_score(item: Mapping[str, Any]) -> Optional[float]:
    """Return a numeric provider score in [0, 1] when surfaced."""

    for key in _SCORE_KEYS:
        value = item.get(key) if isinstance(item, Mapping) else None
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _result_dict_to_source(
    item: Mapping[str, Any],
    *,
    query: CollectorQuery,
    collected_at: datetime,
    provider: str = "live",
) -> ResearchSource:
    """Coerce a generic provider result into our :class:`ResearchSource` shape.

    Defensive: tolerates field-name variations across providers, missing
    fields, dict/list-shaped thumbnails, and unknown extra keys. Returns
    a usable :class:`ResearchSource` even when most fields are absent —
    a placeholder title (``"(untitled)"``) keeps the pack renderable.
    """

    if not isinstance(item, Mapping):
        item = {}

    title = _first_string(item, _TITLE_KEYS) or "(untitled)"
    url = _first_string(item, _URL_KEYS)
    snippet = _first_string(item, _SNIPPET_KEYS)
    thumbnail = _first_thumbnail(item)
    provider_score = _first_provider_score(item)
    domain = extract_domain(url) if url else ""

    attachments: Tuple[ResearchAttachment, ...] = ()
    if thumbnail:
        attachments = (
            ResearchAttachment(
                kind="image",
                url=thumbnail,
                description="thumbnail (metadata only — 이미지 원본 저장 안 함)",
            ),
        )

    source_type = _classify_remote_source_type(domain, query.role, url=url or None)
    gh_meta = parse_github_url(url) if url else None

    extra: dict[str, Any] = {
        "domain": domain,
        "snippet": snippet or None,
        "thumbnail_url": thumbnail,
        "query": query.query,
        "provider": provider,
    }
    if provider_score is not None:
        extra["provider_score"] = provider_score
    if gh_meta is not None:
        extra["github"] = dict(gh_meta)

    confidence = compute_confidence(
        source_type=source_type,
        role=query.role,
        has_url=bool(url),
        has_snippet=bool(snippet),
        has_thumbnail=bool(thumbnail),
        provider_score=provider_score,
    )

    return ResearchSource(
        source_type=source_type,
        source_url=url or None,
        title=title,
        summary=snippet or None,
        collected_by_role=query.role,
        why_relevant=None,
        collected_at=collected_at,
        confidence=confidence,
        attachments=attachments,
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Domain → SourceType classification (for live providers)
# ---------------------------------------------------------------------------


_DESIGN_DOMAINS = (
    "behance.net",
    "awwwards.com",
    "mobbin.com",
    "notefolio.net",
    "dribbble.com",
    "pinterest.com",
    "canva.com",
    "wix.com",
)
_OFFICIAL_HINTS = (
    "developer.mozilla.org",
    "web.dev",
    "react.dev",
    "vuejs.org",
    "angular.io",
    "nextjs.org",
    "fastapi.tiangolo.com",
    "django",
    "postgresql.org",
    "playwright.dev",
    "testing-library.com",
    "owasp.org",
    "rfc-editor.org",
)


def _classify_remote_source_type(
    domain: str,
    role: str,
    *,
    url: Optional[str] = None,
) -> SourceType:
    """Best-effort source_type based on URL/domain only (no fetch).

    GitHub issue/PR URLs are recognised explicitly; everything else falls
    back to the domain-based heuristic.
    """

    if url:
        gh = parse_github_url(url)
        if gh is not None:
            return (
                SourceType.GITHUB_ISSUE
                if gh["kind"] == "issue"
                else SourceType.GITHUB_PR
            )

    short = (domain or "").lower()
    if any(d in short for d in _DESIGN_DOMAINS):
        return SourceType.DESIGN_REFERENCE
    if any(d in short for d in _OFFICIAL_HINTS):
        return SourceType.OFFICIAL_DOCS
    if "github.com" in short:
        # repo root / commit / wiki / etc — surface as official_docs so the
        # role profile still ranks it ahead of generic web results.
        return SourceType.OFFICIAL_DOCS
    if "reddit.com" in short or "forum" in short or "stackoverflow.com" in short:
        return SourceType.COMMUNITY_SIGNAL
    return SourceType.WEB_RESULT


def extract_domain(url: Optional[str]) -> str:
    """Return ``host[:port]`` (lower-cased) for *url*, or ``""``."""

    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(str(url))
    except Exception:  # noqa: BLE001
        return ""
    return (parsed.netloc or "").lower()


# ---------------------------------------------------------------------------
# Multi-provider composite (auto / multi mode)
# ---------------------------------------------------------------------------


class MultiProviderCollector(ResearchCollector):
    """Auto-mode composite that fans out to multiple sub-collectors.

    For each :meth:`search` call the composite walks a role-specific
    provider order (``role_policy``), invokes each available sub-collector
    in turn, and returns the deduped/merged hits. Providers whose name is
    in ``role_policy`` but absent from ``providers`` (e.g. their API key
    wasn't set) are skipped silently — :attr:`skipped_providers` lists
    them with the reason for observability.

    Budget bookkeeping:

    - The outer ``collect_research_pack`` loop calls ``budget.record_call()``
      once per role-level :meth:`search`. The composite counts that as the
      *first* inner provider's slot for free.
    - Every additional inner provider beyond the first claims another
      ``budget.record_call()`` if and only if ``budget.can_call()``. This
      keeps the operator's ``ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS``
      cap as a true ceiling on total provider invocations across the
      whole collection run.
    """

    name = "auto"

    def __init__(
        self,
        *,
        providers: Sequence[ResearchCollector],
        role_policy: Optional[Mapping[str, Tuple[str, ...]]] = None,
        budget: Optional[BudgetTracker] = None,
        skipped: Optional[Mapping[str, str]] = None,
    ) -> None:
        # Map each provider's ``name`` → instance so role policy lookup is O(1).
        self._provider_map: dict[str, ResearchCollector] = {}
        for collector in providers:
            self._provider_map[collector.name] = collector
        self._role_policy: Mapping[str, Tuple[str, ...]] = (
            role_policy if role_policy is not None else DEFAULT_ROLE_PROVIDER_POLICY
        )
        self._budget = budget
        self._skipped_providers: dict[str, str] = dict(skipped or {})
        # Inner provider call counter for observability — independent of the
        # outer BudgetTracker so tests can verify both behaviours.
        self._inner_calls = 0

    @property
    def active_providers(self) -> Tuple[str, ...]:
        return tuple(self._provider_map.keys())

    @property
    def skipped_providers(self) -> Mapping[str, str]:
        return dict(self._skipped_providers)

    @property
    def inner_calls(self) -> int:
        return self._inner_calls

    def provider_order_for_role(self, role: str) -> Tuple[str, ...]:
        """Return the ordered provider names this composite would query
        for *role*. Filters to providers actually present in the composite.

        Unknown roles fall back to ``DEFAULT_AUTO_PROVIDERS`` so the chain
        still does something useful instead of silently returning empty.
        """

        short = short_role(role)
        configured = self._role_policy.get(short)
        if configured is None:
            configured = DEFAULT_AUTO_PROVIDERS
        return tuple(name for name in configured if name in self._provider_map)

    def search(self, query: CollectorQuery) -> Sequence[ResearchSource]:
        order = self.provider_order_for_role(query.role)
        if not order:
            return ()
        merged: list[ResearchSource] = []
        first_call_consumed = False
        for provider_name in order:
            provider = self._provider_map.get(provider_name)
            if provider is None:
                continue
            if first_call_consumed:
                # The outer loop only paid for one budget slot; second-and-
                # later providers in this role's policy must claim their own.
                if self._budget is not None:
                    if not self._budget.can_call():
                        break
                    self._budget.record_call()
            first_call_consumed = True
            try:
                hits = provider.search(query)
            except CollectorError:
                hits = ()
            except Exception:  # noqa: BLE001 - never crash the composite
                hits = ()
            self._inner_calls += 1
            # Stamp provider rank inside extra so downstream rendering can
            # show "1순위 검색 — Tavily" / "2순위 검색 — Brave" without
            # re-deriving the order.
            ranked_hits: list[ResearchSource] = []
            for idx, src in enumerate(hits):
                ranked_hits.append(_with_provider_rank(src, provider_name, idx))
            merged.extend(ranked_hits)
        return _dedupe_sources(merged)


def _with_provider_rank(
    source: ResearchSource, provider: str, rank: int
) -> ResearchSource:
    """Return *source* with ``provider`` and ``provider_rank`` in extra.

    Underlying collectors already stamp ``provider``; the multi composite
    re-stamps it (defensively) and adds ``provider_rank`` so downstream
    sort/UI can preserve "this came from the 1st provider in policy".
    """

    base_extra = dict(getattr(source, "extra", {}) or {})
    base_extra.setdefault("provider", provider)
    base_extra["provider_rank"] = rank
    return ResearchSource(
        source_type=source.source_type,
        source_url=source.source_url,
        title=source.title,
        summary=source.summary,
        collected_by_role=source.collected_by_role,
        why_relevant=source.why_relevant,
        risk_or_limit=source.risk_or_limit,
        confidence=source.confidence,
        collected_at=source.collected_at,
        attachments=source.attachments,
        attachment_id=source.attachment_id,
        author_role=getattr(source, "author_role", None),
        extra=base_extra,
    )


def _normalize_url(url: Optional[str]) -> str:
    """Return a canonical form for *url* used for dedupe keys.

    Lower-cases scheme+host, strips trailing slashes from the path, drops
    fragments, and ignores common tracking query keys (``utm_*``). Two
    URLs that differ only in those things end up with the same key.
    """

    if not url:
        return ""
    text = str(url).strip()
    if not text:
        return ""
    try:
        parsed = urllib.parse.urlsplit(text)
    except Exception:  # noqa: BLE001 - defensive
        return text.lower()
    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    # Drop tracking params; keep everything else so legitimate query
    # parameters still distinguish unique pages.
    if parsed.query:
        kept = []
        for part in parsed.query.split("&"):
            if not part:
                continue
            key = part.split("=", 1)[0].lower()
            if key.startswith("utm_") or key in {"ref", "ref_src"}:
                continue
            kept.append(part)
        query = "&".join(kept)
    else:
        query = ""
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def _dedupe_sources(
    sources: Sequence[ResearchSource],
) -> Tuple[ResearchSource, ...]:
    """Drop duplicate sources from *sources* in arrival order.

    Dedupe keys (any match collapses to first occurrence):

    1. Normalised URL (lowercase scheme/host, no trailing slash, no UTM).
    2. ``(domain, source_type)`` + lower-cased title prefix — catches
       providers that return the same page under slightly different URL
       shapes (mobile/desktop variants, AMP, language redirects).
    3. URL-less sources fall back to ``(title, source_type)``.
    """

    seen_urls: set[str] = set()
    seen_titles: set[Tuple[str, str]] = set()
    seen_dom_type_title: set[Tuple[str, str, str]] = set()
    deduped: list[ResearchSource] = []
    for src in sources:
        url_key = _normalize_url(src.source_url)
        type_value = (
            src.source_type.value
            if isinstance(src.source_type, SourceType)
            else str(src.source_type)
        )
        title_key = (src.title or "").strip().lower()
        domain_key = ((src.extra or {}).get("domain") or extract_domain(src.source_url)).lower()

        if url_key:
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)
        else:
            tt = (title_key, type_value)
            if tt in seen_titles:
                continue
            seen_titles.add(tt)
        # Even when the URL differs, collapse near-duplicates that share
        # domain + type + title prefix (provider returned the same article
        # under two URLs, e.g. with/without query string).
        title_prefix = title_key[:80]
        composite = (domain_key, type_value, title_prefix)
        if title_prefix and composite in seen_dom_type_title:
            continue
        if title_prefix:
            seen_dom_type_title.add(composite)
        deduped.append(src)
    return tuple(deduped)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_collector(
    config: Optional[CollectorConfig] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    budget: Optional[BudgetTracker] = None,
) -> ResearchCollector:
    """Resolve env config and return a usable collector.

    Fallback chain:
    - ``enabled=False`` → :class:`NoOpCollector` (always returns ``()``).
    - ``provider=mock`` (default) → :class:`MockSearchCollector`.
    - ``provider=tavily`` + ``TAVILY_API_KEY`` set → :class:`TavilySearchCollector`.
    - ``provider=brave`` + ``BRAVE_SEARCH_API_KEY`` set → :class:`BraveSearchCollector`.
    - ``provider=auto`` / ``multi`` → :class:`MultiProviderCollector` with
      every external provider whose API key is set; if no key is present,
      falls back to :class:`MockSearchCollector` (so dev/test runs stay
      deterministic without leaking the auto-mode contract).
    - Provider key missing → silent fallback to :class:`MockSearchCollector`.

    Pass ``budget`` to wire a shared :class:`BudgetTracker` into the auto
    composite; without it the composite still works but the outer cap can
    be exceeded by ``len(providers) - 1`` calls per role-level search.
    """

    cfg = config if config is not None else CollectorConfig.from_env(env)
    if not cfg.enabled:
        return NoOpCollector()
    if cfg.provider == PROVIDER_TAVILY and cfg.api_key:
        try:
            return TavilySearchCollector(api_key=cfg.api_key)
        except ProviderUnavailable:
            return MockSearchCollector()
    if cfg.provider == PROVIDER_BRAVE and cfg.api_key:
        try:
            return BraveSearchCollector(api_key=cfg.api_key)
        except ProviderUnavailable:
            return MockSearchCollector()
    if cfg.is_auto:
        return _build_auto_collector(cfg, budget=budget)
    return MockSearchCollector()


def _build_auto_collector(
    cfg: CollectorConfig,
    *,
    budget: Optional[BudgetTracker] = None,
) -> ResearchCollector:
    """Construct a :class:`MultiProviderCollector` from *cfg*.

    Honours ``cfg.providers`` as the candidate list; for each candidate
    we instantiate the live provider when its API key is set and record a
    ``skipped`` reason otherwise. If no candidate ends up usable, fall
    back to :class:`MockSearchCollector` so the rest of the pipeline keeps
    working in dev environments.
    """

    candidates = cfg.providers or DEFAULT_AUTO_PROVIDERS
    instances: list[ResearchCollector] = []
    skipped: dict[str, str] = {}
    for provider_name in candidates:
        if provider_name == PROVIDER_TAVILY:
            api_key = cfg.api_keys.get(PROVIDER_TAVILY)
            if not api_key:
                skipped[PROVIDER_TAVILY] = f"{ENV_TAVILY_API_KEY} not set"
                continue
            try:
                instances.append(TavilySearchCollector(api_key=api_key))
            except ProviderUnavailable as exc:
                skipped[PROVIDER_TAVILY] = str(exc)
        elif provider_name == PROVIDER_BRAVE:
            api_key = cfg.api_keys.get(PROVIDER_BRAVE)
            if not api_key:
                skipped[PROVIDER_BRAVE] = f"{ENV_BRAVE_API_KEY} not set"
                continue
            try:
                instances.append(BraveSearchCollector(api_key=api_key))
            except ProviderUnavailable as exc:
                skipped[PROVIDER_BRAVE] = str(exc)
        # Unknown providers were filtered out at parse time; defensive
        # branch left implicit so adding a new provider is one match arm.

    if not instances:
        # Every candidate skipped → mock fallback so dev/test runs work
        # without API keys. The skipped reasons are still surfaced via
        # the outcome metadata path so operators can see *why*.
        return MockSearchCollector()

    return MultiProviderCollector(
        providers=instances,
        budget=budget,
        skipped=skipped,
    )


# ---------------------------------------------------------------------------
# HTTP helpers (used by Tavily/Brave skeletons; not exercised in tests)
# ---------------------------------------------------------------------------


def _http_get_json(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout_seconds: int,
) -> Any:  # pragma: no cover - real network only
    request = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def _http_post_json(
    url: str,
    *,
    payload: Mapping[str, Any],
    timeout_seconds: int,
) -> Any:  # pragma: no cover - real network only
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)
