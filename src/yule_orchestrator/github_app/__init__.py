"""GitHub App config / auth / doctor — A-G1.

Provides the env contract reader, JWT builder, installation-token
client, and offline / live config doctor used by the engineering-
agent's GitHub WorkOS surface (G2 triage / G3 executor / G4 Discord
bridge / G5 e2e harness).

Design contract:

  * Production code never prints pem contents, JWT tokens, or
    Authorization headers. Every dataclass repr / error message
    redacts secrets at the boundary.
  * Live HTTP only when the caller passes ``live=True``. The
    default surface is offline-safe so unit tests stay fast and
    deterministic.
  * Signer / HTTP client are :class:`Protocol` injection seams so
    tests pass fake implementations and production lazy-loads
    ``cryptography`` only when actually signing.
  * Placeholder app ids (``123456`` etc.) are recognised so the
    doctor refuses to live-call against an example value.

CLI surface lives in G6; this module exposes only the importable
core.
"""

from __future__ import annotations

from .auth import (
    GITHUB_APP_JWT_ALGORITHM,
    GITHUB_APP_JWT_TTL_SECONDS,
    GitHubAppDependencyError,
    GitHubAppSigner,
    GitHubAppSigningError,
    JWTClaims,
    build_jwt,
)
from .client import (
    GitHubAppAuthError,
    GitHubAppClient,
    GitHubAppHTTPError,
    GitHubAppNotFoundError,
    GitHubAppPermissionError,
    GitHubAppServerError,
    HTTPClient,
    HTTPResponse,
    InstallationToken,
    RepoAccess,
)
from .config import (
    ENV_GITHUB_APP_ID,
    ENV_GITHUB_APP_INSTALLATION_ID,
    ENV_GITHUB_APP_PRIVATE_KEY_PATH,
    ENV_GITHUB_DEFAULT_DRY_RUN,
    ENV_GITHUB_OWNER,
    ENV_GITHUB_REPO,
    PLACEHOLDER_APP_IDS,
    GitHubAppConfig,
    GitHubAppConfigError,
    PrivateKeyPathProblem,
    validate_private_key_path,
)
from .doctor import (
    CHECK_ENV_CONFIG,
    CHECK_LIVE_INSTALLATION_TOKEN,
    CHECK_LIVE_REPO_ACCESS,
    CHECK_PLACEHOLDER_APP_ID,
    CHECK_PRIVATE_KEY_LOADABLE,
    CHECK_PRIVATE_KEY_PATH,
    CHECK_STATUS_FAIL,
    CHECK_STATUS_OK,
    CHECK_STATUS_SKIP,
    CHECK_STATUS_WARN,
    DOCTOR_OVERALL_FAIL,
    DOCTOR_OVERALL_OK,
    DOCTOR_OVERALL_WARN,
    DoctorCheck,
    DoctorResult,
    doctor,
    redact_secret_like,
)


__all__ = (
    # config
    "ENV_GITHUB_APP_ID",
    "ENV_GITHUB_APP_INSTALLATION_ID",
    "ENV_GITHUB_APP_PRIVATE_KEY_PATH",
    "ENV_GITHUB_DEFAULT_DRY_RUN",
    "ENV_GITHUB_OWNER",
    "ENV_GITHUB_REPO",
    "PLACEHOLDER_APP_IDS",
    "GitHubAppConfig",
    "GitHubAppConfigError",
    "PrivateKeyPathProblem",
    "validate_private_key_path",
    # auth
    "GITHUB_APP_JWT_ALGORITHM",
    "GITHUB_APP_JWT_TTL_SECONDS",
    "GitHubAppDependencyError",
    "GitHubAppSigner",
    "GitHubAppSigningError",
    "JWTClaims",
    "build_jwt",
    # client
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
    # doctor
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
    "redact_secret_like",
)
