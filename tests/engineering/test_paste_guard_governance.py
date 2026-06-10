"""PasteGuard governance regression — outbound secret hard-rail gate.

Mirrors :mod:`tests.engineering.test_issue_73_round2_governance` in
posture: one suite that pins the most important hard rails of the
F1 / #88 outbound preflight so a single rename / regex flip / repr
leak trips a clearly named test.

Rails pinned:

  1. ``guard_outbound(LLM, raw_anthropic_key)`` strips the raw bytes
     from the redacted payload AND from every public field of
     :class:`GuardVerdict` / :class:`SecretFinding` (no `__repr__`
     leak, no `suggested_redaction` leak).
  2. ``guard_outbound(DISCORD, pem_block)`` drops the entire PEM
     body and never echoes BEGIN/END header lines.
  3. ``guard_outbound(GITHUB, db_url_with_password)`` masks the
     credentials inside a DB URL so a bot comment cannot leak DSNs.
  4. ``guard_outbound(VAULT, opaque_high_entropy_blob)`` flags
     advisory-level generic credentials so a vault write cannot
     silently store an unscanned secret.
  5. Every public field of every dataclass is scanned for raw
     secret substrings — this is the catch-all that catches a
     future field addition that forgets to mask.

Compatibility rail (criterion #5 of #88):

  6. Existing governance imports (``is_protected_branch``,
     ``RecordOnlyCodeEditor``) remain importable and unchanged
     — PasteGuard must not regress upstream hard rails.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.coding_executor_live import (
    RecordOnlyCodeEditor,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    is_protected_branch,
)
from yule_engineering.agents.security.paste_guard import (
    GuardVerdict,
    OutboundChannel,
    RISK_ADVISORY,
    RISK_CRITICAL,
    SecretPattern,
    guard_outbound,
)


# Fake-but-pattern-shaped sentinels. These are NOT real credentials;
# they exist purely so a regex flip or repr leak is visible in test
# output. The strings still match the catalogue so the guard treats
# them as real secrets.
ANTHROPIC_RAW = "sk-ant-" + "X" * 40 + "AA"
PEM_RAW = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktLXYxAAAAA_secret_payload_here==\n"
    "-----END OPENSSH PRIVATE KEY-----"
)
DB_URL_RAW = "mongodb+srv://yule:hunter2supersecret@cluster0.mongo/yule"
HIGH_ENTROPY_RAW = "Z" * 50


def _all_field_strings(verdict: GuardVerdict) -> str:
    """Concatenate every public string field on the verdict + findings.

    Used to guarantee no raw secret bytes leak through `__repr__`,
    `suggested_redaction`, the `redacted` body, or the audit hash.
    """

    parts = [verdict.channel.value, verdict.original_hash, verdict.redacted, repr(verdict)]
    for finding in verdict.findings:
        parts.append(finding.pattern.value)
        parts.append(finding.risk_level)
        parts.append(finding.suggested_redaction)
        parts.append(repr(finding))
        parts.append(str(finding.span))
    return "\n".join(parts)


class OutboundChannelHardRailTests(unittest.TestCase):
    """Each outbound channel × representative secret pattern."""

    def test_llm_channel_blocks_raw_anthropic_key(self) -> None:
        verdict = guard_outbound(
            channel=OutboundChannel.LLM,
            payload=f"please remember my key {ANTHROPIC_RAW} for later",
        )
        self.assertEqual(verdict.channel, OutboundChannel.LLM)
        self.assertNotIn(ANTHROPIC_RAW, verdict.redacted)
        # Pattern catalogue must recognise the hit as critical.
        self.assertTrue(verdict.has_critical())
        self.assertEqual(
            verdict.findings[0].pattern, SecretPattern.ANTHROPIC_API_KEY
        )

    def test_discord_channel_strips_pem_block(self) -> None:
        verdict = guard_outbound(
            channel=OutboundChannel.DISCORD, payload=PEM_RAW
        )
        self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", verdict.redacted)
        self.assertNotIn("END OPENSSH PRIVATE KEY", verdict.redacted)
        self.assertNotIn("secret_payload_here", verdict.redacted)
        self.assertEqual(
            verdict.findings[0].pattern, SecretPattern.PEM_BLOCK
        )

    def test_github_channel_strips_db_url_password(self) -> None:
        verdict = guard_outbound(
            channel=OutboundChannel.GITHUB,
            payload=f"connection: {DB_URL_RAW} (do not share)",
        )
        # The raw URL with password must not survive into the
        # outbound GitHub comment.
        self.assertNotIn(DB_URL_RAW, verdict.redacted)
        self.assertNotIn("hunter2supersecret", verdict.redacted)
        self.assertEqual(
            verdict.findings[0].pattern, SecretPattern.DB_URL_WITH_PASSWORD
        )

    def test_vault_channel_flags_high_entropy_credential(self) -> None:
        verdict = guard_outbound(
            channel=OutboundChannel.VAULT,
            payload=f"opaque token: {HIGH_ENTROPY_RAW}",
        )
        # The generic catch-all must at minimum tag this as advisory
        # so a vault write does not silently store an unscanned blob.
        patterns = [f.pattern for f in verdict.findings]
        self.assertIn(SecretPattern.GENERIC_HIGH_ENTROPY, patterns)
        advisory = [f for f in verdict.findings if f.risk_level == RISK_ADVISORY]
        self.assertTrue(advisory)
        self.assertNotIn(HIGH_ENTROPY_RAW, verdict.redacted)


class RawSecretNeverLeaksInVerdictTests(unittest.TestCase):
    """No public field on any dataclass may contain raw secret bytes."""

    def test_anthropic_key_absent_from_all_verdict_fields(self) -> None:
        verdict = guard_outbound(
            channel=OutboundChannel.LLM, payload=f"k={ANTHROPIC_RAW}"
        )
        blob = _all_field_strings(verdict)
        self.assertNotIn(ANTHROPIC_RAW, blob)

    def test_pem_block_body_absent_from_all_verdict_fields(self) -> None:
        verdict = guard_outbound(
            channel=OutboundChannel.DISCORD, payload=PEM_RAW
        )
        blob = _all_field_strings(verdict)
        self.assertNotIn("secret_payload_here", blob)
        self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", blob)

    def test_db_url_password_absent_from_all_verdict_fields(self) -> None:
        verdict = guard_outbound(
            channel=OutboundChannel.GITHUB, payload=DB_URL_RAW
        )
        blob = _all_field_strings(verdict)
        self.assertNotIn("hunter2supersecret", blob)


class UpstreamHardRailCompatibilityTests(unittest.TestCase):
    """Round 2 hard rails (#73) must remain intact alongside #88."""

    def test_is_protected_branch_still_blocks_main(self) -> None:
        self.assertTrue(is_protected_branch("main"))
        self.assertTrue(is_protected_branch("release/2026-05"))

    def test_record_only_editor_class_still_importable(self) -> None:
        # PasteGuard sits beside the live executor — the executor's
        # LLM-edit block must stay in place. Just import + sanity.
        self.assertTrue(callable(RecordOnlyCodeEditor))


if __name__ == "__main__":
    unittest.main()
