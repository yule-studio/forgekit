from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.research_loop import (
    ForumPublicationOutcome,
    ResearchLoopOutcome,
    RoleAssignment,
    RoleLoopOutput,
    publish_research_loop_to_forum,
    run_research_loop,
)
from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState
from yule_orchestrator.discord.research_forum import (
    PREFIX_DECISION,
    PREFIX_RESEARCH,
    PREFIX_REFERENCE,
    ResearchForumContext,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _session(
    *,
    task_type: str = "onboarding-flow",
    role_sequence=(
        "tech-lead",
        "product-designer",
        "frontend-engineer",
        "qa-engineer",
    ),
    executor_role: str = "frontend-engineer",
    state: WorkflowState = WorkflowState.APPROVED,
    write_requested: bool = False,
    write_blocked_reason: str = "",
    prompt: str = "온보딩 step 2 정리",
) -> WorkflowSession:
    now = datetime(2026, 4, 30, 9, 0)
    return WorkflowSession(
        session_id="sid-1",
        prompt=prompt,
        task_type=task_type,
        state=state,
        created_at=now,
        updated_at=now,
        role_sequence=role_sequence,
        executor_role=executor_role,
        executor_runner="codex",
        write_requested=write_requested,
        write_blocked_reason=write_blocked_reason,
        channel_id=10,
    )


def _image_attachment(**overrides) -> SimpleNamespace:
    base = dict(
        filename="hero.png",
        url="https://cdn/x.png",
        content_type="image/png",
        id="att-1",
        size=1024,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class RunResearchLoopHappyPathTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.session = _session()
        self.message = (
            "온보딩 step 2 너무 길어요. https://stripe.com/pricing 참고하고 "
            "https://github.com/yule-studio/repo/issues/144 도 보고 싶습니다."
        )

    def test_collects_diverse_source_types(self) -> None:
        outcome = run_research_loop(
            session=self.session,
            message_text=self.message,
            attachments=[_image_attachment()],
            posted_at=datetime(2026, 4, 30, 9, 0),
        )
        self.assertFalse(outcome.insufficient)
        self.assertIsNotNone(outcome.research_pack)
        types = [
            (s.extra or {}).get("source_type") for s in outcome.research_pack.sources
        ]
        self.assertIn("user_message", types)
        self.assertIn("url", types)
        self.assertIn("github_issue", types)
        self.assertIn("image_reference", types)

    def test_runs_deliberation_for_every_role(self) -> None:
        outcome = run_research_loop(
            session=self.session,
            message_text=self.message,
            attachments=[_image_attachment()],
        )
        roles = [output.role for output in outcome.role_outputs]
        self.assertEqual(
            roles,
            [
                "engineering-agent/tech-lead",
                "engineering-agent/product-designer",
                "engineering-agent/frontend-engineer",
                "engineering-agent/qa-engineer",
            ],
        )

    def test_synthesis_present_with_consensus(self) -> None:
        outcome = run_research_loop(
            session=self.session,
            message_text=self.message,
            attachments=[_image_attachment()],
        )
        self.assertIsNotNone(outcome.synthesis)
        self.assertIn("onboarding-flow", outcome.synthesis.consensus)
        self.assertIn("**[tech-lead 종합]**", outcome.synthesis_text or "")

    def test_assignments_flag_executor(self) -> None:
        outcome = run_research_loop(
            session=self.session,
            message_text=self.message,
            attachments=[_image_attachment()],
        )
        executors = [a for a in outcome.assignments if a.is_executor]
        self.assertEqual(len(executors), 1)
        self.assertEqual(executors[0].role, "frontend-engineer")

    def test_role_comment_kwargs_carry_evidence(self) -> None:
        outcome = run_research_loop(
            session=self.session,
            message_text=self.message,
            attachments=[_image_attachment()],
        )
        designer = next(
            o for o in outcome.role_outputs if o.role.endswith("product-designer")
        )
        materials = designer.comment_kwargs.get("collected_materials")
        self.assertTrue(materials)
        joined = "\n".join(materials)
        self.assertIn("[image_reference]", joined)


class RunResearchLoopInsufficientTestCase(unittest.TestCase):
    def test_short_message_with_no_links_triggers_followup(self) -> None:
        outcome = run_research_loop(
            session=_session(),
            message_text="짧음",
            attachments=(),
        )
        self.assertTrue(outcome.insufficient)
        self.assertIsNotNone(outcome.follow_up_prompt)
        self.assertIn("자료가 부족합니다", outcome.follow_up_prompt or "")
        self.assertIsNone(outcome.research_pack)
        self.assertEqual(outcome.role_outputs, ())
        self.assertIsNone(outcome.synthesis)

    def test_landing_page_without_visual_reference_is_insufficient(self) -> None:
        outcome = run_research_loop(
            session=_session(task_type="landing-page"),
            message_text=(
                "랜딩 페이지에서 hero 카피를 다시 짜고 싶은데 step copy 패턴을 "
                "한 번 보고 싶어요. 우리 제품 톤은 유지하면서요."
            ),
            attachments=(),
        )
        self.assertTrue(outcome.insufficient)
        self.assertIn("시각 reference", outcome.follow_up_prompt or "")


class RunResearchLoopProfileFilteringTestCase(unittest.TestCase):
    def test_designer_evidence_prefers_image_over_url(self) -> None:
        outcome = run_research_loop(
            session=_session(),
            message_text="https://example.com/notes 참고하고 싶음",
            attachments=[_image_attachment()],
        )
        designer = next(
            o for o in outcome.role_outputs if o.role.endswith("product-designer")
        )
        first_line = designer.comment_kwargs["collected_materials"][0]
        self.assertTrue(first_line.startswith("[image_reference]"))

    def test_backend_evidence_prefers_official_docs(self) -> None:
        outcome = run_research_loop(
            session=_session(
                task_type="backend-feature",
                role_sequence=(
                    "tech-lead",
                    "backend-engineer",
                    "qa-engineer",
                ),
                executor_role="backend-engineer",
            ),
            message_text=(
                "https://docs.python.org/3/library/asyncio-task.html 보고 "
                "https://github.com/yule-studio/repo/issues/144 도 검토 필요"
            ),
        )
        self.assertFalse(outcome.insufficient, msg=outcome.follow_up_prompt)
        backend = next(
            o for o in outcome.role_outputs if o.role.endswith("backend-engineer")
        )
        first_line = backend.comment_kwargs["collected_materials"][0]
        self.assertTrue(
            first_line.startswith("[official_docs]"),
            f"expected official_docs first, got {first_line!r}",
        )


class RunResearchLoopApprovalTestCase(unittest.TestCase):
    def test_pending_write_surfaces_in_synthesis(self) -> None:
        session = _session(
            task_type="backend-feature",
            role_sequence=(
                "tech-lead",
                "backend-engineer",
                "qa-engineer",
            ),
            executor_role="backend-engineer",
            state=WorkflowState.INTAKE,
            write_requested=True,
            write_blocked_reason="user_approved=False",
        )
        outcome = run_research_loop(
            session=session,
            message_text=(
                "https://docs.python.org/3/library/asyncio.html 기준으로 "
                "백엔드 작업 승인 필요. "
                "https://github.com/yule-studio/repo/issues/144 도 참조."
            ),
        )
        self.assertIsNotNone(outcome.synthesis)
        self.assertTrue(outcome.synthesis.approval_required)
        self.assertIn("user_approved", outcome.synthesis.approval_reason or "")


class PublishResearchLoopToForumTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.session = _session()
        self.outcome = run_research_loop(
            session=self.session,
            message_text=(
                "온보딩 step 2 너무 길어요. https://stripe.com/pricing 참고하고 "
                "https://github.com/yule-studio/repo/issues/144 도 보고 싶습니다."
            ),
            attachments=[_image_attachment()],
        )

    def _stub_thread_fn(self, captured):
        async def thread_fn(**kwargs):
            captured.setdefault("threads", []).append(kwargs)
            return {"id": 9001, "url": "https://discord.com/threads/9001"}

        return thread_fn

    def _stub_post_fn(self, captured, ids=None):
        ids = ids or iter(range(5000, 5100))

        async def post_fn(**kwargs):
            captured.setdefault("posts", []).append(kwargs)
            return {"id": next(ids)}

        return post_fn

    def test_posts_thread_role_comments_and_decision(self) -> None:
        captured: dict = {}
        outcome = _run(
            publish_research_loop_to_forum(
                self.outcome,
                forum_context=ResearchForumContext(channel_id=42, channel_name="운영-리서치"),
                create_thread_fn=self._stub_thread_fn(captured),
                post_message_fn=self._stub_post_fn(captured),
                posted_by="bot:engineering-agent",
            )
        )
        self.assertTrue(outcome.posted)
        self.assertEqual(len(captured.get("threads", [])), 1)
        # 4 role comments + 1 decision comment.
        self.assertEqual(len(captured.get("posts", [])), 5)
        self.assertEqual(set(outcome.role_comments.keys()), {
            "engineering-agent/tech-lead",
            "engineering-agent/product-designer",
            "engineering-agent/frontend-engineer",
            "engineering-agent/qa-engineer",
        })
        self.assertIsNotNone(outcome.decision_comment)
        self.assertTrue(outcome.decision_comment.posted)
        self.assertIn(PREFIX_DECISION, outcome.decision_comment.body or "")

    def test_thread_prefix_defaults_to_reference_for_visual_packs(self) -> None:
        captured: dict = {}
        _run(
            publish_research_loop_to_forum(
                self.outcome,
                forum_context=ResearchForumContext(channel_id=42),
                create_thread_fn=self._stub_thread_fn(captured),
                post_message_fn=self._stub_post_fn(captured),
            )
        )
        thread_call = captured["threads"][0]
        self.assertTrue(thread_call["name"].startswith(f"{PREFIX_REFERENCE} "))

    def test_member_bots_mode_posts_only_research_kickoff(self) -> None:
        captured: dict = {}
        result = _run(
            publish_research_loop_to_forum(
                self.outcome,
                forum_context=ResearchForumContext(channel_id=42),
                create_thread_fn=self._stub_thread_fn(captured),
                post_message_fn=self._stub_post_fn(captured),
                comment_mode="member-bots",
            )
        )

        self.assertTrue(result.posted)
        self.assertEqual(result.role_comments, {})
        self.assertIsNone(result.decision_comment)
        self.assertIsNotNone(result.kickoff_comment)
        self.assertTrue(result.kickoff_comment.posted)
        posts = captured.get("posts", [])
        self.assertEqual(len(posts), 1)
        self.assertIn("[research-open:sid-1]", posts[0]["content"])
        self.assertNotIn("[research-turn:", posts[0]["content"])

    def test_publish_includes_collection_summary_when_provided(self) -> None:
        captured: dict = {}
        collection = SimpleNamespace(
            collector_name="mock",
            query="온보딩 UX reference",
            auto_collected_count=2,
        )
        _run(
            publish_research_loop_to_forum(
                self.outcome,
                forum_context=ResearchForumContext(channel_id=42),
                create_thread_fn=self._stub_thread_fn(captured),
                post_message_fn=self._stub_post_fn(captured),
                collection_outcome=collection,
                collection_role="engineering-agent/product-designer",
            )
        )

        thread_call = captured["threads"][0]
        self.assertIn("1차 자료 정리", thread_call["content"])
        self.assertIn("기본 검색(mock)", thread_call["content"])

    def test_run_research_loop_can_reuse_precollected_pack(self) -> None:
        collection = SimpleNamespace(
            collector_name="mock",
            query="precollected",
            auto_collected_count=3,
        )

        outcome = run_research_loop(
            session=self.session,
            message_text="짧",
            research_pack=self.outcome.research_pack,
            collection=collection,
        )

        self.assertFalse(outcome.insufficient)
        self.assertIs(outcome.research_pack, self.outcome.research_pack)
        self.assertIs(outcome.collection, collection)
        self.assertGreater(len(outcome.role_outputs), 0)

    def test_skipped_when_outcome_insufficient(self) -> None:
        thin = run_research_loop(
            session=_session(),
            message_text="짧",
            attachments=(),
        )
        captured: dict = {}
        result = _run(
            publish_research_loop_to_forum(
                thin,
                forum_context=ResearchForumContext(channel_id=1),
                create_thread_fn=self._stub_thread_fn(captured),
                post_message_fn=self._stub_post_fn(captured),
            )
        )
        self.assertFalse(result.posted)
        self.assertIsNone(result.thread)
        self.assertEqual(result.skipped_reason, "insufficient research")
        self.assertNotIn("threads", captured)
        self.assertNotIn("posts", captured)

    def test_role_comments_and_decision_skipped_when_thread_unposted(self) -> None:
        async def fail_thread(**_):
            raise RuntimeError("403 forbidden")

        captured: dict = {}
        result = _run(
            publish_research_loop_to_forum(
                self.outcome,
                forum_context=ResearchForumContext(channel_id=1),
                create_thread_fn=fail_thread,
                post_message_fn=self._stub_post_fn(captured),
            )
        )
        self.assertFalse(result.posted)
        self.assertEqual(result.role_comments, {})
        self.assertIsNone(result.decision_comment)
        self.assertIsNotNone(result.thread)
        self.assertIn("403", result.thread.error or "")
        self.assertIsNotNone(result.thread.fallback_markdown)


class RunnerInjectionTestCase(unittest.TestCase):
    def test_runner_take_is_used(self) -> None:
        from yule_orchestrator.agents.deliberation import (
            DeliberationContext,
            FrontendEngineerTake,
        )

        custom = FrontendEngineerTake(
            role="engineering-agent/frontend-engineer",
            ui_components=("custom-component",),
            user_flow="custom flow",
            perspective="custom perspective",
            evidence=("[official_docs] runner 이 직접 채운 evidence",),
            risks=("custom risk",),
            next_actions=("runner-driven action",),
        )

        def runner(ctx: DeliberationContext):
            if ctx.role.endswith("frontend-engineer"):
                return custom
            return None

        outcome = run_research_loop(
            session=_session(
                task_type="frontend-feature",
                role_sequence=(
                    "tech-lead",
                    "frontend-engineer",
                    "qa-engineer",
                ),
                executor_role="frontend-engineer",
            ),
            message_text=(
                "https://docs.python.org/3/library/asyncio.html 보고 "
                "https://github.com/yule-studio/repo/issues/144 검토"
            ),
            attachments=[_image_attachment()],
            runner_fn=runner,
        )
        frontend = next(
            o for o in outcome.role_outputs if o.role.endswith("frontend-engineer")
        )
        self.assertIs(frontend.take, custom)
        self.assertIn("runner 이 직접 채운", frontend.comment_kwargs["collected_materials"][0])


if __name__ == "__main__":
    unittest.main()
