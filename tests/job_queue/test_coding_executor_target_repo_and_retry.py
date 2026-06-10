"""P1-B + P1-C — target repo resolution, idempotent branch reuse,
specific subprocess-phase reasons, task-log render, and the producer's
non-overreach on failed_retryable / terminal rows.

본 모듈은 사용자가 명시한 6+ 케이스 모두 stdlib unittest 가드:

  1. true phantom (row missing) → producer self-heals enqueue
  2. real failed_retryable → producer skip (no infinite fresh-row)
  3. repeated deterministic subprocess failure respects retry / max_attempts
     (no fresh row at attempt=0 each loop)
  4. actual subprocess phase reason is surfaced
     (target_repo_checkout_missing / worktree_provision_failed / etc.)
  5. task-log obsidian render no longer raises
  6. canonical-session-like recovery — failed_retryable row keeps queue
     retry semantics, doesn't generate parallel duplicates
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_engineering.agents.coding.authorization import proposal_from_dict
from yule_engineering.agents.coding.job import (
    STATUS_READY,
    build_coding_job_from_proposal,
)
from yule_engineering.agents.job_queue.coding_execute_dispatcher import (
    JOB_TYPE_CODING_EXECUTE,
    MARKER_STATE_PENDING_RETRY,
    MARKER_STATE_TERMINAL,
    SESSION_EXTRA_DISPATCH_KEY,
    dispatch_ready_coding_jobs,
    validate_coding_dispatch_marker,
)
from yule_engineering.agents.job_queue.coding_executor_live import (
    LocalGitWorktreeProvisioner,
    TargetRepoUnavailableError,
    WorktreeProvisionError,
    _default_repo_root_resolver,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    CodingExecutorWorker,
    JOB_TYPE_CODING_EXECUTE as _JOB_TYPE,
    REASON_TARGET_REPO_MISSING,
    REASON_WORKTREE_FAILED,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


_REPO = "yule-studio/naver-search-clone"
_PROMPT = (
    "repo: https://github.com/yule-studio/naver-search-clone.git\n"
    "목표: 네이버 검색 풀스택 MVP 구현."
)


@dataclass
class _SessionFake:
    session_id: str
    prompt: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


def _proposal_payload(session_id: str) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "user_request": _PROMPT,
        "executor_role": "backend-engineer",
        "review_roles": ["tech-lead"],
        "participant_roles": ["backend-engineer", "tech-lead"],
        "write_scope": [],
        "forbidden_scope": [],
        "reason": "p1-b test",
        "safety_rules": [],
        "approval_required": True,
        "metadata": {},
        "lifecycle_mode": "implementation",
        "research_leads": [],
    }


def _ready_coding_job(session_id: str) -> Dict[str, Any]:
    proposal = proposal_from_dict(_proposal_payload(session_id))
    job = build_coding_job_from_proposal(
        proposal,
        status=STATUS_READY,
        approved_at=datetime.now(tz=timezone.utc),
    )
    payload = dict(job.to_dict())
    metadata = dict(payload.get("metadata") or {})
    metadata.update(
        {
            "issue_number": 1,
            "repo_full_name": _REPO,
            "base_branch": "main",
            "dry_run": False,
            "approval_id": "approval-1",
        }
    )
    payload["metadata"] = metadata
    payload["status"] = STATUS_READY
    return payload


def _ready_session(
    session_id: str, marker: Optional[Mapping[str, Any]] = None
) -> _SessionFake:
    extra: Dict[str, Any] = {
        "coding_proposal": _proposal_payload(session_id),
        "coding_job": _ready_coding_job(session_id),
        "github_work_order_issue": {
            "issue_number": 1,
            "repo": _REPO,
            "created_via": "auto_create",
        },
    }
    if marker is not None:
        extra[SESSION_EXTRA_DISPATCH_KEY] = dict(marker)
    return _SessionFake(session_id=session_id, prompt=_PROMPT, extra=extra)


def _insert_coding_row(
    db_path: Path,
    *,
    job_id: str,
    session_id: str,
    state: JobState,
    result: Optional[Mapping[str, Any]] = None,
    attempt: int = 0,
    max_attempts: int = 3,
) -> None:
    now_ts = datetime.now(tz=timezone.utc).timestamp()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO job_queue
              (job_id, job_type, role, session_id, payload_json,
               result_json, state, priority, attempt, max_attempts,
               available_at, picked_by, picked_until,
               created_at, updated_at)
            VALUES (?, 'coding_execute', '', ?, '{}', ?, ?, 0, ?, ?, ?,
                    NULL, NULL, ?, ?)
            """,
            (
                job_id,
                session_id,
                json.dumps(dict(result or {})),
                state.value,
                attempt,
                max_attempts,
                now_ts,
                now_ts,
                now_ts,
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Case 1 + 2 — phantom vs failed_retryable producer behavior
# ---------------------------------------------------------------------------


class ProducerRespectsQueueSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)
        self.worker = CodingExecutorWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )

    def _drive_once(self, session: _SessionFake):
        store = {session.session_id: session}

        def _update(updated, *, now=None):  # noqa: ANN001
            store[updated.session_id] = _SessionFake(
                session_id=updated.session_id,
                prompt=getattr(updated, "prompt", ""),
                extra=dict(updated.extra),
            )

        return dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=lambda: [store[session.session_id]],
            update_session_fn=_update,
        ), store

    def test_phantom_marker_still_self_heals(self) -> None:
        """case 1: marker.job_id 가 queue 에 없으면 (true phantom) 새 row
        만들어야 한다."""

        session = _ready_session(
            "11917bf1e75d",
            marker={
                "job_id": "phantom-id-never-existed",
                "executor_role": "backend-engineer",
            },
        )
        dispatched, _ = self._drive_once(session)
        self.assertEqual(len(dispatched), 1)
        self.assertTrue(dispatched[0].created)

    def test_failed_retryable_row_is_NOT_re_enqueued(self) -> None:
        """case 2: marker 가 failed_retryable row 를 가리키면 producer 는
        새 row 절대 만들지 말아야 한다 — queue retry semantics 가 책임."""

        _insert_coding_row(
            self.db_path,
            job_id="job-failing",
            session_id="11917bf1e75d",
            state=JobState.FAILED_RETRYABLE,
            result={
                "error": "edit_failed: _SubprocessError: subprocess failed: exit=255"
            },
            attempt=1,
        )
        session = _ready_session(
            "11917bf1e75d",
            marker={
                "job_id": "job-failing",
                "executor_role": "backend-engineer",
            },
        )
        dispatched, _ = self._drive_once(session)
        self.assertEqual(dispatched, ())
        # 큐에 새 row 가 안 생겼다 — 여전히 job-failing 하나만
        counts = self.queue.count_by_type_and_state()
        self.assertEqual(
            counts.get((JOB_TYPE_CODING_EXECUTE, JobState.FAILED_RETRYABLE.value), 0),
            1,
        )

    def test_repeated_ticks_with_failed_retryable_do_not_grow_queue(self) -> None:
        """case 3: 여러 producer tick 동안 failed_retryable 가 계속 있어도
        새 row 가 추가되지 않는다 (attempt counter 우회 차단)."""

        _insert_coding_row(
            self.db_path,
            job_id="job-stuck",
            session_id="11917bf1e75d",
            state=JobState.FAILED_RETRYABLE,
            result={"error": "edit_failed: subprocess exit=255"},
            attempt=2,
        )
        session = _ready_session(
            "11917bf1e75d", marker={"job_id": "job-stuck"}
        )
        for _ in range(5):
            self._drive_once(session)
        counts = self.queue.count_by_type_and_state()
        self.assertEqual(
            counts.get((JOB_TYPE_CODING_EXECUTE, JobState.FAILED_RETRYABLE.value), 0),
            1,
        )

    def test_terminal_row_is_NOT_re_enqueued(self) -> None:
        """SAVED / FAILED_TERMINAL → dispatch 결과로 인정, 새 row 금지."""

        _insert_coding_row(
            self.db_path,
            job_id="job-done",
            session_id="sess-done",
            state=JobState.SAVED,
        )
        session = _ready_session(
            "sess-done", marker={"job_id": "job-done"}
        )
        dispatched, _ = self._drive_once(session)
        self.assertEqual(dispatched, ())


# ---------------------------------------------------------------------------
# Case 4 — phase-specific reason surfacing
# ---------------------------------------------------------------------------


class WorktreePhaseReasonTests(unittest.TestCase):
    """Provisioner 가 base / branch / target_repo specific reason 으로
    실패하면 worker 가 그 token 을 surface 해야 한다 (generic edit_failed
    대신)."""

    def _request(self, **overrides) -> CodingExecuteRequest:
        defaults = dict(
            session_id="s",
            executor_role="backend-engineer",
            user_request="x",
            generated_prompt="x",
            write_scope=(),
            forbidden_scope=(),
            safety_rules=(),
            base_branch="main",
            branch_hint="agent/backend-engineer/issue-1-coding-execute",
            repo_full_name=_REPO,
            issue_number=1,
            dry_run=False,
            metadata={},
        )
        defaults.update(overrides)
        return CodingExecuteRequest(**defaults)

    def test_target_repo_missing_raises_specific_error(self) -> None:
        """case 4 — repo_full_name 의 local checkout 이 없으면
        ``TargetRepoUnavailableError`` (NOT generic subprocess error)."""

        def _no_resolver(_name):
            return None  # 항상 미발견

        provisioner = LocalGitWorktreeProvisioner(
            repo_root="/nonexistent/orchestrator",
            repo_root_resolver=_no_resolver,
            worktree_root="/tmp/yule-test-worktrees",
        )
        with self.assertRaises(TargetRepoUnavailableError):
            provisioner.provision(
                request=self._request(),
                branch="agent/backend-engineer/issue-1-coding-execute",
            )

    def test_default_resolver_uses_orchestrator_when_repo_name_empty(self) -> None:
        path, _ = _default_repo_root_resolver(
            "", orchestrator_repo_root="/my/orch"
        )
        self.assertEqual(path, "/my/orch")

    def test_default_resolver_finds_sibling_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            (parent / "orch").mkdir()
            sibling = parent / "naver-search-clone"
            sibling.mkdir()
            path, searched = _default_repo_root_resolver(
                _REPO, orchestrator_repo_root=str(parent / "orch")
            )
            self.assertIsNotNone(path)
            # macOS tempfile 가 /var → /private/var symlink 를 resolve
            # 하므로 두 경로 모두 같은 directory 를 가리키는지 확인.
            self.assertEqual(Path(path).resolve(), sibling.resolve())
            self.assertTrue(any("naver-search-clone" in s for s in searched))

    def test_default_resolver_returns_none_when_no_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = _default_repo_root_resolver(
                _REPO, orchestrator_repo_root=str(Path(tmp) / "orch")
            )
            self.assertIsNone(path)

    def test_worktree_add_failed_surfaces_specific_reason(self) -> None:
        """case 4 — git worktree add 가 unknown error 로 실패하면
        ``WorktreeProvisionError(reason='worktree_add_failed')``."""

        # Synthetic runner that simulates a base sha success then
        # worktree add failure (generic, non-branch-exists).
        from yule_engineering.agents.job_queue.coding_executor_live import (
            _SubprocessError,
        )

        calls: List[List[str]] = []

        def _runner(cmd, **_kwargs):
            calls.append(list(cmd))
            if "rev-parse" in cmd:
                return type(
                    "R",
                    (),
                    {"stdout": "abc123\n", "stderr": "", "exit_code": 0},
                )()
            if "worktree" in cmd and "add" in cmd:
                raise _SubprocessError(
                    exit_code=128,
                    stdout="",
                    stderr="fatal: invalid reference: zzz",
                )
            return type(
                "R", (), {"stdout": "", "stderr": "", "exit_code": 0}
            )()

        provisioner = LocalGitWorktreeProvisioner(
            repo_root="/tmp/orch",
            runner=_runner,
            repo_root_resolver=lambda _n: "/tmp/orch",  # bypass resolver
            worktree_root="/tmp/yule-test-worktrees",
        )
        try:
            provisioner.provision(
                request=self._request(),
                branch="agent/backend-engineer/issue-1-coding-execute",
            )
            self.fail("expected WorktreeProvisionError")
        except WorktreeProvisionError as exc:
            self.assertEqual(exc.reason, "worktree_add_failed")
            self.assertIn("invalid reference", str(exc))


# ---------------------------------------------------------------------------
# Case E — branch-already-exists idempotent reuse
# ---------------------------------------------------------------------------


class BranchAlreadyExistsIdempotencyTests(unittest.TestCase):
    def test_branch_already_exists_retries_without_b_flag(self) -> None:
        """'branch already exists' 류 에러는 ``git worktree add <path>
        <branch>`` (no ``-b``) 로 자동 재시도 — 영원히 같은 에러 반복 안 함."""

        from yule_engineering.agents.job_queue.coding_executor_live import (
            _SubprocessError,
        )

        calls: List[List[str]] = []

        def _runner(cmd, **_kwargs):
            calls.append(list(cmd))
            if "rev-parse" in cmd:
                return type(
                    "R",
                    (),
                    {"stdout": "deadbeef\n", "stderr": "", "exit_code": 0},
                )()
            if "worktree" in cmd and "add" in cmd:
                # 첫 번째: -b 형식 → "already exists"
                if "-b" in cmd:
                    raise _SubprocessError(
                        exit_code=128,
                        stdout="",
                        stderr=(
                            "fatal: a branch named "
                            "'agent/backend-engineer/issue-1-coding-execute' "
                            "already exists"
                        ),
                    )
                # 두 번째: -b 없이 → 성공
                return type(
                    "R", (), {"stdout": "", "stderr": "", "exit_code": 0}
                )()
            return type(
                "R", (), {"stdout": "", "stderr": "", "exit_code": 0}
            )()

        provisioner = LocalGitWorktreeProvisioner(
            repo_root="/tmp/orch",
            runner=_runner,
            repo_root_resolver=lambda _n: "/tmp/orch",
            worktree_root="/tmp/yule-test-worktrees-bx",
        )
        request = CodingExecuteRequest(
            session_id="s",
            executor_role="backend-engineer",
            user_request="x",
            generated_prompt="x",
            write_scope=(),
            forbidden_scope=(),
            safety_rules=(),
            base_branch="main",
            branch_hint="agent/backend-engineer/issue-1-coding-execute",
            repo_full_name=_REPO,
            dry_run=False,
        )
        ctx = provisioner.provision(
            request=request,
            branch="agent/backend-engineer/issue-1-coding-execute",
        )
        self.assertEqual(ctx.base_commit_sha, "deadbeef")
        # 두 번 worktree add 가 불렸고 두 번째는 -b 없음
        adds = [c for c in calls if "worktree" in c and "add" in c]
        self.assertEqual(len(adds), 2)
        self.assertIn("-b", adds[0])
        self.assertNotIn("-b", adds[1])


# ---------------------------------------------------------------------------
# Case F — task-log obsidian render
# ---------------------------------------------------------------------------


class TaskLogObsidianRenderTests(unittest.TestCase):
    def test_task_log_render_uses_body_metadata(self) -> None:
        from yule_engineering.agents.job_queue.obsidian_writer_worker import (
            NOTE_KIND_TASK_LOG,
            ObsidianWriteRequest,
            default_render_fn,
        )

        request = ObsidianWriteRequest(
            session_id="11917bf1e75d",
            note_kind=NOTE_KIND_TASK_LOG,
            title="coding-executor — failed (backend-engineer)",
            metadata={
                "kind": "coding_execute_progress",
                "body": (
                    "# Coding execute — failed\n\n"
                    "reason: worktree_provision_failed:worktree_add_failed\n"
                ),
                "rendered_markdown": "# duplicate\n",
                "reason": "test",
            },
        )
        # No ObsidianRenderError — used to raise
        # "default_render_fn does not support note_kind='task-log'"
        note = default_render_fn(request)
        self.assertIn("Coding execute", note.content)
        self.assertTrue(note.path)


if __name__ == "__main__":
    unittest.main()
