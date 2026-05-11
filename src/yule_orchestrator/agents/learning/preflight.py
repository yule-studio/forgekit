"""Preflight judgement seam — F2 / issue #89.

Cross-session companion to
:mod:`yule_orchestrator.agents.lifecycle.preflight_judgement`. The
round-1 seam evaluates the session-extra ledger; this F2 seam queries
the durable :class:`MistakeLedger` so the hook can fire at the very
top of ``coding_executor_worker._run_pipeline`` — before any session
is hydrated.

The verdict shape mirrors Acceptance Criteria 3:

  * ``level``: :class:`BlockerLevel` (ADVISORY / WARNING / BLOCK)
  * ``reason``: short Korean string summarising the matched mistakes
  * ``suggested_action``: caller-facing instruction (주의 / 재검토 권장
    / needs_approval 로 라우팅)
  * ``matched_mistakes``: tuple of :class:`MistakeRecord` the verdict
    was derived from
  * ``evaluated_at``: ISO-8601 UTC timestamp

A BLOCK verdict carries an explicit ``needs_approval`` recommendation
in the suggested action — the caller is expected to route the work
to the existing approval lane instead of executing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence, Tuple

from .mistake_ledger import (
    BlockerLevel,
    MistakeLedger,
    MistakeRecord,
    max_blocker_level,
)


_SUGGESTED_ACTIONS: Mapping[BlockerLevel, str] = {
    BlockerLevel.ADVISORY: "주의",
    BlockerLevel.WARNING: "재검토 권장",
    BlockerLevel.BLOCK: "needs_approval 로 라우팅",
}


_LEVEL_LABEL: Mapping[BlockerLevel, str] = {
    BlockerLevel.ADVISORY: "advisory",
    BlockerLevel.WARNING: "warning",
    BlockerLevel.BLOCK: "block",
}


# Default similarity threshold for the preflight lookup. We use a
# slightly *lower* bar than the ad-hoc :meth:`MistakeLedger.find_similar`
# default because the preflight hook deliberately errs on the side of
# surfacing — a false advisory is fine, a missed block is not.
DEFAULT_PREFLIGHT_SIMILARITY: float = 0.55

# How many matched mistakes we summarise in the verdict reason.
REASON_SUMMARY_LIMIT: int = 3


@dataclass(frozen=True)
class PreflightVerdict:
    """Result of one preflight evaluation.

    ``level`` is the highest :class:`BlockerLevel` among
    ``matched_mistakes``; when the lookup matches nothing the level
    is ADVISORY and the reason explains the lookup found no match
    (so audit readers can tell "no signal" apart from "no lookup").

    ``recommend_needs_approval`` is True iff the verdict is BLOCK —
    callers can check this single property without re-parsing the
    suggested action string.
    """

    level: BlockerLevel
    reason: str
    suggested_action: str
    matched_mistakes: Tuple[MistakeRecord, ...]
    evaluated_at: str
    role: str = ""
    task_signature: str = ""

    @property
    def is_block(self) -> bool:
        return self.level == BlockerLevel.BLOCK

    @property
    def is_advisory(self) -> bool:
        return self.level == BlockerLevel.ADVISORY

    @property
    def recommend_needs_approval(self) -> bool:
        """True iff the caller MUST route to the approval lane.

        Mirrors the issue #89 hard rail: BLOCK never auto-executes.
        """

        return self.level == BlockerLevel.BLOCK

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "level": self.level.value,
            "reason": self.reason,
            "suggested_action": self.suggested_action,
            "matched_mistake_ids": [m.id for m in self.matched_mistakes],
            "evaluated_at": self.evaluated_at,
            "role": self.role,
            "task_signature": self.task_signature,
            "recommend_needs_approval": self.recommend_needs_approval,
        }


def judge_preflight(
    *,
    role: str,
    task_signature: str,
    ledger: MistakeLedger,
    recent_audit: Optional[Sequence[Mapping[str, Any]]] = None,
    similarity_threshold: float = DEFAULT_PREFLIGHT_SIMILARITY,
    now: Optional[datetime] = None,
) -> PreflightVerdict:
    """Decide whether *role* should proceed with *task_signature*.

    Deterministic with respect to the ledger state: the same role +
    signature + ledger snapshot always yields the same verdict
    (matching tests can therefore assert on level / reason / suggested
    action without snapshotting timestamps — ``evaluated_at`` is the
    only timestamp and the caller can pass ``now=`` to pin it).

    ``recent_audit`` is reserved for future producers that want to
    cross-check the verdict against a short rolling audit slice. The
    round-1 implementation accepts and ignores it — the contract is
    pinned now so a follow-up can plug in without changing call sites.

    Empty ledger (or no role matches) → ADVISORY verdict with a
    "no recurring mistake" reason. Callers can branch on
    :attr:`PreflightVerdict.matched_mistakes` to skip surfacing the
    advisory when nothing matched.
    """

    role_value = str(role or "").strip()
    signature_value = str(task_signature or "").strip()
    evaluated_at = _utc_iso(now)

    if not role_value:
        return PreflightVerdict(
            level=BlockerLevel.ADVISORY,
            reason="role 미지정 — preflight 평가 생략",
            suggested_action=_SUGGESTED_ACTIONS[BlockerLevel.ADVISORY],
            matched_mistakes=(),
            evaluated_at=evaluated_at,
            role="",
            task_signature=signature_value,
        )
    if not signature_value:
        return PreflightVerdict(
            level=BlockerLevel.ADVISORY,
            reason="task_signature 미지정 — preflight 평가 생략",
            suggested_action=_SUGGESTED_ACTIONS[BlockerLevel.ADVISORY],
            matched_mistakes=(),
            evaluated_at=evaluated_at,
            role=role_value,
            task_signature="",
        )

    matches = ledger.find_similar(
        role=role_value,
        signature=signature_value,
        threshold=similarity_threshold,
    )
    if not matches:
        return PreflightVerdict(
            level=BlockerLevel.ADVISORY,
            reason="이전 실수 매칭 없음 — 진행 가능",
            suggested_action=_SUGGESTED_ACTIONS[BlockerLevel.ADVISORY],
            matched_mistakes=(),
            evaluated_at=evaluated_at,
            role=role_value,
            task_signature=signature_value,
        )

    level = max_blocker_level(*(m.blocker_level for m in matches))
    reason = _format_reason(level=level, matches=matches)
    suggested = _SUGGESTED_ACTIONS[level]
    return PreflightVerdict(
        level=level,
        reason=reason,
        suggested_action=suggested,
        matched_mistakes=tuple(matches),
        evaluated_at=evaluated_at,
        role=role_value,
        task_signature=signature_value,
    )


# ---------------------------------------------------------------------------
# Pipeline helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightHookResult:
    """Helper bundle returned by :func:`preflight_pipeline_hook`.

    The helper is the wiring seam noted in issue #89 Acceptance
    Criteria 6 — actual integration into ``coding_executor_worker``
    /  ``role_take_worker`` is a follow-up PR. We land the helper now
    so the contract is pinned and exercised by regression tests.

    ``should_proceed`` is False iff the verdict is BLOCK. ``stamp``
    is the dict the caller can splice into ``session.extra`` or the
    job metadata for audit purposes.
    """

    verdict: PreflightVerdict
    should_proceed: bool
    stamp: Mapping[str, Any]


def preflight_pipeline_hook(
    *,
    role: str,
    task_signature: str,
    ledger: MistakeLedger,
    recent_audit: Optional[Sequence[Mapping[str, Any]]] = None,
    similarity_threshold: float = DEFAULT_PREFLIGHT_SIMILARITY,
    now: Optional[datetime] = None,
) -> PreflightHookResult:
    """Convenience wrapper for pipeline producers.

    Returns a :class:`PreflightHookResult` with the verdict, a
    boolean the caller can branch on, and a small ``stamp`` dict
    suitable for the job metadata / session extra surface.

    A BLOCK verdict sets ``should_proceed=False`` — the caller is
    expected to route the work to the existing ``needs_approval``
    lane (the verdict's ``suggested_action`` documents that).
    """

    verdict = judge_preflight(
        role=role,
        task_signature=task_signature,
        ledger=ledger,
        recent_audit=recent_audit,
        similarity_threshold=similarity_threshold,
        now=now,
    )
    stamp = {
        "preflight": dict(verdict.to_payload()),
    }
    return PreflightHookResult(
        verdict=verdict,
        should_proceed=not verdict.is_block,
        stamp=stamp,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _format_reason(
    *,
    level: BlockerLevel,
    matches: Sequence[MistakeRecord],
) -> str:
    top = list(matches[:REASON_SUMMARY_LIMIT])
    label = _LEVEL_LABEL.get(level, level.value.lower())
    total = len(matches)
    bullets: list[str] = []
    for record in top:
        bullets.append(
            f"`{record.pattern}` (×{record.occurrences}, {record.blocker_level.value})"
        )
    suffix = ""
    if total > len(top):
        suffix = f" 외 {total - len(top)}건"
    joined = ", ".join(bullets) if bullets else "매칭된 실수 없음"
    return (
        f"{label} — 동일 role 의 과거 실수 {total}건 중 상위 매칭: "
        f"{joined}{suffix}"
    )


def _utc_iso(now: Optional[datetime]) -> str:
    if now is None:
        now = datetime.now(tz=timezone.utc)
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


__all__ = (
    "DEFAULT_PREFLIGHT_SIMILARITY",
    "PreflightHookResult",
    "PreflightVerdict",
    "judge_preflight",
    "preflight_pipeline_hook",
)
