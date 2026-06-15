"""Hot-path seam — capability gate + dispatch receipt (issue #185 follow-up A/C)."""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_core.context_loader import load_agent_context
from yule_engineering.agents.harness import load_grant_table
from yule_engineering.agents.harness.hot_path import (
    actor_id_for,
    build_capability_block_gate,
    dispatch_receipt,
    evaluate_input_capabilities,
    requested_capabilities,
)
from yule_engineering.agents.runners.role_runner import (
    STATUS_BLOCKED,
    STATUS_OK,
    RoleRunnerInput,
    RoleRunnerOutput,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _input(role="security-engineer", caps=None, change=None):
    md = {}
    if caps is not None:
        md["capabilities"] = caps
    if change is not None:
        md["change"] = change
    return RoleRunnerInput(role=role, session_id="s1", prompt="p", metadata=md)


class ActorAndCapabilityTests(unittest.TestCase):
    def test_actor_id_from_role(self) -> None:
        self.assertEqual(actor_id_for(_input(role="qa-engineer")), "engineering-agent/qa-engineer")

    def test_actor_id_explicit_override(self) -> None:
        inp = RoleRunnerInput(role="x", session_id="s", prompt="p", metadata={"actor_id": "legal-agent"})
        self.assertEqual(actor_id_for(inp), "legal-agent")

    def test_requested_capabilities(self) -> None:
        self.assertEqual(
            requested_capabilities(_input(caps=["/compact", "  ", "compact-to-vault"])),
            ("/compact", "compact-to-vault"),
        )


class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load_grant_table()

    def test_blocks_non_grantable_command(self) -> None:
        gate = build_capability_block_gate(self.table)
        out = gate(None, _input(role="qa-engineer", caps=["/model"]))
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.status, STATUS_BLOCKED)
        self.assertIn("/model", out.metrics["blocked_capabilities"])

    def test_allows_granted(self) -> None:
        gate = build_capability_block_gate(self.table)
        self.assertIsNone(gate(None, _input(role="security-engineer", caps=["/security-review"])))

    def test_advisory_does_not_block(self) -> None:
        gate = build_capability_block_gate(self.table)
        # marketing-agent not granted /diff but it is grantable → advisory, not block
        inp = RoleRunnerInput(
            role="x", session_id="s", prompt="p",
            metadata={"actor_id": "marketing-agent", "capabilities": ["/diff"]},
        )
        self.assertIsNone(gate(None, inp))

    def test_no_capabilities_proceeds(self) -> None:
        gate = build_capability_block_gate(self.table)
        self.assertIsNone(gate(None, _input(role="qa-engineer")))


class DispatchReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load_grant_table()
        cls.loaded = load_agent_context(
            repo_root=_REPO_ROOT, agent_id="engineering-agent", role_id="security-engineer"
        )

    def test_receipt_captures_runner_and_role(self) -> None:
        out = RoleRunnerOutput(provider="claude", status=STATUS_OK, text="t")
        receipt = dispatch_receipt(self.loaded, self.table, _input(), out)
        self.assertEqual(receipt.selected_runner, "claude")
        self.assertEqual(receipt.selected_role, "security-engineer")
        labels = {l for (l, _p) in receipt.loaded_docs}
        self.assertIn("entrypoint", labels)
        self.assertIn("role_instructions", labels)

    def test_receipt_security_required_from_change(self) -> None:
        out = RoleRunnerOutput(provider="claude", status=STATUS_OK, text="t")
        inp = _input(change={"paths": ["src/auth/login.py"], "summary": "JWT session"})
        receipt = dispatch_receipt(self.loaded, self.table, inp, out)
        self.assertEqual(receipt.security_status, "required")
        self.assertIn("auth", receipt.security.triggers)

    def test_receipt_blocked_capability_surfaces(self) -> None:
        out = RoleRunnerOutput(provider="grant-gate", status=STATUS_BLOCKED, text="")
        inp = _input(role="qa-engineer", caps=["/model"])
        receipt = dispatch_receipt(self.loaded, self.table, inp, out, agent_id="engineering-agent")
        caps = {d.capability for d in receipt.blocked_or_missing}
        self.assertIn("/model", caps)


if __name__ == "__main__":
    unittest.main()
