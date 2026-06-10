"""F16 PR-2 — merge_pull_request opt-in env gate (issue #128).

These tests pin the **safety contract** of the live client's merge
seam: without ``YULE_GITHUB_MERGE_ENABLED=true`` no PUT is ever
issued, no matter what the gate says. The other gate steps live in
:mod:`tests.job_queue.test_pr_merge_approval`; here we focus on the
env-level guard + the inheritance contract.
"""

from __future__ import annotations

import os
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.github_app.live_client import (
    ENV_GITHUB_MERGE_ENABLED,
    LiveGithubAppHTTPError,
    LiveGithubAppMergeDisabled,
    _is_merge_enabled,
)


class IsMergeEnabledTests(unittest.TestCase):
    def test_default_environ_returns_false(self) -> None:
        os.environ.pop(ENV_GITHUB_MERGE_ENABLED, None)
        self.assertFalse(_is_merge_enabled())

    def test_truthy_strings(self) -> None:
        for raw in ("true", "TRUE", "True", "1", "yes", "on"):
            with self.subTest(raw=raw):
                self.assertTrue(_is_merge_enabled({ENV_GITHUB_MERGE_ENABLED: raw}))

    def test_falsy_strings(self) -> None:
        for raw in ("false", "0", "no", "off", "", "anything-else"):
            with self.subTest(raw=raw):
                self.assertFalse(_is_merge_enabled({ENV_GITHUB_MERGE_ENABLED: raw}))

    def test_env_override_takes_precedence_over_os_environ(self) -> None:
        os.environ[ENV_GITHUB_MERGE_ENABLED] = "true"
        try:
            self.assertFalse(_is_merge_enabled({ENV_GITHUB_MERGE_ENABLED: "false"}))
            self.assertTrue(_is_merge_enabled({ENV_GITHUB_MERGE_ENABLED: "true"}))
        finally:
            os.environ.pop(ENV_GITHUB_MERGE_ENABLED, None)


class DisabledExceptionTests(unittest.TestCase):
    def test_is_subclass_of_http_error(self) -> None:
        # Callers catching the base class still trap the disabled case.
        self.assertTrue(issubclass(LiveGithubAppMergeDisabled, LiveGithubAppHTTPError))

    def test_carries_status_503_by_convention(self) -> None:
        # Live client raises with status=503 to distinguish from
        # GitHub's own 4xx/5xx responses in audit logs.
        exc = LiveGithubAppMergeDisabled("disabled", status=503, url="u")
        self.assertEqual(exc.status, 503)
        self.assertEqual(exc.url, "u")
        self.assertIn("disabled", str(exc))


if __name__ == "__main__":
    unittest.main()
