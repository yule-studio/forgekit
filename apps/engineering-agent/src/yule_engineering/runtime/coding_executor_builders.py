"""Coding executor builders — live PR merge executor + approval enqueuer +
next slice dispatcher.

``coding_executor_runner`` 의 background loop 들이 wiring 하는 executor
빌더 군. 책임 분리 (P0-Y runner 1000 LOC 초과 해소) 를 위해 runner 에서
분리됐다. 순수 빌더 — runner 의 다른 함수를 역참조하지 않으므로
순환 없음. runner 는 본 모듈을 re-export 해 기존 import 경로를 보존한다.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional


logger = logging.getLogger(__name__)


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
        from yule_discord.integrations.pr_merge_adapter import (
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


__all__ = (
    "ENV_GITHUB_APP_MERGE_OPT_IN",
    "MERGE_EXEC_STAGE_CONFIG_ERROR",
    "MERGE_EXEC_STAGE_EXECUTOR_BUILD_FAILED",
    "MERGE_EXEC_STAGE_IMPORT_FAILED",
    "MERGE_EXEC_STAGE_LIVE_CLIENT_FAILED",
    "MERGE_EXEC_STAGE_OK",
    "MERGE_EXEC_STAGE_OPT_IN_OFF",
    "_build_next_slice_dispatcher",
    "_maybe_build_approval_enqueuer",
    "_maybe_build_live_pr_merge_executor",
    "_opt_in_enabled",
    "build_live_pr_merge_executor_with_stage",
)
