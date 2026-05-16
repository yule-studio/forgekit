"""``yule runtime status`` CLI adapter — A-M6.3 + A-M7.1.

Glue between argparse and :func:`build_runtime_status`. Kept in a
sibling module so the data builder + renderer in ``status.py``
stay free of CLI / IO concerns and remain unit-testable without
arguments mocking.

A-M7.1 added the ``--post-discord`` flag: after rendering the
status to stdout, the CLI optionally builds the markdown summary
and POSTs it to ``#봇-상태`` via :func:`runtime.status_poster.post_runtime_status_summary`.
Posting is idempotent — a state-hash dedup ensures repeated runs
on identical state don't repost.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from ..agents.job_queue.heartbeat import HeartbeatStore
from ..agents.job_queue.store import JobQueue
from .status import (
    build_runtime_status,
    render_runtime_status_json,
    render_runtime_status_text,
)


# Exit codes — runtime status CLI uses the same conventions as
# run-service so an operator's typo lands a clear code.
EXIT_OK: int = 0
EXIT_UNKNOWN_PROFILE: int = 78  # systemd EX_CONFIG — unrecoverable
EXIT_POST_FAILED: int = 1  # post errored; status text still printed


def run_runtime_status_command(
    *,
    profile: str = "engineering",
    emit_json: bool = False,
    db_path: Optional[Path] = None,
    failed_limit: int = 10,
    post_discord: bool = False,
    force_post: bool = False,
    post_fn: Optional[Callable[[str], Any]] = None,
    state_store: Any = None,
    fallback_lister: Optional[Callable[..., Sequence[Any]]] = None,
    circuit_persistence: Any = None,
) -> int:
    """Build the snapshot and print it. Always read-only.

    When *post_discord* is true the CLI also POSTs the markdown
    summary to ``#봇-상태`` after the local print. Posting failures
    do NOT mask the rendered output — the operator still sees the
    status text/json, then a stderr line names the post error.

    *post_fn* / *state_store* / *fallback_lister* are injectable
    for unit tests; production passes ``None`` and the helpers
    fall back to the env-resolved Discord poster + a JSON-file
    state store under the cache dir.

    Returns 0 on full success, 78 (EX_CONFIG) when the profile
    is unknown, 1 when post was requested and failed.
    """

    queue = JobQueue(db_path=db_path)
    heartbeats = HeartbeatStore(db_path=db_path)
    # A-M7-final: auto-load persisted circuit snapshots so the
    # plain-text / JSON render also surfaces circuit-open services
    # (the supervisor's in-memory ledger isn't reachable from this
    # process, but the persistence mirror is).
    circuits = _load_circuit_snapshots_safe(
        persistence=circuit_persistence, db_path=db_path
    )
    # P0-T status visibility fix — completion funnel recent rows 도 CLI
    # 에서 항상 비어있던 회귀 차단. session_lister 가 None 이면 default
    # workflow_state.list_sessions 사용. fallback_lister 가 inject 되면 그것 사용.
    completion_funnel_recent = _load_completion_funnel_safe(
        fallback_lister=fallback_lister
    )
    try:
        report = build_runtime_status(
            profile=profile,
            queue=queue,
            heartbeats=heartbeats,
            failed_limit=max(0, int(failed_limit)),
            circuit_snapshots=circuits,
            completion_funnel_recent=completion_funnel_recent,
        )
    except ValueError as exc:
        sys.stderr.write(f"yule runtime status: {exc}\n")
        return EXIT_UNKNOWN_PROFILE

    if emit_json:
        sys.stdout.write(render_runtime_status_json(report) + "\n")
    else:
        sys.stdout.write(render_runtime_status_text(report) + "\n")

    if not post_discord:
        return EXIT_OK

    return _dispatch_status_post(
        report=report,
        circuits=circuits,
        force_post=force_post,
        post_fn=post_fn,
        state_store=state_store,
        fallback_lister=fallback_lister,
    )


def _load_completion_funnel_safe(
    *,
    fallback_lister: Optional[Callable[..., Sequence[Any]]] = None,
):
    """Best-effort completion funnel collector for the CLI.

    P0-T runtime status visibility fix — CLI 에서 ``(no recent completions)``
    처럼 항상 비어 보이던 회귀 차단. status_poster 의 collector 를 그대로
    재사용해 markdown post 와 CLI render 가 같은 데이터를 본다. session
    lister 가 없거나 raise 하면 빈 시퀀스 — CLI 가 죽지 않는다.
    """

    try:
        from .status_poster import collect_recent_completion_funnel
    except Exception:  # noqa: BLE001 - partial install fallback
        return ()
    try:
        return collect_recent_completion_funnel(
            session_lister=fallback_lister,
        )
    except Exception:  # noqa: BLE001 - never crash the CLI
        return ()


def _load_circuit_snapshots_safe(
    *,
    persistence: Any,
    db_path: Optional[Path],
):
    """Best-effort circuit-snapshot loader.

    Returns ``None`` (caller treats as "no circuits known") when
    the persistence layer is unavailable / unreadable. The status
    CLI must never crash because of an opportunistic surface.
    """

    try:
        from .circuit_breaker import (
            CircuitBreakerPersistence,
            load_persisted_circuit_snapshots,
        )
    except Exception:  # noqa: BLE001 - partial install fallback
        return None
    store = persistence
    if store is None:
        try:
            store = CircuitBreakerPersistence(db_path=db_path)
        except Exception:  # noqa: BLE001 - persistence unreachable
            return None
    try:
        return load_persisted_circuit_snapshots(persistence=store)
    except Exception:  # noqa: BLE001 - persistence read failure
        return None


def _dispatch_status_post(
    *,
    report: Any,
    circuits: Any,
    force_post: bool,
    post_fn: Optional[Callable[[str], Any]],
    state_store: Any,
    fallback_lister: Optional[Callable[..., Sequence[Any]]],
) -> int:
    """Run the Discord post path; return the CLI exit code.

    Lazy-imports the poster module so a CLI invocation that
    doesn't use ``--post-discord`` doesn't pay the import cost.
    Circuits are passed in (already loaded by the caller) so the
    text/JSON render and the markdown post share one snapshot.
    """

    from .status_poster import (
        collect_recent_fallback_audits,
        post_runtime_status_summary,
    )

    fallbacks = collect_recent_fallback_audits(
        session_lister=fallback_lister,
    )
    outcome = asyncio.new_event_loop().run_until_complete(
        post_runtime_status_summary(
            report=report,
            circuits=circuits,
            fallbacks=fallbacks,
            state_store=state_store,
            post_fn=post_fn,
            force=force_post,
        )
    )

    if outcome.error:
        sys.stderr.write(
            f"yule runtime status: post failed — {outcome.error}\n"
        )
        return EXIT_POST_FAILED

    if outcome.did_post:
        sys.stderr.write(
            f"yule runtime status: posted to #봇-상태 "
            f"(message_id={outcome.posted_message_id}, "
            f"reason={outcome.decision_reason})\n"
        )
    else:
        sys.stderr.write(
            f"yule runtime status: skipped post — {outcome.skipped_reason}\n"
        )
    return EXIT_OK


__all__ = ("run_runtime_status_command",)
