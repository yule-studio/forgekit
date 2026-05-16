"""ProblemLedger + ProblemObject — self-improvement runtime tests."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.problem_ledger import (
    ProblemLedger,
    ProblemObject,
    ProblemStatus,
    build_problem_signature,
    default_ledger_path,
)


_FIXED_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)


class SignatureBuilderTests(unittest.TestCase):
    def test_same_signal_same_evidence_same_signature(self) -> None:
        a = build_problem_signature(
            signal_id="example",
            evidence={"topic_key": "k", "service_id": "svc"},
        )
        b = build_problem_signature(
            signal_id="example",
            evidence={"service_id": "svc", "topic_key": "k"},
        )
        self.assertEqual(a, b)

    def test_different_evidence_different_signature(self) -> None:
        a = build_problem_signature(
            signal_id="example", evidence={"topic_key": "k1"}
        )
        b = build_problem_signature(
            signal_id="example", evidence={"topic_key": "k2"}
        )
        self.assertNotEqual(a, b)

    def test_volatile_keys_ignored(self) -> None:
        a = build_problem_signature(
            signal_id="example",
            evidence={"topic_key": "k", "count": 5, "detected_at": "t1"},
        )
        b = build_problem_signature(
            signal_id="example",
            evidence={"topic_key": "k", "count": 10, "detected_at": "t2"},
        )
        self.assertEqual(a, b)


class LedgerLifecycleTests(unittest.TestCase):
    def test_register_new_problem_returns_is_new_true(self) -> None:
        ledger = ProblemLedger()
        problem, is_new = ledger.register_or_update(
            signal_id="x",
            severity="medium",
            summary="x",
            evidence={"topic_key": "k"},
            now=_FIXED_NOW,
        )
        self.assertTrue(is_new)
        self.assertEqual(problem.occurrence_count, 1)
        self.assertEqual(problem.status, ProblemStatus.DETECTED)

    def test_repeat_register_bumps_occurrence(self) -> None:
        ledger = ProblemLedger()
        ledger.register_or_update(
            signal_id="x", severity="medium", summary="x", now=_FIXED_NOW
        )
        problem, is_new = ledger.register_or_update(
            signal_id="x", severity="medium", summary="x", now=_FIXED_NOW
        )
        self.assertFalse(is_new)
        self.assertEqual(problem.occurrence_count, 2)

    def test_terminal_problems_skip_bump(self) -> None:
        ledger = ProblemLedger()
        ledger.register_or_update(
            signal_id="x", severity="medium", summary="x", now=_FIXED_NOW
        )
        sig = ledger.all()[0].signature
        ledger.transition(sig, status=ProblemStatus.COMPLETED)
        problem, is_new = ledger.register_or_update(
            signal_id="x", severity="medium", summary="x", now=_FIXED_NOW
        )
        self.assertFalse(is_new)
        self.assertEqual(problem.occurrence_count, 1)  # not bumped

    def test_transition_records_metadata(self) -> None:
        ledger = ProblemLedger()
        ledger.register_or_update(
            signal_id="x", severity="medium", summary="x", now=_FIXED_NOW
        )
        sig = ledger.all()[0].signature
        updated = ledger.transition(
            sig,
            status=ProblemStatus.FIXING,
            owner_role="backend-engineer",
            suggested_next_action="runtime_code_change",
            approval_scope="delegated_ok",
            delegated_ok=True,
            worktree_branch="codex/self-improve/x",
            related_job_ids=["j1", "j2"],
        )
        assert updated is not None
        self.assertEqual(updated.status, ProblemStatus.FIXING)
        self.assertEqual(updated.owner_role, "backend-engineer")
        self.assertEqual(updated.related_job_ids, ("j1", "j2"))
        self.assertTrue(updated.delegated_ok)
        self.assertEqual(
            updated.worktree_branch, "codex/self-improve/x"
        )

    def test_transition_increment_retry_bumps_counter(self) -> None:
        ledger = ProblemLedger()
        ledger.register_or_update(
            signal_id="x", severity="medium", summary="x", now=_FIXED_NOW
        )
        sig = ledger.all()[0].signature
        ledger.transition(sig, status=ProblemStatus.FIXING, increment_retry=True)
        ledger.transition(sig, status=ProblemStatus.FIXING, increment_retry=True)
        problem = ledger.get(sig)
        assert problem is not None
        self.assertEqual(problem.retry_count, 2)

    def test_transition_unknown_signature_returns_none(self) -> None:
        ledger = ProblemLedger()
        self.assertIsNone(
            ledger.transition("nope", status=ProblemStatus.FIXING)
        )

    def test_summary_counters(self) -> None:
        ledger = ProblemLedger()
        for sid in ("a", "b", "c"):
            ledger.register_or_update(
                signal_id=sid, severity="medium", summary=sid, now=_FIXED_NOW
            )
        sigs = [p.signature for p in ledger.all()]
        ledger.transition(sigs[0], status=ProblemStatus.FIXING)
        ledger.transition(sigs[1], status=ProblemStatus.WAITING_OPERATOR)
        counts = ledger.summary_counters()
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["fixing"], 1)
        self.assertEqual(counts["waiting_operator"], 1)
        self.assertEqual(counts["detected"], 1)


class LedgerPersistenceTests(unittest.TestCase):
    def test_round_trip_through_disk(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            ledger_a = ProblemLedger(ledger_path=path)
            ledger_a.register_or_update(
                signal_id="x",
                severity="high",
                summary="bug",
                evidence={"topic_key": "k"},
                now=_FIXED_NOW,
            )
            sig = ledger_a.all()[0].signature
            ledger_a.transition(
                sig,
                status=ProblemStatus.FIXING,
                owner_role="backend-engineer",
            )
            # Re-open: the new instance should see the persisted row.
            ledger_b = ProblemLedger(ledger_path=path)
            problem = ledger_b.get(sig)
            self.assertIsNotNone(problem)
            assert problem is not None
            self.assertEqual(problem.status, ProblemStatus.FIXING)
            self.assertEqual(problem.owner_role, "backend-engineer")


class PayloadRoundTripTests(unittest.TestCase):
    def test_to_payload_and_from_payload(self) -> None:
        problem = ProblemObject(
            signature="sig.x",
            signal_id="x",
            severity="high",
            summary="x",
            first_seen_at="2026-05-16T12:00:00+00:00",
            last_seen_at="2026-05-16T12:00:00+00:00",
            occurrence_count=3,
            status=ProblemStatus.FIXING,
            evidence={"topic_key": "k"},
            owner_role="backend-engineer",
            suggested_next_action="runtime_code_change",
            approval_scope="delegated_ok",
            delegated_ok=True,
            retry_count=1,
            worktree_branch="codex/self-improve/x",
            related_session_ids=("s1",),
            related_job_ids=("j1",),
            related_pr_urls=("https://github.com/foo/bar/pull/1",),
        )
        payload = problem.to_payload()
        # JSON-serialisable
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        restored = ProblemObject.from_payload(decoded)
        self.assertEqual(restored, problem)


class DefaultLedgerPathTests(unittest.TestCase):
    def test_env_override_used_first(self) -> None:
        env = {"YULE_SELF_IMPROVEMENT_LEDGER_PATH": "/tmp/sip.json"}
        self.assertEqual(default_ledger_path(env=env), Path("/tmp/sip.json"))

    def test_falls_back_to_cache_sibling(self) -> None:
        env = {"YULE_CACHE_DB_PATH": "/var/lib/yule/cache.sqlite3"}
        self.assertEqual(
            default_ledger_path(env=env),
            Path("/var/lib/yule/self_improvement_problems.json"),
        )

    def test_default_when_no_env(self) -> None:
        self.assertEqual(
            default_ledger_path(env={}),
            Path(".cache/yule/self_improvement_problems.json"),
        )


if __name__ == "__main__":
    unittest.main()
