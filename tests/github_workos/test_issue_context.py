"""WorkRequest builders + secret-like redaction at the boundary."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.github_workos.issue_context import (
    SourceKind,
    WorkRequest,
    build_request_from_discord_intake,
    build_request_from_github_issue,
    redact_secret_like,
)


class GitHubIssueBuilderTests(unittest.TestCase):
    def test_basic_issue_payload_maps_to_work_request(self) -> None:
        payload = {
            "number": 42,
            "title": "Spring Boot API 설계 검토",
            "body": "POST /orders 엔드포인트를 추가하고 싶다.",
            "html_url": "https://github.com/yule-studio/foo/issues/42",
            "state": "open",
            "labels": [{"name": "enhancement"}, {"name": "backend"}],
            "user": {"login": "codwithyc"},
        }
        request = build_request_from_github_issue(payload)
        self.assertEqual(request.kind, SourceKind.GITHUB_ISSUE)
        self.assertEqual(request.title, "Spring Boot API 설계 검토")
        self.assertIn("POST /orders", request.body)
        self.assertEqual(request.source_id, "issue#42")
        self.assertEqual(request.labels, ("enhancement", "backend"))
        self.assertEqual(request.sender, "codwithyc")
        self.assertEqual(
            request.extra.get("html_url"),
            "https://github.com/yule-studio/foo/issues/42",
        )

    def test_missing_number_falls_back_to_question(self) -> None:
        request = build_request_from_github_issue({"title": "x", "body": "y"})
        self.assertEqual(request.source_id, "issue#?")

    def test_string_label_entries_accepted(self) -> None:
        payload = {
            "number": 7,
            "title": "ops issue",
            "labels": ["devops", "deploy"],
        }
        request = build_request_from_github_issue(payload)
        self.assertEqual(request.labels, ("devops", "deploy"))

    def test_link_extraction_from_body(self) -> None:
        payload = {
            "number": 10,
            "title": "linkful",
            "body": "see https://example.com/a and https://example.com/b.",
        }
        request = build_request_from_github_issue(payload)
        self.assertEqual(
            request.raw_links,
            ("https://example.com/a", "https://example.com/b"),
        )

    def test_non_mapping_payload_raises(self) -> None:
        with self.assertRaises(TypeError):
            build_request_from_github_issue(["not", "a", "dict"])  # type: ignore[arg-type]


class DiscordIntakeBuilderTests(unittest.TestCase):
    def test_first_line_becomes_title(self) -> None:
        text = "Spring Boot API 설계 좀 봐줘\n\n관련 클래스: OrderController.java"
        request = build_request_from_discord_intake(
            text, message_id="1234", sender="codwithyc", channel="업무-접수"
        )
        self.assertEqual(request.kind, SourceKind.DISCORD_INTAKE)
        self.assertEqual(request.title, "Spring Boot API 설계 좀 봐줘")
        self.assertIn("OrderController.java", request.body)
        self.assertEqual(request.source_id, "discord#1234")
        self.assertEqual(request.sender, "codwithyc")
        self.assertEqual(request.extra.get("channel"), "업무-접수")

    def test_title_capped_at_200_chars(self) -> None:
        long_first_line = "A" * 250
        request = build_request_from_discord_intake(long_first_line)
        self.assertEqual(len(request.title), 200)

    def test_empty_text_returns_empty_request(self) -> None:
        request = build_request_from_discord_intake("", message_id="9")
        self.assertEqual(request.title, "")
        self.assertEqual(request.body, "")
        self.assertEqual(request.source_id, "discord#9")


class SecretRedactionTests(unittest.TestCase):
    def test_github_pat_replaced(self) -> None:
        redacted = redact_secret_like(
            "내 토큰은 github_pat_11ABCDEFG0abcdefghijklmnopqrstuv 야"
        )
        self.assertIn("[redacted-github-pat]", redacted)
        self.assertNotIn("github_pat_11ABCDEFG0", redacted)

    def test_ghp_token_replaced(self) -> None:
        redacted = redact_secret_like("ghp_abcdefghijklmnopqrst1234")
        self.assertIn("[redacted-github-token]", redacted)

    def test_openai_sk_replaced(self) -> None:
        redacted = redact_secret_like("sk-abcdefghijklmnopqrstuvwxyz12")
        self.assertIn("[redacted-api-key]", redacted)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz12", redacted)

    def test_aws_access_key_replaced(self) -> None:
        redacted = redact_secret_like("AKIAIOSFODNN7EXAMPLE bar")
        self.assertIn("[redacted-aws-key]", redacted)

    def test_private_key_block_replaced(self) -> None:
        original = (
            "context\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...\n"
            "-----END RSA PRIVATE KEY-----\n"
            "tail"
        )
        redacted = redact_secret_like(original)
        self.assertIn("[redacted-private-key-block]", redacted)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", redacted)
        self.assertIn("tail", redacted)

    def test_jwt_replaced(self) -> None:
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTYifQ"
            ".s5KQUjxbQrLZiqgz1234567890"
        )
        redacted = redact_secret_like(f"token: {jwt}")
        self.assertIn("[redacted-jwt]", redacted)

    def test_bearer_header_replaced(self) -> None:
        redacted = redact_secret_like(
            "Authorization: Bearer abc123_def-456.ghi789jklmnop"
        )
        self.assertIn("Bearer [redacted-bearer]", redacted)
        self.assertNotIn("abc123_def-456.ghi789jklmnop", redacted)

    def test_env_assignment_value_replaced(self) -> None:
        redacted = redact_secret_like("DISCORD_TOKEN=Bot_SuperLongRealToken123")
        self.assertIn("DISCORD_TOKEN=[redacted-env-value]", redacted)

    def test_idempotent(self) -> None:
        once = redact_secret_like("ghp_aaaaaaaaaaaaaaaaaaaaaa")
        twice = redact_secret_like(once)
        self.assertEqual(once, twice)

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(redact_secret_like(""), "")
        self.assertEqual(redact_secret_like(None), "")  # type: ignore[arg-type]

    def test_redaction_applied_at_github_issue_boundary(self) -> None:
        payload = {
            "number": 99,
            "title": "ghp_abcdefghijklmnopqrstuvwxyz0123",
            "body": "오류 로그 첨부: sk-abcdefghijklmnopqrstuv",
            "user": {"login": "ghp_someuserysecretlooking12345"},
        }
        request = build_request_from_github_issue(payload)
        self.assertIn("[redacted-github-token]", request.title)
        self.assertIn("[redacted-api-key]", request.body)
        # Sender field also redacted.
        self.assertNotIn("someuserysecretlooking12345", request.sender)

    def test_redaction_applied_at_discord_boundary(self) -> None:
        text = (
            "코드 좀 봐줘\n"
            "ANTHROPIC_KEY=sk-supersecretvaluethatlooksreal12"
        )
        request = build_request_from_discord_intake(
            text, message_id="1", sender="me", channel="업무-접수"
        )
        # The sk- is the inner pattern; the env-assignment regex
        # also catches the whole line. Either replacement is fine —
        # what matters is that the original raw value is gone.
        self.assertNotIn("supersecretvaluethatlooksreal12", request.body)


if __name__ == "__main__":
    unittest.main()
