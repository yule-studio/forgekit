"""CLI issue fetch compatibility for GitHub WorkOS.

The `gh issue view --json` payload uses GraphQL-shaped keys such as
`author` and `url`, while the WorkOS triage boundary consumes
REST/webhook-shaped keys (`user`, `html_url`). Pin the adapter seam so
live issue triage does not regress on gh field names.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.cli.github_workos import _fetch_issue_payload


class GithubWorkosCliIssueFetchTests(unittest.TestCase):
    def test_fetch_issue_normalizes_gh_author_and_url_keys(self) -> None:
        payload = {
            "number": 20,
            "title": "[Feature] 코딩 에이전트",
            "body": "issue body",
            "labels": [{"name": "feature"}],
            "author": {"login": "masterway"},
            "url": "https://github.com/yule-studio/yule-studio-agent/issues/20",
            "state": "OPEN",
        }
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

        with patch("subprocess.run", return_value=completed) as run:
            result = _fetch_issue_payload(
                repo="yule-studio/yule-studio-agent",
                issue_number=20,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["user"], {"login": "masterway"})
        self.assertEqual(
            result["html_url"],
            "https://github.com/yule-studio/yule-studio-agent/issues/20",
        )
        args = run.call_args.args[0]
        requested_fields = args[args.index("--json") + 1]
        self.assertIn("author", requested_fields.split(","))
        self.assertIn("url", requested_fields.split(","))
        self.assertNotIn("user", requested_fields.split(","))
        self.assertNotIn("html_url", requested_fields.split(","))


if __name__ == "__main__":
    unittest.main()
