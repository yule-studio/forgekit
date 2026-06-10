"""GitHub App configuration — env contract reader.

Reads the env-var contract and builds an immutable
:class:`GitHubAppConfig`. Refuses placeholder values so the doctor
/ live token issuance can't accidentally try to authenticate
against an example app id.

Secret handling:
    * ``__repr__`` deliberately omits ``private_key_path`` value —
      the path itself is not a secret but operators have asked for
      no incidental leakage in tracebacks.
    * The pem **contents** are never read by this module. The
      caller fetches them via :func:`load_private_key_bytes` and
      must keep them out of any log surface.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Mapping, Optional


# ---------------------------------------------------------------------------
# Env contract
# ---------------------------------------------------------------------------


ENV_GITHUB_APP_ID: str = "YULE_GITHUB_APP_ID"
ENV_GITHUB_APP_INSTALLATION_ID: str = "YULE_GITHUB_APP_INSTALLATION_ID"
ENV_GITHUB_APP_PRIVATE_KEY_PATH: str = "YULE_GITHUB_APP_PRIVATE_KEY_PATH"
ENV_GITHUB_OWNER: str = "YULE_GITHUB_OWNER"
ENV_GITHUB_REPO: str = "YULE_GITHUB_REPO"
ENV_GITHUB_DEFAULT_DRY_RUN: str = "YULE_GITHUB_DEFAULT_DRY_RUN"


# Placeholder app ids that doctor / live calls must refuse.
PLACEHOLDER_APP_IDS: FrozenSet[str] = frozenset(
    {"123456", "1234567", "0", "111111", "999999"}
)


_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GitHubAppConfigError(ValueError):
    """Raised when the env contract is missing / malformed.

    Carries the offending env key so doctor / CLI can surface a
    targeted hint without exposing secrets.
    """

    def __init__(self, message: str, *, key: Optional[str] = None) -> None:
        super().__init__(message)
        self.key = key


class PrivateKeyPathProblem(GitHubAppConfigError):
    """Raised when the configured pem path fails an offline sanity
    check (missing / unreadable / loose permissions).

    ``severity`` is ``"fail"`` for missing/unreadable and ``"warn"``
    for permission concerns so doctor can map directly without
    re-classifying.
    """

    def __init__(
        self, message: str, *, severity: str = "fail", path: Optional[str] = None
    ) -> None:
        super().__init__(message, key=ENV_GITHUB_APP_PRIVATE_KEY_PATH)
        self.severity = severity
        self.path = path


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitHubAppConfig:
    """Immutable env-resolved GitHub App configuration.

    All fields are mandatory except ``default_dry_run`` (defaults
    to True so the agent never live-writes by accident).
    """

    app_id: str
    installation_id: str
    private_key_path: str
    owner: str
    repo: str
    default_dry_run: bool = True

    def is_placeholder_app_id(self) -> bool:
        return self.app_id in PLACEHOLDER_APP_IDS

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    def __repr__(self) -> str:  # pragma: no cover - trivial
        # private_key_path omitted on purpose. The path isn't a
        # secret but stack traces / repr in audit logs should not
        # leak machine layout.
        return (
            f"GitHubAppConfig(app_id={self.app_id!r}, "
            f"installation_id={self.installation_id!r}, "
            f"owner={self.owner!r}, repo={self.repo!r}, "
            f"default_dry_run={self.default_dry_run!r}, "
            f"private_key_path=<configured>)"
        )

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
    ) -> "GitHubAppConfig":
        """Build a config from *env* (defaults to ``os.environ``).

        Raises :class:`GitHubAppConfigError` with the offending env
        key whenever a mandatory value is missing / blank.
        """

        env_map: Mapping[str, str] = env if env is not None else os.environ

        app_id = _required(env_map, ENV_GITHUB_APP_ID)
        installation_id = _required(env_map, ENV_GITHUB_APP_INSTALLATION_ID)
        private_key_path = _required(env_map, ENV_GITHUB_APP_PRIVATE_KEY_PATH)
        owner = _required(env_map, ENV_GITHUB_OWNER)
        repo = _required(env_map, ENV_GITHUB_REPO)

        default_dry_run_raw = (env_map.get(ENV_GITHUB_DEFAULT_DRY_RUN) or "").strip().lower()
        if not default_dry_run_raw:
            default_dry_run = True  # safe default — never live without opt-in
        elif default_dry_run_raw in _TRUTHY:
            default_dry_run = True
        elif default_dry_run_raw in _FALSY:
            default_dry_run = False
        else:
            raise GitHubAppConfigError(
                f"{ENV_GITHUB_DEFAULT_DRY_RUN} must be one of "
                f"{sorted(_TRUTHY | _FALSY)}; got {default_dry_run_raw!r}",
                key=ENV_GITHUB_DEFAULT_DRY_RUN,
            )

        return cls(
            app_id=app_id,
            installation_id=installation_id,
            private_key_path=private_key_path,
            owner=owner,
            repo=repo,
            default_dry_run=default_dry_run,
        )


# ---------------------------------------------------------------------------
# Private key path validation (offline-only — no contents read)
# ---------------------------------------------------------------------------


def validate_private_key_path(path: str) -> None:
    """Check the pem path is sane.

    Raises :class:`PrivateKeyPathProblem` with ``severity="fail"``
    when the file is missing / unreadable / not a regular file,
    and ``severity="warn"`` when the file mode allows other-write.

    Loose other-read mode is intentionally **not** rejected —
    plenty of operators store keys with mode 644 in private home
    dirs and the doctor surfaces it as a warning, not a failure.
    """

    if not path or not str(path).strip():
        raise PrivateKeyPathProblem(
            "private key path is empty",
            severity="fail",
            path=path,
        )
    p = Path(path)
    if not p.exists():
        raise PrivateKeyPathProblem(
            f"private key path does not exist: {path}",
            severity="fail",
            path=path,
        )
    if not p.is_file():
        raise PrivateKeyPathProblem(
            f"private key path is not a regular file: {path}",
            severity="fail",
            path=path,
        )
    if not os.access(str(p), os.R_OK):
        raise PrivateKeyPathProblem(
            f"private key path is not readable by current user: {path}",
            severity="fail",
            path=path,
        )

    try:
        mode = p.stat().st_mode
    except OSError as exc:  # pragma: no cover - extremely rare
        raise PrivateKeyPathProblem(
            f"could not stat private key path: {exc}",
            severity="fail",
            path=path,
        ) from exc
    # World-writable is a hard warning. We don't fail because some
    # ops setups intentionally relax mode while debugging — but the
    # doctor must surface this loudly.
    if mode & stat.S_IWOTH:
        raise PrivateKeyPathProblem(
            f"private key path is world-writable (mode={oct(mode & 0o777)}): {path}",
            severity="warn",
            path=path,
        )
    # Group-writable on a multi-user box is a softer warn but worth
    # flagging.
    if mode & stat.S_IWGRP:
        raise PrivateKeyPathProblem(
            f"private key path is group-writable (mode={oct(mode & 0o777)}): {path}",
            severity="warn",
            path=path,
        )


def load_private_key_bytes(path: str) -> bytes:
    """Read pem bytes from *path*. Caller MUST NOT log the return.

    Runs :func:`validate_private_key_path` first; severity=warn
    problems do not block the read (consistent with doctor's
    classification).
    """

    try:
        validate_private_key_path(path)
    except PrivateKeyPathProblem as exc:
        if exc.severity == "fail":
            raise
        # warn → log via raise-and-catch is the caller's call; we
        # just proceed.
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise PrivateKeyPathProblem(
            f"could not read private key bytes: {exc}",
            severity="fail",
            path=path,
        ) from exc


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _required(env_map: Mapping[str, str], key: str) -> str:
    raw = env_map.get(key)
    if raw is None:
        raise GitHubAppConfigError(
            f"required env {key} is unset", key=key
        )
    text = str(raw).strip()
    if not text:
        raise GitHubAppConfigError(
            f"required env {key} is blank", key=key
        )
    return text
