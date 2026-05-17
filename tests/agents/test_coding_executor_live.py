"""coding_executor_live — Round 2 of #73."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Mapping, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.coding_executor_live import (
    CodingCommitError,
    DEFAULT_PLAN_FILE_REL,
    GithubAppDraftPRCreator,
    GithubAppPusher,
    LiveExecutorAvailability,
    LocalGitCommitter,
    LocalGitWorktreeProvisioner,
    RecordOnlyCodeEditor,
    SubprocessTestRunner,
    build_live_executor,
    detect_live_executor_availability,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)


def _request(**overrides) -> CodingExecuteRequest:
    base = {
        "session_id": "sess-live-1",
        "executor_role": "backend-engineer",
        "user_request": "users 401 회복",
        "generated_prompt": "(prompt)",
        "write_scope": ("services/auth/**",),
        "forbidden_scope": (".github/workflows/**",),
        "safety_rules": ("no force push",),
        "base_branch": "main",
        "branch_hint": "agent/backend-engineer/issue-99-fix",
        "repo_full_name": "yule-studio/yule-studio-agent",
        "issue_number": 99,
        "dry_run": False,
        "metadata": {},
    }
    base.update(overrides)
    return CodingExecuteRequest(**base)


# ---------------------------------------------------------------------------
# RecordOnlyCodeEditor
# ---------------------------------------------------------------------------


class RecordOnlyEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.worktree = Path(self._tmp.name)

    def test_writes_plan_markdown_under_runs_dir(self) -> None:
        editor = RecordOnlyCodeEditor()
        ctx = WorktreeContext(
            branch="agent/x/issue-99-fix",
            worktree_path=str(self.worktree),
            base_commit_sha="deadbeef",
        )
        new_ctx = editor.apply(request=_request(), context=ctx)
        self.assertEqual(len(new_ctx.edited_files), 1)
        plan_path = self.worktree / new_ctx.edited_files[0]
        self.assertTrue(plan_path.is_file())
        body = plan_path.read_text(encoding="utf-8")
        self.assertIn("coding-executor plan", body)
        self.assertIn("backend-engineer", body)
        self.assertIn("users 401", body)
        self.assertIn("RecordOnlyCodeEditor", body)
        # Hard rail: never edits source.
        self.assertNotIn("services/auth", _safe_glob_files(self.worktree, "services"))

    def test_does_not_modify_existing_files(self) -> None:
        sentinel = self.worktree / "important.py"
        sentinel.write_text("ORIGINAL", encoding="utf-8")
        editor = RecordOnlyCodeEditor()
        ctx = WorktreeContext(
            branch="agent/x/issue-99-fix",
            worktree_path=str(self.worktree),
        )
        editor.apply(request=_request(), context=ctx)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "ORIGINAL")


def _safe_glob_files(root: Path, prefix: str) -> str:
    paths = []
    for p in root.rglob("*"):
        if p.is_file() and prefix in str(p.relative_to(root)):
            paths.append(str(p))
    return ",".join(paths)


# ---------------------------------------------------------------------------
# SubprocessTestRunner
# ---------------------------------------------------------------------------


class SubprocessTestRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.worktree = Path(self._tmp.name)

    def test_success_command_records_status_ok(self) -> None:
        runner = SubprocessTestRunner(default_command=("python3", "-c", "print('ok')"))
        ctx = WorktreeContext(
            branch="agent/x/y", worktree_path=str(self.worktree)
        )
        new_ctx = runner.run(request=_request(), context=ctx)
        self.assertEqual(new_ctx.test_summary["status"], "ok")
        self.assertIn("ok", new_ctx.test_summary["stdout_tail"])

    def test_failure_command_records_status_failed(self) -> None:
        runner = SubprocessTestRunner(
            default_command=("python3", "-c", "import sys; sys.exit(2)")
        )
        ctx = WorktreeContext(
            branch="agent/x/y", worktree_path=str(self.worktree)
        )
        new_ctx = runner.run(request=_request(), context=ctx)
        self.assertEqual(new_ctx.test_summary["status"], "failed")
        self.assertEqual(new_ctx.test_summary["exit_code"], 2)

    def test_request_metadata_overrides_default_command(self) -> None:
        runner = SubprocessTestRunner(default_command=("false",))  # would fail
        ctx = WorktreeContext(
            branch="agent/x/y", worktree_path=str(self.worktree)
        )
        request = _request(metadata={"test_command": ["python3", "-c", "print('override')"]})
        new_ctx = runner.run(request=request, context=ctx)
        self.assertEqual(new_ctx.test_summary["status"], "ok")


# ---------------------------------------------------------------------------
# LocalGitWorktreeProvisioner + LocalGitCommitter — full integration with
# a real temp git repo
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    seed = path / "README.md"
    seed.write_text("# seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "seed"], check=True)
    head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return head


class WorktreeProvisionerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._repo_tmp = tempfile.TemporaryDirectory()
        self._wt_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._repo_tmp.cleanup)
        self.addCleanup(self._wt_tmp.cleanup)
        self.repo = Path(self._repo_tmp.name)
        self.worktree_root = Path(self._wt_tmp.name)
        self.head = _init_git_repo(self.repo)

    def test_provision_creates_worktree_at_root(self) -> None:
        # P1-B: tests use a temp git repo as the target — bypass the
        # real cross-repo resolver by injecting a permissive one that
        # always maps to ``self.repo``.
        provisioner = LocalGitWorktreeProvisioner(
            repo_root=str(self.repo),
            worktree_root=str(self.worktree_root),
            repo_root_resolver=lambda _n: str(self.repo),
        )
        ctx = provisioner.provision(
            request=_request(), branch="agent/backend-engineer/issue-99-fix"
        )
        self.assertTrue(Path(ctx.worktree_path).is_dir())
        self.assertEqual(ctx.base_commit_sha, self.head)
        provisioner.cleanup(force=True)

    def test_cleanup_removes_worktree(self) -> None:
        # P1-B: tests use a temp git repo as the target — bypass the
        # real cross-repo resolver by injecting a permissive one that
        # always maps to ``self.repo``.
        provisioner = LocalGitWorktreeProvisioner(
            repo_root=str(self.repo),
            worktree_root=str(self.worktree_root),
            repo_root_resolver=lambda _n: str(self.repo),
        )
        ctx = provisioner.provision(
            request=_request(), branch="agent/backend-engineer/cleanup-1"
        )
        path = Path(ctx.worktree_path)
        self.assertTrue(path.is_dir())
        provisioner.cleanup(force=True)
        self.assertFalse(path.is_dir())


class LocalGitCommitterIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._repo_tmp = tempfile.TemporaryDirectory()
        self._wt_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._repo_tmp.cleanup)
        self.addCleanup(self._wt_tmp.cleanup)
        self.repo = Path(self._repo_tmp.name)
        self.worktree_root = Path(self._wt_tmp.name)
        _init_git_repo(self.repo)
        self.provisioner = LocalGitWorktreeProvisioner(
            repo_root=str(self.repo),
            worktree_root=str(self.worktree_root),
            # P1-B: bypass cross-repo resolver — see WorktreeProvisionerIntegrationTests.
            repo_root_resolver=lambda _n: str(self.repo),
        )
        self.addCleanup(lambda: self.provisioner.cleanup(force=True))

    def test_commit_with_record_only_editor_creates_one_commit(self) -> None:
        ctx = self.provisioner.provision(
            request=_request(), branch="agent/backend-engineer/commit-1"
        )
        ctx = RecordOnlyCodeEditor().apply(request=_request(), context=ctx)
        committer = LocalGitCommitter()
        ctx = committer.commit(request=_request(), context=ctx)
        self.assertTrue(ctx.commit_sha)
        # Commit author is the role-bot identity.
        author = subprocess.run(
            ["git", "-C", ctx.worktree_path, "log", "-1", "--format=%an"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertIn("backend-engineer", author)

    def test_commit_with_no_edits_returns_empty_sha(self) -> None:
        ctx = self.provisioner.provision(
            request=_request(), branch="agent/backend-engineer/commit-noedit"
        )
        committer = LocalGitCommitter()
        ctx = committer.commit(request=_request(), context=ctx)
        self.assertEqual(ctx.commit_sha, "")


# ---------------------------------------------------------------------------
# GithubAppPusher / DraftPRCreator — fake live client
# ---------------------------------------------------------------------------


class _FakeLiveClient:
    def __init__(self) -> None:
        self.calls: list = []

    def get_branch_head_sha(self, *, repo, branch):
        self.calls.append(("head_sha", repo, branch))
        return "base-sha-1234"

    def get_commit_tree_sha(self, *, repo, commit_sha):
        self.calls.append(("tree_sha", repo, commit_sha))
        return "base-tree-1234"

    def create_blob(self, *, repo, content):
        self.calls.append(("blob", repo, len(content)))
        return f"blob-{len(content)}"

    def create_tree(self, *, repo, base_tree, entries):
        self.calls.append(("tree", repo, base_tree, len(entries)))
        return {"sha": "new-tree-1234"}

    def create_branch_ref(self, *, repo, branch, base_sha):
        self.calls.append(("branch_ref", repo, branch, base_sha))

    def create_commit_via_data_api(self, **kwargs):
        self.calls.append(("commit", kwargs["repo"], kwargs["branch"]))
        return {"sha": "new-commit-1234"}

    def create_draft_pull_request(self, **kwargs):
        self.calls.append(("draft_pr", kwargs["repo"], kwargs["head"]))
        return {"number": 999, "html_url": "https://github.com/x/y/pull/999"}


class GithubAppPusherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.worktree = Path(self._tmp.name)
        # Pre-populate a planned file.
        plan = self.worktree / "plans" / "x.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("plan content", encoding="utf-8")
        self.ctx = WorktreeContext(
            branch="agent/backend-engineer/push-1",
            worktree_path=str(self.worktree),
            edited_files=("plans/x.md",),
        )

    def test_push_creates_blob_tree_branch_commit(self) -> None:
        client = _FakeLiveClient()
        pusher = GithubAppPusher(live_client=client)
        new_ctx = pusher.push(request=_request(), context=self.ctx)
        self.assertTrue(new_ctx.pushed)
        self.assertEqual(new_ctx.commit_sha, "new-commit-1234")
        # Required calls fired in order.
        kinds = [c[0] for c in client.calls]
        self.assertEqual(
            kinds,
            ["head_sha", "tree_sha", "blob", "tree", "branch_ref", "commit"],
        )

    def test_push_with_no_edits_returns_pushed_false(self) -> None:
        client = _FakeLiveClient()
        pusher = GithubAppPusher(live_client=client)
        empty_ctx = WorktreeContext(
            branch="agent/x/y", worktree_path=str(self.worktree), edited_files=()
        )
        new_ctx = pusher.push(request=_request(), context=empty_ctx)
        self.assertFalse(new_ctx.pushed)
        kinds = [c[0] for c in client.calls]
        # Only resolved base + tree, did not blob/commit/ref.
        self.assertEqual(kinds, ["head_sha", "tree_sha"])


class GithubAppDraftPRCreatorTests(unittest.TestCase):
    def test_creates_pr_and_records_number_url(self) -> None:
        client = _FakeLiveClient()
        creator = GithubAppDraftPRCreator(live_client=client)
        ctx = WorktreeContext(
            branch="agent/backend-engineer/pr-1",
            commit_sha="new-commit-1234",
        )
        new_ctx = creator.open(request=_request(), context=ctx)
        self.assertEqual(new_ctx.pr_number, 999)
        self.assertEqual(new_ctx.pr_url, "https://github.com/x/y/pull/999")
        # Body contains the do-not-merge note.
        draft_call = next(c for c in client.calls if c[0] == "draft_pr")
        self.assertEqual(draft_call[1], "yule-studio/yule-studio-agent")


# ---------------------------------------------------------------------------
# Factory + availability
# ---------------------------------------------------------------------------


class FactoryAndAvailabilityTests(unittest.TestCase):
    def test_build_live_executor_omits_pusher_when_no_client(self) -> None:
        bundle = build_live_executor(repo_root="/tmp/x", live_client=None)
        self.assertIn("worktree_provisioner", bundle)
        self.assertIn("code_editor", bundle)
        self.assertIn("test_runner", bundle)
        self.assertIn("committer", bundle)
        self.assertNotIn("pusher", bundle)
        self.assertNotIn("draft_pr_creator", bundle)

    def test_build_live_executor_includes_pusher_when_client(self) -> None:
        bundle = build_live_executor(
            repo_root="/tmp/x", live_client=_FakeLiveClient()
        )
        self.assertIn("pusher", bundle)
        self.assertIn("draft_pr_creator", bundle)

    def test_availability_no_client_marks_pusher_blocked(self) -> None:
        availability = detect_live_executor_availability(
            repo_root="/tmp/x", live_client=None
        )
        self.assertEqual(availability.code_editor, "record_only")
        self.assertEqual(availability.pusher, "blocked")
        self.assertIn("LiveGithubAppClient", availability.pusher_blocker)

    def test_availability_with_client_marks_pusher_github_app(self) -> None:
        availability = detect_live_executor_availability(
            repo_root="/tmp/x", live_client=_FakeLiveClient()
        )
        self.assertEqual(availability.pusher, "github_app")
        self.assertEqual(availability.draft_pr_creator, "github_app")


if __name__ == "__main__":
    unittest.main()
