"""ResearchPack rendering / serialization.

Split out of :mod:`pack.py` along the **builder vs renderer** responsibility
seam (책임 분리): :mod:`pack.py` BUILDS/assembles the neutral
:class:`~...pack.ResearchPack` data model, while this module RENDERS/formats
that model into transport shapes — JSON-serialisable dicts
(``pack_to_dict`` / ``pack_from_dict`` round-trip) and human-friendly
Markdown (``pack_to_markdown``).

Import direction is **one-way**: this module imports data-model classes and a
couple of id/clean helpers from :mod:`pack`; :mod:`pack` never imports back
from here, so there is no cycle. Pure move — no behavior changed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from .pack import (
    ResearchAttachment,
    ResearchFinding,
    ResearchPack,
    ResearchRequest,
    ResearchSource,
    SourceType,
    _gen_id,
)


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
