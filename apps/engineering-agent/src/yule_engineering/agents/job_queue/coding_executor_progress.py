"""Progress stamping for the coding executor worker.

Side-effecting (session.extra) progress-marker writers extracted out of
:mod:`coding_executor_worker` (#73 follow-up) so the worker keeps the
``process_job`` pipeline while *progress stamping* lives in one cohesive
module.

Both functions are best-effort: a storage hiccup never breaks the
executor pipeline — the queue row result still carries the same audit so
``#봇-상태`` can recover. The ``..workflow_state`` import happens at call
time (inside each function) so test seams that monkey-patch
``workflow_state.load_session`` / ``update_session`` keep working.

This module imports *nothing* from the worker — one-way dependency, no
cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .pr_merge_continuation import (
    EXTRA_PR_MERGE_AUDIT,
    EXTRA_PR_MERGE_STAGE,
    PostPRAction,
    decide_post_pr_action,
)
from .work_order_coding_continuation import stamp_progress_marker


def stamp_progress(
    *,
    session_id: str,
    marker: str,
    detail: Optional[Mapping[str, Any]] = None,
) -> None:
    """Persist a progress marker on ``session.extra`` — best-effort.

    Uses :func:`agents.job_queue.work_order_coding_continuation.stamp_progress_marker`
    which is the SSoT for the 5 progress markers (issue_created /
    coding_dispatch_queued / coding_in_progress / draft_pr_opened /
    coding_blocked). Storage failure is swallowed so the executor
    pipeline never breaks on a session cache hiccup — the queue row
    result still carries the same audit so #봇-상태 can recover.
    """

    if not session_id:
        return
    try:
        from ..workflow_state import load_session as _load
        from ..workflow_state import update_session as _update
        from dataclasses import replace as _replace
    except Exception:  # noqa: BLE001 - partial install
        return
    try:
        session = _load(session_id)
    except Exception:  # noqa: BLE001
        return
    if session is None:
        return
    try:
        existing_extra = getattr(session, "extra", None) or {}
        if not isinstance(existing_extra, Mapping):
            existing_extra = {}
        new_extra = stamp_progress_marker(
            session_extra=existing_extra,
            marker=marker,
            detail=dict(detail or {}),
        )
        updated = _replace(session, extra=dict(new_extra))
        _update(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        pass


def stamp_pr_merge_continuation(
    *,
    session_id: str,
    job_id: str,
    repo_full_name: Optional[str],
    pr_number: Optional[int],
    pr_url: Optional[str],
    head_sha: Optional[str],
    base_branch: str,
    dry_run: bool,
) -> None:
    """P1-L — draft PR 직후 work_mode 분기 stage 를 session.extra 에 stamp.

    세션이 없거나 PR 메타가 부족하면 silent skip (caller flow 영향 X).
    autonomous_merge 분기는 background 머지 루프가 pick, approval_required
    분기는 background producer 가 approval card 를 올릴 신호.
    """

    if not session_id or dry_run:
        return
    try:
        from ..workflow_state import load_session as _load
        from ..workflow_state import update_session as _update
        from dataclasses import replace as _replace
    except Exception:  # noqa: BLE001
        return
    try:
        session = _load(session_id)
    except Exception:  # noqa: BLE001
        return
    if session is None:
        return

    existing_extra = getattr(session, "extra", None) or {}
    if not isinstance(existing_extra, Mapping):
        existing_extra = {}

    decision = decide_post_pr_action(
        session_id=session_id,
        session_extra=existing_extra,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        pr_url=pr_url,
        head_sha=head_sha,
        base_branch=base_branch,
        dry_run=dry_run,
    )
    if decision.action == PostPRAction.SKIP:
        return

    merged_extra = dict(existing_extra)
    for key, value in decision.extra_updates.items():
        merged_extra[key] = value
    audit_entry = {
        "stage": merged_extra.get(EXTRA_PR_MERGE_STAGE),
        "action": decision.action.value,
        "reason": decision.reason,
        "job_id": job_id,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "head_sha": head_sha,
        "at": datetime.now(tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        ),
    }
    existing_audit = list(merged_extra.get(EXTRA_PR_MERGE_AUDIT) or ())
    existing_audit.append(audit_entry)
    merged_extra[EXTRA_PR_MERGE_AUDIT] = existing_audit

    try:
        updated = _replace(session, extra=merged_extra)
        _update(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        return

    # Operator-visible 줄. progress marker 도 한 줄 같이 찍어서 #봇-상태
    # 가 stage 변화를 timeline 위에서 본다.
    stamp_progress(
        session_id=session_id,
        marker="pr_merge_pending",
        detail={
            "job_id": job_id,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "work_mode_action": decision.action.value,
            "reason": decision.reason,
        },
    )


__all__ = (
    "stamp_progress",
    "stamp_pr_merge_continuation",
)
