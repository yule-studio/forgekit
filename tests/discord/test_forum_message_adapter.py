"""Forum-thread on_message adapter — A-M7.5b integration tests.

Pin the wire between the M7.5 pure helpers and Discord's
``on_message``. The adapter must:

  * Skip non-forum messages without paying any SQLite cost.
  * Route Obsidian save requests through the
    ``forum_obsidian_handoff`` producer + reply with the friendly
    template.
  * Route role-change requests through ``parse_role_change_request``
    + persist ``active_research_roles`` + audit.
  * Friendly no-op when the thread has no matching session.
  * Bot self-messages and slash commands fall through unchanged
    (handled by the caller before this adapter runs).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.approval_worker import ApprovalWorker
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.discord.forum.message_adapter import (
    RESPONSE_ROLE_ADDED,
    RESPONSE_ROLE_ALL_TEAM,
    RESPONSE_ROLE_CHANGE_NO_SESSION,
    SKIPPED_NOT_FORUM_THREAD,
    SKIPPED_NO_INTENT,
    route_forum_message,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _forum_message(
    *,
    channel_id: int = 50001,
    parent_channel_id: int = 50000,
    channel_name: str = "k8s 운영 자료",
    content: str = "...",
    author_name: str = "masterway",
    author_id: int = 7,
    message_id: int = 60001,
    guild_id: int = 40000,
):
    channel = SimpleNamespace(
        id=channel_id,
        parent_id=parent_channel_id,
        parent=SimpleNamespace(id=parent_channel_id, name="운영-리서치"),
        name=channel_name,
        guild=SimpleNamespace(id=guild_id),
    )
    author = SimpleNamespace(
        id=author_id, name=author_name, global_name=author_name, bot=False
    )
    return SimpleNamespace(
        id=message_id,
        channel=channel,
        author=author,
        content=content,
        guild=SimpleNamespace(id=guild_id),
        jump_url=f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}",
    )


def _regular_channel_message(content: str):
    channel = SimpleNamespace(
        id=999, parent_id=None, parent=None, name="general"
    )
    author = SimpleNamespace(
        id=1, name="masterway", global_name="masterway", bot=False
    )
    return SimpleNamespace(
        id=42, channel=channel, author=author, content=content
    )


def _open_session(
    *,
    session_id: str = "sess-adapter-1",
    forum_thread_id: int = 50001,
    extra_overrides=None,
):
    extra = {"research_forum_thread_id": forum_thread_id}
    if extra_overrides:
        extra.update(extra_overrides)
    when = datetime.now(tz=timezone.utc)
    return SimpleNamespace(
        session_id=session_id,
        prompt="k8s 운영 자료 정리",
        extra=extra,
        thread_id=None,
        role_sequence=("tech-lead", "devops-engineer"),
        updated_at=when.isoformat(),
    )


class _AdapterFixture(unittest.TestCase):
    APPROVAL_CHANNEL_ID: int = 80001

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)
        self.posted_cards: List = []

        async def post_fn(request, rendered):
            self.posted_cards.append((request, rendered))
            return {
                "posted_message_id": 91000 + len(self.posted_cards),
                "channel_id": self.APPROVAL_CHANNEL_ID,
            }

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: self.APPROVAL_CHANNEL_ID,
        )

        self.sent_thread_replies: List[str] = []

        async def _send(channel, text, *args, **kwargs):
            self.sent_thread_replies.append(text)

        self.send_chunks_factory = lambda _module=None: _send

        self.session_updates: List = []

        def session_updater(updated, *, now=None):
            self.session_updates.append(updated)

        self.session_updater = session_updater


# ---------------------------------------------------------------------------
# Branch 0 — non-forum messages skip the adapter cheaply.
# ---------------------------------------------------------------------------


class NonForumMessagePassthroughTests(_AdapterFixture):
    def test_regular_channel_message_returns_handled_false(self) -> None:
        msg = _regular_channel_message("Obsidian에 정리해줘")
        result = _run(
            route_forum_message(
                message=msg,
                text=msg.content,
                discord_module=None,
                send_chunks_factory=self.send_chunks_factory,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [_open_session()],
                session_updater=self.session_updater,
            )
        )
        self.assertFalse(result.handled)
        self.assertEqual(result.skipped_reason, SKIPPED_NOT_FORUM_THREAD)
        # Adapter must not post anything.
        self.assertEqual(self.posted_cards, [])
        self.assertEqual(self.sent_thread_replies, [])

    def test_empty_text_returns_handled_false(self) -> None:
        msg = _forum_message(content="   ")
        result = _run(
            route_forum_message(
                message=msg,
                text="",
                discord_module=None,
                send_chunks_factory=self.send_chunks_factory,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [_open_session()],
                session_updater=self.session_updater,
            )
        )
        self.assertFalse(result.handled)
        self.assertEqual(result.skipped_reason, SKIPPED_NO_INTENT)


# ---------------------------------------------------------------------------
# Branch 1 — Obsidian save request → producer → reply
# ---------------------------------------------------------------------------


class ObsidianSaveRequestRouteTests(_AdapterFixture):
    def test_save_request_creates_approval_card_and_replies(self) -> None:
        session = _open_session()
        msg = _forum_message(content="Obsidian에 정리하고 싶어")
        result = _run(
            route_forum_message(
                message=msg,
                text=msg.content,
                discord_module=None,
                send_chunks_factory=self.send_chunks_factory,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
                session_updater=self.session_updater,
            )
        )
        self.assertTrue(result.handled)
        self.assertIsNotNone(result.approval_job_id)
        # Approval card actually posted via the stub.
        self.assertEqual(len(self.posted_cards), 1)
        # Friendly thread reply sent — operator sees confirmation.
        self.assertEqual(len(self.sent_thread_replies), 1)
        self.assertIn(
            result.approval_job_id, self.sent_thread_replies[0]
        )

    def test_unrelated_forum_message_routed_to_followup_branch(
        self,
    ) -> None:
        # P0-F: forum thread, no save request, no role change → now
        # falls into branch 3 (forum conversational follow-up). With
        # a thread-anchored session and the conversation helper
        # finding *some* response, branch 3 returns handled=True
        # — no longer dropped silently.
        session = _open_session()
        msg = _forum_message(content="이 자료 어떻게 봐?")
        result = _run(
            route_forum_message(
                message=msg,
                text=msg.content,
                discord_module=None,
                send_chunks_factory=self.send_chunks_factory,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
                session_updater=self.session_updater,
            )
        )
        # Branch 3 picked it up; approval branch did not.
        self.assertTrue(result.handled)
        self.assertEqual(self.posted_cards, [])


# ---------------------------------------------------------------------------
# Branch 2 — role-change request → active_research_roles updated
# ---------------------------------------------------------------------------


class RoleChangeRouteTests(_AdapterFixture):
    def test_qa_join_updates_active_roles_and_replies(self) -> None:
        session = _open_session(
            extra_overrides={
                "active_research_roles": [
                    "tech-lead",
                    "devops-engineer",
                ],
            }
        )
        msg = _forum_message(content="QA도 참여시켜줘")
        result = _run(
            route_forum_message(
                message=msg,
                text=msg.content,
                discord_module=None,
                send_chunks_factory=self.send_chunks_factory,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
                session_updater=self.session_updater,
            )
        )
        self.assertTrue(result.handled)
        # Session was updated.
        self.assertEqual(len(self.session_updates), 1)
        updated = self.session_updates[0]
        active = updated.extra["active_research_roles"]
        self.assertIn("qa-engineer", active)
        # Reply contains the added role label.
        self.assertEqual(len(self.sent_thread_replies), 1)
        self.assertIn("qa-engineer", self.sent_thread_replies[0])
        # Audit captured.
        audits = updated.extra.get("role_changes") or []
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["action"], "add")
        self.assertEqual(
            list(audits[0]["roles_added"]), ["qa-engineer"]
        )

    def test_all_team_request_replies_with_user_attribution(self) -> None:
        session = _open_session(
            extra_overrides={
                "active_research_roles": [
                    "tech-lead",
                    "backend-engineer",
                ],
            }
        )
        msg = _forum_message(content="전체 팀 관점으로 봐줘")
        result = _run(
            route_forum_message(
                message=msg,
                text=msg.content,
                discord_module=None,
                send_chunks_factory=self.send_chunks_factory,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
                session_updater=self.session_updater,
            )
        )
        self.assertTrue(result.handled)
        self.assertEqual(self.sent_thread_replies, [RESPONSE_ROLE_ALL_TEAM])
        # Active roles updated to include every engineering role.
        from yule_orchestrator.agents.lifecycle.role_selection import (
            ALL_ENGINEERING_ROLES,
        )

        updated = self.session_updates[0]
        active = updated.extra["active_research_roles"]
        for role in ALL_ENGINEERING_ROLES:
            with self.subTest(role=role):
                self.assertIn(role, active)

    def test_role_change_with_no_matching_session_returns_friendly_notice(
        self,
    ) -> None:
        msg = _forum_message(channel_id=99999, content="QA도 참여시켜줘")
        result = _run(
            route_forum_message(
                message=msg,
                text=msg.content,
                discord_module=None,
                send_chunks_factory=self.send_chunks_factory,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [_open_session(forum_thread_id=11)],
                session_updater=self.session_updater,
            )
        )
        self.assertTrue(result.handled)
        self.assertEqual(
            result.response_sent, RESPONSE_ROLE_CHANGE_NO_SESSION
        )
        self.assertEqual(self.session_updates, [])


# ---------------------------------------------------------------------------
# Routing summary at kickoff (work-thread)
# ---------------------------------------------------------------------------


class KickoffRoutingSummaryTests(unittest.TestCase):
    def test_kickoff_message_includes_routing_summary_when_active_set(
        self,
    ) -> None:
        from yule_orchestrator.discord.bot import (
            _format_engineering_kickoff_message,
        )

        when = datetime.now(tz=timezone.utc)
        session = SimpleNamespace(
            session_id="sess-kickoff-1",
            task_type="research",
            executor_role=None,
            executor_runner=None,
            extra={
                "active_research_roles": ["tech-lead", "devops-engineer"],
                "excluded_research_roles": [
                    "backend-engineer",
                    "qa-engineer",
                ],
                "role_selection_primary": ["devops-engineer"],
                "role_selection_reviewer": [],
                "role_selection_source": "tech_lead_rule",
            },
        )
        plan = SimpleNamespace(role_sequence=("tech-lead", "devops-engineer"))
        text = _format_engineering_kickoff_message(session, plan)
        # Summary line surfaces participating + standby roles.
        self.assertIn("참여 역할", text)
        self.assertIn("devops-engineer", text)
        self.assertIn("대기 역할", text)
        # User-add hint included.
        self.assertIn("참여시켜", text)

    def test_kickoff_message_skips_summary_when_no_role_selection(
        self,
    ) -> None:
        from yule_orchestrator.discord.bot import (
            _format_engineering_kickoff_message,
        )

        session = SimpleNamespace(
            session_id="sess-kickoff-2",
            task_type="research",
            executor_role=None,
            executor_runner=None,
            extra={},
        )
        plan = SimpleNamespace(role_sequence=("tech-lead",))
        text = _format_engineering_kickoff_message(session, plan)
        # No routing summary — legacy kickoff text shape preserved.
        self.assertNotIn("참여 역할", text)
        self.assertNotIn("대기 역할", text)


if __name__ == "__main__":
    unittest.main()
