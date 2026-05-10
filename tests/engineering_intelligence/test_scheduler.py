"""Refresh planner — due / skipped / backoff / quota / overdue axes."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.engineering_intelligence.collector import (
    FakeSourceCollectorAdapter,
    collect_for_role_with_schedule,
)
from yule_orchestrator.agents.engineering_intelligence.models import (
    EngineeringKnowledgeItem,
    Importance,
    SourceAxis,
    SourceKind,
)
from yule_orchestrator.agents.engineering_intelligence.scheduler import (
    SourceRefreshState,
    compute_refresh_plan,
    overdue_axes_for_role,
    record_refresh_outcome,
)


def _now() -> datetime:
    # Pinned anchor so backoff math is reproducible.
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)


class ComputeRefreshPlanBasicsTests(unittest.TestCase):
    def test_never_attempted_sources_are_immediately_due(self) -> None:
        plan = compute_refresh_plan("backend-engineer", now=_now(), states={})
        # Every auto-collectable source is due in the empty-state case
        # (capped by tick quota).
        self.assertEqual(plan.role, "backend-engineer")
        self.assertGreater(len(plan.due), 0)
        for entry in plan.due:
            self.assertEqual(entry.decision, "due")
            self.assertEqual(entry.reason, "never_attempted")

    def test_review_required_sources_are_skipped_by_default(self) -> None:
        # ai-engineer carries langchain-blog: review_required=True but
        # auto_collect=True (the only seed where review_required gates
        # without auto_collect=False also gating). That's the entry the
        # planner classifies as "skipped_review_required".
        plan = compute_refresh_plan("ai-engineer", now=_now(), states={})
        skipped_review = [
            e for e in plan.skipped if e.decision == "skipped_review_required"
        ]
        self.assertTrue(skipped_review)
        self.assertIn(
            "langchain-blog", {e.source_id for e in skipped_review}
        )

    def test_review_required_can_be_opted_in(self) -> None:
        plan = compute_refresh_plan(
            "ai-engineer",
            now=_now(),
            states={},
            include_review_required=True,
            include_auto_collect_disabled=True,
            tick_quota=99,
        )
        # langchain-blog appears in due now.
        self.assertIn(
            "langchain-blog",
            {e.source_id for e in plan.due},
        )

    def test_auto_collect_disabled_sources_are_skipped_by_default(self) -> None:
        plan = compute_refresh_plan("backend-engineer", now=_now(), states={})
        skipped_disabled = [
            e
            for e in plan.skipped
            if e.decision == "skipped_auto_collect_disabled"
        ]
        # OWASP Top 10 is auto_collect=False on the backend registry.
        self.assertIn(
            "owasp-top-10", {e.source_id for e in skipped_disabled}
        )

    def test_tick_quota_caps_due(self) -> None:
        plan = compute_refresh_plan(
            "backend-engineer",
            now=_now(),
            states={},
            tick_quota=2,
        )
        self.assertEqual(len(plan.due), 2)
        # Overflow re-classified as skipped_quota.
        quota_skipped = [
            e for e in plan.skipped if e.decision == "skipped_quota"
        ]
        self.assertTrue(quota_skipped)


class FreshnessAndBackoffTests(unittest.TestCase):
    def test_fresh_success_within_interval_is_skipped(self) -> None:
        # spring-blog uses ENGINEERING_BLOG → 360 minute (6h) cadence.
        # Mark it as just-attempted; should land in skipped_fresh.
        last_iso = (_now() - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        states = {
            "spring-blog": SourceRefreshState(
                source_id="spring-blog",
                last_attempted_at=last_iso,
                last_succeeded_at=last_iso,
                last_status="success",
            )
        }
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states=states, tick_quota=99
        )
        skipped_ids = {
            e.source_id for e in plan.skipped if e.decision == "skipped_fresh"
        }
        self.assertIn("spring-blog", skipped_ids)
        self.assertNotIn(
            "spring-blog", {e.source_id for e in plan.due}
        )

    def test_failure_applies_exponential_backoff(self) -> None:
        # Same fresh anchor (5 min ago) on an RSS source whose interval
        # is 360m. With 1 prior failure, backoff = 360 × 1 = 360m → still
        # in backoff. With 0 failures (fresh success), only 5/360 has
        # passed so it's also fresh-skipped — testing the *backoff* tag.
        last_iso = (_now() - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        states = {
            "spring-blog": SourceRefreshState(
                source_id="spring-blog",
                last_attempted_at=last_iso,
                last_status="failure",
                consecutive_failures=2,  # backoff multiplier 4
            )
        }
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states=states, tick_quota=99
        )
        backoff_skipped = {
            e.source_id
            for e in plan.skipped
            if e.decision == "skipped_backoff"
        }
        self.assertIn("spring-blog", backoff_skipped)

    def test_overdue_failure_eventually_retried(self) -> None:
        # Push last_attempted way back so even ×8 backoff has elapsed.
        # 360m × 8 = 2880m = 2 days.
        last_iso = (_now() - timedelta(days=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        states = {
            "spring-blog": SourceRefreshState(
                source_id="spring-blog",
                last_attempted_at=last_iso,
                last_status="failure",
                consecutive_failures=4,
            )
        }
        plan = compute_refresh_plan(
            "backend-engineer", now=_now(), states=states, tick_quota=99
        )
        due_ids = {e.source_id for e in plan.due}
        self.assertIn("spring-blog", due_ids)
        # Due reason mentions retry.
        for entry in plan.due:
            if entry.source_id == "spring-blog":
                self.assertTrue(entry.reason.startswith("retry_after_"))


class RecordOutcomeTests(unittest.TestCase):
    def test_success_resets_failure_counter(self) -> None:
        prior = SourceRefreshState(
            source_id="x",
            last_attempted_at="2026-05-09T11:00:00Z",
            last_status="failure",
            consecutive_failures=3,
        )
        updated = record_refresh_outcome(
            prior, now=_now(), success=True, items_collected=2
        )
        self.assertEqual(updated.last_status, "success")
        self.assertEqual(updated.consecutive_failures, 0)
        self.assertEqual(updated.items_collected_last_run, 2)
        self.assertEqual(updated.last_succeeded_at, "2026-05-09T12:00:00Z")
        # Original is unchanged (frozen dataclass).
        self.assertEqual(prior.consecutive_failures, 3)

    def test_failure_increments_counter(self) -> None:
        prior = SourceRefreshState(
            source_id="x", consecutive_failures=1, last_status="failure"
        )
        updated = record_refresh_outcome(
            prior, now=_now(), success=False, notes="HTTPError"
        )
        self.assertEqual(updated.consecutive_failures, 2)
        self.assertEqual(updated.last_status, "failure")
        self.assertEqual(updated.notes, "HTTPError")
        self.assertIsNone(updated.last_succeeded_at)


class OverdueAxesTests(unittest.TestCase):
    def test_axis_with_recent_success_is_healthy(self) -> None:
        # Mark every backend axis source as successfully refreshed
        # within its interval — overdue list should be empty.
        from yule_orchestrator.agents.engineering_intelligence.source_registry import (
            role_sources,
        )

        recent_iso = (_now() - timedelta(minutes=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        states = {
            s.source_id: SourceRefreshState(
                source_id=s.source_id,
                last_attempted_at=recent_iso,
                last_succeeded_at=recent_iso,
                last_status="success",
            )
            for s in role_sources("backend-engineer")
        }
        overdue = overdue_axes_for_role(
            "backend-engineer", states=states, now=_now()
        )
        self.assertEqual(overdue, ())

    def test_axis_with_no_recent_success_is_overdue(self) -> None:
        # Empty state map → never refreshed → every axis is overdue.
        overdue = overdue_axes_for_role(
            "backend-engineer", states={}, now=_now()
        )
        # API_SCHEMA_AUTH should appear (Spring/PostgreSQL/etc seed it).
        self.assertIn(SourceAxis.API_SCHEMA_AUTH.value, overdue)


class CollectorWithScheduleTests(unittest.TestCase):
    def test_only_due_sources_get_called(self) -> None:
        # Block spring-blog with a fresh state so the adapter shouldn't
        # see it.
        last_iso = (_now() - timedelta(minutes=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        states = {
            "spring-blog": SourceRefreshState(
                source_id="spring-blog",
                last_attempted_at=last_iso,
                last_succeeded_at=last_iso,
                last_status="success",
            )
        }

        def _item(source_id: str) -> EngineeringKnowledgeItem:
            return EngineeringKnowledgeItem(
                item_id=f"{source_id}-1",
                topic_key=f"{source_id}-topic",
                title=f"item from {source_id}",
                role="backend-engineer",
                stack_tags=("test",),
                source_name=source_id,
                source_url=f"https://example.com/{source_id}",
                source_kind=SourceKind.RELEASE_NOTES,
                collected_at="2026-05-09T12:00:00Z",
                importance=Importance.MEDIUM,
            )

        adapter = FakeSourceCollectorAdapter(
            {
                "spring-blog": [_item("spring-blog")],
                "fastapi-changelog": [_item("fastapi-changelog")],
                "postgresql-release-notes": [_item("postgresql-release-notes")],
            }
        )
        result, new_states = collect_for_role_with_schedule(
            "backend-engineer",
            adapter=adapter,
            states=states,
            now=_now(),
            tick_quota=99,
        )
        self.assertNotIn("spring-blog", adapter.calls)
        self.assertIn("fastapi-changelog", adapter.calls)
        # New state row created for the visited source, with success.
        self.assertEqual(
            new_states["fastapi-changelog"].last_status, "success"
        )
        self.assertEqual(new_states["fastapi-changelog"].items_collected_last_run, 1)
        # Skipped sources surface in result.rejected.
        skipped_reasons = {r["reason"] for r in result.rejected if "source_id" in r}
        self.assertIn("skipped_fresh", skipped_reasons)


if __name__ == "__main__":
    unittest.main()
