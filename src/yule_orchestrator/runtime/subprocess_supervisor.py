"""Multi-worker subprocess supervisor — A-M6.0.

Backs ``yule runtime up --profile engineering``. Spawns each
implemented :class:`ServiceSpec` as its own ``yule run-service``
subprocess, keeps it alive with a backoff restart policy, and
forwards SIGTERM/SIGINT to every child for graceful shutdown.

Production deployments swap this out for systemd units that call
the same ``yule run-service`` entrypoint directly. The supervisor
here exists for dev / single-host setups.

Dependency injection seams:

  * ``spawn_fn`` — replaces ``asyncio.create_subprocess_exec`` for
    tests (returns a ``_FakeProcess`` rather than launching a real
    binary).
  * ``sleep_fn`` — replaces ``asyncio.sleep`` so the backoff math
    can be exercised without real wall-clock delay.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
)

from .services import ServiceKind, ServiceSpec, list_services


logger = logging.getLogger(__name__)


# Backoff schedule. Cap at 30 s so a permanently-failing service
# doesn't burn restart attempts at fast intervals — the supervisor
# stays "checking once every 30 s" and the operator has time to
# look at the journal.
DEFAULT_BACKOFF_SCHEDULE: Tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0)
DEFAULT_SHUTDOWN_TIMEOUT: float = 30.0


# ``run_service`` 's exit code 78 means "config error — don't
# restart". Mirrors systemd's ``RestartPreventExitStatus=78`` so
# dev (here) and prod (systemd) treat the same exit code identically.
EXIT_PREVENT_RESTART: int = 78


@dataclass
class ManagedProcess:
    """One child process the supervisor manages.

    Mutable on purpose (restart_count / process / exit_code change
    each iteration) so the test harness can inspect state mid-run.
    """

    spec: ServiceSpec
    cmd: Sequence[str]
    env: dict
    process: Optional[Any] = None
    restart_count: int = 0
    last_exit_code: Optional[int] = None
    stopped_intentionally: bool = False


# Async spawn function shape: returns whatever ``asyncio`` returns.
# Tests pass a stub returning a fake-process-like object exposing
# ``wait()`` (awaitable) + ``terminate()`` + ``kill()`` + ``returncode``.
SpawnFn = Callable[
    [Sequence[str], dict],
    Awaitable[Any],
]
SleepFn = Callable[[float], Awaitable[None]]


async def _default_spawn(cmd: Sequence[str], env: dict) -> Any:
    # Capture child stdout/stderr through pipes so we can prefix
    # each line with the service id. Without this every child
    # writes raw lines into the same terminal stream and a busy
    # runtime makes operator log triage hard.
    return await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _forward_with_prefix(
    *,
    stream: Any,
    sink: Any,
    prefix: str,
) -> None:
    """Read *stream* line-by-line, write each line to *sink*
    prefixed with ``[<prefix>] ``.

    Tolerant of:
      - non-utf8 bytes (decoded with errors=replace)
      - partial trailing line (forwarded with trailing newline)
      - sink errors (broken stdout pipe — log + drop the rest;
        we don't want a logging bug to kill the supervisor)
    """

    if stream is None:
        return
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            if not text.endswith("\n"):
                text = text + "\n"
            try:
                sink.write(f"[{prefix}] {text}")
                sink.flush()
            except Exception:  # noqa: BLE001 - sink may be closed
                return
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception:  # noqa: BLE001 - never crash the supervisor
        logger.warning(
            "stdout forward raised for %s", prefix, exc_info=True
        )


async def run_runtime_up(
    *,
    profile: str = "engineering",
    spawn_fn: Optional[SpawnFn] = None,
    sleep_fn: Optional[SleepFn] = None,
    backoff_schedule: Sequence[float] = DEFAULT_BACKOFF_SCHEDULE,
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
    extra_env: Optional[dict] = None,
    base_cmd: Sequence[str] = ("yule", "run-service"),
    shutdown_event: Optional[asyncio.Event] = None,
) -> int:
    """Spawn every implemented service in *profile* and supervise.

    Returns ``0`` once all children have been reaped (after
    SIGTERM). Errors during spawn surface as a non-zero return.
    """

    spawn_fn = spawn_fn or _default_spawn
    sleep_fn = sleep_fn or asyncio.sleep

    # Build the managed-process list from the inventory. ``RESERVED_*``
    # rows are skipped silently — they're listed in the inventory for
    # documentation but not implemented yet (gateway is M6.1).
    managed: List[ManagedProcess] = []
    import os as _os

    base_env = dict(_os.environ)
    if extra_env:
        base_env.update(extra_env)

    for spec in list_services(profile):
        if not spec.is_implemented():
            logger.info(
                "runtime up: skipping reserved service %s", spec.service_id
            )
            continue
        cmd = list(base_cmd) + [spec.service_id]
        managed.append(ManagedProcess(spec=spec, cmd=cmd, env=base_env))

    shutdown_event = shutdown_event or asyncio.Event()
    _install_signal_handlers(shutdown_event)

    supervisors = [
        asyncio.create_task(
            _supervise_one(
                managed=mp,
                shutdown_event=shutdown_event,
                spawn_fn=spawn_fn,
                sleep_fn=sleep_fn,
                backoff_schedule=tuple(backoff_schedule),
            ),
            name=f"supervise:{mp.spec.service_id}",
        )
        for mp in managed
    ]

    # Wait for shutdown_event. Each supervisor task drains its own
    # child on the way down.
    await shutdown_event.wait()
    logger.info("runtime up: shutdown_event fired, draining children")

    await _drain_children(
        managed=managed,
        sleep_fn=sleep_fn,
        shutdown_timeout=shutdown_timeout,
    )

    # Cancel supervisors that haven't returned yet (they should have
    # noticed the shutdown_event by now).
    for task in supervisors:
        if not task.done():
            task.cancel()
    await asyncio.gather(*supervisors, return_exceptions=True)

    return 0


async def _supervise_one(
    *,
    managed: ManagedProcess,
    shutdown_event: asyncio.Event,
    spawn_fn: SpawnFn,
    sleep_fn: SleepFn,
    backoff_schedule: Tuple[float, ...],
) -> None:
    """Spawn → wait → backoff → spawn loop for a single service."""

    while not shutdown_event.is_set():
        try:
            managed.process = await spawn_fn(managed.cmd, managed.env)
        except Exception:  # noqa: BLE001 - log + back off
            logger.exception(
                "runtime up: spawn failed for %s", managed.spec.service_id
            )
            await sleep_fn(_backoff_for(managed.restart_count, backoff_schedule))
            managed.restart_count += 1
            continue

        logger.info("runtime up: started %s", managed.spec.service_id)

        # Each spawn gets its own pair of forwarder tasks so child
        # stdout/stderr lines land in the parent stream prefixed by
        # the service id. Cancelled on wait() return so a restart
        # cleanly replaces them.
        prefix = managed.spec.service_id
        forwarders: list[asyncio.Task] = []
        if getattr(managed.process, "stdout", None) is not None:
            forwarders.append(
                asyncio.create_task(
                    _forward_with_prefix(
                        stream=managed.process.stdout,
                        sink=sys.stdout,
                        prefix=prefix,
                    ),
                    name=f"stdout:{prefix}",
                )
            )
        if getattr(managed.process, "stderr", None) is not None:
            forwarders.append(
                asyncio.create_task(
                    _forward_with_prefix(
                        stream=managed.process.stderr,
                        sink=sys.stderr,
                        prefix=prefix,
                    ),
                    name=f"stderr:{prefix}",
                )
            )

        try:
            exit_code = await managed.process.wait()
        except asyncio.CancelledError:
            # Drain path will terminate / kill the child; cancel
            # forwarders before propagating.
            for task in forwarders:
                if not task.done():
                    task.cancel()
            raise
        finally:
            for task in forwarders:
                if not task.done():
                    task.cancel()
            for task in forwarders:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        managed.last_exit_code = int(exit_code) if exit_code is not None else None

        if shutdown_event.is_set():
            logger.info(
                "runtime up: %s exited during shutdown (code=%s)",
                managed.spec.service_id,
                managed.last_exit_code,
            )
            return

        if managed.last_exit_code == EXIT_PREVENT_RESTART:
            logger.error(
                "runtime up: %s exited with code 78 (config error); not restarting",
                managed.spec.service_id,
            )
            managed.stopped_intentionally = True
            return

        if managed.last_exit_code == 0:
            # A queue worker that exits cleanly without a shutdown
            # signal is unusual but not catastrophic. We restart it
            # just in case the operator wanted a long-running loop.
            logger.warning(
                "runtime up: %s exited 0 unexpectedly, restarting",
                managed.spec.service_id,
            )
        else:
            logger.warning(
                "runtime up: %s exited code=%s, scheduling restart",
                managed.spec.service_id,
                managed.last_exit_code,
            )

        backoff = _backoff_for(managed.restart_count, backoff_schedule)
        managed.restart_count += 1
        await sleep_fn(backoff)


def _backoff_for(restart_count: int, schedule: Tuple[float, ...]) -> float:
    if not schedule:
        return 1.0
    idx = min(restart_count, len(schedule) - 1)
    return float(schedule[idx])


async def _drain_children(
    *,
    managed: Sequence[ManagedProcess],
    sleep_fn: SleepFn,
    shutdown_timeout: float,
) -> None:
    """SIGTERM every live child, wait up to *shutdown_timeout*, then
    SIGKILL the rest.
    """

    for mp in managed:
        proc = mp.process
        if proc is None or _process_done(proc):
            continue
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001 - already exited, etc
            logger.warning(
                "runtime up: terminate() raised for %s",
                mp.spec.service_id,
                exc_info=True,
            )

    deadline = max(0.0, float(shutdown_timeout))
    waited = 0.0
    poll = 0.5
    while waited < deadline:
        if all(_process_done(mp.process) for mp in managed):
            return
        await sleep_fn(poll)
        waited += poll

    for mp in managed:
        proc = mp.process
        if proc is None or _process_done(proc):
            continue
        logger.warning(
            "runtime up: %s did not exit within %.1fs, sending SIGKILL",
            mp.spec.service_id,
            shutdown_timeout,
        )
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _process_done(proc: Any) -> bool:
    if proc is None:
        return True
    return getattr(proc, "returncode", None) is not None


def _install_signal_handlers(shutdown_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: shutdown_event.set())


# ---------------------------------------------------------------------------
# Dry-run / list mode
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DryRunPlan:
    """What ``yule runtime up --dry-run`` prints — no spawning."""

    profile: str
    services: Tuple[Tuple[str, str, Tuple[str, ...]], ...]
    skipped: Tuple[Tuple[str, str], ...]


def build_dry_run_plan(
    *,
    profile: str = "engineering",
    base_cmd: Sequence[str] = ("yule", "run-service"),
) -> DryRunPlan:
    services: list[Tuple[str, str, Tuple[str, ...]]] = []
    skipped: list[Tuple[str, str]] = []
    for spec in list_services(profile):
        if not spec.is_implemented():
            skipped.append((spec.service_id, spec.description))
            continue
        cmd = tuple(list(base_cmd) + [spec.service_id])
        services.append((spec.service_id, spec.description, cmd))
    return DryRunPlan(
        profile=profile,
        services=tuple(services),
        skipped=tuple(skipped),
    )


def render_dry_run_plan(plan: DryRunPlan) -> str:
    lines: list[str] = [
        f"profile: {plan.profile}",
        f"services to start: {len(plan.services)}",
    ]
    for service_id, description, cmd in plan.services:
        lines.append(f"  - {service_id}  # {description}")
        lines.append("    cmd: " + " ".join(cmd))
    if plan.skipped:
        lines.append("")
        lines.append(f"reserved (not started): {len(plan.skipped)}")
        for service_id, description in plan.skipped:
            lines.append(f"  - {service_id}  # {description}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------


def parse_args_and_run(argv: Optional[Iterable[str]] = None) -> int:
    """argparse-style entry the CLI dispatcher calls."""

    import argparse

    parser = argparse.ArgumentParser(prog="yule runtime up")
    parser.add_argument("--profile", default="engineering")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true", help="alias for --dry-run")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=args.log_level,
        format="[%(name)s] %(levelname)s %(message)s",
    )

    if args.dry_run or args.list:
        plan = build_dry_run_plan(profile=args.profile)
        print(render_dry_run_plan(plan))
        return 0

    try:
        return asyncio.run(run_runtime_up(profile=args.profile))
    except KeyboardInterrupt:
        return 0


__all__ = (
    "DEFAULT_BACKOFF_SCHEDULE",
    "DEFAULT_SHUTDOWN_TIMEOUT",
    "DryRunPlan",
    "ManagedProcess",
    "build_dry_run_plan",
    "parse_args_and_run",
    "render_dry_run_plan",
    "run_runtime_up",
)
