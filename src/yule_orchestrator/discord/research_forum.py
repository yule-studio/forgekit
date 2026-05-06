"""Adapter layer for the agent research Forum (`#운영-리서치`).

The hard work — actually creating threads and posting messages via
discord.py — is intentionally a *small* surface here (`create_research_post`
and `post_agent_comment`). Everything else (env config, body and comment
formatting, prefix detection) is **pure functions** so unit tests can
exercise them without spinning up Discord.

Operating rules: ``policies/runtime/agents/engineering-agent/research-forum.md``.
The forum is shared across departments; the env keys are
``DISCORD_AGENT_RESEARCH_FORUM_*`` (not ``DISCORD_ENGINEERING_*``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Awaitable, Iterable, Mapping, Optional, Sequence

from ..agents.research_pack import ResearchAttachment, ResearchPack, ResearchSource


# ---------------------------------------------------------------------------
# Env config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchForumContext:
    """Resolved Forum channel target.

    Either ``channel_id`` or ``channel_name`` is enough to route. When both
    are missing, ``configured`` is False and forum publishing is disabled.
    """

    channel_id: Optional[int] = None
    channel_name: Optional[str] = None

    @property
    def configured(self) -> bool:
        return self.channel_id is not None or bool((self.channel_name or "").strip())

    @classmethod
    def from_env(cls) -> "ResearchForumContext":
        return cls(
            channel_id=_optional_int_env("DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_ID"),
            channel_name=_optional_string_env(
                "DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_NAME"
            ),
        )


# ---------------------------------------------------------------------------
# Prefix vocabulary (research-forum.md §3)
# ---------------------------------------------------------------------------


PREFIX_RESEARCH = "[Research]"
PREFIX_TOOL = "[Tool]"
PREFIX_REFERENCE = "[Reference]"
PREFIX_DECISION = "[Decision]"
PREFIX_OBSIDIAN = "[Obsidian]"

THREAD_TITLE_PREFIXES = (PREFIX_RESEARCH, PREFIX_TOOL, PREFIX_REFERENCE)
COMMENT_PREFIXES = (PREFIX_DECISION, PREFIX_OBSIDIAN)
ALL_PREFIXES = THREAD_TITLE_PREFIXES + COMMENT_PREFIXES


# ---------------------------------------------------------------------------
# Title / body / comment formatters (pure)
# ---------------------------------------------------------------------------


DISCORD_THREAD_TITLE_LIMIT = 100
TOPIC_BUDGET = 60  # leaves room for the prefix + safety margin

# Discord forum starter message content cap. The API hard-rejects bodies
# > 4000 chars with ``error code 50035 — Must be 4000 or fewer in length``.
# We post starters at the lower :data:`FORUM_STARTER_CONTENT_LIMIT` so a
# single unicode-width surprise (e.g. emoji widening, paragraph the
# formatter glued in) doesn't push us over.
DISCORD_MESSAGE_CONTENT_LIMIT = 4000
FORUM_STARTER_CONTENT_LIMIT = 3900
FORUM_STARTER_OVERFLOW_NOTICE = (
    "_본문이 길어 일부를 생략했습니다. 상세 자료는 후속 댓글 또는 Obsidian export를 확인하세요._"
)


def derive_research_topic(pack: "ResearchPack") -> str:
    """Pick a short, semantic topic string for the forum thread title.

    Resolution order (each step short-circuits when it produces a
    non-empty value within :data:`TOPIC_BUDGET`):
      1. ``pack.title`` if it looks intentional (already short, no full
         sentence punctuation).
      2. First sentence of ``pack.summary`` trimmed to a topic phrase.
      3. ``pack.tags`` joined with ``·`` if any.
      4. ``pack.request.question`` if the pack carries an autonomous
         request.
      5. ``pack.title`` truncated as a last resort.
      6. Literal ``"engineering 작업"`` so the title is never empty.

    The function does *not* prepend ``[Research]`` — that is
    :func:`normalize_thread_title`'s job, which also enforces the 100
    char limit on the combined string.
    """

    candidates: list[str] = []
    title = (getattr(pack, "title", "") or "").strip()
    if title:
        candidates.append(title)

    summary = (getattr(pack, "summary", "") or "").strip()
    if summary:
        first = _first_sentence(summary)
        if first and first != title:
            candidates.append(first)

    tags = tuple(getattr(pack, "tags", ()) or ())
    if tags:
        candidates.append(" · ".join(str(t) for t in tags if str(t).strip()))

    request = getattr(pack, "request", None)
    if request is not None:
        question = (getattr(request, "question", "") or "").strip()
        if question:
            candidates.append(_first_sentence(question))

    for candidate in candidates:
        compact = _compact_topic(candidate)
        if compact and len(compact) <= TOPIC_BUDGET:
            return compact

    # Fall back to a hard-trimmed version of the first non-empty
    # candidate — title preferred. Final fallback keeps a sensible
    # anchor so create_thread_fn never sees blank.
    for candidate in candidates:
        if candidate:
            return _compact_topic(candidate)[:TOPIC_BUDGET]
    return "engineering 작업"


def _first_sentence(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    for sep in (". ", "! ", "? ", "\n"):
        idx = cleaned.find(sep)
        if 0 < idx < TOPIC_BUDGET * 2:
            cleaned = cleaned[:idx]
            break
    return cleaned.strip().rstrip("." )


def _compact_topic(text: str) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned.strip()


def _extract_original_request(pack: "ResearchPack") -> str:
    """Return the user-facing original request text for the body block.

    Looks at ``pack.request.question`` first (autonomous collector
    populates this from the conversation prompt), then ``pack.summary``
    only if it visibly differs from the title — title alone is not
    informative as "원문 요청". Returns ``""`` when nothing usable
    exists so the caller can omit the section entirely.
    """

    request = getattr(pack, "request", None)
    if request is not None:
        # ResearchRequest may carry the original question under ``topic``
        # (current schema) or ``question`` (older drafts). Read both
        # defensively so callers from either era surface the prompt.
        for attr in ("question", "topic"):
            value = (getattr(request, attr, "") or "").strip()
            if value:
                return value
    summary = (getattr(pack, "summary", "") or "").strip()
    title = (getattr(pack, "title", "") or "").strip()
    if summary and summary != title and len(summary) > len(title):
        return summary
    return ""


def normalize_thread_title(
    title: str,
    *,
    prefix: Optional[str] = None,
    max_chars: int = DISCORD_THREAD_TITLE_LIMIT,
) -> str:
    """Return a thread title that fits Discord's 100-char limit.

    Discord forum thread names must be 1..100 characters. If *title*
    already starts with a known thread prefix, the prefix is preserved.
    Otherwise *prefix* (or ``[Research]``) is prepended. The combined
    string is then trimmed to ``max_chars``: word-boundary first, with a
    trailing ellipsis-style ``…`` so the cut is visible. Empty input
    falls back to ``"(untitled)"`` so create_thread_fn never sees an
    empty name.
    """

    cleaned = (title or "").strip()
    if not cleaned:
        cleaned = "(untitled)"

    matched_prefix: Optional[str] = None
    for known in ALL_PREFIXES:
        if cleaned.startswith(known):
            matched_prefix = known
            cleaned = cleaned[len(known):].strip() or "(untitled)"
            break

    chosen_prefix = matched_prefix
    if chosen_prefix is None:
        chosen_prefix = prefix if prefix in THREAD_TITLE_PREFIXES else PREFIX_RESEARCH

    full = f"{chosen_prefix} {cleaned}".strip()
    if len(full) <= max_chars:
        return full
    return _safe_truncate(full, max_chars=max_chars)


def truncate_for_starter_message(
    body: str,
    *,
    limit: int = FORUM_STARTER_CONTENT_LIMIT,
    notice: str = FORUM_STARTER_OVERFLOW_NOTICE,
) -> str:
    """Trim *body* so it fits Discord's forum starter message limit.

    The starter content cap is 4000 chars; we target a slightly lower
    ``limit`` (default :data:`FORUM_STARTER_CONTENT_LIMIT`) so unicode
    surprises don't push the API over. When the body fits as-is we
    return it unchanged so short threads stay readable.

    Truncation prefers paragraph then line boundaries, falling back to
    a hard slice. The trailing ``notice`` is always appended when we cut
    so operators see why the thread looks short and where to find the
    full record (Obsidian export, follow-up comments).

    The Obsidian/ResearchPack/persistence layers receive the *original*
    body — only the starter content shown in Discord is shortened.
    """

    if not body:
        return body
    if len(body) <= limit:
        return body

    notice_block = ("\n\n" + notice) if notice else ""
    budget = limit - len(notice_block)
    if budget <= 0:
        # Pathological tiny limit — fall back to a hard slice of the
        # notice itself so the caller still gets a string under ``limit``.
        return notice[:limit] if notice else body[:limit]

    head = body[:budget]
    # Prefer the last paragraph break inside the budget so we don't
    # split sections like ``**요약**\n...`` mid-paragraph.
    pivot = head.rfind("\n\n")
    if pivot >= int(budget * 0.5):
        head = head[:pivot]
    else:
        line_pivot = head.rfind("\n")
        if line_pivot >= int(budget * 0.5):
            head = head[:line_pivot]
    return head.rstrip() + notice_block


def _safe_truncate(text: str, *, max_chars: int) -> str:
    """Trim *text* to ``max_chars`` at a word boundary when possible.

    Reserves one character for the trailing ``…`` marker so the result
    is exactly ``max_chars`` after the marker is appended. Falls back to
    a hard slice when there's no whitespace inside the budget.
    """

    if max_chars <= 1:
        return text[:max_chars]
    budget = max_chars - 1
    head = text[:budget]
    pivot = head.rfind(" ")
    # Only break on whitespace when it leaves at least 60% of the budget
    # — otherwise hard-slice keeps more signal.
    if pivot >= int(budget * 0.6):
        head = head[:pivot]
    return head.rstrip() + "…"


def format_research_post_body(
    pack: ResearchPack,
    *,
    posted_by: Optional[str] = None,
    collection_outcome: Optional[Any] = None,
    collection_role: Optional[str] = None,
    collection_next_steps: Sequence[str] = (),
) -> str:
    """Render a ResearchPack as the body of a forum thread.

    When *collection_outcome* is provided (the result of
    ``research_collector.auto_collect_or_request_more_input``), a
    "1차 자료 수집 — <role>" block is appended at the top so the forum
    thread surfaces the autonomous collection in the same body. The
    block carries 수집 주제 / 출처 / 활용 가능성 / 한계 / 다음 토의 단계.
    Falls back gracefully (no block) when the import or call fails.
    """

    lines: list[str] = []
    if posted_by:
        lines.append(f"_posted by_ `{posted_by}`")
        lines.append("")

    original_request = _extract_original_request(pack)
    if original_request:
        lines.append("## 원문 요청")
        lines.append(original_request)
        lines.append("")

    collection_block = _render_collection_block(
        pack=pack,
        outcome=collection_outcome,
        role=collection_role,
        next_steps=collection_next_steps,
    )
    if collection_block:
        lines.append(collection_block)
        lines.append("")

    budget_block = _render_budget_block(collection_outcome)
    if budget_block:
        lines.append(budget_block)
        lines.append("")

    if pack.summary:
        lines.append("**요약**")
        lines.append(pack.summary.strip())
        lines.append("")
    if pack.urls:
        lines.append("**자료 링크**")
        for url in pack.urls:
            lines.append(f"- {url}")
        lines.append("")
    attachments = pack.attachments
    if attachments:
        lines.append("**첨부**")
        for att in attachments:
            lines.append(_format_attachment_line(att))
        lines.append("")
    if pack.tags:
        lines.append(f"**태그** {' '.join(f'`{t}`' for t in pack.tags)}")
        lines.append("")
    sources = list(pack.sources)
    if len(sources) > 1:
        lines.append(f"**출처 {len(sources)}건**")
        for source in sources:
            lines.append(_format_source_line(source))
    elif sources:
        # When there's exactly one source, we still include provenance for
        # Obsidian export later — but compactly.
        only = sources[0]
        provenance = _format_source_line(only)
        if provenance.strip("- ").strip():
            lines.append("**출처**")
            lines.append(provenance)
    return "\n".join(line for line in lines).strip()


def _render_budget_block(outcome: Optional[Any]) -> str:
    """Render the ``"### 수집 예산 / 종료 조건"`` block from a CollectionOutcome.

    Pulls budget tier, provider call usage vs cap, per-role results cap,
    role targets, stop reason, and under-covered roles directly from
    ``outcome``. Returns an empty string when the outcome is missing or
    doesn't carry the budget metadata (legacy/round-tripped outcomes that
    pre-date the budget policy still flow through the body cleanly).
    """

    if outcome is None:
        return ""
    tier = getattr(outcome, "budget_tier", None)
    if not tier:
        return ""

    iterations = int(getattr(outcome, "iterations", 0) or 0)
    max_calls = int(getattr(outcome, "max_provider_calls", 0) or 0)
    max_results_per_role = int(getattr(outcome, "max_results_per_role", 0) or 0)
    stop_reason = getattr(outcome, "stop_reason", None) or "unknown"
    under_covered = tuple(getattr(outcome, "under_covered_roles", ()) or ())
    role_targets = tuple(getattr(outcome, "role_targets", ()) or ())

    lines: list[str] = ["### 수집 예산 / 종료 조건", f"- tier: {tier}"]
    if max_calls:
        # Cap the displayed usage at max so partial outcomes (e.g. when
        # the loop bails early) read naturally as ``2/8`` rather than
        # ``2/2``.
        used = min(iterations, max_calls) if iterations else iterations
        lines.append(f"- provider calls: {used}/{max_calls}")
    elif iterations:
        lines.append(f"- provider calls: {iterations}")

    if max_results_per_role:
        lines.append(f"- max results per role: {max_results_per_role}")

    if role_targets:
        target_strs = []
        for role, min_sources in role_targets:
            if not role:
                continue
            try:
                target_strs.append(f"{role} {int(min_sources)}")
            except (TypeError, ValueError):
                continue
        if target_strs:
            lines.append("- role target: " + ", ".join(target_strs))

    lines.append(f"- stop reason: {stop_reason}")

    if under_covered:
        joined = ", ".join(str(r) for r in under_covered if r)
        if joined:
            lines.append(f"- 부족한 역할: {joined}")

    return "\n".join(lines)


def _render_collection_block(
    *,
    pack: ResearchPack,
    outcome: Optional[Any],
    role: Optional[str],
    next_steps: Sequence[str],
) -> str:
    """Wrap ``research_collector.format_collection_summary`` defensively.

    Returns an empty string if the outcome is missing, unstructured, or if
    the collector module itself can't be imported. The forum body should
    never crash because the collector hook is unavailable.
    """

    if outcome is None:
        return ""
    try:
        from ..agents.research_collector import format_collection_summary
    except Exception:  # noqa: BLE001
        return ""

    target_role = (
        role
        or getattr(getattr(outcome, "pack", None), "request", None)
        and getattr(outcome.pack.request, "role", None)
    )
    if not target_role and getattr(pack, "request", None) is not None:
        target_role = getattr(pack.request, "role", None)
    if not target_role:
        target_role = "engineering-agent/tech-lead"

    collector_name = getattr(outcome, "collector_name", "?")
    query = getattr(outcome, "query", "") or ""

    try:
        return format_collection_summary(
            pack,
            collector_name=collector_name,
            query=query,
            role=target_role,
            next_steps=next_steps,
        )
    except Exception:  # noqa: BLE001
        return ""


def format_agent_comment(
    *,
    role: str,
    collected_materials: Iterable[str] = (),
    interpretation: str = "",
    risks: str = "",
    next_actions: Iterable[str] = (),
    confidence: str = "medium",
    confidence_reason: str = "",
) -> str:
    """Render the standard role-review comment.

    Layout follows research-forum.md §4.1:
    ``역할 / 수집 자료 / 해석 / 리스크 / 다음 행동`` plus a trailing
    confidence line.  ``collected_materials`` and ``next_actions`` are
    rendered as numbered sub-lists; empty inputs degrade to a short
    fallback so a comment is never silent.
    """

    safe_role = role.strip() or "<unknown-role>"
    safe_conf = (confidence or "medium").strip().lower()
    if safe_conf not in {"high", "medium", "low"}:
        safe_conf = "medium"

    material_items = [m for m in (collected_materials or ()) if m and m.strip()]
    material_lines = (
        "\n".join(f"  {idx}. {item.strip()}" for idx, item in enumerate(material_items, start=1))
        if material_items
        else "  - 수집된 자료 없음 — 추가 조사 필요"
    )

    actions = [a for a in (next_actions or ()) if a and a.strip()]
    action_lines = (
        "\n".join(f"  {idx}. {action.strip()}" for idx, action in enumerate(actions, start=1))
        if actions
        else "  - 추가 행동 없음"
    )

    interpretation_text = interpretation.strip() or "(해석 미기재)"
    risk_text = risks.strip() or "특별한 리스크 없음"
    confidence_line = (
        f"신뢰도: {safe_conf}"
        + (f" — {confidence_reason.strip()}" if confidence_reason.strip() else "")
    )
    return (
        f"[role:{safe_role}]\n"
        f"- 역할: {safe_role}\n"
        f"- 수집 자료:\n"
        f"{material_lines}\n"
        f"- 해석: {interpretation_text}\n"
        f"- 리스크: {risk_text}\n"
        f"- 다음 행동:\n"
        f"{action_lines}\n"
        f"- {confidence_line}"
    )


def format_thread_markdown_fallback(
    pack: ResearchPack,
    *,
    title: Optional[str] = None,
    posted_by: Optional[str] = None,
    reason: Optional[str] = None,
    collection_outcome: Optional[Any] = None,
    collection_role: Optional[str] = None,
    collection_next_steps: Sequence[str] = (),
) -> str:
    """Markdown blob for posting to a regular text channel when the
    forum endpoint is unavailable (no token / 403 / unconfigured).

    The shape mirrors a forum thread: H2 title, an optional warning
    line explaining why we're falling back, and the same body the
    forum thread would have carried (including the autonomous
    collection block when *collection_outcome* is present). Callers
    can pipe this directly into ``channel.send`` (split if it exceeds
    2000 chars).
    """

    final_title = normalize_thread_title(title or pack.title)
    body = format_research_post_body(
        pack,
        posted_by=posted_by,
        collection_outcome=collection_outcome,
        collection_role=collection_role,
        collection_next_steps=collection_next_steps,
    )
    notice_bits = ["⚠️ 운영-리서치 forum 게시에 실패했습니다 — 일반 thread markdown fallback."]
    if reason and reason.strip():
        notice_bits.append(f"사유: {reason.strip()}")
    notice = " ".join(notice_bits)

    parts: list[str] = [f"## {final_title}", f"_{notice}_"]
    if body:
        parts.append(body)
    return "\n\n".join(parts).strip()


def detect_thread_prefix(title: str) -> Optional[str]:
    """Return the matching thread prefix, or None if title has none."""

    cleaned = (title or "").strip()
    for known in ALL_PREFIXES:
        if cleaned.startswith(known):
            return known
    return None


# ---------------------------------------------------------------------------
# Discord-touching helpers (small)
# ---------------------------------------------------------------------------


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
    # body fit, otherwise a truncated version with the overflow notice.
    starter_body: Optional[str] = None
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
    starter_body = truncate_for_starter_message(body)

    if not forum_context.configured:
        reason = "forum channel not configured"
        return ForumPostOutcome(
            posted=False,
            error=reason,
            title=title,
            body=body,
            starter_body=starter_body,
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
            fallback_markdown=format_thread_markdown_fallback(
                pack,
                title=title,
                posted_by=posted_by,
                reason=str(exc),
            ),
        )

    thread_id = _extract_thread_id(result)
    thread_url = _extract_thread_url(result)
    return ForumPostOutcome(
        posted=True,
        thread_id=thread_id,
        thread_url=thread_url,
        title=title,
        body=body,
        starter_body=starter_body,
    )


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
    try:
        result = await _maybe_await(
            post_message_fn(thread_id=thread_id, content=body)
        )
    except Exception as exc:  # noqa: BLE001
        return ForumCommentOutcome(posted=False, error=str(exc), body=body)
    message_id = _extract_message_id(result)
    return ForumCommentOutcome(posted=True, message_id=message_id, body=body)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_attachment_line(att: ResearchAttachment) -> str:
    parts = [f"`{att.kind}`"]
    if att.filename:
        parts.append(att.filename)
    parts.append(f"<{att.url}>")
    if att.description:
        parts.append(f"— {att.description}")
    return "- " + " ".join(parts)


def _format_source_line(source: ResearchSource) -> str:
    bits: list[str] = []
    if source.author_role:
        bits.append(f"`{source.author_role}`")
    if source.posted_at:
        bits.append(source.posted_at.isoformat())
    if source.source_url:
        bits.append(source.source_url)
    if not bits and (source.title or "").strip():
        bits.append(source.title.strip())
    if not bits:
        return "- (출처 미상)"
    return "- " + " · ".join(bits)


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


def _optional_int_env(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer value, got: {raw!r}") from exc


def _optional_string_env(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    text = raw.strip()
    return text or None
