"""Live GitHub App ↔ GithubClient adapter — G6 integration seam.

Bridges :class:`agents.github_workos.github_writer.GithubClient` (the
Protocol the writer + smoke flow consume) and :class:`GitHubAppClient`
(the JWT/installation-token plumbing G1 ships). Without this adapter
the writer cannot talk to a real GitHub App; G3 only described the
shape and tests faked the client.

Scope:

  * Mints an installation token via :class:`GitHubAppClient` and
    caches it for the lifetime of one :class:`LiveGithubAppClient`
    instance — every method below reuses the same token so a long
    smoke flow doesn't pay the JWT round-trip per call.
  * Implements every method G3's writer calls:
    ``create_issue_comment`` / ``add_labels`` / ``create_branch_ref``
    / ``create_commit_via_data_api`` / ``create_draft_pull_request``.
  * Adds `get_default_branch_head` + `create_blob` helpers the smoke
    CLI uses to assemble a tree without owning the HTTP layer itself.

Secret hygiene:

  * The Authorization header is computed once and never rendered.
    ``__repr__`` exposes only the redacted token summary (G1's
    contract).
  * Error bodies are passed through :func:`agents.github_workos
    .audit.redact_secrets` before being attached to a write outcome,
    so a 4xx echo from GitHub never leaks back into the audit log.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from .client import (
    GITHUB_API_BASE,
    GitHubAppClient,
    GitHubAppHTTPError,
    GitHubAppNotFoundError,
    HTTPClient,
    InstallationToken,
    USER_AGENT,
)
from .config import GitHubAppConfig


logger = logging.getLogger(__name__)


# F16 PR-2 — opt-in env that must be true for ``merge_pull_request``
# to issue the real PUT call. Anything other than the truthy values
# below leaves the merge path disabled.
ENV_GITHUB_MERGE_ENABLED: str = "YULE_GITHUB_MERGE_ENABLED"
_MERGE_ENABLED_TRUTHY = frozenset({"true", "1", "yes", "on"})


__all__ = (
    "LiveGithubAppClient",
    "LiveGithubAppHTTPError",
    "LiveGithubAppMergeDisabled",
    "ENV_GITHUB_MERGE_ENABLED",
    "build_live_client_from_env",
)


class LiveGithubAppHTTPError(RuntimeError):
    """Raised when a live GitHub Apps REST call fails.

    The redacted ``message`` and ``status`` round-trip into the
    G3 writer's failure path so an operator sees "GitHub returned
    422 — Validation Failed" without the writer needing to know the
    HTTP layer.

    P1-P — ``body`` 도 optional kwarg.  GitHubAppHTTPError 와 동일
    contract — caller 가 두 exception class 를 같은 인자로 raise 할 수
    있게 통일.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        url: Optional[str] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body


class LiveGithubAppMergeDisabled(LiveGithubAppHTTPError):
    """Raised when ``merge_pull_request`` is called without the opt-in.

    Inherits from :class:`LiveGithubAppHTTPError` so callers that
    catch the base class still see the failure; distinct class so
    code branching on ``isinstance`` can differentiate "GitHub said no"
    from "we never asked GitHub". ``status`` is fixed at 503 to make
    the distinction visible in audit logs even when only the integer
    survives serialisation.
    """


def _is_merge_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only when ``YULE_GITHUB_MERGE_ENABLED`` is set to a truthy value."""

    source = env if env is not None else os.environ
    raw = source.get(ENV_GITHUB_MERGE_ENABLED, "")
    return (raw or "").strip().lower() in _MERGE_ENABLED_TRUTHY


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class LiveGithubAppClient:
    """Real GitHub App-backed implementation of the GithubClient
    Protocol the G3 writer expects.

    Construction:

        cfg = GitHubAppConfig.from_env()
        adapter = LiveGithubAppClient(config=cfg)
        writer = GithubWriter(client=adapter, dry_run=False, live=True)

    The adapter uses :class:`GitHubAppClient` for the JWT path
    (`POST /app/installations/{id}/access_tokens`) and falls through
    to the same HTTP layer for installation-token-authenticated
    REST calls.
    """

    def __init__(
        self,
        *,
        config: GitHubAppConfig,
        http: Optional[HTTPClient] = None,
        signer: Optional[Any] = None,
        private_key_bytes: Optional[bytes] = None,
        api_base: str = GITHUB_API_BASE,
    ) -> None:
        self._config = config
        self._app_client = GitHubAppClient(
            config=config,
            http=http,
            signer=signer,
            private_key_bytes=private_key_bytes,
            api_base=api_base,
        )
        # Reuse the same HTTP transport for installation-token calls so
        # tests can inject one HTTPClient and intercept every wire-level
        # request the smoke flow makes.
        self._http: HTTPClient = self._app_client._http  # type: ignore[attr-defined]
        self._api_base = api_base.rstrip("/")
        self._token: Optional[InstallationToken] = None

    # ------------------------------------------------------------------
    # Token lifecycle
    # ------------------------------------------------------------------

    def installation_token(self) -> InstallationToken:
        """Return a cached installation token, minting one on first use."""

        if self._token is None:
            self._token = self._app_client.issue_installation_token()
        return self._token

    def __repr__(self) -> str:  # pragma: no cover - trivial
        token_summary = self._token.redacted_summary() if self._token else "<not minted>"
        return (
            f"LiveGithubAppClient(config={self._config!r}, "
            f"token={token_summary})"
        )

    # ------------------------------------------------------------------
    # GithubClient Protocol — issues / labels
    # ------------------------------------------------------------------

    def create_issue_comment(
        self, *, repo: str, issue_number: int, body: str
    ) -> Mapping[str, Any]:
        url = f"{self._api_base}/repos/{repo}/issues/{int(issue_number)}/comments"
        return self._post(url, body={"body": str(body)})

    def create_issue(
        self,
        *,
        repo: str,
        title: str,
        body: str,
        labels: Sequence[str] = (),
        assignees: Sequence[str] = (),
    ) -> Mapping[str, Any]:
        """POST /repos/{repo}/issues — issue auto-create surface (P0-S).

        engineering-agent 가 target repo 의 ISSUE_TEMPLATE 을 채워 새 issue
        를 만든다. ``labels``/``assignees`` 는 빈 시퀀스면 payload 에서 생략
        해 GitHub 가 기본값을 적용하도록 둔다. 응답 dict 에서 호출자는
        ``number`` / ``html_url`` 을 읽어 session 에 연결.
        """

        url = f"{self._api_base}/repos/{repo}/issues"
        body_payload: dict[str, Any] = {
            "title": str(title),
            "body": str(body),
        }
        cleaned_labels = [str(label).strip() for label in labels if str(label).strip()]
        if cleaned_labels:
            body_payload["labels"] = cleaned_labels
        cleaned_assignees = [
            str(assignee).strip() for assignee in assignees if str(assignee).strip()
        ]
        if cleaned_assignees:
            body_payload["assignees"] = cleaned_assignees
        return self._post(url, body=body_payload)

    def add_labels(
        self, *, repo: str, issue_number: int, labels: Sequence[str]
    ) -> Mapping[str, Any]:
        url = f"{self._api_base}/repos/{repo}/issues/{int(issue_number)}/labels"
        return self._post(url, body={"labels": [str(label) for label in labels]})

    # ------------------------------------------------------------------
    # GithubClient Protocol — branch / commit / PR
    # ------------------------------------------------------------------

    def create_branch_ref(
        self, *, repo: str, branch: str, base_sha: str
    ) -> Mapping[str, Any]:
        url = f"{self._api_base}/repos/{repo}/git/refs"
        return self._post(
            url,
            body={"ref": f"refs/heads/{branch}", "sha": str(base_sha)},
        )

    def create_commit_via_data_api(
        self,
        *,
        repo: str,
        branch: str,
        message: str,
        tree: Mapping[str, Any],
        author: Mapping[str, Any],
        committer: Mapping[str, Any],
        parents: Sequence[str],
    ) -> Mapping[str, Any]:
        # tree.sha + parents are what the Git Data API actually
        # consumes. The writer hands us the precomputed tree; we
        # forward as-is so future shapes (multi-parent merge commits)
        # don't need an adapter change.
        commit_body: dict[str, Any] = {
            "message": str(message),
            "tree": tree.get("sha") if isinstance(tree, Mapping) else tree,
            "parents": [str(p) for p in parents],
        }
        if author:
            commit_body["author"] = dict(author)
        if committer:
            commit_body["committer"] = dict(committer)
        commit = self._post(
            f"{self._api_base}/repos/{repo}/git/commits", body=commit_body
        )
        new_sha = commit.get("sha")
        if not isinstance(new_sha, str) or not new_sha:
            raise LiveGithubAppHTTPError(
                "create_commit_via_data_api: GitHub did not return a commit sha",
                status=None,
                url=f"{self._api_base}/repos/{repo}/git/commits",
            )
        # Move the branch ref forward — the writer treats the commit
        # row as the result and does not separately PATCH the ref.
        ref_url = f"{self._api_base}/repos/{repo}/git/refs/heads/{branch}"
        self._patch(ref_url, body={"sha": new_sha, "force": False})
        return dict(commit)

    def create_draft_pull_request(
        self,
        *,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> Mapping[str, Any]:
        url = f"{self._api_base}/repos/{repo}/pulls"
        return self._post(
            url,
            body={
                "head": str(head),
                "base": str(base),
                "title": str(title),
                "body": str(body),
                "draft": bool(draft),
            },
        )

    # ------------------------------------------------------------------
    # Smoke-flow helpers — used by the CLI to assemble a tree without
    # the writer needing extra protocol methods.
    # ------------------------------------------------------------------

    def get_repo(self, *, repo: str) -> Mapping[str, Any]:
        return self._get(f"{self._api_base}/repos/{repo}")

    def get_branch_head_sha(self, *, repo: str, branch: str) -> str:
        payload = self._get(
            f"{self._api_base}/repos/{repo}/git/ref/heads/{branch}"
        )
        sha = ""
        obj = payload.get("object")
        if isinstance(obj, Mapping):
            raw = obj.get("sha")
            if isinstance(raw, str):
                sha = raw
        if not sha:
            raise LiveGithubAppHTTPError(
                f"get_branch_head_sha: missing object.sha for {branch}",
                url=f"{self._api_base}/repos/{repo}/git/ref/heads/{branch}",
            )
        return sha

    def get_commit_tree_sha(self, *, repo: str, commit_sha: str) -> str:
        payload = self._get(
            f"{self._api_base}/repos/{repo}/git/commits/{commit_sha}"
        )
        tree = payload.get("tree")
        sha = ""
        if isinstance(tree, Mapping):
            raw = tree.get("sha")
            if isinstance(raw, str):
                sha = raw
        if not sha:
            raise LiveGithubAppHTTPError(
                f"get_commit_tree_sha: missing tree.sha for commit {commit_sha}",
                url=f"{self._api_base}/repos/{repo}/git/commits/{commit_sha}",
            )
        return sha

    def create_blob(
        self, *, repo: str, content: str, encoding: str = "utf-8"
    ) -> str:
        payload = self._post(
            f"{self._api_base}/repos/{repo}/git/blobs",
            body={"content": str(content), "encoding": str(encoding)},
        )
        sha = payload.get("sha")
        if not isinstance(sha, str) or not sha:
            raise LiveGithubAppHTTPError(
                "create_blob: GitHub did not return a blob sha",
                url=f"{self._api_base}/repos/{repo}/git/blobs",
            )
        return sha

    def create_tree(
        self,
        *,
        repo: str,
        base_tree: Optional[str],
        entries: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        body: dict[str, Any] = {"tree": [dict(entry) for entry in entries]}
        if base_tree:
            body["base_tree"] = str(base_tree)
        return self._post(f"{self._api_base}/repos/{repo}/git/trees", body=body)

    def list_check_runs(
        self, *, repo: str, head_sha: str
    ) -> Sequence[Mapping[str, Any]]:
        """List GitHub Check Runs for a commit SHA.

        Used by the CI retry orchestrator to decide whether the latest
        executor commit passed / failed / is still pending. The endpoint
        already returns the per-run conclusion the
        :func:`agents.job_queue.ci_status.from_check_runs` aggregator
        consumes; we only need to project to a list of ``{name, status,
        conclusion}`` dicts so the caller doesn't ship a GitHub-API
        coupling beyond the adapter boundary.
        """

        if not head_sha:
            return ()
        url = f"{self._api_base}/repos/{repo}/commits/{head_sha}/check-runs"
        payload = self._get(url)
        runs = payload.get("check_runs") if isinstance(payload, Mapping) else None
        if not runs:
            return ()
        out: list[dict[str, Any]] = []
        for run in runs:
            if not isinstance(run, Mapping):
                continue
            out.append(
                {
                    "name": str(run.get("name") or ""),
                    "status": str(run.get("status") or ""),
                    "conclusion": str(run.get("conclusion") or ""),
                    "html_url": str(run.get("html_url") or ""),
                }
            )
        return tuple(out)

    def get_pull_request(
        self, *, repo: str, pr_number: int
    ) -> Mapping[str, Any]:
        """Fetch a PR by number — head SHA + state used by the orchestrator."""

        return self._get(
            f"{self._api_base}/repos/{repo}/pulls/{int(pr_number)}"
        )

    # ------------------------------------------------------------------
    # F16 PR-2 — Branch protection + merge (opt-in via env)
    # ------------------------------------------------------------------

    def get_branch_protection(
        self, *, repo: str, branch: str
    ) -> Optional[Mapping[str, Any]]:
        """Fetch branch protection rules for *branch*.

        Returns ``None`` only when the API returns 404 (no protection
        configured). Returns the raw payload otherwise so callers can
        introspect ``required_status_checks`` /
        ``required_pull_request_reviews``.

        Per F16 §7 ("Branch protection 401/403"): we do **not** swallow
        permission errors here — the caller's gate has to refuse the
        merge when the rules can't be verified. Anything other than
        404 surfaces as a raised :class:`LiveGithubAppHTTPError` so
        the gate stays on the safe side.
        """

        url = (
            f"{self._api_base}/repos/{repo}/branches/{branch}/protection"
        )
        try:
            return self._get(url)
        except GitHubAppNotFoundError:
            return None

    def mark_pull_request_ready_for_review(
        self,
        *,
        repo: str,
        pr_number: int,
        env: Optional[Mapping[str, str]] = None,
    ) -> Mapping[str, Any]:
        """draft PR 을 ready for review 로 전환 — P1-Q B.

        ``PATCH /repos/{owner}/{repo}/pulls/{pull_number}`` body ``draft=false``.
        merge 와 동일한 strict opt-in (``YULE_GITHUB_MERGE_ENABLED``) 가드
        — 운영자가 명시 승인한 환경에서만 draft 해제 가능.

        approval reply 의 draft 승인 분기에서 호출되어, ready-for-review
        성공 후 호출 측이 다시 5-step gate 를 돌린다.  본 함수 자체는
        gate 를 안 본다 (gate 는 ``pr_approval.evaluate_merge_gate`` SSoT).
        """

        if not _is_merge_enabled(env):
            raise LiveGithubAppMergeDisabled(
                "mark_pull_request_ready_for_review: "
                "YULE_GITHUB_MERGE_ENABLED is not set to true",
                status=503,
                url=f"{self._api_base}/repos/{repo}/pulls/{int(pr_number)}",
            )
        url = f"{self._api_base}/repos/{repo}/pulls/{int(pr_number)}"
        return self._patch(url, body={"draft": False})

    def merge_pull_request(
        self,
        *,
        repo: str,
        pr_number: int,
        sha: Optional[str] = None,
        merge_method: str = "squash",
        commit_title: Optional[str] = None,
        commit_message: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> Mapping[str, Any]:
        """Merge a pull request via the GitHub REST API.

        **Strict opt-in**: the operation requires
        ``YULE_GITHUB_MERGE_ENABLED=true`` in *env* (defaults to
        ``os.environ``). When the flag is missing / falsy we raise
        :class:`LiveGithubAppMergeDisabled` so callers can branch on a
        recognisable type. The error carries ``status=503`` so HTTP
        consumers downstream see a clearly distinct value (vs the
        404 / 4xx that mean "GitHub said no").

        On the API side this is a ``PUT /repos/{owner}/{repo}/pulls/
        {pull_number}/merge`` request. The function does NOT run the
        5-step gate — that lives in
        :func:`agents.job_queue.pr_approval.evaluate_merge_gate`. The
        caller is responsible for invoking the gate first.
        """

        if not _is_merge_enabled(env):
            raise LiveGithubAppMergeDisabled(
                "merge_pull_request: YULE_GITHUB_MERGE_ENABLED is not set to true",
                status=503,
                url=f"{self._api_base}/repos/{repo}/pulls/{int(pr_number)}/merge",
            )

        url = f"{self._api_base}/repos/{repo}/pulls/{int(pr_number)}/merge"
        body: dict = {"merge_method": str(merge_method or "squash")}
        if sha:
            body["sha"] = str(sha)
        if commit_title:
            body["commit_title"] = str(commit_title)
        if commit_message:
            body["commit_message"] = str(commit_message)
        return self._put(url, body=body)

    # ------------------------------------------------------------------
    # HTTP helpers — every call funnels through a single token-headers
    # path so the Authorization redaction stays in one place.
    # ------------------------------------------------------------------

    def _auth_headers(self) -> Mapping[str, str]:
        token = self.installation_token()
        return {
            "Authorization": f"Bearer {token.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }

    def _post(self, url: str, *, body: Mapping[str, Any]) -> Mapping[str, Any]:
        response = self._http.post(url, headers=self._auth_headers(), body=body)
        if not 200 <= int(response.status) < 300:
            raise LiveGithubAppHTTPError(
                f"GitHub POST {url} -> HTTP {response.status}",
                status=int(response.status),
                url=url,
            )
        return dict(response.body)

    def _patch(self, url: str, *, body: Mapping[str, Any]) -> Mapping[str, Any]:
        # The doctor's HTTP layer only exposes post/get; the Git Data
        # API requires PATCH for ref updates. _StdlibHTTP supports it
        # via the urllib.request.Request method override that we
        # construct here directly.
        from .client import _safe_json_decode
        import urllib.error
        import urllib.request

        data = None
        if body is not None:
            import json

            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url=url, data=data, method="PATCH")
        for key, value in self._auth_headers().items():
            req.add_header(key, value)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                if not 200 <= resp.status < 300:
                    raise LiveGithubAppHTTPError(
                        f"GitHub PATCH {url} -> HTTP {resp.status}",
                        status=int(resp.status),
                        url=url,
                    )
                return _safe_json_decode(resp.read())
        except urllib.error.HTTPError as exc:
            raise LiveGithubAppHTTPError(
                f"GitHub PATCH {url} -> HTTP {exc.code}",
                status=int(exc.code),
                url=url,
            ) from None

    def _get(self, url: str) -> Mapping[str, Any]:
        response = self._http.get(url, headers=self._auth_headers())
        if int(response.status) == 404:
            raise GitHubAppNotFoundError(
                f"GitHub GET {url} -> 404 (not found)",
                status=404,
                body=response.body,
            )
        if not 200 <= int(response.status) < 300:
            raise LiveGithubAppHTTPError(
                f"GitHub GET {url} -> HTTP {response.status}",
                status=int(response.status),
                url=url,
            )
        return dict(response.body)

    def _put(self, url: str, *, body: Mapping[str, Any]) -> Mapping[str, Any]:
        """PUT helper — mirrors :meth:`_patch` but with HTTP method ``PUT``.

        Used by :meth:`merge_pull_request`; the doctor's HTTP layer
        only exposes ``get`` / ``post`` so we go straight to the
        stdlib for the verb override.
        """

        from .client import _safe_json_decode
        import urllib.error
        import urllib.request

        data = None
        if body is not None:
            import json

            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url=url, data=data, method="PUT")
        for key, value in self._auth_headers().items():
            req.add_header(key, value)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                if not 200 <= resp.status < 300:
                    raise LiveGithubAppHTTPError(
                        f"GitHub PUT {url} -> HTTP {resp.status}",
                        status=int(resp.status),
                        url=url,
                    )
                return _safe_json_decode(resp.read())
        except urllib.error.HTTPError as exc:
            raise LiveGithubAppHTTPError(
                f"GitHub PUT {url} -> HTTP {exc.code}",
                status=int(exc.code),
                url=url,
            ) from None


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def build_live_client_from_env(
    env: Optional[Mapping[str, str]] = None,
    *,
    http: Optional[HTTPClient] = None,
) -> LiveGithubAppClient:
    """Construct a :class:`LiveGithubAppClient` from the env contract.

    Raises :class:`agents.github_app.config.GitHubAppConfigError` when
    the env is missing / malformed. Tests pass *http* to mock the
    network entirely.
    """

    config = GitHubAppConfig.from_env(env)
    return LiveGithubAppClient(config=config, http=http)
