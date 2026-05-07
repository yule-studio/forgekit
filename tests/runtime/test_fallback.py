"""runtime.fallback — A-M7 unit tests.

Pin the degrade / fallback contract:

  * single role failed → synthesis still runs over remaining
    takes; degrade banner names the missing role
  * all expected roles failed → deterministic fallback synthesis
    is generated, marked "fallback으로 생성됨", and the audit
    record carries ``human_approval_required=True``
  * fallback content does NOT auto-create an obsidian write — the
    M5b approval guard blocks because the request lacks an
    approval triple
  * audit record persists onto ``session.extra['fallback_audits']``
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.deliberation import TechLeadOpening
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.obsidian_writer_worker import (
    NOTE_KIND_KNOWLEDGE,
    SKIPPED_APPROVAL_REQUIRED,
    ObsidianWriteRequest,
    ObsidianWriterWorker,
)
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState
from yule_orchestrator.runtime.fallback import (
    FALLBACK_AUTHORITY_DEGRADED_SYNTHESIS,
    FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
    REASON_HUMAN_APPROVAL_REQUIRED,
    DegradeNotice,
    build_deterministic_fallback_synthesis,
    build_fallback_audit_record,
    persist_fallback_audit,
    render_degraded_synthesis_text,
    summarise_role_results,
)


def _session(session_id: str = "sess-fb-1") -> WorkflowSession:
    when = datetime.now(tz=timezone.utc)
    return WorkflowSession(
        session_id=session_id,
        prompt="결정 노트 정리 부탁",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=when,
        updated_at=when,
        role_sequence=("tech-lead", "backend-engineer"),
    )


# ---------------------------------------------------------------------------
# Degrade summary
# ---------------------------------------------------------------------------


class DegradeSummaryTests(unittest.TestCase):
    def test_classifies_completed_failed_missing(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead", "backend-engineer", "qa-engineer"),
            completed_roles=("tech-lead",),
            failed_roles=("backend-engineer",),
        )
        self.assertEqual(notice.completed_roles, ("tech-lead",))
        self.assertEqual(notice.failed_roles, ("backend-engineer",))
        # qa-engineer wasn't reported either way → missing.
        self.assertEqual(notice.missing_roles, ("qa-engineer",))
        self.assertTrue(notice.degraded)
        self.assertFalse(notice.all_failed)

    def test_all_failed_when_every_role_failed(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead", "backend-engineer"),
            failed_roles=("tech-lead", "backend-engineer"),
        )
        self.assertTrue(notice.all_failed)

    def test_no_degrade_when_every_role_completed(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead",),
            completed_roles=("tech-lead",),
        )
        self.assertFalse(notice.degraded)
        self.assertEqual(notice.to_text(), "")

    def test_degrade_banner_names_missing_and_failed(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead", "qa-engineer", "ai-engineer"),
            completed_roles=("tech-lead",),
            failed_roles=("qa-engineer",),
        )
        text = notice.to_text()
        self.assertIn("실패한 역할", text)
        self.assertIn("qa-engineer", text)
        self.assertIn("누락된 역할", text)
        self.assertIn("ai-engineer", text)


# ---------------------------------------------------------------------------
# Audit record
# ---------------------------------------------------------------------------


class AuditRecordTests(unittest.TestCase):
    def test_degraded_synthesis_does_not_require_approval(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead", "backend-engineer"),
            completed_roles=("tech-lead",),
            failed_roles=("backend-engineer",),
        )
        record = build_fallback_audit_record(
            session_id="sess-fb-1",
            notice=notice,
            authority=FALLBACK_AUTHORITY_DEGRADED_SYNTHESIS,
        )
        self.assertEqual(record.failed_roles, ("backend-engineer",))
        self.assertFalse(record.human_approval_required)
        self.assertEqual(
            record.fallback_authority,
            FALLBACK_AUTHORITY_DEGRADED_SYNTHESIS,
        )
        self.assertTrue(record.fallback_id.startswith("fb-"))
        # ISO-formatted created_at lands in the payload too.
        payload = record.to_payload()
        self.assertEqual(payload["session_id"], "sess-fb-1")
        self.assertEqual(payload["failed_roles"], ["backend-engineer"])
        self.assertIn("T", payload["created_at"])

    def test_all_role_fallback_requires_human_approval(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead", "qa-engineer"),
            failed_roles=("tech-lead", "qa-engineer"),
        )
        record = build_fallback_audit_record(
            session_id="sess-fb-2",
            notice=notice,
            authority=FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
        )
        self.assertTrue(record.human_approval_required)
        # Default reason explains why fallback fired without approval.
        self.assertIn("template", record.reason)

    def test_session_id_is_required(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead",),
            failed_roles=("tech-lead",),
        )
        with self.assertRaises(ValueError):
            build_fallback_audit_record(
                session_id="",
                notice=notice,
                authority=FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
            )


# ---------------------------------------------------------------------------
# persist_fallback_audit — session.extra writes
# ---------------------------------------------------------------------------


class PersistFallbackAuditTests(unittest.TestCase):
    def _make_session(self, extra: Dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(session_id="sess-fb-1", extra=extra)

    def test_writes_record_into_fallback_audits_bucket(self) -> None:
        session = self._make_session({})
        captured: List[Any] = []

        def loader(_sid: str):
            return session

        def updater(updated, *, now):
            captured.append(updated)

        notice = summarise_role_results(
            expected_roles=("tech-lead",),
            failed_roles=("tech-lead",),
        )
        record = build_fallback_audit_record(
            session_id="sess-fb-1",
            notice=notice,
            authority=FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
        )
        ok = persist_fallback_audit(
            record,
            session_loader=loader,
            session_updater=updater,
        )
        self.assertTrue(ok)
        # SimpleNamespace doesn't dataclasses.replace, so the fallback
        # path mutates `extra` in place — that's what the test exercises.
        self.assertEqual(len(session.extra["fallback_audits"]), 1)
        stamped = session.extra["fallback_audits"][0]
        self.assertEqual(stamped["fallback_id"], record.fallback_id)
        self.assertEqual(
            stamped["fallback_authority"],
            FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
        )

    def test_caps_audit_history_at_max_entries(self) -> None:
        from yule_orchestrator.runtime.fallback import (
            MAX_FALLBACK_AUDIT_ENTRIES,
        )

        # Pre-seed with > MAX entries so the next append must trim.
        primed = [
            {"fallback_id": f"fb-{i}", "session_id": "s"}
            for i in range(MAX_FALLBACK_AUDIT_ENTRIES + 5)
        ]
        session = self._make_session({"fallback_audits": list(primed)})

        def loader(_sid: str):
            return session

        def updater(updated, *, now):
            return None

        notice = summarise_role_results(
            expected_roles=("tech-lead",),
            failed_roles=("tech-lead",),
        )
        record = build_fallback_audit_record(
            session_id="sess-fb-1",
            notice=notice,
            authority=FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
        )
        persist_fallback_audit(
            record,
            session_loader=loader,
            session_updater=updater,
        )
        self.assertEqual(
            len(session.extra["fallback_audits"]),
            MAX_FALLBACK_AUDIT_ENTRIES,
        )
        # Newest entry is the one we just appended.
        self.assertEqual(
            session.extra["fallback_audits"][-1]["fallback_id"],
            record.fallback_id,
        )

    def test_returns_false_when_session_missing(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead",),
            failed_roles=("tech-lead",),
        )
        record = build_fallback_audit_record(
            session_id="sess-fb-1",
            notice=notice,
            authority=FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
        )
        ok = persist_fallback_audit(
            record,
            session_loader=lambda _sid: None,
            session_updater=lambda updated, *, now: None,
        )
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# Degraded synthesis renderer
# ---------------------------------------------------------------------------


class DegradedSynthesisTextTests(unittest.TestCase):
    def test_banner_prepended_when_degrade_present(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead", "qa-engineer"),
            completed_roles=("tech-lead",),
            failed_roles=("qa-engineer",),
        )
        text = render_degraded_synthesis_text(
            base_text="**[tech-lead 종합]** ...",
            notice=notice,
        )
        # Banner first, base content preserved.
        self.assertTrue(text.startswith("[degrade]"))
        self.assertIn("qa-engineer", text)
        self.assertIn("tech-lead 종합", text)

    def test_no_banner_when_nothing_degraded(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead",),
            completed_roles=("tech-lead",),
        )
        base = "**[tech-lead 종합]** 합의안: ..."
        self.assertEqual(
            render_degraded_synthesis_text(base_text=base, notice=notice),
            base,
        )


# ---------------------------------------------------------------------------
# All-role deterministic fallback
# ---------------------------------------------------------------------------


class DeterministicFallbackSynthesisTests(unittest.TestCase):
    def test_synthesis_marked_fallback_and_requires_approval(self) -> None:
        synth, rendered = build_deterministic_fallback_synthesis(
            session=_session(),
            expected_roles=("tech-lead", "backend-engineer"),
        )
        # Plainly labelled so the operator can never mistake fallback
        # output for a real consensus.
        self.assertIn("fallback으로 생성됨", rendered)
        # The TechLeadSynthesis dataclass forces approval_required so
        # render_synthesis emits "승인 필요: yes — ...".
        self.assertTrue(synth.approval_required)
        self.assertEqual(
            synth.approval_reason, REASON_HUMAN_APPROVAL_REQUIRED
        )
        self.assertIn("승인 필요: yes", rendered)

    def test_handles_empty_expected_roles_without_error(self) -> None:
        # Defensive: synthesis still produces a record, just with no
        # role takes feeding into it.
        synth, rendered = build_deterministic_fallback_synthesis(
            session=_session(),
            expected_roles=(),
        )
        self.assertIn("fallback으로 생성됨", rendered)
        self.assertTrue(synth.approval_required)


# ---------------------------------------------------------------------------
# Approval guard — fallback does NOT auto-save final knowledge
# ---------------------------------------------------------------------------


class FallbackDoesNotAutoSaveKnowledgeTests(unittest.TestCase):
    def test_obsidian_writer_blocks_fallback_without_approval_triple(
        self,
    ) -> None:
        # Simulate the production path: fallback content gets queued
        # as an obsidian_write request *without* a populated approval
        # triple (because no human approved the fallback). The
        # ObsidianWriterWorker's M5b approval guard must refuse to
        # auto-save it as final knowledge.
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "queue.sqlite3"
            queue = JobQueue(db_path=db)
            heartbeats = HeartbeatStore(db_path=db)
            written: List[Any] = []

            def render_fn(_request):
                return SimpleNamespace(title="x")

            def write_fn(note, vault, request):
                written.append(request)
                return None

            worker = ObsidianWriterWorker(
                queue=queue,
                heartbeats=heartbeats,
                render_fn=render_fn,
                write_fn=write_fn,
                vault_root_resolver=lambda _r: Path(tmp),
            )
            request = ObsidianWriteRequest(
                session_id="sess-fb-1",
                note_kind=NOTE_KIND_KNOWLEDGE,
                title="fallback synthesis",
                approval_id=None,
                approved_by=None,
                approved_at=None,
            )
            import asyncio

            outcome = asyncio.new_event_loop().run_until_complete(
                worker.run_one(request)
            )
            self.assertEqual(outcome.skipped_reason, SKIPPED_APPROVAL_REQUIRED)
            # Crucially, write_fn was NOT called — fallback never
            # reached the vault.
            self.assertEqual(written, [])


if __name__ == "__main__":
    unittest.main()
