"""GitHub App JWT builder tests — A-G1.

Pin JWT structure + signer Protocol behaviour without dragging in
``cryptography``. Tests use the fake signer from
:mod:`agents.github_app.auth`.
"""

from __future__ import annotations

import base64
import json
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.github_app.auth import (
    GITHUB_APP_JWT_ALGORITHM,
    GITHUB_APP_JWT_TTL_SECONDS,
    GitHubAppDependencyError,
    GitHubAppSigningError,
    JWTClaims,
    build_jwt,
    fake_signer,
)


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _decode_segments(jwt: str):
    parts = jwt.split(".")
    assert len(parts) == 3
    header = json.loads(_b64url_decode(parts[0]))
    claims = json.loads(_b64url_decode(parts[1]))
    signature = _b64url_decode(parts[2])
    return header, claims, signature


class BuildJWTTests(unittest.TestCase):
    def test_returns_three_part_jwt(self) -> None:
        jwt = build_jwt(
            app_id="987654",
            private_key=b"fake",
            signer=fake_signer,
            now=1_700_000_000,
        )
        self.assertEqual(jwt.count("."), 2)

    def test_header_alg_is_rs256_typ_jwt(self) -> None:
        jwt = build_jwt(
            app_id="987654", private_key=b"fake", signer=fake_signer, now=1_700_000_000
        )
        header, _, _ = _decode_segments(jwt)
        self.assertEqual(header["alg"], GITHUB_APP_JWT_ALGORITHM)
        self.assertEqual(header["typ"], "JWT")

    def test_claims_iss_iat_exp(self) -> None:
        jwt = build_jwt(
            app_id="987654", private_key=b"fake", signer=fake_signer, now=1_700_000_000
        )
        _, claims, _ = _decode_segments(jwt)
        self.assertEqual(claims["iss"], "987654")
        self.assertEqual(claims["iat"], 1_700_000_000)
        self.assertEqual(claims["exp"], 1_700_000_000 + GITHUB_APP_JWT_TTL_SECONDS)

    def test_signature_present(self) -> None:
        jwt = build_jwt(
            app_id="987654", private_key=b"fake", signer=fake_signer, now=1
        )
        _, _, signature = _decode_segments(jwt)
        self.assertGreater(len(signature), 0)

    def test_callable_signer_supported(self) -> None:
        captured = {}

        def signer(payload: bytes, key: bytes) -> bytes:
            captured["payload_len"] = len(payload)
            captured["key"] = key
            return b"\x01\x02\x03"

        build_jwt(app_id="9", private_key=b"k", signer=signer, now=1)
        self.assertGreater(captured["payload_len"], 0)
        self.assertEqual(captured["key"], b"k")

    def test_object_signer_with_sign_method_supported(self) -> None:
        class Sgn:
            def sign(self, payload: bytes, key: bytes) -> bytes:
                return b"x" * 16

        build_jwt(app_id="9", private_key=b"k", signer=Sgn(), now=1)

    def test_blank_app_id_raises(self) -> None:
        with self.assertRaises(GitHubAppSigningError):
            build_jwt(app_id=" ", private_key=b"k", signer=fake_signer, now=1)

    def test_empty_private_key_raises(self) -> None:
        with self.assertRaises(GitHubAppSigningError):
            build_jwt(app_id="9", private_key=b"", signer=fake_signer, now=1)

    def test_ttl_out_of_range_raises(self) -> None:
        with self.assertRaises(GitHubAppSigningError):
            build_jwt(app_id="9", private_key=b"k", signer=fake_signer, now=1, ttl_seconds=0)
        with self.assertRaises(GitHubAppSigningError):
            build_jwt(app_id="9", private_key=b"k", signer=fake_signer, now=1, ttl_seconds=601)

    def test_invalid_signer_type_raises(self) -> None:
        with self.assertRaises(GitHubAppSigningError):
            build_jwt(
                app_id="9", private_key=b"k", signer="not-callable", now=1
            )


class RS256DependencyTests(unittest.TestCase):
    def test_rs256_signer_raises_dependency_error_when_cryptography_missing(self) -> None:
        # The host might or might not have ``cryptography`` installed.
        # If missing, calling build_jwt without an injected signer
        # must raise GitHubAppDependencyError, not crash with an
        # ImportError.
        try:
            import cryptography  # noqa: F401
        except ImportError:
            cryptography_installed = False
        else:
            cryptography_installed = True

        if cryptography_installed:
            self.skipTest("cryptography installed — dependency-missing path not exercisable here")

        with self.assertRaises(GitHubAppDependencyError):
            build_jwt(app_id="9", private_key=b"k", now=1)


class ClaimsModelTests(unittest.TestCase):
    def test_to_dict_round_trip(self) -> None:
        claims = JWTClaims(iss="9", iat=1, exp=600)
        self.assertEqual(claims.to_dict(), {"iss": "9", "iat": 1, "exp": 600})


class TokenSecretRedactionTests(unittest.TestCase):
    def test_jwt_returned_does_not_show_private_key(self) -> None:
        # The JWT itself encodes the signature, but not the input
        # private key. Sanity check: pem bytes aren't smuggled into
        # the b64-encoded segments.
        secret_pem = b"-----BEGIN PRIVATE KEY-----\nSECRET-LEAKED\n-----END PRIVATE KEY-----\n"
        jwt = build_jwt(app_id="9", private_key=secret_pem, signer=fake_signer, now=1)
        self.assertNotIn("SECRET-LEAKED", jwt)
        self.assertNotIn("PRIVATE KEY", jwt)


if __name__ == "__main__":
    unittest.main()
