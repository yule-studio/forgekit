"""Bootstrap hot-path enforcement — gate + receipt wiring (issue #185 follow-up A)."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness import load_grant_table
from yule_engineering.agents.runners.bootstrap import (
    build_role_runner_dispatch_from_env,
    grant_enforcement_enabled,
)
from yule_engineering.agents.runners.role_runner import (
    STATUS_BLOCKED,
    STATUS_FALLBACK,
    RoleRunnerInput,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _Session:
    session_id: str = "s1"
    extra: dict = field(default_factory=dict)


def _input(role="qa-engineer", caps=None):
    md = {"capabilities": caps} if caps is not None else {}
    return RoleRunnerInput(role=role, session_id="s1", prompt="p", metadata=md)


class EnforcementFlagTests(unittest.TestCase):
    def test_flag_default_off(self) -> None:
        self.assertFalse(grant_enforcement_enabled({}))
        self.assertTrue(grant_enforcement_enabled({"YULE_GRANT_ENFORCEMENT_ENABLED": "true"}))


class BootstrapWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load_grant_table()

    def test_ungranted_capability_blocked_in_dispatch(self) -> None:
        dispatch, _trace = build_role_runner_dispatch_from_env(
            env={}, grant_table=self.table
        )
        out = dispatch(_Session(), _input(caps=["/model"]))  # non-grantable → BLOCK
        self.assertEqual(out.status, STATUS_BLOCKED)

    def test_no_capabilities_falls_through_to_deterministic(self) -> None:
        dispatch, _trace = build_role_runner_dispatch_from_env(
            env={}, grant_table=self.table
        )
        out = dispatch(_Session(), _input())
        self.assertEqual(out.status, STATUS_FALLBACK)  # no provider configured

    def test_receipt_sink_receives_per_run_receipt(self) -> None:
        captured = []
        dispatch, _trace = build_role_runner_dispatch_from_env(
            env={},
            grant_table=self.table,
            receipt_sink=lambda session, receipt: captured.append(receipt),
            repo_root=_REPO_ROOT,
        )
        dispatch(_Session(), _input())
        self.assertTrue(captured, "receipt sink should fire per run")
        receipt = captured[-1]
        d = receipt.to_dict()
        self.assertIn("loaded_docs", d)
        self.assertEqual(d["selected_agent"], "engineering-agent")
        self.assertEqual(d["selected_role"], "qa-engineer")

    def test_session_receipt_bucket_persisted(self) -> None:
        from yule_engineering.agents.runners.bootstrap import _build_session_receipt_sink

        session = _Session()
        dispatch, _trace = build_role_runner_dispatch_from_env(
            env={},
            grant_table=self.table,
            receipt_sink=_build_session_receipt_sink(),
            repo_root=_REPO_ROOT,
        )
        dispatch(session, _input())
        self.assertIn("execution_receipts", session.extra)
        self.assertEqual(len(session.extra["execution_receipts"]), 1)

    def test_no_grant_table_is_backward_compatible(self) -> None:
        captured = []
        dispatch, _trace = build_role_runner_dispatch_from_env(
            env={}, receipt_sink=lambda s, r: captured.append(r), repo_root=_REPO_ROOT
        )
        out = dispatch(_Session(), _input(caps=["/model"]))
        # no grant_table ⇒ no gate, no receipt emission
        self.assertEqual(out.status, STATUS_FALLBACK)
        self.assertEqual(captured, [])


if __name__ == "__main__":
    unittest.main()
