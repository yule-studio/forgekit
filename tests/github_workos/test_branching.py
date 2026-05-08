"""Branch policy — G3.

Pin the contract that:

  * Slug stays ascii / hyphenated / lowercase even with Korean / emoji
    / runs of punctuation.
  * github / discord source identifiers route into stable
    ``agent/<role>/...`` shapes.
  * Protected branches (main / master / develop / production / etc.) —
    including ``refs/heads/<protected>`` and ``origin/<protected>``
    fully-qualified shapes — refuse downstream writes.
  * Collision suffix appends ``-2``, ``-3``, …​ until ``exists_fn``
    returns False, and raises when the search exceeds the cap.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.github_workos.branching import (
    PROTECTED_BRANCHES,
    derive_branch_name,
    derive_branch_with_collision_suffix,
    is_protected_branch,
    slugify_for_branch,
)


@dataclass
class _StubPlan:
    title: str = ""
    primary_role: str = "backend-engineer"
    issue_number: Optional[int] = None
    session_id: Optional[str] = None
    source: str = "github"


class SlugifyTests(unittest.TestCase):
    def test_korean_collapses_to_ascii_marker(self) -> None:
        # Hangul gets replaced by the "ko" marker so the slug still
        # carries some signal after the ascii cast.
        slug = slugify_for_branch("Hermes 통합 검토 — 커밋 정책!")
        self.assertTrue(slug.startswith("hermes"))
        for ch in slug:
            self.assertTrue(ch.isascii(), slug)

    def test_pure_symbols_yield_empty_slug(self) -> None:
        # Caller falls back to issue/session id when slug is empty.
        self.assertEqual(slugify_for_branch("   ___---!!!   "), "")
        self.assertEqual(slugify_for_branch("???"), "")
        self.assertEqual(slugify_for_branch(""), "")
        self.assertEqual(slugify_for_branch(None), "")

    def test_long_input_capped_at_word_boundary(self) -> None:
        long = "Bug API 401 in users endpoint affecting Authentication flows"
        slug = slugify_for_branch(long, max_chars=30)
        self.assertLessEqual(len(slug), 30)
        # Word-boundary cut — should never end on a stray hyphen.
        self.assertFalse(slug.endswith("-"))

    def test_consecutive_punct_collapses_to_single_hyphen(self) -> None:
        slug = slugify_for_branch("foo!!!---bar___baz")
        self.assertEqual(slug, "foo-bar-baz")


class IsProtectedBranchTests(unittest.TestCase):
    def test_default_protected_names_match(self) -> None:
        for name in ("main", "master", "develop", "production", "prod"):
            with self.subTest(name=name):
                self.assertTrue(is_protected_branch(name))

    def test_case_insensitive(self) -> None:
        self.assertTrue(is_protected_branch("Main"))
        self.assertTrue(is_protected_branch("MASTER"))

    def test_qualified_refs_match(self) -> None:
        # Misconfigured callers occasionally pass ``refs/heads/main``;
        # we still refuse.
        self.assertTrue(is_protected_branch("refs/heads/main"))
        self.assertTrue(is_protected_branch("origin/master"))

    def test_normal_branches_pass(self) -> None:
        self.assertFalse(is_protected_branch("agent/backend-engineer/issue-1-foo"))
        self.assertFalse(is_protected_branch("feat/whatever"))
        self.assertFalse(is_protected_branch(""))
        self.assertFalse(is_protected_branch(None))


class DeriveBranchNameTests(unittest.TestCase):
    def test_github_source_uses_issue_anchor(self) -> None:
        plan = _StubPlan(
            title="Bug: API 401 in users endpoint",
            primary_role="backend-engineer",
            issue_number=42,
            source="github",
        )
        name = derive_branch_name(plan)
        self.assertTrue(name.startswith("agent/backend-engineer/issue-42-"))
        self.assertIn("bug", name)
        self.assertFalse(is_protected_branch(name))

    def test_discord_source_uses_session_anchor(self) -> None:
        plan = _StubPlan(
            title="Hermes 통합 검토",
            primary_role="ai-engineer",
            session_id="abc12345xyz",
            source="discord",
        )
        name = derive_branch_name(plan)
        self.assertTrue(name.startswith("agent/ai-engineer/discord-abc12345xyz-"))

    def test_unknown_source_with_session_falls_back_to_discord_anchor(self) -> None:
        plan = _StubPlan(
            title="x",
            primary_role="backend-engineer",
            session_id="sess-1",
            source="unknown",
        )
        name = derive_branch_name(plan)
        self.assertIn("discord-sess-1", name)

    def test_no_anchor_uses_fallback_seed(self) -> None:
        plan = _StubPlan(title="manual", primary_role="tech-lead", source="manual")
        name = derive_branch_name(plan, fallback_seed="audit-id-123")
        self.assertIn("work-audit-id-123", name)

    def test_role_short_form_used_in_branch(self) -> None:
        plan = _StubPlan(
            title="x",
            primary_role="engineering-agent/backend-engineer",
            issue_number=1,
            source="github",
        )
        name = derive_branch_name(plan)
        # Role qualifier is stripped — "backend-engineer" only.
        self.assertIn("agent/backend-engineer/", name)
        self.assertNotIn("engineering-agent/backend-engineer/", name)

    def test_protected_collision_raises(self) -> None:
        # Engineering one of the protected names into the slug
        # shouldn't be reachable in practice, but if a caller forces it
        # we refuse.
        plan = _StubPlan(
            title="main",
            primary_role="engineering",
            issue_number=None,
            session_id=None,
            source="manual",
        )
        # The fallback seed wraps it as ``work-main-main`` which is safe;
        # construct an explicit collision with a custom plan.
        # Forcing a literal protected ref via the role short:
        with self.assertRaises(ValueError):
            # We construct a name that ends in a protected token by
            # passing a plan whose role/seed combine to "main".
            # This isn't achievable through the public path, so we
            # exercise the assertion via derive_branch_with_collision_suffix.
            derive_branch_with_collision_suffix(
                "agent/role/main", exists_fn=lambda _: False
            )


class CollisionSuffixTests(unittest.TestCase):
    def test_no_collision_returns_base_name(self) -> None:
        out = derive_branch_with_collision_suffix(
            "agent/backend-engineer/issue-1-foo",
            exists_fn=lambda _: False,
        )
        self.assertEqual(out, "agent/backend-engineer/issue-1-foo")

    def test_collision_appends_2_then_3(self) -> None:
        existing = {
            "agent/x/issue-1-foo",
            "agent/x/issue-1-foo-2",
        }
        out = derive_branch_with_collision_suffix(
            "agent/x/issue-1-foo",
            exists_fn=lambda n: n in existing,
        )
        self.assertEqual(out, "agent/x/issue-1-foo-3")

    def test_runaway_raises(self) -> None:
        # Always-exists fn → bounded loop must raise.
        with self.assertRaises(ValueError):
            derive_branch_with_collision_suffix(
                "agent/x/issue-1-foo",
                exists_fn=lambda _: True,
                max_attempts=3,
            )


if __name__ == "__main__":
    unittest.main()
