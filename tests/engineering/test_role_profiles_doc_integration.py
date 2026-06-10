"""Phase 7 — role-profiles.md ↔ manifest.json + memory pipeline integration.

The role policy documentation must:

1. Live at the canonical path
   ``policies/runtime/agents/engineering-agent/role-profiles.md``.
2. Be listed in ``agents/engineering-agent/manifest.json``'s ``policies``
   array so ``context_loader`` picks it up when the department boots.
3. Be ingested by ``yule memory reindex`` as a SOURCE_POLICY document
   so retrieval / search hit it without bespoke wiring.
4. Mention every engineering role short-id at least once so a
   future role rename doesn't silently drift away from the doc.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = (
    REPO_ROOT
    / "policies"
    / "runtime"
    / "agents"
    / "engineering-agent"
    / "role-profiles.md"
)
AGENT_JSON = REPO_ROOT / "agents" / "engineering-agent" / "manifest.json"


_REQUIRED_ROLES = (
    "tech-lead",
    "ai-engineer",
    "backend-engineer",
    "frontend-engineer",
    "devops-engineer",
    "qa-engineer",
    "product-designer",
)


class RolePoliciesDocPresenceTests(unittest.TestCase):
    def test_doc_file_exists(self) -> None:
        self.assertTrue(
            DOC_PATH.exists(),
            f"role-profiles.md missing at {DOC_PATH}",
        )

    def test_doc_mentions_every_role_short_id(self) -> None:
        body = DOC_PATH.read_text(encoding="utf-8")
        for role in _REQUIRED_ROLES:
            self.assertIn(role, body, f"role-profiles.md missing role id: {role}")

    def test_doc_describes_participation_levels(self) -> None:
        body = DOC_PATH.read_text(encoding="utf-8")
        for level in ("required", "primary", "reviewer", "optional", "excluded"):
            self.assertIn(level, body, f"participation level missing: {level}")

    def test_doc_describes_fallback_policies(self) -> None:
        body = DOC_PATH.read_text(encoding="utf-8")
        for policy in (
            "empty_prompt",
            "vague_infra",
            "vague_ai_research",
            "vague_product",
            "vague_engineering",
            "legacy_quartet",
        ):
            self.assertIn(policy, body, f"fallback policy missing: {policy}")


class AgentJsonPolicyListTests(unittest.TestCase):
    def test_manifest_json_lists_role_profiles_doc(self) -> None:
        manifest = json.loads(AGENT_JSON.read_text(encoding="utf-8"))
        policies = manifest.get("policies") or []
        expected = "policies/runtime/agents/engineering-agent/role-profiles.md"
        self.assertIn(
            expected,
            policies,
            "manifest.json policies array must include role-profiles.md so "
            "context_loader picks it up during department bootstrap.",
        )


class MemoryReindexPicksUpDocTests(unittest.TestCase):
    """``yule memory reindex`` indexes ``policies/`` recursively as
    SOURCE_POLICY. Verify role-profiles.md actually lands in the index
    so retrieval / search hit it without extra wiring."""

    def test_role_profiles_doc_indexed_as_policy(self) -> None:
        from yule_engineering.memory import (
            open_memory_index,
            reindex_paths,
        )
        from yule_engineering.memory.models import SOURCE_POLICY

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.sqlite3"
            with open_memory_index(repo_root=REPO_ROOT, db_path=db_path) as index:
                count = reindex_paths(
                    paths=[REPO_ROOT / "policies"],
                    source_kind=SOURCE_POLICY,
                    index=index,
                    base_dir=REPO_ROOT,
                )
                self.assertGreaterEqual(count, 1)
                # Search the live index for a token that only the
                # role-profiles doc carries — assert at least one hit
                # whose path points at our file.
                cur = index.connection.cursor()
                cur.execute(
                    "SELECT path FROM documents WHERE source_kind = ?",
                    (SOURCE_POLICY,),
                )
                paths = {row[0] for row in cur.fetchall() if row and row[0]}
                self.assertTrue(
                    any(
                        "engineering-agent/role-profiles.md" in path
                        for path in paths
                    ),
                    f"role-profiles.md not indexed; got {sorted(paths)[:5]}…",
                )


if __name__ == "__main__":
    unittest.main()
