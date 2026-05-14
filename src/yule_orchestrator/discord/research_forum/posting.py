"""research_forum — Discord async create/post layer + outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Iterable, Mapping, Optional, Sequence, Tuple

from ...agents.research.pack import ResearchAttachment, ResearchPack, ResearchSource

from .formatters import (
    DISCORD_MESSAGE_REPLY_LIMIT,
    FORUM_STARTER_CONTENT_LIMIT,
    FORUM_STARTER_CONTINUATION_NOTICE,
    FORUM_STARTER_OVERFLOW_NOTICE,
    derive_research_topic,
    format_agent_comment,
    format_research_post_body,
    format_thread_markdown_fallback,
    normalize_thread_title,
    split_forum_starter_and_replies,
    truncate_for_starter_message,
)
from .prefixes import COMMENT_PREFIXES, THREAD_TITLE_PREFIXES


@dataclass(frozen=True)
class ForumPostOutcome:
    posted: bool
    thread_id: Optional[int] = None
    thread_url: Optional[str] = None
    error: Optional[str] = None
    title: Optional[str] = None
    # Original full body — preserved for Obsidian export, fallback
    # rendering, and persistence so no information is lost.
    body: Optional[str] = None
    # Body that was actually sent to Discord as the starter message
    # (capped at FORUM_STARTER_CONTENT_LIMIT). Equals ``body`` when the
    # body fit, otherwise a truncated version with the continuation notice.
    starter_body: Optional[str] = None
    # Continuation reply chunks that were posted to the thread after
    # creation. Each chunk is ≤ DISCORD_MESSAGE_REPLY_LIMIT chars.
    continuation_chunks: Tuple[str, ...] = field(default_factory=tuple)
    # Errors from posting individual continuation chunks. Thread
    # creation success is not undone by chunk failures — the chunk
    # error string is just recorded here for diagnostics.
    continuation_errors: Tuple[str, ...] = field(default_factory=tuple)
    # Whether a "⚠️ 상세 댓글 일부 실패" notice was successfully posted
    # to the thread when continuation_errors is non-empty. ``False``
    # means either no errors occurred, the notice itself failed to
    # post, or post_message_fn was not provided. The original errors
    # are preserved on ``continuation_errors`` either way.
    continuation_notice_posted: bool = False
    fallback_markdown: Optional[str] = None


@dataclass(frozen=True)
class ForumCommentOutcome:
    posted: bool
    message_id: Optional[int] = None
    error: Optional[str] = None
    body: Optional[str] = None


CreateThreadFn = Any  # Callable[[*, channel_id, name, content], Awaitable]
PostMessageFn = Any   # Callable[[*, thread_id, content], Awaitable]

async def create_research_post(
    pack: ResearchPack,
    *,
    forum_context: ResearchForumContext,
    create_thread_fn: CreateThreadFn,
    posted_by: Optional[str] = None,
    prefix: Optional[str] = None,
    collection_outcome: Optional[Any] = None,
    collection_role: Optional[str] = None,
    collection_next_steps: Sequence[str] = (),
    post_message_fn: Optional[PostMessageFn] = None,
) -> ForumPostOutcome:
    """Compose title+body, hand them to *create_thread_fn*, return outcome.

    *create_thread_fn* is injected so production can wrap discord.py and
    tests can stub it. It is awaited with kwargs ``channel_id``, ``name``,
    ``content``, and is expected to return an object with ``id``/``url``
    or a Mapping-shaped result.

    When *collection_outcome* is provided, the thread body includes the
    autonomous collection summary block (수집 주제 / 출처 / 활용 가능성 /
    한계 / 다음 토의 단계) at the top. *collection_role* defaults to the
    pack's request role when present.
    """

    short_topic = derive_research_topic(pack)
    title = normalize_thread_title(short_topic, prefix=prefix)
    body = format_research_post_body(
        pack,
        posted_by=posted_by,
        collection_outcome=collection_outcome,
        collection_role=collection_role,
        collection_next_steps=collection_next_steps,
    )
    starter_body, reply_chunks = split_forum_starter_and_replies(body)

    if not forum_context.configured:
        reason = "forum channel not configured"
        return ForumPostOutcome(
            posted=False,
            error=reason,
            title=title,
            body=body,
            starter_body=starter_body,
            continuation_chunks=reply_chunks,
            fallback_markdown=format_thread_markdown_fallback(
                pack,
                title=title,
                posted_by=posted_by,
                reason=reason,
            ),
        )

    try:
        result = await _maybe_await(
            create_thread_fn(
                channel_id=forum_context.channel_id,
                channel_name=forum_context.channel_name,
                name=title,
                content=starter_body,
            )
        )
    except Exception as exc:  # noqa: BLE001 - surface to caller, do not crash
        return ForumPostOutcome(
            posted=False,
            error=str(exc),
            title=title,
            body=body,
            starter_body=starter_body,
            continuation_chunks=reply_chunks,
            fallback_markdown=format_thread_markdown_fallback(
                pack,
                title=title,
                posted_by=posted_by,
                reason=str(exc),
            ),
        )

    thread_id = _extract_thread_id(result)
    thread_url = _extract_thread_url(result)

    continuation_errors: Tuple[str, ...] = ()
    notice_posted = False
    if reply_chunks and post_message_fn is not None and thread_id is not None:
        continuation_errors = await _post_continuation_chunks(
            post_message_fn=post_message_fn,
            thread_id=thread_id,
            chunks=reply_chunks,
        )
        if continuation_errors:
            notice_posted = await _post_continuation_failure_notice(
                post_message_fn=post_message_fn,
                thread_id=thread_id,
                total_chunks=len(reply_chunks),
                failed_chunks=len(continuation_errors),
            )

    return ForumPostOutcome(
        posted=True,
        thread_id=thread_id,
        thread_url=thread_url,
        title=title,
        body=body,
        starter_body=starter_body,
        continuation_chunks=reply_chunks,
        continuation_errors=continuation_errors,
        continuation_notice_posted=notice_posted,
    )

def chunk_for_discord_message(
    text: str,
    *,
    limit: int = DISCORD_MESSAGE_REPLY_LIMIT,
) -> Tuple[str, ...]:
    """Return *text* as a tuple of ≤ ``limit`` char Discord chunks.

    Single source of truth for Discord-bound content sizing. Delegates
    to :func:`split_discord_message` so long single lines get hard-
    sliced and the chunker never emits a chunk over the cap. Empty or
    None input returns an empty tuple so callers can short-circuit.

    Production path: every ``post_message_fn(thread_id, content=...)``
    and ``channel.send(content)`` call should run their content through
    this helper (or the wrappers in this module) so Discord never sees
    > 1900 chars.
    """

    if not text:
        return ()
    from ..formatter import split_discord_message

    return tuple(split_discord_message(text, limit=limit))

async def _post_continuation_chunks(
    *,
    post_message_fn: "PostMessageFn",
    thread_id: int,
    chunks: Sequence[str],
) -> Tuple[str, ...]:
    """Post each *chunks* entry into *thread_id* and gather any errors.

    Per-chunk failures are recorded but do not abort the loop — the
    thread itself was already created, so the operator should still see
    whichever chunks succeeded. Returned tuple is empty when every chunk
    posted cleanly.

    Defensive sizing: each *chunks* entry is re-run through the Discord
    chunker. ``split_forum_starter_and_replies`` already targets the
    reply cap, but a caller passing pre-built chunks at a different
    boundary should still end up posting ≤ DISCORD_MESSAGE_REPLY_LIMIT.
    """

    errors: list[str] = []
    safe_pieces: list[str] = []
    for chunk in chunks:
        for piece in chunk_for_discord_message(chunk):
            safe_pieces.append(piece)
    total = len(safe_pieces)
    for index, chunk in enumerate(safe_pieces, start=1):
        try:
            await _maybe_await(
                post_message_fn(thread_id=thread_id, content=chunk)
            )
        except Exception as exc:  # noqa: BLE001 - record per-chunk
            errors.append(f"chunk {index}/{total}: {exc}")
    return tuple(errors)

async def _post_continuation_failure_notice(
    *,
    post_message_fn: "PostMessageFn",
    thread_id: int,
    total_chunks: int,
    failed_chunks: int,
) -> bool:
    """Post a short "댓글 일부 실패" notice into the same forum thread.

    Returns True when the notice posted cleanly, False when the notice
    itself raised. The thread starter is already in Discord, so a notice
    failure must never crash :func:`create_research_post` — we just leave
    the operator-visible record on ``ForumPostOutcome.continuation_errors``.
    """

    notice = (
        f"⚠️ 상세 자료 댓글 {total_chunks}건 중 {failed_chunks}건 게시에 "
        "실패했습니다. 원본 자료는 session/Obsidian export에 보존되어 있어요."
    )
    try:
        await _maybe_await(
            post_message_fn(thread_id=thread_id, content=notice)
        )
    except Exception:  # noqa: BLE001 - notice itself failing must not propagate
        return False
    return True

async def post_agent_comment(
    *,
    thread_id: int,
    role: str,
    collected_materials: Iterable[str] = (),
    interpretation: str = "",
    risks: str = "",
    next_actions: Iterable[str] = (),
    confidence: str = "medium",
    confidence_reason: str = "",
    post_message_fn: PostMessageFn,
) -> ForumCommentOutcome:
    """Format the role review comment and post it via *post_message_fn*."""

    body = format_agent_comment(
        role=role,
        collected_materials=collected_materials,
        interpretation=interpretation,
        risks=risks,
        next_actions=next_actions,
        confidence=confidence,
        confidence_reason=confidence_reason,
    )
    # Long-context evidence + multi-paragraph perspective can push role
    # comments well past Discord's 2000-char ``content`` limit. Chunk
    # before posting and return the first message's id (the post counts
    # as posted as soon as any chunk lands).
    pieces = chunk_for_discord_message(body) or (body,)
    first_message_id: Optional[int] = None
    for index, piece in enumerate(pieces, start=1):
        try:
            result = await _maybe_await(
                post_message_fn(thread_id=thread_id, content=piece)
            )
        except Exception as exc:  # noqa: BLE001
            return ForumCommentOutcome(posted=False, error=str(exc), body=body)
        if first_message_id is None:
            first_message_id = _extract_message_id(result)
    return ForumCommentOutcome(posted=True, message_id=first_message_id, body=body)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value

def _extract_thread_id(result: Any) -> Optional[int]:
    if result is None:
        return None
    if isinstance(result, Mapping):
        for key in ("id", "thread_id"):
            value = result.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None
    for attr in ("id", "thread_id"):
        value = getattr(result, attr, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None

def _extract_thread_url(result: Any) -> Optional[str]:
    if result is None:
        return None
    if isinstance(result, Mapping):
        value = result.get("url") or result.get("jump_url")
    else:
        value = getattr(result, "jump_url", None) or getattr(result, "url", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None

def _extract_message_id(result: Any) -> Optional[int]:
    return _extract_thread_id(result)


__all__ = (
    "ForumPostOutcome",
    "ForumCommentOutcome",
    "create_research_post",
    "chunk_for_discord_message",
    "_post_continuation_chunks",
    "_post_continuation_failure_notice",
    "post_agent_comment",
    "_maybe_await",
    "_extract_thread_id",
    "_extract_thread_url",
    "_extract_message_id",
)
