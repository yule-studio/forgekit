"""GitHub audit row + secret redaction — G3.

Pin the contract that:

  * :func:`build_github_audit_record` produces an
    agent_ops_audit-compatible payload (every base AgentOpsEntry key
    is present, GitHub-specific fields live under ``github``).
  * :func:`redact_secrets` strips Authorization headers, bearer
    tokens, ``ghp_…`` PATs, GitHub App PEM bodies, and key-named
    fields recursively.
  * Caller-supplied detail strings cannot smuggle a token into the
    audit row — every public surface in the writer routes through
    redact_secrets.
"""

from __future__ import annotations

import json
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.github_workos.audit import (
    ACTION_GITHUB_BRANCH_CREATE,
    ACTION_GITHUB_ISSUE_COMMENT,
    GithubWriteAudit,
    OUTCOME_DRY_RUN,
    OUTCOME_OK,
    build_github_audit_record,
    redact_secrets,
)


class RedactSecretsStringTests(unittest.TestCase):
    def test_authorization_header_redacted(self) -> None:
        out = redact_secrets("Authorization: Bearer ghp_abcdefghij1234567890XX")
        self.assertNotIn("Bearer ghp_", out)
        self.assertNotIn("ghp_abcdefghij1234567890XX", out)
        self.assertIn("Authorization: <redacted>", out)

    def test_bearer_anywhere_redacted(self) -> None:
        out = redact_secrets("Header: x-api-key=Bearer xyz123abc456")
        self.assertNotIn("Bearer xyz123abc456", out)

    def test_gh_token_classes_redacted(self) -> None:
        for token in (
            "ghp_abcdefghij1234567890XX",
            "ghu_abcdefghij1234567890XX",
            "ghs_abcdefghij1234567890XX",
            "ghr_abcdefghij1234567890XX",
            "github_pat_aaa1234567890bbbb",
        ):
            with self.subTest(token=token):
                out = redact_secrets(f"oops {token} leaked")
                self.assertNotIn(token, out)

    def test_pem_body_redacted(self) -> None:
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "AAAA...secret-key-content...AAAA\n"
            "-----END RSA PRIVATE KEY-----"
        )
        out = redact_secrets(f"App identity:\n{pem}\nend")
        self.assertNotIn("secret-key-content", out)
        self.assertIn("<redacted-private-key>", out)

    def test_long_strings_capped(self) -> None:
        # Pathological exception messages can't blow up the audit row.
        out = redact_secrets("A" * 10000)
        self.assertLessEqual(len(out), 800 + 3)

    def test_clean_strings_pass_through(self) -> None:
        clean = "branch agent/backend/issue-1-foo created on owner/repo"
        self.assertEqual(redact_secrets(clean), clean)


class RedactSecretsCollectionTests(unittest.TestCase):
    def test_dict_keys_are_redacted_when_sensitive(self) -> None:
        out = redact_secrets(
            {
                "branch": "agent/x/issue-1-foo",
                "Authorization": "Bearer ghp_abcdefghij1234567890XX",
                "token": "ghs_abcdefghij1234567890XX",
                "x-github-token": "ghp_abcdefghij1234567890XX",
                "private_key": "raw-pem",
                "public": "ok",
            }
        )
        self.assertEqual(out["branch"], "agent/x/issue-1-foo")
        self.assertEqual(out["Authorization"], "<redacted>")
        self.assertEqual(out["token"], "<redacted>")
        self.assertEqual(out["x-github-token"], "<redacted>")
        self.assertEqual(out["private_key"], "<redacted>")
        self.assertEqual(out["public"], "ok")

    def test_nested_collections_redacted_recursively(self) -> None:
        out = redact_secrets(
            {
                "headers": [
                    "Authorization: Bearer ghp_aaa1234567890bbbb1234",
                    "X-Trace: 1",
                ],
                "body": {"token": "ghp_xxx1234567890yyyyyy12"},
            }
        )
        # List element is redacted in-place.
        self.assertNotIn("ghp_", json.dumps(out))


class BuildGithubAuditRecordTests(unittest.TestCase):
    def test_payload_is_agent_ops_audit_compatible(self) -> None:
        record = build_github_audit_record(
            action=ACTION_GITHUB_ISSUE_COMMENT,
            actor_role="backend-engineer",
            autonomy_level="L1",
            policy_reason="allowed (min=L1)",
            target_repo="owner/repo",
            issue_number=42,
            session_id="sess-1",
            dry_run=False,
            outcome=OUTCOME_OK,
            summary="comment posted",
            references=("https://github.com/owner/repo/issues/42",),
            decision_id="dec-1",
        )
        payload = record.as_payload()
        # Base agent_ops_audit keys all present.
        for key in (
            "entry_id",
            "session_id",
            "action",
            "autonomy_level",
            "summary",
            "reasoning",
            "outcome",
            "references",
            "topic_key",
            "job_id",
            "decision_id",
            "actor",
            "recorded_at",
        ):
            self.assertIn(key, payload, key)
        # GitHub extension lives under ``github``.
        self.assertIn("github", payload)
        github_block = payload["github"]
        self.assertEqual(github_block["target_repo"], "owner/repo")
        self.assertEqual(github_block["issue_number"], 42)
        self.assertFalse(github_block["dry_run"])

    def test_dry_run_default_outcome(self) -> None:
        record = build_github_audit_record(
            action=ACTION_GITHUB_BRANCH_CREATE,
            actor_role="backend-engineer",
            autonomy_level="L2",
            policy_reason="allowed (min=L2)",
            branch="agent/backend/issue-1-foo",
        )
        self.assertEqual(record.outcome, OUTCOME_DRY_RUN)
        self.assertTrue(record.dry_run)

    def test_policy_reason_secret_is_redacted(self) -> None:
        record = build_github_audit_record(
            action=ACTION_GITHUB_ISSUE_COMMENT,
            actor_role="backend-engineer",
            autonomy_level="L1",
            policy_reason=(
                "allowed (caller passed Authorization: Bearer ghp_aaaaaaaaaaaaaaaaaaaa)"
            ),
            outcome=OUTCOME_OK,
        )
        self.assertNotIn("ghp_", record.policy_reason)
        self.assertNotIn("Bearer ghp", record.policy_reason)

    def test_summary_secret_is_redacted(self) -> None:
        record = build_github_audit_record(
            action=ACTION_GITHUB_ISSUE_COMMENT,
            actor_role="ai-engineer",
            autonomy_level="L1",
            policy_reason="allowed",
            summary="comment body included token=ghp_aaaaaaaaaaaaaaaaaaaa",
            outcome=OUTCOME_OK,
        )
        self.assertNotIn("ghp_", record.summary)

    def test_references_filtered_and_redacted(self) -> None:
        record = build_github_audit_record(
            action=ACTION_GITHUB_ISSUE_COMMENT,
            actor_role="backend-engineer",
            autonomy_level="L1",
            policy_reason="allowed",
            references=(
                "https://github.com/owner/repo/issues/1",
                "Authorization: Bearer ghp_aaa1234567890bbbb1234",
                "",
                None,  # type: ignore[arg-type]
            ),
        )
        # Empty / None dropped; remaining strings redacted.
        self.assertEqual(len(record.references), 2)
        for ref in record.references:
            self.assertNotIn("ghp_", ref)


if __name__ == "__main__":
    unittest.main()
