from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents import (
    Dispatcher,
    DispatchRequest,
    TaskType,
    build_participants_pool,
)
from yule_engineering.agents.messaging.dispatcher import (
    ROLE_DEFAULT_WEIGHTS,
    TASK_REFERENCE_SOURCES,
    render_plan_summary,
)
from yule_engineering.agents.runners import (
    AgentRequest,
    AgentResponse,
    AgentRunner,
    RunnerCapability,
    RunnerHooks,
    RunnerStatus,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class _StaticRunner(AgentRunner):
    capabilities: Sequence[RunnerCapability] = (RunnerCapability.ADVISE,)

    def __init__(
        self,
        runner_id: str,
        provider: str,
        *,
        config: Mapping[str, Any] | None = None,
        hooks: RunnerHooks | None = None,
    ) -> None:
        super().__init__(config=config, hooks=hooks)
        self.runner_id = runner_id
        self.provider = provider

    def is_available(self) -> bool:
        return True

    def submit(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(runner_id=self.runner_id, status=RunnerStatus.OK, text="")


def _build_full_pool() -> "Dispatcher":
    factories = {
        runner_id: lambda config, hooks, rid=runner_id: _StaticRunner(rid, "test", config=config, hooks=hooks)
        for runner_id in ("claude", "codex", "gemini", "ollama", "github-copilot")
    }
    pool = build_participants_pool(REPO_ROOT, "engineering-agent", factories=factories)
    return Dispatcher(pool)


class ClassifyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.disp = _build_full_pool()

    def test_explicit_task_type_wins(self) -> None:
        request = DispatchRequest(prompt="something", task_type=TaskType.QA_TEST)
        self.assertEqual(self.disp.classify(request), TaskType.QA_TEST)

    def test_landing_keyword(self) -> None:
        self.assertEqual(
            self.disp.classify(DispatchRequest(prompt="새 랜딩페이지 hero 섹션 정리")),
            TaskType.LANDING_PAGE,
        )

    def test_onboarding_keyword(self) -> None:
        self.assertEqual(
            self.disp.classify(DispatchRequest(prompt="onboarding step 2 흐름 개선")),
            TaskType.ONBOARDING_FLOW,
        )

    def test_visual_polish_keyword(self) -> None:
        self.assertEqual(
            self.disp.classify(DispatchRequest(prompt="히어로 visual polish 정리")),
            TaskType.VISUAL_POLISH,
        )

    def test_email_campaign_keyword(self) -> None:
        self.assertEqual(
            self.disp.classify(DispatchRequest(prompt="welcome email 캠페인 설계")),
            TaskType.EMAIL_CAMPAIGN,
        )

    def test_backend_keyword(self) -> None:
        self.assertEqual(
            self.disp.classify(DispatchRequest(prompt="users API schema 추가")),
            TaskType.BACKEND_FEATURE,
        )

    def test_falls_back_to_unknown(self) -> None:
        self.assertEqual(
            self.disp.classify(DispatchRequest(prompt="회의록 요약")),
            TaskType.UNKNOWN,
        )


class RoleSequenceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.disp = _build_full_pool()

    def test_tech_lead_starts_every_sequence(self) -> None:
        for task_type in TaskType:
            plan = self.disp.dispatch(DispatchRequest(prompt="x", task_type=task_type))
            self.assertEqual(plan.role_sequence[0], "tech-lead", task_type)

    def test_landing_page_executor_is_frontend(self) -> None:
        plan = self.disp.dispatch(DispatchRequest(prompt="x", task_type=TaskType.LANDING_PAGE))
        executor = plan.executor()
        self.assertIsNotNone(executor)
        self.assertEqual(executor.role, "frontend-engineer")
        self.assertTrue(executor.is_executor)

    def test_backend_feature_executor_is_backend(self) -> None:
        plan = self.disp.dispatch(DispatchRequest(prompt="x", task_type=TaskType.BACKEND_FEATURE))
        self.assertEqual(plan.executor().role, "backend-engineer")

    def test_unknown_executor_is_tech_lead(self) -> None:
        plan = self.disp.dispatch(DispatchRequest(prompt="회의록"))
        self.assertEqual(plan.executor().role, "tech-lead")

    def test_only_one_executor_per_plan(self) -> None:
        plan = self.disp.dispatch(DispatchRequest(prompt="x", task_type=TaskType.ONBOARDING_FLOW))
        executors = [a for a in plan.assignments if a.is_executor]
        self.assertEqual(len(executors), 1)


class WeightSelectionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.disp = _build_full_pool()

    def test_visual_polish_picks_gemini_for_designer(self) -> None:
        plan = self.disp.dispatch(DispatchRequest(prompt="x", task_type=TaskType.VISUAL_POLISH))
        designer = next(a for a in plan.assignments if a.role == "product-designer")
        self.assertEqual(designer.runner_id, "gemini")
        # base 9 + bonus 3 = 12
        self.assertEqual(designer.score, 12)

    def test_qa_test_picks_codex_for_qa(self) -> None:
        plan = self.disp.dispatch(DispatchRequest(prompt="x", task_type=TaskType.QA_TEST))
        qa = next(a for a in plan.assignments if a.role == "qa-engineer")
        self.assertEqual(qa.runner_id, "codex")
        # base 9 + bonus 2 = 11
        self.assertEqual(qa.score, 11)

    def test_runner_missing_from_pool_is_skipped(self) -> None:
        # Build pool that only has gemini + ollama
        factories = {
            "gemini": lambda config, hooks: _StaticRunner("gemini", "test", config=config, hooks=hooks),
            "ollama": lambda config, hooks: _StaticRunner("ollama", "test", config=config, hooks=hooks),
        }
        pool = build_participants_pool(REPO_ROOT, "engineering-agent", factories=factories)
        disp = Dispatcher(pool)

        plan = disp.dispatch(DispatchRequest(prompt="x", task_type=TaskType.BACKEND_FEATURE))
        backend = next(a for a in plan.assignments if a.role == "backend-engineer")
        self.assertIn(backend.runner_id, {"gemini", "ollama"})

    def test_weights_match_policy_doc(self) -> None:
        # Defensive: dispatcher's defaults must agree with role-weights-v0.md.
        self.assertEqual(ROLE_DEFAULT_WEIGHTS["product-designer"]["gemini"], 9)
        self.assertEqual(ROLE_DEFAULT_WEIGHTS["qa-engineer"]["codex"], 9)
        self.assertEqual(ROLE_DEFAULT_WEIGHTS["tech-lead"]["claude"], 9)


class ReferencesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.disp = _build_full_pool()

    def test_landing_page_references(self) -> None:
        plan = self.disp.dispatch(DispatchRequest(prompt="x", task_type=TaskType.LANDING_PAGE))
        self.assertEqual(
            plan.reference_sources,
            ("Wix Templates", "Awwwards", "Behance", "Pinterest Trends"),
        )

    def test_email_campaign_references(self) -> None:
        plan = self.disp.dispatch(DispatchRequest(prompt="x", task_type=TaskType.EMAIL_CAMPAIGN))
        self.assertIn("Really Good Emails", plan.reference_sources)
        self.assertIn("Meta Ad Library", plan.reference_sources)
        self.assertIn("TikTok Creative Center", plan.reference_sources)
        self.assertIn("Google Trends", plan.reference_sources)

    def test_backend_feature_has_no_visual_references(self) -> None:
        plan = self.disp.dispatch(DispatchRequest(prompt="x", task_type=TaskType.BACKEND_FEATURE))
        self.assertEqual(plan.reference_sources, ())

    def test_table_matches_spec(self) -> None:
        self.assertEqual(
            TASK_REFERENCE_SOURCES[TaskType.VISUAL_POLISH],
            ("Pinterest Trends", "Notefolio", "Behance", "Canva Design School"),
        )


class WriteGateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.disp = _build_full_pool()

    def test_no_write_requested_no_block(self) -> None:
        plan = self.disp.dispatch(
            DispatchRequest(prompt="x", task_type=TaskType.BACKEND_FEATURE, write_requested=False)
        )
        self.assertFalse(plan.write_blocked)
        self.assertIsNone(plan.write_block_reason)

    def test_write_without_approval_is_blocked(self) -> None:
        plan = self.disp.dispatch(
            DispatchRequest(
                prompt="x",
                task_type=TaskType.BACKEND_FEATURE,
                write_requested=True,
                user_approved=False,
            )
        )
        self.assertTrue(plan.write_blocked)
        self.assertIn("backend-engineer", plan.write_block_reason)

    def test_write_with_approval_passes(self) -> None:
        plan = self.disp.dispatch(
            DispatchRequest(
                prompt="x",
                task_type=TaskType.BACKEND_FEATURE,
                write_requested=True,
                user_approved=True,
            )
        )
        self.assertFalse(plan.write_blocked)


class RankingSlotTestCase(unittest.TestCase):
    def test_ranking_signal_slot_unused_by_default(self) -> None:
        disp = _build_full_pool()
        plan = disp.dispatch(DispatchRequest(prompt="x", task_type=TaskType.BACKEND_FEATURE))
        backend = next(a for a in plan.assignments if a.role == "backend-engineer")
        # claude defaults to 9, codex with backend bonus = 8; claude wins.
        self.assertEqual(backend.runner_id, "claude")
        self.assertEqual(backend.score, 9)

    def test_ranking_signal_applied_when_weight_positive(self) -> None:
        class FavorOllama:
            def score(self, role, runner_id, request):
                return 10.0 if runner_id == "ollama" else 0.0

        disp = _build_full_pool()
        disp.ranking_signal = FavorOllama()
        plan = disp.dispatch(
            DispatchRequest(prompt="x", task_type=TaskType.BACKEND_FEATURE),
            ranking_weight=1.0,
        )
        backend = next(a for a in plan.assignments if a.role == "backend-engineer")
        # ollama base 3 + ranking 10 = 13 — beats claude 9.
        self.assertEqual(backend.runner_id, "ollama")


class RenderTestCase(unittest.TestCase):
    def test_summary_includes_key_lines(self) -> None:
        disp = _build_full_pool()
        plan = disp.dispatch(
            DispatchRequest(
                prompt="x",
                task_type=TaskType.LANDING_PAGE,
                write_requested=True,
                user_approved=False,
            )
        )
        summary = render_plan_summary(plan)
        self.assertIn("task_type: landing-page", summary)
        self.assertIn("[exec] frontend-engineer", summary)
        self.assertIn("references:", summary)
        self.assertIn("write blocked", summary)


if __name__ == "__main__":
    unittest.main()
