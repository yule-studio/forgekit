"""TriagePlan → executable GitHub action plan + audited execution — G3.

Wires the four building blocks (branching / commit_policy /
pr_template / github_writer) into a single transaction:

  1. :func:`build_github_action_plan` consumes a triage plan and
     produces an ordered list of :class:`ActionStep` rows
     (autonomy_level, action_kind, payload).
  2. :func:`execute_github_action_plan` walks the plan via a
     :class:`github_writer.GithubWriter`, capturing one
     :class:`audit.GithubWriteAudit` per step.

The whole module is deliberately ``dry_run`` by default — the real
GitHub write needs both ``writer.live=True`` and ``writer.dry_run=False``
*plus* a policy gate that allows the action's autonomy level. Tests
exercise the plan + dispatcher with stub clients so no live API call
ever happens during CI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from .audit import (
    ACTION_GITHUB_BRANCH_CREATE,
    ACTION_GITHUB_ISSUE_COMMENT,
    ACTION_GITHUB_LABEL_ADD,
    ACTION_GITHUB_PR_DRAFT_CREATE,
    GithubWriteAudit,
    redact_secrets,
)
from .branching import derive_branch_name, is_protected_branch
from .github_writer import GithubWriter, GithubWriteResult
from .pr_template import render_pr_body


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Triage-plan Protocol — superset of branching.TriagePlanLike + PR fields
# ---------------------------------------------------------------------------


class TriagePlanLike(Protocol):
    """Structural typing for a G2-produced triage plan.

    The plan is built by G2 and consumed here; its concrete dataclass
    lives in another package. Listed fields are what G3 reads; the
    plan may carry many more without breaking the contract.
    """

    issue_number: Optional[int]
    session_id: Optional[str]
    title: str
    body: str
    primary_role: str
    autonomy_level: str
    source: str
    repo: str
    labels: Sequence[str]
    in_scope: Sequence[str]
    out_of_scope: Sequence[str]
    test_plan: Sequence[str]
    risks: Sequence[str]
    approvals_needed: Sequence[str]
    work_orders: Sequence[Mapping[str, str]]
    base_branch: Optional[str]


# ---------------------------------------------------------------------------
# Plan dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionStep:
    """One step in a :class:`GithubActionPlan`.

    Each step describes *what* the writer will attempt; *whether* it
    actually executes is decided by the writer's policy_gate +
    dry_run flags at execution time.
    """

    kind: str
    autonomy_level: str
    repo: str
    summary: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    # Optional cross-references — branch name / issue number / pr
    # draft head etc. so a reader can match up steps without
    # parsing payload.
    branch: Optional[str] = None
    issue_number: Optional[int] = None


@dataclass(frozen=True)
class GithubActionPlan:
    """Ordered list of actions the agent intends to execute on GitHub."""

    repo: str
    branch: Optional[str]
    base_branch: str
    steps: Tuple[ActionStep, ...]
    pr_title: str = ""
    pr_body: str = ""
    pr_summary_items: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionRecord:
    """One step's execution record — paired with audit row + result."""

    step: ActionStep
    result: GithubWriteResult
    audit: Optional[GithubWriteAudit]


@dataclass(frozen=True)
class ExecutionReport:
    """Full result of :func:`execute_github_action_plan`.

    ``records`` keeps step-by-step results in execution order. If a
    step fails or is denied AND ``stop_on_failure=True`` (the default),
    ``halted=True`` and remaining steps are not attempted.
    """

    plan: GithubActionPlan
    records: Tuple[ExecutionRecord, ...]
    halted: bool = False
    halt_reason: str = ""

    @property
    def all_succeeded(self) -> bool:
        return all(record.result.ok for record in self.records)

    @property
    def audits(self) -> Tuple[GithubWriteAudit, ...]:
        return tuple(
            record.audit for record in self.records if record.audit is not None
        )


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


_DEFAULT_BASE_BRANCH: str = "main"


def build_github_action_plan(
    plan: TriagePlanLike,
    *,
    base_branch: Optional[str] = None,
    audit_id: str = "pending",
    trace_links: Optional[Mapping[str, str]] = None,
    additional_labels: Optional[Sequence[str]] = None,
    branch_name_override: Optional[str] = None,
) -> GithubActionPlan:
    """Convert *plan* into an ordered :class:`GithubActionPlan`.

    Standard ordering reflects the safest "narrate before write"
    progression:

      1. issue comment summarising what the agent will do.
      2. label add (if any).
      3. branch create.
      4. (commits are added by the caller — they need real diff
         content and a tree object that lives outside G3.)
      5. draft PR open.

    The plan never targets a protected branch — :func:`derive_branch_name`
    enforces it. The PR body is rendered via :func:`pr_template.render_pr_body`
    so every PR opened by the agent shares the same section contract.
    """

    repo = (getattr(plan, "repo", "") or "").strip()
    if not repo:
        raise ValueError("triage plan missing 'repo' — cannot build action plan")
    base = (
        (base_branch or getattr(plan, "base_branch", None) or _DEFAULT_BASE_BRANCH)
        .strip()
    )
    autonomy_level = (
        (getattr(plan, "autonomy_level", "") or "L1").strip().upper() or "L1"
    )
    issue_number = _safe_int(getattr(plan, "issue_number", None))
    session_id = (getattr(plan, "session_id", "") or "").strip() or None

    branch_name = (
        branch_name_override.strip()
        if isinstance(branch_name_override, str) and branch_name_override.strip()
        else derive_branch_name(plan, fallback_seed=audit_id)
    )
    if is_protected_branch(branch_name):
        raise ValueError(
            f"branch_name {branch_name!r} resolves to a protected ref — abort"
        )

    labels = list(getattr(plan, "labels", ()) or ())
    if additional_labels:
        for extra in additional_labels:
            cleaned = str(extra).strip()
            if cleaned and cleaned not in labels:
                labels.append(cleaned)

    summary_items = list(getattr(plan, "in_scope", ()) or ())
    pr_body = render_pr_body(
        plan,
        audit_id=audit_id,
        agent_work_orders=list(getattr(plan, "work_orders", ()) or ()),
        trace_links=trace_links,
        change_summary=summary_items,
    )
    pr_title = (getattr(plan, "title", "") or "").strip() or branch_name

    steps: List[ActionStep] = []

    # 1. Narrate intent on the issue (when there is one).
    if issue_number is not None:
        narrate_body = _narration_comment(
            plan=plan,
            audit_id=audit_id,
            branch_name=branch_name,
            base_branch=base,
        )
        steps.append(
            ActionStep(
                kind=ACTION_GITHUB_ISSUE_COMMENT,
                autonomy_level=_max_autonomy(autonomy_level, "L1"),
                repo=repo,
                summary=f"narrate intent on {repo}#{issue_number}",
                payload={"body": narrate_body},
                issue_number=issue_number,
            )
        )

    # 2. Labels (if any).
    if labels and issue_number is not None:
        steps.append(
            ActionStep(
                kind=ACTION_GITHUB_LABEL_ADD,
                autonomy_level=_max_autonomy(autonomy_level, "L1"),
                repo=repo,
                summary=f"add labels {labels} to {repo}#{issue_number}",
                payload={"labels": tuple(labels)},
                issue_number=issue_number,
            )
        )

    # 3. Branch create.
    steps.append(
        ActionStep(
            kind=ACTION_GITHUB_BRANCH_CREATE,
            autonomy_level=_max_autonomy(autonomy_level, "L2"),
            repo=repo,
            summary=f"create branch {branch_name} (base={base})",
            payload={"base_branch": base},
            branch=branch_name,
            issue_number=issue_number,
        )
    )

    # 4. Draft PR (commits are caller's responsibility — they need the
    #    real tree object after the local diff is staged).
    steps.append(
        ActionStep(
            kind=ACTION_GITHUB_PR_DRAFT_CREATE,
            autonomy_level=_max_autonomy(autonomy_level, "L2"),
            repo=repo,
            summary=f"open draft PR {branch_name} → {base}",
            payload={
                "head": branch_name,
                "base": base,
                "title": pr_title,
                "body_preview_length": len(pr_body.render()),
            },
            branch=branch_name,
            issue_number=issue_number,
        )
    )

    return GithubActionPlan(
        repo=repo,
        branch=branch_name,
        base_branch=base,
        steps=tuple(steps),
        pr_title=pr_title,
        pr_body=pr_body.render(),
        pr_summary_items=tuple(summary_items),
    )


def _narration_comment(
    *,
    plan: TriagePlanLike,
    audit_id: str,
    branch_name: str,
    base_branch: str,
) -> str:
    role = (getattr(plan, "primary_role", "") or "").strip() or "engineering"
    autonomy = (getattr(plan, "autonomy_level", "") or "L1").strip().upper() or "L1"
    return (
        f"🤖 **agent intent — `{role}`**\n\n"
        f"- 자동화 수준: `{autonomy}`\n"
        f"- 작업 브랜치: `{branch_name}` (base `{base_branch}`)\n"
        f"- audit id: `{audit_id}`\n"
        f"- 다음 단계: branch 생성 → 코드 작성 → draft PR 게시 (모두 dry-run "
        "기본값 — `live=True` 와 policy 승인이 모두 있어야 실제 write 됨).\n"
    )


def _max_autonomy(plan_level: str, floor_level: str) -> str:
    """Return whichever autonomy level is *more* permissive.

    The plan's stated level wins unless it's below the floor — every
    action has a minimum autonomy listed in
    :mod:`github_writer._DEFAULT_MIN_AUTONOMY` and we surface that
    here so a denied policy decision is clearly traceable.
    """

    return plan_level if _rank(plan_level) >= _rank(floor_level) else floor_level


def _rank(level: str) -> int:
    return {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}.get(
        (level or "").strip().upper(), 0
    )


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


AuditSink = Callable[[GithubWriteAudit], None]


def execute_github_action_plan(
    plan: GithubActionPlan,
    *,
    writer: GithubWriter,
    audit_sink: Optional[AuditSink] = None,
    session_id: Optional[str] = None,
    decision_id: Optional[str] = None,
    stop_on_failure: bool = True,
) -> ExecutionReport:
    """Walk *plan* through *writer*, returning per-step records.

    *audit_sink* receives every :class:`GithubWriteAudit` the writer
    produces (one per step). It defaults to a no-op so callers that
    only want results can ignore audit. Production wires this to
    ``append_agent_ops_audit`` on a session.

    The default ``stop_on_failure=True`` aborts at the first
    denied/failed step. The first failed audit is also surfaced via
    ``halted=True`` + ``halt_reason`` so the caller can decide whether
    to surface the issue back to the user.
    """

    sink = audit_sink or (lambda _audit: None)
    records: List[ExecutionRecord] = []
    halted = False
    halt_reason = ""

    for step in plan.steps:
        result = _dispatch_step(
            step,
            writer=writer,
            plan=plan,
            session_id=session_id,
            decision_id=decision_id,
        )
        if result.audit is not None:
            try:
                sink(result.audit)
            except Exception:  # noqa: BLE001 - audit must never break dispatch
                logger.warning(
                    "audit_sink raised for action=%s step=%s — dropped record",
                    step.kind,
                    step.summary,
                    exc_info=True,
                )
        records.append(
            ExecutionRecord(step=step, result=result, audit=result.audit)
        )
        if not result.ok and stop_on_failure:
            halted = True
            halt_reason = redact_secrets(result.detail or result.outcome)
            break

    return ExecutionReport(
        plan=plan,
        records=tuple(records),
        halted=halted,
        halt_reason=halt_reason,
    )


def _dispatch_step(
    step: ActionStep,
    *,
    writer: GithubWriter,
    plan: GithubActionPlan,
    session_id: Optional[str],
    decision_id: Optional[str],
) -> GithubWriteResult:
    if step.kind == ACTION_GITHUB_ISSUE_COMMENT:
        body = str(step.payload.get("body") or "")
        return writer.post_issue_comment(
            repo=step.repo,
            issue_number=int(step.issue_number or 0),
            body=body,
            autonomy_level=step.autonomy_level,
            session_id=session_id,
            decision_id=decision_id,
        )
    if step.kind == ACTION_GITHUB_LABEL_ADD:
        labels = tuple(step.payload.get("labels") or ())
        return writer.add_labels(
            repo=step.repo,
            issue_number=int(step.issue_number or 0),
            labels=labels,
            autonomy_level=step.autonomy_level,
            session_id=session_id,
            decision_id=decision_id,
        )
    if step.kind == ACTION_GITHUB_BRANCH_CREATE:
        base = str(step.payload.get("base_branch") or plan.base_branch or "main")
        # Real wiring fetches the base SHA via the client; the
        # writer takes the SHA as a string, so the caller is
        # responsible for resolving it. For dry_run / denied paths
        # we pass an obvious placeholder so an accidental live
        # call against this exact step would still be denied at
        # the API level (422) instead of writing a wrong-base ref.
        base_sha = str(step.payload.get("base_sha") or f"refs/heads/{base}")
        return writer.create_branch(
            repo=step.repo,
            branch=step.branch or "",
            base_sha=base_sha,
            autonomy_level=step.autonomy_level,
            session_id=session_id,
            decision_id=decision_id,
        )
    if step.kind == ACTION_GITHUB_PR_DRAFT_CREATE:
        return writer.create_draft_pull_request(
            repo=step.repo,
            head=str(step.payload.get("head") or step.branch or ""),
            base=str(step.payload.get("base") or plan.base_branch or "main"),
            title=plan.pr_title,
            body=plan.pr_body,
            autonomy_level=step.autonomy_level,
            session_id=session_id,
            decision_id=decision_id,
        )
    # Unknown step kind — never happens with build_github_action_plan,
    # but a hand-constructed ActionStep could land here. Surface it as
    # a denied result so the audit captures the divergence.
    from .audit import build_github_audit_record, OUTCOME_FAILED

    audit = build_github_audit_record(
        action=step.kind,
        actor_role=writer.actor_role,
        autonomy_level=step.autonomy_level,
        policy_reason=f"unknown action {step.kind!r}",
        target_repo=step.repo,
        issue_number=step.issue_number,
        session_id=session_id,
        pr_number=None,
        branch=step.branch,
        dry_run=writer.dry_run or not writer.live,
        outcome=OUTCOME_FAILED,
        summary=step.summary,
        decision_id=decision_id,
    )
    return GithubWriteResult(
        ok=False,
        outcome=OUTCOME_FAILED,
        detail=f"unknown action kind {step.kind!r}",
        body={},
        audit=audit,
    )


__all__ = (
    "ActionStep",
    "AuditSink",
    "ExecutionRecord",
    "ExecutionReport",
    "GithubActionPlan",
    "TriagePlanLike",
    "build_github_action_plan",
    "execute_github_action_plan",
)
