"""Single-worker entrypoint — A-M6.0.

Both ``yule run-service <name>`` (CLI) and the systemd unit
``yule-eng-research-worker.service`` invoke this module. It resolves
the :class:`ServiceSpec`, builds the right worker (or supervisor),
wires a shutdown event to ``SIGTERM``/``SIGINT``, and spins
:func:`run_worker_loop` (or :func:`run_supervisor_watch_loop`) until
the signal arrives.

Exit codes follow systemd convention:

  * ``0`` — clean shutdown (SIGTERM received)
  * ``78`` (``EX_CONFIG``) — unknown / reserved service. systemd's
    ``RestartPreventExitStatus=78`` keeps these from infinite-restart.
  * ``1`` — unexpected error during loop construction.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..agents.job_queue import (
    ApprovalWorker,
    AutonomyLockRegistry,
    AutonomyProducer,
    CodingExecutorWorker,
    HeartbeatStore,
    JobQueue,
    ObsidianWriterWorker,
    ResearchWorker,
    RoleTakeWorker,
    WorkflowSessionState,
    build_completion_funnel,
    build_discussion_followup_dispatcher,
    default_render_fn,
    default_vault_root_resolver,
    default_write_fn,
    dispatch_ready_coding_jobs,
)
from ..agents.job_queue.approval_discord_poster import (
    build_approval_channel_resolver,
    build_production_post_fn,
)
from ..agents.job_queue.coding_execute_progress import (
    make_github_pr_comment_fn,
    record_coding_execute_progress,
)
from ..agents.job_queue.coding_executor_live import (
    build_live_executor,
    detect_live_executor_availability,
)
from ..agents.job_queue.coding_executor_worker import (
    CodingExecuteOutcome,
    CodingExecuteRequest,
)
from ..agents.job_queue.standalone_runners import (
    build_research_runner,
    build_role_take_runner,
)
from ..agents.job_queue.worker_loop import (
    run_supervisor_watch_loop,
    run_worker_loop,
)
from .services import ServiceKind, ServiceSpec, resolve_service


logger = logging.getLogger(__name__)


EXIT_OK: int = 0
EXIT_UNKNOWN_SERVICE: int = 78
EXIT_INTERNAL_ERROR: int = 1


# ---------------------------------------------------------------------------
# Public entrypoint used by the CLI.
# ---------------------------------------------------------------------------


def run_service_main(
    service_id: str,
    *,
    db_path: Optional[Path] = None,
    log_level: str = "INFO",
) -> int:
    """Run *service_id* until SIGTERM/SIGINT.

    Synchronous wrapper so the argparse dispatcher can call it
    directly. Builds an asyncio loop, registers signal handlers,
    drives the worker.
    """

    logging.basicConfig(level=log_level, format="[%(name)s] %(levelname)s %(message)s")

    spec = resolve_service(service_id)
    if spec is None:
        sys.stderr.write(f"yule run-service: unknown service id {service_id!r}\n")
        return EXIT_UNKNOWN_SERVICE
    if not spec.is_implemented():
        sys.stderr.write(
            f"yule run-service: service {service_id!r} is reserved "
            "for a later milestone and has no implementation yet.\n"
        )
        return EXIT_UNKNOWN_SERVICE

    try:
        return asyncio.run(_run_async(spec, db_path=db_path))
    except KeyboardInterrupt:
        # ``asyncio.run`` re-raises Ctrl-C; treat as graceful shutdown.
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 - log + non-zero exit
        logger.exception("run-service %s failed: %s", service_id, exc)
        return EXIT_INTERNAL_ERROR


async def _run_async(spec: ServiceSpec, *, db_path: Optional[Path]) -> int:
    """Async core. Wires shutdown event + builds the worker per spec."""

    shutdown_event = asyncio.Event()
    _install_signal_handlers(shutdown_event)

    queue = JobQueue(db_path=db_path)
    heartbeats = HeartbeatStore(db_path=db_path)

    if spec.kind == ServiceKind.SUPERVISOR:
        post_fn, post_interval = _build_supervisor_status_post(
            queue=queue, heartbeats=heartbeats
        )
        autonomy_tick_fn, autonomy_interval = _build_autonomy_producer_tick(
            queue=queue, heartbeats=heartbeats
        )
        await run_supervisor_watch_loop(
            heartbeat_store=heartbeats,
            job_queue=queue,
            shutdown_event=shutdown_event,
            status_post_fn=post_fn,
            status_post_interval_seconds=post_interval,
            autonomy_producer_tick_fn=autonomy_tick_fn,
            autonomy_producer_interval_seconds=autonomy_interval,
        )
        return EXIT_OK

    if spec.kind == ServiceKind.DISCORD_GATEWAY:
        return await _run_discord_gateway(spec, shutdown_event=shutdown_event)

    process_job_fn = _build_process_job(spec, queue=queue, heartbeats=heartbeats)
    job_types, roles = _pick_filters_for(spec)

    await run_worker_loop(
        service_id=spec.service_id,
        queue=queue,
        heartbeats=heartbeats,
        process_job=process_job_fn,
        job_types=job_types,
        roles=roles,
        shutdown_event=shutdown_event,
    )
    return EXIT_OK


async def _run_discord_gateway(
    spec: ServiceSpec,
    *,
    shutdown_event: asyncio.Event,
) -> int:
    """Run the engineering gateway under ``run-service``.

    Resolves the gateway token, layers the planning-bot env
    overrides via :func:`build_gateway_env_overrides`, then drives
    the bot through :func:`run_engineering_gateway_until_shutdown`
    so SIGTERM at the runtime level translates into
    ``await bot.close()`` instead of relying on discord.py's
    internal signal handlers (which only fire when ``bot.run``
    owns the main thread — under ``run-service`` the runtime owns
    the loop, so the legacy thread path saw the runtime swallow
    the signal first and the bot kept running until the parent
    killed it).
    """

    from ..discord.bot import (
        build_engineering_gateway_bot,
        run_engineering_gateway_until_shutdown,
    )
    from .gateway_env import (
        build_gateway_env_overrides,
        resolve_gateway_token,
    )
    import os

    token = resolve_gateway_token()
    if token is None:
        sys.stderr.write(
            "yule run-service: ENGINEERING_AGENT_BOT_GATEWAY_TOKEN unset; "
            "engineering gateway cannot start.\n"
        )
        return EXIT_UNKNOWN_SERVICE

    overrides = build_gateway_env_overrides(gateway_token=token)
    # Apply overrides in-place so the bot's env reads see the
    # gateway-only view.
    for key, value in overrides.items():
        os.environ[key] = value

    # A-M11b: install the role-runner dispatcher from env *before* the
    # bot starts. ``build_engineering_gateway_bot`` also calls the
    # installer (legacy ``run_discord_bot`` direct path), so this call
    # is idempotent — last-one-wins on the dispatcher binding. We
    # install here so an operator running ``yule run-service
    # eng-discord-gateway`` sees the trace stdout line *before* the
    # bot's discord login attempt, which makes "왜 fallback 떨어졌어?"
    # answerable from the run-service logs alone.
    _install_role_runner_dispatch_for_run_service()

    repo_root = Path(os.environ.get("YULE_REPO_ROOT", os.getcwd()))
    try:
        await run_engineering_gateway_until_shutdown(
            shutdown_event=shutdown_event,
            bot_factory=lambda: build_engineering_gateway_bot(repo_root),
            token=token,
        )
    except KeyboardInterrupt:
        return EXIT_OK
    return EXIT_OK


def _install_role_runner_dispatch_for_run_service() -> None:
    """Best-effort role-runner wiring shim for the run-service path.

    Mirrors the bot.py installer so a ``yule run-service`` start
    publishes the same env-derived dispatcher. Sanitised stdout line
    on success / fallback. Failure is swallowed — the gateway must
    boot even if the role-runner subsystem is misconfigured.
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


# ---------------------------------------------------------------------------
# Worker construction — one closure per ServiceKind.
# ---------------------------------------------------------------------------


def _build_process_job(spec: ServiceSpec, *, queue, heartbeats):
    """Return an async ``process_job(job)`` closure for *spec*.

    Each branch wires the queue + heartbeats into the right worker
    and returns the worker's bound ``process_job`` (already async or
    wrapped to look async). Same shape regardless of worker kind so
    :func:`run_worker_loop` doesn't branch.
    """

    if spec.kind == ServiceKind.RESEARCH_WORKER:
        worker = ResearchWorker(queue=queue, heartbeats=heartbeats)
        # M6.1a wiring: the standalone runner reloads the session,
        # runs the collector with the role+prompt the producer
        # stamped on the job's payload, and persists the resulting
        # research_pack onto session.extra. Forum publish + user
        # follow_up message stay on the in-process gateway path
        # (handled by M3's run_one) until M6.2 splits that off.
        research_runner = build_research_runner()

        async def _process(job):
            await worker.process_job(job, runner=research_runner)

        return _process

    if spec.kind == ServiceKind.ROLE_WORKER:
        if not spec.role:
            raise ValueError(
                f"role worker {spec.service_id} missing role filter"
            )
        worker = RoleTakeWorker(
            queue=queue, heartbeats=heartbeats, role_filter=spec.role
        )
        role_runner = build_role_take_runner()

        async def _process(job):
            worker.process_job(job, runner=role_runner)

        return _process

    if spec.kind == ServiceKind.APPROVAL_WORKER:
        # M6.1b-1 wiring: production post_fn POSTs the rendered
        # approval card to ``DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID``
        # via the Discord REST API using
        # ``ENGINEERING_AGENT_BOT_GATEWAY_TOKEN`` (or
        # ``DISCORD_BOT_TOKEN`` as a single-bot dev fallback).
        # M6.2 wraps the channel resolver with a NAME-fallback so an
        # operator who only set ``DISCORD_ENGINEERING_APPROVAL_CHANNEL_NAME``
        # + ``DISCORD_GUILD_ID`` still gets a resolved id without
        # the gateway being involved.
        production_post_fn = build_production_post_fn()
        channel_resolver = build_approval_channel_resolver()
        worker = ApprovalWorker(
            queue=queue,
            heartbeats=heartbeats,
            post_fn=production_post_fn,
            channel_resolver=channel_resolver,
        )

        async def _process(job):
            await worker.process_job(job)

        return _process

    if spec.kind == ServiceKind.OBSIDIAN_WRITER:
        worker = ObsidianWriterWorker(
            queue=queue,
            heartbeats=heartbeats,
            render_fn=default_render_fn,
            write_fn=default_write_fn,
            vault_root_resolver=default_vault_root_resolver,
        )

        async def _process(job):
            await worker.process_job(job)

        return _process

    if spec.kind == ServiceKind.CODING_EXECUTOR:
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

        # Producer side runs once per consumer tick — picks up any
        # ``coding_job=ready`` session the gateway approved between
        # iterations and stamps the dispatch marker on the session.
        # Failures inside the dispatcher are swallowed (see the
        # dispatcher's per-row try/except) so a bad session can never
        # crash the consumer loop.
        async def _process(job):
            try:
                dispatch_ready_coding_jobs(worker=worker)
            except Exception:  # noqa: BLE001 - producer must not break consumer
                logger.warning(
                    "coding_execute dispatcher tick raised", exc_info=True
                )
            outcome = worker.process_job(job)
            _record_coding_progress_after_outcome(
                outcome=outcome,
                obsidian_writer=obsidian_progress_writer,
                progress_post_fn=progress_post_fn,
            )

        return _process

    raise ValueError(f"no worker builder for kind={spec.kind!r}")


# ---------------------------------------------------------------------------
# Coding executor bundle — Round 3 wiring
# ---------------------------------------------------------------------------
#
# Resolves the live executor Protocol implementations once at service
# startup. The bundle composition is *layered* so an operator can opt in
# to live push without first wiring the LLM editor (which the project
# treats as a separate authorization gate):
#
#   * worktree provisioner / record-only editor / subprocess test runner /
#     local git committer — always wired (no external creds).
#   * pusher + draft PR creator — wired ONLY when GitHub App env is set
#     (``YULE_GITHUB_APP_ID`` + installation id + private key path).
#     Otherwise the worker keeps the ``_NotImplementedStep`` defaults
#     and a live ``dry_run=False`` job lands as
#     ``REASON_NOT_IMPLEMENTED`` rather than half-running.
#
# An operator who explicitly forces the dry-run env wins over both
# (we never push when the operator says "just verify the plumbing").
ENV_CODING_EXECUTOR_REPO_ROOT: str = "YULE_CODING_EXECUTOR_REPO_ROOT"
ENV_CODING_EXECUTOR_WORKTREE_ROOT: str = "YULE_CODING_EXECUTOR_WORKTREE_ROOT"


def build_coding_executor_bundle(
    *,
    env: Optional[Mapping[str, str]] = None,
    live_client_factory: Optional[Any] = None,
) -> Mapping[str, Any]:
    """Return the kwargs for :class:`CodingExecutorWorker` construction.

    *env* defaults to ``os.environ``; tests pass a controlled mapping
    to drive the matrix without polluting the live process. The
    *live_client_factory* injection point lets tests stub the live
    GitHub App client without exercising the real JWT path.
    """

    import os as _os

    source: Mapping[str, str] = env if env is not None else _os.environ
    repo_root = (source.get(ENV_CODING_EXECUTOR_REPO_ROOT) or "").strip()
    if not repo_root:
        repo_root = (source.get("YULE_REPO_ROOT") or _os.getcwd()).strip() or _os.getcwd()
    worktree_root = (source.get(ENV_CODING_EXECUTOR_WORKTREE_ROOT) or "").strip() or None

    live_client = None
    if live_client_factory is not None:
        try:
            live_client = live_client_factory()
        except Exception:  # noqa: BLE001 - never crash service startup
            logger.warning(
                "coding executor: live_client_factory raised; "
                "pusher / draft PR will fall back to dry-run blocker",
                exc_info=True,
            )
            live_client = None
    else:
        live_client = _maybe_build_live_github_client(env=source)

    bundle = build_live_executor(
        repo_root=repo_root,
        live_client=live_client,
        worktree_root=worktree_root,
    )
    availability = detect_live_executor_availability(
        repo_root=repo_root, live_client=live_client
    )
    logger.info(
        "coding executor wiring: pusher=%s draft_pr=%s editor=%s",
        availability.pusher,
        availability.draft_pr_creator,
        availability.code_editor,
    )
    return bundle


def _build_coding_progress_post_fn() -> Optional[Any]:
    """Construct the GitHub PR comment poster wired around the live client.

    Returns None when the GitHub App env is absent so the runtime
    happily writes only the Obsidian task-log + in-memory progress
    history. The poster is shared across all coding_execute outcomes
    in this process — tokens are minted lazily inside the live client.
    """

    import os as _os

    live_client = _maybe_build_live_github_client(env=_os.environ)
    if live_client is None:
        return None
    return make_github_pr_comment_fn(live_client)


def _record_coding_progress_after_outcome(
    *,
    outcome: CodingExecuteOutcome,
    obsidian_writer: Any,
    progress_post_fn: Optional[Any],
) -> None:
    """Wrap :func:`record_coding_execute_progress` for the run-service path.

    Loads the originating session via the workflow store (best effort —
    when the cache row is gone the recorder still creates the in-memory
    entry but the persisted history isn't updated). All exceptions are
    swallowed so a progress recorder bug never derails the consumer.
    """

    if outcome is None or outcome.job is None:
        return
    payload = outcome.job.payload or {}
    session_id = str(payload.get("session_id") or outcome.job.session_id or "")
    if not session_id:
        return

    try:
        from ..agents.workflow_state import load_session
    except Exception:  # noqa: BLE001
        return
    try:
        session = load_session(session_id)
    except Exception:  # noqa: BLE001
        session = None
    if session is None:
        return

    try:
        request = CodingExecuteRequest.from_payload(payload)
    except Exception:  # noqa: BLE001
        request = None

    try:
        record_coding_execute_progress(
            session=session,
            outcome=outcome,
            request=request,
            obsidian_writer=obsidian_writer,
            github_comment_fn=progress_post_fn,
            repo_full_name=request.repo_full_name if request else None,
        )
    except Exception:  # noqa: BLE001 - never crash consumer on progress bug
        logger.warning(
            "coding_execute progress recorder raised", exc_info=True
        )


def _maybe_build_live_github_client(*, env: Mapping[str, str]) -> Optional[Any]:
    """Return a live GitHub App client if env is set, else None.

    The factory only fires when *all three* env keys are present —
    we don't want a partial config (e.g. app id but no key path) to
    silently force a stub through. Errors at construction are
    swallowed so the consumer falls back to dry-run-only.
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


def _pick_filters_for(spec: ServiceSpec):
    """Return ``(job_types, roles)`` filters for the queue pick."""

    if spec.kind == ServiceKind.RESEARCH_WORKER:
        return (("research_collect",), ())
    if spec.kind == ServiceKind.ROLE_WORKER:
        return (("role_take",), (spec.role,) if spec.role else ())
    if spec.kind == ServiceKind.APPROVAL_WORKER:
        return (("approval_post",), ())
    if spec.kind == ServiceKind.OBSIDIAN_WRITER:
        return (("obsidian_write",), ())
    if spec.kind == ServiceKind.CODING_EXECUTOR:
        return (("coding_execute",), ())
    return ((), ())


# M6.1b-1 landed the production post_fn (build_production_post_fn).
# The remaining placeholder is the gateway service wiring (M6.1b-2).


# ---------------------------------------------------------------------------
# A-M7-final: status posting wired into the supervisor watch loop.
# ---------------------------------------------------------------------------


_STATUS_POST_ENABLED_ENV: str = "ENGINEERING_STATUS_POST_ENABLED"
_STATUS_POST_INTERVAL_ENV: str = "ENGINEERING_STATUS_POST_INTERVAL_SECONDS"
_DEFAULT_STATUS_POST_INTERVAL_SECONDS: float = 3600.0  # 1h


def _build_supervisor_status_post(
    *,
    queue,
    heartbeats,
):
    """Return ``(post_fn, interval)`` pair.

    Production wiring: when
    ``ENGINEERING_STATUS_POST_ENABLED`` is truthy we build a
    closure that snapshots the current runtime state and POSTs
    the markdown summary to ``#봇-상태`` via the M7.1 helpers.
    Disabled by default so an operator must opt in.

    Returns ``(None, None)`` when posting is off — the supervisor
    loop treats that as "no posting tick" and behaves exactly like
    the M6.0 path.
    """

    import os as _os

    raw_enabled = (_os.environ.get(_STATUS_POST_ENABLED_ENV) or "").strip().lower()
    if raw_enabled not in {"1", "true", "yes", "on"}:
        return None, None

    raw_interval = (_os.environ.get(_STATUS_POST_INTERVAL_ENV) or "").strip()
    interval: float
    if raw_interval:
        try:
            interval = max(60.0, float(raw_interval))
        except ValueError:
            interval = _DEFAULT_STATUS_POST_INTERVAL_SECONDS
    else:
        interval = _DEFAULT_STATUS_POST_INTERVAL_SECONDS

    async def _post_once() -> None:
        # Lazy imports keep the supervisor branch importable when
        # status posting is off (no Discord deps loaded).
        from .circuit_breaker import load_persisted_circuit_snapshots
        from .status import build_runtime_status
        from .status_poster import (
            collect_recent_fallback_audits,
            post_runtime_status_summary,
        )

        circuits = load_persisted_circuit_snapshots()
        report = build_runtime_status(
            queue=queue,
            heartbeats=heartbeats,
            circuit_snapshots=circuits,
        )
        fallbacks = collect_recent_fallback_audits()
        outcome = await post_runtime_status_summary(
            report=report,
            circuits=circuits,
            fallbacks=fallbacks,
        )
        if outcome.error:
            logger.warning(
                "supervisor status post failed: %s", outcome.error
            )
        elif outcome.did_post:
            logger.info(
                "supervisor status post: posted (reason=%s, message_id=%s)",
                outcome.decision_reason,
                outcome.posted_message_id,
            )

    return _post_once, interval


# ---------------------------------------------------------------------------
# Round 4 of #73 — autonomy producer tick wiring
# ---------------------------------------------------------------------------
#
# The supervisor watchdog runs the autonomy producer on its own
# interval so the runtime auto-generates the next coding_execute /
# discussion follow-up after every completion. The interval is
# operator-tunable; ``None`` (default) keeps the producer dormant
# so this PR doesn't change supervisor behaviour for installations
# that haven't opted in yet.

ENV_AUTONOMY_PRODUCER_ENABLED: str = "YULE_AUTONOMY_PRODUCER_ENABLED"
ENV_AUTONOMY_PRODUCER_INTERVAL: str = "YULE_AUTONOMY_PRODUCER_INTERVAL_SECONDS"
DEFAULT_AUTONOMY_PRODUCER_INTERVAL_SECONDS: float = 30.0


def _build_autonomy_producer_tick(
    *,
    queue: JobQueue,
    heartbeats: HeartbeatStore,
):
    """Return ``(tick_fn, interval)`` for the supervisor's autonomy hook.

    Returns ``(None, None)`` unless
    ``YULE_AUTONOMY_PRODUCER_ENABLED`` is truthy. The producer wires
    to a single ``CodingExecutorWorker`` instance + the production
    follow-up dispatcher; the lock registry is process-local so two
    supervisor restarts naturally start with a clean slate.

    Failures during construction (e.g. missing executor bundle env)
    log a warning + return the dormant pair — the supervisor still
    runs sweep / status post, just without the producer tick.
    """

    import os as _os

    raw_enabled = (_os.environ.get(ENV_AUTONOMY_PRODUCER_ENABLED) or "").strip().lower()
    if raw_enabled not in {"1", "true", "yes", "on"}:
        return None, None

    raw_interval = (_os.environ.get(ENV_AUTONOMY_PRODUCER_INTERVAL) or "").strip()
    interval: float
    if raw_interval:
        try:
            interval = max(5.0, float(raw_interval))
        except ValueError:
            interval = DEFAULT_AUTONOMY_PRODUCER_INTERVAL_SECONDS
    else:
        interval = DEFAULT_AUTONOMY_PRODUCER_INTERVAL_SECONDS

    try:
        coding_bundle = build_coding_executor_bundle()
        coding_worker = CodingExecutorWorker(
            queue=queue, heartbeats=heartbeats, **coding_bundle
        )
        role_worker = RoleTakeWorker(queue=queue, heartbeats=heartbeats)
        research_worker = ResearchWorker(queue=queue, heartbeats=heartbeats)
        followup = build_discussion_followup_dispatcher(
            role_take_worker=role_worker,
            research_worker=research_worker,
        )
        session_state = WorkflowSessionState()
        registry = AutonomyLockRegistry()
        producer = AutonomyProducer(
            session_state=session_state,
            coding_executor=coding_worker,
            lock_registry=registry,
            followup_dispatch=followup,
        )
    except Exception:  # noqa: BLE001 - never crash supervisor startup
        logger.warning(
            "autonomy producer construction failed — supervisor will tick "
            "without producer hook",
            exc_info=True,
        )
        return None, None

    def _tick():
        return producer.tick()

    return _tick, interval


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------


def _install_signal_handlers(shutdown_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            # Windows / threads without a default sig handler. The
            # supervisor parent (M6.0) sends SIGTERM via
            # ``Process.terminate`` so this only matters on dev hosts.
            signal.signal(sig, lambda *_: shutdown_event.set())


# ---------------------------------------------------------------------------
# CLI argument helpers used by main.py.
# ---------------------------------------------------------------------------


def parse_args_and_run(argv: Optional[Iterable[str]] = None) -> int:
    """argparse-style entry; ``cli/main.py`` calls this from the
    ``run-service`` subparser."""

    import argparse

    parser = argparse.ArgumentParser(prog="yule run-service")
    parser.add_argument("service_id")
    parser.add_argument(
        "--log-level", default="INFO", help="logging level (DEBUG/INFO/...)"
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="override SQLite cache path (defaults to YULE_CACHE_DB_PATH)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    db = Path(args.db_path) if args.db_path else None
    return run_service_main(
        args.service_id, db_path=db, log_level=args.log_level
    )


__all__ = (
    "DEFAULT_AUTONOMY_PRODUCER_INTERVAL_SECONDS",
    "ENV_AUTONOMY_PRODUCER_ENABLED",
    "ENV_AUTONOMY_PRODUCER_INTERVAL",
    "ENV_CODING_EXECUTOR_REPO_ROOT",
    "ENV_CODING_EXECUTOR_WORKTREE_ROOT",
    "EXIT_INTERNAL_ERROR",
    "EXIT_OK",
    "EXIT_UNKNOWN_SERVICE",
    "build_coding_executor_bundle",
    "parse_args_and_run",
    "run_service_main",
)
