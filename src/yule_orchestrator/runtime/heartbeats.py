"""Heartbeat helpers for `run_service`.

P0-T runtime status visibility — gateway / member bot / supervisor 같은
non-queue 서비스가 status surface 에서 영원히 UNKNOWN 으로 보이던 회귀
차단. heartbeat 가 살아있으면 ALIVE, 끊기면 STALE 로 자연스럽게 전이된다.

본 모듈은 `run_service.py` (1387 LOC) 에서 추출한 첫 번째 분리 단위.
heartbeat 책임만 가진다 — service routing, executor builder, signal handler
등 다른 책임은 caller 가 보존한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from ..agents.job_queue import HeartbeatStore


logger = logging.getLogger(__name__)


async def heartbeat_loop(
    *,
    service_id: str,
    heartbeats: HeartbeatStore,
    shutdown_event: asyncio.Event,
    metadata: Mapping[str, Any],
    interval_seconds: float = 30.0,
) -> None:
    """Background task — *service_id* heartbeat 를 *interval_seconds* 마다
    record 한다. shutdown 이벤트가 set 되면 빠져나온다.

    Failure mode:
      record 가 raise 해도 background loop 는 죽지 않는다. discord
      gateway / member bot 처럼 절대 죽으면 안 되는 task 안에서 돌기
      때문.
    """

    pid = os.getpid()
    while not shutdown_event.is_set():
        try:
            heartbeats.record(service_id, pid=pid, metadata=dict(metadata))
        except Exception:  # noqa: BLE001 - never crash the parent loop
            logger.warning(
                "heartbeat loop record failed for %s",
                service_id,
                exc_info=True,
            )
        try:
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=interval_seconds
            )
        except asyncio.TimeoutError:
            continue


def record_graceful_disable(
    *,
    service_id: str,
    db_path: Optional[Path],
    env_key: str,
    reason: str,
) -> None:
    """Stamp a heartbeat row with ``state=graceful_disabled`` so the
    status surface distinguishes "operator disabled (token missing /
    placeholder)" from "worker likely never started".

    Best-effort — heartbeat 기록 실패는 graceful-disable 자체를 막지
    않는다. caller 는 호출 후 그대로 EXIT_UNKNOWN_SERVICE 로 종료한다.
    """

    try:
        store = HeartbeatStore(db_path=db_path)
        store.record(
            service_id,
            pid=os.getpid(),
            metadata={
                "state": "graceful_disabled",
                "env_key": env_key,
                "reason": reason,
            },
        )
    except Exception:  # noqa: BLE001 - never crash on heartbeat
        logger.warning(
            "graceful-disable heartbeat record failed for %s",
            service_id,
            exc_info=True,
        )


__all__ = ("heartbeat_loop", "record_graceful_disable")
