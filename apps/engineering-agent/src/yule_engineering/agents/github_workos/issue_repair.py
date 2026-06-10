"""Issue repair — existing issue 의 title/body/labels 를 새 plan 으로 갱신.

배경
====
라이브 스모크에서 이미 잘못된 fallback (raw title / no labels / weak body)
으로 issue #1 이 생성됐다. 사용자가 수동으로 고치지 않고 *자동* 으로
바로잡을 수 있어야 한다.

본 모듈은:

1. session_id / repo / issue_number 가 주어지면 그 session 의 prompt 로
   :func:`build_default_issue_body` 를 다시 호출해 새 plan 을 만든다.
2. plan 의 title / body / labels 를 GithubClient.update_issue 로 PATCH.
3. 결과 audit 를 session.extra 의 `github_work_order_issue` anchor 에 stamp.

policy:
* live update 는 GithubClient.update_issue 가 반드시 있어야 함. 없으면
  dry-run 으로 변환된 plan 만 반환 (audit 가능).
* protected branch / repo allowlist / autonomy gate 는 호출 측 PolicyGate
  가 책임 — 본 helper 는 PATCH 호출만 (gate 가 차단하면 그대로 raise).
* 자동 머지 / 자동 close 는 절대 하지 않는다 — issue body / title / labels
  만 update.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence, Tuple

from .issue_auto_create import (
    AUDIT_TEMPLATE_FALLBACK,
    IssueAutoCreatePlan,
    build_default_issue_body,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol — minimal update surface needed for repair
# ---------------------------------------------------------------------------


class IssueUpdateClient(Protocol):
    """Subset of GithubClient used by repair.

    Production wires this to a live client that calls
    ``PATCH /repos/{owner}/{repo}/issues/{number}`` + ``POST /repos/.../
    labels``. Tests inject an in-memory recorder.
    """

    def update_issue(
        self,
        *,
        repo: str,
        issue_number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        labels: Optional[Sequence[str]] = None,
    ) -> Mapping[str, Any]:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Repair outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssueRepairOutcome:
    """:func:`repair_existing_issue` 결과.

    ``dry_run`` True 면 client 호출은 일어나지 않고 plan 만 반환 — operator
    가 검토하고 별도로 update_issue 를 호출 가능.
    """

    repo: str
    issue_number: int
    plan: IssueAutoCreatePlan
    updated: bool
    dry_run: bool
    skipped_reason: Optional[str] = None
    response: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Repair helper
# ---------------------------------------------------------------------------


def repair_existing_issue(
    *,
    repo: str,
    issue_number: int,
    request_summary: str,
    session_id: Optional[str] = None,
    repo_contract: Optional[Any] = None,
    extra_labels: Iterable[str] = (),
    obsidian_template_loader: Optional[Any] = None,
    client: Optional[IssueUpdateClient] = None,
    dry_run: bool = True,
) -> IssueRepairOutcome:
    """existing issue 를 quality plan 으로 PATCH.

    ``dry_run=True`` (기본) 일 때 — client 가 있어도 호출하지 않는다.
    operator 가 plan 을 검토한 뒤 `dry_run=False` 로 다시 호출하거나, 별도
    surface (CLI / Discord 슬래시) 에서 PATCH 실행.

    repo / issue_number 가 비어있거나 잘못된 값이면 ``skipped_reason``
    채우고 plan 만 반환 (raise 없음).
    """

    repo = (repo or "").strip()
    if not repo or "/" not in repo:
        return IssueRepairOutcome(
            repo=repo,
            issue_number=int(issue_number or 0),
            plan=_empty_plan(),
            updated=False,
            dry_run=True,
            skipped_reason="invalid_repo",
        )
    try:
        issue_num_int = int(issue_number)
    except (TypeError, ValueError):
        return IssueRepairOutcome(
            repo=repo,
            issue_number=0,
            plan=_empty_plan(),
            updated=False,
            dry_run=True,
            skipped_reason="invalid_issue_number",
        )
    if issue_num_int <= 0:
        return IssueRepairOutcome(
            repo=repo,
            issue_number=issue_num_int,
            plan=_empty_plan(),
            updated=False,
            dry_run=True,
            skipped_reason="invalid_issue_number",
        )

    plan = build_default_issue_body(
        request_summary=request_summary,
        repo_contract=repo_contract,
        session_id=session_id,
        extra_labels=tuple(extra_labels or ()),
        obsidian_template_loader=obsidian_template_loader,
    )

    if dry_run or client is None:
        return IssueRepairOutcome(
            repo=repo,
            issue_number=issue_num_int,
            plan=plan,
            updated=False,
            dry_run=True,
            skipped_reason="dry_run" if client is not None else "no_client_wired",
        )

    try:
        response = client.update_issue(
            repo=repo,
            issue_number=issue_num_int,
            title=plan.title,
            body=plan.body,
            labels=list(plan.labels),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "repair_existing_issue: client.update_issue raised — falling back to dry-run report",
            exc_info=True,
        )
        return IssueRepairOutcome(
            repo=repo,
            issue_number=issue_num_int,
            plan=plan,
            updated=False,
            dry_run=False,
            skipped_reason=f"client_error:{type(exc).__name__}",
            response={"error": str(exc) or type(exc).__name__},
        )

    return IssueRepairOutcome(
        repo=repo,
        issue_number=issue_num_int,
        plan=plan,
        updated=True,
        dry_run=False,
        response=dict(response or {}),
    )


def repair_outcome_to_audit(
    outcome: IssueRepairOutcome,
    *,
    actor: str = "engineering-agent",
    when: Optional[datetime] = None,
) -> Mapping[str, Any]:
    """outcome 을 session.extra 의 anchor stamp 에 그대로 합칠 dict 로 변환.

    기존 :data:`github_work_order_issue` anchor 와 같은 모양 — operator 가
    `yule runtime status` / vault 노트에서 동일한 surface 로 읽는다.
    """

    when_iso = (when or datetime.now(tz=timezone.utc)).replace(microsecond=0).isoformat()
    return {
        "repo": outcome.repo,
        "issue_number": outcome.issue_number,
        "audit_reason": "issue_repair",
        "outcome": "ok" if outcome.updated else (outcome.skipped_reason or "skipped"),
        "updated": outcome.updated,
        "dry_run": outcome.dry_run,
        "repaired_at": when_iso,
        "actor": actor,
        "title": outcome.plan.title,
        "labels": list(outcome.plan.labels),
        "skipped_reason": outcome.skipped_reason,
    }


def _empty_plan() -> IssueAutoCreatePlan:
    return IssueAutoCreatePlan(
        title="",
        body="",
        labels=(),
        assignees=(),
        template_path=None,
        confidence="low",
        audit_reason=AUDIT_TEMPLATE_FALLBACK,
        needs_operator_decision=True,
    )


__all__ = (
    "IssueRepairOutcome",
    "IssueUpdateClient",
    "repair_existing_issue",
    "repair_outcome_to_audit",
)
