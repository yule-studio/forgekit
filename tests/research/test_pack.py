from __future__ import annotations

import unittest
from datetime import datetime, timedelta

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.research_pack import (
    ResearchAttachment,
    ResearchFinding,
    ResearchPack,
    ResearchRequest,
    ResearchSource,
    SourceType,
    classify_attachment,
    dedup_urls,
    extract_urls,
    make_finding,
    make_research_request,
    merge_packs,
    normalize_attachment_metadata,
    pack_from_discord_message,
    pack_from_request,
    pack_to_dict,
    pack_to_markdown,
    pack_with_extra_source,
    pack_with_finding,
    source_from_code_context,
    source_from_community_signal,
    source_from_design_reference,
    source_from_file_attachment,
    source_from_github_issue,
    source_from_github_pr,
    source_from_image_reference,
    source_from_official_docs,
    source_from_url,
    source_from_user_message,
    source_from_web_result,
)


class ExtractUrlsTestCase(unittest.TestCase):
    def test_finds_multiple(self) -> None:
        text = "참고: https://stripe.com/pricing 그리고 http://example.com/x?y=1, 끝"
        self.assertEqual(
            extract_urls(text),
            ("https://stripe.com/pricing", "http://example.com/x?y=1"),
        )

    def test_strips_trailing_punctuation(self) -> None:
        self.assertEqual(
            extract_urls("see (https://example.com/foo)."),
            ("https://example.com/foo",),
        )

    def test_dedup_within_text(self) -> None:
        text = "https://a.example/x and https://a.example/x"
        self.assertEqual(extract_urls(text), ("https://a.example/x",))

    def test_empty_text(self) -> None:
        self.assertEqual(extract_urls(""), ())
        self.assertEqual(extract_urls("그냥 평범한 문장"), ())


class DedupUrlsTestCase(unittest.TestCase):
    def test_preserves_first_seen_order(self) -> None:
        self.assertEqual(
            dedup_urls(["https://b", "https://a", "https://b"]),
            ("https://b", "https://a"),
        )

    def test_drops_none_and_empty(self) -> None:
        self.assertEqual(
            dedup_urls([None, "", " ", "https://x"]),
            ("https://x",),
        )

    def test_strips_trailing_punctuation(self) -> None:
        self.assertEqual(
            dedup_urls(["https://x).", "https://x;"]),
            ("https://x",),
        )


class PackFromDiscordMessageTestCase(unittest.TestCase):
    def test_extracts_primary_url(self) -> None:
        pack = pack_from_discord_message(
            title="Stripe Pricing 패턴",
            content="참고 https://stripe.com/pricing — hero 카피와 step copy",
            author_role="engineering-agent/product-designer",
            channel_id=111,
            thread_id=222,
            message_id=333,
        )
        self.assertEqual(pack.primary_url, "https://stripe.com/pricing")
        self.assertEqual(pack.urls, ("https://stripe.com/pricing",))
        self.assertEqual(len(pack.sources), 1)
        source = pack.sources[0]
        self.assertEqual(source.source_url, "https://stripe.com/pricing")
        self.assertEqual(source.author_role, "engineering-agent/product-designer")
        self.assertTrue(source.discord_origin)

    def test_no_url_yields_none_primary(self) -> None:
        pack = pack_from_discord_message(title="회의록", content="텍스트만 있음")
        self.assertIsNone(pack.primary_url)
        self.assertEqual(pack.urls, ())
        self.assertFalse(pack.sources[0].discord_origin)

    def test_attachments_round_trip(self) -> None:
        att = ResearchAttachment(kind="image", url="https://cdn/x.png", filename="x.png")
        pack = pack_from_discord_message(
            title="t",
            content="",
            attachments=[att],
        )
        self.assertEqual(pack.attachments, (att,))


class MergePacksTestCase(unittest.TestCase):
    def _pack(self, **kwargs) -> ResearchPack:
        defaults = dict(
            title="t",
            content="https://a.example",
        )
        defaults.update(kwargs)
        return pack_from_discord_message(**defaults)

    def test_unions_sources_and_urls(self) -> None:
        p1 = self._pack(title="A", content="https://a", message_id=1)
        p2 = self._pack(title="B", content="https://b", message_id=2)
        merged = merge_packs([p1, p2])
        self.assertEqual(set(merged.urls), {"https://a", "https://b"})
        self.assertEqual(len(merged.sources), 2)

    def test_dedup_same_message(self) -> None:
        p1 = self._pack(title="A", content="https://a", message_id=1, channel_id=10, thread_id=20)
        p2 = self._pack(title="A", content="https://a", message_id=1, channel_id=10, thread_id=20)
        merged = merge_packs([p1, p2])
        self.assertEqual(len(merged.sources), 1)

    def test_picks_first_non_untitled_title(self) -> None:
        p1 = pack_from_discord_message(title="(untitled)", content="x")
        p2 = pack_from_discord_message(title="Real Title", content="y")
        merged = merge_packs([p1, p2])
        self.assertEqual(merged.title, "Real Title")

    def test_uses_earliest_created_at(self) -> None:
        t0 = datetime(2026, 4, 30, 9, 0)
        t1 = datetime(2026, 4, 30, 10, 0)
        p_old = pack_from_discord_message(title="t", content="x", posted_at=t1)
        p_new = pack_from_discord_message(title="t", content="x", posted_at=t0)
        merged = merge_packs([p_old, p_new])
        self.assertEqual(merged.created_at, t0)

    def test_unions_tags_dedup(self) -> None:
        p1 = pack_from_discord_message(title="t", content="x", tags=["a", "b"])
        p2 = pack_from_discord_message(title="t", content="x", tags=["b", "c"])
        merged = merge_packs([p1, p2])
        self.assertEqual(set(merged.tags), {"a", "b", "c"})

    def test_empty_input_raises(self) -> None:
        with self.assertRaises(ValueError):
            merge_packs([])


class PackWithExtraSourceTestCase(unittest.TestCase):
    def test_appends_new_source(self) -> None:
        pack = pack_from_discord_message(title="t", content="https://a", message_id=1)
        extra = ResearchSource(source_url="https://b", message_id=2)
        result = pack_with_extra_source(pack, extra)
        self.assertEqual(len(result.sources), 2)
        self.assertIn("https://b", result.urls)

    def test_no_dup_when_same_message(self) -> None:
        from yule_orchestrator.agents.research_pack import SourceType

        pack = pack_from_discord_message(title="t", content="https://a", message_id=1, channel_id=10)
        # Same source_type as pack_from_discord_message (USER_MESSAGE) so the
        # dedup key (source_type, message_id, thread_id, channel_id, attachment_id, url) collides.
        same = ResearchSource(
            source_type=SourceType.USER_MESSAGE,
            source_url="https://a",
            message_id=1,
            channel_id=10,
            title=None,
        )
        result = pack_with_extra_source(pack, same)
        self.assertEqual(len(result.sources), 1)

    def test_sets_primary_url_when_none(self) -> None:
        pack = pack_from_discord_message(title="t", content="텍스트만")
        self.assertIsNone(pack.primary_url)
        extra = ResearchSource(source_url="https://x", message_id=99)
        result = pack_with_extra_source(pack, extra)
        self.assertEqual(result.primary_url, "https://x")


class AuthorRolesTestCase(unittest.TestCase):
    def test_unions_roles_dedup(self) -> None:
        p1 = pack_from_discord_message(
            title="t", content="x", author_role="engineering-agent/tech-lead", message_id=1
        )
        p2 = pack_from_discord_message(
            title="t",
            content="y",
            author_role="engineering-agent/backend-engineer",
            message_id=2,
        )
        p3 = pack_from_discord_message(
            title="t", content="z", author_role="engineering-agent/tech-lead", message_id=3
        )
        merged = merge_packs([p1, p2, p3])
        self.assertEqual(
            set(merged.author_roles),
            {"engineering-agent/tech-lead", "engineering-agent/backend-engineer"},
        )


class AttachmentDedupTestCase(unittest.TestCase):
    def test_attachments_dedup_across_sources(self) -> None:
        att = ResearchAttachment(kind="image", url="https://cdn/x.png")
        s1 = ResearchSource(source_url="https://a", attachments=(att,))
        s2 = ResearchSource(source_url="https://b", attachments=(att,))
        pack = ResearchPack(
            title="t",
            sources=(s1, s2),
        )
        self.assertEqual(len(pack.attachments), 1)


# ---------------------------------------------------------------------------
# Rich source typing (v0.2)
# ---------------------------------------------------------------------------


class SourceTypeEnumTestCase(unittest.TestCase):
    def test_canonical_values_present(self) -> None:
        expected = {
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
        actual = {member.value for member in SourceType}
        self.assertTrue(expected.issubset(actual), f"missing types: {expected - actual}")

    def test_resolved_source_type_default_is_unknown(self) -> None:
        source = ResearchSource()
        self.assertEqual(source.source_type, SourceType.UNKNOWN)


class ClassifyAttachmentTestCase(unittest.TestCase):
    def test_image_by_mime_prefix(self) -> None:
        self.assertEqual(
            classify_attachment(filename="anything.bin", content_type="image/png"),
            SourceType.IMAGE_REFERENCE,
        )

    def test_image_by_extension(self) -> None:
        for filename in ("hero.png", "shot.JPG", "moodboard.heic", "mock.svg"):
            with self.subTest(filename=filename):
                self.assertEqual(
                    classify_attachment(filename=filename),
                    SourceType.IMAGE_REFERENCE,
                )

    def test_non_image_falls_back(self) -> None:
        self.assertEqual(
            classify_attachment(filename="report.pdf", content_type="application/pdf"),
            SourceType.FILE_ATTACHMENT,
        )

    def test_explicit_fallback_can_be_overridden(self) -> None:
        self.assertEqual(
            classify_attachment(filename="x", fallback=SourceType.CODE_CONTEXT),
            SourceType.CODE_CONTEXT,
        )


class NormalizeAttachmentMetadataTestCase(unittest.TestCase):
    def test_lowercases_content_type_and_trims_filename(self) -> None:
        att = ResearchAttachment(
            kind="file",
            url="https://cdn/x.png",
            filename="  hero.PNG  ",
            content_type="  Image/PNG  ",
            size_bytes=1024,
        )
        normalized = normalize_attachment_metadata(att)
        self.assertEqual(normalized.filename, "hero.PNG")
        self.assertEqual(normalized.content_type, "image/png")
        # auto-promote generic file → image when classified as IMAGE_REFERENCE
        self.assertEqual(normalized.kind, "image")

    def test_negative_size_dropped(self) -> None:
        att = ResearchAttachment(kind="file", url="x", size_bytes=-5)
        self.assertIsNone(normalize_attachment_metadata(att).size_bytes)

    def test_non_image_kind_preserved(self) -> None:
        att = ResearchAttachment(
            kind="custom", url="x", filename="x.pdf", content_type="application/pdf"
        )
        self.assertEqual(normalize_attachment_metadata(att).kind, "custom")


class TypedSourceConstructorsTestCase(unittest.TestCase):
    def test_user_message(self) -> None:
        src = source_from_user_message(
            content="hero 섹션 다시 짜야 함",
            collected_by_role="engineering-agent/tech-lead",
            channel_id=999,
            message_id=10,
            why_relevant="요청 본문",
        )
        self.assertEqual(src.source_type, SourceType.USER_MESSAGE)
        self.assertEqual(src.role, "engineering-agent/tech-lead")
        self.assertEqual(src.confidence, "high")
        self.assertEqual(src.why_relevant, "요청 본문")
        self.assertTrue(src.discord_origin)

    def test_url(self) -> None:
        src = source_from_url(
            url="https://example.com/x",
            collected_by_role="engineering-agent/product-designer",
            title="Stripe pricing",
            why_relevant="레퍼런스",
        )
        self.assertEqual(src.source_type, SourceType.URL)
        self.assertEqual(src.source_url, "https://example.com/x")
        self.assertEqual(src.title, "Stripe pricing")

    def test_web_result(self) -> None:
        src = source_from_web_result(
            url="https://example.com/q",
            title="search hit",
            summary="찾은 자료",
            collected_by_role="engineering-agent/tech-lead",
        )
        self.assertEqual(src.source_type, SourceType.WEB_RESULT)
        self.assertEqual(src.summary, "찾은 자료")

    def test_image_reference(self) -> None:
        src = source_from_image_reference(
            url="https://cdn/x.png",
            collected_by_role="engineering-agent/product-designer",
            filename="hero.png",
            content_type="image/png",
            attachment_id="att-1",
        )
        self.assertEqual(src.source_type, SourceType.IMAGE_REFERENCE)
        self.assertEqual(src.attachment_id, "att-1")
        self.assertEqual(len(src.attachments), 1)
        self.assertEqual(src.attachments[0].kind, "image")

    def test_file_attachment_promotes_image(self) -> None:
        # 이미지 파일이 file_attachment 채널로 들어와도 IMAGE_REFERENCE로 자동 분류된다.
        src = source_from_file_attachment(
            url="https://cdn/y.jpg",
            collected_by_role="engineering-agent/product-designer",
            filename="moodboard.JPG",
            content_type="image/jpeg",
        )
        self.assertEqual(src.source_type, SourceType.IMAGE_REFERENCE)
        self.assertEqual(src.attachments[0].kind, "image")

    def test_file_attachment_preserves_for_non_image(self) -> None:
        src = source_from_file_attachment(
            url="https://cdn/report.pdf",
            collected_by_role="engineering-agent/qa-engineer",
            filename="report.pdf",
            content_type="application/pdf",
        )
        self.assertEqual(src.source_type, SourceType.FILE_ATTACHMENT)

    def test_github_issue_and_pr(self) -> None:
        issue = source_from_github_issue(
            url="https://github.com/o/r/issues/1",
            title="bug X",
            collected_by_role="engineering-agent/qa-engineer",
            issue_number=1,
            repository="o/r",
        )
        self.assertEqual(issue.source_type, SourceType.GITHUB_ISSUE)
        self.assertEqual(issue.extra["github"]["kind"], "issue")
        pr = source_from_github_pr(
            url="https://github.com/o/r/pull/2",
            title="feature Y",
            collected_by_role="engineering-agent/backend-engineer",
            pr_number=2,
            repository="o/r",
            state="open",
        )
        self.assertEqual(pr.source_type, SourceType.GITHUB_PR)
        self.assertEqual(pr.extra["github"]["state"], "open")

    def test_code_context(self) -> None:
        src = source_from_code_context(
            repo_path="src/yule_orchestrator/agents/research_pack.py",
            summary="ResearchPack 정의 위치",
            collected_by_role="engineering-agent/backend-engineer",
            line_range=(100, 130),
        )
        self.assertEqual(src.source_type, SourceType.CODE_CONTEXT)
        self.assertEqual(src.extra["repo_path"], "src/yule_orchestrator/agents/research_pack.py")
        self.assertEqual(src.extra["line_range"], [100, 130])

    def test_official_docs(self) -> None:
        src = source_from_official_docs(
            url="https://react.dev/learn",
            title="React docs",
            collected_by_role="engineering-agent/frontend-engineer",
            publisher="React",
        )
        self.assertEqual(src.source_type, SourceType.OFFICIAL_DOCS)
        self.assertEqual(src.extra["publisher"], "React")

    def test_community_signal(self) -> None:
        src = source_from_community_signal(
            url="https://reddit.com/r/x",
            title="reddit thread",
            collected_by_role="engineering-agent/frontend-engineer",
            platform="reddit",
        )
        self.assertEqual(src.source_type, SourceType.COMMUNITY_SIGNAL)
        # 기본 confidence는 low (커뮤니티 신호는 검증 전엔 약함)
        self.assertEqual(src.confidence, "low")

    def test_design_reference(self) -> None:
        src = source_from_design_reference(
            url="https://www.behance.net/x",
            title="behance moodboard",
            collected_by_role="engineering-agent/product-designer",
            platform="behance",
        )
        self.assertEqual(src.source_type, SourceType.DESIGN_REFERENCE)
        self.assertEqual(src.extra["platform"], "behance")


class ResearchRequestAndFindingTestCase(unittest.TestCase):
    def test_make_request_assigns_id_and_timestamp(self) -> None:
        req = make_research_request(
            topic="hero 정리",
            role="engineering-agent/tech-lead",
            session_id="sess-1",
        )
        self.assertTrue(req.request_id.startswith("req-"))
        self.assertEqual(req.role, "engineering-agent/tech-lead")
        self.assertEqual(req.session_id, "sess-1")
        self.assertIsNotNone(req.created_at)

    def test_make_finding_with_supporting_ids(self) -> None:
        finding = make_finding(
            title="시안 채택 안",
            summary="hero 카피 단순화 + CTA 색 강조",
            role="engineering-agent/product-designer",
            supporting_source_ids=("abc", "def"),
            confidence="high",
            risk_or_limit="모바일 그리드 미검증",
        )
        self.assertTrue(finding.finding_id.startswith("find-"))
        self.assertEqual(finding.role, "engineering-agent/product-designer")
        self.assertEqual(finding.supporting_source_ids, ("abc", "def"))
        self.assertEqual(finding.confidence, "high")


class PackFromRequestTestCase(unittest.TestCase):
    def test_includes_request_and_sources(self) -> None:
        req = make_research_request(
            topic="새 hero",
            role="engineering-agent/product-designer",
            session_id="sess-1",
        )
        s1 = source_from_url(
            url="https://stripe.com/pricing",
            collected_by_role="engineering-agent/product-designer",
            title="stripe pricing",
        )
        s2 = source_from_design_reference(
            url="https://www.behance.net/x",
            title="behance hero",
            collected_by_role="engineering-agent/product-designer",
        )
        pack = pack_from_request(
            request=req,
            sources=(s1, s2),
            tags=("research",),
        )
        self.assertIs(pack.request, req)
        self.assertEqual(pack.title, "새 hero")
        self.assertEqual(pack.primary_url, "https://stripe.com/pricing")
        self.assertEqual(set(pack.urls), {"https://stripe.com/pricing", "https://www.behance.net/x"})


class StableIdAndDedupTestCase(unittest.TestCase):
    def test_stable_id_is_consistent(self) -> None:
        s = ResearchSource(source_url="https://a", message_id=1)
        self.assertEqual(s.stable_id, ResearchSource(source_url="https://a", message_id=1).stable_id)

    def test_dedup_distinguishes_source_type(self) -> None:
        # 같은 메시지에서 user_message와 image_reference가 모두 들어올 수 있다 — 별개로 보존.
        same_msg = dict(message_id=1, channel_id=10, source_url=None)
        s_msg = ResearchSource(source_type=SourceType.USER_MESSAGE, **same_msg)
        s_img = ResearchSource(source_type=SourceType.IMAGE_REFERENCE, **same_msg)
        pack = ResearchPack(title="t", sources=(s_msg,))
        result = pack_with_extra_source(pack, s_img)
        self.assertEqual(len(result.sources), 2)


class PackToDictTestCase(unittest.TestCase):
    def test_round_trip_via_json(self) -> None:
        import json

        req = make_research_request(
            topic="topic",
            role="engineering-agent/tech-lead",
            session_id="sess",
        )
        s1 = source_from_user_message(
            content="새 hero",
            collected_by_role="engineering-agent/tech-lead",
            channel_id=999,
            message_id=1,
        )
        s2 = source_from_image_reference(
            url="https://cdn/x.png",
            collected_by_role="engineering-agent/product-designer",
            filename="hero.png",
            content_type="image/png",
        )
        pack = pack_from_request(request=req, sources=(s1, s2))
        finding = make_finding(
            title="채택 안",
            summary="hero copy 단순화",
            role="engineering-agent/product-designer",
            supporting_source_ids=(s2.stable_id,),
        )
        pack = pack_with_finding(pack, finding)

        as_dict = pack_to_dict(pack)
        # JSON 직렬화 가능해야 함 — 외부 transport(SQLite cache 등) 호환.
        rendered = json.dumps(as_dict, ensure_ascii=False)
        self.assertIn("user_message", rendered)
        self.assertIn("image_reference", rendered)
        self.assertIn("hero copy", rendered)
        # 핵심 필드
        self.assertEqual(as_dict["request"]["topic"], "topic")
        self.assertEqual(as_dict["sources"][0]["source_type"], "user_message")
        self.assertEqual(as_dict["sources"][1]["source_type"], "image_reference")
        self.assertEqual(as_dict["findings"][0]["title"], "채택 안")


class PackToMarkdownTestCase(unittest.TestCase):
    def test_renders_grouped_sections_and_findings(self) -> None:
        req = make_research_request(
            topic="새 hero",
            role="engineering-agent/tech-lead",
            session_id="sess-1",
        )
        sources = (
            source_from_user_message(
                content="hero 섹션 정리해줘",
                collected_by_role="engineering-agent/tech-lead",
                channel_id=999,
                message_id=1,
            ),
            source_from_url(
                url="https://stripe.com/pricing",
                collected_by_role="engineering-agent/product-designer",
                title="stripe pricing",
                why_relevant="step copy 강조 패턴",
            ),
            source_from_image_reference(
                url="https://cdn/hero.png",
                collected_by_role="engineering-agent/product-designer",
                filename="hero.png",
                content_type="image/png",
            ),
            source_from_official_docs(
                url="https://react.dev/learn",
                title="react docs",
                collected_by_role="engineering-agent/frontend-engineer",
                publisher="React",
            ),
            source_from_github_issue(
                url="https://github.com/o/r/issues/1",
                title="기존 hero 회귀",
                collected_by_role="engineering-agent/qa-engineer",
                issue_number=1,
                repository="o/r",
            ),
        )
        pack = pack_from_request(
            request=req,
            sources=sources,
            tags=("research", "ux"),
            summary="hero 정리 작업",
        )
        finding = make_finding(
            title="hero copy 단순화 채택",
            summary="3줄 → 2줄, CTA 색 강조",
            role="engineering-agent/product-designer",
            supporting_source_ids=(sources[1].stable_id, sources[2].stable_id),
            confidence="high",
        )
        pack = pack_with_finding(pack, finding)

        md = pack_to_markdown(pack)
        # 헤더와 요약
        self.assertIn("# 새 hero", md)
        self.assertIn("> hero 정리 작업", md)
        # 요청 블록
        self.assertIn("## 요청", md)
        self.assertIn("`engineering-agent/tech-lead`", md)
        # source_type별 그룹 헤딩
        self.assertIn("## 출처 — user_message", md)
        self.assertIn("## 출처 — url", md)
        self.assertIn("## 출처 — image_reference", md)
        self.assertIn("## 출처 — official_docs", md)
        self.assertIn("## 출처 — github_issue", md)
        # 발견 사항
        self.assertIn("## 발견 사항", md)
        self.assertIn("hero copy 단순화 채택", md)
        # 태그
        self.assertIn("`research`", md)


class BackwardCompatTestCase(unittest.TestCase):
    """Make sure existing callers (forum adapter / obsidian export) keep working."""

    def test_pack_from_discord_message_still_returns_user_message_typed(self) -> None:
        pack = pack_from_discord_message(
            title="t",
            content="https://a",
            author_role="engineering-agent/tech-lead",
            message_id=1,
        )
        self.assertEqual(pack.sources[0].source_type, SourceType.USER_MESSAGE)
        self.assertEqual(pack.sources[0].author_role, "engineering-agent/tech-lead")
        # role/timestamp 합성 속성도 동작
        self.assertEqual(pack.sources[0].role, "engineering-agent/tech-lead")

    def test_legacy_constructor_without_new_fields(self) -> None:
        # 기존 callers는 새 필드 없이도 ResearchSource를 만들 수 있어야 한다.
        s = ResearchSource(source_url="https://x", title="t")
        self.assertEqual(s.source_type, SourceType.UNKNOWN)
        self.assertIsNone(s.collected_by_role)


if __name__ == "__main__":
    unittest.main()
