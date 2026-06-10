"""GitHub App doctor — offline + live config validation.

``doctor(live=False)`` runs config + filesystem checks only and is
safe to call during boot without any network or pem sign. It will
also refuse to escalate to the live path when the app id is a
placeholder so a forgotten env value can't become a wasted GitHub
401.

``doctor(live=True)`` adds:
  * JWT mint
  * installation token issuance
  * repo metadata fetch

Even on live=True every fetched secret (token / Authorization
header) is redacted from the result. Operators should be able to
paste the doctor output into a chat without leaking anything
sensitive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple

from .auth import (
    GitHubAppDependencyError,
    GitHubAppSigningError,
    fake_signer,
)
from .client import (
    GitHubAppAuthError,
    GitHubAppClient,
    GitHubAppHTTPError,
    GitHubAppNotFoundError,
    GitHubAppPermissionError,
    GitHubAppServerError,
    HTTPClient,
)
from .config import (
    ENV_GITHUB_APP_ID,
    ENV_GITHUB_APP_INSTALLATION_ID,
    ENV_GITHUB_APP_PRIVATE_KEY_PATH,
    ENV_GITHUB_OWNER,
    ENV_GITHUB_REPO,
    PLACEHOLDER_APP_IDS,
    GitHubAppConfig,
    GitHubAppConfigError,
    PrivateKeyPathProblem,
    load_private_key_bytes,
    validate_private_key_path,
)


# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------


CHECK_STATUS_OK: str = "ok"
CHECK_STATUS_WARN: str = "warn"
CHECK_STATUS_FAIL: str = "fail"
CHECK_STATUS_SKIP: str = "skip"

DOCTOR_OVERALL_OK: str = "ok"
DOCTOR_OVERALL_WARN: str = "warn"
DOCTOR_OVERALL_FAIL: str = "fail"


# Check ids — stable strings the CLI / tests can match on.
CHECK_ENV_CONFIG: str = "env_config"
CHECK_PLACEHOLDER_APP_ID: str = "placeholder_app_id"
CHECK_PRIVATE_KEY_PATH: str = "private_key_path"
CHECK_PRIVATE_KEY_LOADABLE: str = "private_key_loadable"
CHECK_LIVE_INSTALLATION_TOKEN: str = "live_installation_token"
CHECK_LIVE_REPO_ACCESS: str = "live_repo_access"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    message: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class DoctorResult:
    overall: str
    live: bool
    checks: Tuple[DoctorCheck, ...]

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "overall": self.overall,
            "live": self.live,
            "checks": [check.to_payload() for check in self.checks],
        }

    @property
    def has_failures(self) -> bool:
        return any(c.status == CHECK_STATUS_FAIL for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == CHECK_STATUS_WARN for c in self.checks)

    def find(self, name: str) -> Optional[DoctorCheck]:
        for check in self.checks:
            if check.name == name:
                return check
        return None


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


_SECRET_PATTERNS: Tuple[re.Pattern[str], ...] = (
    # GitHub bot tokens (ghs_, ghp_, ghu_, ghr_, gho_, ghu_)
    re.compile(r"gh[opsur]_[A-Za-z0-9]{20,}"),
    # generic Bearer / token headers
    re.compile(r"(?i)\bauthorization\s*:\s*\S+"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\btoken\s+[A-Za-z0-9._\-]{8,}"),
    # raw RSA pem block
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


def redact_secret_like(text: str) -> str:
    """Best-effort scrub of token / Authorization / pem blocks.

    Used by doctor when surfacing error messages so accidental
    inclusion of a token in a GitHub error body never leaks.
    """

    if not text:
        return ""
    out = str(text)
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("<redacted>", out)
    return out


# ---------------------------------------------------------------------------
# doctor()
# ---------------------------------------------------------------------------


def doctor(
    *,
    env: Optional[Mapping[str, str]] = None,
    live: bool = False,
    http: Optional[HTTPClient] = None,
    signer: Optional[Any] = None,
    private_key_bytes: Optional[bytes] = None,
) -> DoctorResult:
    """Run the GitHub App config / live doctor.

    *live=False* (default) avoids ALL network and ALL signing.
    *live=True* mints a JWT, requests an installation token, and
    fetches the repo metadata. Placeholder app ids are caught
    *before* the live escalation so an example value can't burn a
    GitHub 401.
    """

    checks: list[DoctorCheck] = []

    config = _check_env_config(env, checks)
    if config is None:
        return _finalise(checks, live=live)

    placeholder_failed = _check_placeholder_app_id(config, checks)
    pk_failed = _check_private_key_path(config, checks)

    if not pk_failed:
        _check_private_key_loadable(config, private_key_bytes, checks)

    if not live:
        # Hard stop here. Never call signer / network.
        return _finalise(checks, live=live)

    # Live path — only continue when the offline checks are clean
    # enough. A placeholder app id or unreadable pem is a guaranteed
    # failure on the wire so we mark the live checks as skipped
    # with a friendly hint instead of burning the request.
    if placeholder_failed:
        checks.append(
            DoctorCheck(
                name=CHECK_LIVE_INSTALLATION_TOKEN,
                status=CHECK_STATUS_SKIP,
                message=(
                    "live check skipped because app id is a placeholder "
                    "— set YULE_GITHUB_APP_ID to the real numeric id"
                ),
            )
        )
        checks.append(
            DoctorCheck(
                name=CHECK_LIVE_REPO_ACCESS,
                status=CHECK_STATUS_SKIP,
                message="live check skipped: depends on installation token",
            )
        )
        return _finalise(checks, live=live)
    if pk_failed:
        checks.append(
            DoctorCheck(
                name=CHECK_LIVE_INSTALLATION_TOKEN,
                status=CHECK_STATUS_SKIP,
                message=(
                    "live check skipped because the private key path failed — "
                    "fix the offline check first"
                ),
            )
        )
        checks.append(
            DoctorCheck(
                name=CHECK_LIVE_REPO_ACCESS,
                status=CHECK_STATUS_SKIP,
                message="live check skipped: depends on installation token",
            )
        )
        return _finalise(checks, live=live)

    token = _check_live_installation_token(
        config=config,
        http=http,
        signer=signer,
        private_key_bytes=private_key_bytes,
        checks=checks,
    )
    if token is not None:
        _check_live_repo_access(
            config=config,
            http=http,
            signer=signer,
            private_key_bytes=private_key_bytes,
            token=token,
            checks=checks,
        )
    else:
        checks.append(
            DoctorCheck(
                name=CHECK_LIVE_REPO_ACCESS,
                status=CHECK_STATUS_SKIP,
                message="live check skipped: installation token issuance failed",
            )
        )

    return _finalise(checks, live=live)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_env_config(
    env: Optional[Mapping[str, str]],
    checks: list,
) -> Optional[GitHubAppConfig]:
    try:
        config = GitHubAppConfig.from_env(env)
    except GitHubAppConfigError as exc:
        checks.append(
            DoctorCheck(
                name=CHECK_ENV_CONFIG,
                status=CHECK_STATUS_FAIL,
                message=redact_secret_like(str(exc)),
                detail={"missing_key": exc.key},
            )
        )
        return None
    checks.append(
        DoctorCheck(
            name=CHECK_ENV_CONFIG,
            status=CHECK_STATUS_OK,
            message=(
                f"config resolved — owner={config.owner}, repo={config.repo}, "
                f"default_dry_run={config.default_dry_run}"
            ),
            detail={
                "owner": config.owner,
                "repo": config.repo,
                "installation_id": config.installation_id,
                "default_dry_run": config.default_dry_run,
            },
        )
    )
    return config


def _check_placeholder_app_id(config: GitHubAppConfig, checks: list) -> bool:
    if config.is_placeholder_app_id():
        checks.append(
            DoctorCheck(
                name=CHECK_PLACEHOLDER_APP_ID,
                status=CHECK_STATUS_FAIL,
                message=(
                    f"YULE_GITHUB_APP_ID is the placeholder value {config.app_id!r} "
                    "— replace with the real numeric app id from "
                    "Discord-equivalent GitHub App settings before live use"
                ),
                detail={
                    "app_id": config.app_id,
                    "placeholders": sorted(PLACEHOLDER_APP_IDS),
                },
            )
        )
        return True
    checks.append(
        DoctorCheck(
            name=CHECK_PLACEHOLDER_APP_ID,
            status=CHECK_STATUS_OK,
            message="app id passes placeholder allow-list",
        )
    )
    return False


def _check_private_key_path(config: GitHubAppConfig, checks: list) -> bool:
    """Returns True iff a hard failure occurred."""

    try:
        validate_private_key_path(config.private_key_path)
    except PrivateKeyPathProblem as exc:
        if exc.severity == "fail":
            checks.append(
                DoctorCheck(
                    name=CHECK_PRIVATE_KEY_PATH,
                    status=CHECK_STATUS_FAIL,
                    message=redact_secret_like(str(exc)),
                    detail={"path": config.private_key_path},
                )
            )
            return True
        checks.append(
            DoctorCheck(
                name=CHECK_PRIVATE_KEY_PATH,
                status=CHECK_STATUS_WARN,
                message=redact_secret_like(str(exc)),
                detail={"path": config.private_key_path},
            )
        )
        return False
    checks.append(
        DoctorCheck(
            name=CHECK_PRIVATE_KEY_PATH,
            status=CHECK_STATUS_OK,
            message=f"pem path exists, readable, mode acceptable: {config.private_key_path}",
            detail={"path": config.private_key_path},
        )
    )
    return False


def _check_private_key_loadable(
    config: GitHubAppConfig,
    private_key_bytes: Optional[bytes],
    checks: list,
) -> None:
    """Best-effort PEM read — returns size only, never bytes."""

    if private_key_bytes is not None:
        size = len(private_key_bytes)
    else:
        try:
            data = load_private_key_bytes(config.private_key_path)
        except PrivateKeyPathProblem as exc:
            checks.append(
                DoctorCheck(
                    name=CHECK_PRIVATE_KEY_LOADABLE,
                    status=CHECK_STATUS_FAIL,
                    message=redact_secret_like(str(exc)),
                )
            )
            return
        size = len(data)
        # NB: drop the buffer immediately — it's a secret.
        del data
    checks.append(
        DoctorCheck(
            name=CHECK_PRIVATE_KEY_LOADABLE,
            status=CHECK_STATUS_OK,
            message=f"pem readable, {size} bytes",
            detail={"size_bytes": size},
        )
    )


def _check_live_installation_token(
    *,
    config: GitHubAppConfig,
    http: Optional[HTTPClient],
    signer: Optional[Any],
    private_key_bytes: Optional[bytes],
    checks: list,
) -> Optional[Any]:
    client = GitHubAppClient(
        config=config,
        http=http,
        signer=signer,
        private_key_bytes=private_key_bytes,
    )
    try:
        token = client.issue_installation_token()
    except GitHubAppAuthError as exc:
        checks.append(_live_failure(CHECK_LIVE_INSTALLATION_TOKEN, "auth", exc))
        return None
    except GitHubAppPermissionError as exc:
        checks.append(_live_failure(CHECK_LIVE_INSTALLATION_TOKEN, "permission", exc))
        return None
    except GitHubAppNotFoundError as exc:
        checks.append(_live_failure(CHECK_LIVE_INSTALLATION_TOKEN, "not_found", exc))
        return None
    except GitHubAppServerError as exc:
        checks.append(_live_failure(CHECK_LIVE_INSTALLATION_TOKEN, "server", exc))
        return None
    except (GitHubAppHTTPError, GitHubAppDependencyError, GitHubAppSigningError) as exc:
        checks.append(_live_failure(CHECK_LIVE_INSTALLATION_TOKEN, "other", exc))
        return None

    checks.append(
        DoctorCheck(
            name=CHECK_LIVE_INSTALLATION_TOKEN,
            status=CHECK_STATUS_OK,
            message=token.redacted_summary(),
            detail={"expires_at": token.expires_at},
        )
    )
    return token


def _check_live_repo_access(
    *,
    config: GitHubAppConfig,
    http: Optional[HTTPClient],
    signer: Optional[Any],
    private_key_bytes: Optional[bytes],
    token: Any,
    checks: list,
) -> None:
    client = GitHubAppClient(
        config=config,
        http=http,
        signer=signer,
        private_key_bytes=private_key_bytes,
    )
    try:
        access = client.check_repo_access(token)
    except GitHubAppAuthError as exc:
        checks.append(_live_failure(CHECK_LIVE_REPO_ACCESS, "auth", exc))
        return
    except GitHubAppPermissionError as exc:
        checks.append(_live_failure(CHECK_LIVE_REPO_ACCESS, "permission", exc))
        return
    except GitHubAppNotFoundError as exc:
        checks.append(_live_failure(CHECK_LIVE_REPO_ACCESS, "not_found", exc))
        return
    except GitHubAppServerError as exc:
        checks.append(_live_failure(CHECK_LIVE_REPO_ACCESS, "server", exc))
        return
    except GitHubAppHTTPError as exc:
        checks.append(_live_failure(CHECK_LIVE_REPO_ACCESS, "other", exc))
        return

    checks.append(
        DoctorCheck(
            name=CHECK_LIVE_REPO_ACCESS,
            status=CHECK_STATUS_OK,
            message=(
                f"repo access ok — {access.full_name}, default_branch="
                f"{access.default_branch}, private={access.private}"
            ),
            detail={
                "full_name": access.full_name,
                "default_branch": access.default_branch,
                "private": access.private,
            },
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _live_failure(name: str, kind: str, exc: Exception) -> DoctorCheck:
    return DoctorCheck(
        name=name,
        status=CHECK_STATUS_FAIL,
        message=redact_secret_like(f"{kind}: {exc}"),
        detail={
            "kind": kind,
            "status": getattr(exc, "status", None),
        },
    )


def _finalise(
    checks: Sequence[DoctorCheck],
    *,
    live: bool,
) -> DoctorResult:
    overall: str
    if any(c.status == CHECK_STATUS_FAIL for c in checks):
        overall = DOCTOR_OVERALL_FAIL
    elif any(c.status == CHECK_STATUS_WARN for c in checks):
        overall = DOCTOR_OVERALL_WARN
    else:
        overall = DOCTOR_OVERALL_OK
    return DoctorResult(overall=overall, live=live, checks=tuple(checks))


__all__ = (
    "CHECK_ENV_CONFIG",
    "CHECK_LIVE_INSTALLATION_TOKEN",
    "CHECK_LIVE_REPO_ACCESS",
    "CHECK_PLACEHOLDER_APP_ID",
    "CHECK_PRIVATE_KEY_LOADABLE",
    "CHECK_PRIVATE_KEY_PATH",
    "CHECK_STATUS_FAIL",
    "CHECK_STATUS_OK",
    "CHECK_STATUS_SKIP",
    "CHECK_STATUS_WARN",
    "DOCTOR_OVERALL_FAIL",
    "DOCTOR_OVERALL_OK",
    "DOCTOR_OVERALL_WARN",
    "DoctorCheck",
    "DoctorResult",
    "doctor",
    "fake_signer",
    "redact_secret_like",
)
