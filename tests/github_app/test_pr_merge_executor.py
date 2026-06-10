"""F16 PR-2 — PRMergeExecutor factory tests (issue #128).

The executor binds a live (or fake) GitHub client to the 5-step gate
and the merge call. These tests pass a hand-rolled fake client so
every gate branch + the merge-disabled / merge-failed / merge-success
paths can be pinned without GitHub.
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.pr_approval import (
    PRMergeProposal,
    PRMergeReplyDispatch,
)
from yule_engineering.github_app.live_client import (
    LiveGithubAppHTTPError,
    LiveGithubAppMergeDisabled,
)
from yule_engineering.github_app.pr_merge_executor import (
    build_pr_merge_executor,
)


def _proposal(**overrides) -> PRMergeProposal:
    base = dict(
        repo="yule-studio/yule-studio-agent",
        pr_number=127,
        pr_title="F15",
        pr_url="https://github.com/yule-studio/yule-studio-agent/pull/127",
        head_sha="abc1234567",
        base_branch="main",
        draft=False,
        mergeable_state="clean",
        summary_md="",
        scope_labels=("docs",),
        risk="LOW",
        check_runs_summary="✅ green",
        branch_protection_summary="🔒 ok",
        body_excerpt="x",
        requested_by="alice",
    )
    base.update(overrides)
    return PRMergeProposal(**base)


def _dispatch(proposal: PRMergeProposal) -> PRMergeReplyDispatch:
    return PRMergeReplyDispatch(
        proposal=proposal,
        approval_job_id="job-1",
        approved_by="alice",
        approved_at="2026-05-13T08:00:00Z",
        source_message_id=2002,
    )


class _FakeClient:
    """Hand-rolled minimum surface for :func:`build_pr_merge_executor`."""

    def __init__(
        self,
        *,
        pr_payload: Mapping[str, Any],
        check_runs: Sequence[Mapping[str, Any]],
        branch_protection: Optional[Mapping[str, Any]] = None,
        protection_raises: Optional[Exception] = None,
        merge_raises: Optional[Exception] = None,
        merge_result: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._pr = pr_payload
        self._runs = check_runs
        self._protection = branch_protection
        self._protection_raises = protection_raises
        self._merge_raises = merge_raises
        self._merge_result = merge_result or {"sha": "def9876543", "merged": True}
        self.merge_calls: list = []

    def get_pull_request(self, *, repo: str, pr_number: int):
        return self._pr

    def list_check_runs(self, *, repo: str, head_sha: str):
        return self._runs

    def get_branch_protection(self, *, repo: str, branch: str):
        if self._protection_raises is not None:
            raise self._protection_raises
        return self._protection

    def merge_pull_request(self, **kwargs):
        if self._merge_raises is not None:
            raise self._merge_raises
        self.merge_calls.append(kwargs)
        return self._merge_result


class SuccessPathTests(unittest.TestCase):
    def test_clean_pr_merges_returns_sha(self) -> None:
        client = _FakeClient(
            pr_payload={
                "head": {"sha": "abc1234567"},
                "mergeable": True,
                "mergeable_state": "clean",
                "draft": False,
            },
            check_runs=[{"conclusion": "success"}, {"conclusion": "success"}],
            branch_protection={
                "required_pull_request_reviews": {
                    "required_approving_review_count": 1,
                },
                "required_status_checks": {"contexts": ["ci"]},
            },
        )
        executor = build_pr_merge_executor(
            client=client, env={"YULE_GITHUB_MERGE_ENABLED": "true"}
        )
        result = executor(_dispatch(_proposal()))
        self.assertEqual(result["merge_sha"], "def9876543")
        self.assertEqual(result["method"], "squash")
        # merge_pull_request was called with the right repo/pr_number.
        self.assertEqual(len(client.merge_calls), 1)
        call = client.merge_calls[0]
        self.assertEqual(call["repo"], "yule-studio/yule-studio-agent")
        self.assertEqual(call["pr_number"], 127)
        self.assertEqual(call["sha"], "abc1234567")


class GateFailureTests(unittest.TestCase):
    def test_draft_pr_blocked_no_merge_call(self) -> None:
        client = _FakeClient(
            pr_payload={
                "head": {"sha": "abc1234567"},
                "mergeable": True,
                "mergeable_state": "clean",
                "draft": True,
            },
            check_runs=[{"conclusion": "success"}],
            branch_protection={
                "required_pull_request_reviews": {
                    "required_approving_review_count": 0
                }
            },
        )
        executor = build_pr_merge_executor(
            client=client, env={"YULE_GITHUB_MERGE_ENABLED": "true"}
        )
        result = executor(_dispatch(_proposal(draft=False)))
        self.assertEqual(result.get("gate_failed_step"), "draft")
        self.assertEqual(len(client.merge_calls), 0)

    def test_red_check_blocks_no_merge_call(self) -> None:
        client = _FakeClient(
            pr_payload={
                "head": {"sha": "abc1234567"},
                "mergeable": True,
                "mergeable_state": "clean",
                "draft": False,
            },
            check_runs=[{"conclusion": "success"}, {"conclusion": "failure"}],
            branch_protection={
                "required_pull_request_reviews": {
                    "required_approving_review_count": 0
                }
            },
        )
        executor = build_pr_merge_executor(
            client=client, env={"YULE_GITHUB_MERGE_ENABLED": "true"}
        )
        result = executor(_dispatch(_proposal()))
        self.assertEqual(result.get("gate_failed_step"), "checks_green")
        self.assertEqual(len(client.merge_calls), 0)

    def test_sha_race_blocks(self) -> None:
        client = _FakeClient(
            pr_payload={
                "head": {"sha": "newsha7654321"},  # changed!
                "mergeable": True,
                "mergeable_state": "clean",
                "draft": False,
            },
            check_runs=[{"conclusion": "success"}],
            branch_protection={
                "required_pull_request_reviews": {
                    "required_approving_review_count": 0
                }
            },
        )
        executor = build_pr_merge_executor(
            client=client, env={"YULE_GITHUB_MERGE_ENABLED": "true"}
        )
        result = executor(_dispatch(_proposal(head_sha="abc1234567")))
        self.assertEqual(result.get("gate_failed_step"), "sha_race")
        self.assertEqual(len(client.merge_calls), 0)

    def test_branch_protection_401_blocks(self) -> None:
        client = _FakeClient(
            pr_payload={
                "head": {"sha": "abc1234567"},
                "mergeable": True,
                "mergeable_state": "clean",
                "draft": False,
            },
            check_runs=[{"conclusion": "success"}],
            protection_raises=LiveGithubAppHTTPError(
                "401 unauthorized", status=401, url="u"
            ),
        )
        executor = build_pr_merge_executor(
            client=client, env={"YULE_GITHUB_MERGE_ENABLED": "true"}
        )
        result = executor(_dispatch(_proposal()))
        self.assertEqual(result.get("gate_failed_step"), "branch_protection")
        self.assertEqual(len(client.merge_calls), 0)


class MergeDisabledTests(unittest.TestCase):
    def test_merge_disabled_env_returns_marker(self) -> None:
        client = _FakeClient(
            pr_payload={
                "head": {"sha": "abc1234567"},
                "mergeable": True,
                "mergeable_state": "clean",
                "draft": False,
            },
            check_runs=[{"conclusion": "success"}],
            branch_protection={
                "required_pull_request_reviews": {
                    "required_approving_review_count": 0
                }
            },
            merge_raises=LiveGithubAppMergeDisabled(
                "disabled", status=503, url="u"
            ),
        )
        executor = build_pr_merge_executor(client=client)
        result = executor(_dispatch(_proposal()))
        self.assertTrue(result.get("merge_disabled"))
        self.assertEqual(result.get("status"), 503)


class MergeFailedTests(unittest.TestCase):
    def test_github_409_conflict_surfaced(self) -> None:
        client = _FakeClient(
            pr_payload={
                "head": {"sha": "abc1234567"},
                "mergeable": True,
                "mergeable_state": "clean",
                "draft": False,
            },
            check_runs=[{"conclusion": "success"}],
            branch_protection={
                "required_pull_request_reviews": {
                    "required_approving_review_count": 0
                }
            },
            merge_raises=LiveGithubAppHTTPError(
                "409 conflict", status=409, url="u"
            ),
        )
        executor = build_pr_merge_executor(
            client=client, env={"YULE_GITHUB_MERGE_ENABLED": "true"}
        )
        result = executor(_dispatch(_proposal()))
        self.assertTrue(result.get("merge_failed"))
        self.assertEqual(result.get("status"), 409)
        self.assertIn("409", result.get("error", ""))


if __name__ == "__main__":
    unittest.main()
