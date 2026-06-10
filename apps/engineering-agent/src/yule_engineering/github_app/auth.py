"""GitHub App JWT signing.

JWT generation does not require any network — only the
:mod:`yule_engineering.github_app.client` issuance step does.

Signing is split behind a :class:`Protocol` so:

  * Production wires the RS256 signer that lazy-imports
    ``cryptography`` (kept out of unit-test paths).
  * Tests inject :func:`fake_signer` which deterministically
    produces a non-empty signature blob.

Secret discipline:
    * Private key bytes never appear in error messages or repr.
    * The built JWT itself is treated as a token — callers must
      not log it. This module's helpers avoid printing it.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol


GITHUB_APP_JWT_ALGORITHM: str = "RS256"
GITHUB_APP_JWT_TTL_SECONDS: int = 540  # GitHub spec is max 600; leave headroom.
GITHUB_APP_JWT_TYPE: str = "JWT"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GitHubAppDependencyError(RuntimeError):
    """Raised when an optional production dependency is missing.

    Distinguished from runtime signing failure so the doctor can
    point operators at the install hint without false-flagging a
    bad key.
    """


class GitHubAppSigningError(RuntimeError):
    """Raised when the signer rejects the payload / key.

    Message redacts key bytes and signature output — only carries
    the offending algorithm + reason class.
    """


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JWTClaims:
    """GitHub-App JWT claim set.

    GitHub requires ``iss`` (app id), ``iat`` (now), ``exp`` (≤ 10 min).
    """

    iss: str
    iat: int
    exp: int

    def to_dict(self) -> dict:
        return {"iss": self.iss, "iat": self.iat, "exp": self.exp}


# ---------------------------------------------------------------------------
# Signer protocol + production / fake implementations
# ---------------------------------------------------------------------------


class GitHubAppSigner(Protocol):
    """Minimal signing surface — production RS256 or fake."""

    def sign(self, payload: bytes, private_key: bytes) -> bytes:  # pragma: no cover - Protocol
        ...


def fake_signer(payload: bytes, private_key: bytes) -> bytes:
    """Deterministic non-RS256 signature for tests.

    Returns a 32-byte zero-prefixed blob so the JWT structure is
    valid while keeping unit tests crypto-free. Tests that pin the
    *exact* signature value should hash the payload separately.
    """

    return b"\x00" * 32


def _rs256_sign(payload: bytes, private_key: bytes) -> bytes:
    """Production RS256 signer.

    Lazy-imports ``cryptography`` so the module stays importable in
    minimal CI envs (and in unit tests that pass the fake signer).
    Raises :class:`GitHubAppDependencyError` when the lib is
    missing.
    """

    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError as exc:
        raise GitHubAppDependencyError(
            "cryptography is required for production RS256 signing — "
            "install via `pip install cryptography` or pass a custom "
            "signer to build_jwt()",
        ) from exc

    try:
        loaded = serialization.load_pem_private_key(private_key, password=None)
    except Exception as exc:  # noqa: BLE001 - cryptography raises ValueError-ish
        raise GitHubAppSigningError(
            "could not load PEM private key (algo=RS256) — "
            "the key bytes are malformed or password-protected"
        ) from exc

    if not isinstance(loaded, rsa.RSAPrivateKey):
        raise GitHubAppSigningError(
            "PEM key is not an RSA private key (algo=RS256)"
        )

    try:
        return loaded.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:  # noqa: BLE001
        raise GitHubAppSigningError(
            "RS256 signing failed for the provided payload"
        ) from exc


# ---------------------------------------------------------------------------
# JWT builder
# ---------------------------------------------------------------------------


def build_jwt(
    *,
    app_id: str,
    private_key: bytes,
    signer: Optional[Any] = None,
    now: Optional[int] = None,
    ttl_seconds: int = GITHUB_APP_JWT_TTL_SECONDS,
) -> str:
    """Build a signed GitHub App JWT.

    *signer* must be a callable ``(payload_bytes, key_bytes) -> bytes``
    or an object exposing ``.sign(payload, key)``. Defaults to the
    lazy-loaded RS256 signer.

    *now* lets tests pin the issued-at timestamp for deterministic
    snapshots. Production passes ``None`` and we use
    :func:`time.time`.

    The resulting JWT string is **secret** — callers must redact
    it from logs and audit records.
    """

    if not app_id or not str(app_id).strip():
        raise GitHubAppSigningError("app_id is required for JWT iss claim")
    if not private_key:
        raise GitHubAppSigningError("private_key is required for RS256 signing")
    if ttl_seconds <= 0 or ttl_seconds > 600:
        raise GitHubAppSigningError(
            f"ttl_seconds must be in (0, 600]; got {ttl_seconds}"
        )

    issued_at = int(now if now is not None else time.time())
    claims = JWTClaims(
        iss=str(app_id),
        iat=issued_at,
        exp=issued_at + int(ttl_seconds),
    )

    header = {"alg": GITHUB_APP_JWT_ALGORITHM, "typ": GITHUB_APP_JWT_TYPE}
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    claims_b64 = _b64url(json.dumps(claims.to_dict(), separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{claims_b64}".encode("ascii")

    signature_bytes = _invoke_signer(signer, signing_input, private_key)
    signature_b64 = _b64url(signature_bytes)
    return f"{header_b64}.{claims_b64}.{signature_b64}"


def _invoke_signer(
    signer: Optional[Any],
    payload: bytes,
    private_key: bytes,
) -> bytes:
    if signer is None:
        return _rs256_sign(payload, private_key)
    if hasattr(signer, "sign"):
        return signer.sign(payload, private_key)  # type: ignore[no-any-return]
    if callable(signer):
        return signer(payload, private_key)
    raise GitHubAppSigningError(
        "signer must be callable or expose .sign(payload, key); "
        f"got {type(signer).__name__}"
    )


# ---------------------------------------------------------------------------
# base64url helpers (no padding — RFC 7515)
# ---------------------------------------------------------------------------


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


__all__ = (
    "GITHUB_APP_JWT_ALGORITHM",
    "GITHUB_APP_JWT_TTL_SECONDS",
    "GitHubAppDependencyError",
    "GitHubAppSigner",
    "GitHubAppSigningError",
    "JWTClaims",
    "build_jwt",
    "fake_signer",
)
