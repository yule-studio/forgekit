"""decision.context_pack — Phase 3 of #73."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.decision import (
    ContextPack,
    build_context_pack,
)


class _Notes:
    def __init__(self, items=()):
        self.items = list(items)
        self.calls = 0

    def find_related_notes(self, *, prompt, session_id, limit):
        self.calls += 1
        return self.items[:limit]


class _Threads:
    def __init__(self, items=()):
        self.items = list(items)

    def find_recent_threads(self, *, prompt, session_id, limit):
        return self.items[:limit]


class _Github:
    def __init__(self, issues=(), prs=()):
        self.issues = list(issues)
        self.prs = list(prs)

    def find_related_issues(self, *, prompt, limit):
        return self.issues[:limit]

    def find_related_prs(self, *, prompt, limit):
        return self.prs[:limit]


class _Code:
    def __init__(self, items=()):
        self.items = list(items)

    def find_code_hints(self, *, prompt, role, limit):
        return self.items[:limit]


class BuildContextPackTests(unittest.TestCase):
    def test_no_providers_returns_empty_pack(self) -> None:
        pack = build_context_pack(prompt="hello")
        self.assertIsInstance(pack, ContextPack)
        self.assertTrue(pack.is_empty)
        self.assertTrue(pack.id.startswith("ctx-"))

    def test_all_providers_populate_pack(self) -> None:
        notes = _Notes(items=["a.md", "b.md", "c.md"])
        threads = _Threads(items=["thread-1", "thread-2"])
        github = _Github(issues=[1, 2, 3], prs=[10, 11])
        code = _Code(items=["src/x.py"])
        pack = build_context_pack(
            prompt="bug fix",
            session_id="sess-1",
            role="backend-engineer",
            note_provider=notes,
            thread_provider=threads,
            github_reference_provider=github,
            code_hint_provider=code,
            note_limit=2,
            thread_limit=1,
            issue_limit=2,
            pr_limit=1,
            code_limit=1,
        )
        self.assertEqual(pack.related_notes, ("a.md", "b.md"))
        self.assertEqual(pack.recent_threads, ("thread-1",))
        self.assertEqual(pack.related_issues, (1, 2))
        self.assertEqual(pack.related_prs, (10,))
        self.assertEqual(pack.code_hints, ("src/x.py",))
        self.assertFalse(pack.is_empty)

    def test_safe_int_drops_garbage(self) -> None:
        pack = build_context_pack(
            prompt="x",
            github_reference_provider=_Github(issues=["1", "two", 3, None], prs=[]),
        )
        self.assertEqual(pack.related_issues, (1, 3))

    def test_each_pack_has_unique_id(self) -> None:
        a = build_context_pack(prompt="x")
        b = build_context_pack(prompt="x")
        self.assertNotEqual(a.id, b.id)

    def test_payload_round_trippable(self) -> None:
        pack = build_context_pack(
            prompt="x",
            note_provider=_Notes(items=["y.md"]),
            metadata={"source": "test"},
        )
        payload = pack.to_payload()
        self.assertEqual(payload["related_notes"], ["y.md"])
        self.assertEqual(payload["metadata"], {"source": "test"})


if __name__ == "__main__":
    unittest.main()
