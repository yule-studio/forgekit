"""autonomy_lock — Round 4 of #73.

The producer / scheduler / orchestrator layer needs a tiny, in-memory
lock primitive so two concurrent ticks never decide to enqueue the
same coding_execute / discussion follow-up at the same time. Hard
correctness still comes from the queue's own dedup; this is the
"don't waste effort + log noise" guard.

Pin:

  * acquire returns a token; second acquire on the same scope returns
    None until release / lease expiry.
  * release is idempotent + only succeeds for the original holder.
  * is_held / held_scopes reflect the current state.
  * expired leases are reclaimed lazily on the next access.
  * scope helpers produce stable, scope-prefixed keys.
"""

from __future__ import annotations

import threading
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.autonomy_lock import (
    AutonomyLockRegistry,
    branch_scope,
    coding_job_scope,
    session_scope,
)


class _FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class AutonomyLockScopeHelpersTests(unittest.TestCase):
    def test_branch_scope_namespaces_by_repo(self) -> None:
        self.assertEqual(
            branch_scope("yule/agent", "feature/x"),
            "branch:yule/agent:feature/x",
        )

    def test_session_scope_strips_whitespace(self) -> None:
        self.assertEqual(session_scope("  abc  "), "session:abc")

    def test_coding_job_scope_includes_role(self) -> None:
        self.assertEqual(
            coding_job_scope("sess", "backend-engineer"),
            "coding_job:sess:backend-engineer",
        )


class AutonomyLockRegistryAcquireReleaseTests(unittest.TestCase):
    def test_first_acquire_returns_token_second_returns_none(self) -> None:
        clock = _FakeClock()
        reg = AutonomyLockRegistry(default_ttl_seconds=10.0, clock=clock)

        first = reg.acquire("session:a", holder="producer")
        self.assertIsNotNone(first)
        self.assertEqual(first.scope, "session:a")
        self.assertEqual(first.holder, "producer")

        second = reg.acquire("session:a", holder="producer")
        self.assertIsNone(second)
        self.assertTrue(reg.is_held("session:a"))

    def test_release_drops_holder_and_allows_reacquire(self) -> None:
        clock = _FakeClock()
        reg = AutonomyLockRegistry(default_ttl_seconds=10.0, clock=clock)
        token = reg.acquire("scope-x", holder="p")
        self.assertIsNotNone(token)
        self.assertTrue(reg.release(token))
        self.assertFalse(reg.is_held("scope-x"))
        again = reg.acquire("scope-x", holder="p")
        self.assertIsNotNone(again)

    def test_release_idempotent_and_rejects_stale_token(self) -> None:
        clock = _FakeClock()
        reg = AutonomyLockRegistry(default_ttl_seconds=10.0, clock=clock)
        token = reg.acquire("scope", holder="p")
        self.assertTrue(reg.release(token))
        # Second release must not raise + must return False.
        self.assertFalse(reg.release(token))
        # Re-acquire and try releasing the *original* (stale) token.
        new_token = reg.acquire("scope", holder="p")
        self.assertFalse(reg.release(token))
        self.assertTrue(reg.is_held("scope"))
        reg.release(new_token)

    def test_lease_expiry_reclaims_lazily(self) -> None:
        clock = _FakeClock()
        reg = AutonomyLockRegistry(default_ttl_seconds=5.0, clock=clock)
        first = reg.acquire("scope", holder="p")
        self.assertIsNotNone(first)
        clock.advance(6.0)
        # lease expired — second acquire should now succeed even
        # without an explicit release, mirroring "fail-safe" semantics.
        second = reg.acquire("scope", holder="other")
        self.assertIsNotNone(second)
        # The original holder's release must not steal the new lease.
        self.assertFalse(reg.release(first))
        self.assertTrue(reg.is_held("scope"))

    def test_held_scopes_filters_out_expired(self) -> None:
        clock = _FakeClock()
        reg = AutonomyLockRegistry(default_ttl_seconds=2.0, clock=clock)
        reg.acquire("a", holder="p")
        reg.acquire("b", holder="p")
        self.assertEqual(set(reg.held_scopes().keys()), {"a", "b"})
        clock.advance(3.0)
        self.assertEqual(reg.held_scopes(), {})

    def test_acquire_rejects_empty_scope_or_holder(self) -> None:
        reg = AutonomyLockRegistry()
        self.assertIsNone(reg.acquire("", holder="p"))
        self.assertIsNone(reg.acquire("s", holder=""))

    def test_reset_drops_all_holders(self) -> None:
        reg = AutonomyLockRegistry(default_ttl_seconds=10.0)
        reg.acquire("a", holder="p")
        reg.acquire("b", holder="p")
        reg.reset()
        self.assertEqual(reg.held_scopes(), {})


class AutonomyLockRegistryConcurrencyTests(unittest.TestCase):
    def test_concurrent_acquire_only_one_winner(self) -> None:
        # Sanity check that the in-memory lock is thread-safe — two
        # threads racing on the same scope must observe exactly one
        # successful acquisition.
        reg = AutonomyLockRegistry(default_ttl_seconds=10.0)
        ready = threading.Event()
        results: list = []

        def _worker() -> None:
            ready.wait()
            token = reg.acquire("hot-scope", holder="p")
            results.append(token)

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        ready.set()
        for t in threads:
            t.join()

        winners = [r for r in results if r is not None]
        self.assertEqual(len(winners), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
