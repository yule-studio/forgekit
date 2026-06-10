"""Identity surface — 7-role coverage, GitHub App actor, owner-as-author."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.github_workos.identity import (
    COMMIT_AUTHOR_POLICY_OWNER_AS_AUTHOR,
    GITHUB_APP_ACTOR,
    SUPPORTED_ROLE_IDS,
    AgentIdentity,
    agent_identity,
    all_agent_identities,
)


class IdentityCoverageTests(unittest.TestCase):
    def test_seven_roles_present(self) -> None:
        ids = set(all_agent_identities().keys())
        self.assertEqual(
            ids,
            {
                "tech-lead",
                "backend-engineer",
                "frontend-engineer",
                "devops-engineer",
                "qa-engineer",
                "ai-engineer",
                "product-designer",
            },
        )
        # SUPPORTED_ROLE_IDS preserves a stable order callers can rely on.
        self.assertEqual(set(SUPPORTED_ROLE_IDS), ids)

    def test_every_identity_has_required_fields(self) -> None:
        for role_id, identity in all_agent_identities().items():
            self.assertIsInstance(identity, AgentIdentity)
            self.assertEqual(identity.role_id, role_id)
            self.assertTrue(
                identity.github_display_name,
                msg=f"role={role_id} missing github_display_name",
            )
            self.assertEqual(
                identity.github_app_actor,
                GITHUB_APP_ACTOR,
                msg=f"role={role_id} must use the shared GitHub App actor",
            )
            self.assertEqual(
                identity.commit_author_policy,
                COMMIT_AUTHOR_POLICY_OWNER_AS_AUTHOR,
                msg=f"role={role_id} must use owner-as-author commits",
            )
            # Coding + review surfaces are non-empty for every role.
            self.assertTrue(
                identity.coding_surface,
                msg=f"role={role_id} missing coding_surface",
            )
            self.assertTrue(
                identity.review_surface,
                msg=f"role={role_id} missing review_surface",
            )

    def test_unknown_role_raises(self) -> None:
        with self.assertRaises(KeyError):
            agent_identity("data-analyst")  # not a role we ship.


class GitHubAppActorTests(unittest.TestCase):
    def test_actor_constant_is_engineering_agent_bot(self) -> None:
        self.assertEqual(
            GITHUB_APP_ACTOR, "yule-studio-engineering-agent[bot]"
        )

    def test_owner_as_author_policy_id_is_explicit_string(self) -> None:
        # The string is quoted in audit rows / docs — pin it.
        self.assertEqual(
            COMMIT_AUTHOR_POLICY_OWNER_AS_AUTHOR, "owner-as-author"
        )


class RoleSpecificSurfaceTests(unittest.TestCase):
    """A handful of pinned surface entries so the triage's
    ``files_or_domains_to_inspect`` list stays predictable."""

    def test_backend_coding_surface_includes_java_or_spring(self) -> None:
        identity = agent_identity("backend-engineer")
        joined = " ".join(identity.coding_surface).lower()
        self.assertTrue(
            ".java" in joined or "build.gradle" in joined or "pom.xml" in joined,
            msg=f"backend coding surface should mention Java/Spring: {joined!r}",
        )

    def test_frontend_coding_surface_includes_app_or_components(self) -> None:
        identity = agent_identity("frontend-engineer")
        joined = " ".join(identity.coding_surface).lower()
        self.assertTrue(
            "app/" in joined or "components/" in joined or ".tsx" in joined,
            msg=f"frontend coding surface should mention app/components/tsx: {joined!r}",
        )

    def test_devops_coding_surface_includes_workflows(self) -> None:
        identity = agent_identity("devops-engineer")
        joined = " ".join(identity.coding_surface).lower()
        self.assertIn(".github/workflows", joined)

    def test_qa_coding_surface_includes_tests_dir(self) -> None:
        identity = agent_identity("qa-engineer")
        joined = " ".join(identity.coding_surface).lower()
        self.assertIn("tests/", joined)

    def test_techlead_review_surface_is_repo_wide(self) -> None:
        identity = agent_identity("tech-lead")
        # Tech-lead is sign-off owner — review_surface must include
        # the repo-wide glob.
        self.assertIn("**/*", identity.review_surface)


if __name__ == "__main__":
    unittest.main()
