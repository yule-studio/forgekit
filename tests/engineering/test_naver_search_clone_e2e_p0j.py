"""P0-J commit 8 — naver-search-clone e2e scenario (#145).

End-to-end: when the user pastes the exact bug-report message —
``Next.js + NestJS + PostgreSQL + Docker Compose 회원가입/로그인/검색
앱 구현해줘`` with a GitHub issue URL — the gateway must:

  1. classify task as `full-stack-app` (NOT platform-infra).
  2. NOT surface the "자료 부족" insufficiency follow-up.
  3. seed official docs for Next.js / NestJS / PostgreSQL / Docker Compose.
  4. emit a coding-bootstrap acknowledgement that proceeds to handoff.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.coding.coding_bootstrap import (
    STATUS_BYPASS,
    evaluate_coding_bootstrap,
)
from yule_orchestrator.agents.coding.official_docs_seed import (
    seed_official_docs,
)
from yule_orchestrator.agents.coding.stack_detector import (
    detect_stacks,
    has_write_intent,
)
from yule_orchestrator.discord.engineering_conversation import (
    TASK_INTAKE_CANDIDATE,
    _suggest_task_type,
    build_engineering_conversation_response,
)


NAVER_SEARCH_CLONE_TEXT = (
    "Next.js + NestJS + PostgreSQL + Docker Compose 기반 "
    "회원가입/로그인/검색 앱 구현해줘"
)
ISSUE_URL = "https://github.com/yule-studio/naver-search-clone/issues/1"


# ---------------------------------------------------------------------------
# Per-component checks
# ---------------------------------------------------------------------------


class StackDetectionTests(unittest.TestCase):
    def test_full_stack_detected(self) -> None:
        detection = detect_stacks(NAVER_SEARCH_CLONE_TEXT)
        self.assertTrue(detection.is_full_stack)
        self.assertFalse(detection.is_infra_only)
        for required in ("Next.js", "NestJS", "PostgreSQL", "Docker Compose"):
            self.assertIn(required, detection.stacks)

    def test_write_intent_detected(self) -> None:
        self.assertTrue(has_write_intent(NAVER_SEARCH_CLONE_TEXT))


class SuggestTaskTypeTests(unittest.TestCase):
    def test_classified_as_full_stack_app(self) -> None:
        task_type = _suggest_task_type(NAVER_SEARCH_CLONE_TEXT)
        self.assertEqual(task_type, "full-stack-app")
        self.assertNotEqual(task_type, "platform-infra")


class CodingBootstrapTests(unittest.TestCase):
    def test_bypass_status_with_issue_url(self) -> None:
        outcome = evaluate_coding_bootstrap(
            message_text=NAVER_SEARCH_CLONE_TEXT,
            user_links=(ISSUE_URL,),
        )
        self.assertEqual(outcome.status, STATUS_BYPASS)
        self.assertTrue(outcome.bypass_insufficiency)
        self.assertTrue(outcome.code_context_pending)
        # All four core stack docs seeded.
        self.assertGreaterEqual(len(outcome.seeded_docs), 4)


class OfficialDocsSeedTests(unittest.TestCase):
    def test_core_4_docs_seeded(self) -> None:
        seeds = seed_official_docs(
            ("Next.js", "NestJS", "PostgreSQL", "Docker Compose")
        )
        urls = {s.url for s in seeds}
        self.assertIn("https://nextjs.org/docs", urls)
        self.assertIn("https://docs.nestjs.com", urls)
        self.assertIn("https://www.postgresql.org/docs/current/", urls)
        self.assertIn("https://docs.docker.com/compose/", urls)


# ---------------------------------------------------------------------------
# End-to-end conversation layer behavior
# ---------------------------------------------------------------------------


class NaverSearchCloneEndToEndTests(unittest.TestCase):
    """The bug-report scenario, simulated through the conversation layer."""

    def test_response_is_task_intake_candidate_not_platform_infra(self) -> None:
        # We don't need a real collector — the conversation layer must
        # NOT trip the platform-infra classification path or the
        # NEEDS_USER_INPUT surface.
        with patch(
            "yule_orchestrator.discord.engineering_conversation._maybe_run_auto_collect",
            return_value=None,
        ):
            response = build_engineering_conversation_response(
                NAVER_SEARCH_CLONE_TEXT,
                user_links=(ISSUE_URL,),
            )
        # Intent is the coding intake path, not status / clarification.
        self.assertEqual(response.intent_id, TASK_INTAKE_CANDIDATE)
        # Suggested task type is full-stack-app.
        self.assertEqual(response.suggested_task_type, "full-stack-app")
        # Write intent detected.
        self.assertTrue(response.write_likely)
        # intake_prompt preserved.
        self.assertEqual(response.intake_prompt, NAVER_SEARCH_CLONE_TEXT)

    def test_bootstrap_body_replaces_needs_user_input_surface(self) -> None:
        # Simulate the collector returning NEEDS_USER_INPUT (the legacy
        # path that triggers "자료 부족"). Bootstrap must bypass it.
        from types import SimpleNamespace

        fake_collection = SimpleNamespace(
            mode=SimpleNamespace(value="needs_user_input"),
            user_prompt="관련 자료를 더 보내주세요.",
            auto_collected_count=0,
            collector_name="mock",
            query="naver search clone",
            pack=None,
        )
        with patch(
            "yule_orchestrator.discord.engineering_conversation._maybe_run_auto_collect",
            return_value=fake_collection,
        ):
            response = build_engineering_conversation_response(
                NAVER_SEARCH_CLONE_TEXT,
                user_links=(ISSUE_URL,),
            )
        # Bootstrap body must be in the content; legacy "자료 부족" must not.
        self.assertIn("coding bootstrap", response.content)
        self.assertIn("📚", response.content)  # stack detection emoji
        self.assertNotIn("자료가 조금 더 필요해요", response.content)
        self.assertNotIn("자동 수집이 비어 있어서", response.content)

    def test_user_provided_collection_still_uses_legacy_body(self) -> None:
        # When the collector succeeds (AUTO_COLLECTED), the legacy body
        # is used — bootstrap doesn't override.
        from types import SimpleNamespace

        fake_collection = SimpleNamespace(
            mode=SimpleNamespace(value="auto_collected"),
            user_prompt=None,
            auto_collected_count=3,
            collector_name="mock",
            query="naver search clone",
            pack=None,
        )
        with patch(
            "yule_orchestrator.discord.engineering_conversation._maybe_run_auto_collect",
            return_value=fake_collection,
        ):
            response = build_engineering_conversation_response(
                NAVER_SEARCH_CLONE_TEXT,
                user_links=(ISSUE_URL,),
            )
        # Legacy auto_collected greeting present.
        self.assertIn("1차 자료를 모아볼게요", response.content)

    def test_no_repo_url_falls_back_to_legacy_collection_path(self) -> None:
        # Without a repo URL, bootstrap status != BYPASS → legacy body wins.
        from types import SimpleNamespace

        fake_collection = SimpleNamespace(
            mode=SimpleNamespace(value="needs_user_input"),
            user_prompt="관련 자료를 더 보내주세요.",
            auto_collected_count=0,
            collector_name="mock",
            query="x",
            pack=None,
        )
        with patch(
            "yule_orchestrator.discord.engineering_conversation._maybe_run_auto_collect",
            return_value=fake_collection,
        ):
            response = build_engineering_conversation_response(
                NAVER_SEARCH_CLONE_TEXT,
                user_links=(),  # no repo URL
            )
        # Legacy NEEDS_USER_INPUT body shown — bootstrap bypass NOT applied.
        self.assertNotIn("coding bootstrap", response.content)
        self.assertIn("자료", response.content)


if __name__ == "__main__":
    unittest.main()
