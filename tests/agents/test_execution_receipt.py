"""Execution receipt — execution proof (issue #185 follow-up, item D)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_core.context_loader import load_agent_context
from yule_engineering.agents.harness import (
    Checkpoint,
    build_execution_receipt,
    load_grant_table,
    run_cleanup,
    run_compaction_to_vault,
)
from yule_engineering.agents.harness.context_compaction import CompactionTurn

_REPO_ROOT = Path(__file__).resolve().parents[2]


class ExecutionReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load_grant_table()

    def _loaded(self, role=None):
        return load_agent_context(
            repo_root=_REPO_ROOT, agent_id="engineering-agent", role_id=role
        )

    def test_receipt_has_required_fields(self) -> None:
        receipt = build_execution_receipt(
            self._loaded(),
            self.table,
            selected_runner="claude",
            requested_capabilities=["/compact", "/model", "compact-to-vault", "no-such-skill"],
        )
        self.assertEqual(receipt.selected_agent, "engineering-agent")
        self.assertIsNone(receipt.selected_role)
        self.assertEqual(receipt.selected_runner, "claude")
        # loaded docs include entrypoint + root + agent
        labels = {label for (label, _path) in receipt.loaded_docs}
        self.assertIn("entrypoint", labels)
        self.assertIn("root_instructions", labels)
        self.assertIn("agent_instructions", labels)
        self.assertTrue(receipt.loaded_policies)
        self.assertIn("compact-to-vault", receipt.granted_skills)

    def test_blocked_or_missing_surfaces_non_allow(self) -> None:
        receipt = build_execution_receipt(
            self._loaded(),
            self.table,
            requested_capabilities=["/compact", "/model", "no-such-skill"],
        )
        caps = {d.capability: d.verdict.value for d in receipt.blocked_or_missing}
        self.assertNotIn("/compact", caps)  # granted → not surfaced
        self.assertEqual(caps.get("/model"), "block")
        self.assertEqual(caps.get("no-such-skill"), "block")

    def test_role_actor_resolves(self) -> None:
        receipt = build_execution_receipt(
            self._loaded(role="security-engineer"),
            self.table,
            requested_capabilities=["/security-review"],
        )
        self.assertEqual(receipt.selected_role, "security-engineer")
        self.assertEqual(receipt.blocked_or_missing, ())  # /security-review granted via override

    def test_statuses_default_not_run(self) -> None:
        receipt = build_execution_receipt(self._loaded(), self.table)
        self.assertEqual(receipt.compaction_status, "not_run")
        self.assertEqual(receipt.cleanup_status, "not_run")
        self.assertEqual(receipt.security_status, "not_evaluated")

    def test_security_decision_surfaces_in_receipt(self) -> None:
        from yule_engineering.agents.harness import assess_security_review

        decision = assess_security_review({"paths": ["src/auth/login.py"], "summary": "jwt"})
        receipt = build_execution_receipt(self._loaded(), self.table, security=decision)
        self.assertEqual(receipt.security_status, "required")
        d = receipt.to_dict()
        self.assertEqual(d["security_status"], "required")
        self.assertIn("auth", d["security"]["triggers"])
        self.assertIn("## Security review: required", receipt.render())

    def test_render_and_dict(self) -> None:
        receipt = build_execution_receipt(
            self._loaded(), self.table, requested_capabilities=["/model"]
        )
        text = receipt.render()
        self.assertIn("# Execution Receipt", text)
        self.assertIn("## Loaded docs", text)
        self.assertIn("## Blocked or missing skills", text)
        self.assertIn("## Compaction status", text)
        self.assertIn("## Cleanup status", text)
        d = receipt.to_dict()
        self.assertEqual(d["selected_agent"], "engineering-agent")
        self.assertIn("blocked_or_missing", d)

    def test_receipt_binds_compaction_and_cleanup_statuses(self) -> None:
        turns = [
            CompactionTurn(0, "user", "prompt", "원문 요청"),
            CompactionTurn(1, "tech-lead", "synthesis", "합의"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            _note, comp = run_compaction_to_vault(
                turns,
                session_id="sess-d",
                vault_root=Path(tmp) / "vault",
                project="yule-studio-agent",
                checkpoint=Checkpoint.SESSION_END,
            )
            clean = run_cleanup(Path(tmp) / "vault")
            receipt = build_execution_receipt(
                self._loaded(), self.table, compaction=comp, cleanup=clean
            )
            self.assertEqual(receipt.compaction_status, "written")
            self.assertEqual(receipt.cleanup_status, "dry_run")
            self.assertIsNotNone(receipt.compaction)
            self.assertIsNotNone(receipt.cleanup)


if __name__ == "__main__":
    unittest.main()
