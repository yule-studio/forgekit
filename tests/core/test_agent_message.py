from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.message import (
    AgentMessage,
    ContextRef,
    Priority,
    REPLY_ACTIONS,
    REQUEST_ACTIONS,
    RequestedAction,
    close_thread,
    new_request,
    reply_to,
    role_address,
    with_thread_id,
)


def _make_request(**overrides) -> AgentMessage:
    base = dict(
        from_role="engineering-agent/tech-lead",
        to_role="engineering-agent/backend-engineer",
        task_type="backend-feature",
        topic="users API에 email 인증 필드 추가",
        content="users 테이블에 email_verified column을 추가",
        requested_action=RequestedAction.IMPLEMENT,
        priority=Priority.P1,
        run_id="ws-abc",
    )
    base.update(overrides)
    return new_request(**base)


class RoleAddressTestCase(unittest.TestCase):
    def test_format(self) -> None:
        self.assertEqual(role_address("engineering-agent", "tech-lead"), "engineering-agent/tech-lead")
        self.assertEqual(role_address("design-agent", "product-designer"), "design-agent/product-designer")


class ActionPartitionTestCase(unittest.TestCase):
    def test_request_and_reply_actions_disjoint(self) -> None:
        self.assertEqual(REQUEST_ACTIONS & REPLY_ACTIONS, frozenset())

    def test_every_action_is_classified(self) -> None:
        all_actions = set(RequestedAction)
        self.assertTrue(all_actions.issuperset(REQUEST_ACTIONS | REPLY_ACTIONS))


class NewRequestTestCase(unittest.TestCase):
    def test_basic_fields_round_trip(self) -> None:
        req = _make_request()
        self.assertEqual(req.from_role, "engineering-agent/tech-lead")
        self.assertEqual(req.to_role, "engineering-agent/backend-engineer")
        self.assertEqual(req.requested_action, RequestedAction.IMPLEMENT)
        self.assertEqual(req.priority, Priority.P1)
        self.assertTrue(req.is_request())
        self.assertFalse(req.is_reply())
        self.assertIsNone(req.parent_message_id)
        self.assertEqual(len(req.message_id), 12)

    def test_default_priority_is_p2(self) -> None:
        req = _make_request(priority=None)
        # passing priority=None falls back to default Priority.P2 in the dataclass
        # via new_request default. Recreate without priority:
        req = new_request(
            from_role="x", to_role="y", task_type="t",
            topic="t", content="c", requested_action=RequestedAction.ANALYZE,
        )
        self.assertEqual(req.priority, Priority.P2)

    def test_reply_action_rejected(self) -> None:
        with self.assertRaises(ValueError):
            new_request(
                from_role="a", to_role="b", task_type="t",
                topic="t", content="c",
                requested_action=RequestedAction.COMPLETED,
            )

    def test_reference_pack_fields_round_trip(self) -> None:
        req = _make_request(
            reference_links=["https://example.com/a"],
            reference_notes=[{"title": "A", "takeaway": "차용"}],
            visual_direction="모노톤 + accent 1색",
            copy_tone="짧고 단정",
            competitive_examples=[{"name": "Stripe", "url": "https://stripe.com"}],
            context_refs=[ContextRef(kind="issue", value="#142")],
        )
        self.assertEqual(req.reference_links, ("https://example.com/a",))
        self.assertEqual(req.reference_notes[0]["title"], "A")
        self.assertEqual(req.visual_direction, "모노톤 + accent 1색")
        self.assertEqual(req.copy_tone, "짧고 단정")
        self.assertEqual(req.competitive_examples[0]["name"], "Stripe")
        self.assertEqual(req.context_refs[0].kind, "issue")


class ReplyToTestCase(unittest.TestCase):
    def test_swaps_routing_and_chains_parent(self) -> None:
        req = _make_request()
        rep = reply_to(req, content="구현 완료", requested_action=RequestedAction.COMPLETED)
        self.assertEqual(rep.from_role, req.to_role)
        self.assertEqual(rep.to_role, req.from_role)
        self.assertEqual(rep.parent_message_id, req.message_id)
        self.assertEqual(rep.task_type, req.task_type)
        self.assertEqual(rep.topic, req.topic)
        self.assertEqual(rep.thread_id, req.thread_id)
        self.assertEqual(rep.run_id, req.run_id)
        self.assertTrue(rep.is_reply())
        self.assertTrue(rep.is_terminal_reply())

    def test_inherits_priority_when_not_overridden(self) -> None:
        req = _make_request(priority=Priority.P0)
        rep = reply_to(req, content="x", requested_action=RequestedAction.IN_PROGRESS)
        self.assertEqual(rep.priority, Priority.P0)

    def test_overrides_priority_when_provided(self) -> None:
        req = _make_request(priority=Priority.P0)
        rep = reply_to(
            req,
            content="x",
            requested_action=RequestedAction.IN_PROGRESS,
            priority=Priority.P2,
        )
        self.assertEqual(rep.priority, Priority.P2)

    def test_request_action_rejected(self) -> None:
        req = _make_request()
        with self.assertRaises(ValueError):
            reply_to(req, content="x", requested_action=RequestedAction.IMPLEMENT)

    def test_needs_clarification_is_non_terminal(self) -> None:
        req = _make_request()
        rep = reply_to(
            req,
            content="요청에 user_role 정보가 빠져 있습니다",
            requested_action=RequestedAction.NEEDS_CLARIFICATION,
        )
        self.assertTrue(rep.is_reply())
        self.assertFalse(rep.is_terminal_reply())


class CloseThreadTestCase(unittest.TestCase):
    def test_close_after_completed(self) -> None:
        req = _make_request()
        rep = reply_to(req, content="구현 완료", requested_action=RequestedAction.COMPLETED)
        closure = close_thread(
            rep,
            summary="email 인증 추가, PR #143 작성",
            references_used=[{"title": "Auth0", "rationale": "이중 토큰 패턴"}],
        )
        self.assertEqual(closure.from_role, rep.to_role)
        self.assertEqual(closure.to_role, "gateway")
        self.assertEqual(closure.parent_message_id, rep.message_id)
        self.assertEqual(closure.requested_action, RequestedAction.ACKNOWLEDGE)
        self.assertEqual(closure.extra["round_trip_outcome"], "completed")
        self.assertEqual(closure.extra["references_used"][0]["title"], "Auth0")

    def test_close_rejected_outcome(self) -> None:
        req = _make_request()
        rep = reply_to(req, content="범위 밖", requested_action=RequestedAction.REJECTED)
        closure = close_thread(rep, summary="범위 외 요청 — 거절")
        self.assertEqual(closure.extra["round_trip_outcome"], "rejected")

    def test_non_terminal_reply_rejected(self) -> None:
        req = _make_request()
        rep = reply_to(req, content="x", requested_action=RequestedAction.IN_PROGRESS)
        with self.assertRaises(ValueError):
            close_thread(rep, summary="early")


class WithThreadIdTestCase(unittest.TestCase):
    def test_assigns_thread_id_without_mutating_original(self) -> None:
        req = _make_request()
        self.assertIsNone(req.thread_id)
        bound = with_thread_id(req, "discord-thread-12345")
        self.assertEqual(bound.thread_id, "discord-thread-12345")
        self.assertIsNone(req.thread_id)
        self.assertEqual(bound.message_id, req.message_id)


class GeneralizationTestCase(unittest.TestCase):
    def test_works_for_design_agent_address(self) -> None:
        req = new_request(
            from_role=role_address("design-agent", "product-designer"),
            to_role=role_address("engineering-agent", "frontend-engineer"),
            task_type="visual-polish",
            topic="히어로 컬러 팔레트 합의",
            content="디자인 토큰 v2를 적용해줘",
            requested_action=RequestedAction.HANDOFF,
        )
        self.assertEqual(req.from_role, "design-agent/product-designer")
        self.assertEqual(req.to_role, "engineering-agent/frontend-engineer")
        self.assertEqual(req.requested_action, RequestedAction.HANDOFF)


if __name__ == "__main__":
    unittest.main()
