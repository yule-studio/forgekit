"""A-M3 router-side wiring test.

Pin the contract that ``_run_research_loop_hook`` actually drives
research collection through the queue: each gateway invocation lands
a ``research_collect`` row, drives it to ``saved`` on success, and a
duplicate intake while the row is in-flight gets the "이미 진행 중"
notice instead of double-running the runner.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, List, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue import (
    JOB_TYPE_RESEARCH_COLLECT,
    JobQueue,
    JobState,
)
from yule_orchestrator.discord.engineering_channel_router import (
    EngineeringResearchLoopReport,
    _run_research_loop_hook,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _StubChannel:
    """Channel that captures send calls and supports `async with .typing()`."""

    def __init__(self) -> None:
        self.sends: List[str] = []

    def typing(self):  # noqa: D401 - protocol method
        return _NoopTyping()


class _NoopTyping:
    async def __aenter__(self) -> "_NoopTyping":
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class _StubMessage:
    def __init__(self, channel: _StubChannel) -> None:
        self.channel = channel
        self.attachments: list = []
        self.author = type("A", (), {"id": 1})()
        self.guild = type("G", (), {"id": 1})()


class _StubSession:
    def __init__(self, session_id: str = "sess-router-m3") -> None:
        self.session_id = session_id
        self.thread_id: Optional[int] = None
        self.extra: dict = {}
        # Required by attachments / collection metadata helpers.
        self.references_user: tuple = ()


class _BaseFixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        try:
            from tests._helpers import isolate_cache_for_test
        except ImportError:  # pragma: no cover
            from _helpers import isolate_cache_for_test  # type: ignore

        isolate_cache_for_test(self)

        self.channel = _StubChannel()
        self.message = _StubMessage(self.channel)
        self.session = _StubSession()
        self.sends: List[str] = []

        async def send_chunks(_channel, content: str, *args, **kwargs):
            self.sends.append(content)

        self.send_chunks = send_chunks

    def _all_jobs(self):
        # Helper: read all research_collect rows for the session via
        # a fresh JobQueue handle. The hook uses the same cache file
        # (YULE_CACHE_DB_PATH from isolate_cache_for_test).
        queue = JobQueue()
        return [
            job
            for job in queue.list_for_session(self.session.session_id)
            if job.job_type == JOB_TYPE_RESEARCH_COLLECT
        ]


class GatewayEnqueuesResearchCollectTests(_BaseFixture):
    def test_runner_call_creates_saved_research_collect_job(self) -> None:
        captured_job: dict[str, Any] = {}

        async def loop_fn(**kwargs):
            captured_job["called"] = True
            return EngineeringResearchLoopReport(
                forum_status_message="운영-리서치 thread 게시: 4242"
            )

        _run(
            _run_research_loop_hook(
                research_loop_fn=loop_fn,
                message=self.message,
                session=self.session,
                prompt_text="k8s 운영 자료 정리해줘",
                send_chunks=self.send_chunks,
            )
        )

        # Runner ran exactly once.
        self.assertTrue(captured_job.get("called"))
        # And the queue carries a SAVED research_collect row for the
        # session — the M2 reaper / supervisor / status diagnostic
        # can now reason about who did what.
        jobs = self._all_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].state, JobState.SAVED)

    def test_forum_status_message_is_still_sent_to_channel(self) -> None:
        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport(
                follow_up_message="자료가 부족해 사용자 입력이 필요합니다",
                forum_status_message="운영-리서치 thread 게시 완료",
            )

        _run(
            _run_research_loop_hook(
                research_loop_fn=loop_fn,
                message=self.message,
                session=self.session,
                prompt_text="...",
                send_chunks=self.send_chunks,
            )
        )

        # The hook still surfaces the runner's user-facing artifacts —
        # M3 only adds queue framing around the call.
        joined = "\n".join(self.sends)
        self.assertIn("자료가 부족해 사용자 입력이 필요합니다", joined)
        self.assertIn("운영-리서치 thread 게시 완료", joined)


class DuplicateIntakeDedupTests(_BaseFixture):
    def test_in_flight_duplicate_is_skipped_with_notice(self) -> None:
        # Plant an in-flight research_collect job — emulates the case
        # where the user re-types intake while the original run is
        # still grinding through Tavily/Brave.
        queue = JobQueue()
        seeded = queue.enqueue(
            session_id=self.session.session_id,
            job_type=JOB_TYPE_RESEARCH_COLLECT,
        )
        queue.transition(seeded.job_id, JobState.ASSIGNED)
        queue.transition(seeded.job_id, JobState.IN_PROGRESS)

        runner_called = False

        async def loop_fn(**_kwargs):
            nonlocal runner_called
            runner_called = True
            return EngineeringResearchLoopReport()

        _run(
            _run_research_loop_hook(
                research_loop_fn=loop_fn,
                message=self.message,
                session=self.session,
                prompt_text="다시 보내본다",
                send_chunks=self.send_chunks,
            )
        )

        # Critical: the runner is NOT called when a duplicate is
        # detected — burning the search budget twice is exactly the
        # regression M3 dedup is meant to prevent.
        self.assertFalse(runner_called)
        # And the user sees a "이미 진행 중" friendly notice.
        joined = "\n".join(self.sends)
        self.assertIn("이미 운영-리서치 수집이 진행 중", joined)


class RunnerExceptionTests(_BaseFixture):
    def test_runner_raise_lands_failed_retryable(self) -> None:
        async def loop_fn(**_kwargs):
            raise RuntimeError("provider 503 outage")

        report = _run(
            _run_research_loop_hook(
                research_loop_fn=loop_fn,
                message=self.message,
                session=self.session,
                prompt_text="...",
                send_chunks=self.send_chunks,
            )
        )

        # User sees the existing ⚠️ error line. A failed_retryable
        # row also lands in the queue so the M2 reaper / a manual
        # requeue_retryable can try again.
        self.assertIn("provider 503 outage", report.error or "")
        joined = "\n".join(self.sends)
        self.assertIn("research loop 실패", joined)

        jobs = self._all_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].state, JobState.FAILED_RETRYABLE)
        self.assertIn(
            "provider 503 outage", jobs[0].result.get("error", "")
        )


if __name__ == "__main__":
    unittest.main()
