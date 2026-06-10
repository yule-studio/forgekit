"""PasteGuard unit tests — pattern catalogue × redaction × channel matrix.

Covers the F1/#88 acceptance criteria:

  * ``SecretPattern`` catalogue (8 entries) — every entry must
    detect a positive case and let a benign negative through.
  * ``scan_payload`` returns a deterministic, sorted tuple of
    :class:`SecretFinding` with masked ``suggested_redaction``.
  * ``redact_payload`` is round-trip safe (re-running on already
    redacted output is a no-op) and emits ``head4 + mask + tail4``.
  * ``guard_outbound`` runs identically across all four
    :class:`OutboundChannel` values and honours ``fail_closed``.
  * Unicode-only payloads pass through cleanly (no encoding crash).
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.security.paste_guard import (
    GuardVerdict,
    OutboundChannel,
    PasteGuardError,
    RISK_ADVISORY,
    RISK_CRITICAL,
    SecretFinding,
    SecretPattern,
    guard_outbound,
    redact_payload,
    scan_payload,
)


# ---------------------------------------------------------------------------
# Sample fixtures — non-real, but shaped to match the patterns. Each one
# is suffixed with a sentinel so a regression that bleeds the raw bytes
# into a log / repr would show up immediately in the failing assertion.
# ---------------------------------------------------------------------------


ANTHROPIC_RAW = "sk-ant-" + "A" * 40 + "ZZ"
OPENAI_RAW = "sk-proj-" + "B" * 30 + "ZZ"
GITHUB_PAT_RAW = "ghp_" + "C" * 40
GITHUB_INSTALL_RAW = "ghs_" + "D" * 40
DISCORD_RAW = "M" + "E" * 23 + "." + "F" * 6 + "." + "G" * 30
PEM_RAW = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA0Z+secretbytes/here==\n"
    "-----END RSA PRIVATE KEY-----"
)
AWS_RAW = "AKIA" + "1" * 16
DB_URL_RAW = "postgres://yule:supersecret@db.internal:5432/yule"
GENERIC_RAW = "Z" * 40  # 40 char base64-shape blob


class PatternDetectMatrixTests(unittest.TestCase):
    """Each catalogue pattern must detect a positive sample."""

    def test_anthropic_key_detected(self) -> None:
        findings = scan_payload(f"prefix {ANTHROPIC_RAW} suffix")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].pattern, SecretPattern.ANTHROPIC_API_KEY)
        self.assertEqual(findings[0].risk_level, RISK_CRITICAL)

    def test_openai_key_detected_and_not_misattributed_as_anthropic(self) -> None:
        findings = scan_payload(f"key={OPENAI_RAW}")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].pattern, SecretPattern.OPENAI_API_KEY)

    def test_github_pat_detected(self) -> None:
        findings = scan_payload(f"token: {GITHUB_PAT_RAW} ;")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].pattern, SecretPattern.GITHUB_PAT)

    def test_github_installation_token_detected(self) -> None:
        findings = scan_payload(f"install token {GITHUB_INSTALL_RAW}")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].pattern, SecretPattern.GITHUB_PAT)

    def test_discord_bot_token_detected(self) -> None:
        findings = scan_payload(f"DISCORD={DISCORD_RAW}")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].pattern, SecretPattern.DISCORD_BOT_TOKEN)

    def test_pem_block_detected(self) -> None:
        findings = scan_payload(PEM_RAW + "\n")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].pattern, SecretPattern.PEM_BLOCK)

    def test_aws_access_key_detected(self) -> None:
        findings = scan_payload(f"aws id {AWS_RAW}")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].pattern, SecretPattern.AWS_ACCESS_KEY)

    def test_db_url_with_password_detected(self) -> None:
        findings = scan_payload(f"DSN={DB_URL_RAW}")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].pattern, SecretPattern.DB_URL_WITH_PASSWORD)

    def test_generic_high_entropy_advisory(self) -> None:
        # 40 char base64 blob is conservative; risk = advisory.
        findings = scan_payload(f"opaque {GENERIC_RAW} end")
        self.assertTrue(
            any(
                f.pattern is SecretPattern.GENERIC_HIGH_ENTROPY
                and f.risk_level == RISK_ADVISORY
                for f in findings
            )
        )


class PatternNoFalsePositiveTests(unittest.TestCase):
    """Benign strings shaped like the catalogue keys must NOT match."""

    def test_plain_korean_text_clean(self) -> None:
        self.assertEqual(scan_payload("안녕하세요, 일반적인 메시지입니다."), ())

    def test_short_sk_string_clean(self) -> None:
        # ``sk-short`` is far below the 20-char minimum.
        self.assertEqual(scan_payload("sk-short"), ())

    def test_db_url_without_password_clean(self) -> None:
        # No credentials means no match.
        self.assertEqual(scan_payload("postgres://localhost/yule"), ())

    def test_short_base64_is_not_flagged(self) -> None:
        # 16-char blob is under the 32-char threshold.
        self.assertEqual(scan_payload("abcd1234EFGH5678"), ())


class RedactRoundTripTests(unittest.TestCase):
    """``redact_payload`` honours ``head4 + mask + tail4`` and is idempotent."""

    def test_redact_uses_head4_mask_tail4_shape(self) -> None:
        body = f"prefix {ANTHROPIC_RAW} suffix"
        redacted = redact_payload(body)
        self.assertNotIn(ANTHROPIC_RAW, redacted)
        # The structural sentinel ``***`` must be present.
        self.assertIn("***", redacted)
        # Head + tail bytes preserved for ops to match audit logs.
        self.assertIn(ANTHROPIC_RAW[:4], redacted)
        self.assertIn(ANTHROPIC_RAW[-4:], redacted)

    def test_redact_is_idempotent(self) -> None:
        body = f"a={GITHUB_PAT_RAW} b={AWS_RAW}"
        once = redact_payload(body)
        twice = redact_payload(once)
        self.assertEqual(once, twice)

    def test_redact_pem_collapses_to_mask(self) -> None:
        redacted = redact_payload(PEM_RAW)
        # The redacted body must not contain BEGIN/END headers OR
        # any of the secret bytes between them.
        self.assertNotIn("BEGIN RSA PRIVATE KEY", redacted)
        self.assertNotIn("END RSA PRIVATE KEY", redacted)
        self.assertNotIn("secretbytes", redacted)

    def test_redact_preserves_surrounding_text(self) -> None:
        body = f"alpha {AWS_RAW} omega"
        redacted = redact_payload(body)
        self.assertTrue(redacted.startswith("alpha "))
        self.assertTrue(redacted.endswith(" omega"))


class MultipleFindingsTests(unittest.TestCase):
    """Multi-secret payloads return one finding per hit, in order."""

    def test_multiple_findings_sorted_by_position(self) -> None:
        body = (
            f"first {ANTHROPIC_RAW} then {GITHUB_PAT_RAW} and finally {AWS_RAW}"
        )
        findings = scan_payload(body)
        self.assertEqual(len(findings), 3)
        # Ordered left-to-right.
        starts = [f.span[0] for f in findings]
        self.assertEqual(starts, sorted(starts))
        patterns = [f.pattern for f in findings]
        self.assertIn(SecretPattern.ANTHROPIC_API_KEY, patterns)
        self.assertIn(SecretPattern.GITHUB_PAT, patterns)
        self.assertIn(SecretPattern.AWS_ACCESS_KEY, patterns)

    def test_overlapping_specific_pattern_wins_over_generic(self) -> None:
        # The AWS key shape (AKIA + 16 chars) also passes the generic
        # base64 heuristic. The specific pattern must win, generic
        # must be suppressed.
        findings = scan_payload(f"raw={AWS_RAW}")
        patterns = [f.pattern for f in findings]
        self.assertIn(SecretPattern.AWS_ACCESS_KEY, patterns)
        self.assertNotIn(SecretPattern.GENERIC_HIGH_ENTROPY, patterns)

    def test_no_finding_returns_empty_tuple(self) -> None:
        self.assertEqual(scan_payload("그냥 평범한 텍스트"), ())


class GuardOutboundChannelMatrixTests(unittest.TestCase):
    """Every :class:`OutboundChannel` runs the same wrapper contract."""

    def _check_channel(self, channel: OutboundChannel) -> GuardVerdict:
        body = f"hello {ANTHROPIC_RAW} world"
        verdict = guard_outbound(channel=channel, payload=body)
        self.assertIsInstance(verdict, GuardVerdict)
        self.assertEqual(verdict.channel, channel)
        self.assertTrue(verdict.original_hash.startswith("sha256:"))
        self.assertEqual(len(verdict.findings), 1)
        self.assertFalse(verdict.blocked)
        self.assertNotIn(ANTHROPIC_RAW, verdict.redacted)
        return verdict

    def test_llm_channel(self) -> None:
        self._check_channel(OutboundChannel.LLM)

    def test_discord_channel(self) -> None:
        self._check_channel(OutboundChannel.DISCORD)

    def test_github_channel(self) -> None:
        self._check_channel(OutboundChannel.GITHUB)

    def test_vault_channel(self) -> None:
        self._check_channel(OutboundChannel.VAULT)

    def test_clean_payload_returns_no_findings(self) -> None:
        verdict = guard_outbound(
            channel=OutboundChannel.LLM, payload="안녕하세요 운영자님"
        )
        self.assertEqual(verdict.findings, ())
        self.assertFalse(verdict.blocked)
        self.assertEqual(verdict.redacted, "안녕하세요 운영자님")


class GuardFailClosedTests(unittest.TestCase):
    """``fail_closed=True`` must drop the payload on internal error."""

    def test_fail_closed_returns_empty_redacted_on_exception(self) -> None:
        import yule_engineering.agents.security.paste_guard as pg

        original = pg.redact_payload

        def boom(text: str, *, mask: str = "***") -> str:
            raise RuntimeError("simulated internal failure")

        pg.redact_payload = boom  # type: ignore[assignment]
        try:
            verdict = pg.guard_outbound(
                channel=OutboundChannel.LLM, payload=f"k={ANTHROPIC_RAW}"
            )
        finally:
            pg.redact_payload = original  # type: ignore[assignment]

        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.redacted, "")
        # Findings cleared so we never echo back partial data.
        self.assertEqual(verdict.findings, ())
        # Hash still computed so audit trail keeps continuity.
        self.assertTrue(verdict.original_hash.startswith("sha256:"))

    def test_fail_open_propagates_exception(self) -> None:
        import yule_engineering.agents.security.paste_guard as pg

        original = pg.scan_payload

        def boom(text: str):
            raise RuntimeError("scan boom")

        pg.scan_payload = boom  # type: ignore[assignment]
        try:
            with self.assertRaises(PasteGuardError):
                pg.guard_outbound(
                    channel=OutboundChannel.LLM,
                    payload="anything",
                    fail_closed=False,
                )
        finally:
            pg.scan_payload = original  # type: ignore[assignment]


class GuardInputValidationTests(unittest.TestCase):
    def test_non_channel_rejected(self) -> None:
        with self.assertRaises(PasteGuardError):
            guard_outbound(channel="llm", payload="hi")  # type: ignore[arg-type]

    def test_none_payload_treated_as_empty(self) -> None:
        verdict = guard_outbound(
            channel=OutboundChannel.GITHUB, payload=None  # type: ignore[arg-type]
        )
        self.assertEqual(verdict.redacted, "")
        self.assertFalse(verdict.blocked)
        self.assertEqual(verdict.findings, ())

    def test_non_string_payload_rejected(self) -> None:
        with self.assertRaises(PasteGuardError):
            guard_outbound(
                channel=OutboundChannel.GITHUB, payload=12345  # type: ignore[arg-type]
            )

    def test_finding_negative_span_rejected(self) -> None:
        with self.assertRaises(PasteGuardError):
            SecretFinding(
                pattern=SecretPattern.AWS_ACCESS_KEY,
                span=(10, 5),
                risk_level=RISK_CRITICAL,
                suggested_redaction="AKIA***1111",
            )


class UnicodeSafetyTests(unittest.TestCase):
    """Unicode + emoji + mixed scripts must not break the guard."""

    def test_korean_with_embedded_anthropic_key(self) -> None:
        body = f"이 메시지에는 키가 있어요: {ANTHROPIC_RAW} — 끝."
        verdict = guard_outbound(channel=OutboundChannel.DISCORD, payload=body)
        self.assertEqual(len(verdict.findings), 1)
        self.assertNotIn(ANTHROPIC_RAW, verdict.redacted)
        self.assertIn("이 메시지에는 키가 있어요", verdict.redacted)

    def test_emoji_payload_is_safe(self) -> None:
        body = "✨ 안녕 🤖 — 비밀 없음"
        verdict = guard_outbound(channel=OutboundChannel.VAULT, payload=body)
        self.assertEqual(verdict.findings, ())
        self.assertEqual(verdict.redacted, body)

    def test_empty_payload(self) -> None:
        verdict = guard_outbound(channel=OutboundChannel.LLM, payload="")
        self.assertEqual(verdict.redacted, "")
        self.assertEqual(verdict.findings, ())
        self.assertFalse(verdict.blocked)


if __name__ == "__main__":
    unittest.main()
