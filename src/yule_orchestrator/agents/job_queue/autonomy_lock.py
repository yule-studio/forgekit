"""Autonomy lock registry — Round 4 of #73.

The producer / scheduler / orchestrator layer enqueues new work in
parallel: a producer tick may fire while a CI retry orchestrator is
still wrapping up, while a discussion follow-up scan is selecting the
next role take, and so on. Most of these paths are already idempotent
at the queue level (``CodingExecutorWorker.find_active`` /
``RoleTakeWorker.find_active``), but parallel paths still need a
coarser guard: two producer ticks running side-by-side must not both
decide to requeue the *same* coding_execute row, and a discussion
follow-up must not race against the CI retry orchestrator on the same
branch.

This module provides one tiny primitive — :class:`AutonomyLockRegistry`
— that holds named scope locks for short windows. The registry is
deliberately *advisory*: callers ``acquire`` before doing the
side-effecting work and ``release`` (or let the lease expire) after.
A held lock means "another autonomy tick is already responsible for
this scope; skip and try again next tick", not "this scope is
forbidden to all writers". The queue layer's own dedup is what gives
hard correctness; this layer reduces wasted work + log noise.

Scope key conventions used by the producer:

  * ``branch:{repo}:{branch}`` — coding_execute / CI retry on the
    same head branch.
  * ``session:{session_id}`` — discussion follow-up dispatch.
  * ``coding_job:{session_id}:{role}`` — coding_execute requeue.

The registry is process-local (in-memory). Two processes pointing at
the same SQLite cache rely on the queue + session.extra dispatch
markers for cross-process safety; this lock is for *intra-process*
parallel ticks only (which is what the supervisor watch loop drives).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional


__all__ = (
    "AutonomyLock",
    "AutonomyLockRegistry",
    "branch_scope",
    "coding_job_scope",
    "session_scope",
)


# ---------------------------------------------------------------------------
# Scope helpers — keep the literal strings in one place so producers /
# orchestrators / tests cannot drift apart.
# ---------------------------------------------------------------------------


def branch_scope(repo: str, branch: str) -> str:
    """Lock scope for a (repo, branch) pair.

    Empty *repo* is acceptable (dry-run / local-only) — we still
    namespace the branch separately so two local-only branches don't
    collide.
    """

    return f"branch:{(repo or '').strip()}:{(branch or '').strip()}"


def session_scope(session_id: str) -> str:
    return f"session:{(session_id or '').strip()}"


def coding_job_scope(session_id: str, executor_role: str) -> str:
    return (
        f"coding_job:{(session_id or '').strip()}:"
        f"{(executor_role or '').strip()}"
    )


# ---------------------------------------------------------------------------
# Lock token + registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutonomyLock:
    """Token returned by :meth:`AutonomyLockRegistry.acquire`.

    The token carries enough metadata for the registry to validate a
    matching ``release`` call — callers should treat it as opaque
    apart from the public ``scope`` / ``holder`` accessors used in
    structured log lines.
    """

    scope: str
    holder: str
    acquired_at: float
    expires_at: float
    token_id: str


class AutonomyLockRegistry:
    """Thread-safe in-memory registry of short-lived advisory locks.

    Production: producer / orchestrator share one registry per
    runtime process. Tests instantiate a fresh registry per case so
    state never leaks between tests.

    Lease semantics:

      * :meth:`acquire` returns ``None`` when the scope is currently
        held + the holder hasn't expired.
      * Expired leases are cleaned up lazily on the next ``acquire``
        / ``is_held`` call. The registry NEVER blocks waiting for a
        lease — callers fall back to "skip this tick, try again".
      * :meth:`release` is idempotent. Releasing a stale token (one
        whose lease already expired and was reclaimed) is a no-op
        rather than an error so the producer can release in a
        ``finally`` block without first checking lease state.
    """

    def __init__(
        self,
        *,
        default_ttl_seconds: float = 30.0,
        clock: Optional[object] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._holders: Dict[str, AutonomyLock] = {}
        self._default_ttl = max(1.0, float(default_ttl_seconds))
        self._clock = clock or time.time
        self._counter = 0

    def _now(self) -> float:
        return float(self._clock() if callable(self._clock) else self._clock)

    def acquire(
        self,
        scope: str,
        *,
        holder: str,
        ttl_seconds: Optional[float] = None,
    ) -> Optional[AutonomyLock]:
        """Try to claim *scope*. Returns the token or ``None``."""

        if not scope or not holder:
            return None
        ttl = max(1.0, float(ttl_seconds if ttl_seconds is not None else self._default_ttl))
        with self._lock:
            now = self._now()
            current = self._holders.get(scope)
            if current is not None and current.expires_at > now:
                return None
            self._counter += 1
            token = AutonomyLock(
                scope=scope,
                holder=holder,
                acquired_at=now,
                expires_at=now + ttl,
                token_id=f"{int(now * 1000)}-{self._counter}",
            )
            self._holders[scope] = token
            return token

    def release(self, lock: Optional[AutonomyLock]) -> bool:
        """Release *lock* if it's still the registry's holder.

        Returns True when the registry actually dropped the entry
        (caller's lease was still live + matched), False otherwise.
        Idempotent — safe to call from a ``finally`` block.
        """

        if lock is None:
            return False
        with self._lock:
            current = self._holders.get(lock.scope)
            if current is None:
                return False
            if current.token_id != lock.token_id:
                return False
            del self._holders[lock.scope]
            return True

    def is_held(self, scope: str) -> bool:
        """Return True when *scope* has an unexpired holder."""

        if not scope:
            return False
        with self._lock:
            current = self._holders.get(scope)
            if current is None:
                return False
            if current.expires_at <= self._now():
                # Lazy reclaim — caller treats expired as "free".
                del self._holders[scope]
                return False
            return True

    def held_scopes(self) -> Mapping[str, AutonomyLock]:
        """Snapshot of currently-held scopes for diagnostics / tests."""

        with self._lock:
            now = self._now()
            return {
                scope: token
                for scope, token in self._holders.items()
                if token.expires_at > now
            }

    def reset(self) -> None:
        """Drop every holder. Used by tests + supervisor restart paths."""

        with self._lock:
            self._holders.clear()
