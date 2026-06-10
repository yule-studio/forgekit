"""GitHub work_order executor runner — extracted from `run_service.py`.

P0-T live smoke fix surface — 승인 reply 가 work_order job 을 enqueue
했지만 그것을 소비하는 background process 가 inventory 에 없어 queued=1
정체했던 회귀를 봉합한다. 본 모듈은 그 missing consumer 의 runtime
adapter.

분리 사유 (governance/code_audit P0-T): `runtime/run_service.py` 가 1387
LOC + 책임 ≥ 2 (heartbeat / discord_runtime / runtime_orchestration /
github_workflow / state_persistence) 로 split_now 위반. 본 모듈은
github_workflow + recovery_orchestration 책임만 갖는다.

Flow:
  1. live GitHub App env 가 있으면 GithubWriter 빌드 (writer_factory
     가 (writer, "L2") 반환), 없으면 (None, "L2") 반환해 worker 가
     SKIPPED_NO_WRITER 로 audit 만 남기고 anchor 만 stamp.
  2. GitHubWorkOrderWorker 가 run_one 로 한 건씩 drain.
  3. startup recovery hook: producer bug 로 SKIPPED_NO_REPO failed
     로 떨어진 rows 를 자동 requeue. operator 가 runtime restart 만으로
     stranded rows 복구.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Mapping, Optional, Tuple

from ..agents.job_queue import HeartbeatStore, JobQueue
from yule_runtime.services import ServiceSpec


logger = logging.getLogger(__name__)


EXIT_OK: int = 0


def maybe_build_live_github_client(
    *, env: Mapping[str, str]
) -> Optional[Any]:
    """Return a live GitHub App client if env is set, else None.

    The factory only fires when *all three* env keys are present — we
    don't want a partial config (e.g. app id but no key path) to
    silently force a stub through. Errors at construction are swallowed
    so the consumer falls back to dry-run-only.
    """

    needed = (
        "YULE_GITHUB_APP_ID",
        "YULE_GITHUB_APP_INSTALLATION_ID",
        "YULE_GITHUB_APP_PRIVATE_KEY_PATH",
    )
    if not all((env.get(name) or "").strip() for name in needed):
        return None
    try:
        from ..github_app.live_client import build_live_client_from_env

        return build_live_client_from_env(env)
    except Exception:  # noqa: BLE001 - log and continue dry-run
        logger.warning(
            "coding executor: build_live_client_from_env raised; falling "
            "back to push-blocked bundle",
            exc_info=True,
        )
        return None


async def run_github_work_order_executor(
    spec: ServiceSpec,
    *,
    queue: JobQueue,
    heartbeats: HeartbeatStore,
    shutdown_event: asyncio.Event,
) -> int:
    """Drain ``github_work_order`` queue until *shutdown_event* fires."""

    from ..agents.github_workos.github_writer import (
        GithubWriter,
        make_default_policy_gate,
    )
    from ..agents.job_queue.github_work_order_executor import (
        GitHubWorkOrderWorker,
        repair_stranded_coding_sessions,
        requeue_missing_plan_failures,
        requeue_no_repo_failures,
        run_until_shutdown,
    )

    live_client = maybe_build_live_github_client(env=os.environ)

    def _writer_factory(_work_order) -> Tuple[Optional[Any], str]:
        if live_client is None:
            # live env 미주입 — worker 가 SKIPPED_NO_WRITER 로 audit
            # 만 남기고 anchor 만 stamp. operator 가 #봇-상태 에서
            # graceful 한 상태로 확인 가능.
            return None, "L2"
        return (
            GithubWriter(
                client=live_client,
                dry_run=False,
                live=True,
                policy_gate=make_default_policy_gate(),
            ),
            "L2",
        )

    worker = GitHubWorkOrderWorker(
        queue=queue,
        writer_factory=_writer_factory,
        heartbeats=heartbeats,
    )

    def _log(message: str, exc: Optional[Any]) -> None:
        if exc is not None:
            logger.warning(message, exc_info=exc)
        else:
            logger.info(message)

    # P0-T / P0-V startup recovery hooks — producer 가 plan / repo 를
    # 빠뜨리고 enqueue 한 row 들이 fix 후에도 stranded 로 남는 회귀를
    # 차단한다. 각 helper 는 자기 error reason 만 picks 해서 audit 라인을
    # 분리한다 — operator 가 #봇-상태 에서 두 결을 따로 본다.
    for label, helper in (
        ("SKIPPED_NO_REPO", requeue_no_repo_failures),
        ("SKIPPED_MISSING_PLAN", requeue_missing_plan_failures),
    ):
        try:
            requeued = helper(queue, log_fn=_log)
            if requeued:
                logger.info(
                    "github_work_order_executor: startup hook requeued "
                    "%d failed_retryable rows (reason=%s) after producer fix",
                    len(requeued),
                    label,
                )
        except Exception:  # noqa: BLE001 — never crash the executor on hook
            logger.warning(
                "github_work_order_executor: startup requeue hook raised (reason=%s)",
                label,
                exc_info=True,
            )

    # P0-X startup sweep — SAVED rows 가 `no_coding_proposal` noop 으로
    # 멈춰있는 session 들을 prompt 만으로 self-heal. canonical session
    # ``11917bf1e75d`` 같은 stranded 케이스가 runtime restart 한 번으로
    # 자동 복구되고 coding_execute 로 이어진다.
    try:
        repaired = repair_stranded_coding_sessions(queue, log_fn=_log)
        if repaired:
            logger.info(
                "github_work_order_executor: startup hook repaired %d "
                "stranded coding sessions (no_coding_proposal → ready)",
                len(repaired),
            )
    except Exception:  # noqa: BLE001 — never crash the executor on hook
        logger.warning(
            "github_work_order_executor: startup repair hook raised",
            exc_info=True,
        )

    await run_until_shutdown(
        worker,
        shutdown_event=shutdown_event,
        interval_seconds=5.0,
        heartbeats=heartbeats,
        heartbeat_interval_seconds=30.0,
        log_fn=_log,
    )
    return EXIT_OK


__all__ = (
    "maybe_build_live_github_client",
    "run_github_work_order_executor",
)
