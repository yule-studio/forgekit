"""P1-Q-2 — 8 사용자 acceptance.

1. replied_message_id on draft escalation card resolves exact session
2. global approval channel generic reply with multiple open cards does not
   choose unrelated most-recent session
3. ambiguous generic approval reply returns explicit "reply directly to card"
4. draft escalation creation supersedes old pr_merge card (same session)
5. same session has at most one replyable pr_merge card after escalation
6. reply router handles latest draft escalation card, not stale old card
7. legacy non-draft single-card flow regression 없음
8. audit / log records superseded card + matched posted_message_id reason
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from typing import Any, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_engineering.agents.job_queue.approval_reply import (
    find_approval_by_posted_message_id,
    find_open_approval_cards_by_kind,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.pr_approval import (
    APPROVAL_KIND_PR_MERGE,
    PRMergeProposal,
)
from yule_engineering.agents.job_queue.pr_merge_continuation_worker import (
    _supersede_old_pr_merge_cards,
    advance_pending_session,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


# ---------------------------------------------------------------------------
# Helpers — direct JobQueue.enqueue 로 approval_post row 만들기
# ---------------------------------------------------------------------------


def _enqueue_pr_merge_card(
    queue: JobQueue,
    *,
    session_id: str,
    posted_message_id: int,
    created_by: str = "auto-continuation",
    extra: Optional[Mapping[str, Any]] = None,
) -> str:
    """SAVED approval_post row 생성 + result_json.posted_message_id stamp."""

    payload = {
        "session_id": session_id,
        "approval_kind": APPROVAL_KIND_PR_MERGE,
        "title": "PR 머지 승인",
        "summary": "summary",
        "requested_action": "merge",
        "created_by": created_by,
        "source_channel_id": None,
        "source_thread_id": None,
        "source_message_id": None,
        "extra": dict(extra or {}),
    }
    job = queue.enqueue(
        job_type="approval_post",
        session_id=session_id,
        role="",
        payload=payload,
    )
    # 직접 sqlite 로 transition + result_json.posted_message_id stamp
    db = queue._db_path  # type: ignore[attr-defined]
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE job_queue SET state=?, result_json=? WHERE job_id=?",
            (
                JobState.SAVED.value,
                json.dumps({"posted_message_id": int(posted_message_id)}),
                job.job_id,
            ),
        )
        conn.commit()
    return job.job_id


def _make_queue() -> tuple:
    tmp = tempfile.TemporaryDirectory()
    db = os.path.join(tmp.name, "queue.sqlite3")
    queue = JobQueue(db_path=db)
    HeartbeatStore(db_path=db)
    return queue, tmp


# ---------------------------------------------------------------------------
# 1. replied_message_id 가 정확한 카드 (그리고 session) 로 resolve
# ---------------------------------------------------------------------------


class RepliedMessageIdResolutionTests(unittest.TestCase):
    def test_replied_message_id_matches_card_across_sessions(self) -> None:
        queue, tmp = _make_queue()
        self.addCleanup(tmp.cleanup)
        # 세션 A 의 카드 (사용자가 답할 대상)
        _enqueue_pr_merge_card(
            queue,
            session_id="fe5eedc65196",
            posted_message_id=1505829911217045525,
            created_by="autonomous_merge_draft_escalation",
            extra={"draft_escalation": True},
        )
        # 무관한 fixture 세션의 카드 — most-recent fallback 의 함정
        _enqueue_pr_merge_card(
            queue,
            session_id="txn-pending-1",
            posted_message_id=9999999,
        )
        # replied_message_id 로 정확한 카드 찾음
        match = find_approval_by_posted_message_id(
            queue=queue,
            posted_message_id=1505829911217045525,
            approval_kind=APPROVAL_KIND_PR_MERGE,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.session_id, "fe5eedc65196")
        payload = match.payload or {}
        self.assertTrue(payload.get("extra", {}).get("draft_escalation"))

    def test_replied_message_id_no_match_returns_none(self) -> None:
        queue, tmp = _make_queue()
        self.addCleanup(tmp.cleanup)
        _enqueue_pr_merge_card(
            queue, session_id="s1", posted_message_id=12345
        )
        self.assertIsNone(
            find_approval_by_posted_message_id(
                queue=queue,
                posted_message_id=99999,
                approval_kind=APPROVAL_KIND_PR_MERGE,
            )
        )


# ---------------------------------------------------------------------------
# 2, 3. global fallback 안전화 + ambiguous 안내
# ---------------------------------------------------------------------------


class AmbiguousReplyTests(unittest.TestCase):
    def test_multiple_open_cards_returns_two_plus(self) -> None:
        queue, tmp = _make_queue()
        self.addCleanup(tmp.cleanup)
        _enqueue_pr_merge_card(queue, session_id="s1", posted_message_id=1)
        _enqueue_pr_merge_card(queue, session_id="s2", posted_message_id=2)
        _enqueue_pr_merge_card(queue, session_id="s3", posted_message_id=3)
        cards = find_open_approval_cards_by_kind(
            queue=queue, approval_kind=APPROVAL_KIND_PR_MERGE
        )
        self.assertEqual(len(cards), 3)

    def test_single_open_card_returns_one(self) -> None:
        queue, tmp = _make_queue()
        self.addCleanup(tmp.cleanup)
        _enqueue_pr_merge_card(queue, session_id="s1", posted_message_id=1)
        cards = find_open_approval_cards_by_kind(
            queue=queue, approval_kind=APPROVAL_KIND_PR_MERGE
        )
        self.assertEqual(len(cards), 1)


# ---------------------------------------------------------------------------
# 4, 5. supersede old pr_merge card on draft escalation
# ---------------------------------------------------------------------------


class SupersedeOldCardTests(unittest.TestCase):
    def test_supersede_marks_old_card_terminal(self) -> None:
        queue, tmp = _make_queue()
        self.addCleanup(tmp.cleanup)
        old_id = _enqueue_pr_merge_card(
            queue,
            session_id="fe5eedc65196",
            posted_message_id=100,
            created_by="auto-continuation",
        )
        new_id = _enqueue_pr_merge_card(
            queue,
            session_id="fe5eedc65196",
            posted_message_id=200,
            created_by="autonomous_merge_draft_escalation",
            extra={"draft_escalation": True},
        )
        # 2 open
        self.assertEqual(
            len(
                find_open_approval_cards_by_kind(
                    queue=queue, approval_kind=APPROVAL_KIND_PR_MERGE
                )
            ),
            2,
        )
        # supersede — keep new_id
        superseded = _supersede_old_pr_merge_cards(
            queue=queue, session_id="fe5eedc65196", keep_job_id=new_id
        )
        self.assertEqual(superseded, [old_id])
        # 1 open
        remaining = find_open_approval_cards_by_kind(
            queue=queue, approval_kind=APPROVAL_KIND_PR_MERGE
        )
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].job_id, new_id)

    def test_supersede_only_targets_same_session(self) -> None:
        queue, tmp = _make_queue()
        self.addCleanup(tmp.cleanup)
        # 다른 session 의 옛 카드 — 영향 받지 말아야
        _enqueue_pr_merge_card(
            queue, session_id="unrelated-session", posted_message_id=10
        )
        old_for_target = _enqueue_pr_merge_card(
            queue, session_id="target", posted_message_id=11
        )
        new_for_target = _enqueue_pr_merge_card(
            queue,
            session_id="target",
            posted_message_id=12,
            extra={"draft_escalation": True},
        )
        superseded = _supersede_old_pr_merge_cards(
            queue=queue, session_id="target", keep_job_id=new_for_target
        )
        self.assertEqual(superseded, [old_for_target])
        # unrelated 카드는 그대로 남음
        cards = find_open_approval_cards_by_kind(
            queue=queue, approval_kind=APPROVAL_KIND_PR_MERGE
        )
        sessions = {c.session_id for c in cards}
        self.assertEqual(sessions, {"unrelated-session", "target"})

    def test_supersede_keeps_new_card(self) -> None:
        queue, tmp = _make_queue()
        self.addCleanup(tmp.cleanup)
        old_id = _enqueue_pr_merge_card(
            queue, session_id="s", posted_message_id=100
        )
        new_id = _enqueue_pr_merge_card(
            queue,
            session_id="s",
            posted_message_id=200,
            extra={"draft_escalation": True},
        )
        _supersede_old_pr_merge_cards(
            queue=queue, session_id="s", keep_job_id=new_id
        )
        # new_id 는 여전히 SAVED 상태, old_id 는 FAILED_TERMINAL
        with sqlite3.connect(str(queue._db_path)) as conn:
            row_old = conn.execute(
                "SELECT state, result_json FROM job_queue WHERE job_id=?",
                (old_id,),
            ).fetchone()
            row_new = conn.execute(
                "SELECT state FROM job_queue WHERE job_id=?", (new_id,)
            ).fetchone()
        self.assertEqual(row_old[0], JobState.FAILED_TERMINAL.value)
        result = json.loads(row_old[1] or "{}")
        self.assertTrue(result.get("superseded"))
        self.assertEqual(result.get("superseded_by"), "draft_escalation_card")
        self.assertEqual(row_new[0], JobState.SAVED.value)


# ---------------------------------------------------------------------------
# 6. reply router handles latest draft escalation (matched_via_reply)
# ---------------------------------------------------------------------------


class ReplyRouterCardFirstTests(unittest.TestCase):
    """라우터의 카드-우선 매칭 동작 — replied_message_id 가 카드의 session_id
    를 결정하므로 most-recent fallback 으로 무관한 fixture 카드가 선택되지
    않는다."""

    def setUp(self) -> None:
        self.queue, self._tmp = _make_queue()
        self.addCleanup(self._tmp.cleanup)

    def test_replied_message_id_overrides_session_resolver(self) -> None:
        # 무관한 fixture 카드 (most-recent fallback 의 함정)
        _enqueue_pr_merge_card(
            self.queue,
            session_id="txn-pending-1",
            posted_message_id=999,
        )
        # 실제 사용자가 답할 draft escalation 카드
        target_card_id = _enqueue_pr_merge_card(
            self.queue,
            session_id="fe5eedc65196",
            posted_message_id=200,
            created_by="autonomous_merge_draft_escalation",
            extra={"draft_escalation": True},
        )
        # replied_message_id 로 정확한 카드 찾음 — caller 가 그 카드의
        # session_id (fe5eedc65196) 를 사용해야 함
        match = find_approval_by_posted_message_id(
            queue=self.queue,
            posted_message_id=200,
            approval_kind=APPROVAL_KIND_PR_MERGE,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.session_id, "fe5eedc65196")
        self.assertEqual(match.job_id, target_card_id)


# ---------------------------------------------------------------------------
# 7. Legacy non-draft single-card flow regression 없음
# ---------------------------------------------------------------------------


class LegacyNonDraftSingleCardTests(unittest.TestCase):
    def test_single_non_draft_card_still_resolves(self) -> None:
        """draft_escalation flag 없는 일반 pr_merge 카드 1장만 있을 때,
        그 카드가 정상 findable."""

        queue, tmp = _make_queue()
        self.addCleanup(tmp.cleanup)
        card_id = _enqueue_pr_merge_card(
            queue,
            session_id="regular-session",
            posted_message_id=500,
            created_by="auto-continuation",
        )
        # posted_message_id 매칭
        match = find_approval_by_posted_message_id(
            queue=queue,
            posted_message_id=500,
            approval_kind=APPROVAL_KIND_PR_MERGE,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.job_id, card_id)
        # extra 에 draft_escalation 없음
        self.assertFalse(
            (match.payload or {}).get("extra", {}).get("draft_escalation", False)
        )


# ---------------------------------------------------------------------------
# 8. End-to-end: advance_pending_session 가 escalation 시 옛 카드 supersede
#    + audit 에 superseded_pr_merge_cards / 시점 기록
# ---------------------------------------------------------------------------


class AdvancePendingSessionIntegrationTests(unittest.TestCase):
    def test_escalation_supersedes_old_card_and_audits(self) -> None:
        queue, tmp = _make_queue()
        self.addCleanup(tmp.cleanup)
        from yule_engineering.agents.job_queue.pr_merge_continuation import (
            EXTRA_PR_MERGE_AUDIT,
            EXTRA_PR_MERGE_PR_NUMBER,
            EXTRA_PR_MERGE_REPO,
            EXTRA_PR_MERGE_STAGE,
            STAGE_AWAITING_DRAFT_APPROVAL,
            STAGE_PR_MERGE_PENDING,
        )
        from yule_engineering.agents.lifecycle.session_mode import (
            EXTRA_WORK_MODE,
            WORK_MODE_AUTONOMOUS,
        )

        # 옛 non-draft 카드 1장
        old_card_id = _enqueue_pr_merge_card(
            queue,
            session_id="canonical-1",
            posted_message_id=1000,
            created_by="auto-continuation",
        )

        # advance_pending_session 호출용 session.extra (draft 상태)
        extra = {
            EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
            EXTRA_PR_MERGE_PR_NUMBER: 4,
            EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
            "pr_merge_pr_url": "https://github.com/yule-studio/naver-search-clone/pull/4",
            "pr_merge_head_sha": "sha4",
            "pr_merge_base_branch": "main",
        }

        # gate 가 draft 거부
        def fake_executor(dispatch):
            return {"gate_failed_step": "draft", "gate_reason": "draft"}

        # approval_enqueuer 는 새 카드 enqueue 흉내 — 실제 row 한 건 추가
        async def fake_enqueuer(*, session, proposal, **_):
            new_job_id = _enqueue_pr_merge_card(
                queue,
                session_id="canonical-1",
                posted_message_id=2000,
                created_by="autonomous_merge_draft_escalation",
                extra={"draft_escalation": True},
            )

            class _Out:
                approval_job_id = new_job_id

            return _Out()

        persisted: List[Mapping[str, Any]] = []

        def persist(new_extra):
            persisted.append(dict(new_extra))

        loop = asyncio.new_event_loop()
        try:
            outcome = loop.run_until_complete(
                advance_pending_session(
                    session_id="canonical-1",
                    session_extra=extra,
                    persist_extra=persist,
                    approval_enqueuer=fake_enqueuer,
                    merge_executor=fake_executor,
                    queue=queue,
                )
            )
        finally:
            loop.close()

        # outcome 은 draft escalation
        self.assertEqual(outcome.new_stage, STAGE_AWAITING_DRAFT_APPROVAL)
        # 옛 카드는 superseded
        with sqlite3.connect(str(queue._db_path)) as conn:
            row = conn.execute(
                "SELECT state, result_json FROM job_queue WHERE job_id=?",
                (old_card_id,),
            ).fetchone()
        self.assertEqual(row[0], JobState.FAILED_TERMINAL.value)
        result_old = json.loads(row[1] or "{}")
        self.assertTrue(result_old.get("superseded"))
        # audit 에 superseded_pr_merge_cards 기록
        final_extra = persisted[-1]
        audit = final_extra[EXTRA_PR_MERGE_AUDIT]
        esc_event = next(
            e for e in audit if e.get("event") == "approval_card_enqueued_draft_escalation"
        )
        self.assertEqual(esc_event["superseded_pr_merge_cards"], [old_card_id])


if __name__ == "__main__":
    unittest.main()
