"""P1-R-2 — `/engineer_intake` 명시 governance 옵션 우선순위.

1.  explicit work_mode=autonomous_merge 가 prompt approval_required 보다 우선
2.  explicit work_mode=approval_required 가 prompt autonomous_merge 보다 우선
3.  explicit branch_strategy=git_flow 영속
4.  explicit release_strategy=tagged_release 영속
5.  explicit issue_policy=issue_required 영속
6.  explicit 옵션 생략 시 prompt token fallback 동작
7.  explicit 옵션 + prompt 둘 다 없으면 default governance 값 영속
8.  intake receipt 끝에 governance contract block 추가됨
9.  mode_decided_by 가 slash_option_explicit / user_explicit / gateway_inferred 구분
10. legacy intake (explicit 옵션 전부 없음) regression 없음
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from typing import Any, Mapping
from unittest.mock import MagicMock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.lifecycle.session_mode import (
    EXTRA_BRANCH_STRATEGY,
    EXTRA_DECIDED_AT,
    EXTRA_DECIDED_BY,
    EXTRA_ISSUE_POLICY,
    EXTRA_RELEASE_STRATEGY,
    EXTRA_SCOPE,
    EXTRA_TOPOLOGY,
    EXTRA_WORK_MODE,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
)
from yule_orchestrator.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    save_session,
)
from yule_orchestrator.discord.commands import (
    _append_governance_block_to_result,
    _persist_intake_mode_and_backlog,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _seed(session_id: str, *, prompt: str = "p") -> WorkflowSession:
    s = WorkflowSession(
        session_id=session_id,
        prompt=prompt,
        task_type="coding_execute",
        state=WorkflowState.IN_PROGRESS,
        created_at=_now(),
        updated_at=_now(),
        executor_role="backend-engineer",
        extra={},
    )
    save_session(s)
    return s


class _CacheTmpFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["YULE_AGENT_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        os.environ.pop("YULE_AGENT_CACHE_DIR", None)


_FULLSTACK_PROMPT_AUTO = (
    "autonomous_merge, single_repo, full_stack_single_repo "
    "네이버 검색 풀스택 MVP 구현해줘 "
    "https://github.com/yule-studio/naver-search-clone"
)
_FULLSTACK_PROMPT_APPROVAL = (
    "approval_required, single_repo, full_stack_single_repo "
    "네이버 검색 풀스택 MVP 구현해줘 "
    "https://github.com/yule-studio/naver-search-clone"
)
_FULLSTACK_PROMPT_NO_TOKEN = (
    "네이버 검색 풀스택 MVP 구현해줘 "
    "https://github.com/yule-studio/naver-search-clone"
)


# ---------------------------------------------------------------------------
# 1, 2 — slash option 이 prompt 토큰보다 우선
# ---------------------------------------------------------------------------


class SlashOptionPriorityTests(_CacheTmpFixture):
    def test_slash_autonomous_overrides_prompt_approval(self) -> None:
        s = _seed("s-1", prompt=_FULLSTACK_PROMPT_APPROVAL)
        summary = _persist_intake_mode_and_backlog(
            session=s,
            prompt_text=_FULLSTACK_PROMPT_APPROVAL,
            explicit_work_mode=WORK_MODE_AUTONOMOUS,
        )
        self.assertIsNotNone(summary)
        self.assertEqual(summary["work_mode"], WORK_MODE_AUTONOMOUS)
        self.assertEqual(summary["mode_decided_by"], "slash_option_explicit")
        fresh = load_session("s-1")
        self.assertEqual(fresh.extra[EXTRA_WORK_MODE], WORK_MODE_AUTONOMOUS)
        self.assertEqual(
            fresh.extra[EXTRA_DECIDED_BY], "slash_option_explicit"
        )

    def test_slash_approval_overrides_prompt_autonomous(self) -> None:
        s = _seed("s-2", prompt=_FULLSTACK_PROMPT_AUTO)
        summary = _persist_intake_mode_and_backlog(
            session=s,
            prompt_text=_FULLSTACK_PROMPT_AUTO,
            explicit_work_mode=WORK_MODE_APPROVAL,
        )
        self.assertEqual(summary["work_mode"], WORK_MODE_APPROVAL)
        self.assertEqual(summary["mode_decided_by"], "slash_option_explicit")


# ---------------------------------------------------------------------------
# 3, 4, 5 — explicit branch_strategy / release_strategy / issue_policy 영속
# ---------------------------------------------------------------------------


class ExplicitGovernanceKeysPersistTests(_CacheTmpFixture):
    def test_explicit_branch_strategy_persists(self) -> None:
        s = _seed("s-3", prompt=_FULLSTACK_PROMPT_NO_TOKEN)
        summary = _persist_intake_mode_and_backlog(
            session=s,
            prompt_text=_FULLSTACK_PROMPT_NO_TOKEN,
            explicit_branch_strategy="git_flow",
        )
        self.assertEqual(summary["branch_strategy"], "git_flow")
        fresh = load_session("s-3")
        self.assertEqual(fresh.extra[EXTRA_BRANCH_STRATEGY], "git_flow")

    def test_explicit_release_strategy_persists(self) -> None:
        s = _seed("s-4", prompt=_FULLSTACK_PROMPT_NO_TOKEN)
        summary = _persist_intake_mode_and_backlog(
            session=s,
            prompt_text=_FULLSTACK_PROMPT_NO_TOKEN,
            explicit_release_strategy="tagged_release",
        )
        self.assertEqual(summary["release_strategy"], "tagged_release")
        fresh = load_session("s-4")
        self.assertEqual(
            fresh.extra[EXTRA_RELEASE_STRATEGY], "tagged_release"
        )

    def test_explicit_issue_policy_persists(self) -> None:
        s = _seed("s-5", prompt=_FULLSTACK_PROMPT_NO_TOKEN)
        summary = _persist_intake_mode_and_backlog(
            session=s,
            prompt_text=_FULLSTACK_PROMPT_NO_TOKEN,
            explicit_issue_policy="issue_required",
        )
        self.assertEqual(summary["issue_policy"], "issue_required")
        fresh = load_session("s-5")
        self.assertEqual(
            fresh.extra[EXTRA_ISSUE_POLICY], "issue_required"
        )

    def test_explicit_topology_and_scope_persist(self) -> None:
        s = _seed("s-5b", prompt=_FULLSTACK_PROMPT_NO_TOKEN)
        summary = _persist_intake_mode_and_backlog(
            session=s,
            prompt_text=_FULLSTACK_PROMPT_NO_TOKEN,
            explicit_topology="single_repo",
            explicit_scope="full_stack_single_repo",
        )
        self.assertEqual(summary["topology"], "single_repo")
        self.assertEqual(summary["scope"], "full_stack_single_repo")


# ---------------------------------------------------------------------------
# 6 — prompt token fallback
# ---------------------------------------------------------------------------


class PromptTokenFallbackTests(_CacheTmpFixture):
    def test_no_slash_options_uses_prompt_tokens(self) -> None:
        s = _seed("s-6", prompt=_FULLSTACK_PROMPT_AUTO)
        summary = _persist_intake_mode_and_backlog(
            session=s, prompt_text=_FULLSTACK_PROMPT_AUTO
        )
        # prompt 의 autonomous_merge 가 영속됨
        self.assertEqual(summary["work_mode"], WORK_MODE_AUTONOMOUS)
        # source 는 slash_option_explicit 가 아님 (user_explicit 또는 inferred)
        self.assertNotEqual(
            summary["mode_decided_by"], "slash_option_explicit"
        )


# ---------------------------------------------------------------------------
# 7 — explicit 옵션 + prompt 둘 다 없으면 default
# ---------------------------------------------------------------------------


class DefaultGovernanceTests(_CacheTmpFixture):
    def test_no_signal_uses_default_governance(self) -> None:
        s = _seed("s-7", prompt="그냥 작업해 줘")
        summary = _persist_intake_mode_and_backlog(
            session=s, prompt_text="그냥 작업해 줘"
        )
        # default 값들
        self.assertEqual(summary["branch_strategy"], "git_flow")
        self.assertEqual(summary["release_strategy"], "tagged_release")
        self.assertEqual(summary["issue_policy"], "issue_required")
        # work_mode 는 default approval_required
        self.assertEqual(summary["work_mode"], WORK_MODE_APPROVAL)


# ---------------------------------------------------------------------------
# 8 — intake receipt 끝에 governance block append
# ---------------------------------------------------------------------------


class GovernanceBlockSurfaceTests(unittest.TestCase):
    def test_append_governance_block_adds_korean_lines(self) -> None:
        class _FakeResult:
            message = "원본 접수 메시지"

        summary = {
            "work_mode": "autonomous_merge",
            "topology": "single_repo",
            "scope": "full_stack_single_repo",
            "branch_strategy": "git_flow",
            "release_strategy": "tagged_release",
            "issue_policy": "issue_required",
            "mode_decided_by": "slash_option_explicit",
            "mode_decided_at": "2026-05-18T00:00:00+00:00",
        }
        result = _append_governance_block_to_result(_FakeResult(), summary)
        self.assertIn("🛡 거버넌스 contract", result.message)
        self.assertIn("autonomous_merge", result.message)
        self.assertIn("git_flow", result.message)
        self.assertIn("tagged_release", result.message)
        self.assertIn("issue_required", result.message)
        self.assertIn("slash_option_explicit", result.message)


# ---------------------------------------------------------------------------
# 9 — mode_decided_by 가 3 분기를 구분
# ---------------------------------------------------------------------------


class ModeDecidedBySourceTests(_CacheTmpFixture):
    def test_slash_option_yields_slash_option_explicit(self) -> None:
        s = _seed("s-9a", prompt=_FULLSTACK_PROMPT_NO_TOKEN)
        summary = _persist_intake_mode_and_backlog(
            session=s,
            prompt_text=_FULLSTACK_PROMPT_NO_TOKEN,
            explicit_work_mode=WORK_MODE_AUTONOMOUS,
        )
        self.assertEqual(summary["mode_decided_by"], "slash_option_explicit")

    def test_prompt_only_yields_user_explicit_or_inferred(self) -> None:
        s = _seed("s-9b", prompt=_FULLSTACK_PROMPT_AUTO)
        summary = _persist_intake_mode_and_backlog(
            session=s, prompt_text=_FULLSTACK_PROMPT_AUTO
        )
        self.assertIn(
            summary["mode_decided_by"],
            ("user_explicit", "gateway_inferred"),
        )
        # 그러나 slash_option_explicit 는 절대 아님
        self.assertNotEqual(
            summary["mode_decided_by"], "slash_option_explicit"
        )

    def test_no_signal_yields_gateway_inferred(self) -> None:
        s = _seed("s-9c", prompt="그냥 작업")
        summary = _persist_intake_mode_and_backlog(
            session=s, prompt_text="그냥 작업"
        )
        self.assertEqual(summary["mode_decided_by"], "gateway_inferred")


# ---------------------------------------------------------------------------
# 10 — legacy intake (옵션 없음) regression 없음
# ---------------------------------------------------------------------------


class LegacyIntakeRegressionTests(_CacheTmpFixture):
    def test_legacy_intake_signature_still_works(self) -> None:
        """옛 caller 가 explicit_* 인자 없이 호출해도 동작."""

        s = _seed("s-10", prompt=_FULLSTACK_PROMPT_APPROVAL)
        # 옛 호출 형식 — 키워드 explicit_* 없이
        summary = _persist_intake_mode_and_backlog(
            session=s, prompt_text=_FULLSTACK_PROMPT_APPROVAL
        )
        self.assertIsNotNone(summary)
        # prompt 의 approval_required 가 영속됨
        self.assertEqual(summary["work_mode"], WORK_MODE_APPROVAL)
        # default governance 키도 영속
        self.assertEqual(summary["branch_strategy"], "git_flow")
        self.assertEqual(summary["release_strategy"], "tagged_release")
        self.assertEqual(summary["issue_policy"], "issue_required")


if __name__ == "__main__":
    unittest.main()
