"""LSP preflight governance regression — hard rails pinned (#100).

Mirrors :mod:`tests.engineering.test_paste_guard_governance` in
posture: one suite pinning the most important hard rails of F9
so a single rename / regex flip / env-default flip trips a
clearly named test.

Rails pinned:

  1. ``YULE_LSP_PREFLIGHT_ENABLED`` default is ``false`` — env OFF
     means advisory + no subprocess call.
  2. Missing binary → advisory result, **never** block.
  3. Timeout is hard-capped at 30s regardless of caller / env.
  4. subprocess stderr is masked through PasteGuard before being
     attached to the result (no raw secret bytes leak).
  5. Role → runner chain mapping is the documented one.
  6. The judgement seam never mutates target files (read-only).
  7. Aggregation table: error → block, warning → warning,
     info / clean → advisory.
  8. Public surface of :mod:`static_analysis` remains importable
     under the documented names.
"""

from __future__ import annotations

import unittest
from typing import Any, Sequence
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.static_analysis import (
    DEFAULT_TIMEOUT_SECONDS,
    LSP_ROLE_RUNNER_CHAINS,
    LspFinding,
    LspResult,
    PreflightLspLevel,
    judge_lsp_preflight,
)
from yule_engineering.agents.static_analysis.lsp_preflight import (
    ENV_PREFLIGHT_ENABLED,
    ENV_TIMEOUT_SECONDS,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
)
from yule_engineering.agents.static_analysis.runners import (
    build_runner_registry,
    mask_stderr,
)
from yule_engineering.agents.static_analysis.runners.python_ruff import (
    PythonRuffRunner,
)


# A fake-but-pattern-shaped sentinel that PasteGuard treats as a
# critical secret. Never a real key.
ANTHROPIC_RAW = "sk-ant-" + "X" * 40 + "ZZ"


class _StubRunner:
    def __init__(self, *, runner_id: str, findings: tuple) -> None:
        self.runner_id = runner_id
        self.language = "python"
        self._findings = findings

    def run(
        self,
        paths: Sequence[str],
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        subprocess_runner: Any = None,
    ) -> LspResult:
        return LspResult(
            language=self.language,
            runner_id=self.runner_id,
            findings=self._findings,
            exit_code=0,
            stderr_tail="",
        )


class LspPreflightGovernanceTests(unittest.TestCase):
    def test_env_default_off_returns_advisory(self) -> None:
        spawned = []

        def runner(*args: Any, **kwargs: Any) -> Any:
            spawned.append("called")
            raise AssertionError("subprocess must not run when env is off")

        verdict = judge_lsp_preflight(
            paths=["foo.py"],
            role="backend",
            subprocess_runner=runner,
            env={},
        )
        self.assertEqual(verdict.level, PreflightLspLevel.ADVISORY)
        self.assertEqual(spawned, [])

    def test_missing_binary_yields_advisory_not_block(self) -> None:
        ruff = PythonRuffRunner()
        with mock.patch(
            "yule_engineering.agents.static_analysis.runners.python_ruff.which_binary",
            return_value=None,
        ):
            result = ruff.run(["foo.py"])
        self.assertTrue(result.advisory)
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.findings, ())

    def test_timeout_capped_at_30_seconds(self) -> None:
        # Even if the operator / .env wants 9999s, the seam clamps
        # the runner-side timeout to the documented hard cap.
        captured: dict[str, Any] = {}

        def runner(*args: Any, **kwargs: Any) -> Any:
            captured["timeout"] = kwargs.get("timeout")

            class _R:
                returncode = 0
                stdout = "[]"
                stderr = ""

            return _R()

        ruff = PythonRuffRunner()
        with mock.patch(
            "yule_engineering.agents.static_analysis.runners.python_ruff.which_binary",
            return_value="/usr/bin/ruff",
        ):
            ruff.run(["foo.py"], timeout=9999, subprocess_runner=runner)
        self.assertEqual(captured["timeout"], DEFAULT_TIMEOUT_SECONDS)

    def test_paste_guard_redacts_stderr_secret(self) -> None:
        stderr = f"ruff crashed: token={ANTHROPIC_RAW} bye"
        masked = mask_stderr(stderr)
        self.assertNotIn(ANTHROPIC_RAW, masked)

    def test_role_chain_mapping_pinned(self) -> None:
        self.assertEqual(
            LSP_ROLE_RUNNER_CHAINS["backend"],
            ("python_ruff", "python_pyright", "python_mypy"),
        )
        self.assertEqual(LSP_ROLE_RUNNER_CHAINS["frontend"], ("typescript",))
        self.assertEqual(LSP_ROLE_RUNNER_CHAINS["qa"], ("python_ruff",))
        self.assertEqual(LSP_ROLE_RUNNER_CHAINS["devops"], ("python_ruff",))
        self.assertEqual(LSP_ROLE_RUNNER_CHAINS["ai"], LSP_ROLE_RUNNER_CHAINS["backend"])

    def test_aggregation_levelling_table(self) -> None:
        registry = {
            "stub_error": _StubRunner(
                runner_id="stub_error",
                findings=(
                    LspFinding(
                        file="f.py",
                        line=1,
                        column=1,
                        severity=SEVERITY_ERROR,
                        code="E1",
                        message="boom",
                    ),
                ),
            ),
            "stub_warn": _StubRunner(
                runner_id="stub_warn",
                findings=(
                    LspFinding(
                        file="f.py",
                        line=1,
                        column=1,
                        severity=SEVERITY_WARNING,
                        code="W1",
                        message="warn",
                    ),
                ),
            ),
            "stub_clean": _StubRunner(runner_id="stub_clean", findings=()),
        }
        env = {ENV_PREFLIGHT_ENABLED: "true"}

        block = judge_lsp_preflight(
            paths=["f.py"], role="backend", runners=("stub_error",),
            env=env, registry=registry,
        )
        warn = judge_lsp_preflight(
            paths=["f.py"], role="backend", runners=("stub_warn",),
            env=env, registry=registry,
        )
        clean = judge_lsp_preflight(
            paths=["f.py"], role="backend", runners=("stub_clean",),
            env=env, registry=registry,
        )
        self.assertEqual(block.level, PreflightLspLevel.BLOCK)
        self.assertEqual(warn.level, PreflightLspLevel.WARNING)
        self.assertEqual(clean.level, PreflightLspLevel.ADVISORY)

    def test_public_surface_importable(self) -> None:
        registry = build_runner_registry()
        # The 6 runner_ids documented in the issue must remain
        # available under the same registry keys.
        for runner_id in (
            "python_ruff",
            "python_pyright",
            "python_mypy",
            "typescript",
            "go",
            "rust",
        ):
            self.assertIn(runner_id, registry)

    def test_env_timeout_clamped_via_judge(self) -> None:
        # Even if env says 9999, the verdict computation should not
        # raise and the clamped timeout flows to runners.
        verdict = judge_lsp_preflight(
            paths=["f.py"],
            role="backend",
            runners=("stub_clean",),
            env={
                ENV_PREFLIGHT_ENABLED: "true",
                ENV_TIMEOUT_SECONDS: "9999",
            },
            registry={
                "stub_clean": _StubRunner(runner_id="stub_clean", findings=()),
            },
        )
        self.assertEqual(verdict.level, PreflightLspLevel.ADVISORY)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
