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
from typing import Iterable, Optional, Sequence

from ..agents.job_queue import (
    ApprovalWorker,
    HeartbeatStore,
    JobQueue,
    ObsidianWriterWorker,
    ResearchWorker,
    RoleTakeWorker,
    default_render_fn,
    default_vault_root_resolver,
    default_write_fn,
    env_approval_channel_resolver,
)
from ..agents.job_queue.approval_worker import (
    ApprovalRequest,
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
        await run_supervisor_watch_loop(
            heartbeat_store=heartbeats,
            job_queue=queue,
            shutdown_event=shutdown_event,
        )
        return EXIT_OK

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
        worker = ApprovalWorker(
            queue=queue,
            heartbeats=heartbeats,
            post_fn=_no_post_fn_yet,
            channel_resolver=env_approval_channel_resolver,
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

    raise ValueError(f"no worker builder for kind={spec.kind!r}")


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
    return ((), ())


# ---------------------------------------------------------------------------
# M6.1b boundary placeholder — approval post_fn production wrapper
# (Discord client / REST POST) lands in the next milestone. Until
# then a standalone approval worker fails approval_post jobs to
# ``failed_retryable`` with a clear error so the supervisor sees the
# gap and an operator can fix the wiring.
# ---------------------------------------------------------------------------


async def _no_post_fn_yet(_request: ApprovalRequest, _rendered: str):
    raise RuntimeError(
        "approval_worker post_fn not wired yet "
        "(M6.1b will connect a Discord REST POST to #승인-대기)"
    )


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
    "EXIT_INTERNAL_ERROR",
    "EXIT_OK",
    "EXIT_UNKNOWN_SERVICE",
    "parse_args_and_run",
    "run_service_main",
)
