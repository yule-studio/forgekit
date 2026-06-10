"""Five memory source adapters (F10 / #101).

Each adapter implements the :class:`MemorySource` protocol — a single
``query(filter)`` method returning an iterable of :class:`MemoryShard`.
Adapters are intentionally narrow:

  * **ObsidianVaultSource** — pure file-system reader over a vault
    directory. No vault writes; never traverses outside the root.
  * **SessionExtraSource** — projects the round-1 session.extra
    ledger into shards. Pure in-memory; caller injects the snapshot.
  * **MistakeLedgerSource** — reads from F2 SQLite ``MistakeLedger``
    via its public read methods (``list_for_role`` / ``find_similar``).
    Never calls a mutating method.
  * **DecisionSource** — projects an injected sequence of audit-style
    decisions into shards. Caller owns the audit fetch.
  * **AuditSource** — projects an injected sequence of general audit
    events into shards. Same shape as DecisionSource but lower trust.

Hard rails:

  * Every adapter exposes ONLY ``query``. No ``write`` / ``insert`` /
    ``delete`` / ``upsert`` method. The governance regression test
    introspects each instance to enforce this.
  * Inputs to in-memory adapters (Session/Decision/Audit) are deep-
    copied at construction so a caller mutating the original list
    does not leak into shard output.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import (
    MemoryFilter,
    MemoryShard,
    ShardKind,
    _utc_now_iso,
    tokenize,
)


# ---------------------------------------------------------------------------
# Obsidian vault source
# ---------------------------------------------------------------------------


class ObsidianVaultSource:
    """Read-only adapter over an Obsidian vault directory.

    The adapter walks ``*.md`` files under ``vault_root`` and projects
    each into a single :class:`MemoryShard`. ``topic_tags`` come from
    YAML frontmatter ``tags:`` (best-effort parse) plus any literal
    ``#tag`` hits in the body. ``related_issue`` is parsed from
    frontmatter ``issue:`` or a trailing ``-issue-<n>.md`` filename
    suffix per the F8 obsidian convention.

    The adapter never writes to the vault. ``query`` re-reads on each
    call so vault edits surface without restart — this is fine for
    MVP scale (≤ low hundreds of notes).
    """

    kind = ShardKind.OBSIDIAN_NOTE

    def __init__(
        self,
        vault_root: str | Path,
        *,
        max_files: int = 200,
        source_label: Optional[str] = None,
    ) -> None:
        self._vault_root = Path(vault_root).expanduser().resolve()
        self._max_files = max(1, int(max_files))
        self._source_label = source_label or "obsidian-vault"

    def query(self, filter: MemoryFilter) -> Iterable[MemoryShard]:
        if not self._vault_root.exists() or not self._vault_root.is_dir():
            return ()
        out: List[MemoryShard] = []
        for path in sorted(self._vault_root.rglob("*.md"))[: self._max_files]:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            shard = self._project(path, text)
            if shard is None:
                continue
            if not _filter_matches(shard, filter):
                continue
            out.append(shard)
            if len(out) >= max(1, filter.limit):
                break
        return tuple(out)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _project(self, path: Path, text: str) -> Optional[MemoryShard]:
        frontmatter, body = _split_frontmatter(text)
        tags = _frontmatter_tags(frontmatter)
        for raw_tag in _inline_hashtags(body):
            tags.append(raw_tag)
        created_at = (
            _frontmatter_value(frontmatter, "created_at")
            or _frontmatter_value(frontmatter, "date")
            or _file_mtime_iso(path)
        )
        issue = _parse_issue_number(
            _frontmatter_value(frontmatter, "issue")
            or _suffix_issue(path.stem)
        )
        try:
            rel = path.relative_to(self._vault_root)
            relative_source = str(rel)
        except ValueError:
            relative_source = path.name
        return MemoryShard(
            kind=ShardKind.OBSIDIAN_NOTE,
            source=f"{self._source_label}:{relative_source}",
            content=body.strip() or text.strip(),
            created_at=created_at,
            topic_tags=tuple(_dedupe_lower(tags)),
            related_issue=issue,
        )


# ---------------------------------------------------------------------------
# Session.extra source
# ---------------------------------------------------------------------------


class SessionExtraSource:
    """Project round-1 session.extra ledger entries into shards.

    The caller (typically the workflow runtime) injects a snapshot at
    construction. The snapshot is a sequence of mappings with at
    least ``content`` + ``created_at``; ``topic_tags`` / ``role`` /
    ``issue`` are read when present.
    """

    kind = ShardKind.SESSION_EXTRA

    def __init__(
        self,
        entries: Sequence[Mapping[str, Any]],
        *,
        source_label: Optional[str] = None,
    ) -> None:
        self._entries: Tuple[Mapping[str, Any], ...] = tuple(
            dict(entry) for entry in entries if isinstance(entry, Mapping)
        )
        self._source_label = source_label or "session.extra"

    def query(self, filter: MemoryFilter) -> Iterable[MemoryShard]:
        out: List[MemoryShard] = []
        for entry in self._entries:
            content = str(entry.get("content") or "").strip()
            if not content:
                continue
            tags = tuple(_dedupe_lower(entry.get("topic_tags") or []))
            shard = MemoryShard(
                kind=ShardKind.SESSION_EXTRA,
                source=(
                    f"{self._source_label}:{entry.get('role') or 'unknown'}"
                ),
                content=content,
                created_at=str(entry.get("created_at") or _utc_now_iso()),
                topic_tags=tags,
                related_issue=_parse_issue_number(entry.get("issue")),
                related_pr=_parse_issue_number(entry.get("pr")),
            )
            if not _filter_matches(shard, filter):
                continue
            out.append(shard)
            if len(out) >= max(1, filter.limit):
                break
        return tuple(out)


# ---------------------------------------------------------------------------
# Mistake ledger source
# ---------------------------------------------------------------------------


class MistakeLedgerSource:
    """Read-only adapter over the F2 MistakeLedger.

    Calls ``list_for_role`` (or ``all_records`` when no role filter)
    and projects each row into a shard. The ``blocker_level`` field
    is preserved so :class:`RelevanceSelector` can detect BLOCK and
    surface it top.

    The adapter only invokes public *read* methods on the ledger.
    The governance regression test asserts no other method is called.
    """

    kind = ShardKind.MISTAKE

    def __init__(self, ledger: Any, *, source_label: Optional[str] = None) -> None:
        self._ledger = ledger
        self._source_label = source_label or "mistake-ledger"

    def query(self, filter: MemoryFilter) -> Iterable[MemoryShard]:
        role = (filter.role or "").strip()
        limit = max(1, int(filter.limit or 1))
        rows: Sequence[Any]
        if role:
            try:
                rows = self._ledger.list_for_role(role, limit=limit)
            except Exception:  # noqa: BLE001 - fail-closed read
                return ()
        else:
            try:
                rows = self._ledger.all_records(include_resolved=False)[:limit]
            except Exception:  # noqa: BLE001
                return ()
        out: List[MemoryShard] = []
        for row in rows:
            shard = self._project(row)
            if shard is None:
                continue
            if not _filter_matches(shard, filter):
                continue
            out.append(shard)
            if len(out) >= limit:
                break
        return tuple(out)

    def _project(self, row: Any) -> Optional[MemoryShard]:
        try:
            role = str(getattr(row, "role"))
            pattern = str(getattr(row, "pattern"))
            signature = str(getattr(row, "signature"))
            last_seen = str(getattr(row, "last_seen"))
            blocker_level = str(getattr(row, "blocker_level"))
        except AttributeError:
            return None
        # MistakeRecord stores blocker_level as Enum — coerce to value
        if hasattr(row.blocker_level, "value"):
            blocker_value = row.blocker_level.value
        else:
            blocker_value = blocker_level
        return MemoryShard(
            kind=ShardKind.MISTAKE,
            source=f"{self._source_label}:{role}",
            content=f"[{pattern}] {signature}",
            created_at=last_seen,
            topic_tags=tuple(_dedupe_lower([pattern, role])),
            blocker_level=str(blocker_value).upper(),
        )


# ---------------------------------------------------------------------------
# Decision source
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AuditEntryView:
    """Lightweight read-only view over an audit/decision mapping.

    Source-injected. Helps the adapters reject mappings that lack the
    minimum required fields without raising.
    """

    role: str
    content: str
    created_at: str
    topic_tags: Tuple[str, ...]
    related_issue: Optional[int]
    related_pr: Optional[int]
    source_id: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> Optional["_AuditEntryView"]:
        if not isinstance(payload, Mapping):
            return None
        role = str(
            payload.get("role")
            or payload.get("actor")
            or payload.get("role_id")
            or ""
        ).strip()
        content = str(
            payload.get("summary")
            or payload.get("reason")
            or payload.get("content")
            or ""
        ).strip()
        if not content:
            return None
        created_at = str(
            payload.get("recorded_at")
            or payload.get("created_at")
            or payload.get("timestamp")
            or _utc_now_iso()
        )
        topic_tags = tuple(_dedupe_lower(payload.get("topic_tags") or []))
        related_issue = _parse_issue_number(
            payload.get("issue")
            or payload.get("issue_number")
            or payload.get("related_issue")
        )
        related_pr = _parse_issue_number(
            payload.get("pr")
            or payload.get("pr_number")
            or payload.get("related_pr")
        )
        source_id = str(
            payload.get("decision_id")
            or payload.get("entry_id")
            or payload.get("id")
            or "anon"
        )
        return cls(
            role=role,
            content=content,
            created_at=created_at,
            topic_tags=topic_tags,
            related_issue=related_issue,
            related_pr=related_pr,
            source_id=source_id,
        )


class DecisionSource:
    """Project DECISION audit entries into shards.

    Caller fetches decisions from agent_ops_audit (or any equivalent
    durable log) and passes the resulting sequence in. The adapter
    keeps a defensive snapshot — later caller mutations cannot leak.
    """

    kind = ShardKind.DECISION

    def __init__(
        self,
        entries: Sequence[Mapping[str, Any]],
        *,
        source_label: Optional[str] = None,
    ) -> None:
        self._entries: Tuple[_AuditEntryView, ...] = tuple(
            view
            for view in (_AuditEntryView.from_mapping(e) for e in entries)
            if view is not None
        )
        self._source_label = source_label or "agent-ops-audit:decision"

    def query(self, filter: MemoryFilter) -> Iterable[MemoryShard]:
        return _project_audit_views(
            self._entries,
            kind=ShardKind.DECISION,
            source_label=self._source_label,
            filter=filter,
        )


# ---------------------------------------------------------------------------
# Audit source
# ---------------------------------------------------------------------------


class AuditSource:
    """Project general audit events into shards.

    Same shape as :class:`DecisionSource` but a different kind so the
    relevance selector applies a different source-trust score (0.8
    vs. 0.9). Intended for non-DECISION action verbs (e.g. retries,
    blocked_completion, postmortem).
    """

    kind = ShardKind.AUDIT

    def __init__(
        self,
        entries: Sequence[Mapping[str, Any]],
        *,
        source_label: Optional[str] = None,
    ) -> None:
        self._entries: Tuple[_AuditEntryView, ...] = tuple(
            view
            for view in (_AuditEntryView.from_mapping(e) for e in entries)
            if view is not None
        )
        self._source_label = source_label or "agent-ops-audit"

    def query(self, filter: MemoryFilter) -> Iterable[MemoryShard]:
        return _project_audit_views(
            self._entries,
            kind=ShardKind.AUDIT,
            source_label=self._source_label,
            filter=filter,
        )


# ---------------------------------------------------------------------------
# Shared internals
# ---------------------------------------------------------------------------


def _project_audit_views(
    entries: Sequence[_AuditEntryView],
    *,
    kind: ShardKind,
    source_label: str,
    filter: MemoryFilter,
) -> Tuple[MemoryShard, ...]:
    out: List[MemoryShard] = []
    for view in entries:
        shard = MemoryShard(
            kind=kind,
            source=f"{source_label}:{view.source_id}",
            content=view.content,
            created_at=view.created_at,
            topic_tags=view.topic_tags,
            related_issue=view.related_issue,
            related_pr=view.related_pr,
        )
        if not _filter_matches(shard, filter, role_override=view.role):
            continue
        out.append(shard)
        if len(out) >= max(1, filter.limit):
            break
    return tuple(out)


def _filter_matches(
    shard: MemoryShard,
    filter: MemoryFilter,
    *,
    role_override: Optional[str] = None,
) -> bool:
    if filter.issue is not None and shard.related_issue != filter.issue:
        return False
    if filter.pr is not None and shard.related_pr != filter.pr:
        return False
    if filter.role:
        role = (role_override or "").strip().lower()
        source = (shard.source or "").lower()
        wanted = filter.role.strip().lower()
        if wanted and wanted not in role and wanted not in source:
            # Also try last segment of slashed role spec.
            last_segment = wanted.rsplit("/", 1)[-1]
            if not (last_segment and (last_segment in role or last_segment in source)):
                return False
    if filter.topic_tags:
        wanted_tokens = set()
        for tag in filter.topic_tags:
            wanted_tokens |= set(tokenize(tag))
        if wanted_tokens:
            shard_tokens = set()
            for tag in shard.topic_tags:
                shard_tokens |= set(tokenize(tag))
            if not (wanted_tokens & shard_tokens):
                return False
    if filter.since:
        if shard.created_at < filter.since:
            return False
    return True


def _split_frontmatter(text: str) -> Tuple[str, str]:
    """Return (frontmatter, body). Empty frontmatter when none."""

    if not text.startswith("---"):
        return ("", text)
    lines = text.splitlines()
    if len(lines) < 2:
        return ("", text)
    end_idx = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx == -1:
        return ("", text)
    frontmatter = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    return (frontmatter, body)


def _frontmatter_value(frontmatter: str, key: str) -> Optional[str]:
    if not frontmatter:
        return None
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        k, _, v = stripped.partition(":")
        if k.strip().lower() == key.strip().lower():
            return v.strip().strip('"').strip("'")
    return None


def _frontmatter_tags(frontmatter: str) -> List[str]:
    if not frontmatter:
        return []
    raw = _frontmatter_value(frontmatter, "tags")
    if not raw:
        return []
    # Accept either `[a, b]` or `a, b` or a single token.
    raw = raw.strip().strip("[").strip("]")
    return [token.strip().strip('"').strip("'") for token in raw.split(",") if token.strip()]


def _inline_hashtags(body: str) -> List[str]:
    if not body:
        return []
    out: List[str] = []
    for token in body.split():
        if token.startswith("#") and len(token) > 1:
            cleaned = token.lstrip("#").rstrip(".,:;)]")
            if cleaned:
                out.append(cleaned)
    return out


def _parse_issue_number(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, int):
            return int(value)
        text = str(value).strip()
        if not text:
            return None
        if text.startswith("#"):
            text = text[1:]
        return int(text)
    except (TypeError, ValueError):
        return None


def _suffix_issue(stem: str) -> Optional[str]:
    if not stem:
        return None
    parts = stem.split("-issue-")
    if len(parts) != 2:
        return None
    tail = parts[1].split("-")[0]
    return tail or None


def _file_mtime_iso(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return _utc_now_iso()
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()


def _dedupe_lower(values: Iterable[Any]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for v in values:
        text = str(v or "").strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


__all__ = (
    "AuditSource",
    "DecisionSource",
    "MistakeLedgerSource",
    "ObsidianVaultSource",
    "SessionExtraSource",
)
