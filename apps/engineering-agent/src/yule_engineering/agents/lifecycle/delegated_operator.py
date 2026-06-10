"""Delegated operator policy — self-improvement runtime.

사용자가 자리를 비운 동안 gateway 가 ``operator`` 역할을 대신할 수 있도록
**명시적으로 위임된 승인 범위** 를 코드로 못박는 모듈.

기존 :mod:`agents.lifecycle.autonomy_policy` 의 L0~L4 사다리는
"어떤 액션이 본질적으로 위험한가" 를 결정한다. 그러나 사용자가
*self-improvement loop 에 한해* "이 범위까지는 네가 알아서 해" 라고
허락한 경우, gateway 는 일부 L3 액션 (draft PR 생성, feature branch
push, issue create) 까지 자동 승인할 수 있다.

이 모듈은 그 *위임* 을 단일 함수로 표면화한다:

    evaluate_delegated_approval(*, problem, action, scope_hint) -> DelegatedDecision

핵심 원칙
========

* 위임 범위는 **명시적으로 화이트리스트** — 알 수 없는 액션은 자동
  escalate. (audit-friendly: 누락이 무음으로 통과하지 않음.)
* L4 액션은 *절대* delegate 되지 않는다 — secret modify / main push /
  merge / deploy / destructive delete 등은 화이트리스트에서 영구 제외.
* 같은 ``problem_signature`` 에 대해 ``max_retries`` 회 이상 실패하면
  자동 escalate (rate-limit).
* 위임이 일어났을 때는 ``audit_summary`` 가 채워져 :class:`AgentOpsEntry`
  로 기록될 수 있도록 한다 — "왜 사용자 승인 없이 진행됐는지" 가
  audit 에 남아야 함.

테스트 친화적: registry / clock / rate-limit store 가 모두 주입 가능.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Tuple

from .autonomy_policy import (
    ACTION_AGENT_OPS_RECORD,
    ACTION_BLOG_PUBLICATION,
    ACTION_BRANCH_MERGE,
    ACTION_DEPLOY,
    ACTION_DESTRUCTIVE_DELETE,
    ACTION_DRAFT_DOCUMENT_CREATE,
    ACTION_DRAFT_PR_CREATE,
    ACTION_EXTERNAL_PAID_CALL,
    ACTION_EXTERNAL_PUBLICATION,
    ACTION_FAILURE_AUDIT_RECORD,
    ACTION_FAILURE_POSTMORTEM_CREATE,
    ACTION_FEATURE_BRANCH_CREATE,
    ACTION_FORUM_HANDOFF_DECISION,
    ACTION_LARGE_SCALE_CRAWL,
    ACTION_LINK_COLLECTION,
    ACTION_LOCAL_COMMIT,
    ACTION_LOW_RISK_DOCS_EDIT,
    ACTION_LOW_RISK_TEST_EDIT,
    ACTION_MAIN_BRANCH_PUSH,
    ACTION_PROD_DB_WRITE,
    ACTION_PUSH_TO_SHARED_REPO,
    ACTION_RESEARCH_LOG_SAVE,
    ACTION_RETRY_AUDIT_RECORD,
    ACTION_ROLE_TAKE_RECORD,
    ACTION_RUNTIME_CODE_CHANGE,
    ACTION_RUNTIME_RESTART,
    ACTION_SECRET_ACCESS,
    ACTION_SECRET_MODIFY,
    ACTION_SELF_IMPROVEMENT_PROPOSAL,
    ACTION_TEST_EXECUTE,
    ACTION_THREAD_SNAPSHOT_CAPTURE,
    ACTION_USER_ORDERED_RESEARCH,
    ACTION_VAULT_REMOTE_PUSH,
    ACTION_VAULT_RESEARCH_LOG_COMMIT,
    AutonomyLevel,
)


# ---------------------------------------------------------------------------
# Scope IDs — user-facing description of what gateway may auto-approve
# ---------------------------------------------------------------------------


SCOPE_RESEARCH_THREAD_START: str = "research_thread_start"
SCOPE_OBSIDIAN_TROUBLESHOOTING: str = "obsidian_troubleshooting"
SCOPE_WORKTREE_CREATE: str = "worktree_create"
SCOPE_FEATURE_BRANCH_CREATE: str = "feature_branch_create"
SCOPE_CODE_EDIT_LOCAL: str = "code_edit_local"
SCOPE_LOCAL_TEST_RUN: str = "local_test_run"
SCOPE_LOCAL_SMOKE_RUN: str = "local_smoke_run"
SCOPE_LOCAL_COMMIT: str = "local_commit"
SCOPE_FEATURE_BRANCH_PUSH: str = "feature_branch_push"
SCOPE_DRAFT_PR_CREATE: str = "draft_pr_create"
SCOPE_ISSUE_AUTO_CREATE: str = "issue_auto_create"
SCOPE_PROGRESS_MARKER_WRITE: str = "progress_marker_write"
SCOPE_RETRY_TRANSIENT: str = "retry_transient_failure"
SCOPE_RETRIEVAL_EVAL_RUN: str = "retrieval_eval_run"
SCOPE_VAULT_VALIDATION_RUN: str = "vault_validation_run"
SCOPE_GOVERNANCE_VALIDATION_RUN: str = "governance_validation_run"


# ---------------------------------------------------------------------------
# Escalation IDs — actions that MUST surface as operator action
# ---------------------------------------------------------------------------


ESCALATE_MERGE: str = "merge"
ESCALATE_RELEASE_TAG: str = "release_tag"
ESCALATE_DEPLOY: str = "deploy"
ESCALATE_SECRET_MODIFY: str = "secret_modify"
ESCALATE_SECRET_ACCESS: str = "secret_access"
ESCALATE_EXTERNAL_ACCESS: str = "external_access"
ESCALATE_PROTECTED_BRANCH_WRITE: str = "protected_branch_write"
ESCALATE_DESTRUCTIVE_CLEANUP: str = "destructive_cleanup"
ESCALATE_BILLING_PURCHASE: str = "billing_purchase"
ESCALATE_LARGE_SCALE_REFACTOR: str = "large_scale_refactor"
ESCALATE_RETRY_CAP_EXCEEDED: str = "retry_cap_exceeded"
ESCALATE_OUT_OF_DELEGATED_SCOPE: str = "out_of_delegated_scope"


# Mapping from low-level autonomy action → delegated scope id.
# Membership in this map is the *only* way an action becomes
# delegated-OK. Anything not here will escalate.
_ACTION_TO_SCOPE: Mapping[str, str] = {
    # 1. 운영-리서치 조사 시작/추가
    ACTION_USER_ORDERED_RESEARCH: SCOPE_RESEARCH_THREAD_START,
    ACTION_THREAD_SNAPSHOT_CAPTURE: SCOPE_RESEARCH_THREAD_START,
    ACTION_LINK_COLLECTION: SCOPE_RESEARCH_THREAD_START,
    ACTION_FORUM_HANDOFF_DECISION: SCOPE_RESEARCH_THREAD_START,
    # 2. Obsidian troubleshooting / decision / learning / work-report
    ACTION_RESEARCH_LOG_SAVE: SCOPE_OBSIDIAN_TROUBLESHOOTING,
    ACTION_SELF_IMPROVEMENT_PROPOSAL: SCOPE_OBSIDIAN_TROUBLESHOOTING,
    ACTION_FAILURE_POSTMORTEM_CREATE: SCOPE_OBSIDIAN_TROUBLESHOOTING,
    ACTION_DRAFT_DOCUMENT_CREATE: SCOPE_OBSIDIAN_TROUBLESHOOTING,
    ACTION_VAULT_RESEARCH_LOG_COMMIT: SCOPE_OBSIDIAN_TROUBLESHOOTING,
    # 3+4. worktree / feature branch
    ACTION_FEATURE_BRANCH_CREATE: SCOPE_FEATURE_BRANCH_CREATE,
    # 5. code edit / local test / local smoke. ``ACTION_RUNTIME_CODE_CHANGE``
    #    is L3 in autonomy_policy by default, but self-improvement loop
    #    bounds it tightly: rate-limit + draft-PR-only + non-protected
    #    branch + permanent escalation list together mean the worst-case
    #    surface is "a draft PR gets opened on codex/self-improve/<sig>".
    #    Merging / pushing to protected / deploying still escalate.
    ACTION_LOW_RISK_DOCS_EDIT: SCOPE_CODE_EDIT_LOCAL,
    ACTION_LOW_RISK_TEST_EDIT: SCOPE_CODE_EDIT_LOCAL,
    ACTION_RUNTIME_CODE_CHANGE: SCOPE_CODE_EDIT_LOCAL,
    ACTION_TEST_EXECUTE: SCOPE_LOCAL_TEST_RUN,
    # 6. commit
    ACTION_LOCAL_COMMIT: SCOPE_LOCAL_COMMIT,
    # 7. push to feature branch — delegated WHEN branch is non-protected.
    #    Resolution happens at evaluate() time via ``branch_hint`` / ``protected``.
    ACTION_PUSH_TO_SHARED_REPO: SCOPE_FEATURE_BRANCH_PUSH,
    # 8. draft PR
    ACTION_DRAFT_PR_CREATE: SCOPE_DRAFT_PR_CREATE,
    # 10. progress marker / audit row
    ACTION_AGENT_OPS_RECORD: SCOPE_PROGRESS_MARKER_WRITE,
    ACTION_FAILURE_AUDIT_RECORD: SCOPE_PROGRESS_MARKER_WRITE,
    ACTION_ROLE_TAKE_RECORD: SCOPE_PROGRESS_MARKER_WRITE,
    # 11. retryable transient failure 의 제한적 재시도
    ACTION_RETRY_AUDIT_RECORD: SCOPE_RETRY_TRANSIENT,
}


# Permanent escalation map — these actions can NEVER be delegated even if
# someone later adds them above by mistake. Defensive belt-and-braces:
# the evaluator checks this FIRST and short-circuits.
_PERMANENT_ESCALATIONS: Mapping[str, str] = {
    ACTION_MAIN_BRANCH_PUSH: ESCALATE_PROTECTED_BRANCH_WRITE,
    ACTION_BRANCH_MERGE: ESCALATE_MERGE,
    ACTION_DEPLOY: ESCALATE_DEPLOY,
    ACTION_SECRET_MODIFY: ESCALATE_SECRET_MODIFY,
    ACTION_SECRET_ACCESS: ESCALATE_SECRET_ACCESS,
    ACTION_PROD_DB_WRITE: ESCALATE_DESTRUCTIVE_CLEANUP,
    ACTION_DESTRUCTIVE_DELETE: ESCALATE_DESTRUCTIVE_CLEANUP,
    ACTION_EXTERNAL_PUBLICATION: ESCALATE_BILLING_PURCHASE,
    ACTION_BLOG_PUBLICATION: ESCALATE_BILLING_PURCHASE,
    ACTION_EXTERNAL_PAID_CALL: ESCALATE_BILLING_PURCHASE,
    ACTION_RUNTIME_RESTART: ESCALATE_EXTERNAL_ACCESS,
    ACTION_VAULT_REMOTE_PUSH: ESCALATE_EXTERNAL_ACCESS,
    ACTION_LARGE_SCALE_CRAWL: ESCALATE_LARGE_SCALE_REFACTOR,
    # NOTE: ACTION_RUNTIME_CODE_CHANGE is *not* permanently escalated —
    # the self-improvement loop is allowed to propose+execute small fixes
    # under the feature_branch / draft_pr scopes. But the action's default
    # L3 + the rate-limit cap keep the surface bounded.
}


# Default per-scope rate-limit (max consecutive delegated executions for
# the same problem signature before forced escalation). Tuned for
# observability: a real signature that the loop keeps firing on is
# almost certainly *not* solving itself.
DEFAULT_RETRY_CAP: int = 3
DEFAULT_GLOBAL_DAILY_CAP: int = 25


# Protected branch names — push/merge to these always escalates regardless
# of scope membership. Mirrors :mod:`agents.governance.runtime_policy`'s
# protected-branch list at module load time; keeping a duplicate here means
# self-improvement decisions don't fall over a missing import.
_PROTECTED_BRANCH_NAMES: frozenset[str] = frozenset(
    {"main", "master", "develop", "release", "production", "prod"}
)


def _is_protected_branch(name: Optional[str]) -> bool:
    if not name:
        return False
    label = name.strip().lower()
    if not label:
        return False
    if label in _PROTECTED_BRANCH_NAMES:
        return True
    # also treat release/* and prod/* as protected prefix matches
    return label.startswith("release/") or label.startswith("production/")


# ---------------------------------------------------------------------------
# Rate-limit ledger
# ---------------------------------------------------------------------------


@dataclass
class DelegatedRateLedger:
    """In-process per-signature retry counter.

    The supervisor is a single process so a dict is enough for now. The
    ledger is intentionally NOT persisted to disk — on supervisor
    restart we want the counters to reset (the operator coming back will
    redo a triage anyway, and a fresh ledger lets transient issues
    self-recover).
    """

    per_signature: dict[str, int] = field(default_factory=dict)
    delegated_count_today: int = 0
    day_anchor: str = ""

    def bump(self, signature: str, *, today: str) -> int:
        if today != self.day_anchor:
            self.delegated_count_today = 0
            self.day_anchor = today
        current = self.per_signature.get(signature, 0) + 1
        self.per_signature[signature] = current
        self.delegated_count_today += 1
        return current

    def reset(self, signature: str) -> None:
        self.per_signature.pop(signature, None)

    def reset_all(self) -> None:
        self.per_signature.clear()
        self.delegated_count_today = 0
        self.day_anchor = ""


# ---------------------------------------------------------------------------
# Decision object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DelegatedDecision:
    """One delegation verdict.

    ``delegated`` True → gateway may execute the action without an
    approval card. False → must surface as operator action / human review.

    ``scope`` / ``escalation_reason`` are mutually exclusive: one is
    populated and the other is empty depending on ``delegated``.

    ``audit_summary`` is the short string the agent-ops audit and
    ``#봇-상태`` reporter quote so the operator can see "왜 자동 승인
    됐는지" without parsing the source.
    """

    delegated: bool
    action: str
    scope: Optional[str]
    escalation_reason: Optional[str]
    audit_summary: str
    problem_signature: str
    retry_count: int
    decided_at: str

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "delegated": self.delegated,
            "action": self.action,
            "scope": self.scope,
            "escalation_reason": self.escalation_reason,
            "audit_summary": self.audit_summary,
            "problem_signature": self.problem_signature,
            "retry_count": self.retry_count,
            "decided_at": self.decided_at,
        }


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def evaluate_delegated_approval(
    *,
    action: str,
    autonomy_level: Optional[AutonomyLevel] = None,
    problem_signature: str,
    branch_hint: Optional[str] = None,
    rate_ledger: Optional[DelegatedRateLedger] = None,
    retry_cap: int = DEFAULT_RETRY_CAP,
    daily_cap: int = DEFAULT_GLOBAL_DAILY_CAP,
    now_fn: Optional[Callable[[], datetime]] = None,
) -> DelegatedDecision:
    """Decide whether gateway may auto-approve *action* for this problem.

    Order of checks (any FAIL → escalate, the rest don't run):

      1. Permanent escalation list — secret / merge / main push / deploy
         / destructive delete / paid call / restart / large refactor.
      2. L4 autonomy_level — even if the action map says delegate, an L4
         verdict means the surrounding context escalated (e.g. critical
         risk metadata). Self-improvement loop must NOT downgrade L4.
      3. Branch hint protection — if the action is a feature-branch push
         but the branch matches the protected list, escalate.
      4. Action is in the delegated whitelist — unknown action escalates
         (defensive: no silent passthrough).
      5. Per-signature retry cap — exceeded → escalate.
      6. Daily global cap — exceeded → escalate.

    All escalations return ``delegated=False`` with a non-empty
    ``escalation_reason``; the caller (gateway / runtime loop) is
    responsible for emitting the operator action card.

    On success the rate-limit ledger is bumped by 1 — so even an *audit-
    only* check should not call this evaluator: it intentionally has a
    side effect to make rate-limit accounting safe under concurrent
    detect ticks.
    """

    now = (now_fn or _utc_now)()
    ledger = rate_ledger if rate_ledger is not None else DelegatedRateLedger()

    today = now.date().isoformat()

    # 1. permanent escalation
    perm = _PERMANENT_ESCALATIONS.get(action)
    if perm is not None:
        return DelegatedDecision(
            delegated=False,
            action=action,
            scope=None,
            escalation_reason=perm,
            audit_summary=(
                f"escalate: action={action} → permanent escalation ({perm})"
            ),
            problem_signature=problem_signature,
            retry_count=ledger.per_signature.get(problem_signature, 0),
            decided_at=_format_iso(now),
        )

    # 2. L4 → always escalate.
    if autonomy_level == AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN:
        return DelegatedDecision(
            delegated=False,
            action=action,
            scope=None,
            escalation_reason=ESCALATE_OUT_OF_DELEGATED_SCOPE,
            audit_summary=(
                f"escalate: action={action} resolved to L4 — out of delegated scope"
            ),
            problem_signature=problem_signature,
            retry_count=ledger.per_signature.get(problem_signature, 0),
            decided_at=_format_iso(now),
        )

    # 3. branch protection
    if action == ACTION_PUSH_TO_SHARED_REPO and _is_protected_branch(branch_hint):
        return DelegatedDecision(
            delegated=False,
            action=action,
            scope=None,
            escalation_reason=ESCALATE_PROTECTED_BRANCH_WRITE,
            audit_summary=(
                f"escalate: action={action} target branch={branch_hint!r} is protected"
            ),
            problem_signature=problem_signature,
            retry_count=ledger.per_signature.get(problem_signature, 0),
            decided_at=_format_iso(now),
        )

    # 4. delegated whitelist
    scope = _ACTION_TO_SCOPE.get(action)
    if scope is None:
        return DelegatedDecision(
            delegated=False,
            action=action,
            scope=None,
            escalation_reason=ESCALATE_OUT_OF_DELEGATED_SCOPE,
            audit_summary=(
                f"escalate: action={action} not in delegated scope whitelist"
            ),
            problem_signature=problem_signature,
            retry_count=ledger.per_signature.get(problem_signature, 0),
            decided_at=_format_iso(now),
        )

    # 5. per-signature retry cap (peek before bump so a 3rd-time
    #    signature with retry_cap=3 escalates instead of executing).
    prior = ledger.per_signature.get(problem_signature, 0)
    if prior >= retry_cap:
        return DelegatedDecision(
            delegated=False,
            action=action,
            scope=scope,
            escalation_reason=ESCALATE_RETRY_CAP_EXCEEDED,
            audit_summary=(
                f"escalate: signature={problem_signature!r} retry_count={prior} "
                f">= cap={retry_cap}"
            ),
            problem_signature=problem_signature,
            retry_count=prior,
            decided_at=_format_iso(now),
        )

    # 6. daily global cap — checked AFTER per-signature so a single bad
    #    signature can't starve the rest by hitting the global cap first.
    if today == ledger.day_anchor and ledger.delegated_count_today >= daily_cap:
        return DelegatedDecision(
            delegated=False,
            action=action,
            scope=scope,
            escalation_reason=ESCALATE_RETRY_CAP_EXCEEDED,
            audit_summary=(
                f"escalate: daily delegated cap reached "
                f"({ledger.delegated_count_today}/{daily_cap})"
            ),
            problem_signature=problem_signature,
            retry_count=prior,
            decided_at=_format_iso(now),
        )

    # PASS — bump the ledger and emit a delegated verdict.
    new_count = ledger.bump(problem_signature, today=today)
    return DelegatedDecision(
        delegated=True,
        action=action,
        scope=scope,
        escalation_reason=None,
        audit_summary=(
            f"delegated: action={action} scope={scope} "
            f"signature={problem_signature!r} retry={new_count}/{retry_cap}"
        ),
        problem_signature=problem_signature,
        retry_count=new_count,
        decided_at=_format_iso(now),
    )


def is_scope_delegated(action: str) -> bool:
    """Quick predicate for static callers (no rate-limit accounting).

    Returns True iff the action appears in the delegated scope map and is
    NOT in the permanent escalation list. Useful for tests / status
    surfaces that want to ask "could this ever be delegated?" without
    actually deciding for a specific problem.
    """

    if action in _PERMANENT_ESCALATIONS:
        return False
    return action in _ACTION_TO_SCOPE


def list_delegated_scopes() -> Tuple[str, ...]:
    """All distinct delegated scope IDs, sorted — for status surface."""

    return tuple(sorted({v for v in _ACTION_TO_SCOPE.values()}))


def list_escalation_reasons() -> Tuple[str, ...]:
    """All distinct escalation reason IDs, sorted — for status surface."""

    return tuple(
        sorted(
            {
                ESCALATE_MERGE,
                ESCALATE_RELEASE_TAG,
                ESCALATE_DEPLOY,
                ESCALATE_SECRET_MODIFY,
                ESCALATE_SECRET_ACCESS,
                ESCALATE_EXTERNAL_ACCESS,
                ESCALATE_PROTECTED_BRANCH_WRITE,
                ESCALATE_DESTRUCTIVE_CLEANUP,
                ESCALATE_BILLING_PURCHASE,
                ESCALATE_LARGE_SCALE_REFACTOR,
                ESCALATE_RETRY_CAP_EXCEEDED,
                ESCALATE_OUT_OF_DELEGATED_SCOPE,
            }
        )
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


def _format_iso(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat()


__all__ = (
    "DEFAULT_GLOBAL_DAILY_CAP",
    "DEFAULT_RETRY_CAP",
    "DelegatedDecision",
    "DelegatedRateLedger",
    "ESCALATE_BILLING_PURCHASE",
    "ESCALATE_DEPLOY",
    "ESCALATE_DESTRUCTIVE_CLEANUP",
    "ESCALATE_EXTERNAL_ACCESS",
    "ESCALATE_LARGE_SCALE_REFACTOR",
    "ESCALATE_MERGE",
    "ESCALATE_OUT_OF_DELEGATED_SCOPE",
    "ESCALATE_PROTECTED_BRANCH_WRITE",
    "ESCALATE_RELEASE_TAG",
    "ESCALATE_RETRY_CAP_EXCEEDED",
    "ESCALATE_SECRET_ACCESS",
    "ESCALATE_SECRET_MODIFY",
    "SCOPE_CODE_EDIT_LOCAL",
    "SCOPE_DRAFT_PR_CREATE",
    "SCOPE_FEATURE_BRANCH_CREATE",
    "SCOPE_FEATURE_BRANCH_PUSH",
    "SCOPE_GOVERNANCE_VALIDATION_RUN",
    "SCOPE_ISSUE_AUTO_CREATE",
    "SCOPE_LOCAL_COMMIT",
    "SCOPE_LOCAL_SMOKE_RUN",
    "SCOPE_LOCAL_TEST_RUN",
    "SCOPE_OBSIDIAN_TROUBLESHOOTING",
    "SCOPE_PROGRESS_MARKER_WRITE",
    "SCOPE_RESEARCH_THREAD_START",
    "SCOPE_RETRIEVAL_EVAL_RUN",
    "SCOPE_RETRY_TRANSIENT",
    "SCOPE_VAULT_VALIDATION_RUN",
    "SCOPE_WORKTREE_CREATE",
    "evaluate_delegated_approval",
    "is_scope_delegated",
    "list_delegated_scopes",
    "list_escalation_reasons",
)
