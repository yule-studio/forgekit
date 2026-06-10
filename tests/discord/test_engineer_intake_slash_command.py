"""Live smoke fix — `/engineer_intake` slash command 회귀.

Reproduces session **c5278a9043f2**:

  /engineer_intake
    prompt: approval_required, single_repo, full_stack_single_repo로 진행해줘
            repo: https://github.com/yule-studio/naver-search-clone.git
            목표: Next.js + NestJS + PostgreSQL + Docker Compose 기반
                  회원가입/로그인/로그아웃/검색 결과 목록 앱 구현
    task_type: (blank)
    write_requested: true

  관찰된 결과:
    - session.task_type = "qa-test"          ← misclassified
    - executor_role     = "qa-engineer"      ← derived from misclassification
    - progress_notes    = []                 ← coding_execute 진입 못 함
    - #승인-대기 카드 누락                    ← intake 본문만 떴음

  본 PR 후 기대:
    - task_type = "full-stack-app"
    - executor_role = "backend-engineer"
    - approval card 가 ApprovalWorker 큐에 적재 (production env 시 #승인-대기 게시)

본 test 가 통과 = session c5278a9043f2 회귀가 다시 silently 들어와도
가장 먼저 잡힌다.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import List, Tuple
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


_C5278A9043F2_PROMPT = (
    "approval_required, single_repo, full_stack_single_repo로 진행해줘.\n"
    "repo: https://github.com/yule-studio/naver-search-clone.git\n"
    "목표: Next.js + NestJS + PostgreSQL + Docker Compose 기반 "
    "회원가입/로그인/로그아웃/검색 결과 목록 앱 구현"
)


class _SmokeFixture(unittest.TestCase):
    """Slash command path test fixture.

    `/engineer_intake` → `_run_engineer_intake` → `WorkflowOrchestrator.intake`
    + (P0-T) `_maybe_post_intake_approval_card` 가 production ApprovalWorker
    를 빌드해 카드 enqueue. 본 test 는 ApprovalWorker.run_one 을 patch 해
    실제 Discord REST 호출 없이 enqueue 만 검증.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # session.extra 갱신 / 큐 row stash 가 `.cache/yule/cache.sqlite3`
        # 또는 env 의 path 로 흘러가지 않게 격리
        self._db_path = Path(self._tmp.name) / "cache.sqlite3"
        os.environ["YULE_CACHE_DB_PATH"] = str(self._db_path)
        self.addCleanup(lambda: os.environ.pop("YULE_CACHE_DB_PATH", None))


# ---------------------------------------------------------------------------
# Classification regression — session c5278a9043f2 repro
# ---------------------------------------------------------------------------


class IntakeClassificationTests(_SmokeFixture):
    def test_session_c5278a9043f2_repro_classifies_as_full_stack(self) -> None:
        """session c5278a9043f2 의 prompt 가 task_type=full-stack-app 으로
        분류되고 executor=backend-engineer 가 선정되는지 확인."""

        from yule_discord.commands import _run_engineer_intake

        # `_maybe_post_intake_approval_card` 의 production worker 빌드를
        # patch — 실제 Discord REST 호출 우회. enqueue 자체가 일어나는지만
        # 검증.
        with mock.patch(
            "yule_discord.commands._maybe_post_intake_approval_card",
            autospec=True,
        ) as patched:
            result = _run_engineer_intake(
                prompt=_C5278A9043F2_PROMPT,
                task_type=None,
                write_requested=True,
                channel_id=100,
                user_id=42,
            )

        # 1. classification — full-stack-app
        self.assertEqual(
            result.session.task_type,
            "full-stack-app",
            f"session c5278a9043f2 repro: 분류가 여전히 잘못됨 — got {result.session.task_type!r}",
        )

        # 2. executor — backend-engineer (FULL_STACK_APP → backend-engineer 매핑)
        self.assertEqual(
            result.session.executor_role,
            "backend-engineer",
            f"executor_role 매핑 회귀 — got {result.session.executor_role!r}",
        )

        # 3. write_requested 플래그 보존
        self.assertTrue(result.session.write_requested)

        # 4. _maybe_post_intake_approval_card 가 호출됨 (write_requested=true 이므로)
        patched.assert_called_once()
        call_kwargs = patched.call_args.kwargs
        self.assertEqual(call_kwargs["session"].session_id, result.session.session_id)
        self.assertEqual(call_kwargs["prompt_text"], _C5278A9043F2_PROMPT)

    def test_explicit_task_type_still_overrides(self) -> None:
        """operator 가 명시한 task_type 은 stack_detector 를 무시."""

        from yule_discord.commands import _run_engineer_intake

        with mock.patch(
            "yule_discord.commands._maybe_post_intake_approval_card",
            autospec=True,
        ):
            result = _run_engineer_intake(
                prompt=_C5278A9043F2_PROMPT,
                task_type="qa-test",
                write_requested=True,
                channel_id=100,
                user_id=42,
            )
        self.assertEqual(result.session.task_type, "qa-test")

    def test_write_requested_false_skips_approval_card(self) -> None:
        """write_requested=False 면 approval card 게시 시도하지 않음."""

        from yule_discord.commands import _run_engineer_intake

        with mock.patch(
            "yule_discord.commands._maybe_post_intake_approval_card",
            autospec=True,
        ) as patched:
            _run_engineer_intake(
                prompt=_C5278A9043F2_PROMPT,
                task_type=None,
                write_requested=False,
                channel_id=100,
                user_id=42,
            )
        patched.assert_not_called()


# ---------------------------------------------------------------------------
# Approval card auto-enqueue — pure helper test
# ---------------------------------------------------------------------------


class IntakeApprovalCardEnqueueTests(_SmokeFixture):
    """`_maybe_post_intake_approval_card` 가 실제로 ApprovalWorker 큐에
    카드를 적재하는지 — production REST 는 mock 으로 우회."""

    def test_session_c5278a9043f2_repro_enqueues_approval_card(self) -> None:
        from types import SimpleNamespace

        # `enqueue_github_work_approval` 자체를 patch — production worker
        # 빌드 path 를 따라가지만 마지막 호출만 가로챈다. 실제 카드 enqueue
        # contract (eligible 한 prompt 가 enqueue 함수까지 도달하는지) 검증.
        captured = {"calls": []}

        async def _stub_enqueue(**kwargs):
            captured["calls"].append(kwargs)
            return SimpleNamespace(
                proposal=SimpleNamespace(proposal_id="p-stub"),
                approval_job_id="job-stub",
                approval_post_outcome=None,
                skipped_reason=None,
            )

        with mock.patch(
            "yule_discord.integrations.github_workos_adapter.enqueue_github_work_approval",
            new=_stub_enqueue,
        ):
            from yule_discord.commands import (
                _maybe_post_intake_approval_card,
            )

            session = SimpleNamespace(
                session_id="sess-c5278a9043f2-repro",
                prompt=_C5278A9043F2_PROMPT,
                task_type="full-stack-app",
                state="intake",
                channel_id=100,
                user_id=42,
                thread_id=None,
                extra={
                    "lifecycle_mode": "implementation",
                    "active_research_roles": ["tech-lead", "backend-engineer"],
                },
                write_requested=True,
            )
            _maybe_post_intake_approval_card(
                session=session,
                prompt_text=_C5278A9043F2_PROMPT,
                requested_by="42",
            )

        self.assertEqual(
            len(captured["calls"]),
            1,
            f"session c5278a9043f2 repro: enqueue_github_work_approval 호출 누락 — "
            f"카드가 #승인-대기 에 안 뜬다는 뜻",
        )
        kwargs = captured["calls"][0]
        self.assertEqual(kwargs["session"].session_id, "sess-c5278a9043f2-repro")
        self.assertEqual(kwargs["request_text"], _C5278A9043F2_PROMPT)
        self.assertEqual(kwargs["requested_by"], "42")
        # approval_worker 가 inject 됐는지 — None 이면 enqueue 가 본질상 의미 없음
        self.assertIsNotNone(kwargs.get("approval_worker"))


if __name__ == "__main__":
    unittest.main()
