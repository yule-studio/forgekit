"""Phase 6 stabilisation — work_report status gate honours
``role_research_results``.

Pin the live-bug regression: a work report used to graduate to
``ready`` even when every member bot's role-scoped collection failed,
because the gate only looked at ``research_pack`` + ``played_roles``
+ ``research_synthesis``. Phase 4 records per-role outcomes onto
``session.extra['role_research_results']`` and Phase 6 makes the gate
require at least one role with status="ok" before a session can ship
``ready`` / ``final``.

Legacy sessions (no Phase 4 bucket) keep their previous behaviour so
existing flows don't regress.
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.lifecycle.status import (
    REPORT_STATUS_INTERIM,
    REPORT_STATUS_READY,
    can_generate_final_work_report,
    compute_report_status,
    has_role_research_evidence,
    missing_role_research_roles,
)
from yule_engineering.agents.reports.work_report import (
    WORK_REPORT_STATUS_INTERIM,
    WORK_REPORT_STATUS_READY,
    build_work_report,
)


class _StubSession:
    """Minimal session stand-in for status helpers — only ``.extra`` is read."""

    def __init__(self, extra: Mapping[str, Any]) -> None:
        self.extra = dict(extra)


def _ready_extra(**overrides: Any) -> dict:
    """Baseline extras that — without Phase 6 — would graduate to READY.

    Phase 6 adds ``role_research_results`` requirement on top: callers
    flip just that field per test to see which gate fires.
    """

    base: dict = {
        "research_pack": {
            "title": "stub",
            "sources": [{"url": "https://x", "title": "y"}],
        },
        "research_source_count": 1,
        "active_research_roles": ["tech-lead", "ai-engineer"],
        "played_roles": ["tech-lead", "ai-engineer"],
        "research_synthesis": {"consensus": "ok"},
    }
    base.update(overrides)
    return base


class HasRoleResearchEvidenceTests(unittest.TestCase):
    """Pure-Python predicate that powers the new gate."""

    def test_legacy_session_without_bucket_returns_true(self) -> None:
        # Bucket absent — Phase 4 didn't run. The "missing bucket → True"
        # branch keeps legacy READY paths working.
        self.assertTrue(has_role_research_evidence(_StubSession({})))

    def test_at_least_one_ok_role_returns_true(self) -> None:
        session = _StubSession(
            {
                "role_research_results": {
                    "ai-engineer": {"status": "ok", "source_count": 3},
                    "qa-engineer": {"status": "failed", "source_count": 0},
                }
            }
        )
        self.assertTrue(has_role_research_evidence(session))

    def test_all_failed_or_empty_returns_false(self) -> None:
        session = _StubSession(
            {
                "role_research_results": {
                    "ai-engineer": {"status": "failed"},
                    "qa-engineer": {"status": "empty"},
                }
            }
        )
        self.assertFalse(has_role_research_evidence(session))

    def test_missing_failed_role_research_roles_lists_failures(self) -> None:
        session = _StubSession(
            {
                "role_research_results": {
                    "ai-engineer": {"status": "ok"},
                    "qa-engineer": {"status": "failed"},
                    "devops-engineer": {"status": "empty"},
                }
            }
        )
        failed = missing_role_research_roles(session)
        # Sorted for stable diagnostic output.
        self.assertEqual(failed, ("devops-engineer", "qa-engineer"))


class ComputeReportStatusGateTests(unittest.TestCase):
    """``compute_report_status`` is the canonical gate — work_report
    pulls from it. Phase 6 makes it honour role_research_results."""

    def test_legacy_session_keeps_ready(self) -> None:
        # No role_research_results bucket — must reach READY exactly
        # like before Phase 6.
        session = _StubSession(_ready_extra())
        status, missing = compute_report_status(session)
        self.assertEqual(status, REPORT_STATUS_READY)
        self.assertEqual(missing, ())

    def test_all_failed_role_research_blocks_ready(self) -> None:
        session = _StubSession(
            _ready_extra(
                role_research_results={
                    "ai-engineer": {"status": "failed", "source_count": 0},
                }
            )
        )
        status, _missing = compute_report_status(session)
        self.assertEqual(status, REPORT_STATUS_INTERIM)

    def test_at_least_one_ok_role_research_unblocks_ready(self) -> None:
        session = _StubSession(
            _ready_extra(
                role_research_results={
                    "ai-engineer": {"status": "ok", "source_count": 4},
                    "qa-engineer": {"status": "failed", "source_count": 0},
                }
            )
        )
        status, missing = compute_report_status(session)
        self.assertEqual(status, REPORT_STATUS_READY)
        self.assertEqual(missing, ())


class CanGenerateFinalReasonTests(unittest.TestCase):
    """The user-facing reason from
    :func:`can_generate_final_work_report` must call out the role
    research failure so the operator knows which gate fires."""

    def test_failure_lists_failed_roles(self) -> None:
        session = _StubSession(
            _ready_extra(
                role_research_results={
                    "qa-engineer": {"status": "failed"},
                    "devops-engineer": {"status": "empty"},
                }
            )
        )
        ok, reason = can_generate_final_work_report(session)
        self.assertFalse(ok)
        self.assertIn("역할 연구 결과 부족", reason or "")
        self.assertIn("qa-engineer", reason or "")
        self.assertIn("devops-engineer", reason or "")


class BuildWorkReportGateTests(unittest.TestCase):
    """End-to-end through ``build_work_report`` — the dataclass status
    + approval_request must reflect Phase 6 gate decisions."""

    def _build(self, **role_research):
        extra = _ready_extra()
        if role_research:
            extra["role_research_results"] = role_research
        return build_work_report(
            session_id="x",
            canonical_prompt="harness",
            extra=extra,
        )

    def test_no_bucket_keeps_ready(self) -> None:
        report = self._build()
        self.assertEqual(report.status, WORK_REPORT_STATUS_READY)

    def test_all_failed_blocks_ready_with_role_specific_reason(self) -> None:
        report = self._build(**{
            "ai-engineer": {"status": "failed"},
            "qa-engineer": {"status": "empty"},
        })
        self.assertEqual(report.status, WORK_REPORT_STATUS_INTERIM)
        # The approval_request must explicitly tell the user *which*
        # roles need a retry — Phase 6 surfaces that so the next turn
        # isn't another silent failure.
        self.assertIn("역할 연구 결과 부족", report.approval_request or "")
        self.assertIn("ai-engineer", report.approval_request or "")
        self.assertIn("qa-engineer", report.approval_request or "")

    def test_one_ok_unblocks_ready(self) -> None:
        report = self._build(**{
            "ai-engineer": {"status": "ok", "source_count": 5},
            "qa-engineer": {"status": "failed"},
        })
        self.assertEqual(report.status, WORK_REPORT_STATUS_READY)

    def test_synthesis_missing_still_blocks_with_synthesis_reason(self) -> None:
        # Synthesis missing wins over the Phase 6 gate so the
        # approval_request points at the right blocker.
        extra = _ready_extra()
        extra.pop("research_synthesis")
        extra["role_research_results"] = {
            "ai-engineer": {"status": "ok", "source_count": 2},
        }
        report = build_work_report(
            session_id="x",
            canonical_prompt="harness",
            extra=extra,
        )
        self.assertEqual(report.status, WORK_REPORT_STATUS_INTERIM)
        self.assertIn("synthesis 미작성", report.approval_request or "")

    def test_missing_roles_take_precedence_in_message(self) -> None:
        # When both gates would fire (role coverage incomplete + role
        # research empty), the role coverage message wins so the
        # operator's first nudge is the obvious one.
        extra = _ready_extra(
            played_roles=["tech-lead"],  # ai-engineer un-played
            role_research_results={
                "ai-engineer": {"status": "failed"},
            },
        )
        report = build_work_report(
            session_id="x",
            canonical_prompt="harness",
            extra=extra,
        )
        self.assertEqual(report.status, WORK_REPORT_STATUS_INTERIM)
        self.assertIn("역할 토의 미완료", report.approval_request or "")
        self.assertIn("ai-engineer", report.approval_request or "")


if __name__ == "__main__":
    unittest.main()
