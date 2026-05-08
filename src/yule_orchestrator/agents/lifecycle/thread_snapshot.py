"""Thread snapshot + link extraction — A-M7.6.

Hydrates the knowledge-note renderer with what actually happened
in the operations-research forum thread. Without this the saved
note is just the original prompt + a synthesis sentence; with it
the note carries the operator's reasoning trail (links collected,
each role's take, tech-lead consensus).

Data contract — the producer (forum_obsidian_handoff) collects a
:class:`ThreadSnapshot` and stores it in ``ApprovalRequest.extra``;
the approval reply router preserves it on
``ObsidianWriteRequest.metadata``; the renderer reads it back to
compose the note body.

Pure-Python — no Discord client. Production wires
``thread_history_fetcher`` to ``message.channel.history`` so the
collector has bounded recent messages; tests pass an explicit list.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


_URL_RE = re.compile(
    r"https?://[^\s<>\"'`]+",
    re.IGNORECASE,
)


# Snapshot caps — Discord message is 2000 chars, so a 50-message
# thread snapshot can already overflow. Stay conservative so the
# saved note + the approval card both fit.
DEFAULT_MAX_MESSAGES: int = 25
DEFAULT_MAX_CHARS_PER_MESSAGE: int = 600
DEFAULT_MAX_LINKS: int = 30


@dataclass(frozen=True)
class ThreadMessage:
    """One forum-thread message normalised for the snapshot.

    ``role`` carries the engineering role id when the author is a
    member bot (resolved at collect-time via env / member-bot
    inventory). When it's the user or an unknown bot, ``role`` is
    ``None`` and ``author`` carries the human label.
    """

    author: str
    content: str
    role: Optional[str] = None
    posted_at: Optional[str] = None

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "author": self.author,
            "content": self.content,
            "role": self.role,
            "posted_at": self.posted_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ThreadMessage":
        return cls(
            author=str(payload.get("author") or "unknown"),
            content=str(payload.get("content") or ""),
            role=_optional_str(payload.get("role")),
            posted_at=_optional_str(payload.get("posted_at")),
        )


@dataclass(frozen=True)
class ThreadSnapshot:
    """Bounded view of a forum thread for vault hydration.

    ``messages`` keeps the producer's chronological order; the
    renderer can section by role via :meth:`role_summaries` or
    quote inline.
    """

    messages: Sequence[ThreadMessage] = ()
    extracted_links: Sequence[str] = ()
    role_summaries: Mapping[str, str] = field(default_factory=dict)
    captured_at: Optional[str] = None

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "messages": [m.to_payload() for m in self.messages],
            "extracted_links": list(self.extracted_links),
            "role_summaries": dict(self.role_summaries),
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_payload(
        cls, payload: Optional[Mapping[str, Any]]
    ) -> "ThreadSnapshot":
        if not isinstance(payload, Mapping):
            return cls()
        msgs_raw = payload.get("messages")
        messages: list[ThreadMessage] = []
        if isinstance(msgs_raw, list):
            for item in msgs_raw:
                if isinstance(item, Mapping):
                    messages.append(ThreadMessage.from_payload(item))
        links_raw = payload.get("extracted_links")
        links = (
            [str(u) for u in links_raw if isinstance(u, str)]
            if isinstance(links_raw, list)
            else []
        )
        roles_raw = payload.get("role_summaries")
        role_summaries = (
            {str(k): str(v) for k, v in roles_raw.items() if v}
            if isinstance(roles_raw, Mapping)
            else {}
        )
        return cls(
            messages=tuple(messages),
            extracted_links=tuple(links),
            role_summaries=role_summaries,
            captured_at=_optional_str(payload.get("captured_at")),
        )

    @property
    def is_empty(self) -> bool:
        """True when the snapshot carries no operator-meaningful content.

        Used by the renderer's empty-note guard so a forum thread
        with only the kickoff marker (no human discussion, no role
        comments, no collected links) doesn't produce a hollow
        vault file.
        """

        return (
            not self.messages
            and not self.extracted_links
            and not any((v or "").strip() for v in self.role_summaries.values())
        )


def extract_links_from_text(
    text: Optional[str], *, max_links: int = DEFAULT_MAX_LINKS
) -> Tuple[str, ...]:
    """Return up to *max_links* unique URLs found in *text*.

    Order-preserving (first occurrence wins) so the saved note
    quotes links in the order they were posted in the thread.
    Strips trailing punctuation Discord links commonly carry.
    """

    if not text:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,);!?'\"")
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= max_links:
            break
    return tuple(out)


def collapse_thread_to_snapshot(
    raw_messages: Iterable[Any],
    *,
    role_resolver: Optional[Any] = None,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    max_chars_per_message: int = DEFAULT_MAX_CHARS_PER_MESSAGE,
    max_links: int = DEFAULT_MAX_LINKS,
    captured_at: Optional[str] = None,
) -> ThreadSnapshot:
    """Compress an iterable of Discord messages (or test stubs) into
    a :class:`ThreadSnapshot`.

    Each input must expose ``content`` (str) and ``author`` (object
    with ``name`` / ``global_name`` / ``bot``). Extra attributes
    used when present: ``id``, ``created_at``.

    *role_resolver* is an optional callable mapping a Discord
    author object → engineering role id (e.g. "tech-lead",
    "devops-engineer"). Production wires it via the bot
    inventory; tests pass a stub. ``None`` → role unresolved.

    The snapshot caps at *max_messages* most-recent messages.
    Each ``content`` is truncated at *max_chars_per_message* with
    a "(...)" tail so a long role take doesn't blow the body up.
    URLs are extracted across the full thread (pre-truncation) so
    the link bucket stays comprehensive.
    """

    captured: list[ThreadMessage] = []
    seen_links: set[str] = set()
    link_order: list[str] = []
    role_buckets: dict[str, list[str]] = {}

    for message in raw_messages or ():
        author = getattr(message, "author", None)
        is_bot = bool(getattr(author, "bot", False))
        author_name = (
            getattr(author, "global_name", None)
            or getattr(author, "name", None)
            or getattr(author, "display_name", None)
            or ("bot" if is_bot else "unknown")
        )
        content = str(getattr(message, "content", "") or "").strip()
        # Skip empty / whitespace-only messages — they add nothing
        # to the snapshot but eat the message-cap budget.
        if not content:
            continue

        # Resolve role label.
        role: Optional[str] = None
        if role_resolver is not None:
            try:
                role = role_resolver(author)
            except Exception:  # noqa: BLE001 - resolver bug must not crash
                role = None
            if role is not None:
                role = str(role).strip() or None

        # Extract links from the FULL content before truncation so
        # we don't lose URLs that fall in the tail.
        for url in extract_links_from_text(content, max_links=max_links):
            if url not in seen_links:
                seen_links.add(url)
                link_order.append(url)

        truncated = (
            content
            if len(content) <= max_chars_per_message
            else content[: max_chars_per_message - 5].rstrip() + " (…)"
        )
        if role and truncated:
            role_buckets.setdefault(role, []).append(truncated)

        posted_at_raw = getattr(message, "created_at", None)
        posted_at = (
            posted_at_raw.isoformat()
            if hasattr(posted_at_raw, "isoformat")
            else _optional_str(posted_at_raw)
        )
        captured.append(
            ThreadMessage(
                author=str(author_name),
                content=truncated,
                role=role,
                posted_at=posted_at,
            )
        )

    if max_messages > 0 and len(captured) > max_messages:
        # Keep most recent — Discord channel.history is reverse-chrono
        # by default but production callers may pass either order.
        captured = captured[-max_messages:]

    role_summaries = {
        role: " · ".join(snippets[:3])  # first 3 takes per role
        for role, snippets in role_buckets.items()
        if snippets
    }

    return ThreadSnapshot(
        messages=tuple(captured),
        extracted_links=tuple(link_order[: max_links if max_links > 0 else None]),
        role_summaries=role_summaries,
        captured_at=captured_at,
    )


def render_thread_snapshot_block(
    snapshot: ThreadSnapshot, *, max_messages: int = 10
) -> str:
    """Format the snapshot as markdown for the knowledge note body.

    Three sections — links / role-by-role summary / chronological
    excerpt. Empty sections are dropped so the saved note doesn't
    carry obvious "(없음)" placeholders.
    """

    parts: list[str] = []
    if snapshot.extracted_links:
        lines = ["### 수집 자료 링크"]
        for url in snapshot.extracted_links:
            lines.append(f"- {url}")
        parts.append("\n".join(lines))

    if snapshot.role_summaries:
        lines = ["### 역할별 검토 요약"]
        for role, summary in snapshot.role_summaries.items():
            if not summary:
                continue
            lines.append(f"**{role}**: {summary}")
        if len(lines) > 1:
            parts.append("\n".join(lines))

    if snapshot.messages:
        recent = snapshot.messages[-max_messages:]
        lines = ["### 운영-리서치 thread 발췌"]
        for msg in recent:
            tag = f" ({msg.role})" if msg.role else ""
            lines.append(f"- **{msg.author}**{tag}: {msg.content}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = (
    "DEFAULT_MAX_CHARS_PER_MESSAGE",
    "DEFAULT_MAX_LINKS",
    "DEFAULT_MAX_MESSAGES",
    "ThreadMessage",
    "ThreadSnapshot",
    "collapse_thread_to_snapshot",
    "extract_links_from_text",
    "render_thread_snapshot_block",
)
