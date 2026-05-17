"""P1-D — recovery hooks for ``coding_execute`` rows blocked on
environmental state (target repo checkout missing) so a later operator
fix (cloning the repo / setting env mapping) auto-revives the row
without requiring a new intake.

Companion to :mod:`github_work_order_recovery` — same SSoT pattern:
``failed_retryable`` / ``failed_terminal`` rows with a recoverable
reason get scanned at startup (and optionally periodically) and, when
the underlying infra is back, ``requeue_retryable`` or direct revive.

The recovery hook is **safe to run repeatedly** — it only requeues when
the resolver confirms the checkout is now present. operator surface
(audit/log) makes it loud when it actually flips state.
"""

from __future__ import annotations

import json as _json
import logging
import sqlite3 as _sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Tuple

from .state_machine import JobState
from .store import JobQueue


logger = logging.getLogger(__name__)


TARGET_REPO_MISSING_REASON_PREFIX: str = "target_repo_checkout_missing"


# ---------------------------------------------------------------------------
# Per-row helpers
# ---------------------------------------------------------------------------


def _resolve_repo_for_request_payload(
    *,
    payload: Mapping[str, Any],
    repo_resolver: Optional[Callable[[str], Optional[str]]] = None,
) -> Tuple[Optional[str], str]:
    """Run *repo_resolver* (or the default cross-repo resolver) against
    the row's payload ``repo_full_name``. Return ``(resolved_path,
    repo_full_name)``.

    ``resolved_path`` None means checkout 여전히 부재 → caller skips.
    """

    repo_full_name = str(payload.get("repo_full_name") or "").strip()
    if not repo_full_name:
        return None, ""
    if repo_resolver is not None:
        try:
            resolved = repo_resolver(repo_full_name)
        except Exception:  # noqa: BLE001 - never crash sweep
            return None, repo_full_name
        if isinstance(resolved, str) and resolved.strip() and Path(resolved).is_dir():
            return resolved, repo_full_name
        return None, repo_full_name
    try:
        from .coding_executor_live import _default_repo_root_resolver
    except Exception:  # noqa: BLE001 - partial install
        return None, repo_full_name
    import os as _os

    orchestrator_root = (
        _os.environ.get("YULE_CODING_EXECUTOR_REPO_ROOT")
        or _os.environ.get("YULE_REPO_ROOT")
        or _os.getcwd()
    )
    try:
        resolved, _ = _default_repo_root_resolver(
            repo_full_name, orchestrator_repo_root=orchestrator_root
        )
    except Exception:  # noqa: BLE001
        return None, repo_full_name
    if resolved and Path(resolved).is_dir():
        return resolved, repo_full_name
    return None, repo_full_name


def _revive_failed_terminal_row(
    *,
    db_path: Path,
    job_id: str,
    note: Mapping[str, Any],
) -> bool:
    """Direct SQL revive for a ``failed_terminal`` coding_execute row.

    state machine forbids FAILED_TERMINAL → QUEUED transition (terminal
    is end-state), but operator-driven environmental recovery is exactly
    the case where we want the row to live again. The revive is bounded
    (only on explicit recovery-hook call) and is loud — caller logs each
    revive at warning level.

    Returns True on success, False if the row state moved out of
    failed_terminal between check and write (race).
    """

    now_ts = datetime.now(tz=timezone.utc).timestamp()
    with _sqlite3.connect(str(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT state, attempt, result_json FROM job_queue WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return False
        current_state = row[0]
        if current_state != JobState.FAILED_TERMINAL.value:
            conn.execute("ROLLBACK")
            return False
        try:
            current_result = _json.loads(row[2] or "{}")
        except Exception:  # noqa: BLE001
            current_result = {}
        if not isinstance(current_result, dict):
            current_result = {}
        current_result.setdefault("revivals", []).append(
            {
                "at": now_ts,
                "from_state": current_state,
                **dict(note or {}),
            }
        )
        conn.execute(
            """
            UPDATE job_queue
            SET state = ?,
                attempt = 0,
                available_at = ?,
                picked_by = NULL,
                picked_until = NULL,
                result_json = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (
                JobState.QUEUED.value,
                now_ts,
                _json.dumps(current_result, ensure_ascii=False),
                now_ts,
                job_id,
            ),
        )
        conn.execute("COMMIT")
    return True


# ---------------------------------------------------------------------------
# Public sweep
# ---------------------------------------------------------------------------


def recover_target_repo_missing_rows(
    queue: JobQueue,
    *,
    repo_resolver: Optional[Callable[[str], Optional[str]]] = None,
    update_session_fn: Optional[Callable[..., Any]] = None,
    load_session_fn: Optional[Callable[[str], Any]] = None,
    max_per_run: int = 50,
    log_fn: Optional[Callable[[str, Optional[Any]], None]] = None,
) -> Tuple[str, ...]:
    """Scan ``coding_execute`` rows blocked on missing target repo
    checkout. For each whose ``repo_full_name`` now resolves to an
    existing local directory, revive the row so the executor picks it
    up on the next tick.

    Both ``failed_retryable`` and ``failed_terminal`` rows are eligible
    — the worker initially writes ``failed_retryable`` (per-row
    max_attempts then naturally → ``failed_terminal``); both states
    represent the *same* recoverable env situation. The recovery hook
    is the single SSoT that knows "OK, env back, revive".

    Loud audit:
      * Warning log per revive (silent heal 금지).
      * Per-session progress marker ``coding_blocked`` gets a
        ``status=repo_found_revived`` detail when ``load_session_fn`` /
        ``update_session_fn`` are injected (production path uses
        ``workflow_state``).

    Returns the tuple of revived job_id 시퀀스.
    """

    db_path = getattr(queue, "_db_path", None)
    if db_path is None:
        return ()

    try:
        with _sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = _sqlite3.Row
            rows = conn.execute(
                """
                SELECT job_id, state, payload_json, result_json, session_id, attempt
                FROM job_queue
                WHERE job_type = 'coding_execute'
                  AND state IN ('failed_retryable', 'failed_terminal')
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (int(max_per_run),),
            ).fetchall()
    except Exception:  # noqa: BLE001 - never crash startup/tick
        logger.warning(
            "coding_execute target-repo recovery: sqlite query failed",
            exc_info=True,
        )
        return ()

    revived: list[str] = []
    for row in rows or ():
        result_raw = row["result_json"] or "{}"
        payload_raw = row["payload_json"] or "{}"
        try:
            result = _json.loads(result_raw)
            payload = _json.loads(payload_raw)
        except Exception:  # noqa: BLE001
            continue
        reason = str(result.get("reason") or result.get("error") or "").strip()
        if not reason.startswith(TARGET_REPO_MISSING_REASON_PREFIX):
            continue

        resolved, repo_full_name = _resolve_repo_for_request_payload(
            payload=payload, repo_resolver=repo_resolver
        )
        if resolved is None:
            # Still missing — operator hasn't cloned / set env yet. skip.
            continue

        state = row["state"]
        job_id = row["job_id"]
        session_id = row["session_id"] or str(payload.get("session_id") or "")

        revived_ok = False
        if state == JobState.FAILED_RETRYABLE.value:
            try:
                queue.requeue_retryable(job_id)
                revived_ok = True
            except Exception:  # noqa: BLE001
                logger.warning(
                    "coding_execute target-repo recovery: requeue_retryable "
                    "raised for %s",
                    job_id,
                    exc_info=True,
                )
        else:  # FAILED_TERMINAL — direct SQL revive
            try:
                revived_ok = _revive_failed_terminal_row(
                    db_path=Path(str(db_path)),
                    job_id=job_id,
                    note={
                        "reason": "target_repo_checkout_appeared",
                        "resolved_repo_path": resolved,
                        "repo_full_name": repo_full_name,
                    },
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "coding_execute target-repo recovery: revive_failed_terminal "
                    "raised for %s",
                    job_id,
                    exc_info=True,
                )

        if not revived_ok:
            continue
        revived.append(job_id)
        _stamp_session_progress(
            session_id=session_id,
            resolved_repo=resolved,
            repo_full_name=repo_full_name,
            previous_state=state,
            load_session_fn=load_session_fn,
            update_session_fn=update_session_fn,
        )
        try:
            logger.warning(
                "coding_execute target-repo recovery: revived %s "
                "(state=%s → queued, repo=%s, resolved=%s)",
                job_id,
                state,
                repo_full_name,
                resolved,
            )
            if log_fn is not None:
                log_fn(
                    f"coding_execute recovery: revived {job_id} "
                    f"(repo={repo_full_name}, resolved={resolved})",
                    None,
                )
        except Exception:  # noqa: BLE001
            pass

    return tuple(revived)


def _stamp_session_progress(
    *,
    session_id: str,
    resolved_repo: str,
    repo_full_name: str,
    previous_state: str,
    load_session_fn: Optional[Callable[[str], Any]],
    update_session_fn: Optional[Callable[..., Any]],
) -> None:
    """Best-effort — flip the session's ``coding_blocked`` marker detail
    to a ``repo_found_revived`` status so operator surface shows
    recovery happened (not just silent revive).
    """

    if not session_id or load_session_fn is None or update_session_fn is None:
        return
    try:
        session = load_session_fn(session_id)
    except Exception:  # noqa: BLE001
        return
    if session is None:
        return
    try:
        from .work_order_coding_continuation import (
            PROGRESS_CODING_BLOCKED,
            SESSION_EXTRA_PROGRESS_KEY,
            stamp_progress_marker,
        )
    except Exception:  # noqa: BLE001
        return
    try:
        new_extra = stamp_progress_marker(
            session_extra=getattr(session, "extra", None) or {},
            marker=PROGRESS_CODING_BLOCKED,
            detail={
                "status": "repo_found_revived",
                "resolved_repo_path": resolved_repo,
                "repo_full_name": repo_full_name,
                "previous_state": previous_state,
            },
        )
    except Exception:  # noqa: BLE001
        return
    try:
        from dataclasses import replace as _replace
        updated = _replace(session, extra=new_extra)
    except TypeError:
        try:
            session.extra = new_extra  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return
        updated = session
    try:
        update_session_fn(updated, dict(new_extra))
    except Exception:  # noqa: BLE001
        pass


__all__ = (
    "TARGET_REPO_MISSING_REASON_PREFIX",
    "_revive_failed_terminal_row",
    "recover_target_repo_missing_rows",
)
