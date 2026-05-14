"""P0-J commit 5 — coding bootstrap insufficiency-bypass tests."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.coding.coding_bootstrap import (
    CodingBootstrap,
    STATUS_BYPASS,
    STATUS_NOT_CODING_REQUEST,
    STATUS_REQUIRES_USER_INPUT,
    evaluate_coding_bootstrap,
)


# ---------------------------------------------------------------------------
# Bypass — naver-search-clone scenario
# ---------------------------------------------------------------------------


class BypassScenarioTests(unittest.TestCase):
    def test_naver_search_clone_via_user_links(self) -> None:
        result = evaluate_coding_bootstrap(
            message_text=(
                "Next.js + NestJS + PostgreSQL + Docker Compose 기반 "
                "회원가입/로그인/검색 앱 구현해줘"
            ),
            user_links=(
                "https://github.com/yule-studio/naver-search-clone/issues/1",
            ),
        )
        self.assertEqual(result.status, STATUS_BYPASS)
        self.assertTrue(result.bypass_insufficiency)
        self.assertTrue(result.code_context_pending)
        self.assertTrue(result.has_github_repo)
        self.assertTrue(result.write_intent)
        self.assertIn("Next.js", result.stacks_mentioned)
        # At least 4 docs seeded (Next.js / NestJS / Postgres / Docker Compose).
        self.assertGreaterEqual(len(result.seeded_docs), 4)

    def test_bypass_via_extra_github_target(self) -> None:
        # When the gateway already parsed github_target into session.extra
        # (P0-H stage 2 wiring), bootstrap reads from there.
        result = evaluate_coding_bootstrap(
            message_text="Next.js + Postgres 회원가입 화면 만들어줘",
            user_links=(),
            existing_extra={
                "github_target": {
                    "kind": "issue",
                    "owner": "foo",
                    "repo": "bar",
                    "number": 1,
                }
            },
        )
        self.assertEqual(result.status, STATUS_BYPASS)
        self.assertTrue(result.bypass_insufficiency)


# ---------------------------------------------------------------------------
# Requires user input — partial signals
# ---------------------------------------------------------------------------


class PartialSignalTests(unittest.TestCase):
    def test_repo_without_stack(self) -> None:
        result = evaluate_coding_bootstrap(
            message_text="이거 구현해줘",  # write intent but no stack
            user_links=("https://github.com/foo/bar/issues/1",),
        )
        self.assertEqual(result.status, STATUS_REQUIRES_USER_INPUT)
        self.assertFalse(result.bypass_insufficiency)
        self.assertIn("stack mention", result.reason or "")

    def test_stack_without_repo(self) -> None:
        result = evaluate_coding_bootstrap(
            message_text="Next.js + Postgres 만들어줘",
            user_links=(),
        )
        self.assertEqual(result.status, STATUS_REQUIRES_USER_INPUT)
        self.assertIn("repo target", result.reason or "")

    def test_stack_and_repo_without_write_intent(self) -> None:
        # No write verb — likely a review request, not a build request.
        result = evaluate_coding_bootstrap(
            message_text="Next.js + Postgres 코드 검토해줘",
            user_links=("https://github.com/foo/bar/issues/1",),
        )
        self.assertEqual(result.status, STATUS_REQUIRES_USER_INPUT)
        self.assertIn("write intent", result.reason or "")


# ---------------------------------------------------------------------------
# Not a coding request
# ---------------------------------------------------------------------------


class NotCodingRequestTests(unittest.TestCase):
    def test_pure_status_question(self) -> None:
        result = evaluate_coding_bootstrap(
            message_text="지금 뭐하고 있어?",
            user_links=(),
        )
        self.assertEqual(result.status, STATUS_NOT_CODING_REQUEST)
        self.assertFalse(result.bypass_insufficiency)

    def test_empty_message(self) -> None:
        result = evaluate_coding_bootstrap(
            message_text="",
            user_links=(),
        )
        self.assertEqual(result.status, STATUS_NOT_CODING_REQUEST)


# ---------------------------------------------------------------------------
# Summary line + round trip
# ---------------------------------------------------------------------------


class SummaryLineTests(unittest.TestCase):
    def test_bypass_summary(self) -> None:
        result = CodingBootstrap(
            status=STATUS_BYPASS,
            bypass_insufficiency=True,
            code_context_pending=True,
            stacks_mentioned=("Next.js", "Postgres"),
            seeded_docs=("Next.js", "PostgreSQL"),
        )
        line = result.status_summary_line()
        self.assertIn("🚀", line)
        self.assertIn("우회", line)
        self.assertIn("2 stacks", line)

    def test_requires_user_input_summary(self) -> None:
        result = CodingBootstrap(
            status=STATUS_REQUIRES_USER_INPUT,
            bypass_insufficiency=False,
            code_context_pending=False,
        )
        self.assertIn("📝", result.status_summary_line())


class RoundTripTests(unittest.TestCase):
    def test_to_dict_includes_all_signals(self) -> None:
        result = evaluate_coding_bootstrap(
            message_text="Next.js + Postgres 만들어줘",
            user_links=("https://github.com/foo/bar/issues/1",),
        )
        payload = result.to_dict()
        self.assertEqual(payload["status"], STATUS_BYPASS)
        self.assertTrue(payload["bypass_insufficiency"])
        self.assertTrue(payload["code_context_pending"])
        self.assertGreaterEqual(len(payload["seeded_docs"]), 1)


if __name__ == "__main__":
    unittest.main()
