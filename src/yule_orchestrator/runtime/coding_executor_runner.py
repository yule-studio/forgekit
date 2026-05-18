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
from typing import Any, Mapping, Optional, Sequence

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


# P1-L-3 — pr_merge_pending stage 소비 주기. autonomous_merge 면 매 tick
# 마다 merge gate 호출 — 너무 빠르면 GitHub API rate 부담, 너무 느리면
# operator 가 "stuck" 으로 인식. 기본 30s.
ENV_PR_MERGE_CONTINUATION_INTERVAL: str = (
    "YULE_PR_MERGE_CONTINUATION_INTERVAL_SECONDS"
)
DEFAULT_PR_MERGE_CONTINUATION_INTERVAL_SECONDS: float = 30.0


def _resolve_pr_merge_continuation_interval() -> float:
    raw = (
        os.environ.get(ENV_PR_MERGE_CONTINUATION_INTERVAL) or ""
    ).strip()
    if not raw:
        return DEFAULT_PR_MERGE_CONTINUATION_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PR_MERGE_CONTINUATION_INTERVAL_SECONDS
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
    pr_merge_continuation_interval = _resolve_pr_merge_continuation_interval()
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
    # P1-L-3 — pr_merge_pending 소비 루프. live merge env 미설정이면
    # autonomous_merge 가 skipped 로 표시될 뿐 loop 자체는 동작 — startup
    # log 가 wiring 상태를 명시한다.
    pr_merge_continuation_task = asyncio.create_task(
        _pr_merge_continuation_loop(
            queue=queue,
            shutdown_event=shutdown_event,
            interval_seconds=pr_merge_continuation_interval,
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
        pr_merge_continuation_task.cancel()
        try:
            await pr_merge_continuation_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    return EXIT_OK


def _persist_session_extra(session_id: str, new_extra: Mapping[str, Any]) -> None:
    """workflow_state persistence — pr_merge_continuation 콜백용.

    silent skip when session 이 사라졌거나 store 가 raise. loop 가 죽지 않게.
    """

    try:
        from dataclasses import replace as _replace
        from datetime import datetime, timezone as _tz
        from ..agents.workflow_state import load_session, update_session
    except Exception:  # noqa: BLE001 - partial install
        return
    try:
        session = load_session(session_id)
    except Exception:  # noqa: BLE001
        return
    if session is None:
        return
    try:
        updated = _replace(session, extra=dict(new_extra))
        update_session(updated, now=datetime.now(tz=_tz.utc))
    except Exception:  # noqa: BLE001 - best-effort
        return


# P1-O — autonomous_merge live executor 의 opt-in env contract.  본
# 모듈에서 직접 정의 — 옛 wiring 은 ``coding_executor_live`` 에서
# import 하려다 silent ImportError 로 떨어져 ``merge_executor=no`` 라는
# 거짓 신호를 startup log 에 노출했다.  이제 SSoT 가 본 모듈이고, bot
# helper (`_build_pr_merge_executor_for_bot`) 도 본 함수를 재사용하므로
# wiring 이 한 자리에서 보장된다.
ENV_GITHUB_APP_MERGE_OPT_IN: str = "YULE_GITHUB_APP_MERGE_OPT_IN"


# diagnostic — 4 stage 중 어디서 None 으로 떨어졌는지 표면화.
MERGE_EXEC_STAGE_IMPORT_FAILED: str = "import_failed"
MERGE_EXEC_STAGE_OPT_IN_OFF: str = "opt_in_off"
MERGE_EXEC_STAGE_CONFIG_ERROR: str = "github_app_config_error"
MERGE_EXEC_STAGE_LIVE_CLIENT_FAILED: str = "live_client_build_failed"
MERGE_EXEC_STAGE_EXECUTOR_BUILD_FAILED: str = "pr_merge_executor_build_failed"
MERGE_EXEC_STAGE_OK: str = "ok"


def _opt_in_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    src = env if env is not None else os.environ
    return (src.get(ENV_GITHUB_APP_MERGE_OPT_IN) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _maybe_build_live_pr_merge_executor(
    *, log: bool = True
) -> Optional[Any]:
    """env 가 갖춰지면 live ``PRMergeExecutor`` 반환. 아니면 None.

    P1-O harden — 옛 broad ``except Exception: return None`` 이 import
    bug 와 env 미설정을 구분 못 해서 operator 가 startup log 만 보고는
    원인을 알 수 없었다.  본 함수는 4 stage 로 명확히 분기해서 log
    warning + reason 으로 surface한다.

    Returns the executor callable when ``stage == ok``, otherwise None.
    별도 stage 정보가 필요하면 :func:`build_live_pr_merge_executor_with_stage`
    를 사용 — 본 함수는 backwards-compat shim.
    """

    executor, _stage = build_live_pr_merge_executor_with_stage(log=log)
    return executor


def build_live_pr_merge_executor_with_stage(
    *, env: Optional[Mapping[str, str]] = None, log: bool = True
) -> tuple:
    """4 stage diagnostic — (executor or None, stage_token).

    Stage tokens:
      * ``import_failed`` — 모듈 import 자체가 실패.  P1-O 이전의 silent
        regression 회귀 차단을 위한 explicit stage.
      * ``opt_in_off`` — ``YULE_GITHUB_APP_MERGE_OPT_IN`` 가 truthy 가 아님.
      * ``github_app_config_error`` — GitHubAppConfig.from_env 실패 (보통
        env contract 누락).
      * ``live_client_build_failed`` — config 는 통과했지만 live client
        construction 에서 raise.
      * ``pr_merge_executor_build_failed`` — live client 까지 OK 인데
        ``build_pr_merge_executor`` 가 raise.
      * ``ok`` — executor callable 반환.
    """

    try:
        from ..github_app.config import GitHubAppConfigError
        from ..github_app.live_client import build_live_client_from_env
        from ..github_app.pr_merge_executor import build_pr_merge_executor
    except Exception:  # noqa: BLE001
        if log:
            logger.warning(
                "pr_merge_continuation: live merge executor build skipped "
                "(stage=%s) — github_app imports unavailable",
                MERGE_EXEC_STAGE_IMPORT_FAILED,
                exc_info=True,
            )
        return None, MERGE_EXEC_STAGE_IMPORT_FAILED

    if not _opt_in_enabled(env):
        if log:
            logger.info(
                "pr_merge_continuation: live merge executor skipped "
                "(stage=%s) — set %s=1 to enable",
                MERGE_EXEC_STAGE_OPT_IN_OFF,
                ENV_GITHUB_APP_MERGE_OPT_IN,
            )
        return None, MERGE_EXEC_STAGE_OPT_IN_OFF

    try:
        live_client = build_live_client_from_env(env)
    except GitHubAppConfigError as exc:
        if log:
            logger.warning(
                "pr_merge_continuation: live merge executor skipped "
                "(stage=%s) — GitHubAppConfig invalid: %s",
                MERGE_EXEC_STAGE_CONFIG_ERROR,
                exc,
            )
        return None, MERGE_EXEC_STAGE_CONFIG_ERROR
    except Exception:  # noqa: BLE001
        if log:
            logger.warning(
                "pr_merge_continuation: live merge executor skipped "
                "(stage=%s) — live client build raised",
                MERGE_EXEC_STAGE_LIVE_CLIENT_FAILED,
                exc_info=True,
            )
        return None, MERGE_EXEC_STAGE_LIVE_CLIENT_FAILED

    try:
        executor = build_pr_merge_executor(client=live_client)
    except Exception:  # noqa: BLE001
        if log:
            logger.warning(
                "pr_merge_continuation: live merge executor skipped "
                "(stage=%s) — build_pr_merge_executor raised",
                MERGE_EXEC_STAGE_EXECUTOR_BUILD_FAILED,
                exc_info=True,
            )
        return None, MERGE_EXEC_STAGE_EXECUTOR_BUILD_FAILED

    if log:
        logger.info(
            "pr_merge_continuation: live merge executor wired (stage=%s)",
            MERGE_EXEC_STAGE_OK,
        )
    return executor, MERGE_EXEC_STAGE_OK


def _maybe_build_approval_enqueuer():
    """approval_required mode 에서 카드 게시용 ApprovalEnqueuer.

    P1-M B 회귀 수정 — ``ApprovalWorker`` 가 ``post_fn`` + ``channel_resolver``
    를 필수로 받는다. 옛 wiring 은 두 인자 모두 생략해 TypeError → silent
    None 으로 떨어졌고 startup log 가 ``approval_enqueuer=no`` 로 나왔다.
    본 helper 는 ``run_service`` 의 production wiring 과 동일하게
    ``build_production_post_fn`` + ``build_approval_channel_resolver`` 를
    재사용한다.

    Discord/runtime 의존성이 빠진 env 에서는 두 헬퍼 중 하나가 raise →
    None 반환 + log warning 으로 운영자에게 실패 사유 노출.
    """

    try:
        from ..agents.job_queue.approval_worker import ApprovalWorker
        from ..agents.job_queue.approval_discord_poster import (
            build_approval_channel_resolver,
            build_production_post_fn,
        )
        from ..agents.job_queue.heartbeat import HeartbeatStore
        from ..agents.job_queue.store import JobQueue
        from ..discord.integrations.pr_merge_adapter import (
            enqueue_pr_merge_approval,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "pr_merge_continuation: ApprovalEnqueuer build skipped "
            "(imports unavailable)",
            exc_info=True,
        )
        return None

    queue = JobQueue()
    heartbeats = HeartbeatStore()
    try:
        production_post_fn = build_production_post_fn()
        channel_resolver = build_approval_channel_resolver()
        approval_worker = ApprovalWorker(
            queue=queue,
            heartbeats=heartbeats,
            post_fn=production_post_fn,
            channel_resolver=channel_resolver,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "pr_merge_continuation: ApprovalEnqueuer build skipped "
            "(post_fn/channel_resolver/ApprovalWorker init failed)",
            exc_info=True,
        )
        return None

    async def _enqueue(*, session, proposal, **kwargs):
        return await enqueue_pr_merge_approval(
            session=session,
            proposal=proposal,
            approval_worker=approval_worker,
            drive_consumer=True,
            **kwargs,
        )

    return _enqueue


def _build_next_slice_dispatcher():
    """merge 후 next coding slice 를 enqueue 하는 콜백.

    minimal MVP — ``coding_backlog`` (list[dict]) 에서 첫 항목 pop 해서
    ``coding_proposal`` 빌더에 넘긴다. 빌드 실패 / backlog 비어있으면
    silent — ``dispatch_next_coding_slice`` 가 audit 에 남김.
    """

    def _enqueue_slice(session_id: str, slice_spec: Mapping[str, Any]) -> None:
        try:
            from ..agents.job_queue.work_order_coding_continuation import (
                promote_session_to_coding_ready,
            )
        except Exception:  # noqa: BLE001
            return
        try:
            promote_session_to_coding_ready(
                session_id=session_id,
                session_prompt=str(slice_spec.get("prompt") or ""),
                auto_rebuild_proposal=True,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "next_slice promote raised for session %s",
                session_id,
                exc_info=True,
            )

    def _on_done(session_id: str) -> None:
        try:
            from dataclasses import replace as _replace
            from datetime import datetime, timezone as _tz
            from ..agents.workflow_state import (
                WorkflowState,
                load_session,
                update_session,
            )
        except Exception:  # noqa: BLE001
            return
        try:
            session = load_session(session_id)
        except Exception:  # noqa: BLE001
            return
        if session is None:
            return
        try:
            updated = _replace(session, state=WorkflowState.COMPLETED)
            update_session(updated, now=datetime.now(tz=_tz.utc))
        except Exception:  # noqa: BLE001
            return

    return _enqueue_slice, _on_done


async def _pr_merge_continuation_loop(
    *,
    queue: JobQueue,
    shutdown_event: asyncio.Event,
    interval_seconds: float,
) -> None:
    """P1-L-3 — pr_merge_pending 세션 주기적 advance.

    각 tick 마다:
      1. ``list_sessions`` 로 최근 세션 100 개 가져옴
      2. ``iter_pending_session_ids`` 로 pr_merge_pending 필터
      3. 각 session 에 대해 ``advance_pending_session`` 호출
         - approval_required → ApprovalEnqueuer 가 카드 한 번 게시
           (audit ``approval_card_enqueued`` event 로 dedup)
         - autonomous_merge → PRMergeExecutor 가 gate + merge 시도
      4. merge 성공 시 ``dispatch_next_coding_slice`` 로 backlog 진행

    loop 가 절대 raise 하지 않게 모든 단계 swallow + warning. 같은
    session 이 한 tick 안에 두 번 advance 되지 않게 dedup 은 helper 가
    감당.
    """

    from ..agents.job_queue.next_slice_dispatcher import (
        dispatch_next_coding_slice,
    )
    from ..agents.job_queue.pr_merge_continuation import (
        EXTRA_PR_MERGE_STAGE,
        STAGE_PR_MERGED,
    )
    from ..agents.job_queue.pr_merge_continuation_worker import (
        advance_pending_session,
        iter_pending_session_ids,
    )
    from ..agents.workflow_state import list_sessions

    approval_enqueuer = _maybe_build_approval_enqueuer()
    pr_merge_executor, merge_stage = build_live_pr_merge_executor_with_stage(
        log=False
    )
    enqueue_slice, on_done = _build_next_slice_dispatcher()

    # P1-O — silent None 금지. startup log 가 4 stage 중 정확히 어디서
    # 떨어졌는지 보여준다 (env unset / config error / build failure).
    logger.info(
        "pr_merge_continuation loop wired: approval_enqueuer=%s "
        "merge_executor=%s merge_stage=%s (env=%s)",
        "yes" if approval_enqueuer is not None else "no",
        "yes" if pr_merge_executor is not None else "no",
        merge_stage,
        ENV_GITHUB_APP_MERGE_OPT_IN,
    )

    while not shutdown_event.is_set():
        try:
            sessions = list_sessions(limit=100)
        except Exception:  # noqa: BLE001 - never crash loop
            logger.warning(
                "pr_merge_continuation: list_sessions raised",
                exc_info=True,
            )
            sessions = ()

        pending_ids = iter_pending_session_ids(sessions)
        for sid in pending_ids:
            session = next(
                (s for s in sessions if getattr(s, "session_id", "") == sid),
                None,
            )
            if session is None:
                continue
            extra = getattr(session, "extra", None) or {}
            if not isinstance(extra, Mapping):
                continue

            def _persist(new_extra: Mapping[str, Any], _sid=sid) -> None:
                _persist_session_extra(_sid, new_extra)

            try:
                outcome = await advance_pending_session(
                    session_id=sid,
                    session_extra=extra,
                    persist_extra=_persist,
                    approval_enqueuer=approval_enqueuer,
                    merge_executor=pr_merge_executor,
                    next_slice_dispatcher=lambda _sid, _extra: None,
                    approval_session_obj=session,
                )
            except Exception:  # noqa: BLE001 - one bad session can't kill loop
                logger.warning(
                    "pr_merge_continuation: advance_pending_session raised "
                    "for session %s",
                    sid,
                    exc_info=True,
                )
                continue

            try:
                logger.info(
                    "pr_merge_continuation tick: session=%s action=%s "
                    "work_mode=%s new_stage=%s",
                    sid,
                    outcome.action,
                    outcome.work_mode,
                    outcome.new_stage,
                )
            except Exception:  # noqa: BLE001
                pass

            # merge 성공 직후에만 next slice 진행. dispatch_next_coding_slice
            # 자체가 pr_merge_stage != pr_merged 면 SKIPPED 하므로 race-safe.
            if outcome.new_stage == STAGE_PR_MERGED:
                try:
                    from ..agents.workflow_state import load_session

                    fresh = load_session(sid)
                    fresh_extra = (
                        getattr(fresh, "extra", None) or {} if fresh else {}
                    )
                    dispatch_next_coding_slice(
                        session_id=sid,
                        session_extra=fresh_extra,
                        persist_extra=_persist,
                        enqueue_slice=enqueue_slice,
                        on_session_done=on_done,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "pr_merge_continuation: next slice dispatch raised "
                        "for session %s",
                        sid,
                        exc_info=True,
                    )

        try:
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=interval_seconds
            )
        except asyncio.TimeoutError:
            continue


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
    "DEFAULT_PR_MERGE_CONTINUATION_INTERVAL_SECONDS",
    "DEFAULT_PRODUCER_INTERVAL_SECONDS",
    "DEFAULT_TARGET_REPO_RECOVERY_INTERVAL_SECONDS",
    "ENV_GITHUB_APP_MERGE_OPT_IN",
    "ENV_KEEPALIVE_INTERVAL",
    "ENV_PICK_LEASE_SECONDS",
    "ENV_PR_MERGE_CONTINUATION_INTERVAL",
    "ENV_PRODUCER_INTERVAL",
    "ENV_TARGET_REPO_RECOVERY_INTERVAL",
    "MERGE_EXEC_STAGE_CONFIG_ERROR",
    "MERGE_EXEC_STAGE_EXECUTOR_BUILD_FAILED",
    "MERGE_EXEC_STAGE_IMPORT_FAILED",
    "MERGE_EXEC_STAGE_LIVE_CLIENT_FAILED",
    "MERGE_EXEC_STAGE_OK",
    "MERGE_EXEC_STAGE_OPT_IN_OFF",
    "_build_next_slice_dispatcher",
    "_lease_keepalive_loop",
    "_maybe_build_approval_enqueuer",
    "_maybe_build_live_pr_merge_executor",
    "build_live_pr_merge_executor_with_stage",
    "_persist_session_extra",
    "_pr_merge_continuation_loop",
    "_recover_lease_expired_rows",
    "_target_repo_recovery_loop",
    "run_coding_executor",
)
