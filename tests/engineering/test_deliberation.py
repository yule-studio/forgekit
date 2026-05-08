from __future__ import annotations

import unittest
from datetime import datetime

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.deliberation import (
    BackendEngineerTake,
    DeliberationContext,
    FrontendEngineerTake,
    KNOWN_SOURCE_TYPES,
    ProductDesignerTake,
    QaEngineerTake,
    ROLE_RESEARCH_PROFILES,
    SOURCE_TYPE_CODE_CONTEXT,
    SOURCE_TYPE_COMMUNITY_SIGNAL,
    SOURCE_TYPE_DESIGN_REFERENCE,
    SOURCE_TYPE_GITHUB_ISSUE,
    SOURCE_TYPE_GITHUB_PR,
    SOURCE_TYPE_IMAGE_REFERENCE,
    SOURCE_TYPE_OFFICIAL_DOCS,
    SOURCE_TYPE_URL,
    SOURCE_TYPE_USER_MESSAGE,
    TechLeadOpening,
    TechLeadSynthesis,
    collected_by_role,
    evidence_lines_for_role,
    filter_pack_for_role,
    render_role_take,
    render_synthesis,
    run_role_deliberation,
    source_meta,
    source_type,
    synthesis_from_dict,
    synthesis_to_dict,
    synthesize,
)
from yule_orchestrator.agents.research.pack import (
    ResearchAttachment,
    ResearchPack,
    ResearchSource,
    pack_from_discord_message,
)
from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState
from yule_orchestrator.discord.engineering_team_runtime import (
    DeliberationLoopResult,
    DeliberationTurnRecord,
    deliberation_role_sequence,
    deliberation_role_turn,
    run_deliberation_loop,
    synthesize_thread,
)


def _session(
    *,
    state: WorkflowState = WorkflowState.APPROVED,
    write_requested: bool = False,
    write_blocked_reason: str = "",
    references_user=(),
    references_suggested=(),
    role_sequence=("tech-lead", "product-designer", "frontend-engineer", "qa-engineer"),
    executor_role: str = "frontend-engineer",
    task_type: str = "landing-page",
    prompt: str = "새 랜딩페이지 hero 섹션 정리",
) -> WorkflowSession:
    now = datetime(2026, 4, 30, 9, 0)
    return WorkflowSession(
        session_id="abc123",
        prompt=prompt,
        task_type=task_type,
        state=state,
        created_at=now,
        updated_at=now,
        role_sequence=role_sequence,
        executor_role=executor_role,
        executor_runner="codex",
        references_user=references_user,
        references_suggested=references_suggested,
        write_requested=write_requested,
        write_blocked_reason=write_blocked_reason,
    )


class FallbackTechLeadTestCase(unittest.TestCase):
    def test_fallback_includes_breakdown_and_dependencies(self) -> None:
        session = _session(references_user=("https://example.com/x",))
        take = run_role_deliberation(
            DeliberationContext(session=session, role="engineering-agent/tech-lead")
        )
        self.assertIsInstance(take, TechLeadOpening)
        self.assertTrue(take.task_breakdown)
        self.assertTrue(any("우선" in d for d in take.dependencies))

    def test_fallback_decisions_when_write_pending(self) -> None:
        session = _session(
            state=WorkflowState.INTAKE,
            write_requested=True,
            write_blocked_reason="write requires approval",
        )
        take = run_role_deliberation(
            DeliberationContext(session=session, role="engineering-agent/tech-lead")
        )
        self.assertIsInstance(take, TechLeadOpening)
        self.assertTrue(any("승인" in d for d in take.decisions_needed))


class FallbackProductDesignerTestCase(unittest.TestCase):
    def test_uses_pack_urls_when_available(self) -> None:
        pack = pack_from_discord_message(
            title="Stripe pricing",
            content="https://stripe.com/pricing 참고",
            channel_id=1,
            message_id=2,
        )
        take = run_role_deliberation(
            DeliberationContext(
                session=_session(),
                role="engineering-agent/product-designer",
                research_pack=pack,
            )
        )
        self.assertIsInstance(take, ProductDesignerTake)
        self.assertTrue(any("stripe.com" in s for s in take.reference_summary))

    def test_falls_back_to_user_refs_without_pack(self) -> None:
        take = run_role_deliberation(
            DeliberationContext(
                session=_session(references_user=("https://user.example/a",)),
                role="engineering-agent/product-designer",
            )
        )
        self.assertIsInstance(take, ProductDesignerTake)
        self.assertTrue(any("user.example" in s for s in take.reference_summary))

    def test_flags_risk_when_no_reference(self) -> None:
        take = run_role_deliberation(
            DeliberationContext(
                session=_session(),
                role="engineering-agent/product-designer",
            )
        )
        self.assertTrue(take.risks)


class FallbackOtherRolesTestCase(unittest.TestCase):
    def test_backend(self) -> None:
        take = run_role_deliberation(
            DeliberationContext(session=_session(), role="engineering-agent/backend-engineer")
        )
        self.assertIsInstance(take, BackendEngineerTake)
        self.assertTrue(take.risks)

    def test_frontend(self) -> None:
        take = run_role_deliberation(
            DeliberationContext(session=_session(), role="engineering-agent/frontend-engineer")
        )
        self.assertIsInstance(take, FrontendEngineerTake)
        self.assertTrue(take.ui_components)

    def test_qa(self) -> None:
        take = run_role_deliberation(
            DeliberationContext(session=_session(), role="engineering-agent/qa-engineer")
        )
        self.assertIsInstance(take, QaEngineerTake)
        self.assertTrue(take.acceptance_criteria)

    def test_unknown_role_returns_generic_take(self) -> None:
        take = run_role_deliberation(
            DeliberationContext(session=_session(), role="design-agent/illustrator")
        )
        self.assertIsInstance(take, TechLeadOpening)
        self.assertEqual(take.role, "design-agent/illustrator")


class RunnerInjectionTestCase(unittest.TestCase):
    def test_runner_structured_take_used(self) -> None:
        custom = ProductDesignerTake(
            reference_summary=("custom: a", "custom: b"),
            ux_direction="custom UX",
            visual_direction="custom visual",
        )

        def runner(_ctx: DeliberationContext) -> ProductDesignerTake:
            return custom

        take = run_role_deliberation(
            DeliberationContext(session=_session(), role="engineering-agent/product-designer"),
            runner_fn=runner,
        )
        self.assertEqual(take.ux_direction, "custom UX")
        self.assertEqual(take.reference_summary, ("custom: a", "custom: b"))

    def test_runner_failure_falls_back(self) -> None:
        def boom(_ctx: DeliberationContext):
            raise RuntimeError("backend down")

        take = run_role_deliberation(
            DeliberationContext(session=_session(), role="engineering-agent/backend-engineer"),
            runner_fn=boom,
        )
        self.assertIsInstance(take, BackendEngineerTake)
        self.assertTrue(take.risks)

    def test_runner_returning_none_falls_back(self) -> None:
        def empty(_ctx: DeliberationContext):
            return None

        take = run_role_deliberation(
            DeliberationContext(session=_session(), role="engineering-agent/qa-engineer"),
            runner_fn=empty,
        )
        self.assertIsInstance(take, QaEngineerTake)


class SynthesisTestCase(unittest.TestCase):
    def _basic_takes(self):
        return (
            run_role_deliberation(
                DeliberationContext(session=_session(), role="engineering-agent/tech-lead")
            ),
            run_role_deliberation(
                DeliberationContext(session=_session(), role="engineering-agent/product-designer")
            ),
            run_role_deliberation(
                DeliberationContext(session=_session(), role="engineering-agent/frontend-engineer")
            ),
            run_role_deliberation(
                DeliberationContext(session=_session(), role="engineering-agent/qa-engineer")
            ),
        )

    def test_synthesis_collects_todos_open_research_decisions(self) -> None:
        synth = synthesize(_session(), self._basic_takes())
        self.assertIsInstance(synth, TechLeadSynthesis)
        self.assertTrue(synth.consensus)
        self.assertTrue(synth.todos)
        # No pack → open research flagged.
        self.assertTrue(synth.open_research)

    def test_synthesis_marks_approval_required(self) -> None:
        session = _session(
            state=WorkflowState.INTAKE,
            write_requested=True,
            write_blocked_reason="user_approved=False",
        )
        synth = synthesize(session, self._basic_takes())
        self.assertTrue(synth.approval_required)
        self.assertIn("user_approved", synth.approval_reason or "")

    def test_synthesis_no_approval_when_already_approved(self) -> None:
        session = _session(
            state=WorkflowState.APPROVED,
            write_requested=True,
            write_blocked_reason="",
        )
        synth = synthesize(session, self._basic_takes())
        self.assertFalse(synth.approval_required)

    def test_synthesis_with_full_reference_pack(self) -> None:
        pack = ResearchPack(
            title="bundle",
            primary_url="https://a",
            sources=(),
        )
        # urls from primary_url alone = 1 → still less than 3
        synth = synthesize(_session(), self._basic_takes(), research_pack=pack)
        self.assertTrue(any("3건" in m for m in synth.open_research))


class RenderTestCase(unittest.TestCase):
    def test_render_role_take_includes_header(self) -> None:
        take = run_role_deliberation(
            DeliberationContext(session=_session(), role="engineering-agent/tech-lead")
        )
        text = render_role_take(take)
        self.assertIn("**[tech-lead]**", text)
        self.assertIn("작업 분해", text)

    def test_render_synthesis_blocks(self) -> None:
        synth = TechLeadSynthesis(
            consensus="합의안 한 줄",
            todos=("todo 1",),
            open_research=("연구 항목",),
            user_decisions_needed=("결정 항목",),
            approval_required=True,
            approval_reason="이유",
        )
        text = render_synthesis(synth)
        self.assertIn("합의안", text)
        self.assertIn("해야 할 일", text)
        self.assertIn("더 조사할 것", text)
        self.assertIn("사용자 결정 필요", text)
        self.assertIn("승인 필요: yes", text)


class SynthesisRoundTripTestCase(unittest.TestCase):
    def test_to_dict_then_from_dict_preserves_fields(self) -> None:
        original = TechLeadSynthesis(
            consensus="합의안 본문",
            todos=("todo a", "todo b"),
            open_research=("자료 보강",),
            user_decisions_needed=("결정 1",),
            approval_required=True,
            approval_reason="쓰기 작업 확인 필요",
        )
        restored = synthesis_from_dict(synthesis_to_dict(original))
        self.assertEqual(restored.consensus, original.consensus)
        self.assertEqual(tuple(restored.todos), original.todos)
        self.assertEqual(tuple(restored.open_research), original.open_research)
        self.assertEqual(
            tuple(restored.user_decisions_needed), original.user_decisions_needed
        )
        self.assertTrue(restored.approval_required)
        self.assertEqual(restored.approval_reason, original.approval_reason)

    def test_payload_carries_version_marker(self) -> None:
        payload = synthesis_to_dict(TechLeadSynthesis(consensus="x"))
        self.assertEqual(payload["v"], 1)

    def test_from_dict_handles_missing_lists(self) -> None:
        restored = synthesis_from_dict({"consensus": "only"})
        self.assertEqual(restored.consensus, "only")
        self.assertEqual(tuple(restored.todos), ())
        self.assertEqual(tuple(restored.open_research), ())
        self.assertEqual(tuple(restored.user_decisions_needed), ())
        self.assertFalse(restored.approval_required)
        self.assertIsNone(restored.approval_reason)

    def test_from_dict_coerces_non_string_consensus(self) -> None:
        restored = synthesis_from_dict({"consensus": 123})
        self.assertEqual(restored.consensus, "123")

    def test_from_dict_blank_approval_reason_becomes_none(self) -> None:
        restored = synthesis_from_dict(
            {"consensus": "x", "approval_required": True, "approval_reason": "   "}
        )
        self.assertTrue(restored.approval_required)
        self.assertIsNone(restored.approval_reason)


class RuntimeIntegrationTestCase(unittest.TestCase):
    def test_deliberation_role_turn_returns_take_and_text(self) -> None:
        take, text = deliberation_role_turn(
            _session(),
            "engineering-agent/qa-engineer",
        )
        self.assertIsInstance(take, QaEngineerTake)
        self.assertIn("**[qa-engineer]**", text)

    def test_synthesize_thread_uses_deliberation_outputs(self) -> None:
        session = _session()
        takes = [
            deliberation_role_turn(session, role)[0]
            for role in (
                "engineering-agent/tech-lead",
                "engineering-agent/product-designer",
                "engineering-agent/frontend-engineer",
                "engineering-agent/qa-engineer",
            )
        ]
        synth, text = synthesize_thread(session, takes)
        self.assertIsInstance(synth, TechLeadSynthesis)
        self.assertIn("tech-lead 종합", text)


def _source(
    *,
    title: str = "src",
    url: str | None = "https://example.com/x",
    source_type_value: str | None = None,
    why_relevant: str | None = None,
    risk_or_limit: str | None = None,
    confidence: float | None = None,
    collected_by: str | None = None,
    summary: str | None = None,
    attachments=(),
    author_role: str | None = None,
) -> ResearchSource:
    extra: dict = {}
    if source_type_value is not None:
        extra["source_type"] = source_type_value
    if why_relevant is not None:
        extra["why_relevant"] = why_relevant
    if risk_or_limit is not None:
        extra["risk_or_limit"] = risk_or_limit
    if confidence is not None:
        extra["confidence"] = confidence
    if collected_by is not None:
        extra["collected_by_role"] = collected_by
    return ResearchSource(
        source_url=url,
        title=title,
        summary=summary,
        author_role=author_role,
        attachments=tuple(attachments),
        extra=extra,
    )


def _pack(*sources: ResearchSource, title: str = "deliberation pack") -> ResearchPack:
    primary = next((s.source_url for s in sources if s.source_url), None)
    return ResearchPack(
        title=title,
        primary_url=primary,
        sources=sources,
    )


class SourceTypeDetectionTestCase(unittest.TestCase):
    def test_explicit_source_type_extra_wins(self) -> None:
        src = _source(source_type_value=SOURCE_TYPE_OFFICIAL_DOCS)
        self.assertEqual(source_type(src), SOURCE_TYPE_OFFICIAL_DOCS)

    def test_image_attachment_implies_image_reference(self) -> None:
        src = _source(
            url=None,
            attachments=(ResearchAttachment(kind="image", url="cdn://img-1"),),
        )
        self.assertEqual(source_type(src), SOURCE_TYPE_IMAGE_REFERENCE)

    def test_github_issue_url_inferred(self) -> None:
        src = _source(url="https://github.com/yule-studio/agent/issues/42")
        self.assertEqual(source_type(src), SOURCE_TYPE_GITHUB_ISSUE)

    def test_github_pull_url_inferred(self) -> None:
        src = _source(url="https://github.com/yule-studio/agent/pull/99")
        self.assertEqual(source_type(src), SOURCE_TYPE_GITHUB_PR)

    def test_official_docs_url_inferred(self) -> None:
        src = _source(url="https://developer.mozilla.org/en/docs/Web/API/Fetch")
        self.assertEqual(source_type(src), SOURCE_TYPE_OFFICIAL_DOCS)

    def test_design_reference_url_inferred(self) -> None:
        src = _source(url="https://www.notefolio.net/portfolio/abc")
        self.assertEqual(source_type(src), SOURCE_TYPE_DESIGN_REFERENCE)

    def test_community_signal_url_inferred(self) -> None:
        src = _source(url="https://www.reddit.com/r/webdev/comments/x")
        self.assertEqual(source_type(src), SOURCE_TYPE_COMMUNITY_SIGNAL)

    def test_no_url_and_no_extra_returns_user_message(self) -> None:
        src = _source(url=None)
        self.assertEqual(source_type(src), SOURCE_TYPE_USER_MESSAGE)

    def test_unknown_url_returns_url(self) -> None:
        src = _source(url="https://something-private.example/x")
        self.assertEqual(source_type(src), SOURCE_TYPE_URL)


class SourceMetaTestCase(unittest.TestCase):
    def test_meta_includes_required_fields(self) -> None:
        src = _source(
            url="https://example.com/x",
            source_type_value=SOURCE_TYPE_OFFICIAL_DOCS,
            why_relevant="React 18 fetch streaming 패턴",
            risk_or_limit="공식 문서 v18 한정",
            confidence=0.8,
            collected_by="engineering-agent/backend-engineer",
            summary="streaming SSR 가이드",
        )
        meta = source_meta(src)
        self.assertEqual(
            set(meta.keys()),
            {
                "title",
                "url",
                "attachment_id",
                "source_type",
                "collected_by_role",
                "summary",
                "why_relevant",
                "risk_or_limit",
                "collected_at",
                "confidence",
            },
        )
        self.assertEqual(meta["source_type"], SOURCE_TYPE_OFFICIAL_DOCS)
        self.assertEqual(meta["confidence"], 0.8)
        self.assertEqual(
            meta["collected_by_role"], "engineering-agent/backend-engineer"
        )
        self.assertEqual(meta["why_relevant"], "React 18 fetch streaming 패턴")

    def test_meta_attachment_id_when_no_url(self) -> None:
        src = _source(
            url=None,
            attachments=(ResearchAttachment(kind="image", url="cdn://x.png"),),
        )
        meta = source_meta(src)
        self.assertEqual(meta["attachment_id"], "cdn://x.png")
        self.assertIsNone(meta["url"])

    def test_confidence_clamped_to_unit_range(self) -> None:
        meta_high = source_meta(_source(confidence=2.0))
        meta_low = source_meta(_source(confidence=-0.5))
        self.assertEqual(meta_high["confidence"], 1.0)
        self.assertEqual(meta_low["confidence"], 0.0)

    def test_collected_by_role_falls_back_to_author(self) -> None:
        src = _source(author_role="engineering-agent/qa-engineer")
        self.assertEqual(
            collected_by_role(src), "engineering-agent/qa-engineer"
        )


class ResearchProfileCatalogTestCase(unittest.TestCase):
    def test_known_source_types_includes_all_required_kinds(self) -> None:
        required = {
            "user_message",
            "url",
            "web_result",
            "image_reference",
            "file_attachment",
            "github_issue",
            "github_pr",
            "code_context",
            "official_docs",
            "community_signal",
            "design_reference",
        }
        self.assertTrue(required.issubset(set(KNOWN_SOURCE_TYPES)))

    def test_each_engineering_role_has_a_profile(self) -> None:
        for role in (
            "tech-lead",
            "product-designer",
            "backend-engineer",
            "frontend-engineer",
            "qa-engineer",
        ):
            self.assertIn(role, ROLE_RESEARCH_PROFILES)
            self.assertGreater(len(ROLE_RESEARCH_PROFILES[role]), 0)

    def test_product_designer_prioritizes_visual_types(self) -> None:
        profile = ROLE_RESEARCH_PROFILES["product-designer"]
        self.assertEqual(profile[0], SOURCE_TYPE_IMAGE_REFERENCE)
        self.assertIn(SOURCE_TYPE_DESIGN_REFERENCE, profile[:3])

    def test_backend_engineer_prioritizes_official_and_code(self) -> None:
        profile = ROLE_RESEARCH_PROFILES["backend-engineer"]
        self.assertEqual(profile[0], SOURCE_TYPE_OFFICIAL_DOCS)
        self.assertIn(SOURCE_TYPE_CODE_CONTEXT, profile[:3])

    def test_frontend_engineer_prioritizes_docs_and_design(self) -> None:
        profile = ROLE_RESEARCH_PROFILES["frontend-engineer"]
        self.assertEqual(profile[0], SOURCE_TYPE_OFFICIAL_DOCS)
        self.assertIn(SOURCE_TYPE_DESIGN_REFERENCE, profile[:3])

    def test_qa_engineer_prioritizes_issues_and_community(self) -> None:
        profile = ROLE_RESEARCH_PROFILES["qa-engineer"]
        self.assertEqual(profile[0], SOURCE_TYPE_GITHUB_ISSUE)
        self.assertIn(SOURCE_TYPE_COMMUNITY_SIGNAL, profile[:3])


class FilterPackForRoleTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.image = _source(
            title="moodboard",
            url=None,
            attachments=(ResearchAttachment(kind="image", url="cdn://moodboard.png"),),
        )
        self.design_ref = _source(
            title="Notefolio",
            url="https://www.notefolio.net/portfolio/x",
        )
        self.docs = _source(
            title="React docs",
            url="https://react.dev/reference/react/useState",
        )
        self.code_ctx = _source(
            title="repo file",
            source_type_value=SOURCE_TYPE_CODE_CONTEXT,
            url=None,
        )
        self.issue = _source(
            title="bug report",
            url="https://github.com/yule-studio/agent/issues/12",
        )

    def _pack(self) -> ResearchPack:
        return _pack(self.image, self.design_ref, self.docs, self.code_ctx, self.issue)

    def test_product_designer_sees_visual_first(self) -> None:
        ordered = filter_pack_for_role(self._pack(), "engineering-agent/product-designer")
        self.assertEqual(ordered[0], self.image)
        self.assertEqual(ordered[1], self.design_ref)

    def test_backend_engineer_sees_docs_and_code_first(self) -> None:
        ordered = filter_pack_for_role(self._pack(), "engineering-agent/backend-engineer")
        self.assertEqual(ordered[0], self.docs)
        self.assertIn(self.code_ctx, ordered[:3])

    def test_frontend_engineer_sees_docs_and_design_first(self) -> None:
        ordered = filter_pack_for_role(self._pack(), "engineering-agent/frontend-engineer")
        self.assertEqual(ordered[0], self.docs)
        self.assertIn(self.design_ref, ordered[:3])

    def test_qa_engineer_sees_issue_first(self) -> None:
        ordered = filter_pack_for_role(self._pack(), "engineering-agent/qa-engineer")
        self.assertEqual(ordered[0], self.issue)

    def test_unknown_role_keeps_original_order(self) -> None:
        ordered = filter_pack_for_role(self._pack(), "unknown/role")
        self.assertEqual(
            ordered,
            (self.image, self.design_ref, self.docs, self.code_ctx, self.issue),
        )

    def test_empty_pack_returns_empty(self) -> None:
        self.assertEqual(filter_pack_for_role(None, "engineering-agent/tech-lead"), ())


class EvidenceLinesTestCase(unittest.TestCase):
    def test_evidence_includes_source_type_and_why(self) -> None:
        src = _source(
            url="https://react.dev/reference",
            why_relevant="hook 사용 패턴",
            title="React docs",
        )
        pack = _pack(src)
        lines = evidence_lines_for_role(pack, "engineering-agent/frontend-engineer")
        self.assertEqual(len(lines), 1)
        self.assertIn("[official_docs]", lines[0])
        self.assertIn("React docs", lines[0])
        self.assertIn("hook 사용 패턴", lines[0])

    def test_evidence_uses_attachment_id_when_url_missing(self) -> None:
        src = _source(
            url=None,
            attachments=(ResearchAttachment(kind="image", url="cdn://moodboard.png"),),
            title="moodboard",
        )
        lines = evidence_lines_for_role(_pack(src), "engineering-agent/product-designer")
        self.assertEqual(len(lines), 1)
        self.assertIn("cdn://moodboard.png", lines[0])
        self.assertIn("[image_reference]", lines[0])

    def test_evidence_respects_limit(self) -> None:
        sources = [_source(url=f"https://x{i}.example") for i in range(5)]
        lines = evidence_lines_for_role(
            _pack(*sources), "engineering-agent/tech-lead", limit=2
        )
        self.assertEqual(len(lines), 2)


class FourSectionContractTestCase(unittest.TestCase):
    """Each role take must populate 관점/근거/리스크/다음 행동."""

    def _pack_for_role(self, role: str) -> ResearchPack:
        if "product-designer" in role:
            return _pack(
                _source(
                    url=None,
                    attachments=(ResearchAttachment(kind="image", url="cdn://m.png"),),
                    title="moodboard",
                    why_relevant="히어로 톤 참고",
                ),
                _source(
                    url="https://www.notefolio.net/x",
                    title="Notefolio",
                    why_relevant="레이아웃 참고",
                ),
            )
        if "backend-engineer" in role:
            return _pack(
                _source(
                    url="https://docs.djangoproject.com/en/5.0/topics/db/queries/",
                    title="Django Queries",
                    why_relevant="ORM 패턴",
                ),
                _source(
                    title="services/auth.py",
                    url=None,
                    source_type_value=SOURCE_TYPE_CODE_CONTEXT,
                    why_relevant="기존 인증 흐름",
                ),
            )
        if "frontend-engineer" in role:
            return _pack(
                _source(
                    url="https://react.dev/reference/react/useState",
                    title="React docs",
                    why_relevant="hook 패턴",
                ),
                _source(
                    url="https://www.behance.net/gallery/x",
                    title="design ref",
                    why_relevant="레이아웃",
                ),
            )
        if "qa-engineer" in role:
            return _pack(
                _source(
                    url="https://github.com/yule-studio/agent/issues/12",
                    title="회귀 이슈",
                    why_relevant="유사 사례",
                ),
                _source(
                    url="https://www.reddit.com/r/webdev/x",
                    title="장애 사례",
                    why_relevant="실패 패턴",
                ),
            )
        return _pack(
            _source(url="https://example.com/x", title="generic", why_relevant="배경")
        )

    def _all_takes(self):
        for role in (
            "engineering-agent/tech-lead",
            "engineering-agent/product-designer",
            "engineering-agent/backend-engineer",
            "engineering-agent/frontend-engineer",
            "engineering-agent/qa-engineer",
        ):
            yield role, run_role_deliberation(
                DeliberationContext(
                    session=_session(),
                    role=role,
                    research_pack=self._pack_for_role(role),
                )
            )

    def test_perspective_populated_for_every_role(self) -> None:
        for role, take in self._all_takes():
            with self.subTest(role=role):
                self.assertTrue(take.perspective, msg=f"perspective empty for {role}")

    def test_risks_populated_for_every_role(self) -> None:
        for role, take in self._all_takes():
            with self.subTest(role=role):
                self.assertTrue(take.risks, msg=f"risks empty for {role}")

    def test_next_actions_populated_for_every_role(self) -> None:
        for role, take in self._all_takes():
            with self.subTest(role=role):
                self.assertTrue(
                    take.next_actions, msg=f"next_actions empty for {role}"
                )

    def test_evidence_populated_when_pack_has_role_relevant_sources(self) -> None:
        for role, take in self._all_takes():
            with self.subTest(role=role):
                self.assertTrue(take.evidence, msg=f"evidence empty for {role}")


class RolePriorityInFallbackTestCase(unittest.TestCase):
    def test_product_designer_evidence_lists_image_first(self) -> None:
        pack = _pack(
            _source(
                url=None,
                attachments=(
                    ResearchAttachment(kind="image", url="cdn://moodboard.png"),
                ),
                title="moodboard",
            ),
            _source(
                url="https://example.com/data-doc",
                title="data doc",
            ),
        )
        take = run_role_deliberation(
            DeliberationContext(
                session=_session(),
                role="engineering-agent/product-designer",
                research_pack=pack,
            )
        )
        self.assertTrue(take.evidence)
        self.assertIn("[image_reference]", take.evidence[0])

    def test_backend_engineer_evidence_lists_docs_first(self) -> None:
        pack = _pack(
            _source(
                url="https://www.behance.net/gallery/x",
                title="design ref",
            ),
            _source(
                url="https://docs.aws.amazon.com/iam/x",
                title="AWS IAM docs",
            ),
        )
        take = run_role_deliberation(
            DeliberationContext(
                session=_session(),
                role="engineering-agent/backend-engineer",
                research_pack=pack,
            )
        )
        self.assertTrue(take.evidence)
        self.assertIn("[official_docs]", take.evidence[0])

    def test_qa_engineer_evidence_lists_issue_first(self) -> None:
        pack = _pack(
            _source(
                url="https://www.notefolio.net/x", title="design ref"
            ),
            _source(
                url="https://github.com/yule-studio/agent/issues/12",
                title="회귀 이슈",
            ),
        )
        take = run_role_deliberation(
            DeliberationContext(
                session=_session(),
                role="engineering-agent/qa-engineer",
                research_pack=pack,
            )
        )
        self.assertTrue(take.evidence)
        self.assertIn("[github_issue]", take.evidence[0])


class PreviousTurnsContextTestCase(unittest.TestCase):
    def test_frontend_takes_use_designer_visual_in_next_actions(self) -> None:
        designer = ProductDesignerTake(
            visual_direction="여백 넓힘 + primary 강조",
        )
        take = run_role_deliberation(
            DeliberationContext(
                session=_session(),
                role="engineering-agent/frontend-engineer",
                previous_turns=(designer,),
            )
        )
        self.assertTrue(
            any("여백 넓힘" in act for act in take.next_actions),
            msg=f"frontend next_actions did not pick up designer visual: {take.next_actions}",
        )

    def test_qa_takes_use_backend_data_impact(self) -> None:
        backend = BackendEngineerTake(
            data_impact="users 테이블에 last_seen 컬럼 추가",
        )
        take = run_role_deliberation(
            DeliberationContext(
                session=_session(),
                role="engineering-agent/qa-engineer",
                previous_turns=(backend,),
            )
        )
        self.assertTrue(
            any("last_seen" in act for act in take.next_actions),
            msg=f"qa next_actions did not pick up backend data impact: {take.next_actions}",
        )

    def test_designer_uses_tech_lead_decisions(self) -> None:
        opening = TechLeadOpening(
            decisions_needed=("승인 필요",),
        )
        take = run_role_deliberation(
            DeliberationContext(
                session=_session(),
                role="engineering-agent/product-designer",
                previous_turns=(opening,),
            )
        )
        self.assertTrue(
            any("승인 필요" in act for act in take.next_actions),
            msg=f"designer next_actions did not pick up tech-lead decision: {take.next_actions}",
        )


class RuntimeRendersFourSectionsTestCase(unittest.TestCase):
    def test_render_includes_all_sections(self) -> None:
        take = run_role_deliberation(
            DeliberationContext(
                session=_session(),
                role="engineering-agent/qa-engineer",
                research_pack=_pack(
                    _source(
                        url="https://github.com/yule-studio/agent/issues/1",
                        title="회귀",
                    )
                ),
            )
        )
        text = render_role_take(take)
        self.assertIn("관점:", text)
        self.assertIn("근거", text)
        self.assertIn("리스크", text)
        self.assertIn("다음 행동", text)


class SynthesisProfileGapTestCase(unittest.TestCase):
    def test_open_research_flags_missing_role_top_type(self) -> None:
        # pack has only a github issue; backend-engineer will speak but its
        # top profile type (official_docs) is missing.
        pack = _pack(
            _source(
                url="https://github.com/yule-studio/agent/issues/1",
                title="bug",
            )
        )
        backend_take = run_role_deliberation(
            DeliberationContext(
                session=_session(),
                role="engineering-agent/backend-engineer",
                research_pack=pack,
            )
        )
        synth = synthesize(_session(), (backend_take,), research_pack=pack)
        self.assertTrue(
            any("backend-engineer 우선 자료 유형" in m for m in synth.open_research),
            msg=f"open_research did not flag missing top type: {synth.open_research}",
        )


class FallbackResilienceTestCase(unittest.TestCase):
    """Even without ResearchPack the fallback must still produce all 4 sections."""

    def test_no_pack_no_previous_turns_still_renders(self) -> None:
        for role in (
            "engineering-agent/tech-lead",
            "engineering-agent/product-designer",
            "engineering-agent/backend-engineer",
            "engineering-agent/frontend-engineer",
            "engineering-agent/qa-engineer",
        ):
            with self.subTest(role=role):
                take = run_role_deliberation(
                    DeliberationContext(session=_session(), role=role)
                )
                self.assertTrue(take.perspective)
                self.assertTrue(take.risks)
                self.assertTrue(take.next_actions)
                # render must not raise
                render_role_take(take)

    def test_runner_raises_falls_back_with_full_contract(self) -> None:
        def boom(_ctx):
            raise RuntimeError("backend down")

        take = run_role_deliberation(
            DeliberationContext(
                session=_session(),
                role="engineering-agent/frontend-engineer",
            ),
            runner_fn=boom,
        )
        self.assertIsInstance(take, FrontendEngineerTake)
        self.assertTrue(take.perspective)
        self.assertTrue(take.next_actions)


class DeliberationLoopTestCase(unittest.TestCase):
    """End-to-end loop: tech-lead → 역할들 → tech-lead 종합."""

    def _pack_with_mixed_sources(self) -> ResearchPack:
        sources = (
            ResearchSource(
                source_url="https://github.com/yule-studio/agent/issues/42",
                title="login flow regression",
                summary="기존 로그인 흐름 회귀 이슈",
                extra={"why_relevant": "QA 회귀 시나리오 후보"},
            ),
            ResearchSource(
                source_url="https://developer.mozilla.org/en/docs/Web/API/Fetch",
                title="MDN fetch streaming",
                extra={"why_relevant": "백엔드 streaming 계약 근거"},
            ),
            ResearchSource(
                source_url="https://www.notefolio.net/portfolio/abc",
                title="landing reference deck",
                extra={"why_relevant": "tone/grid 레퍼런스"},
            ),
            ResearchSource(
                source_url=None,
                title="hero screenshot",
                attachments=(
                    ResearchAttachment(
                        kind="image",
                        url="cdn://hero-1.png",
                        content_type="image/png",
                    ),
                ),
                extra={"why_relevant": "상단 모듈 시각 톤"},
            ),
        )
        return ResearchPack(
            title="loop pack",
            primary_url=sources[0].source_url,
            sources=sources,
        )

    def test_deliberation_role_sequence_normalizes_and_prefixes(self) -> None:
        session = _session(
            role_sequence=("product-designer", "engineering-agent/qa-engineer"),
        )
        sequence = deliberation_role_sequence(session)
        self.assertEqual(sequence[0], "engineering-agent/tech-lead")
        self.assertIn("engineering-agent/product-designer", sequence)
        self.assertIn("engineering-agent/qa-engineer", sequence)

    def test_deliberation_role_sequence_dedups(self) -> None:
        session = _session(
            role_sequence=(
                "tech-lead",
                "engineering-agent/tech-lead",
                "product-designer",
            ),
        )
        sequence = deliberation_role_sequence(session)
        self.assertEqual(
            sequence.count("engineering-agent/tech-lead"), 1
        )

    def test_deliberation_role_sequence_default_when_empty(self) -> None:
        session = _session(role_sequence=())
        sequence = deliberation_role_sequence(session)
        self.assertEqual(
            sequence,
            (
                "engineering-agent/tech-lead",
                "engineering-agent/product-designer",
                "engineering-agent/backend-engineer",
                "engineering-agent/frontend-engineer",
                "engineering-agent/qa-engineer",
            ),
        )

    def test_run_deliberation_loop_produces_turn_per_role_plus_synthesis(
        self,
    ) -> None:
        session = _session(
            role_sequence=(
                "tech-lead",
                "product-designer",
                "backend-engineer",
                "frontend-engineer",
                "qa-engineer",
            ),
        )
        result = run_deliberation_loop(
            session,
            research_pack=self._pack_with_mixed_sources(),
        )
        self.assertIsInstance(result, DeliberationLoopResult)
        self.assertEqual(len(result.turns), 5)
        self.assertEqual(
            tuple(rec.role for rec in result.turns),
            (
                "engineering-agent/tech-lead",
                "engineering-agent/product-designer",
                "engineering-agent/backend-engineer",
                "engineering-agent/frontend-engineer",
                "engineering-agent/qa-engineer",
            ),
        )
        for record in result.turns:
            self.assertIsInstance(record, DeliberationTurnRecord)
            self.assertTrue(record.rendered)
        self.assertIsInstance(result.synthesis, TechLeadSynthesis)
        self.assertIn("tech-lead 종합", result.synthesis_text)

    def test_run_deliberation_loop_passes_previous_turns_to_each_role(
        self,
    ) -> None:
        """후속 역할의 take는 직전 역할들의 take를 컨텍스트로 받아 형성된다.

        이 테스트는 frontend-engineer가 product-designer의 visual_direction을
        next_actions로 받아쓰고, qa-engineer가 backend-engineer의 data_impact를
        받아쓰는지 검증한다 — 같은 말을 반복하지 않고 이어서 토의하는 핵심.
        """

        session = _session(
            role_sequence=(
                "tech-lead",
                "product-designer",
                "backend-engineer",
                "frontend-engineer",
                "qa-engineer",
            ),
        )
        result = run_deliberation_loop(
            session,
            research_pack=self._pack_with_mixed_sources(),
        )
        by_role = {rec.role: rec.take for rec in result.turns}

        designer = by_role["engineering-agent/product-designer"]
        backend = by_role["engineering-agent/backend-engineer"]
        frontend = by_role["engineering-agent/frontend-engineer"]
        qa = by_role["engineering-agent/qa-engineer"]

        self.assertIsInstance(designer, ProductDesignerTake)
        self.assertIsInstance(backend, BackendEngineerTake)
        self.assertIsInstance(frontend, FrontendEngineerTake)
        self.assertIsInstance(qa, QaEngineerTake)

        # frontend 의 다음 행동 중 하나가 디자이너의 visual_direction 을 인용한다.
        if designer.visual_direction:
            self.assertTrue(
                any(designer.visual_direction in action for action in frontend.next_actions),
                msg=f"frontend.next_actions did not cite designer.visual_direction; got={frontend.next_actions}",
            )

        # qa 의 다음 행동 중 하나가 백엔드 data_impact 를 인용한다.
        if backend.data_impact:
            self.assertTrue(
                any(backend.data_impact in action for action in qa.next_actions),
                msg=f"qa.next_actions did not cite backend.data_impact; got={qa.next_actions}",
            )

    def test_run_deliberation_loop_synthesis_assembles_per_role_todos(
        self,
    ) -> None:
        session = _session()
        result = run_deliberation_loop(
            session,
            research_pack=self._pack_with_mixed_sources(),
        )
        # synthesis.todos 가 역할 prefix를 가진 항목으로 구성된다.
        self.assertTrue(result.synthesis.todos)
        prefixes = {todo.split(" ")[0] for todo in result.synthesis.todos if todo.startswith("[")}
        self.assertIn("[tech-lead]", prefixes)
        # 적어도 한 advisor 역할의 todo가 포함된다.
        advisor_prefixes = {
            "[product-designer]",
            "[backend-engineer]",
            "[frontend-engineer]",
            "[qa-engineer]",
        }
        self.assertTrue(
            advisor_prefixes & prefixes,
            msg=f"no advisor-role todos found; got prefixes={prefixes}",
        )

    def test_run_deliberation_loop_runner_failure_falls_back(self) -> None:
        """runner_fn 이 모든 호출에서 예외를 던져도 결정적 fallback 으로 끝까지 흐른다."""

        def runner(_ctx: DeliberationContext):
            raise RuntimeError("runner backend down")

        session = _session(
            role_sequence=(
                "tech-lead",
                "product-designer",
                "backend-engineer",
                "frontend-engineer",
                "qa-engineer",
            ),
        )
        result = run_deliberation_loop(
            session,
            research_pack=self._pack_with_mixed_sources(),
            runner_fn=runner,
        )
        self.assertEqual(len(result.turns), 5)
        # 모든 역할이 4-section contract 를 채운 채 반환된다.
        for record in result.turns:
            self.assertTrue(record.take.perspective)
            self.assertTrue(record.take.next_actions)
            self.assertTrue(record.rendered)

    def test_run_deliberation_loop_marks_approval_required(self) -> None:
        session = _session(
            state=WorkflowState.INTAKE,
            write_requested=True,
            write_blocked_reason="user_approved=False",
        )
        result = run_deliberation_loop(
            session,
            research_pack=self._pack_with_mixed_sources(),
        )
        self.assertTrue(result.synthesis.approval_required)
        self.assertIn("승인 필요: yes", result.synthesis_text)


# ---------------------------------------------------------------------------
# Phase 3 follow-up: deterministic fallback consumes RetrievedMemory
# ---------------------------------------------------------------------------


from yule_orchestrator.agents.deliberation import (  # noqa: E402  (placed after the rest for grouping)
    AiEngineerTake,
    RetrievedMemory,
    _deterministic_role_take,
    memory_evidence_lines,
    memory_hint_for_role,
)


def _decision_memory(title: str = "Stripe Pricing 합의") -> RetrievedMemory:
    return RetrievedMemory(
        title=title,
        snippet="hero step copy를 분할한다",
        source_kind="obsidian",
        note_kind="decision",
        path=f"Agents/Engineering/Decisions/{title}.md",
        score=-7.1,
    )


def _policy_memory(
    title: str = "Backend Deployment Policy",
) -> RetrievedMemory:
    return RetrievedMemory(
        title=title,
        snippet="Stripe pricing 변경은 backend 검토 후 적용",
        source_kind="policy",
        note_kind=None,
        path=f"policies/runtime/agents/{title}.md",
        score=-5.4,
    )


def _reference_memory(
    title: str = "Stripe Pricing visual reference",
) -> RetrievedMemory:
    return RetrievedMemory(
        title=title,
        snippet="이미지 모음을 정리한 노트",
        source_kind="obsidian",
        note_kind="reference",
        path=f"Agents/Engineering/References/{title}.md",
        score=-6.0,
    )


def _research_memory(title: str = "Stripe Pricing 자료") -> RetrievedMemory:
    return RetrievedMemory(
        title=title,
        snippet="Stripe pricing 페이지의 hero step 패턴 자료",
        source_kind="obsidian",
        note_kind="research",
        path=f"Agents/Engineering/Research/{title}.md",
        score=-4.2,
    )


class MemoryHelpersTestCase(unittest.TestCase):
    def test_memory_evidence_lines_renders_per_hit(self) -> None:
        lines = memory_evidence_lines(
            (_decision_memory(), _reference_memory()),
            limit=2,
        )
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("decision" in line for line in lines))
        self.assertTrue(any("reference" in line for line in lines))

    def test_memory_evidence_lines_strips_fts5_markers(self) -> None:
        hit = RetrievedMemory(
            title="x",
            snippet="…before «match» after…",
            source_kind="policy",
        )
        line = memory_evidence_lines((hit,))[0]
        self.assertNotIn("«", line)
        self.assertNotIn("»", line)

    def test_memory_hint_for_role_filters_by_kind(self) -> None:
        hint = memory_hint_for_role(
            (_policy_memory(), _decision_memory()),
            kind="decision",
        )
        self.assertIsNotNone(hint)
        self.assertIn("Stripe Pricing 합의", hint)

    def test_memory_helpers_handle_empty(self) -> None:
        self.assertEqual(memory_evidence_lines(()), ())
        self.assertIsNone(memory_hint_for_role(()))


class FallbacksConsumeMemoryContextTestCase(unittest.TestCase):
    def _ctx(self, role: str, memory_context=()) -> DeliberationContext:
        return DeliberationContext(
            session=_session(),
            role=role,
            research_pack=None,
            memory_context=tuple(memory_context),
        )

    # --- baselines for diff-checks ----------------------------------------

    def _baseline_text(self, role: str) -> str:
        ctx = self._ctx(role)
        return render_role_take(_deterministic_role_take(ctx))

    # --- per-role assertions ----------------------------------------------

    def test_tech_lead_fallback_includes_decision_memory(self) -> None:
        baseline = self._baseline_text("engineering-agent/tech-lead")
        ctx = self._ctx(
            "engineering-agent/tech-lead",
            memory_context=(_decision_memory(), _policy_memory()),
        )
        take = _deterministic_role_take(ctx)
        self.assertIsInstance(take, TechLeadOpening)
        rendered = render_role_take(take)
        self.assertIn("Stripe Pricing 합의", rendered)
        self.assertNotEqual(rendered, baseline)

    def test_product_designer_fallback_prepends_reference_memory(self) -> None:
        baseline = self._baseline_text("engineering-agent/product-designer")
        ctx = self._ctx(
            "engineering-agent/product-designer",
            memory_context=(_reference_memory(), _research_memory()),
        )
        take = _deterministic_role_take(ctx)
        self.assertIsInstance(take, ProductDesignerTake)
        rendered = render_role_take(take)
        self.assertIn("visual reference", rendered)
        # The first reference summary entry must be the memory hit.
        self.assertTrue(
            take.reference_summary[0].lower().startswith("[memory")
            or "Stripe Pricing visual reference" in take.reference_summary[0],
            f"reference_summary[0]={take.reference_summary[0]!r}",
        )
        self.assertNotEqual(rendered, baseline)

    def test_backend_engineer_fallback_surfaces_policy_memory_in_risks(self) -> None:
        baseline = self._baseline_text("engineering-agent/backend-engineer")
        ctx = self._ctx(
            "engineering-agent/backend-engineer",
            memory_context=(_policy_memory(),),
        )
        take = _deterministic_role_take(ctx)
        self.assertIsInstance(take, BackendEngineerTake)
        joined_risks = " · ".join(take.risks)
        self.assertIn("Backend Deployment Policy", joined_risks)
        self.assertNotEqual(render_role_take(take), baseline)

    def test_frontend_engineer_fallback_uses_reference_memory_in_next_actions(self) -> None:
        baseline = self._baseline_text("engineering-agent/frontend-engineer")
        ctx = self._ctx(
            "engineering-agent/frontend-engineer",
            memory_context=(_reference_memory(),),
        )
        take = _deterministic_role_take(ctx)
        self.assertIsInstance(take, FrontendEngineerTake)
        joined = " · ".join(take.next_actions)
        self.assertIn("Stripe Pricing visual reference", joined)
        self.assertNotEqual(render_role_take(take), baseline)

    def test_qa_engineer_fallback_quotes_decision_memory_in_regression(self) -> None:
        ctx = self._ctx(
            "engineering-agent/qa-engineer",
            memory_context=(_decision_memory(),),
        )
        take = _deterministic_role_take(ctx)
        self.assertIsInstance(take, QaEngineerTake)
        joined = " · ".join(take.regression_targets) + " · ".join(take.next_actions)
        self.assertIn("Stripe Pricing 합의", joined)

    def test_ai_engineer_fallback_consumes_research_memory(self) -> None:
        ctx = self._ctx(
            "engineering-agent/ai-engineer",
            memory_context=(_research_memory(), _decision_memory()),
        )
        take = _deterministic_role_take(ctx)
        self.assertIsInstance(take, AiEngineerTake)
        joined_evidence = " · ".join(take.evidence)
        self.assertIn("Stripe Pricing 자료", joined_evidence)

    def test_empty_memory_context_keeps_existing_fallback_output(self) -> None:
        # Sanity: every role's deterministic output stays stable when
        # memory_context is empty.
        for role in (
            "engineering-agent/tech-lead",
            "engineering-agent/product-designer",
            "engineering-agent/backend-engineer",
            "engineering-agent/frontend-engineer",
            "engineering-agent/qa-engineer",
            "engineering-agent/ai-engineer",
        ):
            with_memory = render_role_take(
                _deterministic_role_take(self._ctx(role, memory_context=()))
            )
            again = render_role_take(
                _deterministic_role_take(self._ctx(role, memory_context=()))
            )
            self.assertEqual(with_memory, again)
            # Critical sections still present (regression contract guard).
            self.assertIn("관점", with_memory)
            self.assertIn("근거", with_memory)
            self.assertIn("리스크", with_memory)
            self.assertIn("다음 행동", with_memory)

    def test_memory_hits_do_not_explode_evidence_length(self) -> None:
        # Hard cap: with 5 memory hits, role take must still emit a
        # bounded number of evidence/extra lines (verbosity guard).
        many = (_decision_memory(), _decision_memory(),
                _policy_memory(), _research_memory(), _reference_memory())
        ctx = self._ctx(
            "engineering-agent/tech-lead", memory_context=many
        )
        take = _deterministic_role_take(ctx)
        self.assertLessEqual(len(take.evidence), 6)


class SynthesizeConsumesMemoryContextTestCase(unittest.TestCase):
    def test_synthesize_with_decision_memory_adds_consensus_prefix(self) -> None:
        session = _session()
        takes = (
            _deterministic_role_take(
                DeliberationContext(session=session, role="engineering-agent/tech-lead")
            ),
        )
        baseline = synthesize(session, takes)
        with_memory = synthesize(
            session,
            takes,
            memory_context=(_decision_memory(),),
        )
        self.assertNotEqual(baseline.consensus, with_memory.consensus)
        # The decision title leaks into either consensus or todos so the
        # operator can see "we consulted prior memory".
        joined = with_memory.consensus + " · ".join(with_memory.todos)
        self.assertIn("Stripe Pricing 합의", joined)

    def test_synthesize_with_policy_memory_adds_open_research(self) -> None:
        session = _session()
        takes = (
            _deterministic_role_take(
                DeliberationContext(session=session, role="engineering-agent/tech-lead")
            ),
        )
        with_memory = synthesize(
            session,
            takes,
            memory_context=(_policy_memory(),),
        )
        self.assertTrue(
            any("Backend Deployment Policy" in entry for entry in with_memory.open_research),
            f"open_research={with_memory.open_research}",
        )

    def test_synthesize_no_memory_matches_legacy_signature(self) -> None:
        session = _session()
        takes = (
            _deterministic_role_take(
                DeliberationContext(session=session, role="engineering-agent/tech-lead")
            ),
        )
        legacy = synthesize(session, takes)
        memory = synthesize(session, takes, memory_context=())
        self.assertEqual(legacy.consensus, memory.consensus)
        self.assertEqual(tuple(legacy.todos), tuple(memory.todos))


class SynthesizeThreadPipesMemoryTestCase(unittest.TestCase):
    """Verify that the runtime seam ``synthesize_thread`` actually feeds
    the retrieval result into ``synthesize`` instead of dropping it.
    """

    def test_synthesize_thread_passes_memory_context(self) -> None:
        from unittest.mock import patch

        session = _session()
        captured: dict[str, object] = {}

        def stub_retrieve(*, role, session, research_pack):  # noqa: D401
            return (_decision_memory(),)

        def fake_synthesize(*args, **kwargs):
            captured["memory_context"] = kwargs.get("memory_context")
            return TechLeadSynthesis(consensus="stub", todos=())

        with patch(
            "yule_orchestrator.discord.engineering_team_runtime."
            "_retrieve_memory_for_role",
            side_effect=stub_retrieve,
        ), patch(
            "yule_orchestrator.discord.engineering_team_runtime.synthesize",
            side_effect=fake_synthesize,
        ):
            synthesize_thread(session, role_takes=())

        self.assertIsNotNone(captured.get("memory_context"))
        self.assertEqual(len(captured["memory_context"]), 1)


# ---------------------------------------------------------------------------
# Phase 3 safety: structured citations + multi-decision synthesis
# ---------------------------------------------------------------------------


from yule_orchestrator.agents.deliberation import (  # noqa: E402
    assign_citation_ids,
    format_memory_block,
    memory_hits_by,
)


def _decision_memory_v2(title: str = "Stripe Pricing 합의 v2") -> RetrievedMemory:
    return RetrievedMemory(
        title=title,
        snippet="hero step copy 일괄 유지로 회귀",
        source_kind="obsidian",
        note_kind="decision",
        path=f"Agents/Engineering/Decisions/{title}.md",
        score=-6.5,
    )


class CitationIdsTestCase(unittest.TestCase):
    def test_assigns_stable_ids_in_input_order(self) -> None:
        hits = (_decision_memory(), _policy_memory(), _reference_memory())
        labelled = assign_citation_ids(hits)
        ids = [h.citation_id for h in labelled]
        self.assertEqual(ids, ["m1", "m2", "m3"])

    def test_assign_citation_ids_preserves_existing(self) -> None:
        existing = _decision_memory()
        existing = type(existing)(
            **{**existing.__dict__, "citation_id": "custom-7"}
        )
        labelled = assign_citation_ids((existing, _policy_memory()))
        self.assertEqual(labelled[0].citation_id, "custom-7")
        self.assertEqual(labelled[1].citation_id, "m2")

    def test_assign_citation_ids_handles_empty(self) -> None:
        self.assertEqual(assign_citation_ids(()), ())


class FormatMemoryBlockTestCase(unittest.TestCase):
    def test_block_keeps_path_score_source(self) -> None:
        block = format_memory_block(
            assign_citation_ids((_decision_memory(), _policy_memory()))
        )
        self.assertIn("[m1]", block)
        self.assertIn("[m2]", block)
        self.assertIn("Agents/Engineering/Decisions", block)
        self.assertIn("policies/runtime/agents", block)
        self.assertIn("score=", block)
        self.assertIn("obsidian", block)
        self.assertIn("policy", block)

    def test_block_strips_fts_markers_in_snippet(self) -> None:
        hit = RetrievedMemory(
            title="x",
            snippet="…before «match» after…",
            source_kind="policy",
            citation_id="m1",
        )
        self.assertNotIn("«", format_memory_block((hit,)))

    def test_empty_block(self) -> None:
        self.assertEqual(format_memory_block(()), "")


class MemoryHitsByTestCase(unittest.TestCase):
    def test_filters_by_kind_and_returns_multiple_hits(self) -> None:
        hits = (
            _decision_memory(title="결정 A"),
            _decision_memory(title="결정 B"),
            _policy_memory(),
        )
        decisions = memory_hits_by(hits, kind="decision", limit=5)
        self.assertEqual([h.title for h in decisions], ["결정 A", "결정 B"])

    def test_returns_empty_when_no_match(self) -> None:
        self.assertEqual(
            memory_hits_by((_policy_memory(),), kind="decision"), ()
        )


class FallbackCitationsTestCase(unittest.TestCase):
    def _ctx(self, role, memory_context):
        return DeliberationContext(
            session=_session(),
            role=role,
            research_pack=None,
            memory_context=tuple(memory_context),
        )

    def test_tech_lead_evidence_includes_citation_id(self) -> None:
        ctx = self._ctx(
            "engineering-agent/tech-lead",
            (_decision_memory(), _policy_memory()),
        )
        take = _deterministic_role_take(ctx)
        joined = " | ".join(take.evidence)
        self.assertIn("[m1", joined)
        self.assertIn("[m2", joined)

    def test_tech_lead_notes_include_citation(self) -> None:
        ctx = self._ctx(
            "engineering-agent/tech-lead",
            (_decision_memory(),),
        )
        take = _deterministic_role_take(ctx)
        self.assertIsNotNone(take.notes)
        self.assertIn("[m1]", take.notes)

    def test_backend_risk_includes_citation_id(self) -> None:
        ctx = self._ctx(
            "engineering-agent/backend-engineer",
            (_policy_memory(), _decision_memory()),
        )
        take = _deterministic_role_take(ctx)
        joined_risks = " · ".join(take.risks)
        joined_actions = " · ".join(take.next_actions)
        self.assertIn("[m1", joined_risks)
        self.assertIn("[m2", joined_actions)


class SynthesizeMultiDecisionTestCase(unittest.TestCase):
    def test_two_decision_hits_both_appear_in_todos(self) -> None:
        session = _session()
        takes = (
            _deterministic_role_take(
                DeliberationContext(session=session, role="engineering-agent/tech-lead")
            ),
        )
        result = synthesize(
            session,
            takes,
            memory_context=(_decision_memory(), _decision_memory_v2()),
        )
        joined = " · ".join(result.todos)
        self.assertIn("Stripe Pricing 합의", joined)
        self.assertIn("Stripe Pricing 합의 v2", joined)

    def test_conflicting_decisions_surface_user_decisions(self) -> None:
        session = _session()
        takes = (
            _deterministic_role_take(
                DeliberationContext(session=session, role="engineering-agent/tech-lead")
            ),
        )
        result = synthesize(
            session,
            takes,
            memory_context=(_decision_memory(), _decision_memory_v2()),
        )
        joined_user = " · ".join(result.user_decisions_needed)
        self.assertIn("Stripe Pricing 합의", joined_user)
        self.assertIn("Stripe Pricing 합의 v2", joined_user)

    def test_single_decision_does_not_request_user_arbitration(self) -> None:
        session = _session()
        takes = (
            _deterministic_role_take(
                DeliberationContext(session=session, role="engineering-agent/tech-lead")
            ),
        )
        result = synthesize(
            session,
            takes,
            memory_context=(_decision_memory(),),
        )
        # Only one decision → no "다중 검토" prompt for the user.
        self.assertFalse(
            any(
                "다중 검토" in entry for entry in result.user_decisions_needed
            ),
            f"user_decisions_needed={result.user_decisions_needed}",
        )

    def test_legacy_signature_unchanged_when_memory_empty(self) -> None:
        session = _session()
        takes = (
            _deterministic_role_take(
                DeliberationContext(session=session, role="engineering-agent/tech-lead")
            ),
        )
        legacy = synthesize(session, takes)
        empty_memory = synthesize(session, takes, memory_context=())
        self.assertEqual(legacy.consensus, empty_memory.consensus)
        self.assertEqual(tuple(legacy.todos), tuple(empty_memory.todos))


if __name__ == "__main__":
    unittest.main()
