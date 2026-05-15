"""GitHub work_order consumer — issue auto-create 종단 worker.

배경
====
PR #166 이 `GitHubWorkOrder.issue_auto_create_plan` payload + GithubWriter
`create_issue` capability 까지 land 했지만, **큐 job 을 실제 drain 하는
consumer 가 없어** issue 가 GitHub 에 자동으로 생성되지 않았다. 본 모듈
이 그 빠진 consumer 역할을 한다.

흐름
====
queue 에 ``github_work_order`` job 이 들어오면:

1. ``existing_issue_number`` 가 명시돼 있으면 — 이미 anchor 가 있는
   기존 issue. 새 issue 를 만들지 않고 session.extra 에 그 번호만 stamp
   한 뒤 SAVED 로 마침.
2. ``issue_auto_create_plan`` 이 있으면 — :func:`GithubWriter.create_issue`
   호출. dry_run 은 ``work_order.dry_run`` 을 따른다. 결과의 issue
   number/url 을 session.extra 에 stamp.
3. plan 도 existing 도 없으면 — 잘못된 enqueue. failed_retryable.

session 갱신 키
--------------
``session.extra[SESSION_EXTRA_GITHUB_ISSUE_KEY]`` 에 다음을 저장:

```
{
  "issue_number": 77,                          # 생성/재사용된 번호
  "html_url": "https://github.com/.../issues/77",
  "title": "[Feat] 회원가입 ...",
  "labels": ["✨ Feature", "📃 Docs"],
  "approval_id": "approval-1",
  "approved_by": "masterway",
  "approved_at": "2026-05-15T...",
  "dry_run": false,
  "audit_reason": "template_used",            # 또는 existing_issue_reused / no_repo_template
  "created_via": "auto_create" | "existing_anchor" | "dry_run_plan",
  "outcome": "ok" | "denied_by_policy" | ...,
  "work_order_job_id": "...",
}
```

후속 단계 (branch / commit / PR) 는 이 key 를 anchor 로 읽어 모든 작업을
같은 issue 에 묶는다. "issue-anchored continuity" 의 SSoT.

dry_run
-------
``GithubWriter`` 의 ``dry_run`` 기본값과 동일하게 — work_order.dry_run
이 True 면 client 호출 없이 audit 만 남기고, session.extra 의
``created_via`` 는 ``"dry_run_plan"`` 으로 표기. operator 가 명시적으로
``dry_run=False`` 를 work order 에 설정해야 실제 GitHub 호출이 발동.

테스트 가능성
-----------
GithubWriter 는 caller 가 inject — production 은
``LiveGithubAppClient`` + ``make_default_policy_gate`` 조합, 테스트는
recording client + permissive policy gate 로 driven.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Tuple

from .github_work_order import (
    GitHubWorkOrder,
    JOB_TYPE_GITHUB_WORK_ORDER,
)
from .heartbeat import HeartbeatStore
from .state_machine import JobState
from .store import Job, JobQueue
from .work_order_coding_continuation import (
    ContinuationOutcome,
    promote_session_to_coding_ready,
)


logger = logging.getLogger(__name__)


SERVICE_ID_GITHUB_WORK_ORDER_EXECUTOR: str = "eng-github-work-order-executor"


# Skipped reason constants — surfaced via :class:`ExecutionOutcome` so the
# supervisor diagnostic can match exact strings without substring guesses.
SKIPPED_NO_REPO: str = "github_work_order_no_repo"
SKIPPED_NO_WRITER: str = "github_work_order_no_writer"
SKIPPED_MISSING_PLAN: str = "github_work_order_missing_plan_or_issue"
SKIPPED_DRY_RUN_PLAN_ONLY: str = "github_work_order_dry_run_plan_only"
SKIPPED_EXISTING_ANCHOR: str = "github_work_order_existing_anchor"


CREATED_VIA_AUTO_CREATE: str = "auto_create"
CREATED_VIA_EXISTING_ANCHOR: str = "existing_anchor"
CREATED_VIA_DRY_RUN: str = "dry_run_plan"


SESSION_EXTRA_GITHUB_ISSUE_KEY: str = "github_work_order_issue"
"""``session.extra`` 에서 anchor issue 정보가 머무는 키. downstream
(branch / PR / progress) 가 일관되게 이 키를 읽는다."""


# ---------------------------------------------------------------------------
# Writer Protocol — caller-injected
# ---------------------------------------------------------------------------


from typing import Protocol


class _CreateIssueWriter(Protocol):
    """Minimum :class:`GithubWriter` shape the executor needs."""

    def create_issue(
        self,
        *,
        repo: str,
        title: str,
        body: str,
        labels=(),
        assignees=(),
        autonomy_level: str = "L2",
        session_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ):
        ...


# Production builder: turn a work order into a (writer, autonomy_level)
# pair. The default builder reads env / config; tests inject a closure
# returning a recording writer.
WriterFactory = Callable[[GitHubWorkOrder], Tuple[Optional[_CreateIssueWriter], str]]


# Session adapter — pure load/update injection for storage agnosticism.
LoadSessionFn = Callable[[str], Optional[Any]]
UpdateSessionFn = Callable[[Any, Mapping[str, Any]], Any]


def _default_load_session(session_id: str) -> Optional[Any]:
    try:
        from ..workflow_state import load_session as _load
    except Exception:  # noqa: BLE001 - partial install
        return None
    try:
        return _load(session_id)
    except Exception:  # noqa: BLE001
        return None


def _default_update_session(session: Any, new_extra: Mapping[str, Any]) -> Any:
    try:
        from dataclasses import replace as _replace
        from ..workflow_state import update_session as _update
    except Exception:  # noqa: BLE001
        return session
    try:
        updated = _replace(session, extra=dict(new_extra))
        return _update(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        return session


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitHubWorkOrderExecutionOutcome:
    """Result of :meth:`GitHubWorkOrderWorker.process_job`."""

    job: Optional[Job]
    issue_number: Optional[int] = None
    issue_url: Optional[str] = None
    created_via: Optional[str] = None
    skipped_reason: Optional[str] = None
    audit_summary: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class GitHubWorkOrderWorker:
    """Drain ``github_work_order`` queue jobs by creating / reusing issues.

    The worker is the **single SSoT for issue-anchored continuity**:
    after :meth:`process_job` returns OK, ``session.extra`` carries a
    ``github_work_order_issue`` block that downstream (branch / PR /
    progress comment) consults as the canonical anchor. No other code
    path stamps that key.
    """

    def __init__(
        self,
        *,
        queue: JobQueue,
        writer_factory: WriterFactory,
        heartbeats: Optional[HeartbeatStore] = None,
        load_session_fn: Optional[LoadSessionFn] = None,
        update_session_fn: Optional[UpdateSessionFn] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        self._queue = queue
        self._writer_factory = writer_factory
        self._heartbeats = heartbeats
        self._load_session = load_session_fn or _default_load_session
        self._update_session = update_session_fn or _default_update_session
        self._worker_id = (
            worker_id
            or f"{SERVICE_ID_GITHUB_WORK_ORDER_EXECUTOR}:{os.getpid()}"
        )

    # ------------------------------------------------------------------
    # Consumer side
    # ------------------------------------------------------------------

    def process_job(
        self,
        job: Job,
        *,
        now: Optional[float] = None,
    ) -> GitHubWorkOrderExecutionOutcome:
        """Drive *job* through assigned → in_progress → saved (or
        failed_retryable). Returns a structured outcome the supervisor
        can read without re-parsing the queue row."""

        if self._heartbeats is not None:
            try:
                self._heartbeats.record(
                    SERVICE_ID_GITHUB_WORK_ORDER_EXECUTOR,
                    pid=os.getpid(),
                    metadata={"job_id": job.job_id},
                    now=now,
                )
            except Exception:  # noqa: BLE001 - observability only
                pass

        in_progress = self._queue.transition(
            job.job_id, JobState.IN_PROGRESS, now=now
        )
        try:
            work_order = GitHubWorkOrder.from_payload(in_progress.payload or {})
        except Exception as exc:  # noqa: BLE001 - corrupt payload
            self._queue.transition(
                in_progress.job_id,
                JobState.FAILED_RETRYABLE,
                result={"error": f"payload_parse_error:{type(exc).__name__}"},
                clear_lease=True,
                now=now,
            )
            raise

        # Reject early: no repo means we can't call GitHub. Mark
        # failed_retryable so an operator who corrects the proposal can
        # requeue without losing the row.
        if not (work_order.repo or "").strip():
            self._queue.transition(
                in_progress.job_id,
                JobState.FAILED_RETRYABLE,
                result={"error": SKIPPED_NO_REPO},
                clear_lease=True,
                now=now,
            )
            return GitHubWorkOrderExecutionOutcome(
                job=in_progress, skipped_reason=SKIPPED_NO_REPO
            )

        # Branch 1 — existing issue anchor. Just stamp session and exit.
        if (
            work_order.existing_issue_number is not None
            and int(work_order.existing_issue_number) > 0
        ):
            outcome = self._stamp_existing_anchor(
                job=in_progress, work_order=work_order, now=now
            )
            return outcome

        # Branch 2 — auto-create plan. Need writer + plan.
        plan = work_order.issue_auto_create_plan
        if not isinstance(plan, Mapping) or not plan:
            self._queue.transition(
                in_progress.job_id,
                JobState.FAILED_RETRYABLE,
                result={"error": SKIPPED_MISSING_PLAN},
                clear_lease=True,
                now=now,
            )
            return GitHubWorkOrderExecutionOutcome(
                job=in_progress, skipped_reason=SKIPPED_MISSING_PLAN
            )

        writer, autonomy_level = self._writer_factory(work_order)
        if writer is None:
            self._queue.transition(
                in_progress.job_id,
                JobState.FAILED_RETRYABLE,
                result={"error": SKIPPED_NO_WRITER},
                clear_lease=True,
                now=now,
            )
            return GitHubWorkOrderExecutionOutcome(
                job=in_progress, skipped_reason=SKIPPED_NO_WRITER
            )

        return self._execute_auto_create(
            job=in_progress,
            work_order=work_order,
            plan=plan,
            writer=writer,
            autonomy_level=autonomy_level or "L2",
            now=now,
        )

    # ------------------------------------------------------------------
    # Branch helpers
    # ------------------------------------------------------------------

    def _stamp_existing_anchor(
        self,
        *,
        job: Job,
        work_order: GitHubWorkOrder,
        now: Optional[float],
    ) -> GitHubWorkOrderExecutionOutcome:
        anchor = {
            "issue_number": int(work_order.existing_issue_number or 0),
            "html_url": None,
            "title": None,
            "labels": [],
            "approval_id": work_order.approval_id,
            "approved_by": work_order.approved_by,
            "approved_at": work_order.approved_at,
            "dry_run": work_order.dry_run,
            "audit_reason": "existing_issue_reused",
            "created_via": CREATED_VIA_EXISTING_ANCHOR,
            "outcome": "ok",
            "work_order_job_id": job.job_id,
            "repo": work_order.repo,
        }
        self._write_session_anchor(work_order.session_id, anchor)
        # P0-S continuation — anchor 가 안착되면 즉시 coding_job=ready 로
        # promote. issue-less bootstrap 과 existing issue 둘 다 같은 downstream
        # coding path 로 모인다.
        continuation = self._continue_to_coding(
            session_id=work_order.session_id,
            work_order=work_order,
            anchor=anchor,
        )
        saved = self._queue.transition(
            job.job_id,
            JobState.SAVED,
            result={
                "created_via": CREATED_VIA_EXISTING_ANCHOR,
                "issue_number": anchor["issue_number"],
                "skipped_reason": SKIPPED_EXISTING_ANCHOR,
                "coding_dispatch_queued": continuation is not None
                and continuation.promoted,
                "coding_dispatch_noop_reason": continuation.noop_reason
                if continuation is not None
                else None,
            },
            clear_lease=True,
            now=now,
        )
        return GitHubWorkOrderExecutionOutcome(
            job=saved,
            issue_number=anchor["issue_number"],
            issue_url=None,
            created_via=CREATED_VIA_EXISTING_ANCHOR,
            skipped_reason=SKIPPED_EXISTING_ANCHOR,
            audit_summary={
                "anchor": anchor,
                "continuation_promoted": bool(
                    continuation and continuation.promoted
                ),
                "continuation_noop": continuation.noop_reason
                if continuation is not None
                else None,
            },
        )

    def _execute_auto_create(
        self,
        *,
        job: Job,
        work_order: GitHubWorkOrder,
        plan: Mapping[str, Any],
        writer: _CreateIssueWriter,
        autonomy_level: str,
        now: Optional[float],
    ) -> GitHubWorkOrderExecutionOutcome:
        title = str(plan.get("title") or "").strip()
        body = str(plan.get("body") or "").strip()
        labels = tuple(str(l) for l in (plan.get("labels") or ()))
        assignees = tuple(str(a) for a in (plan.get("assignees") or ()))

        result = writer.create_issue(
            repo=str(work_order.repo or ""),
            title=title,
            body=body,
            labels=labels,
            assignees=assignees,
            autonomy_level=autonomy_level,
            session_id=work_order.session_id,
            decision_id=work_order.proposal_id or None,
        )

        outcome_label = str(getattr(result, "outcome", "") or "")
        succeeded = bool(getattr(result, "succeeded", False))
        body_response = getattr(result, "body", {}) or {}
        if not isinstance(body_response, Mapping):
            body_response = {}
        issue_number = _coerce_int(body_response.get("number"))
        issue_url = (
            body_response.get("html_url")
            or body_response.get("url")
            or None
        )
        if not succeeded and outcome_label != "ok":
            # dry_run, denied_by_policy, failed — treat all non-ok as
            # plan-only. We still stamp an anchor so operator can see
            # the plan; only succeeded == True records a real issue.
            issue_number = None
            issue_url = None

        created_via = (
            CREATED_VIA_AUTO_CREATE
            if succeeded
            else CREATED_VIA_DRY_RUN
        )
        skipped_reason: Optional[str] = None
        if not succeeded:
            skipped_reason = SKIPPED_DRY_RUN_PLAN_ONLY

        anchor = {
            "issue_number": issue_number,
            "html_url": issue_url,
            "title": title,
            "labels": list(labels),
            "approval_id": work_order.approval_id,
            "approved_by": work_order.approved_by,
            "approved_at": work_order.approved_at,
            "dry_run": work_order.dry_run,
            "audit_reason": str(plan.get("audit_reason") or "template_used"),
            "created_via": created_via,
            "outcome": outcome_label or "unknown",
            "work_order_job_id": job.job_id,
            "repo": work_order.repo,
            "template_path": plan.get("template_path"),
        }
        self._write_session_anchor(work_order.session_id, anchor)

        # P0-S continuation — anchor stamp 후 즉시 coding_job=ready 로 promote.
        # dry_run plan-only 케이스도 같은 path 를 타고 dispatcher 가 dry_run
        # 으로 자연스럽게 진행한다 (executor 가 자체 dry_run gate 보유).
        continuation = self._continue_to_coding(
            session_id=work_order.session_id,
            work_order=work_order,
            anchor=anchor,
        )

        # Always SAVED — even dry_run / denied is a deterministic
        # outcome we want the operator to see in #봇-상태. failure means
        # the writer raised an exception, which is converted below.
        saved = self._queue.transition(
            job.job_id,
            JobState.SAVED,
            result={
                "created_via": created_via,
                "issue_number": issue_number,
                "issue_url": issue_url,
                "outcome": outcome_label,
                "skipped_reason": skipped_reason,
                "coding_dispatch_queued": continuation is not None
                and continuation.promoted,
                "coding_dispatch_noop_reason": continuation.noop_reason
                if continuation is not None
                else None,
            },
            clear_lease=True,
            now=now,
        )
        return GitHubWorkOrderExecutionOutcome(
            job=saved,
            issue_number=issue_number,
            issue_url=str(issue_url) if issue_url else None,
            created_via=created_via,
            skipped_reason=skipped_reason,
            audit_summary={
                "anchor": anchor,
                "continuation_promoted": bool(
                    continuation and continuation.promoted
                ),
                "continuation_noop": continuation.noop_reason
                if continuation is not None
                else None,
            },
        )

    # ------------------------------------------------------------------
    # Session writer
    # ------------------------------------------------------------------

    def _continue_to_coding(
        self,
        *,
        session_id: str,
        work_order: GitHubWorkOrder,
        anchor: Mapping[str, Any],
    ) -> Optional[ContinuationOutcome]:
        """Anchor stamp 직후 같은 세션을 coding_job=ready 로 promote.

        idempotent — 이미 같은 anchor 로 ready 상태면 noop. session 이
        없거나 coding_proposal 이 없으면 noop (operator audit 에 noop
        reason 남김).
        """

        if not session_id:
            return None
        try:
            session = self._load_session(session_id)
        except Exception:  # noqa: BLE001
            return None
        if session is None:
            return None
        existing_extra = getattr(session, "extra", None) or {}
        if not isinstance(existing_extra, Mapping):
            existing_extra = {}
        outcome = promote_session_to_coding_ready(
            session_extra=existing_extra,
            anchor=anchor,
            repo=work_order.repo,
            base_branch=work_order.base_branch,
            dry_run=bool(work_order.dry_run),
            approval_id=work_order.approval_id or None,
            approved_by=work_order.approved_by or None,
            approved_at=work_order.approved_at or None,
        )
        if outcome.new_extra is None:
            return outcome
        try:
            self._update_session(session, outcome.new_extra)
        except Exception:  # noqa: BLE001 - best-effort
            pass
        return outcome

    def _write_session_anchor(
        self, session_id: str, anchor: Mapping[str, Any]
    ) -> None:
        """Stamp the anchor on ``session.extra`` — best-effort.

        The queue row is the authoritative record; session.extra is
        the convenience surface so downstream (branch/PR/progress)
        can read the issue number without scanning the queue.
        """

        if not session_id:
            return
        try:
            session = self._load_session(session_id)
        except Exception:  # noqa: BLE001
            return
        if session is None:
            return
        existing_extra = getattr(session, "extra", None) or {}
        if not isinstance(existing_extra, Mapping):
            existing_extra = {}
        new_extra = dict(existing_extra)
        new_extra[SESSION_EXTRA_GITHUB_ISSUE_KEY] = dict(anchor)
        try:
            self._update_session(session, new_extra)
        except Exception:  # noqa: BLE001 - best effort
            pass

    # ------------------------------------------------------------------
    # Producer-style helper for tests / single-shot dispatch
    # ------------------------------------------------------------------

    def run_one(
        self,
        *,
        session_id: Optional[str] = None,
        proposal_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Optional[GitHubWorkOrderExecutionOutcome]:
        """Pick one queued github_work_order job and execute it.

        Returns ``None`` when nothing is pickable. Useful from the
        runtime supervisor's tick loop and from tests that want to
        drive a single drain step.
        """

        picked = self._queue.pick(
            worker_id=self._worker_id,
            job_types=[JOB_TYPE_GITHUB_WORK_ORDER],
            now=now,
        )
        if picked is None:
            return None
        if session_id and picked.session_id != session_id:
            # Not our session — release the lease.
            self._queue.transition(
                picked.job_id,
                JobState.QUEUED,
                clear_lease=True,
                now=now,
            )
            return None
        if proposal_id:
            payload = picked.payload or {}
            if str(payload.get("proposal_id") or "") != proposal_id:
                self._queue.transition(
                    picked.job_id,
                    JobState.QUEUED,
                    clear_lease=True,
                    now=now,
                )
                return None
        return self.process_job(picked, now=now)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = (
    "CREATED_VIA_AUTO_CREATE",
    "CREATED_VIA_DRY_RUN",
    "CREATED_VIA_EXISTING_ANCHOR",
    "GitHubWorkOrderExecutionOutcome",
    "GitHubWorkOrderWorker",
    "LoadSessionFn",
    "SERVICE_ID_GITHUB_WORK_ORDER_EXECUTOR",
    "SESSION_EXTRA_GITHUB_ISSUE_KEY",
    "SKIPPED_DRY_RUN_PLAN_ONLY",
    "SKIPPED_EXISTING_ANCHOR",
    "SKIPPED_MISSING_PLAN",
    "SKIPPED_NO_REPO",
    "SKIPPED_NO_WRITER",
    "UpdateSessionFn",
    "WriterFactory",
)
