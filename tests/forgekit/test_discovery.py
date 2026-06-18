"""Idea-discovery + video-watch (WT3) — real briefs/gap map, honest video ingest.

Proves: a "SaaS 아이디어" request yields a ReferenceBundle + CompetitorGapMap + IdeaBrief,
a high-value brief promotes to a PM handoff, self-improve signals split out, and
video-watch summarises operator transcript/notes but is reference_only (no fake fetch)
for a bare link. Pure → bare CI.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import discovery as D
from forgekit_console.discovery import models as M
from forgekit_console.discovery import video_watch as V


class IdeaDiscoveryTests(unittest.TestCase):
    _SIGNALS = [
        "노트 앱 동기화가 느려서 불편하다",          # pain
        "AI 메모 정리 트렌드가 급상승",               # trend
        "기존 제품 Notion 은 오프라인이 약함",        # competitor
        "forgekit 콘솔 자체 도움말이 부족하다",       # self-improve
    ]

    def test_pipeline_produces_bundle_gapmap_briefs(self) -> None:
        res = D.run_idea_discovery(self._SIGNALS, title="SaaS 아이디어")
        self.assertTrue(res.reference_bundle.items)              # ReferenceBundle
        self.assertTrue(res.gap_map.competitors)                 # competitor captured
        self.assertTrue(res.gap_map.gaps)                        # pain → gap
        self.assertTrue(res.idea_briefs)                         # IdeaBrief(s)
        top = res.top_brief
        self.assertTrue(top.differentiation.hypothesis)
        self.assertTrue(top.next_experiment.experiment)

    def test_self_improve_signal_splits_out(self) -> None:
        res = D.run_idea_discovery(self._SIGNALS)
        self.assertTrue(res.self_improve_signals)                # forgekit-improve split
        self.assertTrue(all(s.kind == M.SIGNAL_SELF_IMPROVE for s in res.self_improve_signals))

    def test_high_value_brief_promotes_to_handoff(self) -> None:
        res = D.run_idea_discovery(self._SIGNALS)
        ho = D.promote_to_handoff(res.top_brief, project="idea")
        self.assertEqual(ho.trace[-1].phase, "tech-lead")         # real WT2 handoff
        self.assertTrue(ho.split.tasks)

    def test_works_on_source_items(self) -> None:
        from forgekit_console.sources.contract import SourceItem

        items = [SourceItem("hackernews", "동기화 느림 불편", score=10.0)]
        res = D.run_idea_discovery(items)
        self.assertEqual(res.reference_bundle.items[0]["source_id"], "hackernews")


class VideoWatchTests(unittest.TestCase):
    def test_transcript_is_summarised_live(self) -> None:
        ing = V.VideoIngest(link="http://x", transcript=(
            "이 영상은 노트 앱의 동기화 불편을 다룬다. 오프라인 편집이 안 되는 문제가 크다. "
            "대안으로 로컬 우선 동기화를 제안한다."))
        res = V.summarize_ingest(ing)
        self.assertEqual(res.status, V.STATUS_LIVE)
        self.assertTrue(res.summary)
        self.assertTrue(res.ideas)               # ideas extracted from the transcript

    def test_link_only_is_reference_only_no_fake_fetch(self) -> None:
        res = V.summarize_ingest(V.VideoIngest(link="https://youtube.com/watch?v=x"))
        self.assertEqual(res.status, V.STATUS_REFERENCE_ONLY)   # honest — no fake crawl
        self.assertEqual(res.ideas, ())
        self.assertIn("planned", res.note)
        self.assertEqual(res.reference["link"], "https://youtube.com/watch?v=x")


if __name__ == "__main__":
    unittest.main()
