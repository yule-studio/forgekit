"""Unit tests for the F12 / #103 static risk classifier.

Covers the 5 RiskClass levels × catalogue surfaces, the protected
branch escalation, the dangerous-flag rules, and the secret keyword
nudge. Pure-function tests — no env reads, no I/O.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.safety.risk_classifier import (
    RiskClass,
    RiskSignal,
    ToolCallContext,
    classify_tool_call,
)


def _ctx(
    tool_id: str,
    *,
    target: str = "",
    args=(),
    role: str = "engineering-agent/devops-engineer",
) -> ToolCallContext:
    return ToolCallContext(
        tool_id=tool_id,
        target=target,
        args=tuple(args),
        role=role,
        session_id="session-test",
        autonomy_level="L2_autonomous_record",
    )


class SafeToolMatrixTests(unittest.TestCase):
    """SAFE-class tool ids — read-only surfaces."""

    def test_read_file_is_safe(self) -> None:
        risk, signals = classify_tool_call(_ctx("read_file", target="README.md"))
        self.assertEqual(risk, RiskClass.SAFE)
        self.assertTrue(any(s.weight is RiskClass.SAFE for s in signals))

    def test_git_status_is_safe(self) -> None:
        risk, _ = classify_tool_call(_ctx("git_status"))
        self.assertEqual(risk, RiskClass.SAFE)

    def test_git_log_is_safe(self) -> None:
        risk, _ = classify_tool_call(_ctx("git_log"))
        self.assertEqual(risk, RiskClass.SAFE)

    def test_grep_is_safe(self) -> None:
        risk, _ = classify_tool_call(_ctx("grep", target="hello"))
        self.assertEqual(risk, RiskClass.SAFE)


class LowToolMatrixTests(unittest.TestCase):
    """LOW-class tool ids — single edits and tests."""

    def test_edit_file_single_is_low(self) -> None:
        risk, _ = classify_tool_call(_ctx("edit_file", target="src/foo.py"))
        self.assertEqual(risk, RiskClass.LOW)

    def test_unittest_runs_low(self) -> None:
        risk, _ = classify_tool_call(_ctx("unittest"))
        self.assertEqual(risk, RiskClass.LOW)

    def test_pytest_runs_low(self) -> None:
        risk, _ = classify_tool_call(_ctx("pytest"))
        self.assertEqual(risk, RiskClass.LOW)

    def test_git_add_is_low(self) -> None:
        risk, _ = classify_tool_call(_ctx("git_add"))
        self.assertEqual(risk, RiskClass.LOW)


class MediumToolMatrixTests(unittest.TestCase):
    """MEDIUM-class tool ids — repo-local mutations."""

    def test_git_commit_is_medium(self) -> None:
        risk, _ = classify_tool_call(_ctx("git_commit"))
        self.assertEqual(risk, RiskClass.MEDIUM)

    def test_git_branch_create_is_medium(self) -> None:
        risk, _ = classify_tool_call(_ctx("git_branch_create", target="feature/x"))
        self.assertEqual(risk, RiskClass.MEDIUM)

    def test_new_module_add_is_medium(self) -> None:
        risk, _ = classify_tool_call(_ctx("new_module_add"))
        self.assertEqual(risk, RiskClass.MEDIUM)

    def test_subprocess_within_repo_is_medium(self) -> None:
        risk, _ = classify_tool_call(_ctx("subprocess_within_repo"))
        self.assertEqual(risk, RiskClass.MEDIUM)

    def test_unknown_tool_defaults_to_medium(self) -> None:
        risk, signals = classify_tool_call(_ctx("totally_new_surface"))
        self.assertEqual(risk, RiskClass.MEDIUM)
        self.assertTrue(any("unknown" in s.name for s in signals))


class HighToolMatrixTests(unittest.TestCase):
    """HIGH-class tool ids — external surface / live state."""

    def test_git_push_feature_branch_is_high(self) -> None:
        risk, _ = classify_tool_call(_ctx("git_push", target="feature/foo"))
        self.assertEqual(risk, RiskClass.HIGH)

    def test_external_http_fetch_is_high(self) -> None:
        risk, _ = classify_tool_call(
            _ctx("external_http_fetch", target="https://api.example.com")
        )
        self.assertEqual(risk, RiskClass.HIGH)

    def test_live_llm_call_is_high(self) -> None:
        risk, _ = classify_tool_call(_ctx("live_llm_call"))
        self.assertEqual(risk, RiskClass.HIGH)

    def test_env_local_modify_via_tool_id_is_high(self) -> None:
        risk, _ = classify_tool_call(_ctx("env_local_modify"))
        self.assertEqual(risk, RiskClass.HIGH)

    def test_edit_file_targeting_env_local_escalates_to_high(self) -> None:
        risk, signals = classify_tool_call(
            _ctx("edit_file", target=".env.local")
        )
        self.assertEqual(risk, RiskClass.HIGH)
        self.assertTrue(any("env_local" in s.name for s in signals))

    def test_subprocess_outside_repo_is_high(self) -> None:
        risk, _ = classify_tool_call(_ctx("subprocess_outside_repo"))
        self.assertEqual(risk, RiskClass.HIGH)

    def test_secret_decode_attempt_is_high(self) -> None:
        risk, _ = classify_tool_call(_ctx("secret_decode_attempt"))
        self.assertEqual(risk, RiskClass.HIGH)


class CriticalToolMatrixTests(unittest.TestCase):
    """CRITICAL-class tool ids — destructive / irreversible."""

    def test_git_reset_hard_is_critical(self) -> None:
        risk, _ = classify_tool_call(_ctx("git_reset_hard"))
        self.assertEqual(risk, RiskClass.CRITICAL)

    def test_rm_rf_is_critical(self) -> None:
        risk, _ = classify_tool_call(_ctx("rm_rf", target="/"))
        self.assertEqual(risk, RiskClass.CRITICAL)

    def test_force_push_is_critical(self) -> None:
        risk, _ = classify_tool_call(_ctx("force_push", target="feature/foo"))
        self.assertEqual(risk, RiskClass.CRITICAL)

    def test_secret_rotation_is_critical(self) -> None:
        risk, _ = classify_tool_call(_ctx("secret_rotation"))
        self.assertEqual(risk, RiskClass.CRITICAL)

    def test_no_verify_flag_escalates_to_critical(self) -> None:
        risk, signals = classify_tool_call(
            _ctx("git_commit", args=("--no-verify",))
        )
        self.assertEqual(risk, RiskClass.CRITICAL)
        self.assertTrue(any("no_verify" in s.name for s in signals))

    def test_dangerously_disable_sandbox_is_critical(self) -> None:
        risk, _ = classify_tool_call(_ctx("dangerouslydisablesandbox"))
        self.assertEqual(risk, RiskClass.CRITICAL)


class ProtectedBranchTests(unittest.TestCase):
    """Hard rail: pushing to a protected branch is always CRITICAL."""

    def test_git_push_main_escalates_to_critical(self) -> None:
        risk, signals = classify_tool_call(_ctx("git_push", target="main"))
        self.assertEqual(risk, RiskClass.CRITICAL)
        self.assertTrue(any("protected_branch" in s.name for s in signals))

    def test_git_push_origin_master_escalates(self) -> None:
        risk, _ = classify_tool_call(_ctx("git_push", target="origin/master"))
        self.assertEqual(risk, RiskClass.CRITICAL)

    def test_git_push_refs_heads_develop_escalates(self) -> None:
        risk, _ = classify_tool_call(
            _ctx("git_push", target="refs/heads/develop")
        )
        self.assertEqual(risk, RiskClass.CRITICAL)

    def test_protected_branch_target_via_args(self) -> None:
        risk, _ = classify_tool_call(
            _ctx("git_push", target="", args=("origin", "main"))
        )
        self.assertEqual(risk, RiskClass.CRITICAL)


class SecretKeywordTests(unittest.TestCase):
    """target or args containing secret-like keywords nudges to HIGH."""

    def test_edit_file_with_secret_keyword_target_escalates(self) -> None:
        risk, signals = classify_tool_call(
            _ctx("edit_file", target="configs/my_secret.yml")
        )
        # Baseline LOW + secret keyword nudge → HIGH.
        self.assertEqual(risk, RiskClass.HIGH)
        self.assertTrue(any("secret.keyword" in s.name for s in signals))

    def test_args_with_api_key_escalates(self) -> None:
        risk, _ = classify_tool_call(
            _ctx("subprocess_within_repo", args=("dump_api_key",))
        )
        self.assertEqual(risk, RiskClass.HIGH)


class SignalShapeTests(unittest.TestCase):
    """Smoke tests on the signal contract itself."""

    def test_signal_dataclass_is_frozen(self) -> None:
        signal = RiskSignal(name="x", weight=RiskClass.SAFE, evidence="e")
        with self.assertRaises(Exception):
            signal.name = "y"  # type: ignore[misc]

    def test_classify_is_pure_deterministic(self) -> None:
        ctx = _ctx("git_push", target="main")
        a = classify_tool_call(ctx)
        b = classify_tool_call(ctx)
        self.assertEqual(a, b)

    def test_context_dataclass_is_frozen(self) -> None:
        ctx = _ctx("read_file")
        with self.assertRaises(Exception):
            ctx.tool_id = "git_push"  # type: ignore[misc]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
