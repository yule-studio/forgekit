"""Refactor — pure-function lifecycle status helpers."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle_status import (
    REPORT_STATUS_INSUFFICIENT,
    REPORT_STATUS_INTERIM,
    REPORT_STATUS_READY,
    RESEARCH_STATUS_INSUFFICIENT,
    RESEARCH_STATUS_MISSING,
    RESEARCH_STATUS_READY,
    can_generate_final_work_report,
    can_write_obsidian_record,
    compute_lifecycle_status,
    compute_report_status,
    compute_research_source_count,
    compute_research_status,
    compute_role_coverage,
    has_synthesis,
)


class _StubSession:
    def __init__(self, extra=None) -> None:
        self.extra = extra or {}


class ComputeRoleCoverageTests(unittest.TestCase):
    def test_played_subset_and_missing_subset(self) -> None:
        played, missing = compute_role_coverage(
            ("tech-lead", "ai-engineer", "qa-engineer"),
            ("ai-engineer",),
        )
        self.assertEqual(played, ("ai-engineer",))
        self.assertEqual(missing, ("tech-lead", "qa-engineer"))

    def test_empty_active_returns_empty(self) -> None:
        played, missing = compute_role_coverage((), ("tech-lead",))
        self.assertEqual(played, ())
        self.assertEqual(missing, ())

    def test_extra_played_roles_dropped(self) -> None:
        # Roles that played but aren't in the active set don't appear
        # in either return — coverage is anchored to the active list.
        played, missing = compute_role_coverage(
            ("ai-engineer",),
            ("ai-engineer", "frontend-engineer"),
        )
        self.assertEqual(played, ("ai-engineer",))
        self.assertEqual(missing, ())


class ComputeResearchStatusTests(unittest.TestCase):
    def test_pack_with_sources_yields_ready(self) -> None:
        s = _StubSession({"research_pack": {"sources": [{"url": "https://a"}]}})
        status, count, has_pack = compute_research_status(s)
        self.assertEqual(status, RESEARCH_STATUS_READY)
        self.assertEqual(count, 1)
        self.assertTrue(has_pack)

    def test_explicit_persisted_status_wins(self) -> None:
        s = _StubSession({
            "research_status": "insufficient",
            "research_source_count": 0,
        })
        status, count, has_pack = compute_research_status(s)
        self.assertEqual(status, RESEARCH_STATUS_INSUFFICIENT)
        self.assertEqual(count, 0)
        self.assertFalse(has_pack)

    def test_no_pack_yields_missing(self) -> None:
        s = _StubSession({})
        status, count, has_pack = compute_research_status(s)
        self.assertEqual(status, RESEARCH_STATUS_MISSING)
        self.assertEqual(count, 0)

    def test_pack_with_no_sources_yields_insufficient(self) -> None:
        s = _StubSession({"research_pack": {"sources": []}})
        status, count, has_pack = compute_research_status(s)
        self.assertEqual(status, RESEARCH_STATUS_INSUFFICIENT)
        self.assertEqual(count, 0)
        # has_pack True (pack dict exists) even though source_count=0;
        # the status takes care of the "has stuff to read" flag for
        # callers that need it.
        self.assertTrue(has_pack)

    def test_source_count_falls_back_to_pack_sources(self) -> None:
        s = _StubSession({
            "research_pack": {"sources": [{}, {}, {}]},
        })
        self.assertEqual(compute_research_source_count(s), 3)


class HasSynthesisTests(unittest.TestCase):
    def test_with_consensus(self) -> None:
        s = _StubSession({"research_synthesis": {"consensus": "RAG"}})
        self.assertTrue(has_synthesis(s))

    def test_empty_consensus(self) -> None:
        s = _StubSession({"research_synthesis": {"consensus": "  "}})
        self.assertFalse(has_synthesis(s))

    def test_missing(self) -> None:
        self.assertFalse(has_synthesis(_StubSession({})))


class ComputeReportStatusTests(unittest.TestCase):
    def _ready_extra(self) -> dict:
        return {
            "research_pack": {"sources": [{"url": "https://a"}]},
            "research_source_count": 5,
            "research_synthesis": {"consensus": "RAG 도입"},
            "active_research_roles": ["tech-lead", "ai-engineer"],
            "played_roles": ["tech-lead", "ai-engineer"],
        }

    def test_ready_when_all_conditions_met(self) -> None:
        s = _StubSession(self._ready_extra())
        status, missing = compute_report_status(s)
        self.assertEqual(status, REPORT_STATUS_READY)
        self.assertEqual(missing, ())

    def test_partial_coverage_yields_interim(self) -> None:
        extra = self._ready_extra()
        extra["played_roles"] = ["ai-engineer"]
        s = _StubSession(extra)
        status, missing = compute_report_status(s)
        self.assertEqual(status, REPORT_STATUS_INTERIM)
        self.assertIn("tech-lead", missing)

    def test_insufficient_research_yields_insufficient(self) -> None:
        extra = self._ready_extra()
        extra["research_pack"] = None
        extra["research_source_count"] = 0
        s = _StubSession(extra)
        status, _ = compute_report_status(s)
        self.assertEqual(status, REPORT_STATUS_INSUFFICIENT)


class GateTests(unittest.TestCase):
    def test_can_generate_final_with_full_lifecycle(self) -> None:
        s = _StubSession({
            "research_pack": {"sources": [{"url": "https://a"}]},
            "research_source_count": 5,
            "research_synthesis": {"consensus": "ok"},
            "active_research_roles": ["tech-lead", "ai-engineer"],
            "played_roles": ["tech-lead", "ai-engineer"],
        })
        ok, reason = can_generate_final_work_report(s)
        self.assertTrue(ok, reason)
        self.assertIsNone(reason)

    def test_can_generate_final_blocked_by_missing_pack(self) -> None:
        ok, reason = can_generate_final_work_report(_StubSession({}))
        self.assertFalse(ok)
        self.assertIn("research_pack", reason or "")

    def test_can_write_obsidian_blocked_by_zero_sources(self) -> None:
        s = _StubSession({"research_pack": {"sources": []}})
        ok, reason = can_write_obsidian_record(s)
        self.assertFalse(ok)
        self.assertIn("자료 0건", reason or "")

    def test_can_write_obsidian_blocked_by_interim_work_report(self) -> None:
        s = _StubSession({
            "research_pack": {"sources": [{"url": "https://a"}]},
            "research_source_count": 1,
            "work_report": {"status": "interim", "missing_roles": ["qa-engineer"]},
        })
        ok, reason = can_write_obsidian_record(s)
        self.assertFalse(ok)
        self.assertIn("qa-engineer", reason or "")

    def test_can_write_obsidian_passes_when_pack_only(self) -> None:
        # Legacy approval flow without work_report stamp — pack +
        # sources alone is enough to allow.
        s = _StubSession({"research_pack": {"sources": [{"url": "https://a"}]}})
        ok, reason = can_write_obsidian_record(s)
        self.assertTrue(ok, reason)

    def test_compute_lifecycle_status_bundles_everything(self) -> None:
        s = _StubSession({
            "research_pack": {"sources": [{"url": "https://a"}, {"url": "https://b"}]},
            "research_source_count": 2,
            "research_synthesis": {"consensus": "RAG"},
            "active_research_roles": ["tech-lead", "ai-engineer", "qa-engineer"],
            "played_roles": ["tech-lead", "ai-engineer"],
        })
        bundle = compute_lifecycle_status(s)
        self.assertEqual(bundle.research_status, RESEARCH_STATUS_READY)
        self.assertEqual(bundle.source_count, 2)
        self.assertTrue(bundle.has_research_pack)
        self.assertTrue(bundle.has_synthesis)
        self.assertEqual(bundle.report_status, REPORT_STATUS_INTERIM)
        self.assertEqual(bundle.missing_roles, ("qa-engineer",))
        self.assertTrue(bundle.can_save_obsidian)


if __name__ == "__main__":
    unittest.main()
