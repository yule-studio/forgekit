"""ClaudeCodeRunner live submit + /compact token capture (issue #185 follow-up B)."""

from __future__ import annotations

import json
import subprocess
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.runners.base import AgentRequest, RunnerStatus
from yule_engineering.agents.runners.claude_code import (
    ClaudeCodeRunner,
    CompactBoundary,
    parse_compact_boundary,
)


def _req() -> AgentRequest:
    return AgentRequest(prompt="설명해줘", role="qa-engineer", task_id="t1")


def _invoke_ok(args, input_text, timeout):
    return 0, "라이브 응답입니다", ""


def _invoke_fail(args, input_text, timeout):
    return 3, "", "boom"


def _invoke_timeout(args, input_text, timeout):
    raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)


class SubmitTests(unittest.TestCase):
    def test_non_live_is_dry_run(self) -> None:
        # injected invoke ⇒ available, but live flag off ⇒ dry-run contract kept
        runner = ClaudeCodeRunner(config={"invoke": _invoke_ok, "live_enabled": False})
        resp = runner.submit(_req())
        self.assertEqual(resp.status, RunnerStatus.DRY_RUN)

    def test_live_ok(self) -> None:
        runner = ClaudeCodeRunner(config={"invoke": _invoke_ok, "live_enabled": True})
        resp = runner.submit(_req())
        self.assertEqual(resp.status, RunnerStatus.OK)
        self.assertEqual(resp.text, "라이브 응답입니다")
        self.assertTrue(resp.metrics.get("live"))

    def test_live_nonzero_exit_is_error(self) -> None:
        runner = ClaudeCodeRunner(config={"invoke": _invoke_fail, "live_enabled": True})
        resp = runner.submit(_req())
        self.assertEqual(resp.status, RunnerStatus.ERROR)
        self.assertIn("exited 3", resp.detail or "")

    def test_live_timeout_is_error(self) -> None:
        runner = ClaudeCodeRunner(
            config={"invoke": _invoke_timeout, "live_enabled": True, "timeout_seconds": 5}
        )
        resp = runner.submit(_req())
        self.assertEqual(resp.status, RunnerStatus.ERROR)
        self.assertIn("timed out", resp.detail or "")

    def test_unavailable_when_no_cli_and_no_invoke(self) -> None:
        runner = ClaudeCodeRunner(config={"cli": "definitely-not-a-real-cli-xyz", "live_enabled": True})
        resp = runner.submit(_req())
        self.assertEqual(resp.status, RunnerStatus.UNAVAILABLE)


class CompactBoundaryParseTests(unittest.TestCase):
    def test_parse_stream_event(self) -> None:
        line = json.dumps(
            {"type": "compact_boundary", "compact_metadata": {"pre_tokens": 1000, "post_tokens": 250}}
        )
        cb = parse_compact_boundary("noise\n" + line + "\nmore")
        self.assertTrue(cb.parsed)
        self.assertEqual(cb.pre_tokens, 1000)
        self.assertEqual(cb.post_tokens, 250)
        self.assertEqual(cb.saved_tokens, 750)

    def test_parse_alt_key_spelling(self) -> None:
        line = json.dumps({"type": "compact_boundary", "preTokens": 800, "postTokens": 300})
        cb = parse_compact_boundary(line)
        self.assertTrue(cb.parsed)
        self.assertEqual(cb.pre_tokens, 800)

    def test_parse_fallback_warns(self) -> None:
        cb = parse_compact_boundary("just regular output, no boundary")
        self.assertFalse(cb.parsed)
        self.assertIsNotNone(cb.warning)
        self.assertIsNone(cb.saved_tokens)


class CompactMethodTests(unittest.TestCase):
    def test_compact_not_live_warns(self) -> None:
        runner = ClaudeCodeRunner(config={"invoke": _invoke_ok, "live_enabled": False})
        cb = runner.compact()
        self.assertFalse(cb.parsed)
        self.assertIn("live", (cb.warning or "").lower())

    def test_compact_live_parses(self) -> None:
        line = json.dumps(
            {"type": "compact_boundary", "compact_metadata": {"pre_tokens": 500, "post_tokens": 100}}
        )

        def _invoke(args, input_text, timeout):
            return 0, line, ""

        runner = ClaudeCodeRunner(config={"invoke": _invoke, "live_enabled": True})
        cb = runner.compact(focus="결제")
        self.assertTrue(cb.parsed)
        self.assertEqual(cb.saved_tokens, 400)


if __name__ == "__main__":
    unittest.main()
