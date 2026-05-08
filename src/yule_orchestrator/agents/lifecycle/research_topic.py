"""Research topic ledger + title normalisation — A-M7.6.

Single source of truth for "is this thread / save request the same
research topic as before?" — answers three questions the M7.5
forum-handoff producer keeps asking:

  * **What is the canonical key for this topic?** — derived from
    a slug of the prompt + the research forum thread id; same
    thread always maps to the same key, so successive save
    requests collapse to one approval card.
  * **What is the canonical title?** — strips ``[Research]``
    prefixes, collapses long sentences into a short noun-phrase,
    caps at a reasonable length so vault filenames stay readable.
  * **What is the lifecycle status?** — researching →
    pending_approval → approved → saved (with optional
    superseded / rejected for revision flows). The producer reads
    the status before enqueuing a new approval card.

Pure-Python — no SQLite, no Discord. The ledger lives on
``session.extra['research_topic']`` so it round-trips through the
existing workflow_state cache layer that every other M5/M6 audit
key uses (approval_rejections / fallback_audits / role_changes).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Lifecycle status — a small enum-ish surface so callers compare by
# constant instead of magic strings.
# ---------------------------------------------------------------------------


STATUS_RESEARCHING: str = "researching"
STATUS_PENDING_APPROVAL: str = "pending_approval"
STATUS_APPROVED: str = "approved"
STATUS_SAVED: str = "saved"
STATUS_SUPERSEDED: str = "superseded"
STATUS_REJECTED: str = "rejected"


_TERMINAL_STATUSES: frozenset[str] = frozenset({STATUS_SUPERSEDED, STATUS_REJECTED})
_ACTIVE_STATUSES: frozenset[str] = frozenset(
    {STATUS_RESEARCHING, STATUS_PENDING_APPROVAL, STATUS_APPROVED, STATUS_SAVED}
)


# ---------------------------------------------------------------------------
# Title normalisation
# ---------------------------------------------------------------------------


_RESEARCH_PREFIX_RE = re.compile(
    r"^\s*\[?\s*(?:research|operations[-\s]?research|운영[-\s]?리서치)\s*\]?\s*[:\-]?\s*",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_TAIL_RE = re.compile(r"[\s\.\?!~。…]+$")


# Vault filenames + Discord embeds tolerate ~80 chars; cap before
# delimiter so trimmed titles still read like phrases.
DEFAULT_TITLE_MAX_LEN: int = 80
DEFAULT_KEY_MAX_LEN: int = 60


def normalize_research_title(
    raw: Optional[str],
    *,
    max_len: int = DEFAULT_TITLE_MAX_LEN,
) -> str:
    """Return a short canonical title for *raw*.

    Strips ``[Research]`` / ``[Research:]`` / ``운영-리서치`` prefixes,
    collapses whitespace, drops trailing punctuation, and caps at
    *max_len*. The original prompt is preserved separately by the
    caller (``original_prompt`` field on the knowledge note) — this
    function is **only** for the human-readable title.

    Empty / whitespace-only input returns ``""``; the caller decides
    what to do (commonly fall back to ``request.title`` or a
    timestamp-based label).
    """

    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    text = _RESEARCH_PREFIX_RE.sub("", text).strip()
    text = _WHITESPACE_RE.sub(" ", text)
    text = _PUNCT_TAIL_RE.sub("", text)
    if len(text) > max_len:
        # Cut at the last whitespace before the cap so we don't
        # split a Korean noun phrase mid-syllable. Falls back to the
        # hard cut when no whitespace is found inside the window.
        cut = text.rfind(" ", 0, max_len)
        text = text[: cut if cut > 0 else max_len].rstrip()
    return text


# ---------------------------------------------------------------------------
# Topic key
# ---------------------------------------------------------------------------


_KEY_TOKEN_RE = re.compile(r"[^a-z0-9가-힣]+")


def derive_topic_key(
    *,
    prompt: Optional[str],
    research_thread_id: Optional[int] = None,
    semantic_title: Optional[str] = None,
    max_len: int = DEFAULT_KEY_MAX_LEN,
) -> str:
    """Derive a stable slug-key for a research topic.

    Resolution:

      1. *semantic_title* (if set) gets slugified — caller-provided
         override wins so a tech-lead-driven label can pin the key.
      2. Otherwise the normalised prompt is slugified.
      3. *research_thread_id* (when present) is appended as a short
         suffix so prompts that look identical but live in
         different threads stay distinct.

    Returns ``""`` only when both the title and prompt are empty —
    callers should treat that as "no key yet, use thread_id alone".
    """

    base_text = (
        normalize_research_title(semantic_title)
        if semantic_title
        else normalize_research_title(prompt)
    )
    if not base_text and not research_thread_id:
        return ""

    # NFKD-normalise so half-width / weird-unicode variants of the
    # same Korean phrase collapse to the same slug. Lowercase ASCII
    # but preserve hangul (the regex below only strips non-token chars).
    folded = unicodedata.normalize("NFKD", base_text or "").lower()
    slug = _KEY_TOKEN_RE.sub("-", folded).strip("-")
    if not slug and not research_thread_id:
        return ""

    if research_thread_id is not None:
        # 6-char hash suffix — short enough to keep the key readable,
        # long enough to avoid accidental collision across threads
        # with the same slug.
        suffix = hashlib.sha1(
            str(int(research_thread_id)).encode("utf-8")
        ).hexdigest()[:6]
        if slug:
            slug = f"{slug}-{suffix}"
        else:
            slug = f"thread-{suffix}"

    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug


# ---------------------------------------------------------------------------
# Ledger record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopicLedgerRecord:
    """One ``research_topic`` audit row stored on session.extra.

    ``approval_job_id`` / ``obsidian_write_job_id`` / ``vault_path``
    are populated as the lifecycle progresses — the producer reads
    these to dedup successive save requests.
    """

    topic_key: str
    research_thread_id: Optional[int]
    canonical_title: str
    original_prompt: str
    active_roles: Sequence[str] = ()
    status: str = STATUS_RESEARCHING
    approval_job_id: Optional[str] = None
    obsidian_write_job_id: Optional[str] = None
    vault_path: Optional[str] = None
    updated_at: str = ""
    revision: int = 1

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "topic_key": self.topic_key,
            "research_thread_id": self.research_thread_id,
            "canonical_title": self.canonical_title,
            "original_prompt": self.original_prompt,
            "active_roles": list(self.active_roles),
            "status": self.status,
            "approval_job_id": self.approval_job_id,
            "obsidian_write_job_id": self.obsidian_write_job_id,
            "vault_path": self.vault_path,
            "updated_at": self.updated_at,
            "revision": self.revision,
        }

    @classmethod
    def from_payload(
        cls, payload: Optional[Mapping[str, Any]]
    ) -> Optional["TopicLedgerRecord"]:
        if not isinstance(payload, Mapping) or not payload:
            return None
        try:
            thread_raw = payload.get("research_thread_id")
            thread_id = int(thread_raw) if thread_raw is not None else None
        except (TypeError, ValueError):
            thread_id = None
        active = payload.get("active_roles")
        if not isinstance(active, (list, tuple)):
            active = ()
        return cls(
            topic_key=str(payload.get("topic_key") or ""),
            research_thread_id=thread_id,
            canonical_title=str(payload.get("canonical_title") or ""),
            original_prompt=str(payload.get("original_prompt") or ""),
            active_roles=tuple(str(r) for r in active),
            status=str(payload.get("status") or STATUS_RESEARCHING),
            approval_job_id=_optional_str(payload.get("approval_job_id")),
            obsidian_write_job_id=_optional_str(
                payload.get("obsidian_write_job_id")
            ),
            vault_path=_optional_str(payload.get("vault_path")),
            updated_at=str(payload.get("updated_at") or ""),
            revision=int(payload.get("revision") or 1),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @property
    def has_pending_approval(self) -> bool:
        return self.status == STATUS_PENDING_APPROVAL

    @property
    def is_saved(self) -> bool:
        return self.status == STATUS_SAVED


# ---------------------------------------------------------------------------
# Session.extra ↔ ledger persistence
# ---------------------------------------------------------------------------


_LEDGER_KEY: str = "research_topic"


def read_topic_ledger(
    session: Any,
) -> Optional[TopicLedgerRecord]:
    """Pull the current ``research_topic`` record off *session.extra*.

    Returns ``None`` when the key is absent — the producer treats
    that as "no prior topic, start a new lifecycle".
    """

    extra = getattr(session, "extra", None) if session is not None else None
    if not isinstance(extra, Mapping):
        return None
    return TopicLedgerRecord.from_payload(extra.get(_LEDGER_KEY))


def write_topic_ledger(
    extra: Optional[Mapping[str, Any]],
    record: TopicLedgerRecord,
) -> dict:
    """Return a copy of *extra* with the ledger record stamped on.

    Pure helper — caller wraps with ``dataclasses.replace`` +
    ``workflow_state.update_session`` to persist. Mirrors the
    pattern ``apply_role_selection_to_extra`` uses.
    """

    new_extra: dict = dict(extra or {})
    payload = dict(record.to_payload())
    if not payload.get("updated_at"):
        payload["updated_at"] = (
            datetime.now(tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    new_extra[_LEDGER_KEY] = payload
    return new_extra


def transition_topic_ledger(
    record: TopicLedgerRecord,
    *,
    status: str,
    approval_job_id: Optional[str] = None,
    obsidian_write_job_id: Optional[str] = None,
    vault_path: Optional[str] = None,
    revision_bump: bool = False,
    now: Optional[datetime] = None,
) -> TopicLedgerRecord:
    """Return a new record reflecting a status transition.

    Pure transformation — does not touch session.extra. Combined
    with :func:`write_topic_ledger` it gives the producer two
    explicit steps so a unit test can drive each independently.
    """

    when = now or datetime.now(tz=timezone.utc)
    return TopicLedgerRecord(
        topic_key=record.topic_key,
        research_thread_id=record.research_thread_id,
        canonical_title=record.canonical_title,
        original_prompt=record.original_prompt,
        active_roles=tuple(record.active_roles),
        status=status,
        approval_job_id=approval_job_id
        if approval_job_id is not None
        else record.approval_job_id,
        obsidian_write_job_id=obsidian_write_job_id
        if obsidian_write_job_id is not None
        else record.obsidian_write_job_id,
        vault_path=vault_path if vault_path is not None else record.vault_path,
        updated_at=when.replace(microsecond=0).isoformat(),
        revision=record.revision + 1 if revision_bump else record.revision,
    )


def build_ledger_record(
    *,
    session: Any,
    research_thread_id: Optional[int] = None,
    semantic_title: Optional[str] = None,
    active_roles: Sequence[str] = (),
    status: str = STATUS_RESEARCHING,
    now: Optional[datetime] = None,
) -> TopicLedgerRecord:
    """Compose a fresh :class:`TopicLedgerRecord` for *session*.

    The producer calls this on the first save request for a thread.
    Subsequent calls go through :func:`transition_topic_ledger` so
    the ``topic_key`` stays stable across status transitions.
    """

    prompt = (getattr(session, "prompt", "") or "").strip()
    title = normalize_research_title(semantic_title) or normalize_research_title(prompt)
    key = derive_topic_key(
        prompt=prompt,
        research_thread_id=research_thread_id,
        semantic_title=semantic_title,
    )
    when = now or datetime.now(tz=timezone.utc)
    return TopicLedgerRecord(
        topic_key=key,
        research_thread_id=research_thread_id,
        canonical_title=title or prompt[:DEFAULT_TITLE_MAX_LEN] or "untitled-topic",
        original_prompt=prompt,
        active_roles=tuple(active_roles),
        status=status,
        approval_job_id=None,
        obsidian_write_job_id=None,
        vault_path=None,
        updated_at=when.replace(microsecond=0).isoformat(),
        revision=1,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = (
    "DEFAULT_KEY_MAX_LEN",
    "DEFAULT_TITLE_MAX_LEN",
    "STATUS_APPROVED",
    "STATUS_PENDING_APPROVAL",
    "STATUS_REJECTED",
    "STATUS_RESEARCHING",
    "STATUS_SAVED",
    "STATUS_SUPERSEDED",
    "TopicLedgerRecord",
    "build_ledger_record",
    "derive_topic_key",
    "normalize_research_title",
    "read_topic_ledger",
    "transition_topic_ledger",
    "write_topic_ledger",
)
