"""필수 테스트 7종 — troubleshooting mandatory capture 정책.

사용자가 명시한 필수 테스트:
1. live smoke failure → troubleshooting record 생성
2. repeated same failure → mistake ledger promotion
3. fallback success still creates troubleshooting record
4. Claude Code/Codex-originated correction path also records troubleshooting
5. preflight sees prior troubleshooting/mistake and surfaces warning/block
6. operator can inspect structured troubleshooting output
7. regression: normal successful path does not spam noise
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.mistake_ledger import (
    record_mistake as real_record_mistake,
)
from yule_orchestrator.agents.lifecycle.preflight_judgement import (
    PREFLIGHT_BLOCK,
    PREFLIGHT_PASS,
    PREFLIGHT_WARNING,
)
from yule_orchestrator.agents.lifecycle.troubleshooting_enforcer import (
    EnforcementJournal,
    mandatory_capture,
    record_claude_correction,
    record_codex_correction,
    record_silent_correction,
)
from yule_orchestrator.agents.lifecycle.troubleshooting_ledger import (
    SURFACE_MISTAKE_LEDGER,
    SURFACE_OBSIDIAN,
    SURFACE_RECORD_LEDGER,
    SURFACE_RESEARCH_THREAD,
    TroubleshootingLedger,
    default_ledger_path,
    derive_problem_signature,
    stamp_troubleshooting_audit,
)
from yule_orchestrator.agents.lifecycle.troubleshooting_preflight import (
    evaluate_combined_preflight,
    lookup_relevant_records,
)
from yule_orchestrator.agents.lifecycle.troubleshooting_record import (
    CaptureReason,
    DETECTED_BY_CLAUDE_CODE,
    DETECTED_BY_RUNTIME_GATEWAY,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    TroubleshootingRecord,
    TroubleshootingStatus,
    is_capture_reason_known,
    render_troubleshooting_note,
    required_sections,
)


_NOW = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. live smoke failure → record 생성
# ---------------------------------------------------------------------------


class LiveSmokeFailureCapturesRecord(unittest.TestCase):
    def test_capture_creates_record_on_minimum_two_surfaces(self) -> None:
        obsidian_writes: list = []
        research_posts: list = []

        ledger = TroubleshootingLedger(
            obsidian_writer=lambda *, record, note_markdown: (
                obsidian_writes.append((record.problem_signature, note_markdown))
                or "obsidian-1"
            ),
            research_thread_poster=lambda *, record: (
                research_posts.append(record.problem_signature) or "thread-1"
            ),
        )

        outcome = ledger.capture(
            title="live smoke failure on approval reply router",
            capture_reason=CaptureReason.LIVE_SMOKE_FAILURE,
            detected_by=DETECTED_BY_RUNTIME_GATEWAY,
            owner_role="backend-engineer",
            scope="approval_reply_router",
            symptom="approval card posted but reply not matched",
            severity=SEVERITY_HIGH,
            now=_NOW,
        )

        # 최소 2 surface 만족: record_ledger + obsidian + research_thread
        self.assertTrue(outcome.meets_minimum_surfaces(minimum=2))
        self.assertIn(SURFACE_RECORD_LEDGER, outcome.surfaces_written)
        self.assertIn(SURFACE_OBSIDIAN, outcome.surfaces_written)
        self.assertIn(SURFACE_RESEARCH_THREAD, outcome.surfaces_written)
        # record 자체가 ledger 에 들어감
        records = ledger.all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].capture_reason, CaptureReason.LIVE_SMOKE_FAILURE.value)
        # obsidian markdown 이 8 섹션 모두 포함
        _, md = obsidian_writes[0]
        for section in required_sections():
            self.assertIn(f"## {section}", md)


# ---------------------------------------------------------------------------
# 2. repeated same failure → mistake ledger promotion
# ---------------------------------------------------------------------------


class RepeatedFailurePromotesMistakeLedger(unittest.TestCase):
    def test_second_occurrence_promotes(self) -> None:
        promotion_calls: list = []

        def fake_record_mistake(extra, **kwargs):
            promotion_calls.append(kwargs)
            return extra or {}, None

        ledger = TroubleshootingLedger(
            mistake_record_fn=fake_record_mistake,
            promotion_threshold=2,
        )
        sig = derive_problem_signature(
            capture_reason=CaptureReason.WRONG_CLASSIFICATION.value,
            scope="dispatcher_classify",
            owner_role="backend-engineer",
        )
        first = ledger.capture(
            title="qa-test misclassification",
            capture_reason=CaptureReason.WRONG_CLASSIFICATION,
            detected_by=DETECTED_BY_RUNTIME_GATEWAY,
            owner_role="backend-engineer",
            scope="dispatcher_classify",
            symptom="full-stack MVP intake classified as qa-test",
            severity=SEVERITY_HIGH,
            problem_signature=sig,
            now=_NOW,
        )
        # 1회만 발생 → promotion 아직 없음
        self.assertFalse(first.mistake_promoted)
        self.assertEqual(len(promotion_calls), 0)

        second = ledger.capture(
            title="qa-test misclassification (repeat)",
            capture_reason=CaptureReason.WRONG_CLASSIFICATION,
            detected_by=DETECTED_BY_RUNTIME_GATEWAY,
            owner_role="backend-engineer",
            scope="dispatcher_classify",
            symptom="same pattern hit again on a new session",
            severity=SEVERITY_HIGH,
            problem_signature=sig,
            now=_NOW,
        )
        self.assertTrue(second.mistake_promoted)
        self.assertEqual(second.occurrence_count, 2)
        self.assertIn(SURFACE_MISTAKE_LEDGER, second.surfaces_written)
        self.assertEqual(len(promotion_calls), 1)
        # mistake_key 가 problem_signature 그대로
        self.assertEqual(promotion_calls[0]["mistake_key"], sig[:64])


# ---------------------------------------------------------------------------
# 3. fallback success still creates troubleshooting record
# ---------------------------------------------------------------------------


class FallbackSuccessStillRecords(unittest.TestCase):
    def test_silent_correction_records_with_mitigated_status(self) -> None:
        ledger = TroubleshootingLedger()
        outcome = record_silent_correction(
            ledger,
            capture_reason=CaptureReason.FALLBACK_SUCCESS_AFTER_FAIL,
            title="approval_post fallback succeeded after fail",
            symptom="첫 시도가 SQLite lock 으로 실패, retry 1회 후 성공",
            detected_by=DETECTED_BY_RUNTIME_GATEWAY,
            scope="approval_post_enqueue",
            owner_role="backend-engineer",
            attempted_fix="첫 호출은 BUSY 로 실패",
            final_fix="0.5s 후 자동 재시도로 성공",
            prevention_rule="approval_worker 에 retry budget 2회 명시",
        )
        # silent correction 도 ledger 에 들어감
        self.assertIn(SURFACE_RECORD_LEDGER, outcome.surfaces_written)
        # status=mitigated (fix 가 들어갔지만 enforcement 는 아직 아님)
        self.assertEqual(outcome.record.status, TroubleshootingStatus.MITIGATED.value)
        # prevention rule 이 들어가있으면 followup_required=False
        self.assertFalse(outcome.record.followup_required)


# ---------------------------------------------------------------------------
# 4. Claude Code / Codex-originated correction also records
# ---------------------------------------------------------------------------


class ClaudeCodeCorrectionCaptured(unittest.TestCase):
    def test_claude_correction_lands_in_same_ledger(self) -> None:
        ledger = TroubleshootingLedger()
        outcome = record_claude_correction(
            ledger,
            title="첫 fix 가 회귀 일부만 잡음",
            symptom="reply_router.py 만 고치고 channel router 도 같이 고쳐야 했음",
            attempted_fix="reply_router.py 수정",
            final_fix="channel router phrase_detect 도 같이 수정",
            prevention_rule="slash path 와 channel path 변경은 항상 paired diff",
            related_files=("src/.../reply_router.py", "src/.../engineering_channel_router/main.py"),
            related_prs=("https://github.com/yule-studio/yule-studio-agent/pull/177",),
        )
        self.assertEqual(outcome.record.detected_by, DETECTED_BY_CLAUDE_CODE)
        # runtime record 와 같은 ledger 에 들어감
        all_records = ledger.all()
        self.assertEqual(len(all_records), 1)
        self.assertEqual(all_records[0].owner_role, "claude-code")
        # PR / file 정보가 보존됨
        self.assertEqual(len(all_records[0].related_files), 2)
        self.assertEqual(len(all_records[0].related_prs), 1)

    def test_codex_correction_uses_codex_owner(self) -> None:
        ledger = TroubleshootingLedger()
        outcome = record_codex_correction(
            ledger,
            title="codex가 같은 wiring 누락 반복",
            symptom="...",
            attempted_fix="...",
            final_fix="...",
        )
        self.assertEqual(outcome.record.owner_role, "codex")


# ---------------------------------------------------------------------------
# 5. preflight sees prior troubleshooting / mistake and surfaces
# ---------------------------------------------------------------------------


class PreflightSurfacesPriorRecord(unittest.TestCase):
    def test_preflight_finds_prior_record_by_file_path(self) -> None:
        ledger = TroubleshootingLedger()
        ledger.capture(
            title="prior approval reply mismatch",
            capture_reason=CaptureReason.APPROVAL_REPLY_MISMATCH,
            detected_by=DETECTED_BY_RUNTIME_GATEWAY,
            owner_role="backend-engineer",
            scope="approval_reply",
            symptom="prior occurrence",
            severity=SEVERITY_HIGH,
            related_files=(
                "src/yule_orchestrator/discord/approval/reply_router.py",
            ),
            now=_NOW,
        )
        # 같은 파일을 건드리는 신규 작업 — preflight 가 prior 를 surface 해야 함
        relevant = lookup_relevant_records(
            ledger,
            file_paths=("src/yule_orchestrator/discord/approval/reply_router.py",),
        )
        self.assertEqual(len(relevant), 1)

    def test_preflight_block_when_critical_repeated(self) -> None:
        # 3 occurrences of critical severity → verdict escalates to block
        ledger = TroubleshootingLedger()
        sig = "critical-bug"
        for _ in range(3):
            ledger.capture(
                title="repeated critical bug",
                capture_reason=CaptureReason.LIVE_SMOKE_FAILURE,
                detected_by=DETECTED_BY_RUNTIME_GATEWAY,
                owner_role="backend-engineer",
                scope="approval",
                symptom="repeated",
                severity="critical",
                problem_signature=sig,
                now=_NOW,
            )
        briefing = evaluate_combined_preflight(
            source=SimpleNamespace(extra={}),
            role_id="backend-engineer",
            action="runtime_code_change",
            ledger=ledger,
            file_paths=(),
            problem_signature=sig,
        )
        self.assertEqual(briefing.verdict, PREFLIGHT_BLOCK)
        self.assertTrue(briefing.is_block())
        # markdown block 이 채워졌고 "prior troubleshooting records" 섹션 포함
        self.assertIn("prior troubleshooting records", briefing.markdown_block)


# ---------------------------------------------------------------------------
# 6. operator can inspect structured output
# ---------------------------------------------------------------------------


class StructuredOutputRoundTrip(unittest.TestCase):
    def test_record_round_trips_through_json_with_all_20_fields(self) -> None:
        record = TroubleshootingRecord(
            record_id="ts-1",
            title="t",
            problem_signature="sig.x",
            capture_reason=CaptureReason.LIVE_SMOKE_FAILURE.value,
            detected_at=_NOW.isoformat(),
            recorded_at=_NOW.isoformat(),
            detected_by=DETECTED_BY_RUNTIME_GATEWAY,
            owner_role="backend-engineer",
            scope="approval",
            severity=SEVERITY_HIGH,
            status=TroubleshootingStatus.OPEN.value,
            symptom="s",
            exact_evidence="e",
            reproduction_steps=("step 1", "step 2"),
            root_cause_hypothesis="h",
            confirmed_root_cause="r",
            attempted_fix="a",
            final_fix="f",
            prevention_rule="p",
            related_session_ids=("sess1",),
            related_job_ids=("job1",),
            related_prs=("pr1",),
            related_files=("f1",),
            followup_required=True,
            tags=("self-improvement",),
            extra={"k": "v"},
            occurrence_count=3,
        )
        payload = record.to_payload()
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        restored = TroubleshootingRecord.from_payload(decoded)
        self.assertEqual(restored, record)

    def test_capture_reason_enum_valid_only(self) -> None:
        self.assertTrue(is_capture_reason_known(
            CaptureReason.LIVE_SMOKE_FAILURE.value
        ))
        self.assertFalse(is_capture_reason_known("totally-not-a-reason"))

    def test_required_sections_complete(self) -> None:
        sections = required_sections()
        # 사용자 § E 가 명시한 8 섹션
        self.assertEqual(len(sections), 8)
        self.assertIn("증상", sections)
        self.assertIn("남은 리스크", sections)

    def test_persistence_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            ledger_a = TroubleshootingLedger(ledger_path=path)
            ledger_a.capture(
                title="persisted",
                capture_reason=CaptureReason.WRONG_CLASSIFICATION,
                detected_by=DETECTED_BY_RUNTIME_GATEWAY,
                owner_role="backend-engineer",
                scope="x",
                symptom="...",
                severity=SEVERITY_MEDIUM,
                now=_NOW,
            )
            ledger_b = TroubleshootingLedger(ledger_path=path)
            self.assertEqual(len(ledger_b.all()), 1)


# ---------------------------------------------------------------------------
# 7. normal successful path does not spam noise
# ---------------------------------------------------------------------------


class NormalPathNoNoise(unittest.TestCase):
    def test_with_block_skip_creates_no_violation(self) -> None:
        ledger = TroubleshootingLedger()
        journal = EnforcementJournal()
        with mandatory_capture(
            ledger,
            journal,
            capture_reason=CaptureReason.LIVE_SMOKE_FAILURE,
            detected_by=DETECTED_BY_RUNTIME_GATEWAY,
            scope="approval",
        ) as guard:
            # normal happy-path — failure 가 안 일어남
            guard.skip(reason="no failure observed; happy path")
        # ledger 에 record 가 추가되지 않음
        self.assertEqual(len(ledger.all()), 0)
        # violation 도 안 잡힘 — explicit skip 이므로
        self.assertEqual(len(journal.recent()), 0)

    def test_block_without_record_or_skip_raises_violation(self) -> None:
        """반대 케이스: 실제 trigger 인데 record 도 skip 도 안 한 경우 violation."""

        ledger = TroubleshootingLedger()
        journal = EnforcementJournal()
        with mandatory_capture(
            ledger,
            journal,
            capture_reason=CaptureReason.LIVE_SMOKE_FAILURE,
            detected_by=DETECTED_BY_RUNTIME_GATEWAY,
            scope="approval",
        ) as guard:
            # 사용자 코드가 record 호출을 잊음
            pass
        # journal 에 violation 1 건 누적 — operator surface 가 추적 가능
        self.assertEqual(len(journal.recent()), 1)
        self.assertEqual(
            journal.recent()[0].capture_reason,
            CaptureReason.LIVE_SMOKE_FAILURE.value,
        )


# ---------------------------------------------------------------------------
# Bonus: session.extra audit stamp
# ---------------------------------------------------------------------------


class SessionExtraStampTests(unittest.TestCase):
    def test_stamp_appends_both_audit_rows(self) -> None:
        from yule_orchestrator.agents.lifecycle.agent_ops_log import AgentOpsEntry

        record = TroubleshootingRecord(
            record_id="ts-1",
            title="t",
            problem_signature="sig.x",
            capture_reason=CaptureReason.LIVE_SMOKE_FAILURE.value,
            detected_at=_NOW.isoformat(),
            recorded_at=_NOW.isoformat(),
            detected_by="runtime/gateway",
            owner_role="backend",
            scope="approval",
            severity=SEVERITY_HIGH,
            status=TroubleshootingStatus.OPEN.value,
            symptom="s",
        )
        audit_entry = AgentOpsEntry(
            entry_id="x",
            session_id="",
            action="live_smoke_failure",
            autonomy_level="L1_AUTO_RECORD_REQUIRED",
            summary="t",
            reasoning="",
            outcome="captured",
            recorded_at=_NOW.isoformat(),
        )
        new_extra = stamp_troubleshooting_audit(
            {}, record=record, audit_entry=audit_entry
        )
        # troubleshooting_audit + agent_ops_audit 모두 들어가있어야 함
        self.assertIn("troubleshooting_audit", new_extra)
        self.assertIn("agent_ops_audit", new_extra)
        self.assertEqual(len(new_extra["troubleshooting_audit"]), 1)


if __name__ == "__main__":
    unittest.main()
