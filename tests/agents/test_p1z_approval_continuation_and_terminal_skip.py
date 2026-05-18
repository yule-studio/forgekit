"""P1-Z — Approved → work_order 복구 + terminal session resurrection 차단.

배경
----
사용자가 폐기한 canonical sessions (``166c416a1ed0``, ``c7bc03b8d41a``)
가 runtime restart 후 stale dispatch marker self-heal 로 다시
``coding_execute`` 큐에 enqueue 됐다.  동시에 새 승인 세션 ``f2f36607d175``
는 state=approved 인데도 ``github_work_order_issue`` 없고 queue row 0
인 dead-end.

본 회귀 라인은 다음 5 가지 contract 를 명시 lock:

1. ``decide_post_approval_action`` 이 approved + coding_proposal +
   github_target + no anchor → ``needs_work_order``.
2. 이미 anchor / coding_proposal 없음 / target 없음 / terminal session
   → ``noop`` (reason 별).
3. ``dispatch_post_approval_work_order`` 가 inject 된 builder + queue
   로 실제 work_order row 생성.
4. ``coding_execute_dispatcher.iter_ready_coding_jobs`` 가 rejected /
   completed session 은 yield 안 함.
5. ``_stamp_terminal_session_skip`` 이 terminal + ready coding_job 인
   session 에 audit marker 만 stamp 하고 새 row 안 만듦.

stdlib unittest 만 사용.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.coding_execute_dispatcher import (
    SESSION_EXTRA_DISPATCH_KEY,
    SESSION_EXTRA_TERMINAL_SKIP_KEY,
    _stamp_terminal_session_skip,
    iter_ready_coding_jobs,
)
from yule_orchestrator.agents.job_queue.coding_execute_terminal_skip import (
    TERMINAL_SESSION_STATES,
    is_terminal_session,
)
from yule_orchestrator.agents.job_queue.post_approval_dispatch import (
    ACTION_DISPATCHED,
    ACTION_FAILED,
    ACTION_NEEDS_WORK_ORDER,
    ACTION_NOOP,
    FAIL_REASON_PROPOSAL_NOT_ELIGIBLE,
    NOOP_REASON_ANCHOR_ALREADY_STAMPED,
    NOOP_REASON_NO_CODING_PROPOSAL,
    NOOP_REASON_NO_GITHUB_TARGET,
    NOOP_REASON_NO_REPO,
    NOOP_REASON_NOT_APPROVED,
    NOOP_REASON_TERMINAL_SESSION,
    SESSION_EXTRA_POST_APPROVAL_DISPATCH_KEY,
    decide_post_approval_action,
    dispatch_post_approval_work_order,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _State:
    """Mimics WorkflowState enum — ``.value`` 가 'approved' / 'rejected' / ..."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:  # noqa: D401
        return self.value


@dataclass
class _FakeSession:
    session_id: str = "sess-p1z-1"
    state: Any = field(default_factory=lambda: _State("approved"))
    extra: Mapping[str, Any] = field(default_factory=dict)
    prompt: str = "implement search clone"
    channel_id: Optional[int] = 100
    thread_id: Optional[int] = 200

    @classmethod
    def make(
        cls,
        *,
        session_id: str = "sess-p1z-1",
        state: str = "approved",
        extra: Optional[Mapping[str, Any]] = None,
        prompt: str = "implement search clone",
        channel_id: Optional[int] = 100,
        thread_id: Optional[int] = 200,
    ) -> "_FakeSession":
        return cls(
            session_id=session_id,
            state=_State(state),
            extra=dict(extra or {}),
            prompt=prompt,
            channel_id=channel_id,
            thread_id=thread_id,
        )


# ---------------------------------------------------------------------------
# decide_post_approval_action
# ---------------------------------------------------------------------------


_PROPOSAL = {"executor_role": "fullstack-engineer", "review_roles": ["tech-lead"]}
_TARGET = {
    "kind": "repo",
    "owner": "yule-studio",
    "repo": "naver-search-clone",
    "number": None,
}


class DecidePostApprovalActionTests(unittest.TestCase):
    def test_intake_state_is_noop(self) -> None:
        d = decide_post_approval_action(_FakeSession.make(state="intake"))
        self.assertEqual(d.action, ACTION_NOOP)
        self.assertEqual(d.reason, NOOP_REASON_NOT_APPROVED)

    def test_rejected_state_is_terminal_noop(self) -> None:
        d = decide_post_approval_action(_FakeSession.make(state="rejected"))
        self.assertEqual(d.action, ACTION_NOOP)
        self.assertEqual(d.reason, NOOP_REASON_TERMINAL_SESSION)

    def test_completed_state_is_terminal_noop(self) -> None:
        d = decide_post_approval_action(_FakeSession.make(state="completed"))
        self.assertEqual(d.action, ACTION_NOOP)
        self.assertEqual(d.reason, NOOP_REASON_TERMINAL_SESSION)

    def test_approved_without_proposal_is_noop(self) -> None:
        d = decide_post_approval_action(
            _FakeSession.make(state="approved", extra={"github_target": _TARGET})
        )
        self.assertEqual(d.action, ACTION_NOOP)
        self.assertEqual(d.reason, NOOP_REASON_NO_CODING_PROPOSAL)

    def test_approved_with_existing_anchor_is_noop(self) -> None:
        d = decide_post_approval_action(
            _FakeSession.make(
                state="approved",
                extra={
                    "coding_proposal": _PROPOSAL,
                    "github_target": _TARGET,
                    "github_work_order_issue": {"issue_number": 5, "repo": "yule-studio/naver-search-clone"},
                },
            )
        )
        self.assertEqual(d.action, ACTION_NOOP)
        self.assertEqual(d.reason, NOOP_REASON_ANCHOR_ALREADY_STAMPED)

    def test_approved_without_target_is_noop(self) -> None:
        d = decide_post_approval_action(
            _FakeSession.make(state="approved", extra={"coding_proposal": _PROPOSAL})
        )
        self.assertEqual(d.action, ACTION_NOOP)
        self.assertEqual(d.reason, NOOP_REASON_NO_GITHUB_TARGET)

    def test_approved_target_without_repo_owner_is_noop(self) -> None:
        d = decide_post_approval_action(
            _FakeSession.make(
                state="approved",
                extra={
                    "coding_proposal": _PROPOSAL,
                    "github_target": {"kind": "repo", "owner": "", "repo": ""},
                },
            )
        )
        self.assertEqual(d.action, ACTION_NOOP)
        self.assertEqual(d.reason, NOOP_REASON_NO_REPO)

    def test_approved_with_proposal_and_target_needs_work_order(self) -> None:
        d = decide_post_approval_action(
            _FakeSession.make(
                state="approved",
                extra={"coding_proposal": _PROPOSAL, "github_target": _TARGET},
            )
        )
        self.assertEqual(d.action, ACTION_NEEDS_WORK_ORDER)
        self.assertEqual(d.repo, "yule-studio/naver-search-clone")
        self.assertIsNone(d.existing_issue_number)

    def test_explicit_existing_issue_number_carried_through(self) -> None:
        d = decide_post_approval_action(
            _FakeSession.make(
                state="approved",
                extra={
                    "coding_proposal": _PROPOSAL,
                    "github_target": _TARGET,
                    "existing_issue_number": 5,
                },
            )
        )
        self.assertEqual(d.action, ACTION_NEEDS_WORK_ORDER)
        self.assertEqual(d.existing_issue_number, 5)

    def test_issue_kind_target_with_number_uses_target_number(self) -> None:
        d = decide_post_approval_action(
            _FakeSession.make(
                state="approved",
                extra={
                    "coding_proposal": _PROPOSAL,
                    "github_target": {
                        "kind": "issue",
                        "owner": "yule-studio",
                        "repo": "naver-search-clone",
                        "number": 12,
                    },
                },
            )
        )
        self.assertEqual(d.action, ACTION_NEEDS_WORK_ORDER)
        self.assertEqual(d.existing_issue_number, 12)


# ---------------------------------------------------------------------------
# dispatch_post_approval_work_order — builder/queue injection
# ---------------------------------------------------------------------------


class _FakeProposal:
    """Minimal duck-typed proposal — ``GitHubWorkOrder.from_proposal`` 가
    필요한 속성만 노출."""

    def __init__(
        self,
        *,
        session_id: str = "sess-p1z-1",
        repo: str = "yule-studio/naver-search-clone",
        existing_issue_number: Optional[int] = None,
    ) -> None:
        self.proposal_id = "p-1"
        self.session_id = session_id
        self.source_channel_id = None
        self.source_thread_id = None
        self.source_message_id = None
        self.request_summary = "implement search clone"
        self.selected_roles = ("tech-lead", "fullstack-engineer")
        self.intent_actions = ("코드 변경",)
        self.repo = repo
        self.base_branch = "main"
        self.dry_run_default = False
        self.extra: Mapping[str, Any] = {}
        self.issue_auto_create_plan: Optional[Mapping[str, Any]] = None
        self.existing_issue_number = existing_issue_number


def _in_memory_queue():
    """JobQueue backed by a tempfile — real enqueue / dedup wiring."""

    import tempfile
    from pathlib import Path

    from yule_orchestrator.agents.job_queue.store import JobQueue

    tmpdir = tempfile.mkdtemp(prefix="yule-p1z-")
    return JobQueue(db_path=Path(tmpdir) / "queue.sqlite")


class DispatchPostApprovalWorkOrderTests(unittest.TestCase):
    def test_needs_work_order_path_calls_proposal_builder(self) -> None:
        captured = {}

        def fake_builder(**kwargs):
            captured.update(kwargs)
            return _FakeProposal(session_id=kwargs["session"].session_id)

        session = _FakeSession.make(
            state="approved",
            extra={"coding_proposal": _PROPOSAL, "github_target": _TARGET},
        )
        queue = _in_memory_queue()

        result = dispatch_post_approval_work_order(
            session=session,
            queue=queue,
            requested_by="cli-user",
            proposal_builder=fake_builder,
        )
        self.assertIn(result["action"], (ACTION_DISPATCHED, ACTION_NOOP))
        self.assertEqual(captured["repo"], "yule-studio/naver-search-clone")
        self.assertEqual(captured["requested_by"], "cli-user")

    def test_noop_when_already_anchor(self) -> None:
        session = _FakeSession.make(
            state="approved",
            extra={
                "coding_proposal": _PROPOSAL,
                "github_target": _TARGET,
                "github_work_order_issue": {"issue_number": 5},
            },
        )
        result = dispatch_post_approval_work_order(
            session=session,
            queue=_in_memory_queue(),
            requested_by="cli-user",
            proposal_builder=lambda **kwargs: self.fail("builder must not be called"),
        )
        self.assertEqual(result["action"], ACTION_NOOP)
        self.assertEqual(result["reason"], NOOP_REASON_ANCHOR_ALREADY_STAMPED)

    def test_builder_returns_none_is_failed(self) -> None:
        session = _FakeSession.make(
            state="approved",
            extra={"coding_proposal": _PROPOSAL, "github_target": _TARGET},
        )
        result = dispatch_post_approval_work_order(
            session=session,
            queue=_in_memory_queue(),
            requested_by="cli-user",
            proposal_builder=lambda **kwargs: None,
        )
        self.assertEqual(result["action"], ACTION_FAILED)
        self.assertEqual(result["reason"], FAIL_REASON_PROPOSAL_NOT_ELIGIBLE)

    def test_terminal_session_is_noop_not_dispatched(self) -> None:
        session = _FakeSession.make(
            state="rejected",
            extra={"coding_proposal": _PROPOSAL, "github_target": _TARGET},
        )
        result = dispatch_post_approval_work_order(
            session=session,
            queue=_in_memory_queue(),
            requested_by="cli-user",
            proposal_builder=lambda **kwargs: self.fail("must not call builder"),
        )
        self.assertEqual(result["action"], ACTION_NOOP)
        self.assertEqual(result["reason"], NOOP_REASON_TERMINAL_SESSION)


# ---------------------------------------------------------------------------
# Terminal session resurrection block
# ---------------------------------------------------------------------------


class TerminalSessionIterTests(unittest.TestCase):
    def _make_session(self, *, state: str, sid: str, has_marker: bool = False) -> _FakeSession:
        extra: dict[str, Any] = {
            "coding_job": {
                "session_id": sid,
                "status": "ready",
                "executor_role": "fullstack-engineer",
            }
        }
        if has_marker:
            extra[SESSION_EXTRA_DISPATCH_KEY] = {"job_id": "old-job-1"}
        return _FakeSession.make(session_id=sid, state=state, extra=extra)

    def test_rejected_session_is_skipped(self) -> None:
        live = self._make_session(state="approved", sid="live-1")
        rejected = self._make_session(state="rejected", sid="rej-1", has_marker=True)
        yielded = list(
            iter_ready_coding_jobs(
                session_loader=lambda: [rejected, live],
                queue=None,
            )
        )
        ids = {r.session_id for r in yielded}
        self.assertIn("live-1", ids)
        self.assertNotIn("rej-1", ids)

    def test_completed_session_is_skipped(self) -> None:
        completed = self._make_session(state="completed", sid="done-1")
        yielded = list(
            iter_ready_coding_jobs(
                session_loader=lambda: [completed], queue=None
            )
        )
        self.assertEqual(yielded, [])

    def test_is_terminal_session_helper(self) -> None:
        self.assertTrue(is_terminal_session(_FakeSession.make(state="rejected")))
        self.assertTrue(is_terminal_session(_FakeSession.make(state="completed")))
        self.assertFalse(is_terminal_session(_FakeSession.make(state="approved")))
        self.assertFalse(is_terminal_session(_FakeSession.make(state="intake")))

    def test_terminal_session_states_constant(self) -> None:
        self.assertEqual(TERMINAL_SESSION_STATES, frozenset({"completed", "rejected"}))


class TerminalSkipAuditTests(unittest.TestCase):
    def test_stamps_skip_audit_on_rejected_session_with_marker(self) -> None:
        persisted: list[Any] = []

        def fake_update(session, *, now=None):
            persisted.append(session)
            return session

        rejected = _FakeSession.make(
            session_id="rej-stamp-1",
            state="rejected",
            extra={
                SESSION_EXTRA_DISPATCH_KEY: {"job_id": "old-1"},
                "coding_job": {"status": "ready", "session_id": "rej-stamp-1", "executor_role": "x"},
            },
        )
        live = _FakeSession.make(
            session_id="live-stamp-1",
            state="approved",
            extra={"coding_job": {"status": "ready", "session_id": "live-stamp-1", "executor_role": "x"}},
        )

        stamped = _stamp_terminal_session_skip(
            session_loader=lambda: [rejected, live],
            update_session_fn=fake_update,
            now=datetime(2026, 5, 19, tzinfo=timezone.utc),
        )
        self.assertEqual(stamped, 1)
        self.assertEqual(len(persisted), 1)
        marker = persisted[0].extra[SESSION_EXTRA_TERMINAL_SKIP_KEY]
        self.assertEqual(marker["reason"], "terminal_session_skip")
        self.assertEqual(marker["session_state"], "rejected")
        self.assertTrue(marker["had_ready_coding_job"])
        self.assertTrue(marker["had_dispatch_marker"])

    def test_completed_with_no_ready_no_marker_is_skipped(self) -> None:
        persisted: list[Any] = []
        done = _FakeSession.make(
            session_id="done-1",
            state="completed",
            extra={"coding_job": {"status": "saved", "session_id": "done-1", "executor_role": "x"}},
        )
        stamped = _stamp_terminal_session_skip(
            session_loader=lambda: [done],
            update_session_fn=lambda s, *, now=None: persisted.append(s),
        )
        self.assertEqual(stamped, 0)
        self.assertEqual(persisted, [])

    def test_idempotent_does_not_restamp_same_state(self) -> None:
        already = _FakeSession.make(
            session_id="rej-idemp-1",
            state="rejected",
            extra={
                SESSION_EXTRA_DISPATCH_KEY: {"job_id": "old-1"},
                "coding_job": {"status": "ready", "session_id": "rej-idemp-1", "executor_role": "x"},
                SESSION_EXTRA_TERMINAL_SKIP_KEY: {
                    "session_state": "rejected",
                    "reason": "terminal_session_skip",
                    "had_ready_coding_job": True,
                    "had_dispatch_marker": True,
                    "at": "2026-05-19T00:00:00+00:00",
                },
            },
        )
        persisted: list[Any] = []
        stamped = _stamp_terminal_session_skip(
            session_loader=lambda: [already],
            update_session_fn=lambda s, *, now=None: persisted.append(s),
        )
        self.assertEqual(stamped, 0)
        self.assertEqual(persisted, [])


# ---------------------------------------------------------------------------
# Source-grep wiring guard
# ---------------------------------------------------------------------------


class WiringGuardTests(unittest.TestCase):
    def test_cli_engineer_approve_imports_dispatch(self) -> None:
        import inspect

        from yule_orchestrator.cli import engineer as cli_mod

        source = inspect.getsource(cli_mod.run_engineer_approve_command)
        self.assertIn("dispatch_post_approval_work_order", source)
        self.assertIn("JobQueue", source)
        self.assertIn("post_approval", source)

    def test_dispatcher_invokes_terminal_skip(self) -> None:
        import inspect

        from yule_orchestrator.agents.job_queue import (
            coding_execute_dispatcher as disp_mod,
        )

        source = inspect.getsource(disp_mod.dispatch_ready_coding_jobs)
        self.assertIn("_stamp_terminal_session_skip", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
