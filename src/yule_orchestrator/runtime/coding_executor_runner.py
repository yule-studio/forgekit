"""Coding executor runner — P0-Y producer/consumer decoupling.

배경
----
이전 wiring 은 ``dispatch_ready_coding_jobs`` 를 ``_process(job)`` 안에서
호출했다. 즉 ``coding_execute`` 큐에 job 이 이미 있을 때만 producer 가
돌았고, 큐가 비어있으면 producer tick 이 **영원히 안 도는 deadlock**.
canonical session ``11917bf1e75d`` 가 coding_job=ready 까지 도달했지만
``coding_execute queued=0`` 인 채로 멈춘 이유.

본 모듈은 그 deadlock 을 끊는다:

  * 별도 background asyncio task 로 ``dispatch_ready_coding_jobs`` 를
    주기적으로 호출.
  * 일반 consumer (run_worker_loop) 는 본인의 일만 한다.
  * 큐가 비어있어도 producer 가 ready coding_job session 을 발견하면
    enqueue → consumer 가 다음 tick 에 pick.

shutdown 시 두 task 모두 graceful cancel. producer raise 는 swallow —
consumer 가 죽지 않게 한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Mapping, Optional

from ..agents.job_queue import (
    CodingExecutorWorker,
    HeartbeatStore,
    JobQueue,
    dispatch_ready_coding_jobs,
)
from ..agents.job_queue.coding_executor_worker import CodingExecuteOutcome
from ..agents.job_queue.worker_loop import run_worker_loop
from .services import ServiceSpec


logger = logging.getLogger(__name__)


EXIT_OK: int = 0


ENV_PRODUCER_INTERVAL: str = "YULE_CODING_EXECUTE_PRODUCER_INTERVAL_SECONDS"
DEFAULT_PRODUCER_INTERVAL_SECONDS: float = 10.0


# P1-A long-running coding_execute lease/keepalive — coding pipeline
# (worktree + edits + tests + commit + push + PR) easily exceeds the
# default 60s lease. Initial lease is large; keepalive ticker extends
# it while process_job is running so the supervisor reaper doesn't
# bounce an active job to ``failed_retryable``.
ENV_PICK_LEASE_SECONDS: str = "YULE_CODING_EXECUTE_PICK_LEASE_SECONDS"
DEFAULT_PICK_LEASE_SECONDS: float = 900.0  # 15 min initial lease
ENV_KEEPALIVE_INTERVAL: str = "YULE_CODING_EXECUTE_KEEPALIVE_INTERVAL_SECONDS"
DEFAULT_KEEPALIVE_INTERVAL_SECONDS: float = 30.0


def _resolve_producer_interval() -> float:
    raw = (os.environ.get(ENV_PRODUCER_INTERVAL) or "").strip()
    if not raw:
        return DEFAULT_PRODUCER_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PRODUCER_INTERVAL_SECONDS
    return max(2.0, value)


def _resolve_pick_lease_seconds() -> float:
    raw = (os.environ.get(ENV_PICK_LEASE_SECONDS) or "").strip()
    if not raw:
        return DEFAULT_PICK_LEASE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PICK_LEASE_SECONDS
    return max(60.0, value)


def _resolve_keepalive_interval_seconds() -> float:
    raw = (os.environ.get(ENV_KEEPALIVE_INTERVAL) or "").strip()
    if not raw:
        return DEFAULT_KEEPALIVE_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_KEEPALIVE_INTERVAL_SECONDS
    return max(5.0, value)


async def _lease_keepalive_loop(
    *,
    queue: JobQueue,
    job_id: str,
    worker_id: str,
    lease_seconds: float,
    interval_seconds: float,
    done_event: asyncio.Event,
) -> None:
    """Background ticker that refreshes ``picked_until`` every
    *interval_seconds* until *done_event* is set.

    Failure modes:
      * ``queue.renew_lease`` 가 None 반환 → job 이 이미 다른 경로로
        terminate / reap 됨. ticker 도 정리.
      * raise → swallow + 다음 tick (DB blip 한 번에 keepalive 가 죽지
        않게).
    """

    while not done_event.is_set():
        try:
            refreshed = queue.renew_lease(
                job_id,
                lease_seconds=lease_seconds,
                worker_id=worker_id,
            )
        except Exception:  # noqa: BLE001 - never crash the keepalive
            logger.warning(
                "coding_execute keepalive: renew_lease raised (job=%s)",
                job_id,
                exc_info=True,
            )
            refreshed = None
        if refreshed is None:
            logger.info(
                "coding_execute keepalive: job %s no longer active "
                "(state changed or row missing) — keepalive exiting",
                job_id,
            )
            return
        try:
            await asyncio.wait_for(
                done_event.wait(), timeout=interval_seconds
            )
        except asyncio.TimeoutError:
            continue


ENV_TARGET_REPO_RECOVERY_INTERVAL: str = (
    "YULE_CODING_EXECUTE_TARGET_REPO_RECOVERY_INTERVAL_SECONDS"
)
DEFAULT_TARGET_REPO_RECOVERY_INTERVAL_SECONDS: float = 60.0


def _resolve_target_repo_recovery_interval() -> float:
    raw = (os.environ.get(ENV_TARGET_REPO_RECOVERY_INTERVAL) or "").strip()
    if not raw:
        return DEFAULT_TARGET_REPO_RECOVERY_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TARGET_REPO_RECOVERY_INTERVAL_SECONDS
    return max(10.0, value)


async def _producer_loop(
    *,
    worker: CodingExecutorWorker,
    shutdown_event: asyncio.Event,
    interval_seconds: float,
    log_fn=logger.info,
) -> None:
    """Run ``dispatch_ready_coding_jobs`` every *interval_seconds* until
    *shutdown_event* fires.

    Failures inside ``dispatch_ready_coding_jobs`` are swallowed — the
    producer must never crash the executor service. The dispatcher itself
    has per-row try/except so a bad session can't kill this loop.
    """

    while not shutdown_event.is_set():
        try:
            dispatched = dispatch_ready_coding_jobs(worker=worker)
        except Exception:  # noqa: BLE001 — producer must not break consumer
            logger.warning(
                "coding_execute producer tick raised", exc_info=True
            )
            dispatched = ()
        if dispatched:
            created = [d for d in dispatched if getattr(d, "created", False)]
            if created:
                try:
                    log_fn(
                        "coding_execute producer: enqueued %d new row(s) "
                        "(sessions=%s)",
                        len(created),
                        [d.session_id for d in created],
                    )
                except Exception:  # noqa: BLE001
                    pass
            # P0-Z phantom marker self-heal — operator surface 가 silent
            # heal 되지 않도록 stale marker 한 줄 노출.
            stale = [
                d
                for d in dispatched
                if getattr(d, "stale_marker_reason", None)
            ]
            if stale:
                for d in stale:
                    try:
                        logger.warning(
                            "coding_execute producer: phantom dispatch "
                            "marker self-healed (session=%s, reason=%s, "
                            "phantom_job_id=%s, new_job_id=%s)",
                            d.session_id,
                            d.stale_marker_reason,
                            d.stale_marker_job_id,
                            d.job_id,
                        )
                    except Exception:  # noqa: BLE001
                        pass
        try:
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=interval_seconds
            )
        except asyncio.TimeoutError:
            continue


async def run_coding_executor(
    spec: ServiceSpec,
    *,
    queue: JobQueue,
    heartbeats: HeartbeatStore,
    shutdown_event: asyncio.Event,
) -> int:
    """Run the coding_execute consumer + a decoupled producer.

    producer + consumer 가 별개 asyncio task 로 동시에 돈다. queue 가
    비어있어도 producer 가 ready coding_job session 을 발견하면 enqueue
    → 같은 process 의 consumer 가 다음 tick 에 pick. 옛 wiring 의
    chicken-and-egg deadlock 해소.
    """

    # Local imports to keep top-level cycle-free.
    from .run_service import (
        build_coding_executor_bundle,
        _build_coding_progress_post_fn,
        _record_coding_progress_after_outcome,
    )
    from ..agents.job_queue import (
        ObsidianWriterWorker,
        default_render_fn,
        default_vault_root_resolver,
        default_write_fn,
    )

    bundle = build_coding_executor_bundle()
    worker = CodingExecutorWorker(
        queue=queue,
        heartbeats=heartbeats,
        **bundle,
    )

    # P1-K — explicit startup audit so operator can see the actual
    # editor wiring + bootstrap env state without inferring from
    # downstream failure reasons.
    code_editor_obj = bundle.get("code_editor")
    code_editor_class = type(code_editor_obj).__name__ if code_editor_obj else "(none)"
    bootstrap_enabled = bool(
        getattr(code_editor_obj, "is_bootstrap_capable", False)
    )
    logger.info(
        "coding_executor wired: editor=%s bootstrap_enabled=%s "
        "(env=YULE_CODING_EXECUTOR_GREENFIELD_BOOTSTRAP_ENABLED)",
        code_editor_class,
        bootstrap_enabled,
    )
    obsidian_progress_writer = ObsidianWriterWorker(
        queue=queue,
        heartbeats=heartbeats,
        render_fn=default_render_fn,
        write_fn=default_write_fn,
        vault_root_resolver=default_vault_root_resolver,
    )
    progress_post_fn = _build_coding_progress_post_fn()

    producer_interval = _resolve_producer_interval()
    pick_lease_seconds = _resolve_pick_lease_seconds()
    keepalive_interval = _resolve_keepalive_interval_seconds()
    target_repo_recovery_interval = _resolve_target_repo_recovery_interval()
    producer_task = asyncio.create_task(
        _producer_loop(
            worker=worker,
            shutdown_event=shutdown_event,
            interval_seconds=producer_interval,
        )
    )
    target_repo_recovery_task = asyncio.create_task(
        _target_repo_recovery_loop(
            queue=queue,
            shutdown_event=shutdown_event,
            interval_seconds=target_repo_recovery_interval,
        )
    )

    # P1-A startup recovery — fix 이전에 lease_expired 로 reap 된
    # coding_execute row 들을 자동 requeue. canonical session
    # ``11917bf1e75d`` 처럼 한 번 reaped 된 job 도 runtime restart 한 번
    # 으로 깨어나서 keepalive 보호 아래 다시 시도된다.
    _recover_lease_expired_rows(queue=queue, log_fn=logger.info)

    # P1-D startup recovery — operator 가 target repo checkout 을 만든
    # 직후 runtime restart 한 번으로 ``target_repo_checkout_missing`` 으로
    # 멈춘 row 가 자동 revive 되게 한다. recovery 는 resolver 가 실제
    # 디렉터리를 확인한 row 만 revive — repo 가 아직 없으면 skip,
    # queue churn 없음.
    try:
        from ..agents.job_queue.coding_execute_recovery import (
            recover_target_repo_missing_rows,
        )

        revived = recover_target_repo_missing_rows(
            queue=queue, log_fn=lambda msg, _exc: logger.info(msg)
        )
        if revived:
            logger.info(
                "coding_execute target-repo recovery: revived %d row(s) "
                "after operator created/registered the missing checkout",
                len(revived),
            )
    except Exception:  # noqa: BLE001 - never crash startup
        logger.warning(
            "coding_execute target-repo recovery: startup sweep raised",
            exc_info=True,
        )

    # P1-I startup recovery — operator 가 ``YULE_CODING_EXECUTOR_
    # GREENFIELD_BOOTSTRAP_ENABLED=1`` 으로 opt-in 한 직후 runtime
    # restart 한 번으로 ``bootstrap_required:*editor_record_only_insufficient*``
    # 또는 ``bootstrap_required:live_editor_unavailable:*`` 로 멈춘 row
    # 가 자동 revive 되게 한다. env off 면 sweep 자체가 no-op (churn 0).
    try:
        from ..agents.job_queue.coding_execute_recovery import (
            recover_bootstrap_required_rows,
        )

        revived_boot = recover_bootstrap_required_rows(
            queue=queue, log_fn=lambda msg, _exc: logger.info(msg)
        )
        if revived_boot:
            logger.info(
                "coding_execute bootstrap-required recovery: revived %d "
                "row(s) after operator opted into greenfield bootstrap",
                len(revived_boot),
            )
    except Exception:  # noqa: BLE001 - never crash startup
        logger.warning(
            "coding_execute bootstrap-required recovery: startup sweep raised",
            exc_info=True,
        )

    async def _process(job):
        # Spawn a per-job keepalive task. picked_by 는 pick(...) 시점에
        # service_id 로 설정되므로 worker_id 도 동일.
        done = asyncio.Event()
        keepalive_task = asyncio.create_task(
            _lease_keepalive_loop(
                queue=queue,
                job_id=job.job_id,
                worker_id=spec.service_id,
                lease_seconds=pick_lease_seconds,
                interval_seconds=keepalive_interval,
                done_event=done,
            )
        )
        try:
            outcome = worker.process_job(job)
        finally:
            done.set()
            try:
                await keepalive_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        _record_coding_progress_after_outcome(
            outcome=outcome,
            obsidian_writer=obsidian_progress_writer,
            progress_post_fn=progress_post_fn,
        )

    try:
        await run_worker_loop(
            service_id=spec.service_id,
            queue=queue,
            heartbeats=heartbeats,
            process_job=_process,
            job_types=("coding_execute",),
            roles=(),
            shutdown_event=shutdown_event,
            pick_lease_seconds=pick_lease_seconds,
        )
    finally:
        producer_task.cancel()
        try:
            await producer_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        target_repo_recovery_task.cancel()
        try:
            await target_repo_recovery_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    return EXIT_OK


async def _target_repo_recovery_loop(
    *,
    queue: JobQueue,
    shutdown_event: asyncio.Event,
    interval_seconds: float,
) -> None:
    """Periodic sweep — re-check target repo availability AND greenfield
    bootstrap capability for blocked ``coding_execute`` rows so a runtime
    that's already up auto-recovers the moment operator either creates
    the missing checkout OR opts into bootstrap (no restart required).

    Sweep is bounded (``max_per_run=50`` each) and helpers themselves
    skip when their gate is unsatisfied — perpetually-missing checkout
    or env-off bootstrap doesn't cause churn.
    """

    from ..agents.job_queue.coding_execute_recovery import (
        recover_bootstrap_required_rows,
        recover_target_repo_missing_rows,
    )

    while not shutdown_event.is_set():
        try:
            revived = recover_target_repo_missing_rows(queue=queue)
        except Exception:  # noqa: BLE001 - never crash sweep
            logger.warning(
                "coding_execute target-repo recovery tick raised",
                exc_info=True,
            )
            revived = ()
        if revived:
            logger.warning(
                "coding_execute target-repo recovery tick: revived "
                "%d row(s) after checkout availability change",
                len(revived),
            )

        # P1-I: bootstrap-required sweep runs on the same tick. If env
        # opt-in is off the helper returns () immediately — no churn.
        try:
            revived_boot = recover_bootstrap_required_rows(queue=queue)
        except Exception:  # noqa: BLE001 - never crash sweep
            logger.warning(
                "coding_execute bootstrap-required recovery tick raised",
                exc_info=True,
            )
            revived_boot = ()
        if revived_boot:
            logger.warning(
                "coding_execute bootstrap-required recovery tick: revived "
                "%d row(s) after bootstrap capability change",
                len(revived_boot),
            )

        try:
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=interval_seconds
            )
        except asyncio.TimeoutError:
            continue


def _recover_lease_expired_rows(
    *,
    queue: JobQueue,
    log_fn=logger.info,
) -> tuple:
    """Scan ``coding_execute`` ``failed_retryable`` rows whose
    ``result_json.error == 'lease_expired'`` and requeue them so the
    keepalive-protected worker can retry. Per-row failure is swallowed
    — startup must never crash.
    """

    import json as _json
    import sqlite3 as _sqlite3

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
                WHERE job_type = 'coding_execute'
                  AND state = 'failed_retryable'
                ORDER BY created_at ASC
                LIMIT 50
                """
            ).fetchall()
    except Exception:  # noqa: BLE001 - never crash startup
        logger.warning(
            "coding_execute lease-expired sweep: sqlite query failed",
            exc_info=True,
        )
        return ()

    requeued: list[str] = []
    for row in rows or ():
        raw = row["result_json"] or "{}"
        try:
            payload = _json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        if str(payload.get("error") or "").strip() != "lease_expired":
            continue
        try:
            queue.requeue_retryable(row["job_id"])
            requeued.append(row["job_id"])
            try:
                log_fn(
                    "coding_execute startup recovery: requeued "
                    "lease_expired row (job=%s)",
                    row["job_id"],
                )
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            logger.warning(
                "coding_execute startup recovery: requeue failed for %s",
                row["job_id"],
                exc_info=True,
            )
            continue
    return tuple(requeued)


__all__ = (
    "DEFAULT_KEEPALIVE_INTERVAL_SECONDS",
    "DEFAULT_PICK_LEASE_SECONDS",
    "DEFAULT_PRODUCER_INTERVAL_SECONDS",
    "DEFAULT_TARGET_REPO_RECOVERY_INTERVAL_SECONDS",
    "ENV_KEEPALIVE_INTERVAL",
    "ENV_PICK_LEASE_SECONDS",
    "ENV_PRODUCER_INTERVAL",
    "ENV_TARGET_REPO_RECOVERY_INTERVAL",
    "_lease_keepalive_loop",
    "_recover_lease_expired_rows",
    "_target_repo_recovery_loop",
    "run_coding_executor",
)
