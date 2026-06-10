"""Discord-side runners for ``run-service`` — gateway / member bot.

P0-T governance/code_audit split — `run_service.py` 가 책임 5 종 이상
(heartbeat / discord / runtime / github / state) 으로 split_now 위반.
본 모듈은 ``discord_runtime`` 책임만 갖는다:

  * ``run_discord_gateway`` — engineering gateway 단일 진입.
  * ``run_discord_member_bot`` — role-별 member bot 단일 진입.
  * ``install_role_runner_dispatch_for_run_service`` — gateway 가 boot
    되기 전에 role-runner dispatcher 를 publish 하는 shim. legacy bot.py
    의 installer 와 동일한 trace 를 stderr 로 흘려 operator 가
    "fallback 떨어졌어?" 를 logs 만으로 답할 수 있게 한다.

Heartbeat / EXIT_OK / EXIT_UNKNOWN_SERVICE 상수는 caller (run_service)
가 명시 import 후 전달한다 — 본 모듈은 직접 sys.exit 하지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from ..agents.job_queue import HeartbeatStore
from .heartbeats import heartbeat_loop, record_graceful_disable
from yule_runtime.services import ServiceSpec


logger = logging.getLogger(__name__)


EXIT_OK: int = 0
EXIT_UNKNOWN_SERVICE: int = 78


async def run_discord_gateway(
    spec: ServiceSpec,
    *,
    shutdown_event: asyncio.Event,
    db_path: Optional[Path] = None,
) -> int:
    """Run the engineering gateway under ``run-service``.

    Resolves the gateway token, layers the planning-bot env overrides
    via :func:`build_gateway_env_overrides`, then drives the bot through
    :func:`run_engineering_gateway_until_shutdown` so SIGTERM at the
    runtime level translates into ``await bot.close()`` instead of
    relying on discord.py's internal signal handlers (which only fire
    when ``bot.run`` owns the main thread — under ``run-service`` the
    runtime owns the loop, so the legacy thread path saw the runtime
    swallow the signal first and the bot kept running until the parent
    killed it).
    """

    from yule_discord.bot import (
        build_engineering_gateway_bot,
        run_engineering_gateway_until_shutdown,
    )
    from .gateway_env import (
        build_gateway_env_overrides,
        resolve_gateway_token,
    )

    token = resolve_gateway_token()
    if token is None:
        sys.stderr.write(
            "yule run-service: ENGINEERING_AGENT_BOT_GATEWAY_TOKEN unset; "
            "engineering gateway cannot start.\n"
        )
        return EXIT_UNKNOWN_SERVICE

    overrides = build_gateway_env_overrides(gateway_token=token)
    for key, value in overrides.items():
        os.environ[key] = value

    # A-M11b: install the role-runner dispatcher from env *before* the
    # bot starts. ``build_engineering_gateway_bot`` also calls the
    # installer (legacy ``run_discord_bot`` direct path), so this call
    # is idempotent — last-one-wins on the dispatcher binding. We
    # install here so an operator running ``yule run-service
    # eng-discord-gateway`` sees the trace stdout line *before* the
    # bot's discord login attempt.
    install_role_runner_dispatch_for_run_service()

    repo_root = Path(os.environ.get("YULE_REPO_ROOT", os.getcwd()))

    # P0-T heartbeat — gateway 가 살아있으면 ALIVE 표시되게.
    heartbeats = HeartbeatStore(db_path=db_path)
    hb_task = asyncio.create_task(
        heartbeat_loop(
            service_id=spec.service_id,
            heartbeats=heartbeats,
            shutdown_event=shutdown_event,
            metadata={
                "state": "online",
                "kind": spec.kind.value,
                "transport": "discord_websocket",
            },
            interval_seconds=30.0,
        )
    )

    try:
        await run_engineering_gateway_until_shutdown(
            shutdown_event=shutdown_event,
            bot_factory=lambda: build_engineering_gateway_bot(repo_root),
            token=token,
        )
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        hb_task.cancel()
        try:
            await hb_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    return EXIT_OK


async def run_discord_member_bot(
    spec: ServiceSpec,
    *,
    shutdown_event: asyncio.Event,
    db_path: Optional[Path] = None,
) -> int:
    """Run a single engineering member bot under ``run-service``.

    Hard rails (matching docs/operations.md §0.1 promise):

      * ``spec.role`` must be set — enforced by the inventory schema.
      * Token lookup uses :func:`member_bots.env_key_for` so the env
        contract stays identical to ``yule discord up`` (one source of
        truth).
      * Graceful disable on missing / placeholder-shape token: emit a
        stderr line with the env_key + reason, return
        :data:`EXIT_UNKNOWN_SERVICE` (78). Both the subprocess
        supervisor and systemd unit treat 78 as "stop, don't restart"
        so the rest of the company keeps running.
      * Valid token → drive the bot through the SIGTERM-aware
        :func:`run_member_bot_until_shutdown` so the runtime's
        shutdown event translates into a graceful Discord disconnect.

    The function never mutates ``os.environ`` because the per-role
    token already lives at its dedicated env_key — the bot reads it
    directly via the resolved profile, no override layering needed.
    """

    if not spec.role:
        sys.stderr.write(
            f"yule run-service: {spec.service_id} is DISCORD_MEMBER_BOT "
            "but spec.role is empty; cannot resolve the member token.\n"
        )
        return EXIT_UNKNOWN_SERVICE

    from yule_discord.member.bot import run_member_bot_until_shutdown
    from yule_discord.member.bots import (
        env_key_for,
        looks_like_real_discord_token,
        MemberBotProfile,
    )

    env_key = env_key_for("engineering-agent", spec.role)
    raw = os.environ.get(env_key)
    token = raw.strip() if isinstance(raw, str) and raw.strip() else None

    if not token:
        sys.stderr.write(
            f"yule run-service: {spec.service_id} graceful-disable — "
            f"{env_key} is empty. Add the bot token to .env.local then "
            f"'yule run-service {spec.service_id}' (or 'yule runtime up') "
            "to bring this role bot online.\n"
        )
        record_graceful_disable(
            service_id=spec.service_id,
            db_path=db_path,
            env_key=env_key,
            reason="token_missing",
        )
        return EXIT_UNKNOWN_SERVICE

    if not looks_like_real_discord_token(token):
        sys.stderr.write(
            f"yule run-service: {spec.service_id} graceful-disable — "
            f"{env_key} is set but doesn't match the Discord bot token "
            "shape (placeholder or wrong format). Regenerate the token "
            "in the Discord developer portal and update .env.local.\n"
        )
        record_graceful_disable(
            service_id=spec.service_id,
            db_path=db_path,
            env_key=env_key,
            reason="token_placeholder",
        )
        return EXIT_UNKNOWN_SERVICE

    profile = MemberBotProfile(
        agent_id="engineering-agent",
        role=spec.role,
        env_key=env_key,
        token=token,
        display_label=f"engineering-agent/{spec.role}",
    )

    heartbeats = HeartbeatStore(db_path=db_path)
    hb_task = asyncio.create_task(
        heartbeat_loop(
            service_id=spec.service_id,
            heartbeats=heartbeats,
            shutdown_event=shutdown_event,
            metadata={
                "state": "online",
                "kind": spec.kind.value,
                "role": spec.role,
                "env_key": env_key,
                "transport": "discord_websocket",
            },
            interval_seconds=30.0,
        )
    )

    try:
        await run_member_bot_until_shutdown(
            profile=profile,
            shutdown_event=shutdown_event,
        )
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        hb_task.cancel()
        try:
            await hb_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    return EXIT_OK


def install_role_runner_dispatch_for_run_service() -> None:
    """Best-effort role-runner wiring shim for the run-service path.

    Mirrors the bot.py installer so a ``yule run-service`` start
    publishes the same env-derived dispatcher. Sanitised stdout line on
    success / fallback. Failure is swallowed — the gateway must boot
    even if the role-runner subsystem is misconfigured.
    """

    try:
        from ..agents.runners.bootstrap import (
            install_engineering_role_runner_dispatch,
        )
    except Exception as exc:  # noqa: BLE001 - partial install fallback
        sys.stderr.write(
            f"warning: role-runner bootstrap import failed ({type(exc).__name__}); "
            "gateway continues with deterministic in-process role bodies\n"
        )
        return

    def _on_failure(exc: BaseException) -> None:
        sys.stderr.write(
            "warning: role-runner dispatch install failed "
            f"({type(exc).__name__}); using deterministic fallback\n"
        )

    try:
        trace = install_engineering_role_runner_dispatch(
            on_install_failure=_on_failure
        )
    except Exception as exc:  # noqa: BLE001 - bootstrap must not kill run-service
        _on_failure(exc)
        return
    if trace is None:
        return
    if trace.deterministic_fallback_only:
        configured = [e.provider for e in trace.entries if e.configured]
        sys.stderr.write(
            "role-runner dispatch installed: deterministic fallback only "
            f"(opted-in providers: {configured or 'none'})\n"
        )
    else:
        available = [
            e.provider for e in trace.entries if e.configured and e.available
        ]
        sys.stderr.write(
            f"role-runner dispatch installed: priority={available} "
            "+ deterministic terminal\n"
        )


__all__ = (
    "EXIT_OK",
    "EXIT_UNKNOWN_SERVICE",
    "install_role_runner_dispatch_for_run_service",
    "run_discord_gateway",
    "run_discord_member_bot",
)
