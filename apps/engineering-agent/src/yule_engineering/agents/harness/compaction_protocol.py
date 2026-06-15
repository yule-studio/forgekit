"""compact→vault *protocol* — checkpoints, receipt, and the /clear guard (issue #185 follow-up).

``context_compaction`` holds the deterministic *core* (fold turns → summary →
vault task-log note). This module turns that core into an enforced **work
protocol**:

  * :class:`Checkpoint` — the moments a long session should surface a
    compaction candidate (big implementation step done, just before
    test/verification, just before session end, on context-threshold).
  * :func:`compaction_candidates` — given the active checkpoints + an
    estimated context ratio, decide whether compaction should be *offered*.
    Honors the ``YULE_COMPACT_TO_VAULT_ENABLED`` flag for *automatic* offers
    while leaving explicit (operator-requested) compaction always available.
  * :func:`run_compaction_to_vault` — run the core and produce a
    :class:`CompactionReceipt` (session_id / focus / pre_tokens / post_tokens /
    task_log_note_path / committed / warnings).
  * :class:`ClearGuard` — refuses ``/clear`` (history reset) until a vault
    record exists for the session. "vault 기록 전 clear 금지" is a hard rail:
    clearing history before the summary is persisted destroys the audit root.

Hard rails (mirrors ``skills/compact-to-vault.md`` + ``context-compression.md``):
  * Writing the note touches the working tree only; commit/push is the gated
    L3 step (``commit=False`` here always).
  * Protected regions (prompt / decision / synthesis) are never folded — that
    is enforced in the core (:data:`PROTECTED_KINDS`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .context_compaction import (
    ENABLE_ENV,
    CompactionNote,
    CompactionSummary,
    CompactionTurn,
    build_compaction_summary,
    compaction_enabled,
    write_compaction_note,
)

# Context-ratio at/above which an automatic compaction candidate is surfaced,
# even mid-session. Aligns with context-compression.md §3.1 (50% for Claude).
DEFAULT_THRESHOLD_RATIO: float = 0.5


class Checkpoint(str, Enum):
    """A moment in a session where compaction should be considered."""

    BIG_IMPL_DONE = "big_impl_done"          # 큰 구현 단계 종료 시
    PRE_VERIFICATION = "pre_verification"    # 테스트/검증 완료 직전
    SESSION_END = "session_end"              # 세션 종료 직전
    CONTEXT_THRESHOLD = "context_threshold"  # context threshold 도달 시


# Checkpoints that *always* warrant a compaction candidate when reached
# (independent of the context ratio).
_ALWAYS_OFFER: frozenset[Checkpoint] = frozenset(
    {Checkpoint.BIG_IMPL_DONE, Checkpoint.PRE_VERIFICATION, Checkpoint.SESSION_END}
)


@dataclass(frozen=True)
class CompactionCandidate:
    checkpoint: Checkpoint
    reason: str
    auto: bool  # True if auto-trigger (flag on); False = explicit/operator only


@dataclass(frozen=True)
class CompactionReceipt:
    """Proof of one compact→vault run (D/F: shown in execution receipt)."""

    session_id: str
    focus: Optional[str]
    checkpoint: Optional[str]
    enabled: bool
    pre_tokens: int
    post_tokens: int
    saved_tokens: int
    task_log_note_path: Optional[str]
    committed: bool
    warnings: Tuple[str, ...] = ()
    token_source: str = "estimate"  # estimate | live_compact_boundary

    @property
    def status(self) -> str:
        if self.task_log_note_path is None:
            return "not_run"
        return "committed" if self.committed else "written"

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "focus": self.focus,
            "checkpoint": self.checkpoint,
            "enabled": self.enabled,
            "status": self.status,
            "pre_tokens": self.pre_tokens,
            "post_tokens": self.post_tokens,
            "saved_tokens": self.saved_tokens,
            "token_source": self.token_source,
            "task_log_note_path": self.task_log_note_path,
            "committed": self.committed,
            "warnings": list(self.warnings),
        }


def compaction_candidates(
    checkpoints: Sequence[Checkpoint],
    *,
    context_ratio: float = 0.0,
    threshold_ratio: float = DEFAULT_THRESHOLD_RATIO,
    enabled: Optional[bool] = None,
) -> List[CompactionCandidate]:
    """Return compaction candidates for the reached *checkpoints*.

    *enabled* defaults to :func:`compaction_enabled` (the
    ``YULE_COMPACT_TO_VAULT_ENABLED`` flag). When the flag is off, candidates
    are still returned but marked ``auto=False`` — i.e. surfaced for an
    explicit/operator decision, never auto-run. CONTEXT_THRESHOLD only fires
    when *context_ratio* has reached *threshold_ratio*.
    """

    flag_on = compaction_enabled() if enabled is None else enabled
    out: List[CompactionCandidate] = []
    seen: set[Checkpoint] = set()
    for cp in checkpoints:
        if cp in seen:
            continue
        seen.add(cp)
        if cp is Checkpoint.CONTEXT_THRESHOLD:
            if context_ratio < threshold_ratio:
                continue
            reason = (
                f"context ratio {context_ratio:.0%} ≥ threshold {threshold_ratio:.0%}"
            )
        elif cp in _ALWAYS_OFFER:
            reason = f"checkpoint reached: {cp.value}"
        else:  # pragma: no cover - enum exhaustiveness guard
            continue
        out.append(CompactionCandidate(checkpoint=cp, reason=reason, auto=flag_on))
    return out


def run_compaction_to_vault(
    turns: Sequence[CompactionTurn],
    *,
    session_id: str,
    vault_root: Path,
    project: str,
    focus: Optional[str] = None,
    checkpoint: Optional[Checkpoint] = None,
    created_at: Optional[datetime] = None,
    issue: Optional[int] = None,
    original_prompt: Optional[str] = None,
    enabled: Optional[bool] = None,
    compact_boundary: Optional[Any] = None,
) -> Tuple[Optional[CompactionNote], CompactionReceipt]:
    """Run the deterministic compact→vault core and emit a receipt.

    Never commits (``commit=False``); committing is the gated L3 step. If
    *turns* is empty the run is a no-op and the receipt status is ``not_run``.

    *compact_boundary* (optional, duck-typed: ``.parsed`` / ``.pre_tokens`` /
    ``.post_tokens`` / ``.warning``) lets a live ``/compact`` capture override
    the deterministic token estimate. When it is present but unparsed, its
    warning is recorded and the estimate is kept (graceful fallback).
    """

    flag_on = compaction_enabled() if enabled is None else enabled
    warnings: List[str] = []
    if not turns:
        warnings.append("no turns to compact — session empty or unresolved")
        receipt = CompactionReceipt(
            session_id=session_id,
            focus=focus,
            checkpoint=checkpoint.value if checkpoint else None,
            enabled=flag_on,
            pre_tokens=0,
            post_tokens=0,
            saved_tokens=0,
            task_log_note_path=None,
            committed=False,
            warnings=tuple(warnings),
        )
        return None, receipt

    summary: CompactionSummary = build_compaction_summary(
        turns, session_id=session_id, focus=focus
    )
    note = write_compaction_note(
        summary,
        vault_root=vault_root,
        project=project,
        created_at=created_at,
        issue=issue,
        original_prompt=original_prompt,
        commit=False,
    )

    # Token accounting: deterministic estimate, optionally overridden by a live
    # /compact compact_boundary capture (graceful fallback + warning otherwise).
    pre_tokens = summary.pre_tokens
    post_tokens = summary.post_tokens
    token_source = "estimate"
    if compact_boundary is not None:
        if getattr(compact_boundary, "parsed", False):
            pre_tokens = int(getattr(compact_boundary, "pre_tokens"))
            post_tokens = int(getattr(compact_boundary, "post_tokens"))
            token_source = "live_compact_boundary"
        else:
            cb_warning = getattr(compact_boundary, "warning", None)
            warnings.append(
                "live /compact token capture unavailable — using estimate"
                + (f" ({cb_warning})" if cb_warning else "")
            )
    saved_tokens = max(0, pre_tokens - post_tokens)

    receipt = CompactionReceipt(
        session_id=session_id,
        focus=focus,
        checkpoint=checkpoint.value if checkpoint else None,
        enabled=flag_on,
        pre_tokens=pre_tokens,
        post_tokens=post_tokens,
        saved_tokens=saved_tokens,
        task_log_note_path=note.relative_path,
        committed=note.committed,
        warnings=tuple(warnings),
        token_source=token_source,
    )
    return note, receipt


# ---------------------------------------------------------------------------
# /clear (history reset) guard
# ---------------------------------------------------------------------------


class ClearBlockedError(RuntimeError):
    """Raised when ``/clear`` is attempted before the session is vaulted."""


@dataclass(frozen=True)
class ClearDecision:
    session_id: str
    allowed: bool
    reason: str


def evaluate_clear(receipt: Optional[CompactionReceipt], *, session_id: str) -> ClearDecision:
    """Decide whether ``/clear`` (history reset) is allowed for a session.

    Allowed only when a compaction receipt exists and a vault task-log note was
    written (``task_log_note_path`` set). "vault 기록 전 clear 금지" — clearing
    before the summary is persisted would destroy the audit root.
    """

    if receipt is None:
        return ClearDecision(session_id, False, "no compaction receipt — vault record missing")
    if receipt.task_log_note_path is None:
        return ClearDecision(
            session_id, False, "compaction did not write a vault task-log note yet"
        )
    if receipt.session_id != session_id:
        return ClearDecision(
            session_id, False,
            f"receipt session_id={receipt.session_id} != {session_id}",
        )
    return ClearDecision(
        session_id, True,
        f"vaulted at {receipt.task_log_note_path} — safe to clear",
    )


def require_clear_allowed(receipt: Optional[CompactionReceipt], *, session_id: str) -> ClearDecision:
    decision = evaluate_clear(receipt, session_id=session_id)
    if not decision.allowed:
        raise ClearBlockedError(
            f"/clear blocked for session {session_id}: {decision.reason}"
        )
    return decision


__all__ = (
    "ENABLE_ENV",
    "DEFAULT_THRESHOLD_RATIO",
    "Checkpoint",
    "CompactionCandidate",
    "CompactionReceipt",
    "ClearBlockedError",
    "ClearDecision",
    "compaction_candidates",
    "run_compaction_to_vault",
    "evaluate_clear",
    "require_clear_allowed",
)
