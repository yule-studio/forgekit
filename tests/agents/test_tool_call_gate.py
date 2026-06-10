"""Unit tests for the F12 / #103 pre-tool-call gate.

The matrix is autonomy × RiskClass → action. The matrix is the
contract, so we sweep every cell. We also assert:

  * env OFF makes the gate transparent + emits a warning,
  * BLOCK verdicts register the canonical signature on an
    injected mistake ledger,
  * the classifier callback is honoured (so callers can plug a
    deterministic fake during tests / re-orchestration).
"""

from __future__ import annotations

import unittest
import warnings

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_learning.mistake_ledger import (
    BlockerLevel,
    MistakeLedger,
)
from yule_engineering.agents.safety.risk_classifier import (
    RiskClass,
    RiskSignal,
    ToolCallContext,
)
from yule_engineering.agents.safety.tool_call_gate import (
    ENV_TOOL_GATE_DEFAULT_AUTONOMY,
    ENV_TOOL_GATE_ENABLED,
    GateAction,
    ToolGateVerdict,
    gate_tool_call,
)


def _ctx(autonomy: str, tool_id: str = "read_file", **kwargs) -> ToolCallContext:
    return ToolCallContext(
        tool_id=tool_id,
        target=kwargs.get("target", ""),
        args=tuple(kwargs.get("args", ())),
        role=kwargs.get("role", "engineering-agent/devops-engineer"),
        session_id=kwargs.get("session_id", "session-test"),
        autonomy_level=autonomy,
    )


def _fake_classifier(risk: RiskClass):
    """Return a classifier callable that always emits *risk*."""

    def _cls(ctx):
        return risk, (RiskSignal(name="fake", weight=risk, evidence="fake"),)

    return _cls


_ENV_ON = {ENV_TOOL_GATE_ENABLED: "true"}


class L0MatrixTests(unittest.TestCase):
    """L0_manual_only — every non-CRITICAL is REQUIRE_APPROVAL."""

    def test_l0_safe_requires_approval(self) -> None:
        verdict = gate_tool_call(
            _ctx("L0_manual_only"),
            classifier=_fake_classifier(RiskClass.SAFE),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)

    def test_l0_low_requires_approval(self) -> None:
        verdict = gate_tool_call(
            _ctx("L0_manual_only"),
            classifier=_fake_classifier(RiskClass.LOW),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)

    def test_l0_medium_requires_approval(self) -> None:
        verdict = gate_tool_call(
            _ctx("L0_manual_only"),
            classifier=_fake_classifier(RiskClass.MEDIUM),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)

    def test_l0_high_requires_approval(self) -> None:
        verdict = gate_tool_call(
            _ctx("L0_manual_only"),
            classifier=_fake_classifier(RiskClass.HIGH),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)

    def test_l0_critical_is_blocked(self) -> None:
        verdict = gate_tool_call(
            _ctx("L0_manual_only"),
            classifier=_fake_classifier(RiskClass.CRITICAL),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.BLOCK)


class L1MatrixTests(unittest.TestCase):
    """L1_advisory — SAFE/LOW ALLOW, others gate."""

    def test_l1_safe_allows(self) -> None:
        verdict = gate_tool_call(
            _ctx("L1_advisory"),
            classifier=_fake_classifier(RiskClass.SAFE),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.ALLOW)

    def test_l1_low_allows(self) -> None:
        verdict = gate_tool_call(
            _ctx("L1_advisory"),
            classifier=_fake_classifier(RiskClass.LOW),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.ALLOW)

    def test_l1_medium_requires_approval(self) -> None:
        verdict = gate_tool_call(
            _ctx("L1_advisory"),
            classifier=_fake_classifier(RiskClass.MEDIUM),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)

    def test_l1_high_requires_approval(self) -> None:
        verdict = gate_tool_call(
            _ctx("L1_advisory"),
            classifier=_fake_classifier(RiskClass.HIGH),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)

    def test_l1_critical_is_blocked(self) -> None:
        verdict = gate_tool_call(
            _ctx("L1_advisory"),
            classifier=_fake_classifier(RiskClass.CRITICAL),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.BLOCK)


class L2MatrixTests(unittest.TestCase):
    """L2_autonomous_record — SAFE/LOW/MEDIUM ALLOW, HIGH approve, CRIT block."""

    def test_l2_safe_allows(self) -> None:
        verdict = gate_tool_call(
            _ctx("L2_autonomous_record"),
            classifier=_fake_classifier(RiskClass.SAFE),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.ALLOW)

    def test_l2_medium_allows(self) -> None:
        verdict = gate_tool_call(
            _ctx("L2_autonomous_record"),
            classifier=_fake_classifier(RiskClass.MEDIUM),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.ALLOW)

    def test_l2_high_requires_approval(self) -> None:
        verdict = gate_tool_call(
            _ctx("L2_autonomous_record"),
            classifier=_fake_classifier(RiskClass.HIGH),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)

    def test_l2_critical_is_blocked(self) -> None:
        verdict = gate_tool_call(
            _ctx("L2_autonomous_record"),
            classifier=_fake_classifier(RiskClass.CRITICAL),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.BLOCK)


class L3MatrixTests(unittest.TestCase):
    """L3_human_approval — SAFE ALLOW, everything else requires approval."""

    def test_l3_safe_allows(self) -> None:
        verdict = gate_tool_call(
            _ctx("L3_human_approval"),
            classifier=_fake_classifier(RiskClass.SAFE),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.ALLOW)

    def test_l3_low_requires_approval(self) -> None:
        verdict = gate_tool_call(
            _ctx("L3_human_approval"),
            classifier=_fake_classifier(RiskClass.LOW),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)

    def test_l3_high_requires_approval(self) -> None:
        verdict = gate_tool_call(
            _ctx("L3_human_approval"),
            classifier=_fake_classifier(RiskClass.HIGH),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)

    def test_l3_critical_is_blocked(self) -> None:
        verdict = gate_tool_call(
            _ctx("L3_human_approval"),
            classifier=_fake_classifier(RiskClass.CRITICAL),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.BLOCK)


class L4MatrixTests(unittest.TestCase):
    """L4_full_autonomous — non-CRITICAL ALLOW, CRITICAL still BLOCK."""

    def test_l4_high_allows(self) -> None:
        verdict = gate_tool_call(
            _ctx("L4_full_autonomous"),
            classifier=_fake_classifier(RiskClass.HIGH),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.ALLOW)

    def test_l4_medium_allows(self) -> None:
        verdict = gate_tool_call(
            _ctx("L4_full_autonomous"),
            classifier=_fake_classifier(RiskClass.MEDIUM),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.ALLOW)

    def test_l4_critical_is_blocked(self) -> None:
        verdict = gate_tool_call(
            _ctx("L4_full_autonomous"),
            classifier=_fake_classifier(RiskClass.CRITICAL),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.BLOCK)


class EnvSwitchTests(unittest.TestCase):
    """env OFF makes the gate transparent + warns."""

    def test_env_off_returns_safe_allow(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            verdict = gate_tool_call(
                _ctx("L0_manual_only"),
                classifier=_fake_classifier(RiskClass.CRITICAL),
                env={ENV_TOOL_GATE_ENABLED: "false"},
            )
        self.assertEqual(verdict.action, GateAction.ALLOW)
        self.assertEqual(verdict.risk_class, RiskClass.SAFE)
        self.assertIn("gate disabled", verdict.reason)
        self.assertTrue(
            any("disabled" in str(w.message) for w in caught),
            "env OFF must emit a warning",
        )

    def test_env_default_is_on(self) -> None:
        verdict = gate_tool_call(
            _ctx("L2_autonomous_record"),
            classifier=_fake_classifier(RiskClass.HIGH),
            env={},  # nothing configured
        )
        # ON default → HIGH at L2 → REQUIRE_APPROVAL
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)

    def test_env_default_autonomy_used_when_ctx_missing(self) -> None:
        verdict = gate_tool_call(
            _ctx(""),  # no autonomy on ctx
            classifier=_fake_classifier(RiskClass.MEDIUM),
            env={
                ENV_TOOL_GATE_ENABLED: "true",
                ENV_TOOL_GATE_DEFAULT_AUTONOMY: "L1_advisory",
            },
        )
        # L1 + MEDIUM → REQUIRE_APPROVAL
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)


class MistakeLedgerIntegrationTests(unittest.TestCase):
    """BLOCK verdicts register the canonical signature on a ledger."""

    def setUp(self) -> None:
        self.ledger = MistakeLedger(database_path=":memory:")

    def tearDown(self) -> None:
        self.ledger.close()

    def test_block_records_signature(self) -> None:
        verdict = gate_tool_call(
            _ctx("L2_autonomous_record"),
            classifier=_fake_classifier(RiskClass.CRITICAL),
            ledger=self.ledger,
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.BLOCK)
        self.assertEqual(
            verdict.signatures,
            ("tool_gate.critical.blocked",),
        )
        records = self.ledger.all_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.signature, "tool_gate.critical.blocked")
        self.assertEqual(record.blocker_level, BlockerLevel.BLOCK)

    def test_repeated_block_bumps_occurrences(self) -> None:
        for _ in range(3):
            gate_tool_call(
                _ctx("L2_autonomous_record"),
                classifier=_fake_classifier(RiskClass.CRITICAL),
                ledger=self.ledger,
                env=_ENV_ON,
            )
        records = self.ledger.all_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].occurrences, 3)

    def test_allow_does_not_touch_ledger(self) -> None:
        gate_tool_call(
            _ctx("L4_full_autonomous"),
            classifier=_fake_classifier(RiskClass.HIGH),
            ledger=self.ledger,
            env=_ENV_ON,
        )
        self.assertEqual(self.ledger.all_records(), ())

    def test_require_approval_does_not_touch_ledger(self) -> None:
        gate_tool_call(
            _ctx("L1_advisory"),
            classifier=_fake_classifier(RiskClass.MEDIUM),
            ledger=self.ledger,
            env=_ENV_ON,
        )
        self.assertEqual(self.ledger.all_records(), ())

    def test_block_without_ledger_still_returns_signature(self) -> None:
        verdict = gate_tool_call(
            _ctx("L2_autonomous_record"),
            classifier=_fake_classifier(RiskClass.CRITICAL),
            ledger=None,
            env=_ENV_ON,
        )
        # Verdict still carries the signature for downstream audit.
        self.assertEqual(
            verdict.signatures,
            ("tool_gate.critical.blocked",),
        )


class IntegrationSmokeTests(unittest.TestCase):
    """End-to-end smoke against the real classifier."""

    def test_real_classifier_git_push_main_is_blocked_at_l4(self) -> None:
        ctx = ToolCallContext(
            tool_id="git_push",
            target="main",
            args=("origin", "main"),
            role="engineering-agent/devops-engineer",
            session_id="s1",
            autonomy_level="L4_full_autonomous",
        )
        verdict = gate_tool_call(ctx, env=_ENV_ON)
        self.assertEqual(verdict.risk_class, RiskClass.CRITICAL)
        self.assertEqual(verdict.action, GateAction.BLOCK)

    def test_real_classifier_read_file_is_allowed_at_l1(self) -> None:
        ctx = ToolCallContext(
            tool_id="read_file",
            target="README.md",
            args=(),
            role="engineering-agent/devops-engineer",
            session_id="s1",
            autonomy_level="L1_advisory",
        )
        verdict = gate_tool_call(ctx, env=_ENV_ON)
        self.assertEqual(verdict.risk_class, RiskClass.SAFE)
        self.assertEqual(verdict.action, GateAction.ALLOW)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
