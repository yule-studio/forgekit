"""LSP preflight unit tests — F9 / #100.

Covers the slim acceptance criteria:

  * 6 runner modules — binary presence check + subprocess fake seam.
  * Severity levelling: error → block / warning → warning /
    info → advisory / clean → advisory.
  * Binary missing → advisory (never block).
  * PasteGuard masks the subprocess stderr tail.
  * Env OFF → always advisory + no subprocess fires.
  * Role → runner chain mapping is stable.
"""

from __future__ import annotations

import json
import os
import unittest
from typing import Any, Sequence
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.static_analysis import (
    DEFAULT_TIMEOUT_SECONDS,
    LSP_ROLE_RUNNER_CHAINS,
    LspFinding,
    LspResult,
    PreflightLspLevel,
    PreflightLspVerdict,
    is_lsp_preflight_enabled,
    judge_lsp_preflight,
    resolve_runner_chain,
)
from yule_orchestrator.agents.static_analysis.lsp_preflight import (
    ENV_PREFLIGHT_ENABLED,
    ENV_RUNNERS,
    ENV_TIMEOUT_SECONDS,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
)
from yule_orchestrator.agents.static_analysis.runners import build_runner_registry
from yule_orchestrator.agents.static_analysis.runners.go import GoRunner
from yule_orchestrator.agents.static_analysis.runners.python_mypy import (
    PythonMypyRunner,
)
from yule_orchestrator.agents.static_analysis.runners.python_pyright import (
    PythonPyrightRunner,
)
from yule_orchestrator.agents.static_analysis.runners.python_ruff import (
    PythonRuffRunner,
)
from yule_orchestrator.agents.static_analysis.runners.rust import RustRunner
from yule_orchestrator.agents.static_analysis.runners.typescript import (
    TypescriptRunner,
)


class _FakeCompletedProcess:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_runner(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Return a callable shaped like ``subprocess.run`` for tests."""

    captured: dict[str, Any] = {}

    def runner(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        captured["argv"] = args[0] if args else kwargs.get("args")
        captured["timeout"] = kwargs.get("timeout")
        return _FakeCompletedProcess(returncode=returncode, stdout=stdout, stderr=stderr)

    runner.captured = captured  # type: ignore[attr-defined]
    return runner


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


class EnvHelperTests(unittest.TestCase):
    def test_truthy_and_falsy_matrix(self) -> None:
        self.assertFalse(is_lsp_preflight_enabled(env={}))
        for value in ("1", "true", "TRUE", "yes", "on"):
            self.assertTrue(
                is_lsp_preflight_enabled(env={ENV_PREFLIGHT_ENABLED: value}),
                msg=f"expected truthy: {value}",
            )
        for value in ("", "0", "false", "no", "off", "garbage"):
            self.assertFalse(
                is_lsp_preflight_enabled(env={ENV_PREFLIGHT_ENABLED: value}),
                msg=f"expected falsy: {value}",
            )


# ---------------------------------------------------------------------------
# Role → chain mapping
# ---------------------------------------------------------------------------


class RoleChainResolutionTests(unittest.TestCase):
    def test_role_chains_for_documented_roles(self) -> None:
        py_chain = ("python_ruff", "python_pyright", "python_mypy")
        self.assertEqual(resolve_runner_chain(role="backend", runners="AUTO"), py_chain)
        self.assertEqual(resolve_runner_chain(role="ai", runners="AUTO"), py_chain)
        self.assertEqual(
            resolve_runner_chain(role="frontend", runners="AUTO"), ("typescript",)
        )
        self.assertEqual(
            resolve_runner_chain(role="devops", runners="AUTO"), ("python_ruff",)
        )
        self.assertEqual(
            resolve_runner_chain(role="qa", runners="AUTO"), ("python_ruff",)
        )

    def test_unknown_role_falls_back_to_default(self) -> None:
        self.assertEqual(
            resolve_runner_chain(role="mystery", runners="AUTO"),
            ("python_ruff",),
        )

    def test_explicit_runners_override_role(self) -> None:
        chain = resolve_runner_chain(role="backend", runners=("rust",))
        self.assertEqual(chain, ("rust",))
        self.assertEqual(
            resolve_runner_chain(role="qa", runners="go,rust"),
            ("go", "rust"),
        )

    def test_env_runners_used_when_auto(self) -> None:
        with mock.patch.dict(os.environ, {ENV_RUNNERS: "go,rust"}, clear=False):
            chain = resolve_runner_chain(role="backend", runners="AUTO")
        self.assertEqual(chain, ("go", "rust"))


# ---------------------------------------------------------------------------
# Env OFF / short-circuits
# ---------------------------------------------------------------------------


class EnvOffShortCircuitTests(unittest.TestCase):
    def test_env_off_returns_advisory_without_running(self) -> None:
        captured: list[str] = []

        def runner(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
            captured.append("called")
            return _FakeCompletedProcess()

        verdict = judge_lsp_preflight(
            paths=["foo.py"],
            role="backend",
            subprocess_runner=runner,
            env={},
        )
        self.assertEqual(verdict.level, PreflightLspLevel.ADVISORY)
        self.assertEqual(verdict.blocker_count, 0)
        self.assertEqual(captured, [])
        self.assertIn("env OFF", verdict.advisory_reason)

    def test_empty_paths_advisory(self) -> None:
        verdict = judge_lsp_preflight(
            paths=[],
            role="backend",
            env={ENV_PREFLIGHT_ENABLED: "true"},
        )
        self.assertEqual(verdict.level, PreflightLspLevel.ADVISORY)
        self.assertIn("paths", verdict.advisory_reason)


# ---------------------------------------------------------------------------
# Runner unit tests (subprocess fake)
# ---------------------------------------------------------------------------


class PythonRuffRunnerTests(unittest.TestCase):
    def test_missing_binary_advisory(self) -> None:
        runner = PythonRuffRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.python_ruff.which_binary",
            return_value=None,
        ):
            result = runner.run(["foo.py"], subprocess_runner=_fake_runner())
        self.assertTrue(result.advisory)
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.findings, ())

    def test_parses_json_findings(self) -> None:
        payload = json.dumps(
            [
                {
                    "code": "F401",
                    "message": "Unused import",
                    "filename": "foo.py",
                    "location": {"row": 3, "column": 1},
                },
                {
                    "code": "W291",
                    "message": "Trailing whitespace",
                    "filename": "foo.py",
                    "location": {"row": 5, "column": 10},
                },
            ]
        )
        runner = PythonRuffRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.python_ruff.which_binary",
            return_value="/usr/bin/ruff",
        ):
            result = runner.run(
                ["foo.py"],
                subprocess_runner=_fake_runner(stdout=payload),
            )
        self.assertEqual(len(result.findings), 2)
        self.assertEqual(result.findings[0].severity, SEVERITY_ERROR)
        self.assertEqual(result.findings[1].severity, SEVERITY_WARNING)
        self.assertEqual(result.findings[0].code, "F401")


class PythonPyrightRunnerTests(unittest.TestCase):
    def test_missing_binary_advisory(self) -> None:
        runner = PythonPyrightRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.python_pyright.which_binary",
            return_value=None,
        ):
            result = runner.run(["foo.py"], subprocess_runner=_fake_runner())
        self.assertTrue(result.advisory)

    def test_parses_general_diagnostics(self) -> None:
        payload = json.dumps(
            {
                "generalDiagnostics": [
                    {
                        "file": "foo.py",
                        "severity": "error",
                        "message": "Type error",
                        "rule": "reportGeneralTypeIssues",
                        "range": {"start": {"line": 9, "character": 4}},
                    },
                    {
                        "file": "foo.py",
                        "severity": "warning",
                        "message": "Unused var",
                        "rule": "reportUnusedVariable",
                    },
                ]
            }
        )
        runner = PythonPyrightRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.python_pyright.which_binary",
            return_value="/usr/bin/pyright",
        ):
            result = runner.run(
                ["foo.py"],
                subprocess_runner=_fake_runner(stdout=payload),
            )
        self.assertEqual(len(result.findings), 2)
        self.assertEqual(result.findings[0].severity, SEVERITY_ERROR)
        self.assertEqual(result.findings[0].line, 10)  # 0-based → 1-based
        self.assertEqual(result.findings[1].severity, SEVERITY_WARNING)


class PythonMypyRunnerTests(unittest.TestCase):
    def test_missing_binary_advisory(self) -> None:
        runner = PythonMypyRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.python_mypy.which_binary",
            return_value=None,
        ):
            result = runner.run(["foo.py"], subprocess_runner=_fake_runner())
        self.assertTrue(result.advisory)

    def test_parses_text_lines(self) -> None:
        stdout = (
            "foo.py:42:7: error: Incompatible return value type  [return-value]\n"
            "foo.py:50: note: Revealed type is 'int'\n"
            "Success: no issues found in 0 source file\n"
        )
        runner = PythonMypyRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.python_mypy.which_binary",
            return_value="/usr/bin/mypy",
        ):
            result = runner.run(
                ["foo.py"],
                subprocess_runner=_fake_runner(stdout=stdout),
            )
        self.assertEqual(len(result.findings), 2)
        self.assertEqual(result.findings[0].severity, SEVERITY_ERROR)
        self.assertEqual(result.findings[0].code, "return-value")
        self.assertEqual(result.findings[1].severity, SEVERITY_INFO)


class TypescriptRunnerTests(unittest.TestCase):
    def test_missing_binary_advisory(self) -> None:
        runner = TypescriptRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.typescript.which_binary",
            return_value=None,
        ):
            result = runner.run(["src/foo.ts"], subprocess_runner=_fake_runner())
        self.assertTrue(result.advisory)

    def test_parses_tsc_diagnostics(self) -> None:
        stdout = (
            "src/foo.ts(12,5): error TS2322: Type 'string' is not assignable to 'number'.\n"
            "src/bar.ts(3,1): warning TS6133: 'x' is declared but never used.\n"
        )
        runner = TypescriptRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.typescript.which_binary",
            return_value="/usr/bin/tsc",
        ):
            result = runner.run(
                ["src"],
                subprocess_runner=_fake_runner(stdout=stdout),
            )
        self.assertEqual(len(result.findings), 2)
        self.assertEqual(result.findings[0].severity, SEVERITY_ERROR)
        self.assertEqual(result.findings[0].code, "TS2322")
        self.assertEqual(result.findings[1].severity, SEVERITY_WARNING)


class GoRunnerTests(unittest.TestCase):
    def test_missing_binary_advisory(self) -> None:
        runner = GoRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.go.which_binary",
            return_value=None,
        ):
            result = runner.run(["./..."], subprocess_runner=_fake_runner())
        self.assertTrue(result.advisory)

    def test_parses_go_vet_stderr(self) -> None:
        stderr = "main.go:14:9: shadow: declaration of \"err\" shadows declaration\n"
        runner = GoRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.go.which_binary",
            return_value="/usr/bin/go",
        ):
            result = runner.run(
                ["./..."],
                subprocess_runner=_fake_runner(stderr=stderr, returncode=1),
            )
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].severity, SEVERITY_ERROR)
        self.assertEqual(result.findings[0].file, "main.go")


class RustRunnerTests(unittest.TestCase):
    def test_missing_binary_advisory(self) -> None:
        runner = RustRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.rust.which_binary",
            return_value=None,
        ):
            result = runner.run([], subprocess_runner=_fake_runner())
        self.assertTrue(result.advisory)

    def test_parses_clippy_jsonl(self) -> None:
        stdout = (
            json.dumps(
                {
                    "reason": "compiler-message",
                    "message": {
                        "message": "unused variable",
                        "level": "warning",
                        "code": {"code": "unused_variables"},
                        "spans": [
                            {
                                "is_primary": True,
                                "file_name": "src/main.rs",
                                "line_start": 7,
                                "column_start": 9,
                            }
                        ],
                    },
                }
            )
            + "\n"
            + json.dumps({"reason": "build-finished", "success": True})
            + "\n"
        )
        runner = RustRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.rust.which_binary",
            return_value="/usr/bin/cargo",
        ):
            result = runner.run(
                [],
                subprocess_runner=_fake_runner(stdout=stdout),
            )
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].severity, SEVERITY_WARNING)
        self.assertEqual(result.findings[0].file, "src/main.rs")
        self.assertEqual(result.findings[0].code, "unused_variables")


# ---------------------------------------------------------------------------
# Verdict aggregation
# ---------------------------------------------------------------------------


class VerdictAggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        # A registry of stub runners that emit a deterministic
        # set of findings without touching subprocess.
        self.registry = {
            "stub_error": _StubRunner(
                runner_id="stub_error",
                language="python",
                findings=(
                    LspFinding(
                        file="foo.py",
                        line=1,
                        column=1,
                        severity=SEVERITY_ERROR,
                        code="E001",
                        message="boom",
                    ),
                ),
            ),
            "stub_warn": _StubRunner(
                runner_id="stub_warn",
                language="python",
                findings=(
                    LspFinding(
                        file="foo.py",
                        line=2,
                        column=1,
                        severity=SEVERITY_WARNING,
                        code="W001",
                        message="warn",
                    ),
                ),
            ),
            "stub_info": _StubRunner(
                runner_id="stub_info",
                language="python",
                findings=(
                    LspFinding(
                        file="foo.py",
                        line=3,
                        column=1,
                        severity=SEVERITY_INFO,
                        code="I001",
                        message="note",
                    ),
                ),
            ),
            "stub_clean": _StubRunner(
                runner_id="stub_clean",
                language="python",
                findings=(),
            ),
        }

    def _judge(self, runner_id: str) -> PreflightLspVerdict:
        return judge_lsp_preflight(
            paths=["foo.py"],
            role="backend",
            runners=(runner_id,),
            env={ENV_PREFLIGHT_ENABLED: "true"},
            registry=self.registry,
        )

    def test_severity_levelling_table(self) -> None:
        # error → block / warning → warning / info → advisory / clean → advisory
        block = self._judge("stub_error")
        self.assertEqual(block.level, PreflightLspLevel.BLOCK)
        self.assertEqual(block.blocker_count, 1)
        self.assertTrue(block.recommend_needs_approval)

        warn = self._judge("stub_warn")
        self.assertEqual(warn.level, PreflightLspLevel.WARNING)
        self.assertEqual(warn.blocker_count, 0)

        info = self._judge("stub_info")
        self.assertEqual(info.level, PreflightLspLevel.ADVISORY)

        clean = self._judge("stub_clean")
        self.assertEqual(clean.level, PreflightLspLevel.ADVISORY)

    def test_to_payload_round_trips(self) -> None:
        verdict = self._judge("stub_error")
        payload = verdict.to_payload()
        self.assertEqual(payload["level"], "block")
        self.assertEqual(payload["blocker_count"], 1)
        self.assertEqual(payload["runner_id_chain"], ["stub_error"])

    def test_registry_skips_unknown_runner_ids(self) -> None:
        verdict = judge_lsp_preflight(
            paths=["foo.py"],
            role="backend",
            runners=("does_not_exist", "stub_warn"),
            env={ENV_PREFLIGHT_ENABLED: "true"},
            registry=self.registry,
        )
        self.assertEqual(verdict.runner_id_chain, ("stub_warn",))


class _StubRunner:
    def __init__(self, *, runner_id: str, language: str, findings: tuple) -> None:
        self.runner_id = runner_id
        self.language = language
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


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------


class TimeoutClampTests(unittest.TestCase):
    def test_user_supplied_timeout_capped(self) -> None:
        captured: dict[str, Any] = {}

        def runner(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
            captured["timeout"] = kwargs.get("timeout")
            return _FakeCompletedProcess(returncode=0, stdout="[]", stderr="")

        ruff = PythonRuffRunner()
        with mock.patch(
            "yule_orchestrator.agents.static_analysis.runners.python_ruff.which_binary",
            return_value="/usr/bin/ruff",
        ):
            ruff.run(["foo.py"], timeout=600, subprocess_runner=runner)
        self.assertEqual(captured["timeout"], DEFAULT_TIMEOUT_SECONDS)


class RegistryWiringTests(unittest.TestCase):
    def test_six_runners_registered_and_mapping_matches(self) -> None:
        registry = build_runner_registry()
        expected = {
            "python_ruff",
            "python_pyright",
            "python_mypy",
            "typescript",
            "go",
            "rust",
        }
        self.assertEqual(set(registry.keys()), expected)
        self.assertEqual(
            LSP_ROLE_RUNNER_CHAINS["backend"],
            ("python_ruff", "python_pyright", "python_mypy"),
        )
        self.assertEqual(LSP_ROLE_RUNNER_CHAINS["frontend"], ("typescript",))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
