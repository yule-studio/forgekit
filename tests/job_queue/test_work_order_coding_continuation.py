"""work_order_coding_continuation helper 회귀 — P0-S anchor → coding_job=ready.

contract:
  - coding_proposal 이 있으면 coding_job=ready 로 promote 되고 metadata
    에 anchor 정보 (issue_number/issue_url/repo_full_name/base_branch/
    dry_run/approval_id) 가 stamp 된다.
  - progress markers (issue_created → coding_dispatch_queued) 가 누적
    stamp.
  - 같은 anchor 로 재호출 시 noop (idempotent).
  - coding_proposal 이 없으면 promote 안 함 + audit reason 만 남김.
  - proposal build 실패 시 NoopReason build_failed.
  - stamp_progress_marker 가 추가 marker 를 idempotent 하게 누적.
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.coding.job import STATUS_READY
from yule_engineering.agents.job_queue.work_order_coding_continuation import (
    CONTINUATION_NOOP_ALREADY_READY,
    CONTINUATION_NOOP_BUILD_FAILED,
    CONTINUATION_NOOP_NO_PROPOSAL,
    PROGRESS_CODING_DISPATCH_QUEUED,
    PROGRESS_CODING_JOB_READY,
    PROGRESS_CODING_IN_PROGRESS,
    PROGRESS_DRAFT_PR_OPENED,
    PROGRESS_ISSUE_CREATED,
    SESSION_EXTRA_CODING_JOB_KEY,
    SESSION_EXTRA_PROGRESS_KEY,
    promote_session_to_coding_ready,
    stamp_progress_marker,
)


def _sample_proposal() -> Mapping[str, Any]:
    return {
        "session_id": "sess-x",
        "user_request": "회원가입 구현",
        "executor_role": "backend-engineer",
        "review_roles": ["tech-lead", "qa-engineer"],
        "participant_roles": ["backend-engineer", "tech-lead", "qa-engineer"],
        "write_scope": ["src/api/auth"],
        "forbidden_scope": ["secret", ".env"],
        "safety_rules": ["no force push"],
        "reason": "auth 흐름은 backend-engineer 책임",
        "approval_required": True,
        "metadata": {},
        "lifecycle_mode": "implementation",
    }


def _anchor(issue_number: int = 77) -> Mapping[str, Any]:
    return {
        "issue_number": issue_number,
        "html_url": f"https://github.com/yule-studio/naver-search-clone/issues/{issue_number}",
        "title": "[기능] 회원가입",
        "created_via": "auto_create",
        "approval_id": "a1",
        "approved_by": "masterway",
        "approved_at": "2026-05-15T11:00:00+00:00",
        "dry_run": False,
        "repo": "yule-studio/naver-search-clone",
    }


class PromoteTests(unittest.TestCase):
    def test_promote_happy_path_stamps_metadata_and_progress(self) -> None:
        extra = {"coding_proposal": _sample_proposal()}
        outcome = promote_session_to_coding_ready(
            session_extra=extra,
            anchor=_anchor(),
            repo="yule-studio/naver-search-clone",
            base_branch="main",
            dry_run=False,
            approval_id="a1",
            approved_by="masterway",
            approved_at="2026-05-15T11:00:00+00:00",
        )
        self.assertTrue(outcome.promoted)
        assert outcome.coding_job is not None
        self.assertEqual(outcome.coding_job["status"], STATUS_READY)
        meta = outcome.coding_job["metadata"]
        self.assertEqual(meta["issue_number"], 77)
        self.assertEqual(meta["repo_full_name"], "yule-studio/naver-search-clone")
        self.assertEqual(meta["base_branch"], "main")
        self.assertEqual(meta["approval_id"], "a1")
        self.assertIn("github_work_order_anchor", meta)
        # progress markers 누적
        assert outcome.new_extra is not None
        progress = outcome.new_extra[SESSION_EXTRA_PROGRESS_KEY]
        self.assertIn(PROGRESS_ISSUE_CREATED, progress)
        # P0-Y: promote_session_to_coding_ready 는 coding_job_ready 만
        # stamp 하고 dispatch_queued 는 실제 enqueue (dispatcher) 가 stamp.
        self.assertIn(PROGRESS_CODING_JOB_READY, progress)
        self.assertNotIn(PROGRESS_CODING_DISPATCH_QUEUED, progress)
        self.assertEqual(
            progress[PROGRESS_ISSUE_CREATED]["detail"]["issue_number"], 77
        )

    def test_idempotent_same_anchor(self) -> None:
        extra = {"coding_proposal": _sample_proposal()}
        first = promote_session_to_coding_ready(
            session_extra=extra,
            anchor=_anchor(),
            repo="r",
            base_branch="main",
            dry_run=False,
        )
        assert first.new_extra is not None
        second = promote_session_to_coding_ready(
            session_extra=first.new_extra,
            anchor=_anchor(),
            repo="r",
            base_branch="main",
            dry_run=False,
        )
        self.assertFalse(second.promoted)
        self.assertEqual(second.noop_reason, CONTINUATION_NOOP_ALREADY_READY)

    def test_different_anchor_repromotes(self) -> None:
        extra = {"coding_proposal": _sample_proposal()}
        first = promote_session_to_coding_ready(
            session_extra=extra,
            anchor=_anchor(issue_number=10),
            repo="r",
            base_branch="main",
            dry_run=False,
        )
        assert first.new_extra is not None
        # 다른 issue 번호의 anchor 가 들어오면 promote 가 다시 일어남
        second = promote_session_to_coding_ready(
            session_extra=first.new_extra,
            anchor=_anchor(issue_number=11),
            repo="r",
            base_branch="main",
            dry_run=False,
        )
        self.assertTrue(second.promoted)
        assert second.coding_job is not None
        self.assertEqual(second.coding_job["metadata"]["issue_number"], 11)

    def test_no_proposal_returns_noop(self) -> None:
        outcome = promote_session_to_coding_ready(
            session_extra={},
            anchor=_anchor(),
            repo="r",
            base_branch="main",
            dry_run=False,
        )
        self.assertFalse(outcome.promoted)
        self.assertEqual(outcome.noop_reason, CONTINUATION_NOOP_NO_PROPOSAL)
        # 하지만 issue_created marker 는 여전히 stamp (anchor 자체는 존재)
        assert outcome.new_extra is not None
        self.assertIn(
            PROGRESS_ISSUE_CREATED,
            outcome.new_extra[SESSION_EXTRA_PROGRESS_KEY],
        )

    def test_research_only_proposal_build_failed(self) -> None:
        proposal = dict(_sample_proposal())
        proposal["lifecycle_mode"] = "research_only"
        proposal["executor_role"] = ""
        outcome = promote_session_to_coding_ready(
            session_extra={"coding_proposal": proposal},
            anchor=_anchor(),
            repo="r",
            base_branch="main",
            dry_run=False,
        )
        self.assertFalse(outcome.promoted)
        self.assertEqual(outcome.noop_reason, CONTINUATION_NOOP_BUILD_FAILED)


class ProgressMarkerTests(unittest.TestCase):
    def test_stamp_progress_marker_adds_and_does_not_overwrite_timestamp(self) -> None:
        extra1 = stamp_progress_marker(
            session_extra={},
            marker=PROGRESS_CODING_IN_PROGRESS,
            at="2026-05-15T12:00:00+00:00",
            detail={"executor_role": "backend-engineer"},
        )
        self.assertIn(
            PROGRESS_CODING_IN_PROGRESS,
            extra1[SESSION_EXTRA_PROGRESS_KEY],
        )
        first_at = extra1[SESSION_EXTRA_PROGRESS_KEY][PROGRESS_CODING_IN_PROGRESS]["at"]
        # 같은 marker 를 다시 stamp 하면 timestamp 는 유지
        extra2 = stamp_progress_marker(
            session_extra=extra1,
            marker=PROGRESS_CODING_IN_PROGRESS,
            at="2026-05-15T13:00:00+00:00",
            detail={"new_field": "x"},
        )
        self.assertEqual(
            extra2[SESSION_EXTRA_PROGRESS_KEY][PROGRESS_CODING_IN_PROGRESS]["at"],
            first_at,
        )

    def test_stamp_progress_marker_does_not_mutate_input(self) -> None:
        original = {"unrelated": True}
        stamp_progress_marker(
            session_extra=original, marker=PROGRESS_DRAFT_PR_OPENED
        )
        self.assertEqual(original, {"unrelated": True})


if __name__ == "__main__":
    unittest.main()
