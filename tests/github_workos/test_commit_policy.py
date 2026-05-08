"""Commit author / committer policy — G3.

Pin the contract that:

  * Author = owner (human) and committer = App bot are split cleanly.
  * Missing names / emails surface explicit warnings instead of
    silently producing an unsigned commit.
  * Email validation accepts verified shapes + the GitHub noreply
    form, and rejects malformed strings.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.github_workos.commit_policy import (
    CommitAuthor,
    CommitIdentity,
    derive_commit_identity,
    is_acceptable_commit_email,
    validate_commit_identity,
)


@dataclass
class _StubAccount:
    name: Optional[str] = None
    email: Optional[str] = None
    login: Optional[str] = None


class IsAcceptableCommitEmailTests(unittest.TestCase):
    def test_noreply_with_id_accepted(self) -> None:
        self.assertTrue(
            is_acceptable_commit_email("12345+codwithyc@users.noreply.github.com")
        )

    def test_noreply_legacy_form_accepted(self) -> None:
        self.assertTrue(
            is_acceptable_commit_email("codwithyc@users.noreply.github.com")
        )

    def test_regular_email_accepted(self) -> None:
        self.assertTrue(is_acceptable_commit_email("user@example.com"))

    def test_malformed_rejected(self) -> None:
        self.assertFalse(is_acceptable_commit_email(""))
        self.assertFalse(is_acceptable_commit_email(None))
        self.assertFalse(is_acceptable_commit_email("no-at-sign"))
        self.assertFalse(is_acceptable_commit_email("user@"))
        self.assertFalse(is_acceptable_commit_email("@example.com"))
        # Newline injection is a classic git-config attack — reject.
        self.assertFalse(
            is_acceptable_commit_email("user@example.com\nUser2: x")
        )


class DeriveCommitIdentityTests(unittest.TestCase):
    def test_owner_as_author_app_as_committer(self) -> None:
        identity = derive_commit_identity(
            owner=_StubAccount(
                name="Yule Owner",
                email="123+codwithyc@users.noreply.github.com",
                login="codwithyc",
            ),
            app=_StubAccount(
                name="Yule App",
                email="999+yule-studio-agent[bot]@users.noreply.github.com",
                login="yule-studio-agent[bot]",
            ),
        )
        self.assertEqual(identity.author.name, "Yule Owner")
        self.assertEqual(identity.committer.login, "yule-studio-agent[bot]")
        self.assertTrue(identity.committer.is_app_bot)
        self.assertFalse(identity.author.is_app_bot)
        # Distinct emails — App audit benefit preserved.
        self.assertNotEqual(identity.author.email, identity.committer.email)
        self.assertFalse(identity.has_warnings)

    def test_missing_owner_email_falls_back_to_noreply_with_warning(self) -> None:
        identity = derive_commit_identity(
            owner=_StubAccount(name="Owner", login="codwithyc"),
            app=_StubAccount(
                name="Yule App",
                email="999+yule-studio-agent[bot]@users.noreply.github.com",
                login="yule-studio-agent[bot]",
            ),
        )
        # Synthesised noreply.
        self.assertTrue(
            identity.author.email.endswith("@users.noreply.github.com")
        )
        # No warning because the synthesised email is valid.
        self.assertFalse(any("acceptable" in w for w in identity.warnings))

    def test_missing_owner_name_warns(self) -> None:
        identity = derive_commit_identity(
            owner=_StubAccount(email="user@example.com", login="codwithyc"),
            app=_StubAccount(
                name="App",
                email="999+app[bot]@users.noreply.github.com",
                login="app[bot]",
            ),
        )
        self.assertTrue(identity.has_warnings)
        joined = " ".join(identity.warnings)
        self.assertIn("name", joined)

    def test_unverifiable_email_surfaces_warning(self) -> None:
        identity = derive_commit_identity(
            owner=_StubAccount(name="Owner", email="not-an-email", login="codwithyc"),
            app=_StubAccount(
                name="App",
                email="999+app[bot]@users.noreply.github.com",
                login="app[bot]",
            ),
        )
        self.assertTrue(identity.has_warnings)
        joined = " ".join(identity.warnings)
        self.assertIn("acceptable", joined)
        # noreply hint in the message so operator knows the fix.
        self.assertIn("noreply", joined)


class ValidateCommitIdentityTests(unittest.TestCase):
    def test_round_trip_passes_when_emails_are_clean(self) -> None:
        identity = CommitIdentity(
            author=CommitAuthor(
                name="Owner",
                email="123+codwithyc@users.noreply.github.com",
                login="codwithyc",
            ),
            committer=CommitAuthor(
                name="App",
                email="999+app[bot]@users.noreply.github.com",
                login="app[bot]",
            ),
        )
        self.assertEqual(validate_commit_identity(identity), ())

    def test_identical_author_and_committer_email_warns(self) -> None:
        same = "123+codwithyc@users.noreply.github.com"
        identity = CommitIdentity(
            author=CommitAuthor(name="Owner", email=same, login="codwithyc"),
            committer=CommitAuthor(name="App", email=same, login="codwithyc"),
        )
        warnings = validate_commit_identity(identity)
        self.assertTrue(any("identical" in w for w in warnings))

    def test_committer_with_bad_email_warns(self) -> None:
        identity = CommitIdentity(
            author=CommitAuthor(
                name="Owner",
                email="123+codwithyc@users.noreply.github.com",
                login="codwithyc",
            ),
            committer=CommitAuthor(name="App", email="bad", login="app[bot]"),
        )
        warnings = validate_commit_identity(identity)
        self.assertTrue(any("committer.email" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
