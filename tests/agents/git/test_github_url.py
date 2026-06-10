"""P0-H stage 2 commit 2 — GitHub URL parser unit tests.

Covers the 5 + 2 (repo / issue / pull_request / commit / compare /
tree / blob) shapes, edge cases, and legacy compatibility.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.git.github_url import (
    GithubTarget,
    parse_github_target,
    parse_github_targets,
    parse_github_url,
)


class RepoShapeTests(unittest.TestCase):
    def test_repo_root(self) -> None:
        t = parse_github_target("https://github.com/yule-studio/yule-studio-agent")
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "repo")
        self.assertEqual(t.owner, "yule-studio")
        self.assertEqual(t.repo, "yule-studio-agent")
        self.assertIsNone(t.number)

    def test_repo_with_git_suffix(self) -> None:
        t = parse_github_target("https://github.com/anthropic/example.git")
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "repo")
        self.assertEqual(t.repo, "example")  # .git stripped

    def test_unrecognized_suffix_falls_to_repo(self) -> None:
        # /settings, /releases etc. — kind stays "repo".
        t = parse_github_target("https://github.com/foo/bar/settings")
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "repo")
        self.assertEqual(t.owner, "foo")


class IssueShapeTests(unittest.TestCase):
    def test_issue(self) -> None:
        t = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/issues/140"
        )
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "issue")
        self.assertEqual(t.number, 140)

    def test_issue_with_anchor(self) -> None:
        t = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/issues/140#issuecomment-1"
        )
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "issue")
        self.assertEqual(t.number, 140)


class PullRequestShapeTests(unittest.TestCase):
    def test_pull(self) -> None:
        t = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/pull/142"
        )
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "pull_request")
        self.assertEqual(t.number, 142)

    def test_pulls_alias_accepted(self) -> None:
        # Defensive: some links use /pulls/<n> in copy-paste — we accept.
        t = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/pulls/142"
        )
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "pull_request")


class CommitShapeTests(unittest.TestCase):
    def test_commit_short_sha(self) -> None:
        t = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/commit/a4e8507"
        )
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "commit")
        self.assertEqual(t.sha, "a4e8507")

    def test_commit_full_sha(self) -> None:
        t = parse_github_target(
            "https://github.com/foo/bar/commit/abcdef1234567890abcdef1234567890abcdef12"
        )
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "commit")
        assert t.sha is not None
        self.assertEqual(len(t.sha), 40)


class CompareShapeTests(unittest.TestCase):
    def test_compare_branches(self) -> None:
        t = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/compare/main...feature/x"
        )
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "compare")
        self.assertEqual(t.compare_from, "main")
        self.assertEqual(t.compare_to, "feature/x")

    def test_compare_sha(self) -> None:
        t = parse_github_target(
            "https://github.com/foo/bar/compare/abcdef1...abcdef2"
        )
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "compare")


class TreeBlobShapeTests(unittest.TestCase):
    def test_tree_branch(self) -> None:
        t = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/tree/feature/p0g"
        )
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "tree")
        self.assertEqual(t.branch_or_sha, "feature")
        # remaining /p0g goes into file_path (path-after-ref shape).
        self.assertEqual(t.file_path, "/p0g")

    def test_blob_file(self) -> None:
        t = parse_github_target(
            "https://github.com/foo/bar/blob/main/apps/engineering-agent/src/yule_engineering/main.py"
        )
        self.assertIsNotNone(t)
        assert t is not None
        self.assertEqual(t.kind, "blob")
        self.assertEqual(t.branch_or_sha, "main")
        self.assertEqual(t.file_path, "/apps/engineering-agent/src/yule_engineering/main.py")


class NonGithubTests(unittest.TestCase):
    def test_non_github_url(self) -> None:
        self.assertIsNone(parse_github_target("https://gitlab.com/foo/bar"))

    def test_none_input(self) -> None:
        self.assertIsNone(parse_github_target(None))

    def test_empty_string(self) -> None:
        self.assertIsNone(parse_github_target(""))

    def test_malformed_url(self) -> None:
        self.assertIsNone(parse_github_target("not a url"))


class RoundTripTests(unittest.TestCase):
    def test_to_dict_from_dict_round_trip(self) -> None:
        t = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/pull/142"
        )
        assert t is not None
        payload = t.to_dict()
        restored = GithubTarget.from_dict(payload)
        self.assertEqual(restored.kind, "pull_request")
        self.assertEqual(restored.number, 142)
        self.assertEqual(restored.owner, "yule-studio")

    def test_to_dict_omits_none(self) -> None:
        t = parse_github_target("https://github.com/foo/bar")
        assert t is not None
        payload = t.to_dict()
        self.assertNotIn("number", payload)
        self.assertNotIn("sha", payload)


class MultiUrlTests(unittest.TestCase):
    def test_parse_targets_filters_non_github(self) -> None:
        urls = (
            "https://github.com/foo/bar/issues/1",
            "https://gitlab.com/baz/qux",
            "https://github.com/foo/bar/pull/2",
            None,
            "",
        )
        targets = parse_github_targets(urls)
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0].kind, "issue")
        self.assertEqual(targets[1].kind, "pull_request")


class LegacyCompatibilityTests(unittest.TestCase):
    """The legacy ``parse_github_url`` shape is still emitted for collector."""

    def test_issue_legacy_shape(self) -> None:
        result = parse_github_url("https://github.com/foo/bar/issues/5")
        self.assertEqual(
            result,
            {"kind": "issue", "owner": "foo", "repo": "bar", "number": 5},
        )

    def test_pr_legacy_shape(self) -> None:
        result = parse_github_url("https://github.com/foo/bar/pull/9")
        self.assertEqual(
            result,
            {"kind": "pull_request", "owner": "foo", "repo": "bar", "number": 9},
        )

    def test_legacy_returns_none_for_commit(self) -> None:
        # Pre-P0-H behavior — collector falls through for non-issue/PR URLs.
        self.assertIsNone(parse_github_url("https://github.com/foo/bar/commit/abc1234"))

    def test_legacy_returns_none_for_repo(self) -> None:
        self.assertIsNone(parse_github_url("https://github.com/foo/bar"))


if __name__ == "__main__":
    unittest.main()
