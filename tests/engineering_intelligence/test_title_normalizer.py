"""Title normalizer — knowledge / engineering-knowledge visible-title scrub.

Scope: titles produced by the engineering-knowledge note pipeline plus
its issue-collection / research-forum / GeekNews-style feeders. The
filename rule that prefixes ``YYYY-MM-DD_<kind>-<slug>.md`` lives in
``recommend_path`` and stays untouched — these tests only cover the
*visible* title (frontmatter ``title:`` / Markdown H1 / Discord digest
bullet / ObsidianWriteRequest title).
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.engineering_intelligence.discord_summary import (
    render_daily_role_summary,
)
from yule_orchestrator.agents.engineering_intelligence.models import (
    Audience,
    CagContext,
    EngineeringKnowledgeItem,
    Importance,
    LearningLevel,
    PracticeVerification,
    SourceKind,
)
from yule_orchestrator.agents.engineering_intelligence.obsidian import (
    build_engineering_knowledge_write_request,
    shareable_external_payload,
)
from yule_orchestrator.agents.engineering_intelligence.renderer import (
    render_engineering_knowledge_note,
    render_frontmatter,
)
from yule_orchestrator.agents.engineering_intelligence.title_normalizer import (
    DEFAULT_TITLE_MAX_CHARS,
    canonical_engineering_title,
    display_title_for,
)


def _good_item(**overrides) -> EngineeringKnowledgeItem:
    base = dict(
        item_id="dev-spring-6",
        topic_key="spring-6-2-virtual-thread",
        title="Spring 6.2 Virtual Thread 권장",
        role="backend-engineer",
        stack_tags=("spring", "java"),
        source_name="Spring Engineering Blog",
        source_url="https://spring.io/blog/2026/05/01/spring-6-2-vt",
        source_kind=SourceKind.ENGINEERING_BLOG,
        collected_at="2026-05-08T03:00:00Z",
        importance=Importance.HIGH,
        audience=Audience.JUNIOR,
        summary="Spring 6.2 부터 가상 스레드 사용 패턴이 정리됐다.",
        why_it_matters="Tomcat 11 spike 대응 권장 설정이 바뀌었다.",
        what_changed="virtual.enabled 기본값 변경.",
        practical_impact="기존 RestController 응답 지연이 줄어든다.",
        recommended_action="ThreadLocal 사용처 마이그레이션 검토.",
        practice_topic="Spring 6.2 가상 스레드 마이그레이션 점검",
        practice_goal="기본 설정으로 켜고 ThreadLocal 안전 확인",
        practice_steps=(
            "spring-boot 3.4 + spring-framework 6.2 실험 프로젝트 생성",
            "RestController 한 개를 가상 스레드로 전환",
        ),
        practice_checklist=("endpoint 응답이 5xx 가 아니다",),
        expected_output="vt 기본 endpoint 동작",
        common_mistakes=("ThreadLocal 을 그대로 두고 가상 스레드만 켜기",),
        practice_verification=PracticeVerification(
            expected_result="2xx 응답 + 로그 깨끗",
            command_to_run="./gradlew bootRun",
        ),
        rag_tags=("spring", "virtual-thread"),
        cag_context_key="spring-6-2-vt-when-traffic-spikes",
        cag_context=CagContext(
            when_to_use="spring 6.2+ 도입 시 vt 회귀 의심 시",
        ),
        retrieval_queries=("Spring 6.2 vt?", "ThreadLocal 누수?"),
        retrieval_summary="vt 마이그레이션 회귀 신호",
        learning_level=LearningLevel.INTERMEDIATE,
        review_after_days=90,
        references=("https://spring.io/blog/2026/05/01/spring-6-2-vt",),
        confidence=0.8,
        dedup_key="eng-knowledge:backend:abc",
    )
    base.update(overrides)
    return EngineeringKnowledgeItem(**base)


# ---------------------------------------------------------------------------
# Pure-function scrub tests
# ---------------------------------------------------------------------------


class CanonicalEngineeringTitleTests(unittest.TestCase):
    def test_empty_returns_empty(self) -> None:
        self.assertEqual(canonical_engineering_title(""), "")
        self.assertEqual(canonical_engineering_title(None), "")  # type: ignore[arg-type]

    def test_clean_title_passes_through(self) -> None:
        self.assertEqual(
            canonical_engineering_title("Spring 6.2 Virtual Thread 권장"),
            "Spring 6.2 Virtual Thread 권장",
        )

    def test_iso_date_prefix_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title("2026-05-08 Spring 6.2 권장"),
            "Spring 6.2 권장",
        )

    def test_iso_date_in_brackets_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title("[2026-05-08] Spring 6.2 권장"),
            "Spring 6.2 권장",
        )

    def test_dotted_date_prefix_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title("2026.05.08 — Spring 6.2 권장"),
            "Spring 6.2 권장",
        )

    def test_korean_date_prefix_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title("2026년 5월 8일 - Spring 6.2 권장"),
            "Spring 6.2 권장",
        )

    def test_trailing_date_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title("Spring 6.2 권장 (2026-05-08)"),
            "Spring 6.2 권장",
        )

    def test_geeknews_label_prefix_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title("[GeekNews] Spring 6.2 권장"),
            "Spring 6.2 권장",
        )

    def test_hn_label_prefix_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title("[HN] Show: tiny rust http"),
            "Show: tiny rust http",
        )

    def test_geeknews_trailing_site_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title("Spring 6.2 권장 — GeekNews"),
            "Spring 6.2 권장",
        )

    def test_hacker_news_trailing_pipe_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title("Tiny rust http | Hacker News"),
            "Tiny rust http",
        )

    def test_korean_colon_label_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title("이슈: Spring 6.2 권장"),
            "Spring 6.2 권장",
        )
        self.assertEqual(
            canonical_engineering_title("공유: 새 RAG 평가 지표"),
            "새 RAG 평가 지표",
        )
        self.assertEqual(
            canonical_engineering_title("리서치: faithfulness 측정"),
            "faithfulness 측정",
        )

    def test_reply_prefix_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title("Re: Spring 6.2 권장"),
            "Spring 6.2 권장",
        )
        self.assertEqual(
            canonical_engineering_title("Fwd: Spring 6.2 권장"),
            "Spring 6.2 권장",
        )

    def test_combined_prefixes_collapse(self) -> None:
        # Discord 이슈방 paste가 가장 더러운 케이스 — 날짜 + 답글 라벨 +
        # 한국어 라벨 + GeekNews suffix가 한 번에 붙어 있어도 모두 떨어진다.
        title = "Re: [GeekNews] 2026-05-08 이슈: Spring 6.2 권장 (GeekNews)"
        self.assertEqual(
            canonical_engineering_title(title),
            "Spring 6.2 권장",
        )

    def test_inline_url_removed(self) -> None:
        self.assertEqual(
            canonical_engineering_title(
                "Spring 6.2 권장 https://spring.io/blog/x"
            ),
            "Spring 6.2 권장",
        )

    def test_outer_quotes_stripped(self) -> None:
        self.assertEqual(
            canonical_engineering_title('"Spring 6.2 권장"'),
            "Spring 6.2 권장",
        )
        self.assertEqual(
            canonical_engineering_title("「Spring 6.2 권장」"),
            "Spring 6.2 권장",
        )

    def test_secret_token_redacted_in_title(self) -> None:
        out = canonical_engineering_title(
            "Spring 6.2: GH_TOKEN=ghp_aaaaaaaaaaaaaaaaaaaaaa 로 export"
        )
        self.assertNotIn("ghp_aaaaaaaaaaaaaaaaaaaaaa", out)
        self.assertIn("[redacted-github-token]", out)

    def test_long_title_truncated_with_ellipsis(self) -> None:
        long = (
            "Spring 6.2 가상 스레드 마이그레이션 시 ThreadLocal 사용처를 "
            "ScopedValue 로 옮기는 방법과 회귀 검증 체크리스트 정리"
        )
        out = canonical_engineering_title(long, max_chars=40)
        self.assertLessEqual(len(out), 41)  # cap + possible "…"
        self.assertTrue(out.endswith("…") or len(out) <= 40)

    def test_prompt_like_long_body_sliced_to_first_sentence(self) -> None:
        prompt = (
            "다음 사항을 검토해 줘. Spring 6.2 가상 스레드를 도입할 때 "
            "ThreadLocal 사용처가 안전한지, ScopedValue 로 옮길 후보가 "
            "어떻게 되는지 정리해 줘. 그리고 회귀 검증 시나리오도 함께."
        )
        out = canonical_engineering_title(prompt)
        # 첫 문장만 살아남고 길이가 cap 안.
        self.assertLessEqual(len(out), DEFAULT_TITLE_MAX_CHARS + 1)
        self.assertNotIn("ScopedValue", out)
        self.assertNotIn("회귀 검증", out)

    def test_multi_line_paste_uses_first_line(self) -> None:
        pasted = "Spring 6.2 권장\n\nThreadLocal 누수 가능성 점검 필요\n추가 메모"
        out = canonical_engineering_title(pasted)
        self.assertEqual(out, "Spring 6.2 권장")

    def test_only_label_returns_empty(self) -> None:
        # `[GeekNews]` 만 들어오면 본문이 사라져 빈 문자열이 된다 — 이건
        # 호출자(display_title_for) 가 fallback 으로 처리한다.
        self.assertEqual(canonical_engineering_title("[GeekNews]"), "")
        self.assertEqual(canonical_engineering_title("이슈:"), "")

    def test_display_title_falls_back_when_scrub_empties(self) -> None:
        item = _good_item(title="[GeekNews]")
        self.assertEqual(display_title_for(item), "(제목 미정)")

    def test_display_title_falls_back_when_input_blank(self) -> None:
        class Stub:
            title = ""

        self.assertEqual(display_title_for(Stub()), "(제목 미정)")


# ---------------------------------------------------------------------------
# Renderer / obsidian / discord wiring tests
# ---------------------------------------------------------------------------


class RendererUsesCanonicalTitleTests(unittest.TestCase):
    """렌더된 frontmatter / H1 가 날짜·라벨이 빠진 깨끗한 제목을 쓴다."""

    def test_frontmatter_title_drops_date_prefix(self) -> None:
        item = _good_item(title="2026-05-08 Spring 6.2 권장")
        fm = render_frontmatter(item)
        self.assertIn('title: "Spring 6.2 권장"', fm)
        self.assertNotIn("2026-05-08", fm.split("\n", 1)[0])

    def test_frontmatter_title_drops_geeknews_label(self) -> None:
        item = _good_item(title="[GeekNews] Spring 6.2 권장 — GeekNews")
        fm = render_frontmatter(item)
        self.assertIn('title: "Spring 6.2 권장"', fm)
        self.assertNotIn("GeekNews", fm.split("\n", 1)[0])

    def test_h1_drops_aggregator_label_and_date(self) -> None:
        item = _good_item(title="Re: [GeekNews] 2026-05-08 이슈: Spring 6.2 권장")
        body = render_engineering_knowledge_note(item)
        # H1 line is the first '# ' line.
        h1 = [
            line for line in body.splitlines() if line.startswith("# ")
        ][0]
        self.assertEqual(h1, "# Spring 6.2 권장")

    def test_h1_does_not_carry_url(self) -> None:
        item = _good_item(
            title="Spring 6.2 권장 https://spring.io/blog/x"
        )
        body = render_engineering_knowledge_note(item)
        h1 = [
            line for line in body.splitlines() if line.startswith("# ")
        ][0]
        self.assertNotIn("https://", h1)

    def test_h1_truncates_prompt_paste(self) -> None:
        item = _good_item(
            title=(
                "다음 사항을 검토해 줘. Spring 6.2 가상 스레드를 도입할 때 "
                "ThreadLocal 사용처가 안전한지 정리해 줘."
            )
        )
        body = render_engineering_knowledge_note(item)
        h1 = [
            line for line in body.splitlines() if line.startswith("# ")
        ][0]
        # 프롬프트 본문이 통째로 H1 으로 나오면 안 된다.
        self.assertNotIn("ThreadLocal", h1)

    def test_collected_at_date_still_in_version_table(self) -> None:
        # 가시 제목은 날짜를 빼지만, 문서 버전 표의 작성일은 그대로 남는다.
        item = _good_item(title="2026-05-08 Spring 6.2 권장")
        body = render_engineering_knowledge_note(item)
        self.assertIn("2026-05-08", body)


class ObsidianRequestUsesCanonicalTitleTests(unittest.TestCase):
    def test_request_title_is_scrubbed(self) -> None:
        item = _good_item(title="[GeekNews] 2026-05-08 Spring 6.2 권장")
        request = build_engineering_knowledge_write_request(item)
        self.assertIsNotNone(request)
        self.assertEqual(request.title, "Spring 6.2 권장")

    def test_request_title_capped_at_80(self) -> None:
        long_title = "Spring 6.2 가상 스레드 마이그레이션 회고 — " + ("아주 긴 부제 " * 10)
        item = _good_item(title=long_title)
        request = build_engineering_knowledge_write_request(item)
        self.assertIsNotNone(request)
        self.assertLessEqual(len(request.title), 80)

    def test_shareable_payload_public_title_scrubbed(self) -> None:
        item = _good_item(title="Re: [GeekNews] Spring 6.2 권장 — GeekNews")
        payload = shareable_external_payload(item)
        self.assertEqual(payload["title"], "Spring 6.2 권장")


class DiscordDigestUsesCanonicalTitleTests(unittest.TestCase):
    def test_digest_line_drops_label_and_date(self) -> None:
        item = _good_item(title="[GeekNews] 2026-05-08 Spring 6.2 권장")
        text = render_daily_role_summary(
            "backend-engineer", [item], today="2026-05-08"
        )
        self.assertIn("**Spring 6.2 권장**", text)
        # Aggregator label / leading date never reach the bullet line.
        self.assertNotIn("[GeekNews]", text)
        self.assertNotIn("**2026-05-08", text)


if __name__ == "__main__":
    unittest.main()
