"""GitHub App installation token + repo access client.

Two responsibilities:

  1. Exchange a JWT for an installation access token via
     ``POST /app/installations/{installation_id}/access_tokens``.
  2. Verify the installation can read the configured repo via
     ``GET /repos/{owner}/{repo}``.

Both depend on an injected :class:`HTTPClient`. The default uses
``urllib.request`` (stdlib only) so no extra deps are pulled.
Tests inject :class:`FakeHTTPClient` and never hit the network.

Status-code mapping is centralised so callers can pattern-match
on a small error class hierarchy:

  * 401 → :class:`GitHubAppAuthError`
  * 403 → :class:`GitHubAppPermissionError`
  * 404 → :class:`GitHubAppNotFoundError`
  * 5xx → :class:`GitHubAppServerError`
  * other 4xx → :class:`GitHubAppHTTPError`

Secrets:
    * Authorization headers are never logged.
    * Returned :class:`InstallationToken` redacts the token in repr.
    * Errors include the status + url (without query secrets) but
      never the request headers.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Tuple

from .auth import build_jwt
from .config import GitHubAppConfig, load_private_key_bytes


GITHUB_API_BASE: str = "https://api.github.com"
USER_AGENT: str = "yule-studio-engineering-agent/g1"
TOKEN_REDACTED: str = "<redacted>"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GitHubAppHTTPError(RuntimeError):
    """Generic HTTP failure outside the typed 401/403/404/5xx set.

    P1-P — ``body`` 도 optional kwarg 로 받는다.  옛 시그니처는 (message,
    status, url) 만 받아서 ``live_client._get`` 의 404 처리에서
    ``GitHubAppNotFoundError(msg, status=404, body=response.body)`` 호출이
    TypeError 로 떨어졌고, 그게 pr_merge_continuation_loop 의 noisy
    traceback 회귀의 직접 원인이었다.  본 클래스의 base signature 가
    SSoT 이므로 모든 subclass 가 동일 인자 contract 를 상속한다.
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


class GitHubAppAuthError(GitHubAppHTTPError):
    """401 — JWT rejected (clock skew / invalid signature / wrong app)."""


class GitHubAppPermissionError(GitHubAppHTTPError):
    """403 — installation lacks required permission for the repo."""


class GitHubAppNotFoundError(GitHubAppHTTPError):
    """404 — repo / installation not visible to this app."""


class GitHubAppServerError(GitHubAppHTTPError):
    """5xx — transient GitHub-side failure; retry candidate."""


# ---------------------------------------------------------------------------
# HTTP injection seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HTTPResponse:
    """Minimal HTTP response surface used by the client."""

    status: int
    body: Mapping[str, Any] = field(default_factory=dict)


class HTTPClient(Protocol):
    """Protocol for the HTTP layer the client depends on."""

    def post(
        self, url: str, *, headers: Mapping[str, str], body: Mapping[str, Any]
    ) -> HTTPResponse:  # pragma: no cover - Protocol
        ...

    def get(
        self, url: str, *, headers: Mapping[str, str]
    ) -> HTTPResponse:  # pragma: no cover - Protocol
        ...


class _StdlibHTTP:
    """Default HTTP client using urllib (stdlib only).

    Never logs the Authorization header. Always sets User-Agent.
    """

    timeout: float

    def __init__(self, *, timeout: float = 15.0) -> None:
        self.timeout = float(timeout)

    def post(
        self, url: str, *, headers: Mapping[str, str], body: Mapping[str, Any]
    ) -> HTTPResponse:
        return self._request("POST", url, headers=headers, body=body)

    def get(
        self, url: str, *, headers: Mapping[str, str]
    ) -> HTTPResponse:
        return self._request("GET", url, headers=headers, body=None)

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Optional[Mapping[str, Any]],
    ) -> HTTPResponse:
        data: Optional[bytes] = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url=url, data=data, method=method)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for key, value in headers.items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return HTTPResponse(
                    status=resp.status,
                    body=_safe_json_decode(resp.read()),
                )
        except urllib.error.HTTPError as exc:
            payload: Mapping[str, Any] = {}
            try:
                payload = _safe_json_decode(exc.read())
            except Exception:  # noqa: BLE001
                payload = {}
            return HTTPResponse(status=exc.code, body=payload)


def _safe_json_decode(raw: Any) -> Mapping[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except (ValueError, UnicodeDecodeError):
        return {}
    if isinstance(decoded, Mapping):
        return decoded
    return {"data": decoded}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallationToken:
    """Issued installation token + expiry.

    ``token`` is the secret. ``__repr__`` redacts it; logs / audit
    records must use :meth:`redacted_summary`.
    """

    token: str
    expires_at: str
    permissions: Mapping[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"InstallationToken(token={TOKEN_REDACTED}, "
            f"expires_at={self.expires_at!r}, "
            f"permissions_count={len(self.permissions)})"
        )

    def redacted_summary(self) -> str:
        """Operator-friendly one-liner for the doctor surface."""

        return f"<installation token expires_at={self.expires_at}>"


@dataclass(frozen=True)
class RepoAccess:
    """Subset of ``GET /repos/{owner}/{repo}`` response.

    Doctor only needs the existence + privacy + default branch hint.
    """

    full_name: str
    default_branch: str
    private: bool
    permissions: Mapping[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class GitHubAppClient:
    """Installation token + repo access client.

    The client is stateless — every call lazily mints a JWT off the
    on-disk pem (or the in-memory key bytes injected for tests).
    """

    config: GitHubAppConfig
    _http: HTTPClient
    _signer: Optional[Any]
    _jwt_builder: Any
    _private_key_bytes: Optional[bytes]
    _api_base: str

    def __init__(
        self,
        *,
        config: GitHubAppConfig,
        http: Optional[HTTPClient] = None,
        signer: Optional[Any] = None,
        jwt_builder: Optional[Any] = None,
        private_key_bytes: Optional[bytes] = None,
        api_base: str = GITHUB_API_BASE,
    ) -> None:
        self.config = config
        self._http = http if http is not None else _StdlibHTTP()
        self._signer = signer
        self._jwt_builder = jwt_builder if jwt_builder is not None else build_jwt
        self._private_key_bytes = private_key_bytes
        self._api_base = api_base.rstrip("/")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def issue_installation_token(self) -> InstallationToken:
        """POST ``/app/installations/{id}/access_tokens``."""

        jwt_token = self._mint_jwt()
        url = f"{self._api_base}/app/installations/{self.config.installation_id}/access_tokens"
        response = self._http.post(
            url,
            headers={"Authorization": f"Bearer {jwt_token}"},
            body={},
        )
        if response.status == 201:
            body = response.body or {}
            token = str(body.get("token") or "")
            if not token:
                raise GitHubAppHTTPError(
                    "GitHub returned 201 but no token field",
                    status=response.status,
                    url=url,
                )
            return InstallationToken(
                token=token,
                expires_at=str(body.get("expires_at") or ""),
                permissions={
                    str(k): str(v)
                    for k, v in (body.get("permissions") or {}).items()
                },
            )
        self._raise_for_status(response.status, url, body=response.body)
        # _raise_for_status always raises but mypy needs a return.
        raise GitHubAppHTTPError(
            f"unexpected response status {response.status}",
            status=response.status,
            url=url,
        )

    def check_repo_access(self, token: InstallationToken) -> RepoAccess:
        """GET ``/repos/{owner}/{repo}`` using *token*."""

        url = f"{self._api_base}/repos/{self.config.owner}/{self.config.repo}"
        response = self._http.get(
            url, headers={"Authorization": f"token {token.token}"}
        )
        if response.status == 200:
            body = response.body or {}
            return RepoAccess(
                full_name=str(body.get("full_name") or self.config.repo_full_name),
                default_branch=str(body.get("default_branch") or "main"),
                private=bool(body.get("private", False)),
                permissions={
                    str(k): bool(v)
                    for k, v in (body.get("permissions") or {}).items()
                },
            )
        self._raise_for_status(response.status, url, body=response.body)
        raise GitHubAppHTTPError(
            f"unexpected response status {response.status}",
            status=response.status,
            url=url,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _mint_jwt(self) -> str:
        key_bytes = self._private_key_bytes
        if key_bytes is None:
            key_bytes = load_private_key_bytes(self.config.private_key_path)
        return self._jwt_builder(
            app_id=self.config.app_id,
            private_key=key_bytes,
            signer=self._signer,
        )

    def _raise_for_status(
        self,
        status: int,
        url: str,
        *,
        body: Mapping[str, Any],
    ) -> None:
        # Body messages from GitHub may include the offending app id
        # but never the token. Still — keep the surface slim.
        message = _short_github_message(body) or f"GitHub returned status {status}"
        if status == 401:
            raise GitHubAppAuthError(message, status=status, url=url)
        if status == 403:
            raise GitHubAppPermissionError(message, status=status, url=url)
        if status == 404:
            raise GitHubAppNotFoundError(message, status=status, url=url)
        if 500 <= status < 600:
            raise GitHubAppServerError(message, status=status, url=url)
        raise GitHubAppHTTPError(message, status=status, url=url)


def _short_github_message(body: Mapping[str, Any]) -> str:
    if not isinstance(body, Mapping):
        return ""
    msg = body.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()[:200]
    return ""


__all__ = (
    "GITHUB_API_BASE",
    "GitHubAppAuthError",
    "GitHubAppClient",
    "GitHubAppHTTPError",
    "GitHubAppNotFoundError",
    "GitHubAppPermissionError",
    "GitHubAppServerError",
    "HTTPClient",
    "HTTPResponse",
    "InstallationToken",
    "RepoAccess",
    "TOKEN_REDACTED",
)
