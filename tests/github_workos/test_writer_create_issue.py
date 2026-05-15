"""GithubWriter.create_issue + LiveGithubAppClient.create_issue 회귀 테스트.

contract:
  - dry_run 기본 → 클라이언트 호출 없음, audit 는 OUTCOME_DRY_RUN
  - dry_run=False + live=True + 정책 허용 → 클라이언트 호출 + 응답 dict
    에서 number/html_url 추출
  - 정책 거부 (예: repo allow-list 위반) → OUTCOME_DENIED_BY_POLICY +
    클라이언트 호출 없음
  - 레이블/담당자 정규화: 공백 strip + 빈 항목 제거
  - LiveGithubAppClient.create_issue 가 ``/repos/{repo}/issues`` POST 와
    title/body/labels/assignees payload 를 보낸다 (빈 시퀀스는 payload
    에서 생략)
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List, Mapping, Sequence, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.github_workos.audit import (
    ACTION_GITHUB_ISSUE_CREATE,
    OUTCOME_DENIED_BY_POLICY,
    OUTCOME_DRY_RUN,
    OUTCOME_OK,
)
from yule_orchestrator.agents.github_workos.github_writer import (
    GithubWriter,
    PolicyGateDecision,
    make_default_policy_gate,
)


class _RecordingClient:
    def __init__(self, *, issue_response: Mapping[str, Any] | None = None) -> None:
        self.calls: List[Tuple[str, dict]] = []
        self._issue = issue_response or {
            "url": "https://api.github.com/repos/owner/repo/issues/77",
            "html_url": "https://github.com/owner/repo/issues/77",
            "number": 77,
            "status": 201,
        }

    # full Protocol shape — only create_issue is exercised here
    def create_issue_comment(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    def create_issue(self, *, repo, title, body, labels=(), assignees=()):
        self.calls.append(
            (
                "create_issue",
                {
                    "repo": repo,
                    "title": title,
                    "body": body,
                    "labels": tuple(labels),
                    "assignees": tuple(assignees),
                },
            )
        )
        return dict(self._issue)

    def add_labels(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    def create_branch_ref(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    def create_commit_via_data_api(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    def create_draft_pull_request(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError


class WriterCreateIssueDryRunTests(unittest.TestCase):
    def test_default_dry_run_blocks_client_call(self) -> None:
        client = _RecordingClient()
        writer = GithubWriter(client=client)
        result = writer.create_issue(
            repo="owner/repo",
            title="[Feat] 회원가입",
            body="요약",
            labels=("✨ Feature",),
            assignees=("codwithyc",),
            session_id="sess-1",
        )
        self.assertEqual(result.outcome, OUTCOME_DRY_RUN)
        self.assertEqual(client.calls, [])
        self.assertTrue(result.audit.dry_run)
        self.assertEqual(result.audit.action, ACTION_GITHUB_ISSUE_CREATE)


class WriterCreateIssuePolicyTests(unittest.TestCase):
    def test_denied_repo_blocks_call(self) -> None:
        client = _RecordingClient()
        gate = make_default_policy_gate(allowed_repos=("owner/another",))
        writer = GithubWriter(
            client=client, dry_run=False, live=True, policy_gate=gate
        )
        result = writer.create_issue(
            repo="owner/repo",
            title="t",
            body="b",
        )
        self.assertEqual(result.outcome, OUTCOME_DENIED_BY_POLICY)
        self.assertEqual(client.calls, [])
        self.assertEqual(result.audit.outcome, OUTCOME_DENIED_BY_POLICY)


class WriterCreateIssueLiveTests(unittest.TestCase):
    def test_live_create_issue_calls_client_and_records_audit(self) -> None:
        client = _RecordingClient()
        writer = GithubWriter(
            client=client,
            dry_run=False,
            live=True,
            policy_gate=make_default_policy_gate(),
        )
        result = writer.create_issue(
            repo="owner/repo",
            title="[Feat] 회원가입",
            body="설명",
            labels=(" ✨ Feature ", "", "📃 Docs"),
            assignees=("codwithyc",),
            session_id="sess-99",
        )
        self.assertEqual(result.outcome, OUTCOME_OK)
        self.assertEqual(len(client.calls), 1)
        kind, kwargs = client.calls[0]
        self.assertEqual(kind, "create_issue")
        self.assertEqual(kwargs["repo"], "owner/repo")
        # cleaned labels: strip + drop empty
        self.assertEqual(kwargs["labels"], ("✨ Feature", "📃 Docs"))
        self.assertEqual(kwargs["assignees"], ("codwithyc",))
        # audit body carries the redacted response
        self.assertIn("html_url", result.body)
        self.assertEqual(result.body["number"], 77)
        self.assertEqual(result.audit.action, ACTION_GITHUB_ISSUE_CREATE)
        self.assertFalse(result.audit.dry_run)
        self.assertEqual(result.audit.session_id, "sess-99")
        # references list captures the issue URL (writer prefers `url` field
        # for parity with other actions; `html_url` is the body link)
        self.assertIn(
            "https://api.github.com/repos/owner/repo/issues/77",
            result.audit.references,
        )


# ---------------------------------------------------------------------------
# LiveGithubAppClient — payload shape
# ---------------------------------------------------------------------------


_FAKE_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAJtest+placeholder=\n"
    "-----END RSA PRIVATE KEY-----\n"
).encode("utf-8")


class _FakeHTTP:
    """Minimal HTTPClient stub matching the contract used elsewhere."""

    def __init__(self) -> None:
        self.posted: List[Dict[str, Any]] = []
        self.gotten: List[Dict[str, Any]] = []

    def post(self, url, *, headers, body):
        from yule_orchestrator.github_app.client import HTTPResponse

        self.posted.append({"url": url, "headers": dict(headers), "body": dict(body)})
        # Token mint endpoint — return a fake installation token.
        if url.endswith("/access_tokens"):
            return HTTPResponse(
                status=201,
                body={"token": "tok", "expires_at": "2030-01-01T00:00:00Z"},
            )
        if url.endswith("/issues"):
            return HTTPResponse(
                status=201,
                body={
                    "number": 11,
                    "html_url": "https://github.com/owner/repo/issues/11",
                },
            )
        return HTTPResponse(status=201, body={})

    def get(self, url, *, headers):  # pragma: no cover - unused here
        from yule_orchestrator.github_app.client import HTTPResponse

        self.gotten.append({"url": url, "headers": dict(headers)})
        return HTTPResponse(status=200, body={})


class _NullSigner:
    def sign(self, message: bytes, private_key: bytes) -> bytes:  # noqa: ARG002
        return b"signed"


def _build_live_client(http: _FakeHTTP):
    from yule_orchestrator.github_app.live_client import LiveGithubAppClient
    from yule_orchestrator.github_app.config import GitHubAppConfig

    cfg = GitHubAppConfig(
        app_id="123",
        installation_id="456",
        private_key_path="/dev/null",
        owner="owner",
        repo="repo",
    )
    return LiveGithubAppClient(
        config=cfg,
        http=http,
        signer=_NullSigner(),
        private_key_bytes=_FAKE_PEM,
    )


class LiveGithubAppClientIssueTests(unittest.TestCase):
    def test_create_issue_posts_to_issues_endpoint_with_labels(self) -> None:
        http = _FakeHTTP()
        client = _build_live_client(http)
        response = client.create_issue(
            repo="owner/repo",
            title="[Feat] X",
            body="body",
            labels=("✨ Feature", "📃 Docs"),
            assignees=("codwithyc",),
        )
        self.assertEqual(response["number"], 11)
        # Locate the issue POST (one for token mint + one for issue create)
        issue_reqs = [r for r in http.posted if r["url"].endswith("/repos/owner/repo/issues")]
        self.assertEqual(len(issue_reqs), 1)
        body = issue_reqs[0]["body"]
        self.assertEqual(body["title"], "[Feat] X")
        self.assertEqual(body["body"], "body")
        self.assertEqual(body["labels"], ["✨ Feature", "📃 Docs"])
        self.assertEqual(body["assignees"], ["codwithyc"])

    def test_create_issue_omits_empty_labels_and_assignees(self) -> None:
        http = _FakeHTTP()
        client = _build_live_client(http)
        client.create_issue(repo="owner/repo", title="t", body="b")
        body = next(
            r["body"]
            for r in http.posted
            if r["url"].endswith("/repos/owner/repo/issues")
        )
        self.assertNotIn("labels", body)
        self.assertNotIn("assignees", body)


if __name__ == "__main__":
    unittest.main()
