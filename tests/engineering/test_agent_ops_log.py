"""agent_ops_log — A-M10a unit tests.

Pin the audit-row dataclass + session.extra round-trip helpers:

  * build entry from autonomy decision,
  * to_payload / from_payload preserves all fields,
  * append_agent_ops_audit appends without overwriting ledger /
    snapshot keys (keeps M7.6 invariants intact),
  * cap at max_entries (head dropped on overflow),
  * read_agent_ops_audit reads from session-shaped or extra-shaped
    inputs,
  * markdown rendering surfaces autonomy level + reason + outcome.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.agent_ops_log import (
    AgentOpsEntry,
    SESSION_EXTRA_KEY,
    append_agent_ops_audit,
    build_agent_ops_entry,
    read_agent_ops_audit,
    render_agent_ops_entry_markdown,
    render_agent_ops_log_markdown,
)
from yule_orchestrator.agents.lifecycle.autonomy_policy import (
    ACTION_FORUM_HANDOFF_DECISION,
    ACTION_RESEARCH_LOG_SAVE,
    AutonomyContext,
    decide_autonomy,
)


class BuildEntryFromDecisionTests(unittest.TestCase):
    def test_build_carries_action_level_summary_outcome(self) -> None:
        decision = decide_autonomy(
            AutonomyContext(
                action=ACTION_RESEARCH_LOG_SAVE,
                session_id="sess-1",
                topic_key="devops-roadmap",
                summary="DevOps 로드맵 research-log 자동 저장",
            )
        )
        entry = build_agent_ops_entry(
            decision=decision,
            outcome="research_log_saved",
            references=("https://k8s.io/docs/",),
        )
        self.assertEqual(entry.action, ACTION_RESEARCH_LOG_SAVE)
        self.assertEqual(entry.autonomy_level, "L1_AUTO_RECORD_REQUIRED")
        self.assertEqual(entry.session_id, "sess-1")
        self.assertEqual(entry.topic_key, "devops-roadmap")
        self.assertIn("DevOps", entry.summary)
        self.assertEqual(entry.outcome, "research_log_saved")
        self.assertEqual(entry.references, ("https://k8s.io/docs/",))
        self.assertTrue(entry.recorded_at)
        self.assertEqual(entry.actor, "engineering-agent")


class PayloadRoundTripTests(unittest.TestCase):
    def test_round_trip_preserves_all_fields(self) -> None:
        original = AgentOpsEntry(
            entry_id="evt-1",
            session_id="sess-1",
            action=ACTION_FORUM_HANDOFF_DECISION,
            autonomy_level="L1_AUTO_RECORD_REQUIRED",
            summary="중복 카드 차단",
            reasoning="L1 — forum handoff 결정 audit",
            outcome="skipped:topic_already_saved",
            references=("vault/10-projects/x.md",),
            topic_key="t-key",
            job_id="job-99",
            decision_id="dec-1",
            recorded_at="2026-05-08T10:30:00+00:00",
        )
        payload = original.to_payload()
        rehydrated = AgentOpsEntry.from_payload(payload)
        self.assertIsNotNone(rehydrated)
        assert rehydrated is not None
        self.assertEqual(rehydrated, original)

    def test_from_payload_returns_none_on_missing_id(self) -> None:
        self.assertIsNone(AgentOpsEntry.from_payload({}))
        self.assertIsNone(AgentOpsEntry.from_payload(None))
        self.assertIsNone(
            AgentOpsEntry.from_payload({"entry_id": ""})
        )


class AppendAndCapTests(unittest.TestCase):
    def _make(self, idx: int) -> AgentOpsEntry:
        return AgentOpsEntry(
            entry_id=f"evt-{idx}",
            session_id="sess",
            action=ACTION_FORUM_HANDOFF_DECISION,
            autonomy_level="L1_AUTO_RECORD_REQUIRED",
            summary=f"summary-{idx}",
            reasoning="L1 audit",
            outcome="ok",
            recorded_at="2026-05-08T10:00:00+00:00",
        )

    def test_append_preserves_other_keys(self) -> None:
        prev = {
            "research_topic": {"topic_key": "x"},
            "research_pack": {"title": "y"},
        }
        out = append_agent_ops_audit(prev, self._make(1))
        self.assertEqual(out["research_topic"], {"topic_key": "x"})
        self.assertEqual(out["research_pack"], {"title": "y"})
        self.assertEqual(len(out[SESSION_EXTRA_KEY]), 1)

    def test_append_extends_existing_list(self) -> None:
        seed = {SESSION_EXTRA_KEY: [self._make(0).to_payload()]}
        out = append_agent_ops_audit(seed, self._make(1))
        self.assertEqual(len(out[SESSION_EXTRA_KEY]), 2)
        self.assertEqual(out[SESSION_EXTRA_KEY][0]["entry_id"], "evt-0")
        self.assertEqual(out[SESSION_EXTRA_KEY][1]["entry_id"], "evt-1")

    def test_cap_drops_head_on_overflow(self) -> None:
        extra: dict = {}
        for i in range(7):
            extra = append_agent_ops_audit(
                extra, self._make(i), max_entries=5
            )
        ids = [item["entry_id"] for item in extra[SESSION_EXTRA_KEY]]
        # Oldest two (evt-0, evt-1) dropped; tail kept.
        self.assertEqual(ids, ["evt-2", "evt-3", "evt-4", "evt-5", "evt-6"])

    def test_does_not_mutate_input(self) -> None:
        before = {"foo": "bar"}
        after = append_agent_ops_audit(before, self._make(1))
        self.assertEqual(before, {"foo": "bar"})
        self.assertIn(SESSION_EXTRA_KEY, after)


class ReadAuditTests(unittest.TestCase):
    def test_reads_from_session_shaped_object(self) -> None:
        entry = AgentOpsEntry(
            entry_id="evt-1",
            session_id="s",
            action="x",
            autonomy_level="L1_AUTO_RECORD_REQUIRED",
            summary="",
            reasoning="",
            outcome="",
            recorded_at="2026-05-08T10:00:00+00:00",
        )
        session = SimpleNamespace(
            extra={SESSION_EXTRA_KEY: [entry.to_payload()]}
        )
        rows = read_agent_ops_audit(session)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].entry_id, "evt-1")

    def test_reads_from_raw_extra_mapping(self) -> None:
        rows = read_agent_ops_audit(
            {SESSION_EXTRA_KEY: [{"entry_id": "x", "session_id": "s"}]}
        )
        self.assertEqual(len(rows), 1)

    def test_returns_empty_when_no_extra(self) -> None:
        self.assertEqual(read_agent_ops_audit(None), ())
        self.assertEqual(read_agent_ops_audit(SimpleNamespace()), ())
        self.assertEqual(read_agent_ops_audit({}), ())


class RenderTests(unittest.TestCase):
    def _entry(self) -> AgentOpsEntry:
        return AgentOpsEntry(
            entry_id="evt-1",
            session_id="sess",
            action=ACTION_RESEARCH_LOG_SAVE,
            autonomy_level="L1_AUTO_RECORD_REQUIRED",
            summary="DevOps 로드맵 research-log 자동 저장",
            reasoning="L1 — 사용자 명시 오더 기반 리서치",
            outcome="research_log_saved",
            references=("https://k8s.io/docs/",),
            topic_key="devops-roadmap",
            job_id="job-9",
            decision_id="dec-1",
            recorded_at="2026-05-08T10:30:00+00:00",
        )

    def test_entry_markdown_includes_level_action_reason_outcome(self) -> None:
        md = render_agent_ops_entry_markdown(self._entry())
        self.assertIn("L1_AUTO_RECORD_REQUIRED", md)
        self.assertIn(ACTION_RESEARCH_LOG_SAVE, md)
        self.assertIn("L1 — 사용자 명시 오더", md)
        self.assertIn("research_log_saved", md)
        self.assertIn("https://k8s.io/docs/", md)
        self.assertIn("devops-roadmap", md)

    def test_log_markdown_handles_empty_list(self) -> None:
        md = render_agent_ops_log_markdown([])
        self.assertIn("기록 없음", md)

    def test_log_markdown_renders_each_entry(self) -> None:
        md = render_agent_ops_log_markdown([self._entry(), self._entry()])
        # Two horizontal rules between blocks + one header.
        self.assertEqual(md.count("---"), 2)
        self.assertEqual(md.count("L1_AUTO_RECORD_REQUIRED"), 2)


if __name__ == "__main__":
    unittest.main()
