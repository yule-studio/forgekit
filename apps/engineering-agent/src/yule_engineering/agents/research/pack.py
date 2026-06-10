"""ResearchPack — neutral data model for research artifacts.

A :class:`ResearchPack` bundles everything we know about *one research item*
inside engineering-agent (and any future department): one or more
:class:`ResearchSource` rows (with provenance + role-driven typing), optional
:class:`ResearchAttachment` rows for non-URL artifacts (images, files,
embeds), and zero or more :class:`ResearchFinding` rows distilled by a role
on top of those sources.

The shape is **transport-agnostic on purpose**:

- Discord forum publisher (``discord/research_forum.py``) ingests these
  to produce thread bodies and per-role comments.
- dispatcher / workflow may later read ``url`` lists for reference packs.
- Obsidian export (``obsidian_export.py``) serializes these to markdown.

This module never calls Discord, never reads the network, and never
writes files. It's pure dataclasses + small URL/dedup/classification
helpers, so unit tests can exercise it without any I/O.

The model is also **role-aware**: each source records who collected it
(``collected_by_role``) and why (``why_relevant``), so per-role research
profiles (product-designer focuses on image/design references, backend
focuses on official_docs/code_context, etc.) can be enforced upstream
without changing the storage shape.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Source typing
# ---------------------------------------------------------------------------


class SourceType(str, Enum):
    """The canonical kinds of research material engineering-agent recognises.

    The string values are stable identifiers used in serialization
    (markdown headings, dict round-trip, frontmatter). Adding a new value
    requires updating ``research-pack.md`` to keep the policy and code in
    sync.
    """

    USER_MESSAGE = "user_message"
    URL = "url"
    WEB_RESULT = "web_result"
    IMAGE_REFERENCE = "image_reference"
    FILE_ATTACHMENT = "file_attachment"
    GITHUB_ISSUE = "github_issue"
    GITHUB_PR = "github_pr"
    CODE_CONTEXT = "code_context"
    OFFICIAL_DOCS = "official_docs"
    COMMUNITY_SIGNAL = "community_signal"
    DESIGN_REFERENCE = "design_reference"
    UNKNOWN = "unknown"


_IMAGE_EXTS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".heic", ".heif", ".tif", ".tiff"}
)
_IMAGE_MIME_PREFIX = "image/"


def classify_attachment(
    *,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    fallback: SourceType = SourceType.FILE_ATTACHMENT,
) -> SourceType:
    """Classify an attachment as :data:`SourceType.IMAGE_REFERENCE` or *fallback*.

    Looks at the MIME prefix first (``image/png`` etc.), then falls back
    to the filename extension. Vision analysis is intentionally *not*
    performed here — we only decide whether the artifact should be
    routed to product-designer's image bucket.
    """

    if isinstance(content_type, str) and content_type.lower().startswith(_IMAGE_MIME_PREFIX):
        return SourceType.IMAGE_REFERENCE
    if isinstance(filename, str):
        lower = filename.lower()
        for ext in _IMAGE_EXTS:
            if lower.endswith(ext):
                return SourceType.IMAGE_REFERENCE
    return fallback


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchAttachment:
    """A non-URL artifact attached to a source (image, file, embed).

    ``kind`` is free-form (``image``/``file``/``embed``/...) so we can carry
    Discord attachment shapes without coupling to discord.py types.
    ``attachment_id`` is the upstream identifier (Discord attachment id /
    storage id) so dedup across re-imports stays stable.
    """

    kind: str
    url: str
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    description: Optional[str] = None
    attachment_id: Optional[str] = None


def normalize_attachment_metadata(att: ResearchAttachment) -> ResearchAttachment:
    """Return *att* with cleaned/normalised metadata.

    - ``content_type`` lower-cased and trimmed.
    - ``filename`` trimmed; empty becomes None.
    - ``kind`` upgraded to ``image`` when MIME or extension suggests it
      and the existing kind is ``file`` / blank / generic.
    - ``size_bytes`` clamped to non-negative ints; non-numeric becomes None.
    """

    filename = (att.filename or None)
    if isinstance(filename, str):
        filename = filename.strip() or None
    content_type = (att.content_type or None)
    if isinstance(content_type, str):
        content_type = content_type.strip().lower() or None

    size_bytes: Optional[int] = att.size_bytes
    if size_bytes is not None:
        try:
            size_bytes = int(size_bytes)
            if size_bytes < 0:
                size_bytes = None
        except (TypeError, ValueError):
            size_bytes = None

    classified = classify_attachment(filename=filename, content_type=content_type)
    kind = (att.kind or "").strip().lower() or "file"
    if classified == SourceType.IMAGE_REFERENCE and kind in {"file", "attachment", ""}:
        kind = "image"

    return replace(
        att,
        kind=kind,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
    )


@dataclass(frozen=True)
class ResearchSource:
    """A single piece of provenance for a research pack.

    Each source carries:

    - **what** — ``title`` / ``summary`` / ``url`` / ``attachment_id``
    - **typing** — :class:`SourceType` and optional kind-specific metadata
      via ``extra``
    - **who and why** — ``collected_by_role`` (preferred over the legacy
      ``author_role``) plus ``why_relevant`` / ``risk_or_limit`` / ``confidence``
    - **provenance** — ``channel_id`` / ``thread_id`` / ``message_id`` for
      Discord-origin sources
    - **when** — ``collected_at`` (preferred) / legacy ``posted_at``

    All fields except ``source_url`` are optional at the dataclass level;
    constructor helpers (``source_from_*``) ensure each :class:`SourceType`
    gets the fields it actually needs.
    """

    source_url: Optional[str] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    author_role: Optional[str] = None
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    message_id: Optional[int] = None
    posted_at: Optional[datetime] = None
    attachments: Sequence[ResearchAttachment] = field(default_factory=tuple)
    extra: Mapping[str, Any] = field(default_factory=dict)

    # Rich source metadata (added in v0.2). All optional so existing
    # ``ResearchSource(source_url=..., title=...)`` constructors continue
    # to work unchanged.
    source_type: SourceType = SourceType.UNKNOWN
    collected_by_role: Optional[str] = None
    why_relevant: Optional[str] = None
    risk_or_limit: Optional[str] = None
    collected_at: Optional[datetime] = None
    confidence: Optional[str] = None
    attachment_id: Optional[str] = None
    source_id: Optional[str] = None

    @property
    def discord_origin(self) -> bool:
        return any(
            v is not None for v in (self.channel_id, self.thread_id, self.message_id)
        )

    @property
    def role(self) -> Optional[str]:
        """Resolved role — prefers ``collected_by_role`` then ``author_role``."""

        return self.collected_by_role or self.author_role

    @property
    def timestamp(self) -> Optional[datetime]:
        """Resolved timestamp — prefers ``collected_at`` then ``posted_at``."""

        return self.collected_at or self.posted_at

    @property
    def stable_id(self) -> str:
        """Best-effort stable id for this source (used by findings)."""

        if self.source_id:
            return self.source_id
        seed_bits = (
            self.message_id,
            self.thread_id,
            self.channel_id,
            self.attachment_id,
            _clean_url(self.source_url),
            (self.title or "").strip(),
        )
        seed = "|".join("" if v is None else str(v) for v in seed_bits)
        if not seed.strip("|"):
            seed = uuid.uuid4().hex
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]


@dataclass(frozen=True)
class ResearchRequest:
    """An explicit ask to collect research for a session/topic.

    Recorded so the resulting pack can be replayed: who asked for what,
    when, with which role-driven research profile.
    """

    request_id: str
    topic: str
    role: str
    session_id: Optional[str] = None
    context: Mapping[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class ResearchFinding:
    """A higher-level conclusion distilled from one or more sources.

    A finding can be authored by any role — ``role`` records who reached
    it. ``supporting_source_ids`` references :attr:`ResearchSource.stable_id`
    so the link between conclusion and evidence stays explicit.
    """

    finding_id: str
    title: str
    summary: str
    role: str
    supporting_source_ids: Sequence[str] = field(default_factory=tuple)
    confidence: str = "medium"
    risk_or_limit: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class ResearchPack:
    """The composite artifact: title + summary + N sources + N findings.

    ``primary_url`` is a convenience pointer (often the first source's URL).
    ``urls`` is the deduped union across all sources + ``primary_url``.
    Both are derived; constructing helpers preserve them.
    """

    title: str
    summary: str = ""
    primary_url: Optional[str] = None
    sources: Sequence[ResearchSource] = field(default_factory=tuple)
    tags: Sequence[str] = field(default_factory=tuple)
    created_at: Optional[datetime] = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    request: Optional[ResearchRequest] = None
    findings: Sequence[ResearchFinding] = field(default_factory=tuple)

    @property
    def urls(self) -> Tuple[str, ...]:
        seen: dict[str, None] = {}
        for url in (self.primary_url, *(s.source_url for s in self.sources)):
            cleaned = _clean_url(url)
            if cleaned and cleaned not in seen:
                seen[cleaned] = None
        return tuple(seen.keys())

    @property
    def attachments(self) -> Tuple[ResearchAttachment, ...]:
        seen: dict[Tuple[str, str], ResearchAttachment] = {}
        for source in self.sources:
            for att in source.attachments:
                key = (att.kind, _clean_url(att.url) or att.url)
                if key not in seen:
                    seen[key] = att
        return tuple(seen.values())

    @property
    def author_roles(self) -> Tuple[str, ...]:
        """Distinct roles that contributed sources, in first-seen order.

        Resolves :attr:`ResearchSource.role` so callers don't have to know
        whether ``collected_by_role`` or legacy ``author_role`` was used.
        """

        seen: dict[str, None] = {}
        for source in self.sources:
            role = (source.role or "").strip()
            if role and role not in seen:
                seen[role] = None
        return tuple(seen.keys())

    def sources_by_type(self) -> dict[SourceType, list[ResearchSource]]:
        """Group sources by :class:`SourceType`, preserving original order."""

        grouped: dict[SourceType, list[ResearchSource]] = {}
        for source in self.sources:
            grouped.setdefault(source.source_type, []).append(source)
        return grouped


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


_URL_PATTERN = re.compile(r"https?://[\w\-./?=&%#:+,@!~*'();$]+", re.IGNORECASE)
_TRAILING_TRIM = ".,);"


def extract_urls(text: str) -> Tuple[str, ...]:
    """Pull URLs out of free text, dedup while preserving first-seen order."""

    if not text:
        return ()
    seen: dict[str, None] = {}
    for raw in _URL_PATTERN.findall(text):
        cleaned = _clean_url(raw)
        if cleaned and cleaned not in seen:
            seen[cleaned] = None
    return tuple(seen.keys())


def dedup_urls(urls: Iterable[Optional[str]]) -> Tuple[str, ...]:
    """Return *urls* with whitespace/trailing punctuation cleaned and deduped.

    Preserves first-seen order. Empty/None inputs are dropped.
    """

    seen: dict[str, None] = {}
    for url in urls:
        cleaned = _clean_url(url)
        if cleaned and cleaned not in seen:
            seen[cleaned] = None
    return tuple(seen.keys())


def _clean_url(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text.rstrip(_TRAILING_TRIM)


# ---------------------------------------------------------------------------
# Shared id / time helpers (used by builders in pack_build.py + pack_render.py)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.utcnow()


def _gen_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:10]
    return f"{prefix}-{short}" if prefix else short


# ---------------------------------------------------------------------------
# Pack constructors / merging
# ---------------------------------------------------------------------------


def pack_from_discord_message(
    *,
    title: str,
    content: str,
    author_role: Optional[str] = None,
    channel_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    message_id: Optional[int] = None,
    posted_at: Optional[datetime] = None,
    attachments: Sequence[ResearchAttachment] = (),
    summary: Optional[str] = None,
    tags: Sequence[str] = (),
    extra: Optional[Mapping[str, Any]] = None,
) -> ResearchPack:
    """Build a single-source pack from one Discord message.

    Preserved at original signature for backward compatibility. The single
    source is typed as :data:`SourceType.USER_MESSAGE` and its
    ``collected_by_role`` mirrors ``author_role`` so role-aware properties
    keep working.
    """

    urls = extract_urls(content)
    primary = urls[0] if urls else None
    normalized_attachments = tuple(normalize_attachment_metadata(att) for att in attachments)
    source = ResearchSource(
        source_type=SourceType.USER_MESSAGE,
        source_url=primary,
        title=title or None,
        summary=(summary or content).strip() or None,
        author_role=author_role,
        collected_by_role=author_role,
        channel_id=channel_id,
        thread_id=thread_id,
        message_id=message_id,
        posted_at=posted_at,
        collected_at=posted_at,
        attachments=normalized_attachments,
    )
    pack_summary = (summary or content).strip()
    return ResearchPack(
        title=title.strip() or "(untitled)",
        summary=pack_summary,
        primary_url=primary,
        sources=(source,),
        tags=tuple(tags),
        created_at=posted_at,
        extra=dict(extra or {}),
    )


def pack_from_request(
    *,
    request: ResearchRequest,
    sources: Sequence[ResearchSource] = (),
    findings: Sequence[ResearchFinding] = (),
    title: Optional[str] = None,
    summary: Optional[str] = None,
    tags: Sequence[str] = (),
    extra: Optional[Mapping[str, Any]] = None,
) -> ResearchPack:
    """Build a pack tied to an explicit :class:`ResearchRequest`.

    ``primary_url`` is the first non-empty source URL. ``created_at`` is
    the request's timestamp (or the earliest source timestamp if the
    request has none).
    """

    cleaned_sources = tuple(sources)
    primary = next(
        (_clean_url(s.source_url) for s in cleaned_sources if _clean_url(s.source_url)),
        "",
    ) or None
    timestamps = [
        ts
        for ts in (request.created_at, *(s.timestamp for s in cleaned_sources))
        if ts is not None
    ]
    created_at = min(timestamps) if timestamps else None
    return ResearchPack(
        title=(title or request.topic).strip() or "(untitled)",
        summary=(summary or "").strip(),
        primary_url=primary,
        sources=cleaned_sources,
        tags=tuple(tags),
        created_at=created_at,
        extra=dict(extra or {}),
        request=request,
        findings=tuple(findings),
    )


def merge_packs(packs: Sequence[ResearchPack]) -> ResearchPack:
    """Fold N packs into one — preserving first non-empty title/summary.

    Sources, findings, tags, and URLs are unioned with dedup. ``primary_url``
    is the first non-empty URL seen across input packs. ``created_at`` is
    the earliest non-None timestamp. Useful when forum publisher folds
    multiple messages from a thread into one composite pack.
    """

    if not packs:
        raise ValueError("merge_packs requires at least one input pack")

    title = next(
        (p.title for p in packs if (p.title or "").strip() and p.title != "(untitled)"),
        packs[0].title,
    )
    summary = next((p.summary for p in packs if (p.summary or "").strip()), "")
    primary_url = next((p.primary_url for p in packs if _clean_url(p.primary_url)), None)

    seen_sources: dict[Tuple[Any, ...], ResearchSource] = {}
    for p in packs:
        for s in p.sources:
            key = _source_dedup_key(s)
            if key not in seen_sources:
                seen_sources[key] = s

    seen_findings: dict[str, ResearchFinding] = {}
    for p in packs:
        for f in p.findings:
            seen_findings.setdefault(f.finding_id, f)

    seen_tags: dict[str, None] = {}
    for p in packs:
        for tag in p.tags:
            t = (tag or "").strip()
            if t and t not in seen_tags:
                seen_tags[t] = None

    timestamps = [p.created_at for p in packs if p.created_at is not None]
    created_at = min(timestamps) if timestamps else None

    merged_extra: dict[str, Any] = {}
    for p in packs:
        for k, v in (p.extra or {}).items():
            merged_extra.setdefault(k, v)

    request = next((p.request for p in packs if p.request is not None), None)

    return ResearchPack(
        title=title,
        summary=summary,
        primary_url=_clean_url(primary_url) or None,
        sources=tuple(seen_sources.values()),
        tags=tuple(seen_tags.keys()),
        created_at=created_at,
        extra=merged_extra,
        request=request,
        findings=tuple(seen_findings.values()),
    )


def pack_with_extra_source(
    pack: ResearchPack,
    source: ResearchSource,
) -> ResearchPack:
    """Return a copy of *pack* with *source* appended (deduped)."""

    key = _source_dedup_key(source)
    existing_keys = {_source_dedup_key(s) for s in pack.sources}
    if key in existing_keys:
        return pack
    new_sources = tuple(pack.sources) + (source,)
    new_primary = pack.primary_url or _clean_url(source.source_url) or None
    return replace(pack, sources=new_sources, primary_url=new_primary)


def pack_with_finding(
    pack: ResearchPack,
    finding: ResearchFinding,
) -> ResearchPack:
    """Return a copy of *pack* with *finding* appended (deduped by id)."""

    if any(f.finding_id == finding.finding_id for f in pack.findings):
        return pack
    new_findings = tuple(pack.findings) + (finding,)
    return replace(pack, findings=new_findings)


def _source_dedup_key(source: ResearchSource) -> Tuple[Any, ...]:
    """Stable identity tuple for source dedup.

    Includes ``source_type`` and ``attachment_id`` so two file_attachments
    with different upstream ids (but no message_id) are not merged, while
    a same-message+url duplicate still folds.
    """

    return (
        source.source_type.value if isinstance(source.source_type, SourceType) else str(source.source_type),
        source.message_id,
        source.thread_id,
        source.channel_id,
        source.attachment_id,
        _clean_url(source.source_url),
    )


# ---------------------------------------------------------------------------
# Small text helpers (used by source/pack builders above)
# ---------------------------------------------------------------------------


def _excerpt(text: str, max_len: int) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    head = body.splitlines()[0].strip()
    if len(head) > max_len:
        head = head[: max_len - 3] + "..."
    return head
