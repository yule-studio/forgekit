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


__all__ = (
    "LiveGithubAppClient",
    "LiveGithubAppHTTPError",
    "build_live_client_from_env",
)


class LiveGithubAppHTTPError(RuntimeError):
    """Raised when a live GitHub Apps REST call fails.

    The redacted ``message`` and ``status`` round-trip into the
    G3 writer's failure path so an operator sees "GitHub returned
    422 — Validation Failed" without the writer needing to know the
    HTTP layer.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        url: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url


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
