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
# Source constructors (typed)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.utcnow()


def _gen_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:10]
    return f"{prefix}-{short}" if prefix else short


def source_from_user_message(
    *,
    content: str,
    collected_by_role: str,
    title: Optional[str] = None,
    channel_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    message_id: Optional[int] = None,
    collected_at: Optional[datetime] = None,
    why_relevant: Optional[str] = None,
    confidence: Optional[str] = "high",
) -> ResearchSource:
    """Build a USER_MESSAGE source from a Discord message body."""

    cleaned = (content or "").strip()
    return ResearchSource(
        source_type=SourceType.USER_MESSAGE,
        title=(title or _excerpt(cleaned, 60)) or None,
        summary=cleaned or None,
        collected_by_role=collected_by_role,
        why_relevant=why_relevant,
        confidence=confidence,
        collected_at=collected_at or _now(),
        channel_id=channel_id,
        thread_id=thread_id,
        message_id=message_id,
    )


def source_from_url(
    *,
    url: str,
    collected_by_role: str,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    why_relevant: Optional[str] = None,
    risk_or_limit: Optional[str] = None,
    confidence: Optional[str] = "medium",
    collected_at: Optional[datetime] = None,
) -> ResearchSource:
    """Build a generic URL source (user-pasted link)."""

    return ResearchSource(
        source_type=SourceType.URL,
        source_url=_clean_url(url) or None,
        title=title,
        summary=summary,
        collected_by_role=collected_by_role,
        why_relevant=why_relevant,
        risk_or_limit=risk_or_limit,
        confidence=confidence,
        collected_at=collected_at or _now(),
    )


def source_from_web_result(
    *,
    url: str,
    title: str,
    summary: str,
    collected_by_role: str,
    why_relevant: Optional[str] = None,
    risk_or_limit: Optional[str] = None,
    confidence: Optional[str] = "medium",
    collected_at: Optional[datetime] = None,
) -> ResearchSource:
    """Build a WEB_RESULT source (search engine / web crawl outcome)."""

    return ResearchSource(
        source_type=SourceType.WEB_RESULT,
        source_url=_clean_url(url) or None,
        title=title,
        summary=summary,
        collected_by_role=collected_by_role,
        why_relevant=why_relevant,
        risk_or_limit=risk_or_limit,
        confidence=confidence,
        collected_at=collected_at or _now(),
    )


def source_from_image_reference(
    *,
    url: str,
    collected_by_role: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    why_relevant: Optional[str] = None,
    attachment_id: Optional[str] = None,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    size_bytes: Optional[int] = None,
    collected_at: Optional[datetime] = None,
    confidence: Optional[str] = "medium",
) -> ResearchSource:
    """Build an IMAGE_REFERENCE source (moodboard, screenshot, mockup).

    The image itself is *not* analysed here. We only record enough metadata
    that an upstream vision pipeline (or a human) can re-fetch it later.
    """

    attachment = ResearchAttachment(
        kind="image",
        url=_clean_url(url) or url,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        description=description,
        attachment_id=attachment_id,
    )
    return ResearchSource(
        source_type=SourceType.IMAGE_REFERENCE,
        source_url=_clean_url(url) or None,
        title=title or filename or "(image)",
        summary=description,
        collected_by_role=collected_by_role,
        why_relevant=why_relevant,
        confidence=confidence,
        collected_at=collected_at or _now(),
        attachments=(normalize_attachment_metadata(attachment),),
        attachment_id=attachment_id,
    )


def source_from_file_attachment(
    *,
    url: str,
    collected_by_role: str,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    size_bytes: Optional[int] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    attachment_id: Optional[str] = None,
    why_relevant: Optional[str] = None,
    collected_at: Optional[datetime] = None,
    confidence: Optional[str] = "medium",
) -> ResearchSource:
    """Build a FILE_ATTACHMENT source.

    Auto-promotes to :class:`SourceType.IMAGE_REFERENCE` when the filename
    or content_type indicates an image — which is how Discord attachments
    end up in product-designer's bucket without callers having to branch.
    """

    classified = classify_attachment(filename=filename, content_type=content_type)
    if classified == SourceType.IMAGE_REFERENCE:
        return source_from_image_reference(
            url=url,
            collected_by_role=collected_by_role,
            title=title or filename,
            description=description,
            why_relevant=why_relevant,
            attachment_id=attachment_id,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            collected_at=collected_at,
            confidence=confidence,
        )

    attachment = ResearchAttachment(
        kind="file",
        url=_clean_url(url) or url,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        description=description,
        attachment_id=attachment_id,
    )
    return ResearchSource(
        source_type=SourceType.FILE_ATTACHMENT,
        source_url=_clean_url(url) or None,
        title=title or filename or "(file)",
        summary=description,
        collected_by_role=collected_by_role,
        why_relevant=why_relevant,
        confidence=confidence,
        collected_at=collected_at or _now(),
        attachments=(normalize_attachment_metadata(attachment),),
        attachment_id=attachment_id,
    )


def source_from_github_issue(
    *,
    url: str,
    title: str,
    collected_by_role: str,
    summary: Optional[str] = None,
    issue_number: Optional[int] = None,
    repository: Optional[str] = None,
    state: Optional[str] = None,
    why_relevant: Optional[str] = None,
    risk_or_limit: Optional[str] = None,
    confidence: Optional[str] = "high",
    collected_at: Optional[datetime] = None,
) -> ResearchSource:
    extra = {
        "github": {
            "kind": "issue",
            "number": issue_number,
            "repository": repository,
            "state": state,
        }
    }
    return ResearchSource(
        source_type=SourceType.GITHUB_ISSUE,
        source_url=_clean_url(url) or None,
        title=title,
        summary=summary,
        collected_by_role=collected_by_role,
        why_relevant=why_relevant,
        risk_or_limit=risk_or_limit,
        confidence=confidence,
        collected_at=collected_at or _now(),
        extra=extra,
    )


def source_from_github_pr(
    *,
    url: str,
    title: str,
    collected_by_role: str,
    summary: Optional[str] = None,
    pr_number: Optional[int] = None,
    repository: Optional[str] = None,
    state: Optional[str] = None,
    why_relevant: Optional[str] = None,
    risk_or_limit: Optional[str] = None,
    confidence: Optional[str] = "high",
    collected_at: Optional[datetime] = None,
) -> ResearchSource:
    extra = {
        "github": {
            "kind": "pull_request",
            "number": pr_number,
            "repository": repository,
            "state": state,
        }
    }
    return ResearchSource(
        source_type=SourceType.GITHUB_PR,
        source_url=_clean_url(url) or None,
        title=title,
        summary=summary,
        collected_by_role=collected_by_role,
        why_relevant=why_relevant,
        risk_or_limit=risk_or_limit,
        confidence=confidence,
        collected_at=collected_at or _now(),
        extra=extra,
    )


def source_from_code_context(
    *,
    repo_path: str,
    summary: str,
    collected_by_role: str,
    title: Optional[str] = None,
    line_range: Optional[Tuple[int, int]] = None,
    why_relevant: Optional[str] = None,
    confidence: Optional[str] = "high",
    collected_at: Optional[datetime] = None,
) -> ResearchSource:
    """Build a CODE_CONTEXT source pointing at a path inside this repo."""

    extra: dict[str, Any] = {"repo_path": repo_path}
    if line_range is not None:
        extra["line_range"] = list(line_range)
    return ResearchSource(
        source_type=SourceType.CODE_CONTEXT,
        title=title or repo_path,
        summary=summary,
        collected_by_role=collected_by_role,
        why_relevant=why_relevant,
        confidence=confidence,
        collected_at=collected_at or _now(),
        extra=extra,
    )


def source_from_official_docs(
    *,
    url: str,
    title: str,
    collected_by_role: str,
    summary: Optional[str] = None,
    publisher: Optional[str] = None,
    why_relevant: Optional[str] = None,
    risk_or_limit: Optional[str] = None,
    confidence: Optional[str] = "high",
    collected_at: Optional[datetime] = None,
) -> ResearchSource:
    extra = {"publisher": publisher} if publisher else {}
    return ResearchSource(
        source_type=SourceType.OFFICIAL_DOCS,
        source_url=_clean_url(url) or None,
        title=title,
        summary=summary,
        collected_by_role=collected_by_role,
        why_relevant=why_relevant,
        risk_or_limit=risk_or_limit,
        confidence=confidence,
        collected_at=collected_at or _now(),
        extra=extra,
    )


def source_from_community_signal(
    *,
    url: str,
    title: str,
    collected_by_role: str,
    summary: Optional[str] = None,
    platform: Optional[str] = None,
    why_relevant: Optional[str] = None,
    risk_or_limit: Optional[str] = None,
    confidence: Optional[str] = "low",
    collected_at: Optional[datetime] = None,
) -> ResearchSource:
    """Build a COMMUNITY_SIGNAL source (Reddit, forum, discussion thread).

    Default ``confidence`` is ``low`` because community posts can be
    anecdotal — callers should bump it deliberately when verifying.
    """

    extra = {"platform": platform} if platform else {}
    return ResearchSource(
        source_type=SourceType.COMMUNITY_SIGNAL,
        source_url=_clean_url(url) or None,
        title=title,
        summary=summary,
        collected_by_role=collected_by_role,
        why_relevant=why_relevant,
        risk_or_limit=risk_or_limit,
        confidence=confidence,
        collected_at=collected_at or _now(),
        extra=extra,
    )


def source_from_design_reference(
    *,
    url: str,
    title: str,
    collected_by_role: str,
    summary: Optional[str] = None,
    platform: Optional[str] = None,
    why_relevant: Optional[str] = None,
    risk_or_limit: Optional[str] = None,
    confidence: Optional[str] = "medium",
    collected_at: Optional[datetime] = None,
) -> ResearchSource:
    """Build a DESIGN_REFERENCE source (Pinterest, Notefolio, Behance, etc.)."""

    extra = {"platform": platform} if platform else {}
    return ResearchSource(
        source_type=SourceType.DESIGN_REFERENCE,
        source_url=_clean_url(url) or None,
        title=title,
        summary=summary,
        collected_by_role=collected_by_role,
        why_relevant=why_relevant,
        risk_or_limit=risk_or_limit,
        confidence=confidence,
        collected_at=collected_at or _now(),
        extra=extra,
    )


def make_research_request(
    *,
    topic: str,
    role: str,
    session_id: Optional[str] = None,
    context: Optional[Mapping[str, Any]] = None,
    request_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> ResearchRequest:
    return ResearchRequest(
        request_id=request_id or _gen_id("req"),
        topic=topic,
        role=role,
        session_id=session_id,
        context=dict(context or {}),
        created_at=created_at or _now(),
    )


def make_finding(
    *,
    title: str,
    summary: str,
    role: str,
    supporting_source_ids: Sequence[str] = (),
    confidence: str = "medium",
    risk_or_limit: Optional[str] = None,
    finding_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> ResearchFinding:
    return ResearchFinding(
        finding_id=finding_id or _gen_id("find"),
        title=title,
        summary=summary,
        role=role,
        supporting_source_ids=tuple(supporting_source_ids),
        confidence=confidence,
        risk_or_limit=risk_or_limit,
        created_at=created_at or _now(),
    )


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
# Serialization
# ---------------------------------------------------------------------------


def pack_to_dict(pack: ResearchPack) -> dict:
    """Convert a pack to a JSON-serialisable dict (no datetimes raw, etc.)."""

    return {
        "title": pack.title,
        "summary": pack.summary,
        "primary_url": pack.primary_url,
        "tags": list(pack.tags),
        "created_at": _iso_or_none(pack.created_at),
        "extra": dict(pack.extra or {}),
        "request": _request_to_dict(pack.request) if pack.request else None,
        "sources": [_source_to_dict(s) for s in pack.sources],
        "findings": [_finding_to_dict(f) for f in pack.findings],
    }


def pack_from_dict(data: Mapping[str, Any]) -> ResearchPack:
    """Reverse :func:`pack_to_dict` — best-effort reconstruction.

    Used when a ResearchPack must survive a round-trip through
    ``WorkflowSession.extra`` (JSON-serialised) so member bots can pick
    up the same evidence that the gateway saw at collection time.

    Missing/unknown fields fall back to safe defaults rather than
    raising, so a pack persisted by an older version still loads.
    """

    if not isinstance(data, Mapping):
        return ResearchPack(title="(untitled)")

    request_data = data.get("request")
    request: Optional[ResearchRequest] = None
    if isinstance(request_data, Mapping):
        request = ResearchRequest(
            request_id=str(request_data.get("request_id") or _gen_id("req")),
            topic=str(request_data.get("topic") or ""),
            role=str(request_data.get("role") or ""),
            session_id=_optional_str(request_data.get("session_id")),
            context=dict(request_data.get("context") or {}),
            created_at=_parse_iso_datetime(request_data.get("created_at")),
        )

    sources: list[ResearchSource] = []
    for entry in data.get("sources") or ():
        if not isinstance(entry, Mapping):
            continue
        sources.append(_source_from_dict(entry))

    findings: list[ResearchFinding] = []
    for entry in data.get("findings") or ():
        if not isinstance(entry, Mapping):
            continue
        findings.append(
            ResearchFinding(
                finding_id=str(entry.get("finding_id") or _gen_id("find")),
                title=str(entry.get("title") or ""),
                summary=str(entry.get("summary") or ""),
                role=str(entry.get("role") or ""),
                supporting_source_ids=tuple(
                    str(sid) for sid in (entry.get("supporting_source_ids") or ())
                ),
                confidence=str(entry.get("confidence") or "medium"),
                risk_or_limit=_optional_str(entry.get("risk_or_limit")),
                created_at=_parse_iso_datetime(entry.get("created_at")),
            )
        )

    return ResearchPack(
        title=str(data.get("title") or "(untitled)"),
        summary=str(data.get("summary") or ""),
        primary_url=_optional_str(data.get("primary_url")),
        sources=tuple(sources),
        tags=tuple(str(t) for t in (data.get("tags") or ())),
        created_at=_parse_iso_datetime(data.get("created_at")),
        extra=dict(data.get("extra") or {}),
        request=request,
        findings=tuple(findings),
    )


def _source_from_dict(entry: Mapping[str, Any]) -> ResearchSource:
    raw_type = entry.get("source_type")
    try:
        source_type = (
            SourceType(raw_type) if isinstance(raw_type, str) else SourceType.UNKNOWN
        )
    except ValueError:
        source_type = SourceType.UNKNOWN

    attachments: list[ResearchAttachment] = []
    for att_entry in entry.get("attachments") or ():
        if not isinstance(att_entry, Mapping):
            continue
        attachments.append(
            ResearchAttachment(
                kind=str(att_entry.get("kind") or "file"),
                url=str(att_entry.get("url") or ""),
                filename=_optional_str(att_entry.get("filename")),
                content_type=_optional_str(att_entry.get("content_type")),
                size_bytes=_coerce_int(att_entry.get("size_bytes")),
                description=_optional_str(att_entry.get("description")),
                attachment_id=_optional_str(att_entry.get("attachment_id")),
            )
        )

    return ResearchSource(
        source_url=_optional_str(entry.get("url") or entry.get("source_url")),
        title=_optional_str(entry.get("title")),
        summary=_optional_str(entry.get("summary")),
        author_role=_optional_str(entry.get("author_role")),
        channel_id=_coerce_int(entry.get("channel_id")),
        thread_id=_coerce_int(entry.get("thread_id")),
        message_id=_coerce_int(entry.get("message_id")),
        posted_at=_parse_iso_datetime(entry.get("posted_at")),
        attachments=tuple(attachments),
        extra=dict(entry.get("extra") or {}),
        source_type=source_type,
        collected_by_role=_optional_str(entry.get("collected_by_role")),
        why_relevant=_optional_str(entry.get("why_relevant")),
        risk_or_limit=_optional_str(entry.get("risk_or_limit")),
        collected_at=_parse_iso_datetime(entry.get("collected_at")),
        confidence=_optional_str(entry.get("confidence")),
        attachment_id=_optional_str(entry.get("attachment_id")),
        source_id=_optional_str(entry.get("source_id")),
    )


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def pack_to_markdown(pack: ResearchPack) -> str:
    """Render a pack to a human-friendly Markdown blob.

    Sources are grouped by :class:`SourceType`, findings get their own
    section, and the request (if present) is summarised first. Designed
    to be diffable: stable ordering by source order within groups,
    canonical heading order matching :class:`SourceType` enum order.
    """

    blocks: list[str] = [f"# {pack.title or '(untitled)'}"]
    if pack.summary:
        blocks.append(f"> {pack.summary.strip()}")

    if pack.request is not None:
        blocks.append(_render_request_block(pack.request))

    if pack.tags:
        blocks.append("**태그:** " + " ".join(f"`{t}`" for t in pack.tags))

    grouped = pack.sources_by_type()
    for source_type in SourceType:
        bucket = grouped.get(source_type)
        if not bucket:
            continue
        heading = f"## 출처 — {source_type.value} ({len(bucket)})"
        body = "\n".join(_render_source_markdown(s) for s in bucket)
        blocks.append(f"{heading}\n{body}")

    if pack.findings:
        blocks.append(_render_findings_block(pack.findings))

    return "\n\n".join(b.strip() for b in blocks if b.strip()) + "\n"


def _request_to_dict(req: ResearchRequest) -> dict:
    return {
        "request_id": req.request_id,
        "topic": req.topic,
        "role": req.role,
        "session_id": req.session_id,
        "context": dict(req.context or {}),
        "created_at": _iso_or_none(req.created_at),
    }


def _source_to_dict(source: ResearchSource) -> dict:
    source_type = (
        source.source_type.value
        if isinstance(source.source_type, SourceType)
        else str(source.source_type)
    )
    return {
        "source_id": source.stable_id,
        "source_type": source_type,
        "title": source.title,
        "url": source.source_url,
        "attachment_id": source.attachment_id,
        "summary": source.summary,
        "collected_by_role": source.role,
        "why_relevant": source.why_relevant,
        "risk_or_limit": source.risk_or_limit,
        "confidence": source.confidence,
        "collected_at": _iso_or_none(source.timestamp),
        "channel_id": source.channel_id,
        "thread_id": source.thread_id,
        "message_id": source.message_id,
        "attachments": [_attachment_to_dict(a) for a in source.attachments],
        "extra": dict(source.extra or {}),
    }


def _finding_to_dict(finding: ResearchFinding) -> dict:
    return {
        "finding_id": finding.finding_id,
        "title": finding.title,
        "summary": finding.summary,
        "role": finding.role,
        "supporting_source_ids": list(finding.supporting_source_ids),
        "confidence": finding.confidence,
        "risk_or_limit": finding.risk_or_limit,
        "created_at": _iso_or_none(finding.created_at),
    }


def _attachment_to_dict(att: ResearchAttachment) -> dict:
    return {
        "kind": att.kind,
        "url": att.url,
        "filename": att.filename,
        "content_type": att.content_type,
        "size_bytes": att.size_bytes,
        "description": att.description,
        "attachment_id": att.attachment_id,
    }


def _render_source_markdown(source: ResearchSource) -> str:
    bits: list[str] = []
    title = source.title or "(no title)"
    bits.append(f"- **{title}**")
    locator = source.source_url or source.attachment_id
    if locator:
        bits.append(f"\n  - locator: `{locator}`")
    role = source.role
    if role:
        bits.append(f"\n  - role: `{role}`")
    if source.confidence:
        bits.append(f"\n  - confidence: {source.confidence}")
    if source.summary:
        bits.append(f"\n  - 요약: {source.summary.strip()}")
    if source.why_relevant:
        bits.append(f"\n  - 관련성: {source.why_relevant.strip()}")
    if source.risk_or_limit:
        bits.append(f"\n  - 한계/리스크: {source.risk_or_limit.strip()}")
    timestamp = source.timestamp
    if timestamp is not None:
        bits.append(f"\n  - collected_at: {timestamp.isoformat()}")
    if source.attachments:
        for att in source.attachments:
            bits.append(_render_attachment_markdown(att))
    return "".join(bits)


def _render_attachment_markdown(att: ResearchAttachment) -> str:
    parts = [f"\n  - 첨부 `{att.kind}`"]
    if att.filename:
        parts.append(f" {att.filename}")
    parts.append(f" <{att.url}>")
    if att.content_type:
        parts.append(f" ({att.content_type})")
    if att.description:
        parts.append(f" — {att.description}")
    return "".join(parts)


def _render_request_block(req: ResearchRequest) -> str:
    lines = ["## 요청"]
    lines.append(f"- request_id: `{req.request_id}`")
    lines.append(f"- topic: {req.topic}")
    lines.append(f"- role: `{req.role}`")
    if req.session_id:
        lines.append(f"- session_id: `{req.session_id}`")
    if req.created_at is not None:
        lines.append(f"- created_at: {req.created_at.isoformat()}")
    if req.context:
        lines.append("- context: " + ", ".join(f"{k}={v}" for k, v in req.context.items()))
    return "\n".join(lines)


def _render_findings_block(findings: Sequence[ResearchFinding]) -> str:
    lines = ["## 발견 사항"]
    for finding in findings:
        lines.append(f"- **{finding.title}** (`{finding.role}`, {finding.confidence})")
        lines.append(f"  - 요약: {finding.summary}")
        if finding.risk_or_limit:
            lines.append(f"  - 한계: {finding.risk_or_limit}")
        if finding.supporting_source_ids:
            ids = ", ".join(f"`{sid}`" for sid in finding.supporting_source_ids)
            lines.append(f"  - 근거 source ids: {ids}")
    return "\n".join(lines)


def _iso_or_none(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat()


def _excerpt(text: str, max_len: int) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    head = body.splitlines()[0].strip()
    if len(head) > max_len:
        head = head[: max_len - 3] + "..."
    return head
