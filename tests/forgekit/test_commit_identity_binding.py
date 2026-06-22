"""GW2-B — agent-identity ↔ commit trailer binding regression.

Proves scripts/ci_check_commit_messages.py:check_identity_binding (and its
integration into check_commit_messages):
- a commit claiming a KNOWN forgekit agent id (canonical OR alias) passes;
- a commit claiming an UNKNOWN id FAILS with the new reason (hard error);
- a commit with NO agent trailer (operator/human) passes — additive, no
  false-positive;
- the Approved-By approval-metadata trailer is parsed and enforced the same way;
- an author-email mismatch on a known agent is a warning (non-blocking), never a
  hard fail;
- the Co-Authored-By ban still fires (regression of GW2-A);
- (integration, self-skips if the real registry isn't importable) the actual
  forgekit_config identity registry accepts a real id and rejects a bogus one.

Pure / CI-safe: the core cases inject a fake ``is_known`` / ``git_identity_for``,
so no heavy import or git is needed; the integration case self-skips.
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


_KNOWN = {"frontend-engineer", "fe", "tech-lead", "gateway"}


def _is_known(agent_id: str) -> bool:
    return agent_id in _KNOWN


def _git_identity_for(agent_id: str) -> dict:
    # Minimal stand-in for forgekit_config.identity.git_identity_for.
    return {"email": "fe@forgekit.local", "canonical_id": agent_id}


def _ok(_msg, is_initial=False):
    class _R:
        ok = True
        reason = ""
        detail = ""

    return _R()


_CLEAN = "✨ 제목\n\n변경 이유\n- 이유\n\n주요 변경 사항\n- 변경\n\n비고\n- 없음\n"


class IdentityBindingCoreTest(unittest.TestCase):
    def test_known_agent_claim_passes(self) -> None:
        msg = _CLEAN + "\nForgekit-Agent: frontend-engineer\n"
        v = guard.check_identity_binding(
            [("sha", msg)], is_known=_is_known, git_identity_for=_git_identity_for
        )
        self.assertEqual(v, [])

    def test_known_agent_alias_claim_passes(self) -> None:
        msg = _CLEAN + "\nForgekit-Agent: fe\n"
        v = guard.check_identity_binding(
            [("sha", msg)], is_known=_is_known, git_identity_for=_git_identity_for
        )
        self.assertEqual(v, [])

    def test_unknown_agent_claim_fails(self) -> None:
        msg = _CLEAN + "\nForgekit-Agent: fk-bogus\n"
        v = guard.check_identity_binding(
            [("sha", msg)], is_known=_is_known, git_identity_for=_git_identity_for
        )
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].reason, guard.REASON_UNKNOWN_AGENT)
        self.assertEqual(v[0].severity, "error")
        self.assertIn("fk-bogus", v[0].detail)

    def test_no_trailer_operator_passes(self) -> None:
        v = guard.check_identity_binding(
            [("sha", _CLEAN)], is_known=_is_known, git_identity_for=_git_identity_for
        )
        self.assertEqual(v, [])

    def test_approved_by_known_passes(self) -> None:
        msg = _CLEAN + "\nApproved-By: tech-lead\n"
        v = guard.check_identity_binding(
            [("sha", msg)], is_known=_is_known, git_identity_for=_git_identity_for
        )
        self.assertEqual(v, [])

    def test_approved_by_unknown_fails(self) -> None:
        msg = _CLEAN + "\nApproved-By: nobody\n"
        v = guard.check_identity_binding(
            [("sha", msg)], is_known=_is_known, git_identity_for=_git_identity_for
        )
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].reason, guard.REASON_UNKNOWN_APPROVER)

    def test_both_trailers_each_validated(self) -> None:
        msg = _CLEAN + "\nForgekit-Agent: fk-bogus\nApproved-By: nobody\n"
        v = guard.check_identity_binding(
            [("sha", msg)], is_known=_is_known, git_identity_for=_git_identity_for
        )
        reasons = {x.reason for x in v}
        self.assertEqual(
            reasons, {guard.REASON_UNKNOWN_AGENT, guard.REASON_UNKNOWN_APPROVER}
        )

    def test_author_email_match_no_warning(self) -> None:
        msg = _CLEAN + "\nForgekit-Agent: frontend-engineer\n"
        v = guard.check_identity_binding(
            [("sha", msg, "fe@forgekit.local")],
            is_known=_is_known,
            git_identity_for=_git_identity_for,
        )
        self.assertEqual(v, [])

    def test_author_email_mismatch_is_warning_only(self) -> None:
        msg = _CLEAN + "\nForgekit-Agent: frontend-engineer\n"
        v = guard.check_identity_binding(
            [("sha", msg, "someone-else@example.com")],
            is_known=_is_known,
            git_identity_for=_git_identity_for,
        )
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].reason, "agent_author_email_mismatch")
        self.assertEqual(v[0].severity, "warning")


class IdentityBindingIntegrationWithMessagesTest(unittest.TestCase):
    """check_commit_messages wires the binding in alongside message checks."""

    def test_message_ok_but_unknown_agent_fails(self) -> None:
        msg = _CLEAN + "\nForgekit-Agent: fk-bogus\n"
        v = guard.check_commit_messages(
            [("sha", msg)],
            validate=_ok,
            is_known=_is_known,
            git_identity_for=_git_identity_for,
        )
        self.assertTrue(any(x.reason == guard.REASON_UNKNOWN_AGENT for x in v))

    def test_co_authored_by_still_banned_regression(self) -> None:
        msg = _CLEAN + "\nForgekit-Agent: frontend-engineer\nCo-Authored-By: X <x@e.com>\n"
        v = guard.check_commit_messages(
            [("sha", msg)],
            validate=_ok,
            is_known=_is_known,
            git_identity_for=_git_identity_for,
        )
        # Co-Authored-By fires; the known agent claim is fine → exactly the ban.
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].reason, guard.REASON_CO_AUTHORED_BY)

    def test_clean_known_agent_commit_fully_passes(self) -> None:
        msg = _CLEAN + "\nForgekit-Agent: tech-lead\nApproved-By: gateway\n"
        v = guard.check_commit_messages(
            [("sha", msg)],
            validate=_ok,
            is_known=_is_known,
            git_identity_for=_git_identity_for,
        )
        self.assertEqual(v, [])

    def test_check_identity_false_skips_binding(self) -> None:
        msg = _CLEAN + "\nForgekit-Agent: fk-bogus\n"
        v = guard.check_commit_messages(
            [("sha", msg)], validate=_ok, check_identity=False
        )
        self.assertEqual(v, [])


class IdentityBindingRealRegistryTest(unittest.TestCase):
    """Integration: reuse the actual forgekit_config identity registry."""

    def setUp(self) -> None:
        try:
            self.is_known, self.git_identity_for = guard._load_identity()
        except Exception as exc:  # noqa: BLE001 — env without forgekit_config
            self.skipTest(f"forgekit_config.identity not importable here: {exc}")

    def test_real_registry_accepts_canonical_and_alias_rejects_bogus(self) -> None:
        good_canonical = _CLEAN + "\nForgekit-Agent: frontend-engineer\n"
        good_alias = _CLEAN + "\nForgekit-Agent: fe\n"
        bad = _CLEAN + "\nForgekit-Agent: definitely-not-an-agent\n"
        self.assertEqual(
            guard.check_identity_binding(
                [("c", good_canonical)],
                is_known=self.is_known,
                git_identity_for=self.git_identity_for,
            ),
            [],
        )
        self.assertEqual(
            guard.check_identity_binding(
                [("a", good_alias)],
                is_known=self.is_known,
                git_identity_for=self.git_identity_for,
            ),
            [],
        )
        bad_v = guard.check_identity_binding(
            [("b", bad)],
            is_known=self.is_known,
            git_identity_for=self.git_identity_for,
        )
        self.assertTrue(bad_v)
        self.assertEqual(bad_v[0].reason, guard.REASON_UNKNOWN_AGENT)


if __name__ == "__main__":
    unittest.main()
