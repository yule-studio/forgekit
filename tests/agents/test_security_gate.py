"""Security auto-dispatch gate (issue #185 follow-up C)."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.security_gate import (
    ANTI_GOAL_NOTE,
    SEVERITY_REQUIRED,
    assess_security_review,
    security_review_required,
)


class TriggerTests(unittest.TestCase):
    def test_auth_change_requires_review(self) -> None:
        d = assess_security_review({"paths": ["src/auth/login.py"], "summary": "add login"})
        self.assertTrue(d.required)
        self.assertIn("auth", d.triggers)
        self.assertEqual(d.severity, SEVERITY_REQUIRED)

    def test_secret_change_requires_review(self) -> None:
        d = assess_security_review({"summary": "rotate API key in credential store"})
        self.assertIn("secret", d.triggers)

    def test_public_surface_requires_review(self) -> None:
        d = assess_security_review({"paths": ["api/routes/webhook.py"]})
        self.assertIn("public_surface", d.triggers)

    def test_deployment_requires_review(self) -> None:
        d = assess_security_review({"paths": [".github/workflows/deploy.yml"]})
        self.assertIn("deployment", d.triggers)

    def test_client_security_requires_review(self) -> None:
        d = assess_security_review({"summary": "tighten CSP and token storage in localStorage"})
        self.assertIn("client_security", d.triggers)

    def test_agent_safety_requires_review(self) -> None:
        d = assess_security_review({"summary": "add tool grant for new approval-gate"})
        self.assertIn("agent_safety", d.triggers)

    def test_multiple_triggers(self) -> None:
        d = assess_security_review(
            {"summary": "new public endpoint with jwt auth", "paths": ["api/auth/route.py"]}
        )
        self.assertIn("auth", d.triggers)
        self.assertIn("public_surface", d.triggers)

    def test_benign_change_not_required(self) -> None:
        d = assess_security_review({"paths": ["README.md"], "summary": "fix typo"})
        self.assertFalse(d.required)
        self.assertFalse(security_review_required({"summary": "rename a variable"}))


class SkipAndAntiGoalTests(unittest.TestCase):
    def test_explicit_skip_recorded(self) -> None:
        d = assess_security_review(
            {"paths": ["src/auth/x.py"]}, force_skip_reason="operator waived: revert"
        )
        self.assertFalse(d.required)
        self.assertEqual(d.skip_reason, "operator waived: revert")

    def test_anti_goal_note_in_payload(self) -> None:
        d = assess_security_review({"summary": "auth change"})
        payload = d.to_dict()
        self.assertEqual(payload["anti_goal_note"], ANTI_GOAL_NOTE)
        self.assertIn("서버", ANTI_GOAL_NOTE)
        self.assertIn("개발자 도구", ANTI_GOAL_NOTE)

    def test_surface_strings(self) -> None:
        req = assess_security_review({"summary": "jwt auth"})
        self.assertIn("REQUIRED", req.surface())
        skip = assess_security_review({"summary": "auth"}, force_skip_reason="waived")
        self.assertIn("skipped", skip.surface())


if __name__ == "__main__":
    unittest.main()
