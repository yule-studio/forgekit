from __future__ import annotations

import os
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.core.timezone import (
    YULE_TIMEZONE_ENV,
    local_tz,
    local_tz_name,
    now_local,
    to_local,
)


class TimezoneHelperTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._previous = os.environ.get(YULE_TIMEZONE_ENV)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._previous is None:
            os.environ.pop(YULE_TIMEZONE_ENV, None)
        else:
            os.environ[YULE_TIMEZONE_ENV] = self._previous

    def test_explicit_iana_zone_overrides_system(self) -> None:
        os.environ[YULE_TIMEZONE_ENV] = "America/New_York"
        self.assertEqual(local_tz(), ZoneInfo("America/New_York"))
        self.assertEqual(local_tz_name(), "America/New_York")
        self.assertEqual(now_local().tzinfo, ZoneInfo("America/New_York"))

    def test_blank_value_falls_back_to_system(self) -> None:
        os.environ[YULE_TIMEZONE_ENV] = "   "
        self.assertEqual(local_tz(), datetime.now().astimezone().tzinfo)

    def test_unset_falls_back_to_system(self) -> None:
        os.environ.pop(YULE_TIMEZONE_ENV, None)
        self.assertEqual(local_tz(), datetime.now().astimezone().tzinfo)

    def test_invalid_zone_raises(self) -> None:
        os.environ[YULE_TIMEZONE_ENV] = "Not/AZone"
        with self.assertRaises(ValueError):
            local_tz()

    def test_to_local_attaches_tz_for_naive(self) -> None:
        os.environ[YULE_TIMEZONE_ENV] = "Asia/Seoul"
        naive = datetime(2026, 4, 23, 9, 0, 0)
        result = to_local(naive)
        self.assertEqual(result.tzinfo, ZoneInfo("Asia/Seoul"))
        self.assertEqual(result.hour, 9)

    def test_to_local_converts_aware(self) -> None:
        os.environ[YULE_TIMEZONE_ENV] = "Asia/Seoul"
        utc_value = datetime(2026, 4, 23, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
        result = to_local(utc_value)
        self.assertEqual(result.tzinfo, ZoneInfo("Asia/Seoul"))
        self.assertEqual(result.hour, 9)


class DayProfileBriefingScheduleTimezoneTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._previous = os.environ.get(YULE_TIMEZONE_ENV)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._previous is None:
            os.environ.pop(YULE_TIMEZONE_ENV, None)
        else:
            os.environ[YULE_TIMEZONE_ENV] = self._previous

    def test_briefing_schedule_uses_yule_timezone(self) -> None:
        from datetime import date
        from yule_orchestrator.planning.day_profile import load_day_profile

        os.environ[YULE_TIMEZONE_ENV] = "Asia/Seoul"
        profile = load_day_profile()
        slots = profile.briefing_schedule(date(2026, 4, 23))
        for slot in slots:
            self.assertEqual(slot.send_at.tzinfo, ZoneInfo("Asia/Seoul"))


if __name__ == "__main__":
    unittest.main()
