"""Regression for naive-vs-aware datetime sort in list_sessions.

Older cache rows persisted ``updated_at`` as a naive ISO string while
newer rows write ``datetime.now().astimezone()`` which is aware. Mixing
both in :func:`list_sessions` previously raised
``TypeError: can't compare offset-naive and offset-aware datetimes``,
breaking ``find_latest_open_session`` and Discord engineering
continuation.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    find_latest_open_session,
    list_sessions,
    save_session,
)


def _session(*, session_id: str, updated_at: datetime) -> WorkflowSession:
    return WorkflowSession(
        session_id=session_id,
        prompt="probe",
        task_type="landing-page",
        state=WorkflowState.APPROVED,
        created_at=updated_at,
        updated_at=updated_at,
        channel_id=42,
        user_id=7,
    )


class MixedAwarenessSortTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._cache_path = Path(self._tmp.name) / "cache.sqlite3"
        self._prev = os.environ.get("YULE_CACHE_DB_PATH")
        os.environ["YULE_CACHE_DB_PATH"] = str(self._cache_path)

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("YULE_CACHE_DB_PATH", None)
        else:
            os.environ["YULE_CACHE_DB_PATH"] = self._prev

    def test_list_sessions_does_not_raise_on_mixed_awareness(self) -> None:
        legacy = _session(
            session_id="legacy-1",
            updated_at=datetime(2026, 5, 1, 9, 0),  # naive
        )
        modern = _session(
            session_id="modern-1",
            updated_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        )
        save_session(legacy)
        save_session(modern)

        sessions = list_sessions(limit=10)
        ids = {s.session_id for s in sessions}
        self.assertIn("legacy-1", ids)
        self.assertIn("modern-1", ids)

    def test_find_latest_open_session_returns_aware_when_more_recent(self) -> None:
        older_naive = _session(
            session_id="older-naive",
            updated_at=datetime(2026, 5, 1, 9, 0),
        )
        newer_aware = _session(
            session_id="newer-aware",
            updated_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        )
        save_session(older_naive)
        save_session(newer_aware)

        found = find_latest_open_session(channel_id=42, user_id=7)
        self.assertIsNotNone(found)
        self.assertEqual(found.session_id, "newer-aware")

    def test_find_latest_open_session_returns_naive_when_more_recent(self) -> None:
        older_aware = _session(
            session_id="older-aware",
            updated_at=datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc),
        )
        newer_naive = _session(
            session_id="newer-naive",
            updated_at=datetime(2026, 5, 1, 18, 0),  # naive but later UTC equivalent
        )
        save_session(older_aware)
        save_session(newer_naive)

        found = find_latest_open_session(channel_id=42, user_id=7)
        self.assertIsNotNone(found)
        self.assertEqual(found.session_id, "newer-naive")

    def test_dataclass_updated_at_not_mutated(self) -> None:
        # Confirm the fix only normalises sorting — the WorkflowSession
        # dataclasses callers receive keep their original tzinfo.
        legacy = _session(
            session_id="legacy-keep",
            updated_at=datetime(2026, 5, 1, 9, 0),
        )
        save_session(legacy)
        sessions = list_sessions(limit=10)
        self.assertTrue(sessions)
        legacy_round_trip = next(
            (s for s in sessions if s.session_id == "legacy-keep"), None
        )
        self.assertIsNotNone(legacy_round_trip)
        self.assertIsNone(legacy_round_trip.updated_at.tzinfo)


if __name__ == "__main__":
    unittest.main()
