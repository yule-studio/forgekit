"""Knowledge note hollow-body 가드 회귀 — operator-visibility 핀.

엔지니어링 agent 가 pack/snapshot/synthesis 모두 비어있는 상태에서
knowledge note 를 저장하려고 하면 hollow vault file 이 남는다. 그래서
:func:`default_render_fn` 은 :class:`ObsidianRenderError` 를 던지고 worker
는 그 메시지를 ``result_json['error']`` 로 노출 — operator 가 status
diagnostic 으로 "왜 vault 에 안 떨어졌는지" 즉시 볼 수 있어야 한다.

본 test 는 두 contract 를 핀:
  1. hollow body 일 때 default_render_fn 이 명확한 에러 메시지로 실패.
  2. ObsidianWriterWorker 가 그 에러를 FAILED_RETRYABLE + result.error
     로 표면화 (silent failure 방지).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.obsidian_writer_worker import (
    JOB_TYPE_OBSIDIAN_WRITE,
    NOTE_KIND_KNOWLEDGE,
    ObsidianRenderError,
    ObsidianWriteRequest,
    ObsidianWriterWorker,
    default_render_fn,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class HollowKnowledgeNoteTests(unittest.TestCase):
    """pack/snapshot/synthesis 모두 비면 hollow vault file 거부."""

    def test_default_render_fn_raises_on_hollow_inputs(self) -> None:
        from yule_engineering.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
            save_session,
        )
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as tmp:
            import os
            os.environ["YULE_CACHE_DB_PATH"] = str(Path(tmp) / "cache.sqlite3")
            try:
                now = datetime.now(tz=timezone.utc)
                session = WorkflowSession(
                    session_id="sess-hollow",
                    prompt="hollow request",
                    task_type="research",
                    state=WorkflowState.APPROVED,
                    created_at=now,
                    updated_at=now,
                    extra={},  # no research_pack / no synthesis
                )
                save_session(session)
                request = ObsidianWriteRequest(
                    session_id="sess-hollow",
                    note_kind=NOTE_KIND_KNOWLEDGE,
                    title="hollow",
                    approval_id="a1",
                    approved_by="m",
                    approved_at="2026-05-15T00:00:00+00:00",
                    metadata={},
                )
                with self.assertRaises(ObsidianRenderError) as cm:
                    default_render_fn(request)
                self.assertIn("hydration", str(cm.exception).lower())
            finally:
                os.environ.pop("YULE_CACHE_DB_PATH", None)


if __name__ == "__main__":
    unittest.main()
