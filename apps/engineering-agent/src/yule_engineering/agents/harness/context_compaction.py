"""compact→vault — deterministic compaction summary + vault task-log note (issue #185).

Implements the *deterministic core* of the ``compact-to-vault`` skill: it
folds a conversation/session into a compaction summary while preserving the
protected regions defined by ``context-compression.md`` (3.2), and renders +
writes a curated task-log note into the Obsidian vault using the canonical
filename convention (``<kind>-<topic-slug>[-issue-<n>].md``, F8/#99).

This module is intentionally *additive and decoupled*:

  * It works on an explicit :class:`CompactionTurn` list, not the live
    runtime, so it is pure and unit-testable. :func:`from_workflow_session`
    is a defensive adapter for the real :class:`WorkflowSession`.
  * It performs no git operation. Writing the note touches the working tree
    only; committing/pushing to the vault is a separate L3 step gated by the
    obsidian git layer + approval (see ``compact-to-vault.md`` hard rails).
  * It is not wired into any hot path. A future dispatcher gates auto-trigger
    behind :data:`ENABLE_ENV` (default off); the live ``/compact`` token
    capture (``compact_boundary``) is a follow-up.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from ..obsidian.filename_convention import validate_filename

# Auto-trigger gate for a future dispatcher. The functions here are callable
# directly regardless; this flag only governs automatic invocation.
ENABLE_ENV: str = "YULE_COMPACT_TO_VAULT_ENABLED"

# Turn kinds whose body is never folded (context-compression.md 3.2).
PROTECTED_KINDS: frozenset[str] = frozenset({"prompt", "decision", "synthesis"})

DEFAULT_HEAD_KEEP: int = 3
DEFAULT_TAIL_KEEP: int = 5
_CHARS_PER_TOKEN: int = 4
_PLACEHOLDER_MAX_CHARS: int = 80


def compaction_enabled() -> bool:
    """True if the auto-trigger flag is set (default False)."""

    return os.environ.get(ENABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompactionTurn:
    """One unit of conversation considered for compaction."""

    index: int
    speaker: str
    kind: str  # prompt | decision | synthesis | take | note | other
    text: str
    audit_id: Optional[str] = None


@dataclass(frozen=True)
class CompactionSummary:
    session_id: str
    focus: Optional[str]
    kept: Tuple[CompactionTurn, ...]
    folded: Tuple[str, ...]  # one-line placeholders, in original order
    summary_text: str
    pre_tokens: int
    post_tokens: int

    @property
    def saved_tokens(self) -> int:
        return max(0, self.pre_tokens - self.post_tokens)


@dataclass(frozen=True)
class CompactionNote:
    relative_path: str  # e.g. 10-projects/<project>/task-logs/task-log-compact-<id>.md
    filename: str
    markdown: str
    written_to: Optional[str] = None
    committed: bool = False


# ---------------------------------------------------------------------------
# Summary building
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    return (len(text or "") + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _fold_placeholder(turn: CompactionTurn) -> str:
    """``[role-take@<role>] <≤80자 요약> (생략된 본문 N자, audit_id=<id>)``."""

    flat = re.sub(r"\s+", " ", (turn.text or "").strip())
    head = flat[:_PLACEHOLDER_MAX_CHARS]
    if len(flat) > _PLACEHOLDER_MAX_CHARS:
        head = head.rstrip() + "…"
    audit = turn.audit_id or "-"
    return (
        f"[{turn.kind}@{turn.speaker}] {head} "
        f"(생략된 본문 {len(flat)}자, audit_id={audit})"
    )


def build_compaction_summary(
    turns: Sequence[CompactionTurn],
    *,
    session_id: str,
    focus: Optional[str] = None,
    head_keep: int = DEFAULT_HEAD_KEEP,
    tail_keep: int = DEFAULT_TAIL_KEEP,
) -> CompactionSummary:
    """Fold *turns* into a summary, preserving protected regions.

    Kept verbatim: the first ``head_keep`` turns, the last ``tail_keep``
    turns, every turn whose ``kind`` is in :data:`PROTECTED_KINDS`, and any
    turn whose text mentions *focus*. Everything else is folded to a
    one-line placeholder (with an ``audit_id`` back-reference).
    """

    ordered = list(turns)
    n = len(ordered)
    keep_idx: set[int] = set()
    for i in range(min(head_keep, n)):
        keep_idx.add(i)
    for i in range(max(0, n - tail_keep), n):
        keep_idx.add(i)
    focus_l = (focus or "").strip().lower()
    for i, t in enumerate(ordered):
        if t.kind in PROTECTED_KINDS:
            keep_idx.add(i)
        elif focus_l and focus_l in (t.text or "").lower():
            keep_idx.add(i)

    kept: list[CompactionTurn] = []
    folded: list[str] = []
    lines: list[str] = []
    for i, t in enumerate(ordered):
        if i in keep_idx:
            kept.append(t)
            body = (t.text or "").strip()
            lines.append(f"[{t.kind}@{t.speaker}] {body}")
        else:
            placeholder = _fold_placeholder(t)
            folded.append(placeholder)
            lines.append(placeholder)

    summary_text = "\n".join(lines).strip()
    pre_tokens = sum(_estimate_tokens(t.text) for t in ordered)
    post_tokens = _estimate_tokens(summary_text)
    return CompactionSummary(
        session_id=session_id,
        focus=focus or None,
        kept=tuple(kept),
        folded=tuple(folded),
        summary_text=summary_text,
        pre_tokens=pre_tokens,
        post_tokens=post_tokens,
    )


# ---------------------------------------------------------------------------
# WorkflowSession adapter (defensive)
# ---------------------------------------------------------------------------


def from_workflow_session(session: Any) -> Tuple[CompactionTurn, ...]:
    """Best-effort extraction of compaction turns from a WorkflowSession.

    Defensive by design (``getattr`` / ``Mapping`` checks) so a partial or
    legacy session never raises. Order: original prompt → progress notes →
    final summary (treated as synthesis).
    """

    turns: list[CompactionTurn] = []
    idx = 0
    prompt = getattr(session, "prompt", None)
    if isinstance(prompt, str) and prompt.strip():
        turns.append(CompactionTurn(idx, "user", "prompt", prompt.strip()))
        idx += 1

    for note in getattr(session, "progress_notes", ()) or ():
        if isinstance(note, str) and note.strip():
            turns.append(CompactionTurn(idx, "progress", "note", note.strip()))
            idx += 1

    extra = getattr(session, "extra", None)
    if isinstance(extra, Mapping):
        for entry in extra.get("agent_ops_audit", []) or []:
            if isinstance(entry, Mapping):
                text = str(entry.get("summary") or entry.get("outcome") or "").strip()
                if text:
                    turns.append(
                        CompactionTurn(
                            idx,
                            str(entry.get("action") or "audit"),
                            "note",
                            text,
                            audit_id=_opt_str(entry.get("entry_id")),
                        )
                    )
                    idx += 1

    summary = getattr(session, "summary", None)
    if isinstance(summary, str) and summary.strip():
        turns.append(CompactionTurn(idx, "tech-lead", "synthesis", summary.strip()))
        idx += 1

    return tuple(turns)


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Vault note rendering + writing
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "session"


def compaction_note_filename(session_id: str, *, issue: Optional[int] = None) -> str:
    """``task-log-compact-<session-slug>[-issue-<n>].md`` (validated)."""

    name = f"task-log-compact-{_slug(session_id)}"
    if issue is not None:
        name += f"-issue-{int(issue)}"
    name += ".md"
    verdict = validate_filename(name)
    if not verdict.valid:
        raise ValueError(
            f"compaction filename {name!r} violates convention: {verdict.reason}"
        )
    return name


def render_compaction_note(
    summary: CompactionSummary,
    *,
    project: str,
    created_at: Optional[datetime] = None,
    issue: Optional[int] = None,
    original_prompt: Optional[str] = None,
) -> str:
    """Render the curated task-log markdown (frontmatter + body)."""

    when = (created_at or datetime.now(tz=timezone.utc)).replace(microsecond=0)
    title = f"compact 요약 — session {summary.session_id}"
    related = f"10-projects/{project}"
    folded_block = (
        "\n".join(f"- {line}" for line in summary.folded)
        if summary.folded
        else "- (접힌 turn 없음 — 보호 영역만으로 budget 이하)"
    )
    prompt_mirror = (original_prompt or "").strip()

    frontmatter = "\n".join(
        [
            "---",
            f"title: {title}",
            "kind: task-log",
            "status: draft",
            f"created_at: {when.isoformat()}",
            "tags: [compact-to-vault, task-log]",
            "related: []",
            f"home_hub: {related}",
            f"session_id: {summary.session_id}",
            f"focus: {summary.focus or '-'}",
            f"pre_tokens: {summary.pre_tokens}",
            f"post_tokens: {summary.post_tokens}",
            f"saved_tokens: {summary.saved_tokens}",
            "---",
        ]
    )

    body = "\n".join(
        [
            f"# {title}",
            "",
            "## 핵심 요약",
            "",
            "```",
            summary.summary_text or "(빈 세션)",
            "```",
            "",
            "## 접힌 turn (audit_id 역참조)",
            "",
            folded_block,
            "",
            "## 토큰",
            "",
            f"- 압축 전(추정): {summary.pre_tokens}",
            f"- 압축 후(추정): {summary.post_tokens}",
            f"- 절감(추정): {summary.saved_tokens}",
            "",
            "## 원문 prompt (mirror — 압축 대상 아님)",
            "",
            "```",
            prompt_mirror or "(원문 prompt 미제공)",
            "```",
            "",
        ]
    )
    return frontmatter + "\n\n" + body


def write_compaction_note(
    summary: CompactionSummary,
    *,
    vault_root: Path,
    project: str,
    created_at: Optional[datetime] = None,
    issue: Optional[int] = None,
    original_prompt: Optional[str] = None,
    commit: bool = False,
) -> CompactionNote:
    """Render + write the task-log note under ``vault_root``.

    Writes the working tree only. ``commit`` is reserved for the gated L3
    vault-commit step handled by the obsidian git layer; this function never
    runs git. The returned note carries ``committed=False`` accordingly.
    """

    filename = compaction_note_filename(summary.session_id, issue=issue)
    relative_path = f"10-projects/{project}/task-logs/{filename}"
    markdown = render_compaction_note(
        summary,
        project=project,
        created_at=created_at,
        issue=issue,
        original_prompt=original_prompt,
    )

    target = Path(vault_root) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")

    return CompactionNote(
        relative_path=relative_path,
        filename=filename,
        markdown=markdown,
        written_to=str(target),
        committed=False,
    )


__all__ = (
    "ENABLE_ENV",
    "PROTECTED_KINDS",
    "CompactionNote",
    "CompactionSummary",
    "CompactionTurn",
    "build_compaction_summary",
    "compaction_enabled",
    "compaction_note_filename",
    "from_workflow_session",
    "render_compaction_note",
    "write_compaction_note",
)
