from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.deliberation import (
    TechLeadSynthesis,
    synthesis_to_dict,
)
from yule_orchestrator.agents.research_pack import (
    ResearchPack,
    ResearchSource,
    pack_to_dict,
)
from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState
from yule_orchestrator.cli.obsidian import run_obsidian_sync_command


def _session(extra) -> WorkflowSession:
    return WorkflowSession(
        session_id="abc12345",
        prompt="hero 정리",
        task_type="landing-page",
        state=WorkflowState.APPROVED,
        created_at=datetime(2026, 4, 30, 9, 0),
        updated_at=datetime(2026, 4, 30, 9, 5),
        executor_role="frontend-engineer",
        extra=extra,
    )


def _pack() -> ResearchPack:
    return ResearchPack(
        title="Stripe Pricing 패턴",
        summary="hero step copy 강조",
        primary_url="https://stripe.com/pricing",
        sources=(
            ResearchSource(
                source_url="https://stripe.com/pricing",
                title="Stripe Pricing",
                author_role="engineering-agent/product-designer",
            ),
        ),
        tags=("reference",),
        created_at=datetime(2026, 4, 30, 9, 0),
    )


class ObsidianSyncCommandTestCase(unittest.TestCase):
    def test_missing_session_returns_error(self) -> None:
        with patch(
            "yule_orchestrator.cli.obsidian.load_session", return_value=None
        ):
            buf_err = io.StringIO()
            with redirect_stderr(buf_err):
                rc = run_obsidian_sync_command(
                    "ghost",
                    kind=None,
                    vault_path=None,
                    overwrite=False,
                    dry_run=False,
                )
        self.assertEqual(rc, 1)
        self.assertIn("not found", buf_err.getvalue())

    def test_session_without_pack_returns_error(self) -> None:
        session = _session(extra={})
        with patch(
            "yule_orchestrator.cli.obsidian.load_session", return_value=session
        ):
            buf_err = io.StringIO()
            with redirect_stderr(buf_err):
                rc = run_obsidian_sync_command(
                    session.session_id,
                    kind=None,
                    vault_path=None,
                    overwrite=False,
                    dry_run=False,
                )
        self.assertEqual(rc, 1)
        self.assertIn("research_pack", buf_err.getvalue())

    def test_legacy_session_without_synthesis_writes_research_note(self) -> None:
        session = _session(extra={"research_pack": pack_to_dict(_pack())})
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ):
                buf_out = io.StringIO()
                with redirect_stdout(buf_out):
                    rc = run_obsidian_sync_command(
                        session.session_id,
                        kind=None,
                        vault_path=tmp,
                        overwrite=False,
                        dry_run=False,
                    )
            self.assertEqual(rc, 0)
            written = list(Path(tmp).rglob("*.md"))
            self.assertEqual(len(written), 1)
            content = written[0].read_text(encoding="utf-8")
            # research kind, not decision
            self.assertIn("Agents/Engineering/Research", str(written[0]))
            # synthesis-only sections must NOT appear
            self.assertNotIn("## 합의안", content)
            self.assertNotIn("## 승인 필요 여부", content)

    def test_session_with_synthesis_renders_decision_sections(self) -> None:
        synthesis = TechLeadSynthesis(
            consensus="hero copy를 step별로 분할 — frontend가 즉시 반영",
            todos=("hero 카피 분할", "QA 회귀 추가"),
            open_research=("3건 이상 reference 보강",),
            user_decisions_needed=("배포 일정 확정",),
            approval_required=True,
            approval_reason="쓰기 작업 확인 필요",
        )
        session = _session(
            extra={
                "research_pack": pack_to_dict(_pack()),
                "research_synthesis": synthesis_to_dict(synthesis),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ):
                rc = run_obsidian_sync_command(
                    session.session_id,
                    kind=None,
                    vault_path=tmp,
                    overwrite=False,
                    dry_run=False,
                )
            self.assertEqual(rc, 0)
            written = list(Path(tmp).rglob("*.md"))
            self.assertEqual(len(written), 1)
            self.assertIn("Agents/Engineering/Decisions", str(written[0]))
            content = written[0].read_text(encoding="utf-8")
            for header in (
                "## 합의안",
                "## 해야 할 일",
                "## 더 조사할 것",
                "## 사용자 결정 필요",
                "## 승인 필요 여부",
            ):
                self.assertIn(header, content)
            self.assertIn("hero 카피 분할", content)
            self.assertIn("쓰기 작업 확인 필요", content)

    def test_corrupt_synthesis_payload_warns_but_still_writes(self) -> None:
        session = _session(
            extra={
                "research_pack": pack_to_dict(_pack()),
                "research_synthesis": "not a dict",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ):
                buf_err = io.StringIO()
                with redirect_stderr(buf_err):
                    rc = run_obsidian_sync_command(
                        session.session_id,
                        kind=None,
                        vault_path=tmp,
                        overwrite=False,
                        dry_run=False,
                    )
            self.assertEqual(rc, 0)
            self.assertIn("warning", buf_err.getvalue().lower())
            written = list(Path(tmp).rglob("*.md"))
            self.assertEqual(len(written), 1)
            # Falls back to research note since synthesis was unreadable
            self.assertIn("Agents/Engineering/Research", str(written[0]))

    def test_dry_run_does_not_write(self) -> None:
        session = _session(extra={"research_pack": pack_to_dict(_pack())})
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ):
                rc = run_obsidian_sync_command(
                    session.session_id,
                    kind=None,
                    vault_path=tmp,
                    overwrite=False,
                    dry_run=True,
                )
            self.assertEqual(rc, 0)
            self.assertEqual(list(Path(tmp).rglob("*.md")), [])

    def test_default_run_does_not_invoke_git(self) -> None:
        session = _session(extra={"research_pack": pack_to_dict(_pack())})
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ), patch(
                "yule_orchestrator.cli.obsidian.commit_single_file"
            ) as commit_spy, patch(
                "yule_orchestrator.cli.obsidian.find_git_repo_root"
            ) as find_spy:
                rc = run_obsidian_sync_command(
                    session.session_id,
                    kind=None,
                    vault_path=tmp,
                    overwrite=False,
                    dry_run=False,
                )
            self.assertEqual(rc, 0)
            commit_spy.assert_not_called()
            find_spy.assert_not_called()

    def test_collision_output_reflects_auto_suffix_path(self) -> None:
        session = _session(extra={"research_pack": pack_to_dict(_pack())})
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ):
                first_buf = io.StringIO()
                with redirect_stdout(first_buf):
                    rc1 = run_obsidian_sync_command(
                        session.session_id,
                        kind=None,
                        vault_path=tmp,
                        overwrite=False,
                        dry_run=False,
                    )
                self.assertEqual(rc1, 0)
                self.assertNotIn("auto-suffix", first_buf.getvalue())

                second_buf = io.StringIO()
                with redirect_stdout(second_buf):
                    rc2 = run_obsidian_sync_command(
                        session.session_id,
                        kind=None,
                        vault_path=tmp,
                        overwrite=False,
                        dry_run=False,
                    )
                self.assertEqual(rc2, 0)
                stdout = second_buf.getvalue()
                self.assertIn("_2.md", stdout)
                self.assertIn("auto-suffix", stdout)

            written = sorted(p.name for p in Path(tmp).rglob("*.md"))
            self.assertEqual(
                written,
                [
                    "2026-04-30_stripe-pricing-패턴.md",
                    "2026-04-30_stripe-pricing-패턴_2.md",
                ],
            )


def _git_available() -> bool:
    return shutil.which("git") is not None


def _init_repo(repo_root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], check=True, cwd=repo_root)
    subprocess.run(
        ["git", "config", "user.email", "obsidian-sync@example.com"],
        check=True,
        cwd=repo_root,
    )
    subprocess.run(
        ["git", "config", "user.name", "obsidian-sync test"],
        check=True,
        cwd=repo_root,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], check=True, cwd=repo_root
    )


@unittest.skipUnless(_git_available(), "git executable not on PATH")
class ObsidianSyncGitCommitTestCase(unittest.TestCase):
    def test_git_commit_in_real_repo_commits_only_target_file(self) -> None:
        session = _session(extra={"research_pack": pack_to_dict(_pack())})
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _init_repo(vault)
            unrelated = vault / "untracked.md"
            unrelated.write_text("noise\n", encoding="utf-8")

            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ):
                buf_out = io.StringIO()
                with redirect_stdout(buf_out):
                    rc = run_obsidian_sync_command(
                        session.session_id,
                        kind=None,
                        vault_path=str(vault),
                        overwrite=False,
                        dry_run=False,
                        git_commit=True,
                    )
            self.assertEqual(rc, 0)
            stdout = buf_out.getvalue()
            self.assertIn("git: committed", stdout)

            tree = subprocess.run(
                ["git", "-C", str(vault), "show", "--name-only", "--pretty=", "HEAD"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            self.assertIn("Agents/Engineering/Research/", tree)
            self.assertNotIn("untracked.md", tree)

            status = subprocess.run(
                ["git", "-C", str(vault), "status", "--porcelain"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            self.assertIn("?? untracked.md", status)

    def test_git_commit_dry_run_does_not_write_or_commit(self) -> None:
        session = _session(extra={"research_pack": pack_to_dict(_pack())})
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _init_repo(vault)

            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ):
                buf_out = io.StringIO()
                with redirect_stdout(buf_out):
                    rc = run_obsidian_sync_command(
                        session.session_id,
                        kind=None,
                        vault_path=str(vault),
                        overwrite=False,
                        dry_run=True,
                        git_commit=True,
                    )
            self.assertEqual(rc, 0)
            stdout = buf_out.getvalue()
            self.assertIn("dry-run: would write", stdout)
            self.assertIn("git: would commit", stdout)

            self.assertEqual(list(vault.rglob("*.md")), [])
            log = subprocess.run(
                ["git", "-C", str(vault), "log", "--oneline"],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(log.stdout.strip(), "")

    def test_git_commit_fails_when_vault_is_not_a_repo(self) -> None:
        session = _session(extra={"research_pack": pack_to_dict(_pack())})
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)

            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ):
                buf_err = io.StringIO()
                with redirect_stderr(buf_err):
                    rc = run_obsidian_sync_command(
                        session.session_id,
                        kind=None,
                        vault_path=str(vault),
                        overwrite=False,
                        dry_run=False,
                        git_commit=True,
                    )
            self.assertEqual(rc, 1)
            self.assertIn("not a git repository", buf_err.getvalue())
            # Note file was still written (write happens before git step)
            self.assertEqual(len(list(vault.rglob("*.md"))), 1)

    def test_git_commit_fails_when_repo_has_staged_changes(self) -> None:
        session = _session(extra={"research_pack": pack_to_dict(_pack())})
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _init_repo(vault)
            other = vault / "other.md"
            other.write_text("# other\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(vault), "add", "--", "other.md"], check=True
            )

            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ):
                buf_err = io.StringIO()
                with redirect_stderr(buf_err):
                    rc = run_obsidian_sync_command(
                        session.session_id,
                        kind=None,
                        vault_path=str(vault),
                        overwrite=False,
                        dry_run=False,
                        git_commit=True,
                    )
            self.assertEqual(rc, 1)
            self.assertIn("staged changes", buf_err.getvalue())

    def test_custom_git_message_is_used(self) -> None:
        session = _session(extra={"research_pack": pack_to_dict(_pack())})
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _init_repo(vault)

            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ):
                rc = run_obsidian_sync_command(
                    session.session_id,
                    kind=None,
                    vault_path=str(vault),
                    overwrite=False,
                    dry_run=False,
                    git_commit=True,
                    git_message="custom obsidian sync message",
                )
            self.assertEqual(rc, 0)
            log = subprocess.run(
                ["git", "-C", str(vault), "log", "-1", "--pretty=%s"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            self.assertEqual(log, "custom obsidian sync message")

    def test_git_commit_with_overwrite_replaces_and_commits(self) -> None:
        session = _session(extra={"research_pack": pack_to_dict(_pack())})
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _init_repo(vault)

            with patch(
                "yule_orchestrator.cli.obsidian.load_session", return_value=session
            ):
                rc1 = run_obsidian_sync_command(
                    session.session_id,
                    kind=None,
                    vault_path=str(vault),
                    overwrite=False,
                    dry_run=False,
                    git_commit=True,
                )
                self.assertEqual(rc1, 0)

                # Force the rendered file to differ so commit isn't a no-op
                target = next(vault.rglob("*.md"))
                target.write_text("# stale\n", encoding="utf-8")
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(vault),
                        "commit",
                        "-am",
                        "stale edit",
                    ],
                    check=True,
                )

                buf_out = io.StringIO()
                with redirect_stdout(buf_out):
                    rc2 = run_obsidian_sync_command(
                        session.session_id,
                        kind=None,
                        vault_path=str(vault),
                        overwrite=True,
                        dry_run=False,
                        git_commit=True,
                    )
            self.assertEqual(rc2, 0)
            self.assertIn("git: committed", buf_out.getvalue())
            # Three commits total: initial sync, stale edit, overwrite sync
            log = subprocess.run(
                ["git", "-C", str(vault), "log", "--oneline"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            self.assertEqual(len(log.strip().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
