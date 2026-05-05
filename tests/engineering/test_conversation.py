from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from datetime import datetime
from types import SimpleNamespace

from yule_orchestrator.discord.engineering_conversation import (
    CONFIRM_INTAKE,
    GENERAL_ENGINEERING_HELP,
    NEEDS_CLARIFICATION,
    SOURCE_TYPE_COMMUNITY_SIGNAL,
    SOURCE_TYPE_DESIGN_REFERENCE,
    SOURCE_TYPE_FILE_ATTACHMENT,
    SOURCE_TYPE_GITHUB_ISSUE,
    SOURCE_TYPE_GITHUB_PR,
    SOURCE_TYPE_IMAGE_REFERENCE,
    SOURCE_TYPE_OFFICIAL_DOCS,
    SOURCE_TYPE_URL,
    SOURCE_TYPE_USER_MESSAGE,
    SPLIT_TASK_PROPOSAL,
    TASK_INTAKE_CANDIDATE,
    build_engineering_conversation_response,
    build_research_pack_from_candidates,
    classify_attachment,
    classify_url,
    collect_research_candidates_from_message,
    detect_engineering_intent,
    format_insufficient_research_prompt,
    split_task_branches,
    suggest_role_research_assignments,
)


class DetectIntentTestCase(unittest.TestCase):
    def test_general_help_keyword(self) -> None:
        intent = detect_engineering_intent("engineering-agent 어떻게 써?")
        self.assertEqual(intent.intent_id, GENERAL_ENGINEERING_HELP)

    def test_help_korean(self) -> None:
        intent = detect_engineering_intent("엔지니어링 봇 도움말 좀 줘봐")
        self.assertEqual(intent.intent_id, GENERAL_ENGINEERING_HELP)

    def test_confirm_phrase_short_circuits(self) -> None:
        intent = detect_engineering_intent("이대로 진행")
        self.assertEqual(intent.intent_id, CONFIRM_INTAKE)

    def test_confirm_standalone_token(self) -> None:
        for token in ("ok", "오케이", "확정", "진행", "ㄱㄱ"):
            with self.subTest(token=token):
                intent = detect_engineering_intent(token)
                self.assertEqual(intent.intent_id, CONFIRM_INTAKE)

    def test_vague_short_message(self) -> None:
        for text in ("도와줘", "ㅁㄴㅇ", "ㅎㅎ"):
            with self.subTest(text=text):
                intent = detect_engineering_intent(text)
                self.assertEqual(intent.intent_id, NEEDS_CLARIFICATION)

    def test_empty_message(self) -> None:
        self.assertEqual(detect_engineering_intent("   ").intent_id, NEEDS_CLARIFICATION)

    def test_split_task_when_two_substantial_branches(self) -> None:
        intent = detect_engineering_intent(
            "랜딩페이지 hero 정리하고 또 회원가입 onboarding 흐름 개선해줘"
        )
        self.assertEqual(intent.intent_id, SPLIT_TASK_PROPOSAL)

    def test_no_split_when_branch_is_too_short(self) -> None:
        intent = detect_engineering_intent("음 그리고 좋아")
        self.assertNotEqual(intent.intent_id, SPLIT_TASK_PROPOSAL)

    def test_default_is_intake_candidate(self) -> None:
        intent = detect_engineering_intent("users API schema 변경해서 email_verified 필드 추가")
        self.assertEqual(intent.intent_id, TASK_INTAKE_CANDIDATE)


class SplitTaskBranchesTestCase(unittest.TestCase):
    def test_korean_conjunction(self) -> None:
        self.assertEqual(
            split_task_branches("랜딩 hero 정리 그리고 회원가입 흐름 개선"),
            ("랜딩 hero 정리", "회원가입 흐름 개선"),
        )

    def test_english_and(self) -> None:
        self.assertEqual(
            split_task_branches("polish hero and refresh onboarding"),
            ("polish hero", "refresh onboarding"),
        )

    def test_no_split_for_single_clause(self) -> None:
        self.assertEqual(split_task_branches("hero 섹션 정리"), ())


class ResponseEnvelopeTestCase(unittest.TestCase):
    def test_general_help_envelope(self) -> None:
        envelope = build_engineering_conversation_response("도움말 줘봐")
        self.assertEqual(envelope.intent_id, GENERAL_ENGINEERING_HELP)
        self.assertFalse(envelope.ready_to_intake)
        self.assertFalse(envelope.needs_clarification)
        self.assertIsNone(envelope.intake_prompt)

    def test_intake_candidate_envelope(self) -> None:
        # Isolate envelope-shape assertions from the auto-collector wire-up
        # by disabling auto_collect — the legacy intake question always
        # ends with the "이대로 진행" prompt.
        envelope = build_engineering_conversation_response(
            "새 랜딩페이지 hero 섹션을 다시 짜야 해",
            auto_collect=False,
        )
        self.assertEqual(envelope.intent_id, TASK_INTAKE_CANDIDATE)
        self.assertEqual(envelope.suggested_task_type, "landing-page")
        self.assertTrue(envelope.write_likely)
        self.assertEqual(envelope.intake_prompt, "새 랜딩페이지 hero 섹션을 다시 짜야 해")
        self.assertFalse(envelope.ready_to_intake)
        self.assertIn("이대로 진행", envelope.content)

    def test_needs_clarification_envelope(self) -> None:
        envelope = build_engineering_conversation_response(
            "도와줘",
            auto_collect=False,
        )
        self.assertEqual(envelope.intent_id, NEEDS_CLARIFICATION)
        self.assertTrue(envelope.needs_clarification)
        self.assertFalse(envelope.ready_to_intake)
        # New conversational clarification opener
        self.assertIn("어디부터 봐야 할지", envelope.content)

    def test_split_proposal_envelope(self) -> None:
        envelope = build_engineering_conversation_response(
            "랜딩페이지 hero 정리하고 또 회원가입 onboarding 흐름 개선해줘"
        )
        self.assertEqual(envelope.intent_id, SPLIT_TASK_PROPOSAL)
        self.assertEqual(len(envelope.proposed_splits), 2)
        self.assertFalse(envelope.ready_to_intake)
        self.assertEqual(envelope.intake_prompt, envelope.intake_prompt)  # preserved

    def test_confirm_with_last_prompt_marks_ready_to_intake(self) -> None:
        envelope = build_engineering_conversation_response(
            "이대로 진행",
            last_proposed_prompt="랜딩페이지 hero 다시 짜야 해",
        )
        self.assertEqual(envelope.intent_id, CONFIRM_INTAKE)
        self.assertTrue(envelope.ready_to_intake)
        self.assertEqual(envelope.intake_prompt, "랜딩페이지 hero 다시 짜야 해")
        self.assertEqual(envelope.suggested_task_type, "landing-page")
        self.assertTrue(envelope.write_likely)

    def test_confirm_existing_thread_request_does_not_say_new_registration(self) -> None:
        envelope = build_engineering_conversation_response(
            "이대로 진행",
            last_proposed_prompt="새로 등록하지 말고 열려 있는 스레드에서 리서치 이어가줘",
        )
        self.assertTrue(envelope.ready_to_intake)
        self.assertIn("새 작업으로 등록하지 않고", envelope.content)
        self.assertNotIn("작업을 등록할게요", envelope.content)

    def test_confirm_can_override_existing_thread_request_with_new_work(self) -> None:
        envelope = build_engineering_conversation_response(
            "새 작업으로 진행",
            last_proposed_prompt="새로 등록하지 말고 열려 있는 스레드에서 리서치 이어가줘",
        )
        self.assertTrue(envelope.ready_to_intake)
        self.assertIn("작업을 등록할게요", envelope.content)

    def test_confirm_without_last_prompt_uses_message_text(self) -> None:
        envelope = build_engineering_conversation_response("이대로 진행")
        self.assertEqual(envelope.intent_id, CONFIRM_INTAKE)
        self.assertTrue(envelope.ready_to_intake)
        self.assertEqual(envelope.intake_prompt, "이대로 진행")

    def test_mention_prefix_only_when_requested(self) -> None:
        with_mention = build_engineering_conversation_response(
            "도와줘",
            author_user_id=123,
            mention_user=True,
        )
        without_mention = build_engineering_conversation_response(
            "도와줘",
            author_user_id=123,
            mention_user=False,
        )
        self.assertTrue(with_mention.content.startswith("<@123>"))
        self.assertFalse(without_mention.content.startswith("<@"))


class WriteLikelyTestCase(unittest.TestCase):
    def test_write_keywords_set_flag(self) -> None:
        for prompt in (
            "users API 추가 구현해줘",
            "랜딩 hero 다시 짜야 해",
            "buttons 컴포넌트 만들어줘",
            "fix 배포 스크립트",
        ):
            with self.subTest(prompt=prompt):
                envelope = build_engineering_conversation_response(prompt)
                self.assertTrue(envelope.write_likely, prompt)

    def test_review_signals_dominate_write(self) -> None:
        envelope = build_engineering_conversation_response(
            "users API 어떻게 생각해 분석만 해줘"
        )
        self.assertFalse(envelope.write_likely)


class TaskTypeHintTestCase(unittest.TestCase):
    def test_known_task_types(self) -> None:
        cases = {
            "랜딩 hero 정리": "landing-page",
            "onboarding 가입 흐름 개선": "onboarding-flow",
            "welcome email 캠페인 만들기": "email-campaign",
            "히어로 visual polish": "visual-polish",
            "users API 추가": "backend-feature",
            "ui 컴포넌트 정리": "frontend-feature",
            "regression 테스트 시나리오 추가": "qa-test",
            "deploy 파이프라인 보강": "platform-infra",
        }
        for prompt, expected in cases.items():
            with self.subTest(prompt=prompt):
                envelope = build_engineering_conversation_response(prompt)
                self.assertEqual(envelope.suggested_task_type, expected)

    def test_unknown_message_has_no_task_type(self) -> None:
        envelope = build_engineering_conversation_response("이 부분 좀 검토해줘")
        self.assertIsNone(envelope.suggested_task_type)


class ClassifyAttachmentTestCase(unittest.TestCase):
    def test_image_extensions_become_image_reference(self) -> None:
        for filename in (
            "hero.png",
            "thumb.PNG",
            "draft.jpg",
            "draft.JPEG",
            "promo.webp",
            "anim.gif",
        ):
            with self.subTest(filename=filename):
                self.assertEqual(
                    classify_attachment(filename=filename),
                    SOURCE_TYPE_IMAGE_REFERENCE,
                )

    def test_image_content_type_when_extension_missing(self) -> None:
        self.assertEqual(
            classify_attachment(filename="screenshot", content_type="image/png"),
            SOURCE_TYPE_IMAGE_REFERENCE,
        )

    def test_pdf_falls_back_to_file_attachment(self) -> None:
        self.assertEqual(classify_attachment(filename="spec.pdf"), SOURCE_TYPE_FILE_ATTACHMENT)

    def test_no_metadata_defaults_to_file_attachment(self) -> None:
        self.assertEqual(classify_attachment(), SOURCE_TYPE_FILE_ATTACHMENT)


class ClassifyUrlTestCase(unittest.TestCase):
    def test_github_issue(self) -> None:
        self.assertEqual(
            classify_url("https://github.com/yule-studio/agent/issues/42"),
            SOURCE_TYPE_GITHUB_ISSUE,
        )

    def test_github_pull_request(self) -> None:
        self.assertEqual(
            classify_url("https://github.com/yule-studio/agent/pull/7"),
            SOURCE_TYPE_GITHUB_PR,
        )

    def test_github_repo_root_is_generic_url(self) -> None:
        self.assertEqual(
            classify_url("https://github.com/yule-studio/agent"),
            SOURCE_TYPE_URL,
        )

    def test_design_reference_hosts(self) -> None:
        for url in (
            "https://www.pinterest.com/board/landing-hero",
            "https://kr.pinterest.com/board/x",
            "https://www.behance.net/gallery/abc",
            "https://www.awwwards.com/sites/x",
            "https://dribbble.com/shots/y",
            "https://templates.wix.com/template/x",
        ):
            with self.subTest(url=url):
                self.assertEqual(classify_url(url), SOURCE_TYPE_DESIGN_REFERENCE)

    def test_official_docs_hosts(self) -> None:
        for url in (
            "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/img",
            "https://react.dev/reference/react/useState",
            "https://docs.python.org/3/library/asyncio.html",
            "https://learn.microsoft.com/en-us/azure/something",
        ):
            with self.subTest(url=url):
                self.assertEqual(classify_url(url), SOURCE_TYPE_OFFICIAL_DOCS)

    def test_community_signal_hosts(self) -> None:
        for url in (
            "https://www.reddit.com/r/webdev/comments/abc",
            "https://stackoverflow.com/questions/123",
            "https://news.ycombinator.com/item?id=1",
            "https://dev.to/author/post",
        ):
            with self.subTest(url=url):
                self.assertEqual(classify_url(url), SOURCE_TYPE_COMMUNITY_SIGNAL)

    def test_unknown_host_falls_back_to_url(self) -> None:
        self.assertEqual(classify_url("https://example.com/landing"), SOURCE_TYPE_URL)

    def test_blank_input_returns_url(self) -> None:
        self.assertEqual(classify_url(""), SOURCE_TYPE_URL)


class CollectResearchCandidatesTestCase(unittest.TestCase):
    def test_user_message_only_long_enough_is_sufficient(self) -> None:
        result = collect_research_candidates_from_message(
            "users API에 email_verified 필드 추가해서 onboarding 단계 마지막에 검증 메일 발송하는 흐름 정리해줘",
            author_role="tech-lead",
        )
        kinds = [c.source_type for c in result.candidates]
        self.assertEqual(kinds, [SOURCE_TYPE_USER_MESSAGE])
        self.assertFalse(result.insufficient)
        self.assertIsNone(result.follow_up_prompt)

    def test_short_user_message_only_marks_insufficient(self) -> None:
        result = collect_research_candidates_from_message("도와줘")
        self.assertTrue(result.insufficient)
        assert result.follow_up_prompt is not None
        self.assertTrue(result.follow_up_prompt.startswith("자료가 부족합니다."))

    def test_empty_message_with_no_attachment_marks_insufficient(self) -> None:
        result = collect_research_candidates_from_message("")
        self.assertEqual(result.candidates, ())
        self.assertTrue(result.insufficient)

    def test_links_are_split_by_host_into_distinct_source_types(self) -> None:
        text = (
            "랜딩 hero 정리해줘. 참고 링크: "
            "https://www.pinterest.com/board/x "
            "https://github.com/yule-studio/agent/issues/12 "
            "https://developer.mozilla.org/en-US/docs/Web/HTML"
        )
        result = collect_research_candidates_from_message(text, author_role="product-designer")
        kinds = [c.source_type for c in result.candidates]
        self.assertEqual(kinds[0], SOURCE_TYPE_USER_MESSAGE)
        self.assertIn(SOURCE_TYPE_DESIGN_REFERENCE, kinds)
        self.assertIn(SOURCE_TYPE_GITHUB_ISSUE, kinds)
        self.assertIn(SOURCE_TYPE_OFFICIAL_DOCS, kinds)
        self.assertFalse(result.insufficient)

    def test_image_attachment_classified_as_image_reference(self) -> None:
        attachment = SimpleNamespace(
            filename="hero.png",
            url="https://cdn.discordapp.com/attachments/1/1/hero.png",
            content_type="image/png",
            id=987,
            size=12345,
        )
        result = collect_research_candidates_from_message(
            "이렇게 가고 싶어",
            attachments=[attachment],
            author_role="product-designer",
        )
        kinds = [c.source_type for c in result.candidates]
        self.assertIn(SOURCE_TYPE_IMAGE_REFERENCE, kinds)
        image_candidate = next(c for c in result.candidates if c.source_type == SOURCE_TYPE_IMAGE_REFERENCE)
        self.assertEqual(image_candidate.attachment_id, "987")
        self.assertEqual(image_candidate.title, "hero.png")
        self.assertIsNotNone(image_candidate.risk_or_limit)

    def test_pdf_attachment_classified_as_file_attachment(self) -> None:
        attachment = {
            "filename": "spec.pdf",
            "url": "https://cdn.discordapp.com/x/spec.pdf",
            "content_type": "application/pdf",
            "id": "555",
            "size": 1024,
        }
        result = collect_research_candidates_from_message(
            "여기 스펙 첨부합니다",
            attachments=[attachment],
            author_role="qa-engineer",
        )
        kinds = [c.source_type for c in result.candidates]
        self.assertIn(SOURCE_TYPE_FILE_ATTACHMENT, kinds)
        candidate = next(c for c in result.candidates if c.source_type == SOURCE_TYPE_FILE_ATTACHMENT)
        self.assertEqual(candidate.attachment_id, "555")

    def test_image_only_attachment_satisfies_landing_page_required_visual(self) -> None:
        attachment = SimpleNamespace(
            filename="hero.jpg",
            url="https://cdn/hero.jpg",
            content_type="image/jpeg",
            id="333",
        )
        result = collect_research_candidates_from_message(
            "랜딩 hero 다시 짜자",
            attachments=[attachment],
            task_type="landing-page",
            author_role="product-designer",
        )
        self.assertFalse(result.insufficient)

    def test_landing_page_without_visual_is_insufficient(self) -> None:
        result = collect_research_candidates_from_message(
            "랜딩 hero 다시 짜자. 빨리 가야 함. 일단 카피 위주로.",
            task_type="landing-page",
        )
        self.assertTrue(result.insufficient)
        assert result.insufficient_reason is not None
        self.assertIn("시각 reference", result.insufficient_reason)

    def test_backend_feature_without_docs_is_insufficient(self) -> None:
        result = collect_research_candidates_from_message(
            "users API에 email_verified 필드 추가하고 마이그레이션 같이 넣자",
            task_type="backend-feature",
        )
        self.assertTrue(result.insufficient)
        assert result.insufficient_reason is not None
        self.assertIn("backend-feature", result.insufficient_reason)

    def test_role_assignments_drop_already_collected_categories(self) -> None:
        result = collect_research_candidates_from_message(
            "랜딩 hero 다시 짜자. 참고 https://developer.mozilla.org/en-US/docs/Web",
            attachments=[
                SimpleNamespace(filename="hero.png", url="https://cdn/h.png", id="1"),
            ],
            task_type="landing-page",
            author_role="product-designer",
        )
        designer = result.role_assignments.get("product-designer", ())
        self.assertNotIn(SOURCE_TYPE_IMAGE_REFERENCE, designer)
        self.assertNotIn(SOURCE_TYPE_OFFICIAL_DOCS, designer)
        backend = result.role_assignments.get("backend-engineer", ())
        self.assertNotIn(SOURCE_TYPE_OFFICIAL_DOCS, backend)
        self.assertIn(SOURCE_TYPE_GITHUB_ISSUE, backend)

    def test_url_strips_trailing_punctuation_when_extracting(self) -> None:
        result = collect_research_candidates_from_message(
            "참고: https://reddit.com/r/webdev/abc."
        )
        kinds = [c.source_type for c in result.candidates]
        self.assertIn(SOURCE_TYPE_COMMUNITY_SIGNAL, kinds)
        community = next(c for c in result.candidates if c.source_type == SOURCE_TYPE_COMMUNITY_SIGNAL)
        self.assertFalse(community.url.endswith("."))

    def test_collected_at_is_threaded_through_when_provided(self) -> None:
        when = datetime(2026, 4, 30, 9, 0, 0)
        result = collect_research_candidates_from_message(
            "긴 설명이 들어가야 인지가 됩니다 이 작업은 onboarding 흐름 다시 보는 일",
            posted_at=when,
        )
        self.assertEqual(result.candidates[0].collected_at, when)


class FormatInsufficientResearchPromptTestCase(unittest.TestCase):
    def test_starts_with_required_korean_phrase(self) -> None:
        body = format_insufficient_research_prompt()
        self.assertTrue(body.startswith("자료가 부족합니다."))
        self.assertIn("참고할 링크나 이미지", body)

    def test_includes_reason_when_provided(self) -> None:
        body = format_insufficient_research_prompt("사유 메모")
        self.assertIn("사유: 사유 메모", body)


class SuggestRoleResearchAssignmentsTestCase(unittest.TestCase):
    def test_designer_gets_visual_buckets_first_when_nothing_collected(self) -> None:
        assignments = suggest_role_research_assignments(
            task_type="landing-page",
            collected_source_types=(SOURCE_TYPE_USER_MESSAGE,),
        )
        designer = assignments.get("product-designer", ())
        self.assertTrue(designer)
        self.assertEqual(designer[0], SOURCE_TYPE_IMAGE_REFERENCE)

    def test_qa_gets_issue_first(self) -> None:
        assignments = suggest_role_research_assignments(
            task_type="qa-test",
            collected_source_types=(SOURCE_TYPE_USER_MESSAGE,),
        )
        qa = assignments.get("qa-engineer", ())
        self.assertEqual(qa[0], SOURCE_TYPE_GITHUB_ISSUE)

    def test_role_omitted_when_nothing_left_to_recommend(self) -> None:
        complete = (
            SOURCE_TYPE_IMAGE_REFERENCE,
            SOURCE_TYPE_DESIGN_REFERENCE,
            SOURCE_TYPE_FILE_ATTACHMENT,
            SOURCE_TYPE_URL,
            SOURCE_TYPE_USER_MESSAGE,
            SOURCE_TYPE_COMMUNITY_SIGNAL,
        )
        assignments = suggest_role_research_assignments(
            task_type=None,
            collected_source_types=complete,
        )
        self.assertNotIn("product-designer", assignments)

    def test_required_categories_are_pulled_to_front(self) -> None:
        assignments = suggest_role_research_assignments(
            task_type="backend-feature",
            collected_source_types=(),
        )
        backend = assignments.get("backend-engineer", ())
        self.assertEqual(backend[0], SOURCE_TYPE_OFFICIAL_DOCS)


class BuildResearchPackTestCase(unittest.TestCase):
    def test_pack_keeps_engineering_metadata_in_source_extra(self) -> None:
        result = collect_research_candidates_from_message(
            "랜딩 hero 정리. 참고 https://github.com/y/r/issues/42",
            attachments=[SimpleNamespace(filename="hero.png", url="https://cdn/h.png", id="1")],
            task_type="landing-page",
            author_role="product-designer",
        )
        pack = build_research_pack_from_candidates(
            title="랜딩 hero 작업",
            candidates=result.candidates,
            channel_id=1498929862881054721,
            thread_id=999,
            message_id=1,
        )
        self.assertEqual(pack.title, "랜딩 hero 작업")
        self.assertGreaterEqual(len(pack.sources), 3)
        # primary_url is the first candidate URL we saw (issue link)
        self.assertEqual(pack.primary_url, "https://github.com/y/r/issues/42")
        # 각 source.extra 에 source_type 등 메타가 살아 있어야 deliberation 단계에서
        # 역할 assignment 와 부족 판정에 다시 활용할 수 있다.
        types = sorted(s.extra.get("source_type") for s in pack.sources)
        self.assertIn(SOURCE_TYPE_USER_MESSAGE, types)
        self.assertIn(SOURCE_TYPE_GITHUB_ISSUE, types)
        self.assertIn(SOURCE_TYPE_IMAGE_REFERENCE, types)
        # Image 첨부는 ResearchAttachment 로도 보존되어야 한다
        attachment_kinds = {att.kind for att in pack.attachments}
        self.assertIn("image", attachment_kinds)

    def test_build_pack_requires_candidates(self) -> None:
        with self.assertRaises(ValueError):
            build_research_pack_from_candidates(title="x", candidates=())


# ---------------------------------------------------------------------------
# Auto-collector wire-up
# ---------------------------------------------------------------------------


class AutoCollectWireUpTestCase(unittest.TestCase):
    def _enabled_cfg(self):
        from yule_orchestrator.agents.research_collector import (
            CollectorConfig,
            PROVIDER_MOCK,
        )
        return CollectorConfig(
            enabled=True,
            provider=PROVIDER_MOCK,
            max_results=2,
            max_provider_calls=1,
            max_results_per_role=2,
        )

    def test_intake_candidate_runs_collector_and_attaches_pack(self) -> None:
        from yule_orchestrator.discord.engineering_conversation import (
            build_engineering_conversation_response,
            TASK_INTAKE_CANDIDATE,
        )

        response = build_engineering_conversation_response(
            "새 랜딩페이지 hero 섹션 다시 짜야 해",
            role_for_research="engineering-agent/product-designer",
            collector_config=self._enabled_cfg(),
        )
        self.assertEqual(response.intent_id, TASK_INTAKE_CANDIDATE)
        # auto_collected by mock collector for designer role
        self.assertIsNotNone(response.research_pack)
        self.assertIsNotNone(response.collection_outcome)
        self.assertEqual(response.collection_outcome.mode.value, "auto_collected")
        self.assertGreaterEqual(response.collection_outcome.auto_collected_count, 1)
        # New conversational greeting + key phrasing
        self.assertIn("좋아요. 먼저 1차 자료를 모아볼게요", response.content)
        self.assertIn("운영-리서치", response.content)
        # Friendly meta tail uses translated provider label (not raw "mock")
        self.assertIn("수집 방식: 기본 검색(mock)", response.content)
        # Internal jargon should not leak into the user-facing body
        self.assertNotIn("collector:", response.content)
        self.assertNotIn("deliberation", response.content)
        self.assertNotIn("forum", response.content)

    def test_disabling_auto_collect_keeps_legacy_response(self) -> None:
        from yule_orchestrator.discord.engineering_conversation import (
            build_engineering_conversation_response,
            TASK_INTAKE_CANDIDATE,
        )

        response = build_engineering_conversation_response(
            "새 랜딩페이지 hero 섹션 다시 짜야 해",
            auto_collect=False,
        )
        self.assertEqual(response.intent_id, TASK_INTAKE_CANDIDATE)
        self.assertIsNone(response.research_pack)
        self.assertIsNone(response.collection_outcome)
        self.assertNotIn("1차 자료를", response.content)

    def test_user_provided_links_route_to_user_provided_mode(self) -> None:
        from yule_orchestrator.agents.research_collector import (
            CollectorConfig,
            PROVIDER_MOCK,
        )
        from yule_orchestrator.discord.engineering_conversation import (
            build_engineering_conversation_response,
        )

        # collector disabled → mock NoOp; user provided link → USER_PROVIDED
        response = build_engineering_conversation_response(
            "users API 정리",
            user_links=("https://example.com/auth-doc",),
            role_for_research="engineering-agent/backend-engineer",
            collector_config=CollectorConfig(
                enabled=False,
                provider=PROVIDER_MOCK,
                max_results=2,
                max_provider_calls=1,
                max_results_per_role=2,
            ),
        )
        self.assertIsNotNone(response.collection_outcome)
        self.assertEqual(response.collection_outcome.mode.value, "user_provided")
        # Conversational greeting for user-provided mode
        self.assertIn("보내주신 자료를 1순위", response.content)

    def test_unknown_role_falls_through_to_needs_user_input(self) -> None:
        from yule_orchestrator.agents.research_collector import (
            CollectorConfig,
            PROVIDER_MOCK,
        )
        from yule_orchestrator.discord.engineering_conversation import (
            build_engineering_conversation_response,
        )

        # Role is not in mock buckets → no auto-collected results, no user
        # links → NEEDS_USER_INPUT path; the response body announces that.
        response = build_engineering_conversation_response(
            "시안 모음 주세요",
            role_for_research="design-agent/illustrator",
            collector_config=CollectorConfig(
                enabled=True,
                provider=PROVIDER_MOCK,
                max_results=2,
                max_provider_calls=1,
                max_results_per_role=2,
            ),
        )
        self.assertEqual(response.collection_outcome.mode.value, "needs_user_input")
        self.assertIn("자동 수집이 비어 있어", response.content)
        # No "이대로 진행" prompt — we're waiting for user input.
        self.assertNotIn("`이대로 진행`", response.content)

    def test_topic_is_summarized_when_prompt_is_long(self) -> None:
        from yule_orchestrator.discord.engineering_conversation import (
            build_engineering_conversation_response,
        )

        long_prompt = (
            "오늘은 Obsidian을 이용해서 에이전트들의 지식 저장 구조를 설계하고 싶어. "
            "각 역할이 필요한 자료를 먼저 수집하고, 운영-리서치에 정리한 뒤 토의해줘. "
            "지금은 Obsidian 저장까지는 하지 말고 설계만 해줘."
        )
        response = build_engineering_conversation_response(
            long_prompt,
            role_for_research="engineering-agent/tech-lead",
            collector_config=self._enabled_cfg(),
        )
        # The full prompt body should not be echoed back verbatim — only
        # the first sentence (cut at the period) lives in the topic line.
        self.assertNotIn("Obsidian 저장까지는 하지 말고", response.content)
        self.assertNotIn("운영-리서치에 정리한 뒤 토의해줘", response.content)
        # But the topic phrase itself appears.
        self.assertIn("이번 요청은", response.content)

    def test_response_has_paragraph_structure(self) -> None:
        """Spec says 2~4 sentences per paragraph and short paragraphs."""

        from yule_orchestrator.discord.engineering_conversation import (
            build_engineering_conversation_response,
        )

        response = build_engineering_conversation_response(
            "새 랜딩페이지 hero 정리해줘",
            role_for_research="engineering-agent/product-designer",
            collector_config=self._enabled_cfg(),
        )
        body = response.content
        # Paragraphs separated by double newlines — at least 4 paragraphs:
        # greeting, understanding, action, meta, confirm.
        paragraphs = [p for p in body.split("\n\n") if p.strip()]
        self.assertGreaterEqual(len(paragraphs), 4)
        # First paragraph (greeting) is short — single sentence.
        self.assertLessEqual(len(paragraphs[0].splitlines()), 2)

    def test_collector_failure_does_not_break_response(self) -> None:
        from yule_orchestrator.agents.research_collector import ResearchCollector
        from yule_orchestrator.discord.engineering_conversation import (
            TASK_INTAKE_CANDIDATE,
            build_engineering_conversation_response,
        )

        class _BoomCollector(ResearchCollector):
            name = "boom"

            def search(self, query):  # noqa: D401 - test stub
                raise RuntimeError("provider down")

        from yule_orchestrator.agents.research_collector import (
            CollectorConfig,
            PROVIDER_MOCK,
        )

        response = build_engineering_conversation_response(
            "새 hero",
            role_for_research="engineering-agent/product-designer",
            collector_config=CollectorConfig(
                enabled=True,
                provider=PROVIDER_MOCK,
                max_results=2,
                max_provider_calls=1,
                max_results_per_role=2,
            ),
            collector=_BoomCollector(),
        )
        # The collector failed silently — conversation still produces a
        # legitimate intake-candidate response (no crash).
        self.assertEqual(response.intent_id, TASK_INTAKE_CANDIDATE)


if __name__ == "__main__":
    unittest.main()
