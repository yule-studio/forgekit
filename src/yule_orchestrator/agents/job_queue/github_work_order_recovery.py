"""github_work_order recovery helpers — startup requeue + plan self-heal.

P0-V split: `github_work_order_executor.py` 가 process_job 분기 + recovery
helper 까지 다 들고 있으면 1000 LOC 초과 + 책임 5 종 이상이 되어
governance/code_audit split_now 위반에 걸린다. 본 모듈은 **recovery
책임만** 갖는다:

  * ``recover_plan_from_work_order`` — 옛 producer 가 plan 을 빠뜨리고
    enqueue 한 work_order 를 즉석에서 minimal RepoContract + default body
    fallback 으로 재구성.
  * ``requeue_no_repo_failures`` — `SKIPPED_NO_REPO` failed_retryable
    rows 를 자동 requeue.
  * ``requeue_missing_plan_failures`` — `SKIPPED_MISSING_PLAN` 동일.

executor 측은 본 모듈의 helper 만 부르고, recovery 로직은 모두 여기에
산다. pure SQLite read + ``queue.requeue_retryable`` — operator 수동 DB
조작 없음.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Tuple

from .github_work_order import GitHubWorkOrder, JOB_TYPE_GITHUB_WORK_ORDER
from .state_machine import JobState
from .store import JobQueue


# 동일 토큰은 executor 모듈에서 SSoT — 본 모듈은 의도적으로 caller 가
# 전달하게 해서 양쪽 reason 토큰이 silently 어긋나지 않게 한다.
DEFAULT_NO_REPO_REASON: str = "github_work_order_no_repo"
DEFAULT_MISSING_PLAN_REASON: str = "github_work_order_missing_plan_or_issue"


# ---------------------------------------------------------------------------
# Plan self-heal — payload 만으로 plan 재구성
# ---------------------------------------------------------------------------


def recover_plan_from_work_order(
    work_order: GitHubWorkOrder,
) -> Optional[Mapping[str, Any]]:
    """Reconstruct ``issue_auto_create_plan`` from work_order payload.

    조건:
      * existing_issue_number 가 있으면 None — caller 는 existing-anchor
        branch 로 흘러야 한다 (plan 으로 떨어지면 안 됨).
      * repo 가 비어있으면 None — caller 가 repo recovery 단계에서 미리
        채웠어야 한다. 아직도 비었다면 plan 도 만들 수 없다.
      * 그 외엔 ``_minimal_repo_contract`` 로 RepoContract 만들어
        ``build_issue_auto_create_plan`` fallback (default body) 호출 →
        plan dict 반환.

    실패 (import miss / repo_contract miss / build raise) 는 모두 None
    반환 — caller 는 그대로 SKIPPED_MISSING_PLAN 으로 떨어뜨려야 한다.
    """

    if (
        work_order.existing_issue_number is not None
        and int(work_order.existing_issue_number) > 0
    ):
        return None
    repo = (work_order.repo or "").strip()
    if not repo:
        return None
    contract = _minimal_repo_contract(repo)
    if contract is None:
        return None
    try:
        from ..github_workos.issue_auto_create import (
            build_issue_auto_create_plan,
        )
    except Exception:  # noqa: BLE001 - partial install
        return None
    try:
        outcome = build_issue_auto_create_plan(
            repo_contract=contract,
            request_summary=str(work_order.request_summary or "").strip(),
            session_id=str(work_order.session_id or "") or None,
        )
    except Exception:  # noqa: BLE001 - never crash the executor on recovery
        return None
    if outcome is None or outcome.plan is None:
        return None
    try:
        return outcome.plan.to_dict()
    except Exception:  # noqa: BLE001
        return None


def _minimal_repo_contract(repo: str):
    """``owner/name`` 문자열로부터 최소 RepoContract 생성.

    SSoT 는 ``discord/integrations/github_workos_adapter._minimal_repo_contract_from_repo``
    이지만 본 모듈은 ``agents/job_queue`` layer 라서 discord 측으로 import
    하면 layering 이 역방향이 된다. 5 줄짜리라 의도적으로 동일 로직을
    여기서 반복 — 양쪽 모두 ``RepoContract(owner, repo, fallback=True)``
    를 만들고 ``build_issue_auto_create_plan`` 이 default body 로 떨어
    뜨린다.
    """

    text = str(repo or "").strip()
    if not text or "/" not in text:
        return None
    owner, _, name = text.partition("/")
    owner = owner.strip()
    name = name.strip().rstrip(".git")
    if not owner or not name:
        return None
    try:
        from ..git.repo_contract import RepoContract
    except Exception:  # noqa: BLE001 - partial install
        return None
    return RepoContract(
        owner=owner,
        repo=name,
        fallback=True,
        failure_mode="executor_recovered_minimal_contract",
        backend=None,
    )


# ---------------------------------------------------------------------------
# Startup requeue hooks — restart 만으로 stranded rows 복구
# ---------------------------------------------------------------------------


def requeue_failed_rows_by_reason(
    queue: JobQueue,
    *,
    error_reasons: Tuple[str, ...],
    max_per_run: int = 50,
    backoff_seconds: float = 0.0,
    log_fn: Optional[Callable[[str, Optional[Any]], None]] = None,
) -> Tuple[str, ...]:
    """Generic — `failed_retryable` work_order rows 중 *error_reasons* 에
    매치하는 것만 requeue. ``requeue_no_repo_failures`` /
    ``requeue_missing_plan_failures`` 의 공통 백엔드.
    """

    import json as _json
    import sqlite3 as _sqlite3

    requeued: list[str] = []
    db_path = getattr(queue, "_db_path", None)
    if db_path is None:
        return ()
    try:
        with _sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = _sqlite3.Row
            rows = conn.execute(
                """
                SELECT job_id, result_json
                FROM job_queue
                WHERE job_type = ?
                  AND state = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (
                    JOB_TYPE_GITHUB_WORK_ORDER,
                    JobState.FAILED_RETRYABLE.value,
                    int(max_per_run),
                ),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - never crash the executor
        if log_fn is not None:
            try:
                log_fn(
                    "requeue_failed_rows_by_reason: sqlite query failed", exc
                )
            except Exception:  # noqa: BLE001
                pass
        return ()

    for row in rows or ():
        raw = row["result_json"] or "{}"
        try:
            payload = _json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        error = str(payload.get("error") or "").strip()
        if error not in error_reasons:
            continue
        try:
            queue.requeue_retryable(
                row["job_id"], backoff_seconds=backoff_seconds
            )
            requeued.append(row["job_id"])
            if log_fn is not None:
                try:
                    log_fn(
                        f"github_work_order: requeued failed_retryable "
                        f"row (error={error}, job_id={row['job_id']})",
                        None,
                    )
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            if log_fn is not None:
                try:
                    log_fn(
                        f"github_work_order: requeue failed for {row['job_id']}",
                        exc,
                    )
                except Exception:  # noqa: BLE001
                    pass
            continue
    return tuple(requeued)


def requeue_no_repo_failures(
    queue: JobQueue,
    *,
    error_reasons: Tuple[str, ...] = (DEFAULT_NO_REPO_REASON,),
    max_per_run: int = 50,
    backoff_seconds: float = 0.0,
    log_fn: Optional[Callable[[str, Optional[Any]], None]] = None,
) -> Tuple[str, ...]:
    """Startup recovery hook — `SKIPPED_NO_REPO` failed_retryable rows 자동
    requeue.

    Live smoke (session ``c5278a9043f2`` 후속): producer bug 로 work_order
    payload 가 repo 없이 enqueue 됐고, executor 가 SKIPPED_NO_REPO 로
    failed_retryable 처리한 row 들이 fix 후 자동 재실행되지 않고 stranded.
    이 helper 가 supervisor / executor startup 시 한 번 호출돼 그런 rows
    를 자동으로 requeue 한다.
    """

    return requeue_failed_rows_by_reason(
        queue,
        error_reasons=error_reasons,
        max_per_run=max_per_run,
        backoff_seconds=backoff_seconds,
        log_fn=log_fn,
    )


def requeue_missing_plan_failures(
    queue: JobQueue,
    *,
    max_per_run: int = 50,
    backoff_seconds: float = 0.0,
    log_fn: Optional[Callable[[str, Optional[Any]], None]] = None,
) -> Tuple[str, ...]:
    """Startup recovery hook — `SKIPPED_MISSING_PLAN` failed_retryable
    rows 자동 requeue.

    P0-V live smoke fix: producer 가 plan 없이 enqueue 한 row 들은
    ``SKIPPED_MISSING_PLAN`` 으로 failed_retryable 처리됐다.
    `recover_plan_from_work_order` 가 들어간 지금은 같은 row 를 다시
    pick 하면 plan 을 즉석에서 재구성해서 성공할 수 있다.
    """

    return requeue_failed_rows_by_reason(
        queue,
        error_reasons=(DEFAULT_MISSING_PLAN_REASON,),
        max_per_run=max_per_run,
        backoff_seconds=backoff_seconds,
        log_fn=log_fn,
    )


def repair_stranded_coding_sessions(
    queue: JobQueue,
    *,
    load_session_fn=None,
    update_session_fn=None,
    max_per_run: int = 50,
    log_fn: Optional[Callable[[str, Optional[Any]], None]] = None,
) -> Tuple[str, ...]:
    """Startup sweep — github_work_order SAVED rows whose continuation
    stalled at ``no_coding_proposal`` get repaired in place.

    P0-X live smoke fix (canonical session ``11917bf1e75d``):

      * executor 가 issue anchor 까지 만들고 SAVED 로 transition 했지만
        ``coding_dispatch_queued=False`` + ``coding_dispatch_noop_reason
        =no_coding_proposal`` 상태로 남음.
      * 새 self-heal (``promote_session_to_coding_ready`` 의 auto_rebuild)
        은 다음 enqueue 부터 작동하지만, 이미 SAVED 로 떨어진 row 는
        worker 가 다시 pick 하지 않음.
      * 본 sweep 이 SAVED rows 를 직접 스캔해 각 session 에 대해
        :func:`work_order_coding_continuation.repair_session_for_coding_dispatch`
        를 호출한다. operator 가 runtime restart 하나로 stranded session
        들이 모두 coding_execute 단계로 흘러가게 한다.

    pure SQLite read + ``repair_session_for_coding_dispatch`` — operator
    수동 DB 조작 없이 작동.

    Defaults: ``load_session_fn`` 가 None 이면 workflow_state.load_session,
    ``update_session_fn`` 이 None 이면 workflow_state.update_session 의
    표준 patterns 를 사용한다. 테스트는 모두 injection.

    Returns repaired session_id 시퀀스 (audit / logging).
    """

    import json as _json
    import sqlite3 as _sqlite3

    if load_session_fn is None or update_session_fn is None:
        try:
            from ..workflow_state import (
                load_session as _default_load,
                update_session as _default_update,
            )
            from datetime import datetime as _dt
        except Exception as exc:  # noqa: BLE001 - partial install
            if log_fn is not None:
                try:
                    log_fn(
                        "repair_stranded_coding_sessions: workflow_state import failed",
                        exc,
                    )
                except Exception:  # noqa: BLE001
                    pass
            return ()
        if load_session_fn is None:
            load_session_fn = _default_load
        if update_session_fn is None:

            def _persist(session, _new_extra):  # noqa: ANN001
                try:
                    _default_update(session, now=_dt.now().astimezone())
                except Exception:  # noqa: BLE001 - never crash sweep
                    pass

            update_session_fn = _persist

    db_path = getattr(queue, "_db_path", None)
    if db_path is None:
        return ()

    try:
        with _sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = _sqlite3.Row
            rows = conn.execute(
                """
                SELECT job_id, payload_json, result_json
                FROM job_queue
                WHERE job_type = ?
                  AND state = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (
                    JOB_TYPE_GITHUB_WORK_ORDER,
                    "saved",
                    int(max_per_run),
                ),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - never crash startup
        if log_fn is not None:
            try:
                log_fn(
                    "repair_stranded_coding_sessions: sqlite query failed",
                    exc,
                )
            except Exception:  # noqa: BLE001
                pass
        return ()

    try:
        from .work_order_coding_continuation import (
            REPAIR_OUTCOME_REPAIRED,
            repair_session_for_coding_dispatch,
        )
    except Exception as exc:  # noqa: BLE001
        if log_fn is not None:
            try:
                log_fn(
                    "repair_stranded_coding_sessions: repair helper import failed",
                    exc,
                )
            except Exception:  # noqa: BLE001
                pass
        return ()

    repaired: list[str] = []
    seen_sessions: set[str] = set()
    for row in rows or ():
        result_raw = row["result_json"] or "{}"
        try:
            result = _json.loads(result_raw)
        except Exception:  # noqa: BLE001
            continue
        # only sweep rows that explicitly stalled at no_coding_proposal
        noop_reason = str(result.get("coding_dispatch_noop_reason") or "").strip()
        coding_queued = bool(result.get("coding_dispatch_queued", False))
        if coding_queued or noop_reason != "no_coding_proposal":
            continue

        payload_raw = row["payload_json"] or "{}"
        try:
            payload = _json.loads(payload_raw)
        except Exception:  # noqa: BLE001
            continue
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id or session_id in seen_sessions:
            continue
        seen_sessions.add(session_id)

        try:
            outcome = repair_session_for_coding_dispatch(
                session_id=session_id,
                load_session_fn=load_session_fn,
                update_session_fn=update_session_fn,
            )
        except Exception as exc:  # noqa: BLE001 - per-session failure is local
            if log_fn is not None:
                try:
                    log_fn(
                        f"repair_stranded_coding_sessions: session {session_id} "
                        "raised",
                        exc,
                    )
                except Exception:  # noqa: BLE001
                    pass
            continue

        if outcome.outcome == REPAIR_OUTCOME_REPAIRED and outcome.promoted:
            repaired.append(session_id)
            if log_fn is not None:
                try:
                    log_fn(
                        f"github_work_order: repaired stranded session "
                        f"{session_id} — coding_proposal rebuilt + promoted",
                        None,
                    )
                except Exception:  # noqa: BLE001
                    pass

    return tuple(repaired)


__all__ = (
    "DEFAULT_MISSING_PLAN_REASON",
    "DEFAULT_NO_REPO_REASON",
    "recover_plan_from_work_order",
    "repair_stranded_coding_sessions",
    "requeue_failed_rows_by_reason",
    "requeue_missing_plan_failures",
    "requeue_no_repo_failures",
)
