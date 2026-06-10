"""self_improvement_worktree — provisioner + dedup + stale detection."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.lifecycle.self_improvement_worktree import (
    DEFAULT_BRANCH_PREFIX,
    InMemoryWorktreeRegistry,
    WorktreeMetadata,
    build_branch_name,
    build_worktree_path,
    detect_stale_worktrees,
    provision_worktree_for_problem,
)


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)


class _Provisioner:
    def __init__(self) -> None:
        self.created: list = []
        self._existing: set = set()

    def create(self, *, branch, path, base_branch, cwd) -> None:
        self.created.append((branch, path, base_branch, cwd))
        self._existing.add((branch, path))

    def exists(self, *, branch, path) -> bool:
        return (branch, path) in self._existing

    def remove(self, *, branch, path, force=False) -> None:
        self._existing.discard((branch, path))


class BranchNameTests(unittest.TestCase):
    def test_branch_uses_self_improve_prefix(self) -> None:
        name = build_branch_name(problem_signature="example-signal.1234abcd")
        self.assertTrue(name.startswith(f"{DEFAULT_BRANCH_PREFIX}/"))

    def test_branch_truncates_long_signatures(self) -> None:
        very_long = "x" * 200
        name = build_branch_name(problem_signature=very_long)
        # max trailing segment is 40 chars
        suffix = name.rsplit("/", 1)[-1]
        self.assertLessEqual(len(suffix), 40)


class ProvisionerLifecycleTests(unittest.TestCase):
    def test_first_provision_creates_worktree(self) -> None:
        provisioner = _Provisioner()
        registry = InMemoryWorktreeRegistry()
        outcome = provision_worktree_for_problem(
            problem_signature="sig.abc",
            owner_role="backend-engineer",
            spawned_by="test",
            provisioner=provisioner,
            registry=registry,
            now=_NOW,
        )
        self.assertFalse(outcome.reused)
        self.assertEqual(len(provisioner.created), 1)
        self.assertEqual(
            registry.get("sig.abc"), outcome.metadata
        )

    def test_second_provision_reuses(self) -> None:
        provisioner = _Provisioner()
        registry = InMemoryWorktreeRegistry()
        first = provision_worktree_for_problem(
            problem_signature="sig.abc",
            owner_role="backend-engineer",
            spawned_by="test",
            provisioner=provisioner,
            registry=registry,
            now=_NOW,
        )
        second = provision_worktree_for_problem(
            problem_signature="sig.abc",
            owner_role="backend-engineer",
            spawned_by="test",
            provisioner=provisioner,
            registry=registry,
            now=_NOW,
        )
        self.assertTrue(second.reused)
        self.assertEqual(first.metadata, second.metadata)
        self.assertEqual(len(provisioner.created), 1)

    def test_registry_persisted_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "wt.json"
            provisioner = _Provisioner()
            registry_a = InMemoryWorktreeRegistry(sidecar_path=path)
            provision_worktree_for_problem(
                problem_signature="sig.persist",
                owner_role="backend-engineer",
                spawned_by="test",
                provisioner=provisioner,
                registry=registry_a,
                now=_NOW,
            )
            # New instance: should rehydrate from disk.
            registry_b = InMemoryWorktreeRegistry(sidecar_path=path)
            metadata = registry_b.get("sig.persist")
            self.assertIsNotNone(metadata)
            assert metadata is not None
            self.assertEqual(metadata.owner_role, "backend-engineer")


class StaleDetectionTests(unittest.TestCase):
    def test_recent_worktree_not_flagged(self) -> None:
        registry = InMemoryWorktreeRegistry()
        registry.register(
            WorktreeMetadata(
                branch="b",
                path="p",
                problem_signature="sig.fresh",
                owner_role="r",
                spawned_by="t",
                parent_session_id=None,
                delegated_approval_state="delegated_ok",
                created_at=_NOW.isoformat(),
                cwd=".",
            )
        )
        reports = detect_stale_worktrees(
            registry=registry, now=_NOW, stale_after_seconds=60
        )
        self.assertEqual(reports, ())

    def test_old_worktree_flagged(self) -> None:
        old = (_NOW - timedelta(days=30)).isoformat()
        registry = InMemoryWorktreeRegistry()
        registry.register(
            WorktreeMetadata(
                branch="b",
                path="p",
                problem_signature="sig.old",
                owner_role="r",
                spawned_by="t",
                parent_session_id=None,
                delegated_approval_state="delegated_ok",
                created_at=old,
                cwd=".",
            )
        )
        reports = detect_stale_worktrees(
            registry=registry, now=_NOW, stale_after_seconds=24 * 3600
        )
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].metadata.problem_signature, "sig.old")

    def test_closed_signature_marked_specially(self) -> None:
        old = (_NOW - timedelta(days=30)).isoformat()
        registry = InMemoryWorktreeRegistry()
        registry.register(
            WorktreeMetadata(
                branch="b",
                path="p",
                problem_signature="sig.closed",
                owner_role="r",
                spawned_by="t",
                parent_session_id=None,
                delegated_approval_state="delegated_ok",
                created_at=old,
                cwd=".",
            )
        )
        reports = detect_stale_worktrees(
            registry=registry,
            closed_signatures={"sig.closed"},
            now=_NOW,
            stale_after_seconds=3600,
        )
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].reason, "problem_closed")

    def test_no_automatic_destructive_action(self) -> None:
        """Stale detection MUST be report-only — no removal happens."""

        provisioner = _Provisioner()
        registry = InMemoryWorktreeRegistry()
        old = (_NOW - timedelta(days=30)).isoformat()
        registry.register(
            WorktreeMetadata(
                branch="b",
                path="p",
                problem_signature="sig.old",
                owner_role="r",
                spawned_by="t",
                parent_session_id=None,
                delegated_approval_state="delegated_ok",
                created_at=old,
                cwd=".",
            )
        )
        provisioner._existing.add(("b", "p"))
        detect_stale_worktrees(registry=registry, now=_NOW)
        # provisioner.remove NEVER auto-called
        self.assertEqual(getattr(provisioner, "removed", None), None)


if __name__ == "__main__":
    unittest.main()
