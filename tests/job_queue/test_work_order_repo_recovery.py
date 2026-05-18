"""work_order executor repo fallback + startup retry hook 회귀 — P0-T.

라이브 관찰 (session ``c5278a9043f2`` 후속):
  - producer bug 로 `/engineer_intake` 가 repo 정보 없이 work_order 를
    enqueue
  - executor 가 SKIPPED_NO_REPO 로 failed_retryable 처리
  - bug fix 후 runtime restart 만으로는 stranded rows 가 자동 재실행되지
    않음 — operator 수동 DB 조작 필요

본 PR fix 2 갭:
  1. **Executor fallback** — payload.repo 비어있을 때 session refs /
     extra / prompt / request_summary 에서 canonical owner/repo 복구.
     성공 시 그대로 issue create 진행.
  2. **Startup retry hook** — `requeue_no_repo_failures` 가 SKIPPED_NO_REPO
     로 떨어진 failed_retryable rows 를 자동 requeue.

본 test 가 통과 = recovery / resume semantics 가 살아있다는 뜻.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.github_workos.audit import OUTCOME_OK
from yule_orchestrator.agents.job_queue.github_work_order import (
    GitHubWorkOrder,
    dispatch_github_work_order,
    JOB_TYPE_GITHUB_WORK_ORDER,
)
from yule_orchestrator.agents.job_queue.github_work_order_executor import (
    CREATED_VIA_AUTO_CREATE,
    GitHubWorkOrderWorker,
    SESSION_EXTRA_GITHUB_ISSUE_KEY,
    SKIPPED_MISSING_PLAN,
    SKIPPED_NO_REPO,
    requeue_missing_plan_failures,
    requeue_no_repo_failures,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _SessionFake:
    session_id: str
    prompt: str = ""
    references_user: Tuple[str, ...] = ()
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _WriterResult:
    ok: bool = True
    outcome: str = OUTCOME_OK
    succeeded: bool = True
    body: Mapping[str, Any] = field(default_factory=dict)


class _StubWriter:
    def __init__(self) -> None:
        self.calls: List[Mapping[str, Any]] = []
        self._next = 77

    def create_issue(self, **kwargs):
        self.calls.append(kwargs)
        n = self._next
        self._next += 1
        return _WriterResult(
            body={
                "number": n,
                "html_url": f"https://github.com/{kwargs['repo']}/issues/{n}",
                "url": f"https://api.github.com/repos/{kwargs['repo']}/issues/{n}",
            },
        )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "q.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)
        self.sessions: Dict[str, _SessionFake] = {}
        self.writer = _StubWriter()

    def _build_worker(self, *, writer: Optional[_StubWriter] = None):
        return GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=lambda _wo: (writer or self.writer, "L2"),
            heartbeats=self.heartbeats,
            load_session_fn=lambda sid: self.sessions.get(sid),
            update_session_fn=self._update_session,
        )

    def _update_session(self, session, new_extra):
        session.extra = dict(new_extra)
        self.sessions[session.session_id] = session
        return session

    def _coding_proposal(self, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "user_request": "live recover",
            "executor_role": "backend-engineer",
            "review_roles": ["tech-lead"],
            "participant_roles": ["backend-engineer", "tech-lead"],
            "write_scope": ["src/api/auth"],
            "forbidden_scope": [".env"],
            "safety_rules": ["no force push"],
            "reason": "live recover",
            "approval_required": True,
            "metadata": {},
            "lifecycle_mode": "implementation",
        }

    def _sample_plan(self) -> Mapping[str, Any]:
        return {
            "title": "[Feature] live recover",
            "body": "## 어떤 기능인가요?\n> recover\n",
            "labels": ["✨ Feature"],
            "assignees": [],
            "template_path": ".github/ISSUE_TEMPLATE/feature.md",
            "confidence": "high",
            "audit_reason": "template_used",
            "needs_operator_decision": False,
            "template_score": 2,
        }


# ---------------------------------------------------------------------------
# Executor fallback
# ---------------------------------------------------------------------------


class ExecutorRepoFallbackTests(_Fixture):
    def test_repo_recovered_from_session_references_user(self) -> None:
        """payload.repo 비어있어도 session.references_user 의 GitHub URL
        에서 canonical owner/repo 복구 후 issue create 진행."""

        self.sessions["sess-r1"] = _SessionFake(
            session_id="sess-r1",
            prompt="구현해줘",
            references_user=("https://github.com/yule-studio/naver-search-clone",),
            extra={"coding_proposal": self._coding_proposal("sess-r1")},
        )
        wo = GitHubWorkOrder(
            proposal_id="p-r1",
            session_id="sess-r1",
            approval_id="a-r1",
            approved_by="m",
            approved_at="2026-05-16T13:00:00+00:00",
            request_summary="회원가입 구현",
            repo=None,  # producer bug — repo missing
            dry_run=False,
            issue_auto_create_plan=self._sample_plan(),
        )
        outcome = dispatch_github_work_order(self.queue, wo)
        assert outcome.job is not None

        worker = self._build_worker()
        exec_outcome = worker.run_one()
        assert exec_outcome is not None
        self.assertEqual(exec_outcome.created_via, CREATED_VIA_AUTO_CREATE)
        # writer 호출 시 복구된 repo 전달됨
        self.assertEqual(
            self.writer.calls[0]["repo"],
            "yule-studio/naver-search-clone",
        )

    def test_repo_recovered_from_extra_coding_repo_full_name(self) -> None:
        self.sessions["sess-r2"] = _SessionFake(
            session_id="sess-r2",
            extra={
                "coding_proposal": self._coding_proposal("sess-r2"),
                "coding_repo_full_name": "owner/cached-repo",
            },
        )
        wo = GitHubWorkOrder(
            proposal_id="p-r2",
            session_id="sess-r2",
            approval_id="a-r2",
            approved_by="m",
            approved_at="2026-05-16T13:00:00+00:00",
            request_summary="x",
            repo="",  # blank
            dry_run=False,
            issue_auto_create_plan=self._sample_plan(),
        )
        dispatch_github_work_order(self.queue, wo)
        outcome = self._build_worker().run_one()
        assert outcome is not None
        self.assertEqual(self.writer.calls[0]["repo"], "owner/cached-repo")

    def test_repo_recovered_from_request_summary_url(self) -> None:
        self.sessions["sess-r3"] = _SessionFake(
            session_id="sess-r3",
            extra={"coding_proposal": self._coding_proposal("sess-r3")},
        )
        wo = GitHubWorkOrder(
            proposal_id="p-r3",
            session_id="sess-r3",
            approval_id="a-r3",
            approved_by="m",
            approved_at="2026-05-16T13:00:00+00:00",
            request_summary=(
                "approval_required https://github.com/yule-studio/another-repo.git "
                "기반 풀스택 구현"
            ),
            repo=None,
            dry_run=False,
            issue_auto_create_plan=self._sample_plan(),
        )
        dispatch_github_work_order(self.queue, wo)
        outcome = self._build_worker().run_one()
        assert outcome is not None
        self.assertEqual(
            self.writer.calls[0]["repo"], "yule-studio/another-repo"
        )

    def test_no_recoverable_repo_still_fails_skip_no_repo(self) -> None:
        """canonical GitHub URL 이 어디에도 없으면 invalid guess 금지 —
        SKIPPED_NO_REPO 로 명확히 실패."""

        self.sessions["sess-rN"] = _SessionFake(
            session_id="sess-rN",
            prompt="repo 정보 없는 일반 자연어 요청",
            extra={"coding_proposal": self._coding_proposal("sess-rN")},
        )
        wo = GitHubWorkOrder(
            proposal_id="p-rN",
            session_id="sess-rN",
            approval_id="a-rN",
            approved_by="m",
            approved_at="2026-05-16T13:00:00+00:00",
            request_summary="github 키워드만 있고 URL 은 없음",
            repo=None,
            dry_run=False,
            issue_auto_create_plan=self._sample_plan(),
        )
        job = dispatch_github_work_order(self.queue, wo).job
        assert job is not None

        outcome = self._build_worker().run_one()
        assert outcome is not None
        self.assertEqual(outcome.skipped_reason, SKIPPED_NO_REPO)
        # writer 호출 없음
        self.assertEqual(self.writer.calls, [])
        # row 가 failed_retryable
        refreshed = self.queue.get(job.job_id)
        self.assertEqual(refreshed.state, JobState.FAILED_RETRYABLE)


# ---------------------------------------------------------------------------
# Startup retry hook
# ---------------------------------------------------------------------------


class StartupRetryHookTests(_Fixture):
    def test_requeue_no_repo_failures_resurrects_failed_retryable_rows(self) -> None:
        """기존 failed_retryable rows (error=github_work_order_no_repo) 를
        bug fix 후 자동 requeue. operator 수동 DB 조작 없음."""

        # 1. session 에 GitHub URL 추가 (recovery 가능하게 fix 된 상태)
        self.sessions["sess-old"] = _SessionFake(
            session_id="sess-old",
            references_user=("https://github.com/yule-studio/naver-search-clone",),
            extra={"coding_proposal": self._coding_proposal("sess-old")},
        )

        # 2. failed_retryable row 시뮬레이션 (producer bug 시점에 stamp 된 것)
        wo = GitHubWorkOrder(
            proposal_id="p-old",
            session_id="sess-old",
            approval_id="a-old",
            approved_by="m",
            approved_at="2026-05-16T11:00:00+00:00",
            request_summary="회원가입 구현",
            repo=None,
            dry_run=False,
            issue_auto_create_plan=self._sample_plan(),
        )
        job = dispatch_github_work_order(self.queue, wo).job
        assert job is not None
        # 한 번 drain — bug 상태에서는 fallback 없으니 SKIPPED_NO_REPO 로
        # failed_retryable 처리. 본 test 에서는 fallback 이 있으므로 다른
        # 방식으로 fail 상태를 직접 만든다 — repo 와 session 모두 없는
        # 케이스로 fail 후, session 을 추가하고 startup hook 호출.
        worker_pre_fix = GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=lambda _wo: (self.writer, "L2"),
            heartbeats=self.heartbeats,
            load_session_fn=lambda sid: None,  # bug 시점 session 없음
            update_session_fn=lambda s, e: s,
        )
        worker_pre_fix.run_one()
        refreshed = self.queue.get(job.job_id)
        self.assertEqual(refreshed.state, JobState.FAILED_RETRYABLE)
        self.assertEqual(refreshed.result.get("error"), SKIPPED_NO_REPO)

        # 3. fix 후 startup hook 호출 — 본 row 가 requeue 됨
        logs: List[Tuple[str, Optional[Any]]] = []
        requeued = requeue_no_repo_failures(
            self.queue, log_fn=lambda msg, exc: logs.append((msg, exc))
        )
        self.assertIn(job.job_id, requeued)

        post = self.queue.get(job.job_id)
        self.assertEqual(post.state, JobState.QUEUED)
        self.assertGreater(post.attempt, refreshed.attempt)

        # 4. fix 된 worker (session 보유) 가 re-pick → 성공
        worker_post_fix = self._build_worker()
        re_outcome = worker_post_fix.run_one()
        assert re_outcome is not None
        self.assertEqual(re_outcome.created_via, CREATED_VIA_AUTO_CREATE)
        self.assertEqual(
            self.writer.calls[0]["repo"],
            "yule-studio/naver-search-clone",
        )
        # session anchor stamp
        sess = self.sessions["sess-old"]
        self.assertIn(SESSION_EXTRA_GITHUB_ISSUE_KEY, sess.extra)

    def test_requeue_skips_other_failures(self) -> None:
        """다른 error reason 의 failed_retryable rows 는 건드리지 않음.

        P0-V 이후엔 plan-missing + repo present 조합이 executor self-heal
        로 성공하므로, "다른 사유로 fail" 상태를 만들려면 writer 가 없는
        경로 (SKIPPED_NO_WRITER) 로 떨어뜨린다.
        """

        wo = GitHubWorkOrder(
            proposal_id="p-other",
            session_id="sess-other",
            approval_id="a-other",
            approved_by="m",
            approved_at="2026-05-16T11:00:00+00:00",
            request_summary="x",
            repo="owner/repo",
            dry_run=False,
            issue_auto_create_plan={
                "title": "fake",
                "body": "body",
                "labels": [],
                "assignees": [],
            },
        )
        job = dispatch_github_work_order(self.queue, wo).job
        assert job is not None
        # writer_factory 가 None 을 반환 — SKIPPED_NO_WRITER failed_retryable
        worker = GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=lambda _wo: (None, "L2"),
            heartbeats=self.heartbeats,
        )
        worker.run_one()
        refreshed = self.queue.get(job.job_id)
        self.assertEqual(refreshed.state, JobState.FAILED_RETRYABLE)
        self.assertNotEqual(refreshed.result.get("error"), SKIPPED_NO_REPO)

        # startup hook 은 본 row 를 건드리지 않음
        requeued = requeue_no_repo_failures(self.queue)
        self.assertEqual(requeued, ())
        post = self.queue.get(job.job_id)
        self.assertEqual(post.state, JobState.FAILED_RETRYABLE)


# ---------------------------------------------------------------------------
# P0-V — plan missing self-heal + startup hook
# ---------------------------------------------------------------------------


class StartupRetryHookMissingPlanTests(unittest.TestCase):
    """Mirror of `StartupRetryHookTests` but for SKIPPED_MISSING_PLAN.

    옛 producer 가 plan 을 빠뜨리고 enqueue 한 row 들이 fix 후에도
    영원히 stranded 로 남는 회귀를 차단.
    """

    def setUp(self) -> None:
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        self.db_path = Path(tmp_dir.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)

        class _Writer:
            def __init__(self) -> None:
                self.calls: List[Mapping[str, Any]] = []

            def create_issue(self, **kwargs):  # noqa: ANN003
                self.calls.append(kwargs)
                return type(
                    "WriteResult",
                    (),
                    {
                        "outcome": OUTCOME_OK,
                        "succeeded": True,
                        "body": {
                            "number": 99,
                            "html_url": "https://example.test/issues/99",
                            "url": "https://example.test/api/issues/99",
                        },
                    },
                )()

        self.writer = _Writer()

    def test_executor_recovers_plan_for_old_failed_row(self) -> None:
        # 1. plan 없이 enqueue (옛 producer 시뮬)
        wo = GitHubWorkOrder(
            proposal_id="p-old",
            session_id="sess-old-plan",
            approval_id="a-old",
            approved_by="m",
            approved_at="2026-05-17T11:00:00+00:00",
            request_summary="네이버 검색 풀스택 MVP 구현해줘",
            repo="yule-studio/naver-search-clone",
            dry_run=False,
            # plan / existing_issue 둘 다 빠짐
        )
        job = dispatch_github_work_order(self.queue, wo).job
        assert job is not None

        # 2. self-heal 이 들어가기 전 시점 시뮬 — writer 없는 worker 가
        # 일단 SKIPPED_MISSING_PLAN 으로 떨어뜨린다. 하지만 본 PR 의 self-heal
        # 은 worker 가 writer 를 갖고 있을 때 작동하므로 그 경로를 정확히
        # 모사하려면 옛 동작을 흉내내야 한다 — 그래서 미리 result_json 에
        # SKIPPED_MISSING_PLAN 을 강제로 stamp.
        import json as _json
        import sqlite3 as _sqlite3

        with _sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE job_queue SET state = ?, result_json = ? WHERE job_id = ?",
                (
                    JobState.FAILED_RETRYABLE.value,
                    _json.dumps({"error": SKIPPED_MISSING_PLAN}),
                    job.job_id,
                ),
            )
            conn.commit()

        refreshed = self.queue.get(job.job_id)
        self.assertEqual(refreshed.state, JobState.FAILED_RETRYABLE)

        # 3. startup hook 실행 → row 가 queued 로 복귀
        requeued = requeue_missing_plan_failures(self.queue)
        self.assertIn(job.job_id, requeued)
        re = self.queue.get(job.job_id)
        self.assertEqual(re.state, JobState.QUEUED)

        # 4. 본 PR self-heal worker 가 re-pick → plan 재구성 → 성공
        worker = GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=lambda _wo: (self.writer, "L2"),
            heartbeats=self.heartbeats,
        )
        outcome = worker.run_one()
        assert outcome is not None
        self.assertIsNone(
            outcome.skipped_reason,
            "self-heal 으로 plan 재구성 후 정상 issue 생성되어야 함",
        )
        self.assertEqual(outcome.created_via, CREATED_VIA_AUTO_CREATE)
        self.assertEqual(len(self.writer.calls), 1)
        self.assertEqual(
            self.writer.calls[0]["repo"], "yule-studio/naver-search-clone"
        )

    def test_requeue_missing_plan_skips_no_repo_failures(self) -> None:
        """`requeue_missing_plan_failures` 는 SKIPPED_NO_REPO row 는 안 건드림."""

        wo = GitHubWorkOrder(
            proposal_id="p-noco",
            session_id="sess-no-repo-row",
            approval_id="a-noco",
            approved_by="m",
            approved_at="2026-05-17T11:00:00+00:00",
            request_summary="x",
            repo=None,
            issue_auto_create_plan={"title": "t", "body": "b"},
        )
        job = dispatch_github_work_order(self.queue, wo).job
        assert job is not None

        # session 없는 worker — SKIPPED_NO_REPO 로 떨어뜨림
        worker = GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=lambda _wo: (self.writer, "L2"),
            heartbeats=self.heartbeats,
        )
        worker.run_one()
        refreshed = self.queue.get(job.job_id)
        self.assertEqual(refreshed.result.get("error"), SKIPPED_NO_REPO)

        requeued = requeue_missing_plan_failures(self.queue)
        self.assertEqual(requeued, ())
        post = self.queue.get(job.job_id)
        self.assertEqual(post.state, JobState.FAILED_RETRYABLE)


# ---------------------------------------------------------------------------
# Producer-side helper — slash command repo extractor
# ---------------------------------------------------------------------------


class ProducerRepoExtractTests(unittest.TestCase):
    def test_extract_repo_from_session_references(self) -> None:
        from types import SimpleNamespace

        from yule_orchestrator.discord.commands import (
            _extract_repo_from_session,
        )

        session = SimpleNamespace(
            references_user=(
                "https://github.com/yule-studio/naver-search-clone.git",
            ),
            extra={},
        )
        self.assertEqual(
            _extract_repo_from_session(session, "ignored prompt"),
            "yule-studio/naver-search-clone",
        )

    def test_extract_repo_from_prompt_text(self) -> None:
        from types import SimpleNamespace

        from yule_orchestrator.discord.commands import (
            _extract_repo_from_session,
        )

        session = SimpleNamespace(references_user=(), extra={})
        prompt = (
            "approval_required https://github.com/yule-studio/another-repo "
            "Next.js + NestJS 구현"
        )
        self.assertEqual(
            _extract_repo_from_session(session, prompt),
            "yule-studio/another-repo",
        )

    def test_extract_repo_none_when_no_github_url(self) -> None:
        from types import SimpleNamespace

        from yule_orchestrator.discord.commands import (
            _extract_repo_from_session,
        )

        session = SimpleNamespace(references_user=(), extra={})
        self.assertIsNone(
            _extract_repo_from_session(session, "단순 자연어 요청")
        )


if __name__ == "__main__":
    unittest.main()
