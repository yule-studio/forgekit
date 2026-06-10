"""research_topic ledger + thread_snapshot helpers — A-M7.6 unit tests.

Pin the pure helpers the M7.6 wiring builds on:

  * topic key derivation — same prompt + thread → same key,
    different threads stay distinct.
  * title normalisation strips ``[Research]`` prefixes + caps len.
  * ledger record round-trip through ``session.extra``.
  * status transitions preserve key / thread / title.
  * thread snapshot link extraction + role bucketing + caps.
  * empty-snapshot detector for the renderer's empty-note guard.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.lifecycle.research_topic import (
    DEFAULT_TITLE_MAX_LEN,
    STATUS_PENDING_APPROVAL,
    STATUS_RESEARCHING,
    STATUS_SAVED,
    TopicLedgerRecord,
    build_ledger_record,
    derive_topic_key,
    normalize_research_title,
    read_topic_ledger,
    transition_topic_ledger,
    write_topic_ledger,
)
from yule_engineering.agents.lifecycle.thread_snapshot import (
    DEFAULT_MAX_LINKS,
    ThreadMessage,
    ThreadSnapshot,
    collapse_thread_to_snapshot,
    extract_links_from_text,
    render_thread_snapshot_block,
)


# ---------------------------------------------------------------------------
# Title normalisation
# ---------------------------------------------------------------------------


class TitleNormalisationTests(unittest.TestCase):
    def test_research_prefix_stripped(self) -> None:
        for raw in (
            "[Research] DevOps 학습 로드맵",
            "[Research]: DevOps 학습 로드맵",
            "운영-리서치: DevOps 학습 로드맵",
            "[research] devops 학습 로드맵",
        ):
            with self.subTest(raw=raw):
                cleaned = normalize_research_title(raw)
                self.assertNotIn("Research", cleaned)
                self.assertNotIn("research", cleaned.lower())
                self.assertIn("DevOps", cleaned) if "DevOps" in raw else self.assertIn("devops", cleaned)

    def test_long_prompt_capped_at_max_len(self) -> None:
        raw = "오늘은 DevOps 엔지니어가 되려면 어떤 걸 어떻게 공부해야 될지 알고 싶어. " * 5
        result = normalize_research_title(raw, max_len=40)
        self.assertLessEqual(len(result), 40)
        # No mid-syllable cut — should end on a word boundary.
        self.assertFalse(result.endswith(" "))

    def test_empty_or_whitespace_returns_empty_string(self) -> None:
        self.assertEqual(normalize_research_title(None), "")
        self.assertEqual(normalize_research_title(""), "")
        self.assertEqual(normalize_research_title("   "), "")


# ---------------------------------------------------------------------------
# Topic key derivation
# ---------------------------------------------------------------------------


class TopicKeyDerivationTests(unittest.TestCase):
    def test_same_prompt_same_thread_same_key(self) -> None:
        a = derive_topic_key(prompt="DevOps 학습 로드맵", research_thread_id=12345)
        b = derive_topic_key(prompt="DevOps 학습 로드맵", research_thread_id=12345)
        self.assertEqual(a, b)
        self.assertTrue(a)

    def test_same_prompt_different_threads_distinct_keys(self) -> None:
        a = derive_topic_key(prompt="DevOps 학습", research_thread_id=11)
        b = derive_topic_key(prompt="DevOps 학습", research_thread_id=22)
        self.assertNotEqual(a, b)

    def test_semantic_title_overrides_prompt(self) -> None:
        a = derive_topic_key(
            prompt="긴 자유 형식 요청…",
            semantic_title="k8s node pool split",
            research_thread_id=99,
        )
        self.assertIn("k8s", a)

    def test_research_prefix_stripped_in_key(self) -> None:
        key = derive_topic_key(
            prompt="[Research] DevOps 엔지니어 학습", research_thread_id=1
        )
        self.assertNotIn("research", key)
        self.assertIn("devops", key)

    def test_empty_inputs_yield_empty_key(self) -> None:
        self.assertEqual(derive_topic_key(prompt=None), "")
        self.assertEqual(derive_topic_key(prompt="", research_thread_id=None), "")


# ---------------------------------------------------------------------------
# Ledger round-trip through session.extra
# ---------------------------------------------------------------------------


class LedgerPersistenceTests(unittest.TestCase):
    def _session(self, *, prompt: str, thread_id: int = 50001):
        when = datetime.now(tz=timezone.utc)
        return SimpleNamespace(
            session_id="sess-ledger-1",
            prompt=prompt,
            extra={"research_forum_thread_id": thread_id},
            updated_at=when.isoformat(),
        )

    def test_build_and_round_trip(self) -> None:
        session = self._session(prompt="DevOps 학습 로드맵")
        record = build_ledger_record(
            session=session,
            research_thread_id=50001,
            active_roles=("tech-lead", "devops-engineer"),
        )
        self.assertEqual(record.status, STATUS_RESEARCHING)
        self.assertTrue(record.topic_key)
        self.assertIn("devops", record.canonical_title.lower())
        new_extra = write_topic_ledger(session.extra, record)
        # Read-back via the canonical reader.
        rehydrated = read_topic_ledger(
            SimpleNamespace(extra=new_extra)
        )
        self.assertIsNotNone(rehydrated)
        assert rehydrated is not None
        self.assertEqual(rehydrated.topic_key, record.topic_key)
        self.assertEqual(
            tuple(rehydrated.active_roles),
            ("tech-lead", "devops-engineer"),
        )

    def test_transition_preserves_identity(self) -> None:
        session = self._session(prompt="k8s 노드 풀 분리")
        record = build_ledger_record(
            session=session, research_thread_id=42
        )
        moved = transition_topic_ledger(
            record,
            status=STATUS_PENDING_APPROVAL,
            approval_job_id="apv-1",
        )
        self.assertEqual(moved.status, STATUS_PENDING_APPROVAL)
        self.assertEqual(moved.topic_key, record.topic_key)
        self.assertEqual(moved.canonical_title, record.canonical_title)
        self.assertEqual(moved.approval_job_id, "apv-1")

    def test_revision_bump_increments_counter(self) -> None:
        session = self._session(prompt="x")
        record = build_ledger_record(
            session=session, research_thread_id=10
        )
        moved = transition_topic_ledger(
            record, status=STATUS_SAVED, vault_path="/v/x.md", revision_bump=True
        )
        self.assertEqual(moved.revision, record.revision + 1)
        self.assertEqual(moved.vault_path, "/v/x.md")


# ---------------------------------------------------------------------------
# Thread snapshot — link extraction + role bucketing + caps
# ---------------------------------------------------------------------------


class LinkExtractionTests(unittest.TestCase):
    def test_extracts_unique_urls_in_order(self) -> None:
        text = (
            "참고: https://kubernetes.io/docs/ 그리고 "
            "https://example.com/foo 보고. "
            "다시 https://kubernetes.io/docs/ 도 보면 좋아."
        )
        urls = extract_links_from_text(text)
        self.assertEqual(
            urls, ("https://kubernetes.io/docs/", "https://example.com/foo")
        )

    def test_strips_trailing_punctuation(self) -> None:
        urls = extract_links_from_text("see https://example.com/x.md.")
        self.assertEqual(urls, ("https://example.com/x.md",))

    def test_caps_at_max_links(self) -> None:
        text = " ".join(f"https://e.com/{i}" for i in range(50))
        urls = extract_links_from_text(text, max_links=10)
        self.assertEqual(len(urls), 10)


class CollapseThreadTests(unittest.TestCase):
    def _msg(self, content: str, *, author: str = "u", is_bot: bool = False):
        return SimpleNamespace(
            content=content,
            author=SimpleNamespace(name=author, global_name=author, bot=is_bot),
            id=hash(content),
            created_at=None,
        )

    def test_role_resolver_buckets_by_role(self) -> None:
        msgs = [
            self._msg("tech-lead 의견 1", author="techbot", is_bot=True),
            self._msg("devops 자료 보강", author="devops_bot", is_bot=True),
            self._msg("사용자 코멘트", author="masterway"),
        ]

        def resolver(author):
            name = getattr(author, "name", "")
            if "tech" in name:
                return "tech-lead"
            if "devops" in name:
                return "devops-engineer"
            return None

        snap = collapse_thread_to_snapshot(msgs, role_resolver=resolver)
        self.assertEqual(len(snap.messages), 3)
        self.assertIn("tech-lead", snap.role_summaries)
        self.assertIn("devops-engineer", snap.role_summaries)
        self.assertNotIn(None, snap.role_summaries)

    def test_extracts_links_from_full_thread(self) -> None:
        msgs = [
            self._msg("see https://k8s.io/x"),
            self._msg("also https://example.com/y"),
        ]
        snap = collapse_thread_to_snapshot(msgs)
        self.assertEqual(
            tuple(snap.extracted_links),
            ("https://k8s.io/x", "https://example.com/y"),
        )

    def test_truncates_long_message_with_marker(self) -> None:
        big = "a" * 1500
        snap = collapse_thread_to_snapshot(
            [self._msg(big)], max_chars_per_message=200
        )
        kept = snap.messages[0].content
        self.assertLessEqual(len(kept), 200)
        self.assertTrue(kept.endswith("(…)"))

    def test_caps_at_max_messages(self) -> None:
        msgs = [self._msg(f"m{i}") for i in range(40)]
        snap = collapse_thread_to_snapshot(msgs, max_messages=10)
        self.assertEqual(len(snap.messages), 10)
        # Most recent kept.
        self.assertEqual(snap.messages[-1].content, "m39")

    def test_empty_snapshot_is_empty_property(self) -> None:
        empty = ThreadSnapshot()
        self.assertTrue(empty.is_empty)
        with_msg = ThreadSnapshot(
            messages=(ThreadMessage(author="x", content="hello"),)
        )
        self.assertFalse(with_msg.is_empty)


class RenderSnapshotBlockTests(unittest.TestCase):
    def test_block_includes_links_roles_and_excerpt(self) -> None:
        snap = ThreadSnapshot(
            messages=(
                ThreadMessage(author="masterway", content="합의 정리하자"),
                ThreadMessage(author="devops", content="rollout 정책 합의됨", role="devops-engineer"),
            ),
            extracted_links=("https://k8s.io/x",),
            role_summaries={"devops-engineer": "rollout 정책 합의됨"},
        )
        text = render_thread_snapshot_block(snap)
        self.assertIn("수집 자료 링크", text)
        self.assertIn("https://k8s.io/x", text)
        self.assertIn("역할별 검토 요약", text)
        self.assertIn("devops-engineer", text)
        self.assertIn("운영-리서치 thread 발췌", text)
        self.assertIn("masterway", text)


# ---------------------------------------------------------------------------
# Round-trip ThreadSnapshot through to_payload / from_payload
# ---------------------------------------------------------------------------


class SnapshotPayloadRoundTripTests(unittest.TestCase):
    def test_round_trip_preserves_all_fields(self) -> None:
        original = ThreadSnapshot(
            messages=(
                ThreadMessage(
                    author="masterway",
                    content="합의 정리",
                    role=None,
                    posted_at="2026-05-08T10:00:00+00:00",
                ),
            ),
            extracted_links=("https://k8s.io/x",),
            role_summaries={"tech-lead": "approve"},
            captured_at="2026-05-08T10:30:00+00:00",
        )
        payload = original.to_payload()
        rehydrated = ThreadSnapshot.from_payload(payload)
        self.assertEqual(len(rehydrated.messages), 1)
        self.assertEqual(rehydrated.messages[0].author, "masterway")
        self.assertEqual(rehydrated.extracted_links, ("https://k8s.io/x",))
        self.assertEqual(rehydrated.role_summaries, {"tech-lead": "approve"})
        self.assertEqual(rehydrated.captured_at, "2026-05-08T10:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
