"""LiveGithubAppClient.list_check_runs / get_pull_request — Round 3 of #73.

Pin the new GET endpoints the CI retry orchestrator depends on:

  * /repos/{repo}/commits/{sha}/check-runs returns the per-run array
    we project to ``{name, status, conclusion, html_url}`` dicts.
  * /repos/{repo}/pulls/{number} round-trips so the orchestrator can
    read head_sha + state.
  * Auth header is *not* observable on the response (regression
    guard — the token must stay inside the adapter).
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.github_app.client import HTTPResponse
from yule_orchestrator.github_app.config import GitHubAppConfig
from yule_orchestrator.github_app.live_client import LiveGithubAppClient


# A 32-byte fake PEM-shaped key satisfies GitHubAppConfig's path
# validation when we redirect the path to a temp file. We keep the
# key bytes in-process so the live client can mint a fake token.
_FAKE_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAJtest+placeholder=\n"
    "-----END RSA PRIVATE KEY-----\n"
).encode("utf-8")


@dataclass
class _FakeHTTP:
    """Minimal HTTPClient stub recording calls + returning canned responses."""

    get_responses: List[Mapping[str, Any]] = field(default_factory=list)
    posted: List[Mapping[str, Any]] = field(default_factory=list)
    gotten: List[Mapping[str, Any]] = field(default_factory=list)

    def post(self, url, *, headers, body):
        self.posted.append({"url": url, "headers": dict(headers), "body": dict(body)})
        # Token mint endpoint — return a fake installation token.
        return HTTPResponse(status=201, body={"token": "tok", "expires_at": "2030-01-01T00:00:00Z"})

    def get(self, url, *, headers):
        idx = len(self.gotten)
        self.gotten.append({"url": url, "headers": dict(headers)})
        body = self.get_responses[idx] if idx < len(self.get_responses) else {}
        return HTTPResponse(status=200, body=dict(body))


def _build_client(http: _FakeHTTP) -> LiveGithubAppClient:
    cfg = GitHubAppConfig(
        app_id="123456",
        installation_id="999999",
        private_key_path="/dev/null",  # bypassed via private_key_bytes
        owner="owner",
        repo="name",
    )
    # Inject a no-op signer so token minting bypasses crypto.
    class _NullSigner:
        def sign(self, message: bytes, private_key: bytes) -> bytes:  # noqa: ARG002
            return b"signed"

    return LiveGithubAppClient(
        config=cfg,
        http=http,
        signer=_NullSigner(),
        private_key_bytes=_FAKE_PEM,
    )


class ListCheckRunsTests(unittest.TestCase):
    def test_returns_projected_runs(self) -> None:
        http = _FakeHTTP(
            get_responses=[
                {
                    "check_runs": [
                        {
                            "name": "lint",
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "https://x/y/runs/1",
                        },
                        {
                            "name": "test",
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://x/y/runs/2",
                        },
                    ]
                }
            ]
        )
        client = _build_client(http)
        runs = client.list_check_runs(repo="owner/name", head_sha="abc123")
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["name"], "lint")
        self.assertEqual(runs[1]["conclusion"], "failure")
        # Auth header must NOT appear in the projected payload — it
        # only flows on the wire request.
        self.assertNotIn("Authorization", runs[0])

    def test_empty_head_sha_short_circuits(self) -> None:
        http = _FakeHTTP()
        client = _build_client(http)
        runs = client.list_check_runs(repo="owner/name", head_sha="")
        self.assertEqual(runs, ())
        # No HTTP call fired.
        self.assertEqual(http.gotten, [])

    def test_missing_check_runs_key_returns_empty(self) -> None:
        http = _FakeHTTP(get_responses=[{}])
        client = _build_client(http)
        runs = client.list_check_runs(repo="owner/name", head_sha="abc")
        self.assertEqual(runs, ())

    def test_filters_non_mapping_run_entries(self) -> None:
        http = _FakeHTTP(
            get_responses=[
                {"check_runs": [{"name": "ok", "status": "completed", "conclusion": "success"}, "junk"]}
            ]
        )
        client = _build_client(http)
        runs = client.list_check_runs(repo="owner/name", head_sha="abc")
        self.assertEqual(len(runs), 1)


class GetPullRequestTests(unittest.TestCase):
    def test_round_trips_payload(self) -> None:
        http = _FakeHTTP(
            get_responses=[{"number": 99, "head": {"sha": "deadbeef"}, "state": "open"}]
        )
        client = _build_client(http)
        pr = client.get_pull_request(repo="owner/name", pr_number=99)
        self.assertEqual(pr["number"], 99)
        self.assertEqual(pr["head"]["sha"], "deadbeef")


if __name__ == "__main__":
    unittest.main()
