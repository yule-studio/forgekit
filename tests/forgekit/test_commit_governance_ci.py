"""GW2 — CI commit-governance guard regression.

Proves scripts/ci_check_commit_messages.py:
- bans the Co-Authored-By trailer even on an otherwise-valid message;
- surfaces a policy failure (reason/detail) as a violation;
- passes a clean commit;
- (integration, skipped if the shared policy can't be imported in this env)
  REUSES the real repo_write_policy — a conventional message passes, a bad one
  fails — so the guard and the local hook share one rule set (no duplication).

Pure / CI-safe: the core cases inject a fake validator, so no heavy import or git
is needed; the integration case self-skips when yule_engineering is absent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _rel in ("scripts",):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ci_check_commit_messages as guard


class _Res:
    def __init__(self, ok: bool, reason: str = "", detail: str = "") -> None:
        self.ok = ok
        self.reason = reason
        self.detail = detail


def _ok(_msg, is_initial=False):
    return _Res(True)


def _bad(_msg, is_initial=False):
    return _Res(False, "invalid_commit_gitmoji", "first token must be a gitmoji")


_CLEAN = "✨ 제목\n\n변경 이유\n- 이유\n\n주요 변경 사항\n- 변경\n\n비고\n- 없음\n"


class CommitGovernanceCoreTest(unittest.TestCase):
    def test_co_authored_by_rejected_even_if_format_ok(self) -> None:
        msg = _CLEAN + "\nCo-Authored-By: Someone <s@example.com>\n"
        v = guard.check_commit_messages([("sha1", msg)], validate=_ok)
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].reason, guard.REASON_CO_AUTHORED_BY)
        self.assertEqual(v[0].sha, "sha1")

    def test_clean_commit_passes(self) -> None:
        v = guard.check_commit_messages([("sha1", _CLEAN)], validate=_ok)
        self.assertEqual(v, [])

    def test_policy_failure_is_surfaced(self) -> None:
        v = guard.check_commit_messages([("sha2", "bad title")], validate=_bad)
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].reason, "invalid_commit_gitmoji")

    def test_multiple_commits_aggregate(self) -> None:
        v = guard.check_commit_messages(
            [("a", _CLEAN), ("b", "bad title")], validate=_bad
        )
        # _bad fails both; "a" also clean of co-authored so just the policy fail each
        self.assertEqual({x.sha for x in v}, {"a", "b"})


class CommitGovernanceRealPolicyTest(unittest.TestCase):
    """Integration: reuse the actual shared policy (skips if not importable)."""

    def setUp(self) -> None:
        try:
            self.validate = guard._load_validator()
        except Exception as exc:  # noqa: BLE001 — env without yule_engineering
            self.skipTest(f"shared repo_write_policy not importable here: {exc}")

    def test_real_policy_accepts_conventional_rejects_bad(self) -> None:
        ok = guard.check_commit_messages([("good", _CLEAN)], validate=self.validate)
        self.assertEqual(ok, [], f"conventional commit should pass; got {ok}")
        bad = guard.check_commit_messages(
            [("bad", "no gitmoji and no sections")], validate=self.validate
        )
        self.assertTrue(bad, "non-conventional commit must be rejected by the shared policy")


if __name__ == "__main__":
    unittest.main()
