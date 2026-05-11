"""Governance regression for the F12 / #103 tool-call gate.

Pins the hard rails into one suite so a single rename / matrix
edit cannot silently regress them:

  1. CRITICAL is BLOCK for *every* autonomy level — even
     L4_full_autonomous, even when the matrix row is edited.
  2. env OFF makes the circuit transparent but emits an explicit
     warning so an audit reader can see the safety surface is
     bypassed.
  3. PasteGuard (outbound secret guard) keeps its own contract —
     the two layers do not share state and a gate verdict cannot
     short-circuit a PasteGuard finding.
  4. Protected branch pushes never ALLOW — at any autonomy level,
     the real classifier escalates to CRITICAL and the gate
     blocks.
  5. BLOCK verdicts produce a stable signature that the mistake
     ledger can ingest (matches
     ``tool_gate.<risk_class>.blocked``).
  6. The gate's default autonomy fallback (``L2_autonomous_record``)
     plus default env (ON) is documented and exercised.
"""

from __future__ import annotations

import unittest
import warnings

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.learning.mistake_ledger import (
    BlockerLevel,
    MistakeLedger,
)
from yule_orchestrator.agents.safety.risk_classifier import (
    RiskClass,
    RiskSignal,
    ToolCallContext,
    classify_tool_call,
)
from yule_orchestrator.agents.safety.tool_call_gate import (
    ENV_TOOL_GATE_DEFAULT_AUTONOMY,
    ENV_TOOL_GATE_ENABLED,
    GateAction,
    ToolGateVerdict,
    gate_tool_call,
)
from yule_orchestrator.agents.security.paste_guard import (
    OutboundChannel,
    guard_outbound,
)


_ALL_AUTONOMY_LEVELS = (
    "L0_manual_only",
    "L1_advisory",
    "L2_autonomous_record",
    "L3_human_approval",
    "L4_full_autonomous",
)


def _critical_classifier(ctx):
    return RiskClass.CRITICAL, (
        RiskSignal(name="fake", weight=RiskClass.CRITICAL, evidence="fake"),
    )


def _make_ctx(autonomy: str, tool_id: str = "git_reset_hard", **kw) -> ToolCallContext:
    return ToolCallContext(
        tool_id=tool_id,
        target=kw.get("target", ""),
        args=tuple(kw.get("args", ())),
        role="engineering-agent/devops-engineer",
        session_id="session-gov",
        autonomy_level=autonomy,
    )


_ENV_ON = {ENV_TOOL_GATE_ENABLED: "true"}


class CriticalAlwaysBlocksTests(unittest.TestCase):
    """Hard rail #1 — CRITICAL is BLOCK at every autonomy level."""

    def test_critical_blocked_for_every_autonomy(self) -> None:
        for autonomy in _ALL_AUTONOMY_LEVELS:
            with self.subTest(autonomy=autonomy):
                verdict = gate_tool_call(
                    _make_ctx(autonomy),
                    classifier=_critical_classifier,
                    env=_ENV_ON,
                )
                self.assertEqual(verdict.action, GateAction.BLOCK)
                self.assertEqual(verdict.risk_class, RiskClass.CRITICAL)

    def test_critical_blocked_even_with_unknown_autonomy(self) -> None:
        verdict = gate_tool_call(
            _make_ctx("L99_yolo_mode"),
            classifier=_critical_classifier,
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.BLOCK)


class EnvOffWarningTests(unittest.TestCase):
    """Hard rail #2 — env OFF transparent but explicitly warned."""

    def test_env_off_emits_warning_and_allows(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            verdict = gate_tool_call(
                _make_ctx("L0_manual_only"),
                classifier=_critical_classifier,
                env={ENV_TOOL_GATE_ENABLED: "false"},
            )
        self.assertEqual(verdict.action, GateAction.ALLOW)
        self.assertEqual(verdict.risk_class, RiskClass.SAFE)
        self.assertIn("gate disabled", verdict.reason)
        self.assertTrue(
            any("disabled" in str(w.message) for w in caught),
            "env OFF must emit an explicit warning so a governance "
            "test can detect the bypass",
        )

    def test_env_off_with_zero_value_also_warns(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            gate_tool_call(
                _make_ctx("L4_full_autonomous"),
                classifier=_critical_classifier,
                env={ENV_TOOL_GATE_ENABLED: "0"},
            )
        self.assertTrue(any("disabled" in str(w.message) for w in caught))


class PasteGuardLayerSeparationTests(unittest.TestCase):
    """Hard rail #3 — gate and PasteGuard do not share state.

    They operate on different axes: the gate decides whether a
    tool fires, PasteGuard decides what bytes leave the process.
    A gate ALLOW does not authorise PasteGuard to leak a secret,
    and a PasteGuard miss does not weaken the gate.
    """

    def test_gate_allow_does_not_affect_paste_guard_finding(self) -> None:
        gate_verdict = gate_tool_call(
            _make_ctx("L4_full_autonomous", tool_id="read_file"),
            env=_ENV_ON,
        )
        self.assertEqual(gate_verdict.action, GateAction.ALLOW)

        # PasteGuard still detects a secret in an outbound payload.
        leak = "ghp_" + "A" * 40
        guard_verdict = guard_outbound(
            channel=OutboundChannel.GITHUB,
            payload=f"prefix {leak} suffix",
        )
        self.assertGreaterEqual(len(guard_verdict.findings), 1)
        self.assertNotIn(leak, guard_verdict.redacted)

    def test_gate_block_independent_of_paste_guard_inputs(self) -> None:
        # CRITICAL gate verdict is independent of any payload that
        # might also be flagged by PasteGuard.
        verdict = gate_tool_call(
            _make_ctx("L2_autonomous_record", tool_id="git_reset_hard"),
            env=_ENV_ON,
        )
        self.assertEqual(verdict.action, GateAction.BLOCK)


class ProtectedBranchNeverAllowedTests(unittest.TestCase):
    """Hard rail #4 — protected branch push never ALLOW."""

    def test_protected_branch_push_blocked_at_every_autonomy(self) -> None:
        for autonomy in _ALL_AUTONOMY_LEVELS:
            with self.subTest(autonomy=autonomy):
                ctx = ToolCallContext(
                    tool_id="git_push",
                    target="main",
                    args=("origin", "main"),
                    role="engineering-agent/devops-engineer",
                    session_id="gov",
                    autonomy_level=autonomy,
                )
                verdict = gate_tool_call(ctx, env=_ENV_ON)
                self.assertEqual(verdict.risk_class, RiskClass.CRITICAL)
                self.assertEqual(verdict.action, GateAction.BLOCK)

    def test_protected_branch_force_push_blocked(self) -> None:
        ctx = ToolCallContext(
            tool_id="force_push",
            target="develop",
            args=(),
            role="engineering-agent/devops-engineer",
            session_id="gov",
            autonomy_level="L4_full_autonomous",
        )
        verdict = gate_tool_call(ctx, env=_ENV_ON)
        self.assertEqual(verdict.action, GateAction.BLOCK)


class LedgerSignatureContractTests(unittest.TestCase):
    """Hard rail #5 — BLOCK signatures match the canonical shape."""

    def setUp(self) -> None:
        self.ledger = MistakeLedger(database_path=":memory:")

    def tearDown(self) -> None:
        self.ledger.close()

    def test_block_signature_canonical_shape(self) -> None:
        verdict = gate_tool_call(
            _make_ctx("L0_manual_only"),
            classifier=_critical_classifier,
            ledger=self.ledger,
            env=_ENV_ON,
        )
        self.assertEqual(
            verdict.signatures,
            ("tool_gate.critical.blocked",),
        )
        records = self.ledger.all_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.blocker_level, BlockerLevel.BLOCK)
        self.assertTrue(record.signature.startswith("tool_gate."))


class DefaultAutonomyDocumentedTests(unittest.TestCase):
    """Hard rail #6 — default autonomy fallback is documented + active."""

    def test_default_autonomy_is_l2_autonomous_record(self) -> None:
        # Empty ctx autonomy + empty env → L2_autonomous_record.
        # MEDIUM at L2 → ALLOW.
        verdict = gate_tool_call(
            _make_ctx("", tool_id="git_commit"),
            env={ENV_TOOL_GATE_ENABLED: "true"},
        )
        self.assertEqual(verdict.risk_class, RiskClass.MEDIUM)
        self.assertEqual(verdict.action, GateAction.ALLOW)

    def test_env_default_autonomy_override_applied(self) -> None:
        verdict = gate_tool_call(
            _make_ctx("", tool_id="git_commit"),
            env={
                ENV_TOOL_GATE_ENABLED: "true",
                ENV_TOOL_GATE_DEFAULT_AUTONOMY: "L0_manual_only",
            },
        )
        # L0 + MEDIUM → REQUIRE_APPROVAL.
        self.assertEqual(verdict.action, GateAction.REQUIRE_APPROVAL)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
