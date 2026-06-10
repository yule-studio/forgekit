"""Phase 5 — coding_job/coding_proposal visibility in session_status.

Pin the contract that ``diagnose_session`` surfaces the new MVP fields
on the report and emits the expected signals so the diagnostic /
supervisor render the live coding-job state correctly.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.lifecycle.session_status import (
    CODING_JOB_READY,
    CODING_PROPOSAL_PENDING,
    diagnose_session,
)


@dataclass
class _StatusFakeSession:
    session_id: str
    prompt: str = ""
    task_type: str = "research"
    state: str = "in_progress"
    summary: str | None = None
    channel_id: int | None = None
    thread_id: int | None = None
    user_id: int | None = None
    updated_at: datetime | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    role_sequence: tuple = ()
    progress_notes: tuple = ()
    write_requested: bool = False
    write_blocked_reason: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CodingProposalSignalsTests(unittest.TestCase):
    def test_proposal_pending_emits_blocked_signal(self) -> None:
        session = _StatusFakeSession(
            session_id="s1",
            updated_at=_now(),
            extra={
                "coding_proposal": {
                    "executor_role": "frontend-engineer",
                    "write_scope": ["src/components/**", "src/styles/**"],
                }
            },
        )
        report = diagnose_session(session)
        self.assertTrue(report.coding_proposal_present)
        self.assertEqual(report.coding_executor_role, "frontend-engineer")
        self.assertEqual(report.coding_job_status, "pending-approval")
        self.assertEqual(report.coding_write_scope, ("src/components/**", "src/styles/**"))
        self.assertTrue(report.has_signal(CODING_PROPOSAL_PENDING))
        signal = report.signal(CODING_PROPOSAL_PENDING)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.severity, "blocked")

    def test_coding_job_ready_emits_info_signal(self) -> None:
        session = _StatusFakeSession(
            session_id="s2",
            updated_at=_now(),
            extra={
                "coding_job": {
                    "executor_role": "backend-engineer",
                    "status": "ready",
                    "write_scope": ["src/api/**"],
                    "approved_at": "2026-05-06T12:00:00+00:00",
                }
            },
        )
        report = diagnose_session(session)
        self.assertEqual(report.coding_job_status, "ready")
        self.assertEqual(report.coding_executor_role, "backend-engineer")
        self.assertEqual(report.coding_write_scope, ("src/api/**",))
        self.assertTrue(report.has_signal(CODING_JOB_READY))
        signal = report.signal(CODING_JOB_READY)
        self.assertEqual(signal.severity, "info")

    def test_no_coding_state_means_no_signal(self) -> None:
        session = _StatusFakeSession(session_id="s3", updated_at=_now())
        report = diagnose_session(session)
        self.assertFalse(report.coding_proposal_present)
        self.assertIsNone(report.coding_job_status)
        self.assertIsNone(report.coding_executor_role)
        self.assertFalse(report.has_signal(CODING_PROPOSAL_PENDING))
        self.assertFalse(report.has_signal(CODING_JOB_READY))


class DiagnosticRendererTests(unittest.TestCase):
    def test_render_summary_lists_coding_job_when_ready(self) -> None:
        from yule_engineering.agents.lifecycle.session_status import render_diagnostic_summary

        session = _StatusFakeSession(
            session_id="s4",
            updated_at=_now(),
            extra={
                "coding_job": {
                    "executor_role": "backend-engineer",
                    "status": "ready",
                    "write_scope": ["src/api/**", "src/auth/**", "tests/api/**"],
                }
            },
        )
        text = render_diagnostic_summary(diagnose_session(session))
        self.assertIn("coding_job: ready", text)
        self.assertIn("backend-engineer", text)
        self.assertIn("write_scope", text)

    def test_render_summary_marks_pending_proposal(self) -> None:
        from yule_engineering.agents.lifecycle.session_status import render_diagnostic_summary

        session = _StatusFakeSession(
            session_id="s5",
            updated_at=_now(),
            extra={
                "coding_proposal": {
                    "executor_role": "ai-engineer",
                    "write_scope": ["apps/engineering-agent/src/yule_engineering/agents/runtime/**"],
                }
            },
        )
        text = render_diagnostic_summary(diagnose_session(session))
        self.assertIn("coding_job: pending-approval", text)
        self.assertIn("ai-engineer", text)


class SupervisorRendererTests(unittest.TestCase):
    def test_render_session_block_includes_coding_job_line(self) -> None:
        from yule_engineering.cli.supervisor import render_session_block

        session = _StatusFakeSession(
            session_id="s6",
            updated_at=_now(),
            extra={
                "coding_job": {
                    "executor_role": "devops-engineer",
                    "status": "ready",
                    "write_scope": [".github/workflows/**"],
                }
            },
        )
        block = list(render_session_block(diagnose_session(session)))
        joined = "\n".join(block)
        self.assertIn("coding_job: ready", joined)
        self.assertIn("devops-engineer", joined)


class FormatStatusDiagnosticResponseTests(unittest.TestCase):
    def test_format_status_includes_coding_proposal_line(self) -> None:
        from yule_engineering.discord.engineering_conversation import (
            format_status_diagnostic_response,
        )

        session = _StatusFakeSession(
            session_id="s7",
            updated_at=_now(),
            extra={
                "coding_proposal": {
                    "executor_role": "qa-engineer",
                    "write_scope": ["tests/**"],
                }
            },
        )
        text = format_status_diagnostic_response(session)
        self.assertIn("coding_job: pending-approval", text)
        self.assertIn("qa-engineer", text)

    def test_format_status_includes_ready_coding_job_line(self) -> None:
        from yule_engineering.discord.engineering_conversation import (
            format_status_diagnostic_response,
        )

        session = _StatusFakeSession(
            session_id="s8",
            updated_at=_now(),
            extra={
                "coding_job": {
                    "executor_role": "frontend-engineer",
                    "status": "ready",
                    "write_scope": ["src/components/**", "src/styles/**", "tests/components/**"],
                }
            },
        )
        text = format_status_diagnostic_response(session)
        self.assertIn("coding_job: ready", text)
        self.assertIn("frontend-engineer", text)
        self.assertIn("src/components/**", text)


if __name__ == "__main__":
    unittest.main()
