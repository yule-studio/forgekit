"""Progress surface for coding_execute outcomes — Round 3 of #73.

After a ``coding_execute`` job lands (success / fail / blocked) the
runtime needs to leave a trail in two places operators / users
already watch:

  * Obsidian — a ``task-log`` note appended via the existing
    ``obsidian_write`` queue. Same format as discussion / research
    progress notes so the operator's vault stays consistent.
  * GitHub — a PR comment on the draft PR the executor opened.

This module is the **producer** for both. It does not perform either
write directly; instead it:

  * normalises a :class:`CodingExecuteOutcome` into a small
    :class:`ProgressEntry` payload,
  * enqueues an ``obsidian_write`` job (tagged
    ``note_kind="task-log"`` — does NOT require approval),
  * (optionally) calls a GitHub PR comment poster the caller
    injects (typically wired around
    :meth:`LiveGithubAppClient.create_issue_comment`),
  * appends the entry to ``session.extra['coding_execute_progress']``
    so the next task selector / status diagnostic can see "what
    just happened" without scraping the queue.

Hard rails:

  * Obsidian write is enqueued, never written inline — the writer
    worker still owns vault permissions / approval guards.
  * GitHub poster is fail-safe: a 4xx from GitHub logs + returns,
    the rest of the pipeline still records the entry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Mapping,
    Optional,
    Tuple,
)

from .coding_execute_dispatcher import SESSION_EXTRA_DISPATCH_KEY
from .coding_executor_worker import (
    CodingExecuteOutcome,
    CodingExecuteRequest,
)
from .obsidian_writer_worker import (
    NOTE_KIND_RESEARCH_LOG,
    ObsidianWriteRequest,
    ObsidianWriterWorker,
)


logger = logging.getLogger(__name__)


SESSION_EXTRA_PROGRESS_KEY: str = "coding_execute_progress"
TASK_LOG_NOTE_KIND: str = "task-log"


GithubPRCommentFn = Callable[..., Any]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgressEntry:
    """Structured snapshot of what an outcome should record.

    Designed so :func:`render_progress_markdown` produces a stable
    Discord / PR comment / Obsidian body without the caller having
    to hand-render every field.
    """

    session_id: str
    executor_role: str
    branch: str
    completion_status: str
    reason: str
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    commit_sha: Optional[str] = None
    test_summary: Mapping[str, Any] = field(default_factory=dict)
    at: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "session_id": self.session_id,
            "executor_role": self.executor_role,
            "branch": self.branch,
            "completion_status": self.completion_status,
            "reason": self.reason,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "commit_sha": self.commit_sha,
            "test_summary": dict(self.test_summary),
            "at": self.at,
        }


@dataclass(frozen=True)
class ProgressOutcome:
    """Returned from :func:`record_coding_execute_progress`.

    Captures whether each side-effect succeeded so the caller can
    log / surface degraded states. Persistence failures are
    represented as ``False`` flags rather than exceptions — the
    progress entry is still recorded onto the in-memory session
    extras so callers keep observability even when the writers
    are misconfigured.
    """

    entry: ProgressEntry
    obsidian_job_id: Optional[str] = None
    obsidian_skipped_reason: Optional[str] = None
    github_comment_posted: bool = False
    github_comment_error: Optional[str] = None
    session_persisted: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def status_from_outcome(outcome: CodingExecuteOutcome) -> str:
    """Map an executor outcome's terminal state to the standard vocab."""

    state = (outcome.terminal_state or "").lower()
    if state == "saved":
        return "done"
    if state == "failed_retryable":
        return "retry_ready"
    if state == "failed_terminal":
        return "blocked"
    return "blocked"


def build_progress_entry(
    outcome: CodingExecuteOutcome,
    *,
    request: Optional[CodingExecuteRequest] = None,
    completion_status: Optional[str] = None,
    when: Optional[datetime] = None,
) -> ProgressEntry:
    """Lift a worker outcome (+ originating request) into a ProgressEntry."""

    job = outcome.job
    payload: Mapping[str, Any] = (job.payload if job is not None else {}) or {}

    session_id = ""
    executor_role = ""
    if request is not None:
        session_id = request.session_id
        executor_role = request.executor_role
    if not session_id:
        session_id = str(payload.get("session_id") or "")
    if not executor_role:
        executor_role = str(payload.get("executor_role") or "")

    when_iso = (when or datetime.now(tz=timezone.utc)).replace(microsecond=0).isoformat()
    return ProgressEntry(
        session_id=session_id,
        executor_role=executor_role,
        branch=outcome.branch or "",
        completion_status=completion_status or status_from_outcome(outcome),
        reason=outcome.failure_reason or "",
        pr_number=outcome.pr_number,
        pr_url=outcome.pr_url,
        commit_sha=outcome.commit_sha,
        test_summary=dict(outcome.test_summary or {}),
        at=when_iso,
    )


def render_progress_markdown(entry: ProgressEntry) -> str:
    """Render *entry* as the body shared between PR comment + task-log.

    Keeps the structure deterministic so the Obsidian writer and the
    GitHub PR comment surface the same content, and the operator
    can grep across both worlds for a single ``session_id``.
    """

    lines: list[str] = []
    lines.append(
        f"## 🤖 coding-executor — {entry.completion_status} (executor=`{entry.executor_role}`)"
    )
    lines.append("")
    if entry.session_id:
        lines.append(f"- session: `{entry.session_id}`")
    if entry.branch:
        lines.append(f"- branch: `{entry.branch}`")
    if entry.commit_sha:
        lines.append(f"- commit: `{entry.commit_sha[:10]}`")
    if entry.pr_number is not None:
        url = entry.pr_url or ""
        if url:
            lines.append(f"- PR: [#{entry.pr_number}]({url})")
        else:
            lines.append(f"- PR: #{entry.pr_number}")
    if entry.reason:
        lines.append(f"- 사유: {entry.reason}")
    if entry.test_summary:
        lines.append("- tests:")
        for key in ("status", "command", "exit_code", "dry_run", "stderr_tail"):
            if key in entry.test_summary:
                value = entry.test_summary[key]
                lines.append(f"  - {key}: `{value}`")
    lines.append("")
    lines.append(f"_{entry.at}_")
    return "\n".join(lines)


def append_progress_history(
    extra: Mapping[str, Any], entry: ProgressEntry
) -> Mapping[str, Any]:
    """Append *entry* to ``coding_execute_progress`` (bounded list).

    Pure transform — returns a new mapping the caller persists.
    History capped at 50 entries so a long-running session doesn't
    grow ``session.extra`` unbounded.
    """

    base: dict = dict(extra or {})
    history_raw = base.get(SESSION_EXTRA_PROGRESS_KEY)
    history: list = list(history_raw) if isinstance(history_raw, (list, tuple)) else []
    history.append(dict(entry.to_payload()))
    if len(history) > 50:
        history = history[-50:]
    base[SESSION_EXTRA_PROGRESS_KEY] = history
    return base


def record_coding_execute_progress(
    *,
    session: Any,
    outcome: CodingExecuteOutcome,
    request: Optional[CodingExecuteRequest] = None,
    completion_status: Optional[str] = None,
    obsidian_writer: Optional[ObsidianWriterWorker] = None,
    github_comment_fn: Optional[GithubPRCommentFn] = None,
    update_session_fn: Optional[Callable[..., Any]] = None,
    repo_full_name: Optional[str] = None,
    when: Optional[datetime] = None,
) -> ProgressOutcome:
    """End-to-end progress recorder.

    Wraps the four side-effects (entry build, history append,
    obsidian enqueue, GitHub PR comment) under one call. Each
    side-effect is tolerant of the other being absent — a session
    with no GitHub comment fn still gets the Obsidian write +
    history; a session with no Obsidian writer still posts the
    PR comment.
    """

    entry = build_progress_entry(
        outcome,
        request=request,
        completion_status=completion_status,
        when=when,
    )

    new_extra = append_progress_history(getattr(session, "extra", None) or {}, entry)
    persisted = _persist_session_extra(
        session,
        new_extra,
        update_session_fn=update_session_fn,
        when=when,
    )

    obsidian_job_id, obsidian_skipped_reason = _maybe_enqueue_obsidian(
        writer=obsidian_writer, entry=entry
    )
    posted, comment_error = _maybe_post_github_comment(
        post_fn=github_comment_fn,
        entry=entry,
        repo_full_name=repo_full_name,
    )
    return ProgressOutcome(
        entry=entry,
        obsidian_job_id=obsidian_job_id,
        obsidian_skipped_reason=obsidian_skipped_reason,
        github_comment_posted=posted,
        github_comment_error=comment_error,
        session_persisted=persisted,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _persist_session_extra(
    session: Any,
    new_extra: Mapping[str, Any],
    *,
    update_session_fn: Optional[Callable[..., Any]] = None,
    when: Optional[datetime] = None,
) -> bool:
    if session is None:
        return False
    try:
        from dataclasses import replace as _replace
        updated = _replace(session, extra=dict(new_extra))
    except Exception:  # noqa: BLE001
        return False
    persist = update_session_fn or _default_update_session
    try:
        persist(updated, now=(when or datetime.now(tz=timezone.utc)))
    except Exception:  # noqa: BLE001
        logger.warning(
            "coding_execute_progress: persisting session.extra raised",
            exc_info=True,
        )
        return False
    return True


def _default_update_session(session: Any, *, now: datetime) -> Any:
    from ..workflow_state import update_session

    return update_session(session, now=now)


def _maybe_enqueue_obsidian(
    *,
    writer: Optional[ObsidianWriterWorker],
    entry: ProgressEntry,
) -> Tuple[Optional[str], Optional[str]]:
    if writer is None:
        return None, "no_obsidian_writer"
    if not entry.session_id:
        return None, "missing_session_id"
    try:
        request = ObsidianWriteRequest(
            session_id=entry.session_id,
            note_kind=TASK_LOG_NOTE_KIND,
            title=f"coding-executor — {entry.completion_status} ({entry.executor_role})",
            metadata={
                "kind": "coding_execute_progress",
                "branch": entry.branch,
                "commit_sha": entry.commit_sha,
                "pr_number": entry.pr_number,
                "pr_url": entry.pr_url,
                "completion_status": entry.completion_status,
                "executor_role": entry.executor_role,
                "reason": entry.reason,
                "rendered_markdown": render_progress_markdown(entry),
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "coding_execute_progress: ObsidianWriteRequest construction raised",
            exc_info=True,
        )
        return None, "request_build_failed"

    try:
        job, _ = writer.enqueue(request)
    except Exception:  # noqa: BLE001
        logger.warning(
            "coding_execute_progress: writer.enqueue raised", exc_info=True
        )
        return None, "enqueue_failed"
    return job.job_id, None


def _maybe_post_github_comment(
    *,
    post_fn: Optional[GithubPRCommentFn],
    entry: ProgressEntry,
    repo_full_name: Optional[str],
) -> Tuple[bool, Optional[str]]:
    if post_fn is None:
        return False, None
    if entry.pr_number is None:
        # No PR yet — happens for dry-run / failed-before-push outcomes.
        return False, "no_pr"
    body = render_progress_markdown(entry)
    try:
        post_fn(
            repo=repo_full_name or "",
            pr_number=int(entry.pr_number),
            body=body,
        )
    except Exception as exc:  # noqa: BLE001 - never crash on GitHub blip
        logger.warning(
            "coding_execute_progress: github_comment_fn raised", exc_info=True
        )
        return False, str(exc)[:200]
    return True, None


def make_github_pr_comment_fn(
    live_client: Any,
) -> GithubPRCommentFn:
    """Wrap a :class:`LiveGithubAppClient` into a poster callable.

    The wrapper hides the LiveGithubAppClient method shape from the
    progress recorder so a future CLI / Discord poster can swap in
    a different surface without touching the recorder.
    """

    def _post(*, repo: str, pr_number: int, body: str) -> Any:
        return live_client.create_issue_comment(
            repo=repo, issue_number=int(pr_number), body=body
        )

    return _post


__all__ = (
    "GithubPRCommentFn",
    "ProgressEntry",
    "ProgressOutcome",
    "SESSION_EXTRA_PROGRESS_KEY",
    "TASK_LOG_NOTE_KIND",
    "append_progress_history",
    "build_progress_entry",
    "make_github_pr_comment_fn",
    "record_coding_execute_progress",
    "render_progress_markdown",
    "status_from_outcome",
)
