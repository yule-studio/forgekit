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


def _resolve_producer_interval() -> float:
    raw = (os.environ.get(ENV_PRODUCER_INTERVAL) or "").strip()
    if not raw:
        return DEFAULT_PRODUCER_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PRODUCER_INTERVAL_SECONDS
    return max(2.0, value)


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
    obsidian_progress_writer = ObsidianWriterWorker(
        queue=queue,
        heartbeats=heartbeats,
        render_fn=default_render_fn,
        write_fn=default_write_fn,
        vault_root_resolver=default_vault_root_resolver,
    )
    progress_post_fn = _build_coding_progress_post_fn()

    producer_interval = _resolve_producer_interval()
    producer_task = asyncio.create_task(
        _producer_loop(
            worker=worker,
            shutdown_event=shutdown_event,
            interval_seconds=producer_interval,
        )
    )

    async def _process(job):
        outcome = worker.process_job(job)
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
        )
    finally:
        producer_task.cancel()
        try:
            await producer_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    return EXIT_OK


__all__ = (
    "DEFAULT_PRODUCER_INTERVAL_SECONDS",
    "ENV_PRODUCER_INTERVAL",
    "run_coding_executor",
)
