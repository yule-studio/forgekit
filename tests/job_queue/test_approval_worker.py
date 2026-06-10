"""ApprovalWorker — A-M5a unit tests.

Pin the foundation contract: an approval card lifecycle goes
through the queue (enqueue → pick → post → save), duplicates are
dropped per ``(session, kind, source_message_id)``, posting
failures bounce the row to ``failed_retryable``, and an unset
``#승인-대기`` channel is also retryable (so an operator can fix
the env and replay the post).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    APPROVAL_KIND_RESEARCH_PROMOTION,
    ApprovalRequest,
    ApprovalWorker,
    JOB_TYPE_APPROVAL_POST,
    SERVICE_ID_APPROVAL_WORKER,
    SKIPPED_APPROVAL_CHANNEL_UNSET,
    SKIPPED_DUPLICATE,
    env_approval_channel_resolver,
    render_approval_request,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _request(**overrides) -> ApprovalRequest:
    base = dict(
        session_id="sess-1",
        approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
        title="k8s 운영 자료 정리 결정 노트",
        summary="3 개 source 검토, devops + backend 합의",
        requested_action="vault decisions/ 폴더에 저장",
        created_by="tech-lead",
        source_channel_id=1001,
        source_thread_id=2002,
        source_message_id=3003,
    )
    base.update(overrides)
    return ApprovalRequest(**base)


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)
        # Default: a stub channel resolver that returns a configured
        # channel id and a recording post_fn so each test can inspect
        # what the worker handed off to Discord.
        self.posted: list[tuple[ApprovalRequest, str]] = []

        async def post_fn(request, rendered):
            self.posted.append((request, rendered))
            return {"posted_message_id": 9999}

        self.post_fn = post_fn
        self.worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: 7777,
        )


# ---------------------------------------------------------------------------
# Render markdown — pure helper, no queue / no Discord.
# ---------------------------------------------------------------------------


class RenderApprovalRequestTests(unittest.TestCase):
    def test_render_includes_kind_label_session_and_action(self) -> None:
        request = _request(
            approval_kind=APPROVAL_KIND_RESEARCH_PROMOTION,
            title="hero copy 결정",
            summary="frontend + design 합의",
            requested_action="research → decisions 승격",
            created_by="qa-engineer",
        )
        rendered = render_approval_request(request)
        # Title carries the human-readable kind label, not the raw enum.
        self.assertIn("[승인 요청 — 리서치 결과 승격]", rendered)
        self.assertIn("hero copy 결정", rendered)
        self.assertIn("`sess-1`", rendered)
        self.assertIn("qa-engineer", rendered)
        self.assertIn("research → decisions 승격", rendered)
        # Source pointer line surfaces channel/thread/message ids so
        # the operator can jump back to the originating thread.
        self.assertIn("채널 `1001`", rendered)
        self.assertIn("thread `2002`", rendered)
        self.assertIn("메시지 `3003`", rendered)
        # Approval phrase hints at the bottom — must match the
        # in-channel UX phrases so the user has one consistent surface.
        self.assertIn("승인", rendered)
        self.assertIn("반려", rendered)

    def test_render_omits_optional_lines_when_absent(self) -> None:
        # No source ids + empty summary — render must not crash and
        # must not leave dangling labels.
        request = ApprovalRequest(
            session_id="sess-2",
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title="간단",
            summary="",
            requested_action="",
            created_by="tech-lead",
        )
        rendered = render_approval_request(request)
        # Source pointer line is conditional — must not appear with
        # empty body.
        self.assertNotIn("출처:", rendered)
        # "요약:" and "요청 액션:" are likewise conditional.
        self.assertNotIn("요약:", rendered)
        self.assertNotIn("요청 액션:", rendered)


# ---------------------------------------------------------------------------
# Enqueue + dedup
# ---------------------------------------------------------------------------


class EnqueueDedupTests(_Fixture):
    def test_enqueue_creates_approval_post_row(self) -> None:
        job, created = self.worker.enqueue(_request())
        self.assertTrue(created)
        self.assertEqual(job.job_type, JOB_TYPE_APPROVAL_POST)
        self.assertEqual(job.state, JobState.QUEUED)
        # Payload round-trips through SQLite TEXT — re-lift to verify.
        request_back = ApprovalRequest.from_payload(job.payload)
        self.assertEqual(request_back.title, "k8s 운영 자료 정리 결정 노트")
        self.assertEqual(request_back.source_message_id, 3003)

    def test_dedup_keys_on_session_kind_and_source_message(self) -> None:
        first, _ = self.worker.enqueue(_request())
        second, created = self.worker.enqueue(_request())
        # Same (session, kind, source_message_id) → dedup hits.
        self.assertEqual(first.job_id, second.job_id)
        self.assertFalse(created)

    def test_different_kind_same_source_does_not_dedup(self) -> None:
        # Same source message but different approval kind — both
        # cards should land. Common in production: research_promotion
        # and obsidian_write can both come from the same synthesis
        # comment.
        a, _ = self.worker.enqueue(
            _request(approval_kind=APPROVAL_KIND_RESEARCH_PROMOTION)
        )
        b, created = self.worker.enqueue(
            _request(approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE)
        )
        self.assertNotEqual(a.job_id, b.job_id)
        self.assertTrue(created)

    def test_terminal_jobs_do_not_block_new_enqueue(self) -> None:
        first, _ = self.worker.enqueue(_request())
        # Drive to SAVED so a re-post on the same triple is allowed.
        self.queue.transition(first.job_id, JobState.ASSIGNED)
        self.queue.transition(first.job_id, JobState.IN_PROGRESS)
        self.queue.transition(first.job_id, JobState.SAVED)
        second, created = self.worker.enqueue(_request())
        self.assertTrue(created)
        self.assertNotEqual(first.job_id, second.job_id)


# ---------------------------------------------------------------------------
# run_one — happy path: post_fn called, state walks to SAVED, heartbeat lands.
# ---------------------------------------------------------------------------


class RunOneSuccessTests(_Fixture):
    def test_run_one_success_invokes_post_fn_and_saves(self) -> None:
        outcome = _run(self.worker.run_one(_request()))
        self.assertIsNone(outcome.skipped_reason)
        assert outcome.job is not None
        self.assertEqual(outcome.job.state, JobState.SAVED)
        # post_fn was called exactly once with the typed request and
        # the rendered markdown — this is the seam M5a-2's gateway
        # producer will replace with the actual Discord client call.
        self.assertEqual(len(self.posted), 1)
        request_seen, rendered_seen = self.posted[0]
        self.assertEqual(request_seen.session_id, "sess-1")
        self.assertIn("승인 요청", rendered_seen)
        # Posted message id captured into the job's result_json so
        # the supervisor / status diagnostic can find it later.
        self.assertEqual(outcome.job.result.get("posted_message_id"), 9999)
        # And the approval channel id is stamped on the row so the
        # diagnostic can describe where the card actually went.
        self.assertEqual(outcome.job.result.get("channel_id"), 7777)

    def test_run_one_records_heartbeat(self) -> None:
        _run(self.worker.run_one(_request()))
        beat = self.heartbeats.get(SERVICE_ID_APPROVAL_WORKER)
        self.assertIsNotNone(beat)

    def test_duplicate_run_one_skips_post(self) -> None:
        # Plant an in-flight job, then call run_one. Worker must
        # NOT re-post — that's exactly the regression M5a dedup
        # is designed to prevent (running the same approval card
        # twice would notify the user twice for one decision).
        first, _ = self.worker.enqueue(_request())
        self.queue.transition(first.job_id, JobState.ASSIGNED)
        self.queue.transition(first.job_id, JobState.IN_PROGRESS)

        outcome = _run(self.worker.run_one(_request()))
        self.assertEqual(outcome.skipped_reason, SKIPPED_DUPLICATE)
        # post_fn must not have been called for the duplicate.
        self.assertEqual(len(self.posted), 0)


# ---------------------------------------------------------------------------
# post_fn failure → failed_retryable
# ---------------------------------------------------------------------------


class PostFailureTests(_Fixture):
    def test_post_fn_exception_lands_failed_retryable(self) -> None:
        async def boom(_request, _rendered):
            raise RuntimeError("discord 5xx outage")

        worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=boom,
            channel_resolver=lambda: 7777,
        )

        with self.assertRaises(RuntimeError):
            _run(worker.run_one(_request()))

        rows = self.queue.list_for_session(
            "sess-1", states=[JobState.FAILED_RETRYABLE]
        )
        self.assertEqual(len(rows), 1)
        failed = rows[0]
        self.assertIsNone(failed.picked_by)
        self.assertIsNone(failed.picked_until)
        self.assertIn("RuntimeError", failed.result.get("error", ""))
        self.assertIn("discord 5xx outage", failed.result.get("error", ""))


# ---------------------------------------------------------------------------
# Channel unset → failed_retryable with the dedicated error string.
# ---------------------------------------------------------------------------


class ChannelUnsetTests(_Fixture):
    def test_channel_unset_marks_failed_retryable_with_error_constant(self) -> None:
        async def post_fn(_request, _rendered):  # pragma: no cover
            self.fail("post_fn must not run when channel is unset")

        worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: None,
        )

        outcome = _run(worker.run_one(_request()))
        # User-facing skipped_reason matches the dedicated constant
        # so the gateway can render a precise "approval channel
        # unset, please set DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID"
        # notice instead of a generic "post failed".
        self.assertEqual(
            outcome.skipped_reason, SKIPPED_APPROVAL_CHANNEL_UNSET
        )
        rows = self.queue.list_for_session(
            "sess-1", states=[JobState.FAILED_RETRYABLE]
        )
        self.assertEqual(len(rows), 1)
        # The error string is the exact constant — no formatting
        # drift so a future requeue / supervisor diagnostic match.
        self.assertEqual(
            rows[0].result.get("error"), SKIPPED_APPROVAL_CHANNEL_UNSET
        )

    def test_channel_resolver_exception_treated_as_unset(self) -> None:
        # If the resolver itself raises (env reader bug), the worker
        # must NOT raise — instead treat as "channel unset" so the
        # gateway has one consistent error path.
        async def post_fn(_request, _rendered):  # pragma: no cover
            self.fail("post_fn must not run when resolver raises")

        def boom() -> int:
            raise RuntimeError("env file not readable")

        worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=boom,
        )

        outcome = _run(worker.run_one(_request()))
        self.assertEqual(
            outcome.skipped_reason, SKIPPED_APPROVAL_CHANNEL_UNSET
        )


# ---------------------------------------------------------------------------
# env_approval_channel_resolver — direct env-reader test (no queue side).
# ---------------------------------------------------------------------------


class EnvResolverTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        # Restore env on cleanup so this test doesn't leak the
        # mutation into the rest of the suite (M3 had the same
        # ``YULE_CACHE_DB_PATH`` leak — keep us defensive).
        self._prev = os.environ.get(
            "DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID"
        )

    def tearDown(self) -> None:  # noqa: D401
        if self._prev is None:
            os.environ.pop(
                "DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID", None
            )
        else:
            os.environ[
                "DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID"
            ] = self._prev

    def test_resolver_returns_int_when_env_set(self) -> None:
        os.environ["DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID"] = "424242"
        self.assertEqual(env_approval_channel_resolver(), 424242)

    def test_resolver_returns_none_when_unset(self) -> None:
        os.environ.pop("DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID", None)
        self.assertIsNone(env_approval_channel_resolver())

    def test_resolver_returns_none_when_env_is_garbage(self) -> None:
        os.environ["DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID"] = "not-a-number"
        self.assertIsNone(env_approval_channel_resolver())


# ---------------------------------------------------------------------------
# Sync post_fn compatibility — production passes a coroutine, but
# tests / CLI tools sometimes pass a sync callable. The worker
# handles both via _maybe_await.
# ---------------------------------------------------------------------------


class SyncPostFnTests(_Fixture):
    def test_sync_post_fn_is_supported(self) -> None:
        called_with: list[tuple[ApprovalRequest, str]] = []

        def sync_post(request, rendered):  # noqa: D401 - test stub
            called_with.append((request, rendered))
            return {"posted_message_id": 1}

        worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=sync_post,
            channel_resolver=lambda: 7777,
        )
        outcome = _run(worker.run_one(_request()))
        self.assertIsNone(outcome.skipped_reason)
        self.assertEqual(len(called_with), 1)


if __name__ == "__main__":
    unittest.main()
