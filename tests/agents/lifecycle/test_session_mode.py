"""P0-H stage 2 commit 4 — session mode/topology ask-once.

Covers the contract in ``docs/autonomy-policy.md §0`` (stage 1):

  1. Fresh session → default applied, ``needs_question=True``.
  2. Session with mode already set → returned as-is, never re-prompts.
  3. Explicit user hint in message → ``decided_by=user_explicit`` when
     all three fields hinted; ``gateway_inferred`` when partial.
  4. ``explicit_mode_change`` overrides with ``decided_by=user_explicit``.
  5. Hint parser recognizes Korean / English aliases.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.session_mode import (
    DECIDED_BY_INFERRED,
    DECIDED_BY_USER,
    EXTRA_DECIDED_AT,
    EXTRA_DECIDED_BY,
    EXTRA_SCOPE,
    EXTRA_TOPOLOGY,
    EXTRA_WORK_MODE,
    SCOPE_CROSS_REPO,
    SCOPE_DEFAULT,
    SCOPE_LAYER,
    TOPOLOGY_DEFAULT,
    TOPOLOGY_MULTI,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
    WORK_MODE_DEFAULT,
    build_mode_question_text,
    ensure_session_mode,
    explicit_mode_change,
    parse_mode_hints,
    read_session_mode,
)


_FIXED_NOW = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


class FreshSessionTests(unittest.TestCase):
    def test_empty_extra_writes_defaults_and_asks(self) -> None:
        extra: dict = {}
        decision = ensure_session_mode(extra, now=_FIXED_NOW)
        self.assertEqual(decision.mode.work_mode, WORK_MODE_DEFAULT)
        self.assertEqual(decision.mode.topology, TOPOLOGY_DEFAULT)
        self.assertEqual(decision.mode.scope, SCOPE_DEFAULT)
        self.assertTrue(decision.needs_question)
        self.assertTrue(decision.persisted)
        self.assertTrue(decision.changed)
        # All 5 keys present in extra.
        self.assertEqual(extra[EXTRA_WORK_MODE], WORK_MODE_DEFAULT)
        self.assertEqual(extra[EXTRA_TOPOLOGY], TOPOLOGY_DEFAULT)
        self.assertEqual(extra[EXTRA_SCOPE], SCOPE_DEFAULT)
        self.assertEqual(extra[EXTRA_DECIDED_BY], DECIDED_BY_INFERRED)
        self.assertEqual(extra[EXTRA_DECIDED_AT], "2026-05-14T12:00:00+00:00")

    def test_apply_defaults_false_does_not_persist(self) -> None:
        extra: dict = {}
        decision = ensure_session_mode(extra, apply_defaults=False)
        self.assertTrue(decision.needs_question)
        self.assertFalse(decision.persisted)
        self.assertFalse(decision.changed)
        self.assertNotIn(EXTRA_WORK_MODE, extra)


class AlreadyDecidedTests(unittest.TestCase):
    """Once mode is set, no re-prompts within the same session."""

    def test_existing_valid_mode_returns_as_is(self) -> None:
        extra = {
            EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
            EXTRA_TOPOLOGY: TOPOLOGY_MULTI,
            EXTRA_SCOPE: SCOPE_CROSS_REPO,
            EXTRA_DECIDED_BY: DECIDED_BY_USER,
            EXTRA_DECIDED_AT: "2026-05-10T00:00:00+00:00",
        }
        snapshot = dict(extra)
        decision = ensure_session_mode(extra, now=_FIXED_NOW)
        self.assertEqual(decision.mode.work_mode, WORK_MODE_AUTONOMOUS)
        self.assertEqual(decision.mode.topology, TOPOLOGY_MULTI)
        self.assertEqual(decision.mode.scope, SCOPE_CROSS_REPO)
        self.assertFalse(decision.needs_question)
        self.assertTrue(decision.persisted)
        self.assertFalse(decision.changed)
        # Extra unchanged.
        self.assertEqual(extra, snapshot)

    def test_invalid_mode_is_treated_as_unset(self) -> None:
        # Corrupt extra with an unknown value → falls back to default
        # path so we don't propagate bad data.
        extra = {EXTRA_WORK_MODE: "weird_unknown_mode"}
        decision = ensure_session_mode(extra, now=_FIXED_NOW)
        self.assertTrue(decision.needs_question)
        self.assertEqual(extra[EXTRA_WORK_MODE], WORK_MODE_DEFAULT)


class UserHintTests(unittest.TestCase):
    def test_all_three_hints_user_explicit(self) -> None:
        extra: dict = {}
        decision = ensure_session_mode(
            extra,
            user_hint_work_mode=WORK_MODE_AUTONOMOUS,
            user_hint_topology=TOPOLOGY_MULTI,
            user_hint_scope=SCOPE_CROSS_REPO,
            now=_FIXED_NOW,
        )
        self.assertEqual(decision.mode.work_mode, WORK_MODE_AUTONOMOUS)
        self.assertEqual(decision.mode.decided_by, DECIDED_BY_USER)
        self.assertFalse(decision.needs_question)
        self.assertTrue(decision.persisted)

    def test_partial_hint_gateway_inferred_still_asks(self) -> None:
        extra: dict = {}
        decision = ensure_session_mode(
            extra,
            user_hint_work_mode=WORK_MODE_AUTONOMOUS,
            now=_FIXED_NOW,
        )
        self.assertEqual(decision.mode.work_mode, WORK_MODE_AUTONOMOUS)
        self.assertEqual(decision.mode.topology, TOPOLOGY_DEFAULT)
        self.assertEqual(decision.mode.decided_by, DECIDED_BY_INFERRED)
        self.assertTrue(decision.needs_question)

    def test_invalid_hint_silently_ignored(self) -> None:
        extra: dict = {}
        decision = ensure_session_mode(
            extra, user_hint_work_mode="not_a_mode", now=_FIXED_NOW
        )
        # No hint applied → defaults + needs_question.
        self.assertEqual(decision.mode.work_mode, WORK_MODE_DEFAULT)
        self.assertEqual(decision.mode.decided_by, DECIDED_BY_INFERRED)


class ExplicitChangeTests(unittest.TestCase):
    def test_partial_change_preserves_other_fields(self) -> None:
        extra = {
            EXTRA_WORK_MODE: WORK_MODE_APPROVAL,
            EXTRA_TOPOLOGY: TOPOLOGY_DEFAULT,
            EXTRA_SCOPE: SCOPE_DEFAULT,
            EXTRA_DECIDED_BY: DECIDED_BY_INFERRED,
            EXTRA_DECIDED_AT: "2026-05-13T00:00:00+00:00",
        }
        decision = explicit_mode_change(
            extra, work_mode=WORK_MODE_AUTONOMOUS, now=_FIXED_NOW
        )
        self.assertEqual(decision.mode.work_mode, WORK_MODE_AUTONOMOUS)
        self.assertEqual(decision.mode.topology, TOPOLOGY_DEFAULT)
        self.assertEqual(decision.mode.scope, SCOPE_DEFAULT)
        self.assertEqual(decision.mode.decided_by, DECIDED_BY_USER)
        self.assertTrue(decision.changed)
        # decided_at bumped.
        self.assertEqual(extra[EXTRA_DECIDED_AT], "2026-05-14T12:00:00+00:00")

    def test_no_change_when_values_match(self) -> None:
        extra = {
            EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
            EXTRA_TOPOLOGY: TOPOLOGY_DEFAULT,
            EXTRA_SCOPE: SCOPE_DEFAULT,
            EXTRA_DECIDED_BY: DECIDED_BY_USER,
            EXTRA_DECIDED_AT: "2026-05-13T00:00:00+00:00",
        }
        decision = explicit_mode_change(
            extra, work_mode=WORK_MODE_AUTONOMOUS, now=_FIXED_NOW
        )
        self.assertFalse(decision.changed)
        # decided_by stays user_explicit; decided_at still bumped.
        self.assertEqual(decision.mode.decided_by, DECIDED_BY_USER)


class HintParserTests(unittest.TestCase):
    def test_korean_autonomous_merge(self) -> None:
        hints = parse_mode_hints("이번 작업은 자율 머지로 진행해줘")
        self.assertEqual(hints["work_mode"], WORK_MODE_AUTONOMOUS)

    def test_english_approval_required(self) -> None:
        hints = parse_mode_hints("Use approval_required mode")
        self.assertEqual(hints["work_mode"], WORK_MODE_APPROVAL)

    def test_topology_multi_repo(self) -> None:
        hints = parse_mode_hints("topology: multi_repo 로 잡아줘")
        self.assertEqual(hints["topology"], TOPOLOGY_MULTI)

    def test_scope_cross_repo_program(self) -> None:
        hints = parse_mode_hints("scope cross_repo_program 으로 가자")
        self.assertEqual(hints["scope"], SCOPE_CROSS_REPO)

    def test_scope_layer_scoped(self) -> None:
        hints = parse_mode_hints("scope: layer_scoped")
        self.assertEqual(hints["scope"], SCOPE_LAYER)

    def test_no_hint(self) -> None:
        hints = parse_mode_hints("그냥 작업해 줘")
        self.assertEqual(
            hints, {"work_mode": None, "topology": None, "scope": None}
        )


class NoRepeatedQuestionRegressionTests(unittest.TestCase):
    """The core acceptance criterion: same session, no re-prompt."""

    def test_back_to_back_calls_only_ask_once(self) -> None:
        extra: dict = {}
        first = ensure_session_mode(extra, now=_FIXED_NOW)
        self.assertTrue(first.needs_question)
        self.assertTrue(first.persisted)
        # Subsequent calls within the same session — never re-ask.
        for _ in range(5):
            decision = ensure_session_mode(extra)
            self.assertFalse(decision.needs_question)
            self.assertFalse(decision.changed)
            self.assertTrue(decision.persisted)


class QuestionRenderTests(unittest.TestCase):
    def test_question_text_includes_all_three_fields(self) -> None:
        extra: dict = {}
        decision = ensure_session_mode(extra, now=_FIXED_NOW)
        text = build_mode_question_text(decision)
        self.assertIn(WORK_MODE_DEFAULT, text)
        self.assertIn(TOPOLOGY_DEFAULT, text)
        self.assertIn(SCOPE_DEFAULT, text)
        self.assertIn("다시 묻지 않습니다", text)


class ReadSessionModeTests(unittest.TestCase):
    def test_returns_none_for_missing_fields(self) -> None:
        self.assertIsNone(read_session_mode({}))
        self.assertIsNone(read_session_mode({EXTRA_WORK_MODE: WORK_MODE_DEFAULT}))

    def test_returns_session_mode_when_all_present(self) -> None:
        extra = {
            EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
            EXTRA_TOPOLOGY: TOPOLOGY_MULTI,
            EXTRA_SCOPE: SCOPE_CROSS_REPO,
            EXTRA_DECIDED_BY: DECIDED_BY_USER,
            EXTRA_DECIDED_AT: "2026-05-14T12:00:00+00:00",
        }
        result = read_session_mode(extra)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.work_mode, WORK_MODE_AUTONOMOUS)
        self.assertEqual(result.decided_by, DECIDED_BY_USER)


if __name__ == "__main__":
    unittest.main()
