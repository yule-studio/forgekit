"""Layered context-loading order + role tier (issue #185 follow-up, item B).

Locks the canonical load order AGENTS.md → root CLAUDE.md → agent
instruction_entry → (role) role instruction_entry → policies, and the
role-selected-vs-not split with missing-file warnings.
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_core.context_loader import (
    LABEL_AGENT,
    LABEL_ENTRYPOINT,
    LABEL_POLICY,
    LABEL_ROLE,
    LABEL_ROOT,
    load_agent_context,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


class LoadOrderTests(unittest.TestCase):
    def test_entrypoint_root_agent_order(self) -> None:
        loaded = load_agent_context(repo_root=_REPO_ROOT, agent_id="engineering-agent")
        labels = [d.label for d in loaded.documents]
        # First three docs are entrypoint → root → agent, in that order.
        self.assertEqual(labels[0], LABEL_ENTRYPOINT)
        self.assertEqual(labels[1], LABEL_ROOT)
        self.assertEqual(labels[2], LABEL_AGENT)
        # AGENTS.md is the very first document.
        self.assertEqual(loaded.documents[0].path.name, "AGENTS.md")
        self.assertEqual(loaded.documents[1].path.name, "CLAUDE.md")

    def test_policies_loaded_after_agent(self) -> None:
        loaded = load_agent_context(repo_root=_REPO_ROOT, agent_id="engineering-agent")
        policy_docs = loaded.documents_by_label(LABEL_POLICY)
        self.assertTrue(policy_docs, "agent policies should load")
        # every policy doc comes after the agent instruction doc
        agent_idx = [d.label for d in loaded.documents].index(LABEL_AGENT)
        first_policy_idx = [d.label for d in loaded.documents].index(LABEL_POLICY)
        self.assertGreater(first_policy_idx, agent_idx)


class RoleTierTests(unittest.TestCase):
    def test_no_role_selected_has_no_role_doc_or_warning(self) -> None:
        loaded = load_agent_context(repo_root=_REPO_ROOT, agent_id="engineering-agent")
        self.assertIsNone(loaded.role_id)
        self.assertFalse(loaded.has_role_instructions())
        self.assertFalse(
            any("role" in w.lower() for w in loaded.warnings),
            "no role warning when role not selected",
        )

    def test_role_selected_loads_role_instruction_after_agent(self) -> None:
        loaded = load_agent_context(
            repo_root=_REPO_ROOT,
            agent_id="engineering-agent",
            role_id="security-engineer",
        )
        self.assertEqual(loaded.role_id, "security-engineer")
        self.assertTrue(loaded.has_role_instructions())
        labels = [d.label for d in loaded.documents]
        self.assertIn(LABEL_ROLE, labels)
        # role doc sits after agent doc and before the first policy
        self.assertGreater(labels.index(LABEL_ROLE), labels.index(LABEL_AGENT))
        self.assertLess(labels.index(LABEL_ROLE), labels.index(LABEL_POLICY))
        self.assertIsNotNone(loaded.role_manifest)

    def test_missing_role_warns_and_continues(self) -> None:
        loaded = load_agent_context(
            repo_root=_REPO_ROOT,
            agent_id="engineering-agent",
            role_id="nonexistent-role",
        )
        # philosophy preserved: warn + continue, do not raise, agent doc still present
        self.assertFalse(loaded.has_role_instructions())
        self.assertTrue(
            any("Missing role manifest" in w for w in loaded.warnings),
            f"expected missing-role warning, got {loaded.warnings}",
        )
        self.assertIn(LABEL_AGENT, [d.label for d in loaded.documents])


if __name__ == "__main__":
    unittest.main()
