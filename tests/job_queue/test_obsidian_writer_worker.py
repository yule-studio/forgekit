"""ObsidianWriterWorker — A-M5b unit tests.

Pin every M5b requirement:

  * enqueue creates an ``obsidian_write`` row scoped to
    ``(session, note_kind, source_thread_id, title)``
  * duplicate enqueues are silently dropped while a job is in flight
  * run_one drives ``queued → assigned → in_progress → saved`` on
    success and calls the bound ``write_fn``
  * heartbeat under ``eng-obsidian-writer`` lands per call
  * ``session.extra['obsidian_writes'][<note_kind>]`` records what
    was written so the status diagnostic / Phase 5 surface can
    describe vault state without re-reading the disk
  * approval guard: ``note_kind="knowledge"`` or ``overwrite=True``
    without ``approval_id`` / ``approved_by`` / ``approved_at``
    lands ``failed_retryable`` with a constant error
  * write_fn exceptions land ``failed_retryable``
  * vault root unavailable lands ``failed_retryable`` with a
    distinct error constant (operator can correct env then requeue)
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.obsidian_writer_worker import (
    JOB_TYPE_OBSIDIAN_WRITE,
    NOTE_KIND_DECISION,
    NOTE_KIND_KNOWLEDGE,
    NOTE_KIND_RESEARCH,
    ObsidianWriteRequest,
    ObsidianWriterWorker,
    SERVICE_ID_OBSIDIAN_WRITER,
    SKIPPED_APPROVAL_REQUIRED,
    SKIPPED_DUPLICATE,
    SKIPPED_VAULT_UNAVAILABLE,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@dataclass
class _FakeWriteResult:
    target_path: Path
    written: bool = True
    dry_run: bool = False
    suffix_applied: bool = False


def _request(**overrides) -> ObsidianWriteRequest:
    base = dict(
        session_id="sess-m5b-1",
        note_kind=NOTE_KIND_RESEARCH,
        title="k8s 운영 자료 정리",
        source_thread_id=4242,
        source_thread_url="https://discord.example/thread/4242",
        project="yule-studio-agent",
    )
    base.update(overrides)
    return ObsidianWriteRequest(**base)


class _Fixture(unittest.TestCase):
    """Per-test SQLite isolation + a default vault dir + capturing
    render/write stubs so tests don't touch the operator's vault."""

    def setUp(self) -> None:  # noqa: D401 - test setup
        try:
            from tests._helpers import isolate_cache_for_test
        except ImportError:  # pragma: no cover
            from _helpers import isolate_cache_for_test  # type: ignore

        isolate_cache_for_test(self)

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault_root = Path(self._tmp.name)

        self.queue = JobQueue()
        self.heartbeats = HeartbeatStore()
        self.rendered: List[ObsidianWriteRequest] = []
        self.writes: List[tuple[Any, Path, ObsidianWriteRequest]] = []

        def render_fn(request: ObsidianWriteRequest) -> dict:
            self.rendered.append(request)
            return {"title": request.title, "rendered": True}

        def write_fn(note: Any, vault: Path, request: ObsidianWriteRequest):
            self.writes.append((note, vault, request))
            target = vault / f"{request.note_kind}/{request.title}.md"
            return _FakeWriteResult(target_path=target, written=not request.dry_run, dry_run=request.dry_run)

        self.render_fn = render_fn
        self.write_fn = write_fn
        self.worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=render_fn,
            write_fn=write_fn,
            vault_root_resolver=lambda _request: self.vault_root,
        )


# ---------------------------------------------------------------------------
# Enqueue + dedup
# ---------------------------------------------------------------------------


class EnqueueDedupTests(_Fixture):
    def test_enqueue_creates_obsidian_write_row(self) -> None:
        job, created = self.worker.enqueue(_request())
        self.assertTrue(created)
        self.assertEqual(job.job_type, JOB_TYPE_OBSIDIAN_WRITE)
        self.assertEqual(job.state, JobState.QUEUED)
        # Payload round-trip through SQLite TEXT.
        request_back = ObsidianWriteRequest.from_payload(job.payload)
        self.assertEqual(request_back.note_kind, NOTE_KIND_RESEARCH)
        self.assertEqual(request_back.source_thread_id, 4242)

    def test_dedup_keys_on_session_kind_thread_title(self) -> None:
        first, _ = self.worker.enqueue(_request())
        second, created = self.worker.enqueue(_request())
        self.assertEqual(first.job_id, second.job_id)
        self.assertFalse(created)

    def test_different_kind_does_not_dedup(self) -> None:
        a, _ = self.worker.enqueue(_request(note_kind=NOTE_KIND_RESEARCH))
        b, created = self.worker.enqueue(_request(note_kind=NOTE_KIND_DECISION))
        self.assertNotEqual(a.job_id, b.job_id)
        self.assertTrue(created)

    def test_terminal_jobs_do_not_block_new_enqueue(self) -> None:
        first, _ = self.worker.enqueue(_request())
        self.queue.transition(first.job_id, JobState.ASSIGNED)
        self.queue.transition(first.job_id, JobState.IN_PROGRESS)
        self.queue.transition(first.job_id, JobState.SAVED)
        second, created = self.worker.enqueue(_request())
        self.assertTrue(created)
        self.assertNotEqual(first.job_id, second.job_id)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class RunOneSuccessTests(_Fixture):
    def test_research_write_walks_state_machine_and_calls_write_fn(self) -> None:
        outcome = _run(self.worker.run_one(_request()))
        self.assertIsNone(outcome.skipped_reason)
        assert outcome.job is not None
        self.assertEqual(outcome.job.state, JobState.SAVED)
        # render_fn + write_fn each called exactly once with the
        # typed request — proves the worker is the seam M5a-2 / M6
        # will plug the real Obsidian export chain into.
        self.assertEqual(len(self.rendered), 1)
        self.assertEqual(len(self.writes), 1)
        # write_fn received the configured vault root.
        _, vault_seen, request_seen = self.writes[0]
        self.assertEqual(vault_seen, self.vault_root)
        self.assertEqual(request_seen.note_kind, NOTE_KIND_RESEARCH)
        # Result summary stamped on the queue row.
        self.assertEqual(outcome.job.result.get("note_kind"), NOTE_KIND_RESEARCH)
        self.assertEqual(outcome.job.result.get("written"), True)
        self.assertEqual(outcome.job.result.get("dry_run"), False)
        self.assertEqual(
            outcome.job.result.get("vault_root"), str(self.vault_root)
        )

    def test_dry_run_writes_nothing_but_still_saves_row(self) -> None:
        outcome = _run(self.worker.run_one(_request(dry_run=True)))
        assert outcome.job is not None
        self.assertEqual(outcome.job.state, JobState.SAVED)
        self.assertEqual(outcome.job.result.get("dry_run"), True)
        self.assertEqual(outcome.job.result.get("written"), False)

    def test_run_one_records_heartbeat(self) -> None:
        _run(self.worker.run_one(_request()))
        beat = self.heartbeats.get(SERVICE_ID_OBSIDIAN_WRITER)
        self.assertIsNotNone(beat)

    def test_session_extra_obsidian_writes_recorded(self) -> None:
        # Seed a real session — _stash_write_result_on_session loads
        # via load_session, so the row must exist in the cache.
        from yule_engineering.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
            load_session,
            save_session,
        )

        save_session(
            WorkflowSession(
                session_id="sess-m5b-1",
                prompt="harness",
                task_type="research",
                state=WorkflowState.IN_PROGRESS,
                created_at=datetime(2026, 5, 7),
                updated_at=datetime(2026, 5, 7),
            )
        )
        _run(self.worker.run_one(_request()))
        reloaded = load_session("sess-m5b-1")
        self.assertIsNotNone(reloaded)
        assert reloaded is not None
        bucket = (reloaded.extra or {}).get("obsidian_writes", {})
        self.assertIn(NOTE_KIND_RESEARCH, bucket)
        # Record carries the post-write target path so the status
        # diagnostic can show "research note saved at <path>" without
        # re-reading the vault.
        record = bucket[NOTE_KIND_RESEARCH]
        self.assertEqual(record.get("note_kind"), NOTE_KIND_RESEARCH)
        self.assertEqual(record.get("title"), "k8s 운영 자료 정리")
        self.assertTrue(str(record.get("target_path", "")).endswith(".md"))

    def test_duplicate_run_one_skips_render_and_write(self) -> None:
        first, _ = self.worker.enqueue(_request())
        self.queue.transition(first.job_id, JobState.ASSIGNED)
        self.queue.transition(first.job_id, JobState.IN_PROGRESS)

        outcome = _run(self.worker.run_one(_request()))
        self.assertEqual(outcome.skipped_reason, SKIPPED_DUPLICATE)
        # Critical regression target: render/write must NOT run for
        # duplicates — would otherwise overwrite an in-flight save.
        self.assertEqual(self.rendered, [])
        self.assertEqual(self.writes, [])


# ---------------------------------------------------------------------------
# Approval guard — knowledge / overwrite require approval triple
# ---------------------------------------------------------------------------


class ApprovalGuardTests(_Fixture):
    def test_knowledge_without_approval_lands_failed_retryable(self) -> None:
        outcome = _run(
            self.worker.run_one(_request(note_kind=NOTE_KIND_KNOWLEDGE))
        )
        self.assertEqual(outcome.skipped_reason, SKIPPED_APPROVAL_REQUIRED)
        rows = self.queue.list_for_session(
            "sess-m5b-1", states=[JobState.FAILED_RETRYABLE]
        )
        self.assertEqual(len(rows), 1)
        # Constant error string so a future supervisor / live-regression
        # harness can match exactly without grep-fuzz.
        self.assertEqual(
            rows[0].result.get("error"), SKIPPED_APPROVAL_REQUIRED
        )
        # render/write must NOT have been called — guard must fire
        # before any vault interaction.
        self.assertEqual(self.rendered, [])
        self.assertEqual(self.writes, [])

    def test_knowledge_with_full_approval_succeeds(self) -> None:
        outcome = _run(
            self.worker.run_one(
                _request(
                    note_kind=NOTE_KIND_KNOWLEDGE,
                    approval_id="apv-1",
                    approved_by="masterway",
                    approved_at="2026-05-07T13:00:00+09:00",
                )
            )
        )
        self.assertIsNone(outcome.skipped_reason)
        assert outcome.job is not None
        self.assertEqual(outcome.job.state, JobState.SAVED)
        # Approval triple round-trips into the row's result_json so
        # the audit trail can answer "who approved this knowledge save?"
        self.assertEqual(outcome.job.result.get("approval_id"), "apv-1")
        self.assertEqual(outcome.job.result.get("approved_by"), "masterway")

    def test_overwrite_without_approval_lands_failed_retryable(self) -> None:
        # overwrite=True is irreversible from the audit standpoint
        # even on non-knowledge kinds, so the same guard fires.
        outcome = _run(
            self.worker.run_one(
                _request(note_kind=NOTE_KIND_RESEARCH, overwrite=True)
            )
        )
        self.assertEqual(outcome.skipped_reason, SKIPPED_APPROVAL_REQUIRED)

    def test_overwrite_with_approval_succeeds(self) -> None:
        outcome = _run(
            self.worker.run_one(
                _request(
                    overwrite=True,
                    approval_id="apv-overwrite-1",
                    approved_by="masterway",
                    approved_at="2026-05-07T13:00:00+09:00",
                )
            )
        )
        self.assertIsNone(outcome.skipped_reason)
        assert outcome.job is not None
        self.assertEqual(outcome.job.state, JobState.SAVED)
        # overwrite flag survives the queue trip and reaches the
        # write_fn — that's the seam M5a-2 needs to actually replace
        # the existing vault note.
        _, _, request_seen = self.writes[0]
        self.assertTrue(request_seen.overwrite)

    def test_partial_approval_still_blocks(self) -> None:
        # Producer bug: only approval_id populated. The guard refuses
        # — same error constant — so a half-filled approval doesn't
        # leak through.
        outcome = _run(
            self.worker.run_one(
                _request(
                    note_kind=NOTE_KIND_KNOWLEDGE,
                    approval_id="apv-1",
                    approved_by=None,
                    approved_at=None,
                )
            )
        )
        self.assertEqual(outcome.skipped_reason, SKIPPED_APPROVAL_REQUIRED)


# ---------------------------------------------------------------------------
# Failure paths — write_fn raise + vault unavailable
# ---------------------------------------------------------------------------


class WriteFailureTests(_Fixture):
    def test_write_fn_exception_lands_failed_retryable(self) -> None:
        def boom(_note, _vault, _request):
            raise OSError("disk full")

        worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=self.render_fn,
            write_fn=boom,
            vault_root_resolver=lambda _r: self.vault_root,
        )
        with self.assertRaises(OSError):
            _run(worker.run_one(_request()))
        rows = self.queue.list_for_session(
            "sess-m5b-1", states=[JobState.FAILED_RETRYABLE]
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("OSError", rows[0].result.get("error", ""))
        self.assertIn("disk full", rows[0].result.get("error", ""))


class VaultUnavailableTests(_Fixture):
    def test_resolver_returns_none_lands_failed_retryable(self) -> None:
        worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=self.render_fn,
            write_fn=self.write_fn,
            vault_root_resolver=lambda _r: None,
        )
        outcome = _run(worker.run_one(_request()))
        self.assertEqual(outcome.skipped_reason, SKIPPED_VAULT_UNAVAILABLE)
        rows = self.queue.list_for_session(
            "sess-m5b-1", states=[JobState.FAILED_RETRYABLE]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0].result.get("error"), SKIPPED_VAULT_UNAVAILABLE
        )
        # render / write must not have been invoked — vault unset
        # blocks before any rendering work.
        self.assertEqual(self.rendered, [])
        self.assertEqual(self.writes, [])

    def test_resolver_exception_treated_as_vault_unavailable(self) -> None:
        # If the resolver itself raises (env / discord client bug),
        # the worker treats it as "vault unavailable" so the operator
        # has one consistent recovery path: fix the env, requeue.
        def boom(_request):
            raise RuntimeError("OBSIDIAN_VAULT_PATH file not readable")

        worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=self.render_fn,
            write_fn=self.write_fn,
            vault_root_resolver=boom,
        )
        outcome = _run(worker.run_one(_request()))
        self.assertEqual(outcome.skipped_reason, SKIPPED_VAULT_UNAVAILABLE)


# ---------------------------------------------------------------------------
# Sync render/write/resolver compatibility (production passes async)
# ---------------------------------------------------------------------------


class SyncCallableSupportTests(_Fixture):
    def test_async_render_and_write_supported(self) -> None:
        async def async_render(request):
            return {"title": request.title, "async": True}

        async def async_write(note, vault, request):
            return _FakeWriteResult(
                target_path=vault / "ok.md", written=True, dry_run=False
            )

        worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=async_render,
            write_fn=async_write,
            vault_root_resolver=lambda _r: self.vault_root,
        )
        outcome = _run(worker.run_one(_request()))
        self.assertIsNone(outcome.skipped_reason)
        assert outcome.job is not None
        self.assertEqual(outcome.job.state, JobState.SAVED)


if __name__ == "__main__":
    unittest.main()
