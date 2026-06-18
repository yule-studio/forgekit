"""Git attribution + GitHub App status — registry-backed, honest.

Proves git author + commit trailers come from the canonical registry (alias-safe),
trailers never fabricate absent values, and the GitHub App status honestly
distinguishes dedicated / partial / shared-fallback / missing / planned from env.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.identity import attribution as attr


class AuthorTests(unittest.TestCase):
    def test_git_author_from_registry(self) -> None:
        self.assertEqual(attr.git_author_for("backend-engineer"),
                         "Forgekit Backend <be@forgekit.local>")

    def test_alias_resolves_for_author(self) -> None:
        self.assertEqual(attr.git_author_for("be"), attr.git_author_for("backend-engineer"))


class AppStatusTests(unittest.TestCase):
    def test_dedicated_when_all_three_present(self) -> None:
        env = {"YULE_GITHUB_APP_TECH_LEAD_APP_ID": "1",
               "YULE_GITHUB_APP_TECH_LEAD_INSTALLATION_ID": "2",
               "YULE_GITHUB_APP_TECH_LEAD_PRIVATE_KEY_PEM": "key"}
        self.assertEqual(attr.github_app_status("tech-lead", env), attr.APP_DEDICATED)

    def test_partial_when_some_present(self) -> None:
        env = {"YULE_GITHUB_APP_TECH_LEAD_APP_ID": "1"}
        self.assertEqual(attr.github_app_status("tech-lead", env), attr.APP_PARTIAL)

    def test_shared_fallback(self) -> None:
        env = {"YULE_GITHUB_APP_SHARED_APP_ID": "1",
               "YULE_GITHUB_APP_SHARED_INSTALLATION_ID": "2",
               "YULE_GITHUB_APP_SHARED_PRIVATE_KEY_PEM": "k"}
        self.assertEqual(attr.github_app_status("tech-lead", env), attr.APP_SHARED)

    def test_missing_when_nothing(self) -> None:
        self.assertEqual(attr.github_app_status("tech-lead", {}), attr.APP_MISSING)


class TrailerTests(unittest.TestCase):
    def test_required_trailers_present(self) -> None:
        tr = attr.commit_trailers("be", flow="repo-autopilot", env={})
        joined = "\n".join(tr)
        self.assertIn("Forgekit-Agent: backend-engineer", joined)   # canonical, not 'be'
        self.assertIn("Forgekit-Role: Backend", joined)
        self.assertIn("Forgekit-Dept: engineering", joined)
        self.assertIn("Forgekit-Flow: repo-autopilot", joined)
        self.assertIn("Forgekit-GitHub-App: missing", joined)       # honest env status

    def test_absent_optional_trailers_not_fabricated(self) -> None:
        tr = attr.commit_trailers("tech-lead", env={})
        joined = "\n".join(tr)
        self.assertNotIn("Forgekit-Handoff-From", joined)           # no handoff → not emitted
        self.assertNotIn("Forgekit-Mode", joined)

    def test_whoami_render_is_honest(self) -> None:
        lines = "\n".join(attr.render_whoami_lines("frontend-engineer", env={}))
        self.assertIn("frontend-engineer", lines)
        self.assertIn("Forgekit Frontend <fe@forgekit.local>", lines)
        self.assertIn("missing", lines)                              # no creds → honest


class WhoamiRouterTests(unittest.TestCase):
    def test_whoami_command_routes(self) -> None:
        from pathlib import Path

        from forgekit_console.commands.parser import parse_input
        from forgekit_console.commands.router import build_default_context, route

        ctx = build_default_context(Path("."))
        audit = route(parse_input("/whoami"), ctx)
        self.assertIn("agent identity", "\n".join(audit.lines))
        detail = route(parse_input("/whoami tech-lead"), ctx)
        self.assertIn("Forgekit Tech Lead", "\n".join(detail.lines))


if __name__ == "__main__":
    unittest.main()
