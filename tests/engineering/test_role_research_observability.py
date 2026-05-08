"""Phase 4 stabilisation — role-scoped research observability.

Pin the live-bug regression: each member bot used to post "이어가겠다"
without any evidence of role-scoped work. The fix wires
``_collect_role_research_pack`` to persist per-role outcomes onto
``session.extra['role_research_results'][<role>]`` and append events
onto ``session.extra['role_activity_log']`` so the gateway diagnostic
can describe what each role actually did. The forum comment also
surfaces "조사 결과: N건 (provider: …)" so the user sees concrete
findings next to the role take.
"""

from __future__ import annotations

import unittest
from datetime import datetime

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


class _SessionFixture(unittest.TestCase):
    """Common base — isolates the workflow cache and seeds a session."""

    def setUp(self) -> None:  # noqa: D401 - test setup
        try:
            from tests._helpers import isolate_cache_for_test
        except ImportError:  # pragma: no cover
            from _helpers import isolate_cache_for_test  # type: ignore

        isolate_cache_for_test(self)

        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
            save_session,
        )

        now = datetime(2026, 4, 30)
        self.session = WorkflowSession(
            session_id="sess-role-obs",
            prompt="k8s ingress 운영 검토",
            task_type="research",
            state=WorkflowState.APPROVED,
            created_at=now,
            updated_at=now,
        )
        save_session(self.session)

    def _reload(self):
        from yule_orchestrator.agents.workflow_state import load_session

        return load_session("sess-role-obs")


class RecordRoleResearchResultTests(_SessionFixture):
    """``record_role_research_result`` writes a JSON-friendly record
    under ``session.extra['role_research_results'][<role>]`` so the
    gateway diagnostic can describe each role's pass after the fact."""

    def test_ok_result_persists_full_payload(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            ROLE_RESEARCH_STATUS_OK,
            record_role_research_result,
        )

        record_role_research_result(
            session_id="sess-role-obs",
            role="devops-engineer",
            query="k8s ingress 운영 검토",
            provider="tavily",
            source_count=4,
            status=ROLE_RESEARCH_STATUS_OK,
            top_findings=("Ingress NGINX 운영 가이드", "rate-limit 정책 비교"),
        )
        reloaded = self._reload()
        bucket = dict((reloaded.extra or {}).get("role_research_results") or {})
        self.assertIn("devops-engineer", bucket)
        record = bucket["devops-engineer"]
        self.assertEqual(record["provider"], "tavily")
        self.assertEqual(record["source_count"], 4)
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["query"], "k8s ingress 운영 검토")
        self.assertIn("recorded_at", record)
        # Findings must round-trip as a list of strings, capped to ≤5.
        findings = record.get("top_findings")
        self.assertIsInstance(findings, list)
        self.assertIn("Ingress NGINX 운영 가이드", findings)

    def test_failed_result_records_error(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            ROLE_RESEARCH_STATUS_FAILED,
            record_role_research_result,
        )

        record_role_research_result(
            session_id="sess-role-obs",
            role="ai-engineer",
            query="memory recall",
            provider=None,
            source_count=0,
            status=ROLE_RESEARCH_STATUS_FAILED,
            error="provider timeout",
        )
        reloaded = self._reload()
        record = (reloaded.extra or {}).get("role_research_results", {}).get(
            "ai-engineer"
        )
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "provider timeout")
        self.assertEqual(record["source_count"], 0)

    def test_repeated_result_overwrites_per_role(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            record_role_research_result,
        )

        record_role_research_result(
            session_id="sess-role-obs",
            role="devops-engineer",
            query="k8s",
            provider="tavily",
            source_count=2,
        )
        record_role_research_result(
            session_id="sess-role-obs",
            role="devops-engineer",
            query="k8s ingress",
            provider="brave",
            source_count=5,
        )
        reloaded = self._reload()
        record = (reloaded.extra or {}).get("role_research_results", {})[
            "devops-engineer"
        ]
        # Latest wins — the diagnostic surface stays compact.
        self.assertEqual(record["provider"], "brave")
        self.assertEqual(record["source_count"], 5)
        self.assertEqual(record["query"], "k8s ingress")

    def test_unknown_session_is_silent(self) -> None:
        # Recorder must never raise — observability must not block the
        # forum post when the session isn't in the cache yet.
        from yule_orchestrator.discord.engineering_team_runtime import (
            record_role_research_result,
        )

        record_role_research_result(
            session_id="ghost",
            role="ai-engineer",
            query="anything",
            provider="tavily",
            source_count=1,
        )


class AppendRoleActivityEventTests(_SessionFixture):
    """``append_role_activity_event`` builds the audit trail Phase 5's
    status diagnostic consumes — one structured event per turn."""

    def test_event_appended_with_timestamp_and_fields(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            ROLE_ACTIVITY_RESEARCH_COMPLETED,
            append_role_activity_event,
        )

        append_role_activity_event(
            session_id="sess-role-obs",
            role="devops-engineer",
            event_type=ROLE_ACTIVITY_RESEARCH_COMPLETED,
            fields={"provider": "tavily", "source_count": 3},
        )
        reloaded = self._reload()
        log = list((reloaded.extra or {}).get("role_activity_log") or [])
        self.assertEqual(len(log), 1)
        event = log[0]
        self.assertEqual(event["role"], "devops-engineer")
        self.assertEqual(event["event_type"], "research_completed")
        self.assertEqual(event["status"], "ok")
        self.assertEqual(event["provider"], "tavily")
        self.assertEqual(event["source_count"], 3)
        self.assertIn("timestamp", event)

    def test_log_appends_in_order(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            append_role_activity_event,
        )

        for kind in ("research_started", "research_completed"):
            append_role_activity_event(
                session_id="sess-role-obs",
                role="ai-engineer",
                event_type=kind,
            )
        reloaded = self._reload()
        log = list((reloaded.extra or {}).get("role_activity_log") or [])
        self.assertEqual(
            [e["event_type"] for e in log],
            ["research_started", "research_completed"],
        )


class RoleResearchFindingsBlockTests(unittest.TestCase):
    """The "조사 결과" block surfaced on the open-call comment must
    reflect the persisted record — counts, provider, and a few top
    findings — so the user sees concrete evidence per role.
    """

    def test_block_uses_live_record_when_provided(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            _render_role_research_findings_block,
        )

        record = {
            "status": "ok",
            "source_count": 3,
            "provider": "tavily",
            "top_findings": [
                "Ingress NGINX 운영 가이드",
                "rate-limit 정책 비교",
            ],
        }
        block = _render_role_research_findings_block(
            session=_StubSession(extra={}),
            role="devops-engineer",
            live_record=record,
        )
        self.assertIsNotNone(block)
        self.assertIn("3건", block)
        self.assertIn("tavily", block)
        self.assertIn("Ingress NGINX 운영 가이드", block)

    def test_block_falls_back_to_session_extra(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            _render_role_research_findings_block,
        )

        session = _StubSession(
            extra={
                "role_research_results": {
                    "ai-engineer": {
                        "status": "ok",
                        "source_count": 2,
                        "provider": "brave",
                    }
                }
            }
        )
        block = _render_role_research_findings_block(
            session=session,
            role="ai-engineer",
        )
        self.assertIsNotNone(block)
        self.assertIn("2건", block)
        self.assertIn("brave", block)

    def test_empty_record_renders_no_new_sources_message(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            _render_role_research_findings_block,
        )

        record = {"status": "empty", "source_count": 0, "provider": None}
        block = _render_role_research_findings_block(
            session=_StubSession(extra={}),
            role="devops-engineer",
            live_record=record,
        )
        self.assertIn("새 출처 없음", block or "")

    def test_failed_record_surfaces_error(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            _render_role_research_findings_block,
        )

        record = {
            "status": "failed",
            "source_count": 0,
            "provider": None,
            "error": "tavily timeout",
        }
        block = _render_role_research_findings_block(
            session=_StubSession(extra={}),
            role="devops-engineer",
            live_record=record,
        )
        self.assertIn("실패", block or "")
        self.assertIn("tavily timeout", block or "")

    def test_no_record_returns_none(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            _render_role_research_findings_block,
        )

        # Legacy session — no role_research_results bucket. Renderer
        # must skip silently so the comment still composes.
        self.assertIsNone(
            _render_role_research_findings_block(
                session=_StubSession(extra={}),
                role="devops-engineer",
            )
        )


class _StubSession:
    """Minimal session stand-in for the renderer's session.extra read."""

    def __init__(self, *, extra: dict) -> None:
        self.session_id = "sess-stub"
        self.extra = extra
        self.prompt = "stub prompt"
        self.task_type = "research"
        self.references_user = ()


class CollectRoleResearchPackPersistsObservabilityTests(_SessionFixture):
    """End-to-end: ``_collect_role_research_pack`` records the role
    research outcome + activity events when the collector returns a
    non-empty pack. We use the in-process collector (mock by default)
    so the test does not hit the real network.
    """

    def test_collection_persists_record_and_log(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            _collect_role_research_pack,
        )

        pack, record = _collect_role_research_pack(
            session=self.session, role="devops-engineer"
        )
        # The mock collector always returns a usable pack so the record
        # must be non-None and reflect a non-failed status.
        self.assertIsNotNone(record)
        self.assertIn(record["status"], {"ok", "empty"})

        reloaded = self._reload()
        bucket = (reloaded.extra or {}).get("role_research_results") or {}
        self.assertIn("devops-engineer", bucket)
        log = list((reloaded.extra or {}).get("role_activity_log") or [])
        # We expect at least research_started + research_completed.
        kinds = [e["event_type"] for e in log]
        self.assertIn("research_started", kinds)
        self.assertTrue(
            "research_completed" in kinds or "research_failed" in kinds,
            kinds,
        )


if __name__ == "__main__":
    unittest.main()
