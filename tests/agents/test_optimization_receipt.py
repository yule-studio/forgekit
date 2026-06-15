"""Execution receipt optimization block + capability inference (Phase C/D)."""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_core.context_loader import load_agent_context
from yule_engineering.agents.harness import load_grant_table
from yule_engineering.agents.harness.hot_path import dispatch_receipt
from yule_engineering.agents.job_queue.standalone_runners import _infer_capability_class
from yule_engineering.agents.runners.role_runner import RoleRunnerInput, RoleRunnerOutput

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _loaded(role="qa-engineer"):
    return load_agent_context(repo_root=_REPO_ROOT, agent_id="engineering-agent", role_id=role)


def _input(role, capability=None):
    md = {"capability_class": capability} if capability else {}
    return RoleRunnerInput(role=role, session_id="s", prompt="p", metadata=md)


class InferenceTests(unittest.TestCase):
    def test_security_engineer_to_security_gate(self) -> None:
        self.assertEqual(_infer_capability_class(role="security-engineer", kind="open", task_type=None), "security_gate")

    def test_qa_test_to_verification(self) -> None:
        self.assertEqual(_infer_capability_class(role="qa-engineer", kind="open", task_type="qa-test"), "verification")

    def test_unclear_is_none(self) -> None:
        self.assertIsNone(_infer_capability_class(role="backend-engineer", kind="open", task_type="backend-feature"))
        self.assertIsNone(_infer_capability_class(role="qa-engineer", kind="open", task_type="research"))


class OptimizationReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load_grant_table()

    def test_rule_first_bypass_recorded(self) -> None:
        out = RoleRunnerOutput(provider="deterministic", status="fallback", text="t")
        r = dispatch_receipt(_loaded(), self.table, _input("qa-engineer", "verification"), out)
        o = r.to_dict()["optimization"]
        self.assertEqual(o["resolution_mode"], "rule_first")
        self.assertFalse(o["llm_used"])
        self.assertTrue(o["bypassed_live_llm"])
        self.assertEqual(o["bypass_reason"], "rule_first:verification")

    def test_llm_required_uses_llm(self) -> None:
        out = RoleRunnerOutput(provider="claude", status="ok", text="t")
        r = dispatch_receipt(_loaded("ai-engineer"), self.table, _input("ai-engineer", "research"), out)
        o = r.to_dict()["optimization"]
        self.assertEqual(o["resolution_mode"], "llm_required")
        self.assertTrue(o["llm_used"])
        self.assertFalse(o["bypassed_live_llm"])
        self.assertIsNone(o["bypass_reason"])

    def test_no_capability_defaults_required_no_bypass(self) -> None:
        out = RoleRunnerOutput(provider="claude", status="ok", text="t")
        r = dispatch_receipt(_loaded(), self.table, _input("qa-engineer"), out)
        o = r.to_dict()["optimization"]
        self.assertEqual(o["resolution_mode"], "llm_required")
        self.assertTrue(o["llm_used"])

    def test_render_includes_minimization_section(self) -> None:
        out = RoleRunnerOutput(provider="deterministic", status="fallback", text="t")
        r = dispatch_receipt(_loaded(), self.table, _input("qa-engineer", "verification"), out)
        text = r.render()
        self.assertIn("## LLM minimization", text)
        self.assertIn("bypassed_live_llm=True", text)


if __name__ == "__main__":
    unittest.main()
