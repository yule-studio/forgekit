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
# P1-I — bootstrap_required recovery. Re-used reason tokens from the
# worker so caller code can grep without circular import.
BOOTSTRAP_REQUIRED_REASON_PREFIX: str = "bootstrap_required"
# Sub-reasons within ``bootstrap_required:<sub>`` that the recovery
# hook considers *capability-driven* (env opt-in flips them). Other
# sub-reasons (e.g. ``scaffold_apply_failed:*``) need operator
# intervention and must NOT be auto-revived.
BOOTSTRAP_RECOVERABLE_SUB_TOKENS: tuple = (
    "editor_record_only_insufficient",
    "live_editor_unavailable",
)


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


def _is_bootstrap_capability_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """``YULE_CODING_EXECUTOR_GREENFIELD_BOOTSTRAP_ENABLED`` env gate."""

    import os as _os

    src = env if env is not None else _os.environ
    raw = (
        src.get("YULE_CODING_EXECUTOR_GREENFIELD_BOOTSTRAP_ENABLED") or ""
    ).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _classify_bootstrap_reason(reason: str) -> Optional[str]:
    """Return the recoverable sub-token name if *reason* qualifies, else None.

    ``reason`` is the full ``bootstrap_required:<sub>`` string. The classifier
    only ack-nowledges capability-driven sub-tokens — scaffold_apply_failed
    / unknown sub-reasons return None so caller skips.
    """

    text = (reason or "").strip()
    if not text.startswith(BOOTSTRAP_REQUIRED_REASON_PREFIX + ":"):
        return None
    sub = text[len(BOOTSTRAP_REQUIRED_REASON_PREFIX) + 1 :]
    for token in BOOTSTRAP_RECOVERABLE_SUB_TOKENS:
        if token in sub:
            return token
    return None


def recover_bootstrap_required_rows(
    queue: JobQueue,
    *,
    repo_resolver: Optional[Callable[[str], Optional[str]]] = None,
    update_session_fn: Optional[Callable[..., Any]] = None,
    load_session_fn: Optional[Callable[[str], Any]] = None,
    capability_enabled_fn: Optional[Callable[[], bool]] = None,
    max_per_run: int = 50,
    log_fn: Optional[Callable[[str, Optional[Any]], None]] = None,
) -> Tuple[str, ...]:
    """Scan ``coding_execute`` rows blocked at ``bootstrap_required:*`` and
    revive the ones whose capability dependency (greenfield bootstrap env
    + target repo checkout) is now satisfied.

    P1-I — canonical session ``11917bf1e75d`` 같은 row 가 operator 가
    ``YULE_CODING_EXECUTOR_GREENFIELD_BOOTSTRAP_ENABLED=1`` 로 opt-in
    한 직후 자동으로 ``state=queued`` 복귀하게 한다.

    Recoverable sub-tokens (NOT silent — log + audit):
      * ``editor_record_only_insufficient`` (greenfield + RecordOnly editor)
      * ``live_editor_unavailable`` (greenfield + env opt-in 안 됨)

    Non-recoverable (skip):
      * ``scaffold_apply_failed:*`` (operator intervention 필요)
      * 그 외 prefix 매치 안 됨 (unrelated terminal)

    Gates (모두 True 일 때만 revive):
      1. ``capability_enabled_fn()`` (default: env gate 검사) True
      2. ``repo_resolver(repo_full_name)`` 가 존재 디렉터리 반환

    Revive 동작:
      * ``failed_retryable`` → ``queue.requeue_retryable`` (attempt 카운터 +1)
      * ``failed_terminal`` → 직접 SQL ``_revive_failed_terminal_row``
        (state=queued, attempt=0, lease clear, revivals[] audit append)
      * session.extra 의 ``coding_blocked`` progress marker 도
        ``status=bootstrap_capability_enabled`` 로 stamp (load/update_session_fn
        주입 시).

    Returns the revived job_id 시퀀스.
    """

    db_path = getattr(queue, "_db_path", None)
    if db_path is None:
        return ()

    enabled_fn = capability_enabled_fn or _is_bootstrap_capability_enabled
    if not enabled_fn():
        # Env not opted in — every row stays as-is. No churn, no log noise.
        return ()

    try:
        with _sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = _sqlite3.Row
            rows = conn.execute(
                """
                SELECT job_id, state, payload_json, result_json,
                       session_id, attempt
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
            "coding_execute bootstrap-required recovery: sqlite query failed",
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
        sub_token = _classify_bootstrap_reason(reason)
        if sub_token is None:
            continue

        # P1-I: repo checkout dependency — bootstrap editor will only
        # succeed if the target repo is materialized.
        resolved, repo_full_name = _resolve_repo_for_request_payload(
            payload=payload, repo_resolver=repo_resolver
        )
        if resolved is None:
            # Capability is on but checkout still missing — let the
            # target_repo recovery hook handle the materialization
            # first. We skip this row this tick.
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
                    "coding_execute bootstrap recovery: requeue_retryable "
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
                        "reason": "bootstrap_capability_enabled",
                        "trigger_sub_token": sub_token,
                        "original_reason": reason,
                        "editor": "GreenfieldBootstrapEditor",
                        "env": "YULE_CODING_EXECUTOR_GREENFIELD_BOOTSTRAP_ENABLED",
                        "repo_full_name": repo_full_name,
                        "resolved_repo_path": resolved,
                    },
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "coding_execute bootstrap recovery: revive_failed_terminal "
                    "raised for %s",
                    job_id,
                    exc_info=True,
                )

        if not revived_ok:
            continue
        revived.append(job_id)
        _stamp_bootstrap_progress(
            session_id=session_id,
            sub_token=sub_token,
            resolved_repo=resolved,
            repo_full_name=repo_full_name,
            previous_state=state,
            load_session_fn=load_session_fn,
            update_session_fn=update_session_fn,
        )
        try:
            logger.warning(
                "coding_execute bootstrap-required recovery: revived %s "
                "(state=%s → queued, sub=%s, repo=%s, resolved=%s)",
                job_id,
                state,
                sub_token,
                repo_full_name,
                resolved,
            )
            if log_fn is not None:
                log_fn(
                    f"coding_execute bootstrap recovery: revived {job_id} "
                    f"(sub={sub_token}, repo={repo_full_name})",
                    None,
                )
        except Exception:  # noqa: BLE001
            pass

    return tuple(revived)


def _stamp_bootstrap_progress(
    *,
    session_id: str,
    sub_token: str,
    resolved_repo: str,
    repo_full_name: str,
    previous_state: str,
    load_session_fn: Optional[Callable[[str], Any]],
    update_session_fn: Optional[Callable[..., Any]],
) -> None:
    """Best-effort — flip ``coding_blocked`` marker to a
    ``status=bootstrap_capability_enabled`` detail so operator surface
    shows recovery happened (not silent revive).
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
            stamp_progress_marker,
        )
    except Exception:  # noqa: BLE001
        return
    try:
        new_extra = stamp_progress_marker(
            session_extra=getattr(session, "extra", None) or {},
            marker=PROGRESS_CODING_BLOCKED,
            detail={
                "status": "bootstrap_capability_enabled",
                "trigger_sub_token": sub_token,
                "resolved_repo_path": resolved_repo,
                "repo_full_name": repo_full_name,
                "previous_state": previous_state,
                "editor": "GreenfieldBootstrapEditor",
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


# P1-Z6 B — pre-PR transient failure recovery.  edit_timeout_live_editor
# / edit_subprocess_failed / commit_failed 같이 "한 번 더 시도하면 풀릴
# 가능성" 이 있는 reason 들.  structural failure (strategy_unresolved /
# write_scope_resolved_empty / forbidden_scope / bootstrap_required:
# scaffold_apply_failed 등) 는 본 recoverable set 에 포함 안 됨.
TRANSIENT_PRE_PR_RETRYABLE_REASONS: tuple = (
    "edit_timeout_live_editor",
    "edit_subprocess_failed",
    "edit_failed",  # 옛 generic reason — backward compat
)


def recover_transient_pre_pr_failures(
    queue: JobQueue,
    *,
    max_per_run: int = 50,
    log_fn: Optional[Callable[[str, Optional[Any]], None]] = None,
) -> Tuple[str, ...]:
    """P1-Z6 B — pre-PR transient ``failed_retryable`` 행을 requeue.

    ``edit_timeout_live_editor`` / ``edit_subprocess_failed`` 같이 transient
    성격의 reason 으로 떨어진 행은 ``requeue_retryable`` 로 재시도 시도.
    attempt 카운터는 자동 +1.  ``max_attempts`` 를 넘으면 queue 가
    terminal 로 떨어뜨림 — 본 함수가 attempt 폭주 만들지 않음.

    structural failure (``write_scope_resolved_empty`` /
    ``tech_lead_strategy_unresolved`` 등) 는 본 recoverable set 에 안
    들어가 절대 자동 retry 되지 않는다.

    반환: requeue 된 job_id 튜플.
    """

    db_path = getattr(queue, "_db_path", None)
    if db_path is None:
        return ()

    import json as _json
    import sqlite3 as _sqlite3

    try:
        with _sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = _sqlite3.Row
            rows = conn.execute(
                """
                SELECT job_id, result_json, attempt
                FROM job_queue
                WHERE job_type = 'coding_execute'
                  AND state = 'failed_retryable'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (int(max_per_run),),
            ).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning(
            "coding_execute transient retry sweep: sqlite query failed",
            exc_info=True,
        )
        return ()

    requeued: list[str] = []
    for row in rows or ():
        try:
            payload = _json.loads(row["result_json"] or "{}")
        except Exception:  # noqa: BLE001
            continue
        reason_raw = str(payload.get("reason") or payload.get("error") or "").strip().lower()
        # reason 토큰만 비교 (suffix detail 무시).  e.g. ``edit_timeout_live_editor:_SubprocessError``
        reason_token = reason_raw.split(":", 1)[0]
        if reason_token not in TRANSIENT_PRE_PR_RETRYABLE_REASONS:
            continue
        try:
            queue.requeue_retryable(row["job_id"])
            requeued.append(row["job_id"])
            logger.warning(
                "coding_execute transient retry: requeued %s (reason=%s, attempt=%s)",
                row["job_id"],
                reason_token,
                row["attempt"],
            )
            if log_fn is not None:
                try:
                    log_fn(
                        f"coding_execute transient retry: requeued {row['job_id']} "
                        f"(reason={reason_token})",
                        None,
                    )
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            logger.warning(
                "coding_execute transient retry: requeue failed for %s",
                row["job_id"],
                exc_info=True,
            )
            continue
    return tuple(requeued)


__all__ = (
    "BOOTSTRAP_RECOVERABLE_SUB_TOKENS",
    "BOOTSTRAP_REQUIRED_REASON_PREFIX",
    "TARGET_REPO_MISSING_REASON_PREFIX",
    "TRANSIENT_PRE_PR_RETRYABLE_REASONS",
    "_classify_bootstrap_reason",
    "_is_bootstrap_capability_enabled",
    "_revive_failed_terminal_row",
    "recover_bootstrap_required_rows",
    "recover_target_repo_missing_rows",
    "recover_transient_pre_pr_failures",
)
