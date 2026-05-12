"""Schema-level checks for engineering-agent role profiles.

Coding Agent Authorization MVP needs every role profile to expose the
same set of fields so the executor selector / authorization proposal
builder can read them deterministically. Pin the contract here so a
typo (or a future profile that forgets ``forbidden_scope``) fails
loudly instead of silently producing an unsafe authorization.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPARTMENT_DIR = REPO_ROOT / "agents" / "engineering-agent"


_ROLES = (
    "tech-lead",
    "ai-engineer",
    "backend-engineer",
    "frontend-engineer",
    "qa-engineer",
    "devops-engineer",
    "product-designer",
)


# Newly required fields introduced by the Coding Agent Authorization
# MVP. The selector consumes them deterministically so they must be
# present and well-formed on every role profile.
_REQUIRED_AUTHORIZATION_FIELDS = (
    "domain_focus",
    "decision_criteria",
    "review_checklist",
    "risk_focus",
    "write_scope_candidates",
    "forbidden_scope",
    "default_executor_priority",
    "default_reviewer_priority",
)


# Thicker profile fields the existing backend role established as the
# baseline. Bring every role up to that level so role agents make
# domain-grounded judgements rather than generic ones.
_REQUIRED_THICK_FIELDS = (
    "decision_scope",
    "research_focus",
    "input_contract",
    "output_contract",
    "collaboration_contract",
    "quality_bar",
    "anti_patterns",
)


def _load_role_profile(role: str) -> dict:
    path = DEPARTMENT_DIR / role / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


class RoleProfileAuthorizationFieldsTests(unittest.TestCase):
    def test_every_role_carries_authorization_fields(self) -> None:
        for role in _ROLES:
            with self.subTest(role=role):
                profile = _load_role_profile(role)
                for field in _REQUIRED_AUTHORIZATION_FIELDS:
                    self.assertIn(field, profile, f"{role}: missing {field}")

    def test_executor_and_reviewer_priority_are_keyword_buckets(self) -> None:
        for role in _ROLES:
            with self.subTest(role=role):
                profile = _load_role_profile(role)
                for field in ("default_executor_priority", "default_reviewer_priority"):
                    bucket = profile[field]
                    self.assertIsInstance(bucket, dict, f"{role}: {field} not a dict")
                    self.assertIn("high", bucket, f"{role}: {field} missing 'high'")
                    self.assertIsInstance(bucket["high"], list)

    def test_write_scope_candidates_and_forbidden_scope_are_listy(self) -> None:
        for role in _ROLES:
            with self.subTest(role=role):
                profile = _load_role_profile(role)
                self.assertIsInstance(profile["write_scope_candidates"], list)
                self.assertGreaterEqual(len(profile["write_scope_candidates"]), 1)
                self.assertIsInstance(profile["forbidden_scope"], list)
                self.assertGreaterEqual(len(profile["forbidden_scope"]), 1)


class RoleProfileThickFieldsTests(unittest.TestCase):
    def test_every_role_has_backend_level_profile(self) -> None:
        for role in _ROLES:
            with self.subTest(role=role):
                profile = _load_role_profile(role)
                for field in _REQUIRED_THICK_FIELDS:
                    self.assertIn(field, profile, f"{role}: missing {field}")
                    self.assertIsInstance(
                        profile[field],
                        list,
                        f"{role}: {field} should be a list",
                    )
                    self.assertGreaterEqual(
                        len(profile[field]),
                        1,
                        f"{role}: {field} must list at least one entry",
                    )


class RoleProfileExecutorVocabularyTests(unittest.TestCase):
    """Sanity-check the deterministic executor selection vocabulary so
    the authorization proposal can rely on minimum coverage. We don't
    enforce exact phrasing — only that each role advertises *some*
    domain-specific keyword for its core area."""

    _CORE_KEYWORDS = {
        "backend-engineer": ("api", "auth", "database", "schema", "transaction"),
        "frontend-engineer": ("react", "ui", "css", "컴포넌트", "frontend"),
        "ai-engineer": ("llm", "rag", "memory", "prompt", "agent runtime"),
        "devops-engineer": ("docker", "ci", "deploy", "supervisor", "devops"),
        "qa-engineer": ("test", "regression", "smoke", "acceptance"),
        "product-designer": ("ux copy", "운영 ux", "디자인 토큰 문서", "사용자 흐름 문서"),
        "tech-lead": ("작업 분해", "권한 제안", "executor 추천"),
    }

    def test_each_role_lists_at_least_one_core_keyword(self) -> None:
        for role, keywords in self._CORE_KEYWORDS.items():
            with self.subTest(role=role):
                profile = _load_role_profile(role)
                priorities = profile["default_executor_priority"]
                joined = " ".join(priorities.get("high", [])).lower()
                hit = any(kw.lower() in joined for kw in keywords)
                self.assertTrue(
                    hit,
                    f"{role}: default_executor_priority.high lacks a core "
                    f"keyword from {keywords!r}",
                )


if __name__ == "__main__":
    unittest.main()
