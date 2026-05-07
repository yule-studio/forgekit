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
    try:
        report = build_runtime_status(
            profile=profile,
            queue=queue,
            heartbeats=heartbeats,
            failed_limit=max(0, int(failed_limit)),
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
        force_post=force_post,
        post_fn=post_fn,
        state_store=state_store,
        fallback_lister=fallback_lister,
    )


def _dispatch_status_post(
    *,
    report: Any,
    force_post: bool,
    post_fn: Optional[Callable[[str], Any]],
    state_store: Any,
    fallback_lister: Optional[Callable[..., Sequence[Any]]],
) -> int:
    """Run the Discord post path; return the CLI exit code.

    Lazy-imports the poster module so a CLI invocation that
    doesn't use ``--post-discord`` doesn't pay the import cost.
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
            circuits={},  # supervisor parent owns circuit state — out of scope here
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
