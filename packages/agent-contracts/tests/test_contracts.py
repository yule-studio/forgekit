"""Smoke tests for the ``yule_agent_contracts`` package.

These exercise the 6 contract models the package exists to provide, plus the
round-trip message helpers that were relocated here from
``yule_engineering.agents.messaging.message``.
"""

from __future__ import annotations

import yule_agent_contracts as contracts
from yule_agent_contracts import (
    AgentCommand,
    AgentEvent,
    AgentMessage,
    AgentRole,
    AgentStatus,
    ContextRef,
    Priority,
    RequestedAction,
    TaskRef,
    close_thread,
    new_request,
    reply_to,
)


def test_public_surface_exports_six_core_models() -> None:
    for name in (
        "AgentCommand",
        "AgentEvent",
        "AgentStatus",
        "AgentMessage",
        "AgentRole",
        "TaskRef",
    ):
        assert hasattr(contracts, name), name


def test_agent_role_round_trips_address() -> None:
    role = AgentRole("engineering-agent", "tech-lead")
    assert role.address == "engineering-agent/tech-lead"
    assert AgentRole.parse(role.address) == role
    # bare single-token addresses (e.g. "gateway") do not raise
    assert AgentRole.parse("gateway") == AgentRole("gateway", "")


def test_task_ref_slug_prefers_repo_number() -> None:
    assert TaskRef(repo="yule-studio/yule-studio-agent", number=185).slug == (
        "yule-studio/yule-studio-agent#185"
    )
    assert TaskRef(number=42).slug == "#42"
    assert TaskRef(task_id="sess-abc").slug == "sess-abc"


def test_agent_command_carries_target_task_payload() -> None:
    cmd = AgentCommand(
        command="role_take",
        target=AgentRole("engineering-agent", "backend-engineer"),
        task=TaskRef(task_id="sess-1"),
        payload={"topic": "auth"},
    )
    assert cmd.command == "role_take"
    assert cmd.target.role == "backend-engineer"
    assert cmd.payload["topic"] == "auth"
    assert cmd.command_id  # auto-generated


def test_agent_event_defaults() -> None:
    evt = AgentEvent(event=contracts.EVENT_COMPLETED, status="done")
    assert evt.event == "completed"
    assert evt.status == "done"
    assert evt.reason == ""
    assert evt.event_id


def test_agent_status_buckets() -> None:
    assert AgentStatus.BLOCKED in contracts.STALLED_STATUSES
    assert AgentStatus.DONE in contracts.TERMINAL_STATUSES
    assert AgentStatus.RUNNING not in contracts.TERMINAL_STATUSES


def test_message_round_trip_request_reply_close() -> None:
    req = new_request(
        from_role="engineering-agent/tech-lead",
        to_role="engineering-agent/backend-engineer",
        task_type="coding",
        topic="add login",
        content="please implement",
        requested_action=RequestedAction.IMPLEMENT,
        context_refs=[ContextRef(kind="issue", value="185")],
    )
    assert isinstance(req, AgentMessage)
    assert req.is_request()
    assert req.priority is Priority.P2

    rep = reply_to(req, content="done", requested_action=RequestedAction.COMPLETED)
    assert rep.from_role == req.to_role
    assert rep.to_role == req.from_role
    assert rep.parent_message_id == req.message_id
    assert rep.is_terminal_reply()

    closure = close_thread(rep, summary="shipped")
    assert closure.to_role == "gateway"
    assert closure.extra["round_trip_outcome"] == "completed"
