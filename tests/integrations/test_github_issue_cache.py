from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from yule_orchestrator.integrations.calendar.cache import load_calendar_cache
from yule_orchestrator.integrations.github.cache import (
    load_cached_issue_payload,
    save_issue_payload,
)
from yule_orchestrator.integrations.github.issues import (
    GitHubIssue,
    GitHubViewerContext,
    list_open_issues,
)


class GitHubIssueCacheTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path("tests/.tmp/github-issue-cache")
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        except (FileNotFoundError, PermissionError) as exc:
            self.skipTest(f"temporary directory is not writable in this environment: {exc}")
        self.db_path = self.temp_dir / "cache.sqlite3"
        self.previous_db_path = os.environ.get("YULE_CACHE_DB_PATH")
        os.environ["YULE_CACHE_DB_PATH"] = str(self.db_path)

    def tearDown(self) -> None:
        if self.previous_db_path is None:
            os.environ.pop("YULE_CACHE_DB_PATH", None)
        else:
            os.environ["YULE_CACHE_DB_PATH"] = self.previous_db_path
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_issue_payload_round_trips_through_local_cache(self) -> None:
        issue = {
            "number": 12,
            "repository": "yule-studio/yule-studio-agent",
            "title": "Cached issue",
            "url": "https://example.com/issues/12",
            "owner": "yule-studio",
            "scope": "org:yule-studio",
        }

        save_issue_payload(
            cache_key="cache-key",
            scope_hash="scope-hash",
            ttl_seconds=60,
            payload=[issue],
        )
        payload = load_cached_issue_payload(cache_key="cache-key", ttl_seconds=60)

        self.assertEqual(payload, [issue])

    @patch("yule_orchestrator.integrations.github.cache.load_json_cache")
    def test_issue_cache_load_does_not_touch_access_time(self, load_json_cache_mock) -> None:
        load_json_cache_mock.return_value = None

        load_cached_issue_payload(cache_key="cache-key", ttl_seconds=60)

        self.assertFalse(load_json_cache_mock.call_args.kwargs["touch"])

    @patch("yule_orchestrator.integrations.calendar.cache.load_json_cache")
    def test_calendar_cache_load_does_not_touch_access_time(self, load_json_cache_mock) -> None:
        load_json_cache_mock.return_value = None

        load_calendar_cache(cache_key="cache-key", ttl_seconds=60)

        self.assertFalse(load_json_cache_mock.call_args.kwargs["touch"])

    def test_list_open_issues_uses_cached_result_before_remote_fetch(self) -> None:
        cached_issue = GitHubIssue(
            number=12,
            repository="yule-studio/yule-studio-agent",
            title="Cached issue",
            url="https://example.com/issues/12",
            owner="yule-studio",
            scope="org:yule-studio",
        )

        with patch("yule_orchestrator.integrations.github.issues.shutil.which", return_value="/opt/homebrew/bin/gh"), patch(
            "yule_orchestrator.integrations.github.issues._load_viewer_context",
            return_value=GitHubViewerContext(viewer_login="codwithyc", org_logins=("yule-studio",)),
        ), patch(
            "yule_orchestrator.integrations.github.issues._load_issue_cache",
            return_value=[cached_issue],
        ), patch(
            "yule_orchestrator.integrations.github.issues._load_stale_issue_cache",
            return_value=None,
        ), patch(
            "yule_orchestrator.integrations.github.issues.subprocess.run"
        ) as run_mock:
            issues = list_open_issues(limit=20)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].title, "Cached issue")
        run_mock.assert_not_called()

    def test_list_open_issues_saves_remote_result_on_cache_miss(self) -> None:
        remote_payload = subprocess.CompletedProcess(
            args=["gh", "search", "issues"],
            returncode=0,
            stdout='[{"number":31,"title":"Remote issue","url":"https://example.com/issues/31","repository":"yule-studio/yule-studio-agent"}]',
            stderr="",
        )

        with patch("yule_orchestrator.integrations.github.issues.shutil.which", return_value="/opt/homebrew/bin/gh"), patch(
            "yule_orchestrator.integrations.github.issues._load_viewer_context",
            return_value=GitHubViewerContext(viewer_login="codwithyc", org_logins=("yule-studio",)),
        ), patch(
            "yule_orchestrator.integrations.github.issues._load_issue_cache",
            return_value=None,
        ), patch(
            "yule_orchestrator.integrations.github.issues._load_stale_issue_cache",
            return_value=None,
        ), patch(
            "yule_orchestrator.integrations.github.issues.subprocess.run",
            return_value=remote_payload,
        ) as run_mock, patch(
            "yule_orchestrator.integrations.github.issues.save_issue_payload"
        ) as save_mock:
            issues = list_open_issues(limit=20)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].title, "Remote issue")
        run_mock.assert_called_once()
        save_mock.assert_called_once()
