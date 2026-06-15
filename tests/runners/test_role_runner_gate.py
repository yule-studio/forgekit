"""Pre-dispatch grant gate wiring on the role-runner hot path (issue #185 follow-up A)."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.runners.role_runner import (
    PROVIDER_DETERMINISTIC,
    RoleRunner,
    RoleRunnerInput,
    RoleRunnerOutput,
    STATUS_BLOCKED,
    STATUS_FALLBACK,
    STATUS_OK,
    build_role_runner_dispatcher,
)


@dataclass
class _Session:
    session_id: str = "s1"
    extra: dict = field(default_factory=dict)  # no active_research_roles → fallback active


def _input(role: str = "qa-engineer") -> RoleRunnerInput:
    return RoleRunnerInput(role=role, session_id="s1", prompt="p")


class _CountingRunner(RoleRunner):
    provider = "claude"

    def __init__(self) -> None:
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def generate(self, input_: RoleRunnerInput) -> RoleRunnerOutput:
        self.calls += 1
        return RoleRunnerOutput(provider=self.provider, status=STATUS_OK, text="take")


class GateTests(unittest.TestCase):
    def test_gate_blocks_before_any_provider(self) -> None:
        runner = _CountingRunner()
        blocked = RoleRunnerOutput(
            provider="grant-gate", status=STATUS_BLOCKED, text="", detail="blocked: /model"
        )
        dispatch = build_role_runner_dispatcher(
            candidates=[runner],
            pre_dispatch_gate=lambda session, inp: blocked,
        )
        out = dispatch(_Session(), _input())
        self.assertEqual(out.status, STATUS_BLOCKED)
        self.assertEqual(runner.calls, 0)  # provider never contacted

    def test_gate_none_proceeds(self) -> None:
        runner = _CountingRunner()
        dispatch = build_role_runner_dispatcher(
            candidates=[runner],
            pre_dispatch_gate=lambda session, inp: None,
        )
        out = dispatch(_Session(), _input())
        self.assertEqual(out.status, STATUS_OK)
        self.assertEqual(runner.calls, 1)

    def test_gate_raise_degrades_to_no_block(self) -> None:
        runner = _CountingRunner()

        def _boom(session, inp):
            raise RuntimeError("gate bug")

        dispatch = build_role_runner_dispatcher(
            candidates=[runner], pre_dispatch_gate=_boom
        )
        out = dispatch(_Session(), _input())
        # a buggy gate must not wedge dispatch — take proceeds
        self.assertEqual(out.status, STATUS_OK)
        self.assertEqual(runner.calls, 1)

    def test_blocked_take_is_audited(self) -> None:
        records = []
        blocked = RoleRunnerOutput(provider="grant-gate", status=STATUS_BLOCKED, text="")
        dispatch = build_role_runner_dispatcher(
            candidates=[_CountingRunner()],
            pre_dispatch_gate=lambda s, i: blocked,
            audit_writer=records.append,
        )
        dispatch(_Session(), _input())
        self.assertTrue(records)
        self.assertEqual(records[-1]["status"], STATUS_BLOCKED)

    def test_no_gate_is_backward_compatible(self) -> None:
        runner = _CountingRunner()
        dispatch = build_role_runner_dispatcher(candidates=[runner])
        out = dispatch(_Session(), _input())
        self.assertEqual(out.status, STATUS_OK)


if __name__ == "__main__":
    unittest.main()
