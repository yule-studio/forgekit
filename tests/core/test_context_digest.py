"""Context digest render (B) — pointer+summary instead of full bodies."""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_core.context_loader import (
    load_agent_context,
    render_context,
    render_context_digest,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


class DigestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_agent_context(
            repo_root=_REPO_ROOT, agent_id="engineering-agent", role_id="security-engineer"
        )

    def test_digest_smaller_than_full(self) -> None:
        full = render_context(self.loaded)
        digest = render_context_digest(self.loaded)
        self.assertLess(len(digest), len(full))
        # meaningfully smaller — pointers, not bodies
        self.assertLess(len(digest), len(full) // 2)

    def test_digest_lists_every_doc_as_pointer(self) -> None:
        digest = render_context_digest(self.loaded)
        self.assertIn("Context digest", digest)
        self.assertIn("[entrypoint] AGENTS.md", digest)
        self.assertIn("[role_instructions]", digest)
        # documents are listed; count of pointer lines >= doc count
        pointer_lines = [l for l in digest.splitlines() if l.startswith("- [")]
        self.assertGreaterEqual(len(pointer_lines), len(self.loaded.documents))


if __name__ == "__main__":
    unittest.main()
