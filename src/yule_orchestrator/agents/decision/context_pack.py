"""Context Pack — Phase 3 of #73.

Bundles the inputs a classifier (or downstream worker) needs to
make a sound decision about a Discord intake message:

  * **related_notes** — Obsidian note paths that look topically related.
  * **recent_threads** — recent thread ids the user has been active in.
  * **related_issues / related_prs** — GitHub items the message
    explicitly mentions or whose title shares keywords.
  * **code_hints** — repo-relative paths that look related to the
    request (file names mentioned literally, or the role's primary
    surface from `role_profiles.activation_keywords`).

Each source is :class:`Protocol` injected so the same builder runs
in unit tests (with fakes) and production (with retrieval / GitHub
App / file-system glob).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from ..memory import MemoryPack


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextPack:
    """Bundle of related context the classifier reads.

    F10 (issue #101) extends this with an optional ``memory_pack``
    field carrying the cross-session :class:`MemoryPack` produced by
    the long-term memory unifier. Older callers that don't wire the
    unifier keep getting ``memory_pack=None`` so the field is fully
    backwards compatible.
    """

    id: str
    related_notes: Tuple[str, ...]
    recent_threads: Tuple[str, ...]
    related_issues: Tuple[int, ...]
    related_prs: Tuple[int, ...]
    code_hints: Tuple[str, ...]
    created_at: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    memory_pack: Optional[MemoryPack] = None

    def to_payload(self) -> Mapping[str, Any]:
        payload: dict = {
            "id": self.id,
            "related_notes": list(self.related_notes),
            "recent_threads": list(self.recent_threads),
            "related_issues": list(self.related_issues),
            "related_prs": list(self.related_prs),
            "code_hints": list(self.code_hints),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }
        if self.memory_pack is not None:
            payload["memory_pack"] = self.memory_pack.to_payload()
        else:
            payload["memory_pack"] = None
        return payload

    @property
    def is_empty(self) -> bool:
        has_memory = bool(
            self.memory_pack is not None and self.memory_pack.shards
        )
        return not (
            self.related_notes
            or self.recent_threads
            or self.related_issues
            or self.related_prs
            or self.code_hints
            or has_memory
        )


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class NoteProvider(Protocol):
    def find_related_notes(
        self, *, prompt: str, session_id: Optional[str], limit: int
    ) -> Sequence[str]:  # pragma: no cover - Protocol
        ...


class ThreadProvider(Protocol):
    def find_recent_threads(
        self, *, prompt: str, session_id: Optional[str], limit: int
    ) -> Sequence[str]:  # pragma: no cover - Protocol
        ...


class GithubReferenceProvider(Protocol):
    def find_related_issues(
        self, *, prompt: str, limit: int
    ) -> Sequence[int]:  # pragma: no cover - Protocol
        ...

    def find_related_prs(
        self, *, prompt: str, limit: int
    ) -> Sequence[int]:  # pragma: no cover - Protocol
        ...


class CodeHintProvider(Protocol):
    def find_code_hints(
        self, *, prompt: str, role: Optional[str], limit: int
    ) -> Sequence[str]:  # pragma: no cover - Protocol
        ...


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_context_pack(
    *,
    prompt: str,
    session_id: Optional[str] = None,
    role: Optional[str] = None,
    note_provider: Optional[NoteProvider] = None,
    thread_provider: Optional[ThreadProvider] = None,
    github_reference_provider: Optional[GithubReferenceProvider] = None,
    code_hint_provider: Optional[CodeHintProvider] = None,
    note_limit: int = 5,
    thread_limit: int = 3,
    issue_limit: int = 5,
    pr_limit: int = 3,
    code_limit: int = 5,
    metadata: Optional[Mapping[str, Any]] = None,
    memory_pack: Optional[MemoryPack] = None,
) -> ContextPack:
    """Compose a :class:`ContextPack` from the available providers.

    Missing providers contribute empty tuples — the pack reflects
    what *was actually queryable* at build time. ``ContextPack.is_empty``
    lets callers decide whether to escalate to clarification.

    Pure: providers do the I/O; this function only orchestrates.
    Always returns a fresh ``id`` (timestamp + uuid suffix).
    """

    notes: Tuple[str, ...] = ()
    threads: Tuple[str, ...] = ()
    issues: Tuple[int, ...] = ()
    prs: Tuple[int, ...] = ()
    hints: Tuple[str, ...] = ()

    if note_provider is not None:
        notes = _safe_tuple(
            note_provider.find_related_notes(
                prompt=prompt, session_id=session_id, limit=note_limit
            )
        )
    if thread_provider is not None:
        threads = _safe_tuple(
            thread_provider.find_recent_threads(
                prompt=prompt, session_id=session_id, limit=thread_limit
            )
        )
    if github_reference_provider is not None:
        issues = _safe_int_tuple(
            github_reference_provider.find_related_issues(
                prompt=prompt, limit=issue_limit
            )
        )
        prs = _safe_int_tuple(
            github_reference_provider.find_related_prs(
                prompt=prompt, limit=pr_limit
            )
        )
    if code_hint_provider is not None:
        hints = _safe_tuple(
            code_hint_provider.find_code_hints(
                prompt=prompt, role=role, limit=code_limit
            )
        )

    return ContextPack(
        id=_new_pack_id(),
        related_notes=notes,
        recent_threads=threads,
        related_issues=issues,
        related_prs=prs,
        code_hints=hints,
        created_at=datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
        metadata=dict(metadata or {}),
        memory_pack=memory_pack,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _safe_tuple(values: Sequence[Any]) -> Tuple[str, ...]:
    if not values:
        return ()
    out: list = []
    for v in values:
        text = str(v).strip()
        if text:
            out.append(text)
    return tuple(out)


def _safe_int_tuple(values: Sequence[Any]) -> Tuple[int, ...]:
    if not values:
        return ()
    out: list = []
    for v in values:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _new_pack_id() -> str:
    return f"ctx-{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:10]}"


__all__ = (
    "CodeHintProvider",
    "ContextPack",
    "GithubReferenceProvider",
    "NoteProvider",
    "ThreadProvider",
    "build_context_pack",
)
