"""Phase E tests — ``yule supervisor run --once`` runtime status.

The supervisor must:

- inspect every recent workflow session via the injected loader,
- print stale/pending/blocked/failed signals from
  :func:`session_status.diagnose_session`,
- never auto-write/commit/push (Phase E is detect/report/propose only),
- exit zero even when sessions are stuck — operators want a status
  report, not a non-zero shell exit on stale work.

Tests inject synthetic ``WorkflowSession`` objects so the run is fully
network-free.
"""

from __future__ import annotations

import io
import unittest
from datetime import datetime
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState
from yule_orchestrator.cli.supervisor import (
    render_session_block,
    run_supervisor_run_once_command,
)


def _session(**overrides: Any) -> WorkflowSession:
    base = dict(
        session_id="sess-stale-1",
        prompt="결제 모듈 멱등성 검증",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=datetime(2026, 5, 5, 10, 0),
        updated_at=datetime(2026, 5, 6, 9, 0),
    )
    base.update(overrides)
    return WorkflowSession(**base)


class SupervisorRunOnceTests(unittest.TestCase):
    def test_empty_loader_returns_zero_with_info_message(self) -> None:
        out = io.StringIO()
        rc = run_supervisor_run_once_command(
            loader=lambda **_kw: (),
            out_stream=out,
        )
        self.assertEqual(rc, 0)
        self.assertIn("no workflow sessions", out.getvalue())

    def test_loader_failure_returns_two(self) -> None:
        def loader(**_kw: Any) -> Any:
            raise RuntimeError("cache offline")

        out = io.StringIO()
        rc = run_supervisor_run_once_command(loader=loader, out_stream=out)
        self.assertEqual(rc, 2)
        self.assertIn("cache offline", out.getvalue())

    def test_renders_each_session_block_with_signals(self) -> None:
        sessions = (
            # 1) research_pack but no forum thread — stale
            _session(
                session_id="sess-stale",
                extra={"research_pack": {"title": "Stripe"}},
            ),
            # 2) Obsidian write failed — failed
            _session(
                session_id="sess-failed",
                extra={
                    "research_pack": {"title": "x"},
                    "research_synthesis": {"summary": "ok"},
                    "obsidian_write_error": "vault offline",
                },
            ),
            # 3) pending Obsidian approval — blocked
            _session(
                session_id="sess-blocked",
                write_requested=True,
                write_blocked_reason="작성 승인 필요",
                extra={"research_pack": {"title": "x"}},
            ),
        )
        out = io.StringIO()
        rc = run_supervisor_run_once_command(
            loader=lambda **_kw: sessions,
            out_stream=out,
        )
        self.assertEqual(rc, 0)
        body = out.getvalue()
        for sid in ("sess-stale", "sess-failed", "sess-blocked"):
            self.assertIn(sid, body)
        self.assertIn("[STALE]", body)
        self.assertIn("[FAILED]", body)
        self.assertIn("[BLOCKED]", body)
        # Phase E guarantee — supervisor never claims to auto-execute.
        self.assertIn("detect/report/propose only", body)
        self.assertNotIn("auto-committed", body.lower())
        self.assertNotIn("auto-pushed", body.lower())
        self.assertNotIn("auto-merged", body.lower())

    def test_only_actionable_filters_out_info_only_sessions(self) -> None:
        sessions = (
            # info-only: pack missing
            _session(session_id="sess-info"),
            # actionable: write failed
            _session(
                session_id="sess-failed",
                extra={"obsidian_write_error": "vault offline"},
            ),
        )
        out = io.StringIO()
        rc = run_supervisor_run_once_command(
            loader=lambda **_kw: sessions,
            only_actionable=True,
            out_stream=out,
        )
        self.assertEqual(rc, 0)
        body = out.getvalue()
        self.assertIn("sess-failed", body)
        self.assertNotIn("sess-info", body)

    def test_summary_line_counts_actionable_vs_info(self) -> None:
        sessions = (
            _session(session_id="info1"),
            _session(
                session_id="failed1",
                extra={"obsidian_write_error": "x"},
            ),
        )
        out = io.StringIO()
        run_supervisor_run_once_command(
            loader=lambda **_kw: sessions, out_stream=out
        )
        body = out.getvalue()
        self.assertIn("summary: 1 actionable / 1 info-only", body)


class RenderSessionBlockTests(unittest.TestCase):
    def test_block_includes_pipeline_state_marks(self) -> None:
        from yule_orchestrator.agents.session_status import diagnose_session

        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 99,
                "forum_comment_mode": "member-bots",
                "forum_kickoff_posted": True,
                "team_conversation": {"played_roles": ["tech-lead"]},
            },
            role_sequence=("tech-lead", "ai-engineer", "qa-engineer"),
        )
        report = diagnose_session(session)
        lines = list(render_session_block(report, index=1))
        joined = "\n".join(lines)
        self.assertIn("research_pack=있음", joined)
        self.assertIn("forum_thread=O", joined)
        self.assertIn("played=1/3", joined)
        self.assertIn("synthesis=X", joined)


if __name__ == "__main__":
    unittest.main()
