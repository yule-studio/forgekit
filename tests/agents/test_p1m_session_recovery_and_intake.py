"""P1-M — 14 사용자 명시 acceptance.

1.  현재 세션 recovery 시 mode/topology/scope 가 복구
2.  현재 세션 recovery 시 coding_backlog 가 생성
3.  현재 세션 recovery 후 PR #4 또는 후속 구현 PR 흐름이 실제 구현 경로
4.  새 intake (autonomous_merge) persists mode/topology/scope
5.  새 intake (approval_required) persists mode/topology/scope
6.  새 intake + recovery 둘 다 seed coding_backlog
7.  approval_required path 가 real pr_merge approval card 게시 (wiring 확인)
8.  approval reply 가 real merge path 로 라우팅 (wiring 확인)
9.  autonomous_merge path 가 real merge executor wiring 보유
10. non-greenfield repo 가 더 이상 silent planning-only PR 로 빠지지 않음
11. issue title 이 한국어 humanizer 거침
12. PR title 이 한국어 humanizer 거침
13. blocker 가 남았을 때 misleading "다음 tick" 메시지 제거
14. duplicate branch replay / planning PR 가드
"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace as _replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.coding.coding_backlog_seed import (
    EXTRA_CODING_BACKLOG,
    FULL_STACK_SEARCH_MVP_PLAN,
    detect_backlog_plan,
    seed_coding_backlog,
)
from yule_orchestrator.agents.coding.human_titles import (
    build_issue_title,
    build_pr_body_intro,
    build_pr_title,
)
from yule_orchestrator.agents.job_queue.coding_executor_live import (
    ENV_PLANNING_ONLY_PR_FORBIDDEN,
    GreenfieldBootstrapEditor,
    NonGreenfieldRealEditUnavailable,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    REASON_NON_GREENFIELD_REAL_EDIT_UNAVAILABLE,
    WorktreeContext,
)
from yule_orchestrator.agents.lifecycle.session_mode import (
    EXTRA_DECIDED_AT,
    EXTRA_DECIDED_BY,
    EXTRA_SCOPE,
    EXTRA_TOPOLOGY,
    EXTRA_WORK_MODE,
    SCOPE_FULL_STACK,
    TOPOLOGY_SINGLE,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
    DECIDED_BY_USER,
)
from yule_orchestrator.agents.lifecycle.session_recovery import (
    recover_session_full,
)
from yule_orchestrator.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    save_session,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _seed(session_id: str, *, prompt: str, extra: Mapping[str, Any]) -> WorkflowSession:
    s = WorkflowSession(
        session_id=session_id,
        prompt=prompt,
        task_type="coding_execute",
        state=WorkflowState.IN_PROGRESS,
        created_at=_now(),
        updated_at=_now(),
        executor_role="backend-engineer",
        extra=dict(extra),
    )
    save_session(s)
    return s


_PROMPT_AUTONOMOUS = (
    "autonomous_merge, single_repo, full_stack_single_repo "
    "네이버 검색 풀스택 MVP 구현해줘 "
    "https://github.com/yule-studio/naver-search-clone"
)
_PROMPT_APPROVAL = (
    "approval_required, single_repo, full_stack_single_repo "
    "네이버 검색 풀스택 MVP 구현해줘 "
    "https://github.com/yule-studio/naver-search-clone"
)


class _CacheTmpFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["YULE_AGENT_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        os.environ.pop("YULE_AGENT_CACHE_DIR", None)


# ---------------------------------------------------------------------------
# 1, 2 — 현재 세션 recovery
# ---------------------------------------------------------------------------


class CurrentSessionRecoveryTests(_CacheTmpFixture):
    def test_recovery_re_parses_mode_from_prompt(self) -> None:
        # canonical 회귀 shape — work_mode=None 인데 PR 메타만 stamp 된 상태.
        _seed(
            "fe5eedc65196",
            prompt=_PROMPT_AUTONOMOUS,
            extra={
                "pr_merge_stage": "pr_merge_pending",
                "pr_merge_pr_number": 4,
            },
        )
        report = recover_session_full(session_id="fe5eedc65196")
        self.assertTrue(report.found)
        self.assertEqual(report.work_mode, WORK_MODE_AUTONOMOUS)
        self.assertEqual(report.topology, TOPOLOGY_SINGLE)
        self.assertEqual(report.scope, SCOPE_FULL_STACK)
        # workflow_state 에도 영속됨
        fresh = load_session("fe5eedc65196")
        self.assertEqual(fresh.extra[EXTRA_WORK_MODE], WORK_MODE_AUTONOMOUS)
        self.assertEqual(fresh.extra[EXTRA_DECIDED_BY], DECIDED_BY_USER)
        self.assertIn(EXTRA_DECIDED_AT, fresh.extra)

    def test_recovery_seeds_coding_backlog(self) -> None:
        _seed(
            "rec-backlog",
            prompt=_PROMPT_AUTONOMOUS,
            extra={},
        )
        report = recover_session_full(session_id="rec-backlog")
        # full_stack + 네이버 검색 키워드 → 8 slice
        self.assertEqual(report.backlog_seeded_count, 8)
        fresh = load_session("rec-backlog")
        self.assertEqual(len(fresh.extra[EXTRA_CODING_BACKLOG]), 8)

    def test_recovery_stamps_pr_merge_when_operator_hint_given(self) -> None:
        _seed(
            "rec-pr",
            prompt=_PROMPT_AUTONOMOUS,
            extra={},
        )
        report = recover_session_full(
            session_id="rec-pr",
            pr_number=4,
            pr_url="https://github.com/yule-studio/naver-search-clone/pull/4",
            head_sha="sha4",
            repo_full_name="yule-studio/naver-search-clone",
        )
        self.assertTrue(report.pr_merge_stamped)
        fresh = load_session("rec-pr")
        self.assertEqual(fresh.extra["pr_merge_stage"], "pr_merge_pending")
        self.assertEqual(fresh.extra["pr_merge_pr_number"], 4)
        self.assertEqual(
            fresh.extra["pr_merge_repo"], "yule-studio/naver-search-clone"
        )


# ---------------------------------------------------------------------------
# 4, 5, 6 — 새 intake 일반 경로 + backlog seed
# ---------------------------------------------------------------------------


class NewIntakeModePersistenceTests(_CacheTmpFixture):
    def test_explicit_autonomous_merge_persists(self) -> None:
        from yule_orchestrator.agents.coding.coding_session_context import (
            prepare_coding_session_context,
        )

        ctx = prepare_coding_session_context(
            message_text=_PROMPT_AUTONOMOUS,
            user_links=("https://github.com/yule-studio/naver-search-clone",),
            existing_extra={},
            discover_contract=False,
        )
        self.assertEqual(ctx.session_mode.work_mode, WORK_MODE_AUTONOMOUS)
        self.assertEqual(ctx.session_mode.topology, TOPOLOGY_SINGLE)
        self.assertEqual(ctx.session_mode.scope, SCOPE_FULL_STACK)
        self.assertEqual(
            ctx.extras_update[EXTRA_WORK_MODE], WORK_MODE_AUTONOMOUS
        )

    def test_explicit_approval_required_persists(self) -> None:
        from yule_orchestrator.agents.coding.coding_session_context import (
            prepare_coding_session_context,
        )

        ctx = prepare_coding_session_context(
            message_text=_PROMPT_APPROVAL,
            user_links=("https://github.com/yule-studio/naver-search-clone",),
            existing_extra={},
            discover_contract=False,
        )
        self.assertEqual(ctx.session_mode.work_mode, WORK_MODE_APPROVAL)
        self.assertEqual(
            ctx.extras_update[EXTRA_WORK_MODE], WORK_MODE_APPROVAL
        )

    def test_new_intake_path_seeds_backlog(self) -> None:
        # intake 직후 호출되는 helper 와 동일 — session 만들고 seed_coding_backlog.
        _seed(
            "new-intake-1",
            prompt=_PROMPT_AUTONOMOUS,
            extra={
                EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
                EXTRA_TOPOLOGY: TOPOLOGY_SINGLE,
                EXTRA_SCOPE: SCOPE_FULL_STACK,
            },
        )
        plan = seed_coding_backlog(session_id="new-intake-1", seeded_by="intake")
        self.assertIsNotNone(plan)
        self.assertEqual(len(plan), 8)

    def test_backlog_seed_is_idempotent(self) -> None:
        _seed(
            "new-intake-2",
            prompt=_PROMPT_AUTONOMOUS,
            extra={
                EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
                EXTRA_TOPOLOGY: TOPOLOGY_SINGLE,
                EXTRA_SCOPE: SCOPE_FULL_STACK,
            },
        )
        seed_coding_backlog(session_id="new-intake-2", seeded_by="intake")
        # 두 번째 호출은 기존 plan 을 보존
        seeded2 = seed_coding_backlog(session_id="new-intake-2", seeded_by="intake")
        self.assertIsNotNone(seeded2)
        fresh = load_session("new-intake-2")
        self.assertEqual(len(fresh.extra[EXTRA_CODING_BACKLOG]), 8)


# ---------------------------------------------------------------------------
# 7, 8, 9 — production wiring (approval_enqueuer / merge_executor wiring)
# ---------------------------------------------------------------------------


class ProductionWiringTests(unittest.TestCase):
    def test_approval_enqueuer_returns_callable_with_resolver(self) -> None:
        """P1-M B 회귀 가드 — 옛 wiring 은 None 반환 (ApprovalWorker init 실패).
        새 wiring 은 production post_fn + channel_resolver 주입."""

        from yule_orchestrator.runtime.coding_executor_runner import (
            _maybe_build_approval_enqueuer,
        )

        enqueuer = _maybe_build_approval_enqueuer()
        # 함수 / 콜백 객체여야 함 (None 이면 wiring 회귀)
        self.assertIsNotNone(enqueuer)
        self.assertTrue(callable(enqueuer))

    def test_merge_executor_respects_env_opt_in(self) -> None:
        """live merge executor 는 env opt-in 시에만 wiring."""

        from yule_orchestrator.runtime.coding_executor_runner import (
            _maybe_build_live_pr_merge_executor,
        )

        # env unset 일 때 None
        prev = os.environ.pop("YULE_GITHUB_APP_MERGE_OPT_IN", None)
        try:
            self.assertIsNone(_maybe_build_live_pr_merge_executor())
        finally:
            if prev is not None:
                os.environ["YULE_GITHUB_APP_MERGE_OPT_IN"] = prev

    def test_pr_merge_reply_routing_helper_exists(self) -> None:
        """approval reply router 가 pr_merge 분기를 갖고 있는지."""

        from yule_orchestrator.discord.approval.reply_router import (
            _try_handle_pr_merge_reply,
        )

        self.assertTrue(callable(_try_handle_pr_merge_reply))


# ---------------------------------------------------------------------------
# 10 — non-greenfield real edit blocker
# ---------------------------------------------------------------------------


class NonGreenfieldBlockerTests(unittest.TestCase):
    def test_planning_only_pr_forbidden_env_raises_on_non_greenfield(
        self,
    ) -> None:
        """env=1 + non-greenfield repo → NonGreenfieldRealEditUnavailable.

        옛 wiring 은 silent delegate 로 planning-only PR 을 생성했지만,
        본 가드가 들어가면 worker 가 REASON_NON_GREENFIELD_REAL_EDIT_UNAVAILABLE
        로 honest blocker stamp.
        """

        editor = GreenfieldBootstrapEditor(env={ENV_PLANNING_ONLY_PR_FORBIDDEN: "1"})
        # non-greenfield worktree — 이미 코드가 있는 repo
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}")
            (root / ".git").mkdir()
            ctx = WorktreeContext(branch="b", worktree_path=str(root))
            req = CodingExecuteRequest(
                session_id="s-nongreen",
                executor_role="backend-engineer",
                user_request="실제 구현 추가",
                generated_prompt="(p)",
                write_scope=("src/**",),
                forbidden_scope=(),
                safety_rules=(),
                base_branch="main",
                branch_hint="agent/x",
                repo_full_name="yule-studio/naver-search-clone",
                issue_number=1,
                dry_run=False,
                metadata={},
            )
            with self.assertRaises(NonGreenfieldRealEditUnavailable):
                editor.apply(request=req, context=ctx)

    def test_default_env_allows_record_only_for_backwards_compat(self) -> None:
        """env unset 이면 (default) 옛 동작 보존 — silent record-only.
        이렇게 해서 operator 가 명시적 opt-in 전까지 회귀 0."""

        editor = GreenfieldBootstrapEditor()  # env=None (real env, unset)
        prev = os.environ.pop(ENV_PLANNING_ONLY_PR_FORBIDDEN, None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "package.json").write_text("{}")
                (root / ".git").mkdir()
                ctx = WorktreeContext(branch="b", worktree_path=str(root))
                req = CodingExecuteRequest(
                    session_id="s-nongreen-2",
                    executor_role="backend-engineer",
                    user_request="실제 구현 추가",
                    generated_prompt="(p)",
                    write_scope=("src/**",),
                    forbidden_scope=(),
                    safety_rules=(),
                    base_branch="main",
                    branch_hint="agent/x",
                    repo_full_name="yule-studio/naver-search-clone",
                    issue_number=1,
                    dry_run=False,
                    metadata={},
                )
                new_ctx = editor.apply(request=req, context=ctx)
                # delegate 동작 — plan 파일 한 개 추가됨
                self.assertGreater(len(new_ctx.edited_files), 0)
                # metadata audit 에 forbidden 플래그가 False 로 기록
                audit = (new_ctx.metadata or {}).get("bootstrap_apply") or {}
                self.assertFalse(audit.get("planning_only_pr_forbidden", True))
        finally:
            if prev is not None:
                os.environ[ENV_PLANNING_ONLY_PR_FORBIDDEN] = prev


# ---------------------------------------------------------------------------
# 11, 12 — issue/PR title humanizer
# ---------------------------------------------------------------------------


class HumanTitleTests(unittest.TestCase):
    def test_pr_title_for_slice_is_korean(self) -> None:
        slice_spec = FULL_STACK_SEARCH_MVP_PLAN[0]  # 인증 백엔드
        title = build_pr_title(
            session_prompt=_PROMPT_AUTONOMOUS,
            slice_spec=slice_spec,
            branch_hint="agent/backend/auth",
            issue_number=4,
        )
        self.assertIn("[구현]", title)
        self.assertIn("인증", title)
        self.assertIn("회원가입", title)
        self.assertIn("(#4)", title)
        # 옛 기계형 제목 패턴 금지
        self.assertNotIn("coding-executor draft", title)

    def test_pr_title_without_slice_uses_prompt_summary(self) -> None:
        title = build_pr_title(
            session_prompt=_PROMPT_AUTONOMOUS, slice_spec=None, issue_number=1
        )
        # 모드 토큰은 제거되고 한국어 요약만 남음
        self.assertNotIn("autonomous_merge", title.lower())
        self.assertNotIn("full_stack_single_repo", title.lower())
        self.assertIn("[구현]", title)
        self.assertIn("네이버", title)

    def test_issue_title_for_slice_is_korean(self) -> None:
        slice_spec = FULL_STACK_SEARCH_MVP_PLAN[2]  # 검색 홈 UI
        title = build_issue_title(
            session_prompt=_PROMPT_AUTONOMOUS, slice_spec=slice_spec
        )
        self.assertIn("[Feature]", title)
        self.assertIn("검색", title)
        self.assertNotIn("autonomous_merge", title.lower())

    def test_pr_body_intro_includes_session_meta(self) -> None:
        body = build_pr_body_intro(
            session_id="fe5eedc65196",
            repo_full_name="yule-studio/naver-search-clone",
            work_mode="autonomous_merge",
            slice_spec=FULL_STACK_SEARCH_MVP_PLAN[0],
            branch="agent/backend/auth",
            backlog_remaining=7,
        )
        self.assertIn("fe5eedc65196", body)
        self.assertIn("yule-studio/naver-search-clone", body)
        self.assertIn("autonomous_merge", body)
        self.assertIn("남은 slice", body)
        self.assertIn("7", body)


# ---------------------------------------------------------------------------
# 13 — backlog detect 가 의도 없을 때 빈 plan 반환 (misleading skip)
# ---------------------------------------------------------------------------


class HonestBacklogDetectTests(unittest.TestCase):
    def test_detect_plan_returns_empty_when_no_intent(self) -> None:
        extra = {
            EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
            EXTRA_TOPOLOGY: TOPOLOGY_SINGLE,
            EXTRA_SCOPE: "single_scope",  # full_stack 아님
        }
        plan = detect_backlog_plan(extra, prompt="auth backend 하나만 추가해줘")
        self.assertEqual(plan, ())

    def test_seed_does_not_stamp_when_intent_absent(self) -> None:
        # seed_coding_backlog 는 plan 이 비어있으면 None 반환 (silent stamp 안 함)
        # → operator 가 status 에서 backlog 비어있음을 정직하게 본다.
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        try:
            os.environ["YULE_AGENT_CACHE_DIR"] = tmp.name
            _seed(
                "honest-1",
                prompt="auth 만 추가",
                extra={EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS},
            )
            seeded = seed_coding_backlog(session_id="honest-1")
            self.assertIsNone(seeded)
            fresh = load_session("honest-1")
            self.assertNotIn(EXTRA_CODING_BACKLOG, fresh.extra)
        finally:
            os.environ.pop("YULE_AGENT_CACHE_DIR", None)
            tmp.cleanup()


# ---------------------------------------------------------------------------
# 14 — duplicate branch / planning PR 가드
# ---------------------------------------------------------------------------


class DuplicateReplayGuardTests(unittest.TestCase):
    def test_backlog_pop_advances_pointer_no_replay(self) -> None:
        """coding_backlog 첫 항목 pop 은 한 번만 동일 slice 를 반환.

        dispatch_next_coding_slice 가 backlog 첫 항목을 pop 한 뒤 두 번째
        호출은 다음 항목을 본다 → 같은 slice replay 없음.
        """

        import tempfile
        from yule_orchestrator.agents.job_queue.next_slice_dispatcher import (
            dispatch_next_coding_slice,
        )
        from yule_orchestrator.agents.job_queue.pr_merge_continuation import (
            EXTRA_PR_MERGE_STAGE,
            STAGE_PR_MERGED,
        )

        tmp = tempfile.TemporaryDirectory()
        try:
            os.environ["YULE_AGENT_CACHE_DIR"] = tmp.name
            _seed(
                "dup-1",
                prompt=_PROMPT_AUTONOMOUS,
                extra={
                    EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
                    EXTRA_TOPOLOGY: TOPOLOGY_SINGLE,
                    EXTRA_SCOPE: SCOPE_FULL_STACK,
                    EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGED,
                    EXTRA_CODING_BACKLOG: [
                        {"summary": "slice-1", "prompt": "first"},
                        {"summary": "slice-2", "prompt": "second"},
                    ],
                },
            )

            slices: list = []

            def enqueue(sid: str, spec: Mapping[str, Any]) -> None:
                slices.append(dict(spec))

            def persist(new_extra: Mapping[str, Any]) -> None:
                session = load_session("dup-1")
                save_session(
                    _replace(session, extra=dict(new_extra), updated_at=_now())
                )

            # 첫 호출
            session = load_session("dup-1")
            dispatch_next_coding_slice(
                session_id="dup-1",
                session_extra=dict(session.extra or {}),
                persist_extra=persist,
                enqueue_slice=enqueue,
            )
            # 두 번째 호출 — backlog 한 칸 줄어든 상태에서 두 번째 slice 만 pop
            session2 = load_session("dup-1")
            dispatch_next_coding_slice(
                session_id="dup-1",
                session_extra=dict(session2.extra or {}),
                persist_extra=persist,
                enqueue_slice=enqueue,
            )
            self.assertEqual(len(slices), 2)
            self.assertEqual(slices[0]["summary"], "slice-1")
            self.assertEqual(slices[1]["summary"], "slice-2")
        finally:
            os.environ.pop("YULE_AGENT_CACHE_DIR", None)
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
