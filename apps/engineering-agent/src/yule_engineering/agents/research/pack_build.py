"""ResearchPack typed source/finding/request builders.

Split out of :mod:`pack.py` along the **builder vs renderer** responsibility
seam (책임 분리). This module holds the typed *constructor* helpers that BUILD
:class:`~...pack.ResearchSource` rows for each :class:`~...pack.SourceType`
(``source_from_user_message`` / ``source_from_url`` / ``source_from_github_*``
/ ``source_from_official_docs`` / ...) plus ``make_research_request`` and
``make_finding``.

Import direction is **one-way**: this module imports the data-model classes
and a few shared helpers (``_now`` / ``_gen_id`` / ``_clean_url`` /
``classify_attachment`` / ``normalize_attachment_metadata`` / ``_excerpt``)
from :mod:`pack`; :mod:`pack` never imports back from here, so there is no
cycle. Pure move — no behavior changed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence, Tuple

from .pack import (
    ResearchAttachment,
    ResearchFinding,
    ResearchRequest,
    ResearchSource,
    SourceType,
    _clean_url,
    _excerpt,
    _gen_id,
    _now,
    classify_attachment,
    normalize_attachment_metadata,
)


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
