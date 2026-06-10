"""P1-K — live bootstrap diagnostics + greenfield detection fix.

라이브 smoke 에서 P1-J 까지 모두 들어갔는데도 scaffold 가 실제로
실행되지 않은 회귀의 root cause:

  * 이전 record-only run 이 ``runs/coding-executor-plans/<slug>.md`` 를
    commit 한 branch 를 reuse 하면 같은 worktree 에 그 plan note 가
    그대로 들어옴 → ``_looks_greenfield(worktree) → False`` →
    ``detect_bootstrap_mode → None`` → editor 가 silent delegate →
    scaffold 영원히 안 만들어짐.

본 모듈은 사용자가 명시한 6 케이스 모두 stdlib unittest 가드.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_engineering.agents.coding.greenfield_bootstrap import (
    MODE_GREENFIELD_FULL_STACK,
    _looks_greenfield,
    detect_bootstrap_mode,
)
from yule_engineering.agents.job_queue.coding_executor_live import (
    ENV_GREENFIELD_BOOTSTRAP_ENABLED,
    GreenfieldBootstrapEditor,
    RecordOnlyCodeEditor,
    detect_live_executor_availability,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)


@contextmanager
def _env_scope(**values: Optional[str]):
    original: Dict[str, Optional[str]] = {
        key: os.environ.get(key) for key in values
    }
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _full_stack_request() -> CodingExecuteRequest:
    return CodingExecuteRequest(
        session_id="11917bf1e75d",
        executor_role="backend-engineer",
        user_request=(
            "네이버 검색 풀스택 MVP 구현해줘. Next.js + NestJS + Postgres + Docker."
        ),
        generated_prompt="(prompt)",
        write_scope=(),
        forbidden_scope=(),
        safety_rules=(),
        base_branch="main",
        branch_hint="agent/backend-engineer/issue-1-coding-execute",
        repo_full_name="yule-studio/naver-search-clone",
        issue_number=1,
        dry_run=False,
        metadata={},
    )


# ---------------------------------------------------------------------------
# Case 1 — startup audit surfaces actual editor + env gate
# ---------------------------------------------------------------------------


class StartupAvailabilityAuditTests(unittest.TestCase):
    def test_availability_label_reflects_bootstrap_env_on(self) -> None:
        """case 1 — env on → label says ``greenfield_bootstrap+record_only_delegate``."""

        with _env_scope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            avail = detect_live_executor_availability(repo_root="/tmp/x")
        self.assertIn("greenfield_bootstrap", avail.code_editor)
        self.assertIn("delegate", avail.code_editor)
        self.assertEqual(avail.code_editor_blocker, "")

    def test_availability_label_reflects_bootstrap_env_off(self) -> None:
        """env off → label is disabled + blocker message names the env var."""

        with _env_scope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: None}):
            avail = detect_live_executor_availability(repo_root="/tmp/x")
        self.assertIn("disabled", avail.code_editor)
        self.assertIn(ENV_GREENFIELD_BOOTSTRAP_ENABLED, avail.code_editor_blocker)


# ---------------------------------------------------------------------------
# Case 2 — canonical greenfield request enters bootstrap mode at runtime
# ---------------------------------------------------------------------------


class BootstrapModeAtRuntimeTests(unittest.TestCase):
    def test_canonical_request_detects_full_stack_mode(self) -> None:
        """case 2 — canonical request text + greenfield worktree →
        ``MODE_GREENFIELD_FULL_STACK`` (NOT None)."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "README.md").write_text("# x")
            mode = detect_bootstrap_mode(
                request=_full_stack_request(), worktree_path=str(root)
            )
        self.assertEqual(mode, MODE_GREENFIELD_FULL_STACK)


# ---------------------------------------------------------------------------
# Case 3 — bootstrap apply audit always present (even for delegate path)
# ---------------------------------------------------------------------------


class BootstrapApplyAuditTests(unittest.TestCase):
    def test_delegate_path_records_audit_metadata(self) -> None:
        """case 3 — non-greenfield repo → editor delegates to record-only,
        but ``metadata['bootstrap_apply']`` 는 여전히 stamp 되어 operator
        가 "왜 scaffold 안 됐는지" 즉시 본다."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}")  # not greenfield
            editor = GreenfieldBootstrapEditor()
            ctx = WorktreeContext(branch="b", worktree_path=str(root))
            new_ctx = editor.apply(request=_full_stack_request(), context=ctx)
        audit = new_ctx.metadata.get("bootstrap_apply") or {}
        self.assertEqual(audit.get("mode"), None)
        self.assertEqual(audit.get("decision"), "delegate_record_only")
        self.assertIn("worktree_path", audit)
        self.assertIn("repo_full_name", audit)
        self.assertIn("bootstrap_enabled", audit)

    def test_scaffold_path_records_files_and_audit(self) -> None:
        """env on + greenfield → metadata.bootstrap_apply 에 files_created
        / files_allowed_by_bootstrap_exception 등 detail audit."""

        with _env_scope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            editor = GreenfieldBootstrapEditor()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".git").mkdir()
                (root / "README.md").write_text("# x")
                ctx = WorktreeContext(branch="b", worktree_path=str(root))
                new_ctx = editor.apply(
                    request=_full_stack_request(), context=ctx
                )
                audit = new_ctx.metadata.get("bootstrap_apply") or {}
                self.assertEqual(audit.get("mode"), MODE_GREENFIELD_FULL_STACK)
                self.assertGreater(
                    len(audit.get("files_created", [])), 5
                )


# ---------------------------------------------------------------------------
# Case 4 — smoke probe is the WORKTREE path, NOT the operator's local checkout
# ---------------------------------------------------------------------------


class SmokeProbeLocationTests(unittest.TestCase):
    def test_scaffold_writes_to_worktree_not_main_checkout(self) -> None:
        """case 4 — scaffold lands at ``context.worktree_path``. operator
        smoke check 가 main local checkout 만 보면 false-negative.

        본 테스트는 두 디렉터리 (main checkout vs worktree) 를 분리해서
        만들고 scaffold 가 정확히 worktree 에만 작성됨을 확인.
        """

        with _env_scope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            with tempfile.TemporaryDirectory() as main_tmp:
                main_checkout = Path(main_tmp) / "naver-search-clone"
                main_checkout.mkdir()
                (main_checkout / ".git").mkdir()
                (main_checkout / "README.md").write_text("# greenfield\n")
                with tempfile.TemporaryDirectory() as wt_tmp:
                    worktree = Path(wt_tmp) / "wt-slug"
                    worktree.mkdir()
                    (worktree / ".git").mkdir()
                    (worktree / "README.md").write_text("# greenfield\n")
                    editor = GreenfieldBootstrapEditor()
                    ctx = WorktreeContext(
                        branch="b", worktree_path=str(worktree)
                    )
                    editor.apply(request=_full_stack_request(), context=ctx)
                    # scaffold 는 worktree 안에만
                    self.assertTrue((worktree / "package.json").is_file())
                    # main checkout 은 안 건드림 (operator pull 전까지 비어있음)
                    self.assertFalse((main_checkout / "package.json").is_file())


# ---------------------------------------------------------------------------
# Case 5 — P1-K direct fix: stale plan-note 가 greenfield 판정을 깨지 않음
# ---------------------------------------------------------------------------


class StalePlanNoteDoesNotBreakDetectionTests(unittest.TestCase):
    def test_runs_directory_with_only_plan_notes_still_greenfield(self) -> None:
        """**핵심 회귀 가드** — 이전 record-only run 이 commit 한
        ``runs/coding-executor-plans/<slug>.md`` 만 있는 worktree 도
        여전히 greenfield 로 본다. 이 fix 가 없으면 detect_bootstrap_mode
        가 영원히 None → silent delegate → no scaffold."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "README.md").write_text("# x")
            plan_dir = root / "runs" / "coding-executor-plans"
            plan_dir.mkdir(parents=True)
            (plan_dir / "agent-backend-engineer.md").write_text(
                "# plan from previous run"
            )
            self.assertTrue(_looks_greenfield(root))
            mode = detect_bootstrap_mode(
                request=_full_stack_request(), worktree_path=str(root)
            )
            self.assertEqual(mode, MODE_GREENFIELD_FULL_STACK)

    def test_runs_with_user_owned_files_is_NOT_greenfield(self) -> None:
        """``runs/`` 안에 user-owned 파일이 있으면 (executor-owned 디렉터리
        아님) — 여전히 not greenfield. 옛 의미 보존."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "README.md").write_text("# x")
            user_runs = root / "runs" / "user-data"
            user_runs.mkdir(parents=True)
            (user_runs / "results.csv").write_text("a,b,c\n1,2,3\n")
            self.assertFalse(_looks_greenfield(root))


# ---------------------------------------------------------------------------
# Case 6 — non-greenfield regression
# ---------------------------------------------------------------------------


class NonGreenfieldRegressionTests(unittest.TestCase):
    def test_existing_code_repo_not_in_bootstrap_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "package.json").write_text("{}")
            mode = detect_bootstrap_mode(
                request=_full_stack_request(), worktree_path=str(root)
            )
        self.assertIsNone(mode)


# ---------------------------------------------------------------------------
# Logging surface — operator must see WHY scaffold ran or didn't
# ---------------------------------------------------------------------------


class LoggingSurfaceTests(unittest.TestCase):
    def test_editor_emits_log_with_decision_path(self) -> None:
        """editor.apply 가 INFO 레벨로 worktree / mode / bootstrap_enabled
        를 한 줄 노출. silent delegate 가 절대 silent 가 아니게."""

        logger = logging.getLogger(
            "yule_engineering.agents.job_queue.coding_executor_live"
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.INFO)
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "package.json").write_text("{}")
                editor = GreenfieldBootstrapEditor()
                ctx = WorktreeContext(branch="b", worktree_path=str(root))
                editor.apply(request=_full_stack_request(), context=ctx)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        output = stream.getvalue()
        self.assertIn("GreenfieldBootstrapEditor.apply", output)
        self.assertIn("detected_mode=", output)
        self.assertIn("bootstrap_enabled=", output)


if __name__ == "__main__":
    unittest.main()
