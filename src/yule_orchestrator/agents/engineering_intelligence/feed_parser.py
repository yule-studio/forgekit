"""Deterministic feed parser — bytes → :class:`EngineeringKnowledgeItem`.

Round 4-ter brings the safest provider class (public RSS / Atom /
GitHub releases atom) one step closer to live without taking the
network dependency yet. The transport seam (.providers.LiveSourceFetcher)
still has no urllib import and no live impl in the registry — but the
*parser half* lands here so a follow-up PR can ship a thin urllib
``BytesFetcher`` and call ``register_safe_feed_providers`` instead of
writing a full transport+parser.

What ships now:

  * :func:`parse_atom_bytes` / :func:`parse_rss_bytes` /
    :func:`parse_feed_bytes` — pure functions over an XML byte
    payload. Fill the required :class:`EngineeringKnowledgeItem`
    fields from the source's metadata and the entry's title / link /
    summary / published timestamp.
  * :class:`BytesFetcher` — Protocol the live half satisfies once
    the urllib client lands. Tests pass a closure returning canned
    XML; production passes a small urllib wrapper.
  * :func:`make_feed_live_factory` — glues a BytesFetcher to the
    parser and returns a ``LiveFetcherFactory`` shaped exactly like
    ``KnowledgeProviderRegistry.register_live`` expects.
  * :func:`register_safe_feed_providers` — convenience: registers
    RSS / ATOM / GITHUB_RELEASES_ATOM in one call so the operator
    flips three transports at once when the urllib bytes_fetcher is
    ready.

Strict offline. Only stdlib ``xml.etree.ElementTree`` /
``email.utils.parsedate_to_datetime`` are imported. No urllib /
requests / socket. Tests pin the parser end-to-end against canned
XML so the contract can't silently regress when the live PR plugs
the BytesFetcher in.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Optional, Protocol, Tuple
from xml.etree import ElementTree as ET

from .models import (
    EngineeringKnowledgeItem,
    Importance,
    SourceEntry,
)
from .providers import LiveProviderSpec, ProviderTransport


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_SUMMARY_LIMIT = 500


class FeedParserError(Exception):
    """Raised when bytes are not a valid feed.

    Distinct from a transport error: malformed XML usually means the
    operator wired a wrong endpoint, not a transient blip. The live
    factory swallows transport errors but logs / surfaces parse
    failures so the dashboard can flag the bad source.
    """


class BytesFetcher(Protocol):
    """The live half of a feed fetch — bytes only, no parsing."""

    def __call__(self, spec: LiveProviderSpec) -> bytes:
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1(
        "|".join(parts).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _coerce_iso(text: str) -> Optional[str]:
    """ISO-8601 (atom) or RFC-822 (rss) → ``YYYY-MM-DDTHH:MM:SSZ``.

    Best-effort: parse with :func:`datetime.fromisoformat` first and
    fall back to :func:`email.utils.parsedate_to_datetime` for the
    RSS-2.0 ``Mon, 01 Jan 2026 00:00:00 GMT`` shape. Anything else
    returns ``None`` — the item still flows through with no
    ``published_at``; freshness scoring will rank it lower but the
    pipeline doesn't drop it.
    """

    text = (text or "").strip()
    if not text:
        return None
    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(elem: Optional[ET.Element]) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _trim(value: str, *, limit: int = _SUMMARY_LIMIT) -> str:
    """Cap summary length per content_policy ("light quotation only")."""

    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _build_item(
    source: SourceEntry,
    *,
    title: str,
    url: str,
    summary: str,
    published: Optional[str],
) -> EngineeringKnowledgeItem:
    """Compose one :class:`EngineeringKnowledgeItem` from parsed feed bits.

    The collector / dedup layer takes over from here — it sets the
    final ``dedup_key`` and may merge with prior items. We just have
    to fill enough fields that those layers don't drop the row.
    """

    role = source.role_tags[0] if source.role_tags else "tech-lead"
    final_title = title or url or source.name
    final_url = url or source.base_url
    item_id = _hash_id(source.source_id, final_title, final_url)
    topic_key = _hash_id("topic", source.source_id, final_title.lower())
    return EngineeringKnowledgeItem(
        item_id=item_id,
        topic_key=topic_key,
        title=final_title,
        role=role,
        stack_tags=tuple(source.stack_tags),
        source_name=source.name,
        source_url=final_url,
        source_kind=source.source_kind,
        collected_at=_now_iso(),
        published_at=published,
        importance=Importance.MEDIUM,
        summary=_trim(summary),
        rag_tags=tuple(source.stack_tags),
    )


# ---------------------------------------------------------------------------
# Atom 1.0 parser
# ---------------------------------------------------------------------------


def parse_atom_bytes(
    payload: bytes,
    *,
    source: SourceEntry,
) -> Tuple[EngineeringKnowledgeItem, ...]:
    """Parse an Atom 1.0 feed into items for *source*.

    Recognises the standard ``<feed><entry>...`` shape plus
    ``<link rel="alternate" href="...">`` (preferred) with a
    fallback to the first ``<link>``. ``updated`` is the canonical
    timestamp; ``published`` is the fallback when ``updated`` is
    absent.
    """

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FeedParserError(f"atom parse failed: {exc}") from exc

    items: list[EngineeringKnowledgeItem] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = _text(entry.find(f"{_ATOM_NS}title"))

        link_elem = entry.find(f"{_ATOM_NS}link[@rel='alternate']")
        if link_elem is None:
            link_elem = entry.find(f"{_ATOM_NS}link")
        url = (link_elem.get("href") if link_elem is not None else "") or ""

        summary = _text(entry.find(f"{_ATOM_NS}summary"))
        if not summary:
            summary = _text(entry.find(f"{_ATOM_NS}content"))

        published = _coerce_iso(_text(entry.find(f"{_ATOM_NS}updated")))
        if not published:
            published = _coerce_iso(_text(entry.find(f"{_ATOM_NS}published")))

        items.append(
            _build_item(
                source,
                title=title,
                url=url,
                summary=summary,
                published=published,
            )
        )
    return tuple(items)


# ---------------------------------------------------------------------------
# RSS 2.0 parser
# ---------------------------------------------------------------------------


def parse_rss_bytes(
    payload: bytes,
    *,
    source: SourceEntry,
) -> Tuple[EngineeringKnowledgeItem, ...]:
    """Parse an RSS 2.0 (or RDF) feed into items for *source*.

    Handles the ``<rss><channel><item>...`` shape. RDF (``<rdf:RDF>``)
    falls back to scanning the root for ``<item>`` directly so older
    feeds still parse without a namespace dance.
    """

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FeedParserError(f"rss parse failed: {exc}") from exc

    channel = root.find("channel")
    item_elements = (
        channel.findall("item") if channel is not None else root.findall("item")
    )

    items: list[EngineeringKnowledgeItem] = []
    for item in item_elements:
        title = _text(item.find("title"))
        url = _text(item.find("link"))
        summary = _text(item.find("description"))
        published = _coerce_iso(_text(item.find("pubDate")))
        items.append(
            _build_item(
                source,
                title=title,
                url=url,
                summary=summary,
                published=published,
            )
        )
    return tuple(items)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _looks_like_atom_payload(payload: bytes) -> bool:
    head = payload[:200].lower()
    return b"<feed" in head and b"atom" in head


def parse_feed_bytes(
    payload: bytes,
    *,
    source: SourceEntry,
    transport: ProviderTransport,
) -> Tuple[EngineeringKnowledgeItem, ...]:
    """Pick the right parser for *transport* and apply it to *payload*.

    GitHub releases atom uses the same Atom 1.0 shape as a regular
    atom feed, so they share a parser. RSS-mode sources sometimes
    expose an Atom payload (we already do this URL heuristic at
    spec-build time, but a misregistered source can leak through);
    we sniff the head and dispatch to the atom parser when the bytes
    say "feed/atom".
    """

    if transport in (
        ProviderTransport.ATOM,
        ProviderTransport.GITHUB_RELEASES_ATOM,
    ):
        return parse_atom_bytes(payload, source=source)
    if transport is ProviderTransport.RSS:
        if _looks_like_atom_payload(payload):
            return parse_atom_bytes(payload, source=source)
        return parse_rss_bytes(payload, source=source)
    raise FeedParserError(
        f"unsupported transport for feed parser: {transport.value}"
    )


# ---------------------------------------------------------------------------
# Live factory glue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedFetchOutcome:
    """Diagnostic record the live factory can hand back per call.

    Currently unused by the registry's :class:`LiveSourceFetcher`
    callable contract (which returns items only) — kept around as a
    lightweight pure-data shape so the future operator dashboard can
    surface "fetch ok / parse failed / transport error" without
    re-running the call.
    """

    source_id: str
    transport: str
    item_count: int
    fetched_bytes: int
    parse_ok: bool
    note: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "source_id": self.source_id,
            "transport": self.transport,
            "item_count": int(self.item_count),
            "fetched_bytes": int(self.fetched_bytes),
            "parse_ok": bool(self.parse_ok),
            "note": self.note,
        }


def make_feed_live_factory(
    bytes_fetcher_factory: Callable[[Mapping[str, str]], BytesFetcher],
    *,
    parser: Callable[
        ..., Tuple[EngineeringKnowledgeItem, ...]
    ] = parse_feed_bytes,
) -> Callable[[Mapping[str, str]], Any]:
    """Wrap *bytes_fetcher_factory* into a registry-shaped live factory.

    The registered live factory must take ``env`` and return a
    :class:`LiveSourceFetcher`. The bytes-fetcher factory takes the
    same env and returns a :class:`BytesFetcher`. We glue them with
    the parser and obey the LiveSourceFetcher Protocol contract:

      * Never raise on transport errors — return ``()`` so the
        scheduler can record the failure and back off.
      * Empty payload → empty tuple. The collector treats this the
        same as "feed has no new items".
      * Parser failures are swallowed at this layer too (the live
        factory's job is to be safe by default); callers that want
        the explicit error observe it via :class:`FeedFetchOutcome`
        once the dashboard lands.
    """

    def factory(env: Mapping[str, str]) -> Callable[..., Any]:
        fetcher = bytes_fetcher_factory(env)

        def fetch(spec: LiveProviderSpec, *, source: SourceEntry):
            try:
                payload = fetcher(spec)
            except Exception:
                return ()
            if not payload:
                return ()
            try:
                return parser(payload, source=source, transport=spec.transport)
            except FeedParserError:
                return ()

        return fetch

    return factory


def register_safe_feed_providers(
    registry: Any,
    *,
    bytes_fetcher_factory: Callable[[Mapping[str, str]], BytesFetcher],
    transports: Tuple[ProviderTransport, ...] = (
        ProviderTransport.RSS,
        ProviderTransport.ATOM,
        ProviderTransport.GITHUB_RELEASES_ATOM,
    ),
) -> Tuple[ProviderTransport, ...]:
    """Register the parser-backed live factory on each of *transports*.

    Returns the transports that were actually registered. Skips any
    transport that is not in the registry rather than raising — the
    operator can choose a subset (e.g. atom-only) without having to
    pre-trim the registry.
    """

    factory = make_feed_live_factory(bytes_fetcher_factory)
    registered: list[ProviderTransport] = []
    for transport in transports:
        if transport not in registry:
            continue
        registry.register_live(transport, live_factory=factory)
        registered.append(transport)
    return tuple(registered)


__all__ = [
    "BytesFetcher",
    "FeedFetchOutcome",
    "FeedParserError",
    "make_feed_live_factory",
    "parse_atom_bytes",
    "parse_feed_bytes",
    "parse_rss_bytes",
    "register_safe_feed_providers",
]
