"""F16 PR-2 — Domain model for PR merge approval cards.

This module is **pure** — no GitHub API calls, no Discord IO. It
defines:

  * :data:`APPROVAL_KIND_PR_MERGE` — new approval kind constant.
  * :class:`PRMergeProposal` — builder dataclass that the
    :mod:`pr_merge_adapter` will convert into an
    :class:`~yule_engineering.agents.job_queue.approval_worker.ApprovalRequest`.
  * :func:`render_pr_merge_summary` — markdown summary card body.
  * :class:`PRMergeGateResult` — output of the 5-step merge gate.
  * :func:`evaluate_merge_gate` — pure decider given a snapshot of PR
    status. The caller is responsible for fetching live GitHub state.
  * :func:`parse_pr_merge_reply_intent` — adds ``REVISE_AND_REPEAT``
    to the base :class:`ApprovalIntent` vocabulary.

The split keeps domain logic test-able without GitHub credentials
and lets the adapter / live_client / approval_reply_router layers
import this module without circular dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence, Tuple, Union

from .approval_reply import (
    ApprovalIntent,
    find_replyable_approval,
    parse_approval_intent,
)
from .approval_worker import ApprovalRequest
from .store import JobQueue


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


APPROVAL_KIND_PR_MERGE: str = "pr_merge"
"""``ApprovalRequest.approval_kind`` value for PR merge approval cards.

Distinct from :data:`APPROVAL_KIND_ENGINEERING_WRITE` (which covers
"please write code for me") because the operator vocabulary differs:
PR merge has its own "수정 후 다시" intent and 5-step gate that does
not apply to a code-write request.
"""


class PRMergeReplyIntent(str, Enum):
    """Extended reply vocabulary for PR merge cards.

    Inherits the base 4 values (APPROVE / REJECT / HOLD / UNCLEAR)
    from :class:`ApprovalIntent` so any caller can keep using the
    smaller enum if they don't care about REVISE_AND_REPEAT.
    """

    APPROVE = "approve"
    REJECT = "reject"
    HOLD = "hold"
    UNCLEAR = "unclear"
    REVISE_AND_REPEAT = "revise_and_repeat"


# Phrases that mean "merge 보류 — PR 수정 후 다시 카드를 받을게".
# Distinct from HOLD (보류 = 같은 카드 유효) because revise invalidates
# the current head_sha — a follow-up commit will produce a new card.
_REVISE_PHRASES: frozenset = frozenset(
    {
        "수정 후 다시",
        "수정후다시",
        "수정 후 다시 보자",
        "수정 후에 다시",
        "수정후 다시",
        "수정후에 다시",
        "다시 작성 후",
        "다시 작성 후 보자",
        "revise",
        "revise and repeat",
        "revise then merge",
        "fix and reopen",
        "fix then merge",
    }
)


_NORMALIZE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub(" ", (text or "").lower()).strip()


def parse_pr_merge_reply_intent(text: str) -> PRMergeReplyIntent:
    """Classify a PR-merge reply into one of 5 intents.

    "수정 후 다시" (and English equivalents) maps to
    :attr:`PRMergeReplyIntent.REVISE_AND_REPEAT`. Everything else
    delegates to :func:`parse_approval_intent` so the standard
    APPROVE / REJECT / HOLD vocabulary continues to work.
    """

    normalised = _normalize(text)
    for phrase in _REVISE_PHRASES:
        if phrase in normalised:
            return PRMergeReplyIntent.REVISE_AND_REPEAT

    base_intent = parse_approval_intent(text)
    return PRMergeReplyIntent(base_intent.value)


# ---------------------------------------------------------------------------
# Proposal dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PRMergeProposal:
    """Snapshot of a PR at the moment we propose to merge it.

    All fields are deterministic so the same PR + same head_sha + same
    check_runs produce a stable summary card (the approval worker's
    dedup key fires correctly on re-post).

    The ``head_sha`` participates in the 5-step gate's race check:
    if a new commit lands after the card is posted, the gate refuses
    to merge.
    """

    repo: str
    pr_number: int
    pr_title: str
    pr_url: str
    head_sha: str
    base_branch: str
    draft: bool
    mergeable_state: str
    summary_md: str
    scope_labels: Tuple[str, ...] = field(default_factory=tuple)
    risk: str = "UNKNOWN"
    check_runs_summary: str = ""
    branch_protection_summary: str = ""
    body_excerpt: str = ""
    requested_by: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_extra(self) -> Mapping[str, Any]:
        """Serialise into ``ApprovalRequest.extra`` payload (JSON-friendly)."""

        return {
            "repo": self.repo,
            "pr_number": int(self.pr_number),
            "pr_title": self.pr_title,
            "pr_url": self.pr_url,
            "head_sha": self.head_sha,
            "base_branch": self.base_branch,
            "draft": bool(self.draft),
            "mergeable_state": self.mergeable_state,
            "scope_labels": list(self.scope_labels),
            "risk": self.risk,
            "check_runs_summary": self.check_runs_summary,
            "branch_protection_summary": self.branch_protection_summary,
            "body_excerpt": self.body_excerpt,
            "requested_by": self.requested_by,
            **{k: v for k, v in (self.extra or {}).items()
               if k not in {"repo", "pr_number"}},
        }

    @classmethod
    def from_extra(cls, payload: Mapping[str, Any]) -> "PRMergeProposal":
        """Reverse of :meth:`to_extra` — lift a queue-row payload back."""

        payload = payload or {}
        return cls(
            repo=str(payload.get("repo") or ""),
            pr_number=int(payload.get("pr_number") or 0),
            pr_title=str(payload.get("pr_title") or ""),
            pr_url=str(payload.get("pr_url") or ""),
            head_sha=str(payload.get("head_sha") or ""),
            base_branch=str(payload.get("base_branch") or ""),
            draft=bool(payload.get("draft", False)),
            mergeable_state=str(payload.get("mergeable_state") or "unknown"),
            summary_md=str(payload.get("summary_md") or ""),
            scope_labels=tuple(payload.get("scope_labels") or ()),
            risk=str(payload.get("risk") or "UNKNOWN"),
            check_runs_summary=str(payload.get("check_runs_summary") or ""),
            branch_protection_summary=str(
                payload.get("branch_protection_summary") or ""
            ),
            body_excerpt=str(payload.get("body_excerpt") or ""),
            requested_by=str(payload.get("requested_by") or ""),
            extra={
                k: v
                for k, v in payload.items()
                if k
                not in {
                    "repo",
                    "pr_number",
                    "pr_title",
                    "pr_url",
                    "head_sha",
                    "base_branch",
                    "draft",
                    "mergeable_state",
                    "summary_md",
                    "scope_labels",
                    "risk",
                    "check_runs_summary",
                    "branch_protection_summary",
                    "body_excerpt",
                    "requested_by",
                }
            },
        )


# ---------------------------------------------------------------------------
# Summary card rendering
# ---------------------------------------------------------------------------


_BODY_EXCERPT_MAX = 280


def build_body_excerpt(body: Optional[str]) -> str:
    """Trim PR body to a short excerpt for the summary card.

    Keeps the first ``_BODY_EXCERPT_MAX`` characters, collapses
    whitespace, and appends an ellipsis when truncated.
    """

    text = (body or "").strip()
    if not text:
        return ""
    text = _NORMALIZE_RE.sub(" ", text)
    if len(text) <= _BODY_EXCERPT_MAX:
        return text
    return text[:_BODY_EXCERPT_MAX].rstrip() + "…"


def render_pr_merge_summary(proposal: PRMergeProposal) -> str:
    """Render the markdown summary card body for *proposal*.

    Layout matches ``docs/pr-approval-merge.md §3``:

      Header line — emoji + PR number + title.
      "무엇이 바뀌나" — body excerpt.
      "영향 범위" — scope_labels joined.
      "위험도" — risk string.
      Tests / CI / Branch protection — check_runs_summary +
      branch_protection_summary.
      Link.

    The function is deterministic — same proposal yields same text —
    so approval_worker's dedup key fires consistently on re-post.
    """

    lines: list[str] = []

    header = f"🔀 PR 머지 승인 — #{proposal.pr_number}"
    if proposal.pr_title:
        header = f"{header} {proposal.pr_title}"
    lines.append(header)
    lines.append("")

    if proposal.body_excerpt:
        lines.append("📋 무엇이 바뀌나")
        lines.append(proposal.body_excerpt)
        lines.append("")

    if proposal.scope_labels:
        scope = " / ".join(proposal.scope_labels)
        lines.append(f"🎯 영향 범위: {scope}")

    risk_label = {
        "LOW": "LOW",
        "MEDIUM": "MEDIUM",
        "HIGH": "HIGH",
    }.get((proposal.risk or "").upper(), proposal.risk or "UNKNOWN")
    risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(
        risk_label, "⚠️"
    )
    lines.append(f"{risk_emoji} 위험도: {risk_label}")

    if proposal.check_runs_summary:
        lines.append(proposal.check_runs_summary)
    if proposal.branch_protection_summary:
        lines.append(proposal.branch_protection_summary)

    if proposal.pr_url:
        lines.append("")
        lines.append(f"🔗 {proposal.pr_url}")
        lines.append("")

    lines.append("응답 어휘: 승인 / 거절 / 수정 후 다시 / 머지 보류")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5-step merge gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PRMergeStatusSnapshot:
    """Live PR + check-runs + branch-protection state at gate time.

    The caller (``pr_merge_executor``) fetches the live values via
    ``live_client.get_pull_request`` / ``list_check_runs`` /
    ``get_branch_protection`` and packs them here. We separate the
    snapshot from gate evaluation so the gate is fully unit-testable
    without GitHub access.
    """

    draft: bool
    mergeable: Optional[bool]
    mergeable_state: str
    head_sha: str
    check_conclusions: Tuple[str, ...]
    required_status_checks: Tuple[str, ...] = field(default_factory=tuple)
    required_approving_reviews: int = 0
    actual_approving_reviews: int = 0
    branch_protection_available: bool = True
    """``False`` when ``get_branch_protection`` raised 401/403/404."""


@dataclass(frozen=True)
class PRMergeGateResult:
    """Output of :func:`evaluate_merge_gate`."""

    allowed: bool
    failed_step: Optional[str] = None
    reason: str = ""
    checks_summary: str = ""


_GATE_STEPS: Tuple[str, ...] = (
    "draft",
    "mergeable",
    "checks_green",
    "branch_protection",
    "sha_race",
)


def evaluate_merge_gate(
    proposal: PRMergeProposal,
    snapshot: PRMergeStatusSnapshot,
) -> PRMergeGateResult:
    """Run the 5-step gate. Returns the first failure or "allowed".

    Steps (matching ``docs/pr-approval-merge.md §5``):

      1. ``draft != True`` — draft PR refuses merge.
      2. ``mergeable_state == "clean"`` — conflicts / behind base reject.
      3. all ``check_runs.conclusion == "success"`` (or "neutral").
      4. branch protection rules — required_status_checks present in
         passed checks, required_approving_reviews satisfied. If
         ``branch_protection_available is False`` we refuse (safe).
      5. ``snapshot.head_sha == proposal.head_sha`` (race protection).

    Note: callers wanting an audit trail should record each step's
    result regardless of which step fires the failure — this function
    returns only the *first* failure.
    """

    # 1. draft
    if snapshot.draft:
        return PRMergeGateResult(
            allowed=False,
            failed_step="draft",
            reason="draft PR — 승인 받아도 merge 거부",
        )

    # 2. mergeable_state
    state = (snapshot.mergeable_state or "").lower()
    if state != "clean":
        # mergeable can be True even when state != clean (e.g. "behind",
        # "unstable") — we still refuse to be conservative.
        reason = f"mergeable_state={state!r} (clean 아님)"
        return PRMergeGateResult(
            allowed=False, failed_step="mergeable", reason=reason
        )
    if snapshot.mergeable is False:
        return PRMergeGateResult(
            allowed=False,
            failed_step="mergeable",
            reason="mergeable=False — conflict 또는 base 변경",
        )

    # 3. check runs
    conclusions = tuple(c.lower() for c in (snapshot.check_conclusions or ()))
    if not conclusions:
        return PRMergeGateResult(
            allowed=False,
            failed_step="checks_green",
            reason="check_runs 결과 없음 — CI 미실행 또는 권한 부족",
            checks_summary="no check runs",
        )
    failing = [c for c in conclusions if c not in {"success", "neutral", "skipped"}]
    if failing:
        return PRMergeGateResult(
            allowed=False,
            failed_step="checks_green",
            reason=f"check_runs not green ({len(failing)} failing)",
            checks_summary=", ".join(sorted(set(failing))),
        )

    # 4. branch protection
    if not snapshot.branch_protection_available:
        return PRMergeGateResult(
            allowed=False,
            failed_step="branch_protection",
            reason="branch protection 조회 실패 — 권한 부족, 안전 측 거부",
        )
    if snapshot.actual_approving_reviews < snapshot.required_approving_reviews:
        return PRMergeGateResult(
            allowed=False,
            failed_step="branch_protection",
            reason=(
                f"required reviews {snapshot.required_approving_reviews}, "
                f"actual {snapshot.actual_approving_reviews}"
            ),
        )

    # 5. sha race
    if snapshot.head_sha and proposal.head_sha and snapshot.head_sha != proposal.head_sha:
        return PRMergeGateResult(
            allowed=False,
            failed_step="sha_race",
            reason=(
                "head_sha 변경됨: card 시점 "
                f"{proposal.head_sha[:7]}, 현재 {snapshot.head_sha[:7]}"
            ),
        )

    return PRMergeGateResult(
        allowed=True,
        reason="모든 gate 통과",
        checks_summary=f"{len(conclusions)} checks green",
    )


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


PR_MERGE_AUDIT_KEY: str = "pr_merge_audit"
"""Key under ``session.extra`` where the append-only audit list lives."""


def make_audit_entry(stage: str, **fields: Any) -> Mapping[str, Any]:
    """Build one audit entry with the canonical ``stage`` key.

    Stages used by the loop:

      * ``card_posted`` — approval card posted to Discord.
      * ``approved`` / ``rejected`` / ``hold`` / ``revise_and_repeat``.
      * ``merge_gate_check`` — gate result (allowed / failed_step / reason).
      * ``merge_disabled`` — ``YULE_GITHUB_MERGE_ENABLED=false``.
      * ``merge_executed`` — successful merge.
      * ``merge_failed`` — GitHub API rejected the merge call.
    """

    entry: dict = {"stage": stage}
    entry.update(fields)
    return entry


# ---------------------------------------------------------------------------
# Reply handler (executor-injected — live merge call happens in commit 10)
# ---------------------------------------------------------------------------


# The merge executor signature. Implementations build a
# :class:`PRMergeStatusSnapshot` from live GitHub state, run
# :func:`evaluate_merge_gate`, and only on success call
# ``live_client.merge_pull_request``. The function is intentionally
# typed loosely so a sync stub (in tests) and a real async live client
# wrapper (in production) both fit.
PRMergeExecutor = Callable[
    ["PRMergeReplyDispatch"], Union[Mapping[str, Any], Awaitable[Mapping[str, Any]]]
]


@dataclass(frozen=True)
class PRMergeReplyDispatch:
    """Bundle passed to the merge executor when intent=APPROVE.

    The executor reads ``proposal`` for repo/pr_number/head_sha and the
    full PR shape, ``approved_by``/``approved_at`` for audit entries,
    and returns a mapping that the handler stores under
    ``audit["merge_result"]``.
    """

    proposal: "PRMergeProposal"
    approval_job_id: str
    approved_by: str
    approved_at: str
    source_message_id: Optional[int]


@dataclass(frozen=True)
class PRMergeReplyResult:
    """Output of :func:`handle_pr_merge_approval_reply`.

    Mirrors :class:`ApprovalReplyOutcome` for symmetry but stays in
    its own dataclass so PR-merge-specific fields (e.g.
    ``merge_disabled``, ``gate_failed_step``) can grow without
    polluting the obsidian path.
    """

    intent: PRMergeReplyIntent
    approval_job_id: Optional[str] = None
    proposal: Optional["PRMergeProposal"] = None
    merge_disabled: bool = False
    gate_failed_step: Optional[str] = None
    gate_reason: str = ""
    merge_result: Optional[Mapping[str, Any]] = None
    rejection_recorded: bool = False
    skipped_reason: Optional[str] = None
    audit: Mapping[str, Any] = field(default_factory=dict)


async def handle_pr_merge_approval_reply(
    *,
    queue: JobQueue,
    text: str,
    session_id: str,
    approved_by: str,
    source_message_id: Optional[int] = None,
    source_thread_id: Optional[int] = None,
    approved_at: str = "",
    merge_executor: Optional[PRMergeExecutor] = None,
    ready_for_review_action: Optional[Callable[..., Any]] = None,
) -> PRMergeReplyResult:
    """Route a user's reply for a PR merge approval card.

    Pure-ish: no Discord, no GitHub call. Side effects:

      * Reads the queue to locate the matching ``approval_post`` row
        whose ``approval_kind == APPROVAL_KIND_PR_MERGE``.
      * On ``intent == APPROVE`` and a registered ``merge_executor``,
        calls the executor (which runs the gate + GitHub merge call).
      * Returns a structured :class:`PRMergeReplyResult` the router /
        bot can render and audit.

    When ``merge_executor is None`` the handler returns with
    ``merge_disabled=True``. This is the safe disabled seam — useful
    in CI / staging where ``YULE_GITHUB_MERGE_ENABLED=false`` would
    still trigger the gate but never the API call.
    """

    intent = parse_pr_merge_reply_intent(text)

    if intent in (PRMergeReplyIntent.HOLD, PRMergeReplyIntent.UNCLEAR):
        return PRMergeReplyResult(
            intent=intent, skipped_reason="intent_not_actionable"
        )

    approval_job = find_replyable_approval(
        queue=queue,
        session_id=session_id,
        approval_kind=APPROVAL_KIND_PR_MERGE,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
    )
    if approval_job is None:
        return PRMergeReplyResult(
            intent=intent, skipped_reason="no_matching_approval"
        )

    payload = approval_job.payload or {}
    request = ApprovalRequest.from_payload(payload)
    if request.approval_kind != APPROVAL_KIND_PR_MERGE:
        return PRMergeReplyResult(
            intent=intent,
            approval_job_id=approval_job.job_id,
            skipped_reason="approval_kind_mismatch",
        )

    proposal = PRMergeProposal.from_extra(request.extra or {})

    if intent == PRMergeReplyIntent.REJECT:
        return PRMergeReplyResult(
            intent=intent,
            approval_job_id=approval_job.job_id,
            proposal=proposal,
            rejection_recorded=True,
            audit={
                "rejected_by": approved_by,
                "rejected_at": approved_at,
                "source_message_id": source_message_id,
            },
        )

    if intent == PRMergeReplyIntent.REVISE_AND_REPEAT:
        # Cards are invalidated by the next commit (sha race in gate).
        # The router may optionally post a review comment when
        # ``YULE_PR_MERGE_REVIEW_COMMENT_ENABLED=true`` — that wire-up
        # is in commit 10. For this commit we only audit the intent.
        return PRMergeReplyResult(
            intent=intent,
            approval_job_id=approval_job.job_id,
            proposal=proposal,
            audit={
                "revise_requested_by": approved_by,
                "revise_at": approved_at,
                "source_message_id": source_message_id,
            },
        )

    # APPROVE — invoke the merge executor if registered.
    if merge_executor is None:
        return PRMergeReplyResult(
            intent=intent,
            approval_job_id=approval_job.job_id,
            proposal=proposal,
            merge_disabled=True,
            audit={
                "approved_by": approved_by,
                "approved_at": approved_at,
                "reason": "merge_executor_not_registered",
            },
        )

    dispatch = PRMergeReplyDispatch(
        proposal=proposal,
        approval_job_id=approval_job.job_id,
        approved_by=approved_by,
        approved_at=approved_at,
        source_message_id=source_message_id,
    )

    # P1-Q — draft escalation branch.  proposal 이 draft_escalation 플래그를
    # 가지고 있고 ready_for_review_action 이 wiring 돼 있으면 사용자 승인
    # 직후 draft 해제 후 gate 재실행.  ready_for_review 실패면 그 사유
    # 그대로 surface.
    is_draft_escalation = bool(
        (proposal.extra or {}).get("draft_escalation")
    )
    if is_draft_escalation and ready_for_review_action is not None:
        try:
            ready_action_result = ready_for_review_action(
                repo=proposal.repo, pr_number=proposal.pr_number
            )
            if hasattr(ready_action_result, "__await__"):
                ready_action_result = await ready_action_result  # type: ignore[assignment]
        except Exception as exc:  # noqa: BLE001
            return PRMergeReplyResult(
                intent=intent,
                approval_job_id=approval_job.job_id,
                proposal=proposal,
                gate_failed_step="draft_ready_for_review",
                gate_reason=str(exc)[:240],
                audit={
                    "approved_by": approved_by,
                    "approved_at": approved_at,
                    "draft_escalation": True,
                    "ready_for_review_error": type(exc).__name__,
                    "ready_for_review_message": str(exc)[:240],
                },
            )
        # gate rerun — merge_executor 가 새 PR state (이제 draft=False) 로
        # snapshot 빌드한 뒤 gate evaluate.  성공하면 merge_sha 반환.

    raw = merge_executor(dispatch)
    if hasattr(raw, "__await__"):
        raw = await raw  # type: ignore[assignment]
    result: Mapping[str, Any] = dict(raw or {})

    gate_failed = result.get("gate_failed_step")
    return PRMergeReplyResult(
        intent=intent,
        approval_job_id=approval_job.job_id,
        proposal=proposal,
        merge_disabled=bool(result.get("merge_disabled", False)),
        gate_failed_step=gate_failed if isinstance(gate_failed, str) else None,
        gate_reason=str(result.get("gate_reason") or ""),
        merge_result=result if result.get("merge_sha") else None,
        audit={
            "approved_by": approved_by,
            "approved_at": approved_at,
            "result": dict(result),
        },
    )


__all__ = (
    "APPROVAL_KIND_PR_MERGE",
    "PRMergeReplyIntent",
    "PRMergeProposal",
    "PRMergeStatusSnapshot",
    "PRMergeGateResult",
    "PRMergeReplyDispatch",
    "PRMergeReplyResult",
    "PRMergeExecutor",
    "PR_MERGE_AUDIT_KEY",
    "build_body_excerpt",
    "render_pr_merge_summary",
    "evaluate_merge_gate",
    "handle_pr_merge_approval_reply",
    "parse_pr_merge_reply_intent",
    "make_audit_entry",
)
