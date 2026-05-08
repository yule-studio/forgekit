"""Senior-agent MVP integration — F-M13 e2e regression.

The user-visible contract this commit closes:

  사용자가 리서치 오더를 내리면 →
    topic ledger 가 만들어지거나 재사용되고 →
    active role 별 role-runner 가 호출되어 audit 가 남고 →
    research-log 가 vault 에 자동 저장된다 (L1, 승인 없음).

  같은 topic 으로 다시 부탁하면 같은 ledger 를 사용한다.
  forum thread 에서 "Obsidian 에 정리하고 싶어" 라고 하면 그건 별도
  L3 승인 카드 흐름이고, 같은 thread 메시지를 두 번 받아도 카드는
  하나만 만들어진다.
  실패 신호가 임계치를 넘으면 self-improvement proposal 이 자동으로
  vault 에 적힌다 (L2).

테스트 커버리지:
  * happy-path research order 가 audit + research-log 를 남긴다.
  * 같은 (prompt, thread) 는 같은 topic_key 로 ledger 를 재사용.
  * role-runner fallback 이 ``used_runner_fallback=True`` + audit
    outcome 에 ``fallback`` 토큰을 남긴다.
  * 사용자가 thread 에서 저장 요청하면 L3 approval card 가 생기고,
    같은 메시지 재전송은 dedup 된다.
  * knowledge note 가 hydration 부족이면 writer 가 거절 (M10b 가드).
  * failed_retryable 누적 → self-improvement proposal enqueue.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.obsidian_writer_worker import (
    JOB_TYPE_OBSIDIAN_WRITE,
    NOTE_KIND_KNOWLEDGE,
    NOTE_KIND_RESEARCH_LOG,
    NOTE_KIND_SELF_IMPROVEMENT_PROPOSAL,
    ObsidianRenderError,
    ObsidianWriteRequest,
    ObsidianWriterWorker,
    SKIPPED_APPROVAL_REQUIRED,
    default_render_fn,
)
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import Job, JobQueue
from yule_orchestrator.agents.lifecycle.agent_ops_log import (
    SESSION_EXTRA_KEY as AGENT_OPS_KEY,
    read_agent_ops_audit,
)
from yule_orchestrator.agents.lifecycle.research_topic import (
    STATUS_RESEARCHING,
    read_topic_ledger,
)
from yule_orchestrator.agents.lifecycle.self_improvement import (
    SIGNAL_FAILED_RETRYABLE_PILEUP,
)
from yule_orchestrator.agents.lifecycle.senior_agent import (
    SeniorAgentRunOutcome,
    emit_self_improvement_proposal,
    handle_research_order,
    replay_audit_entries,
)
from yule_orchestrator.agents.lifecycle.thread_snapshot import (
    ThreadMessage,
    ThreadSnapshot,
)
from yule_orchestrator.agents.runners.role_runner import (
    DEFAULT_PROVIDER_PRIORITY,
    DeterministicRoleRunner,
    PROVIDER_CLAUDE,
    PROVIDER_DETERMINISTIC,
    RoleRunner,
    RoleRunnerInput,
    RoleRunnerOutput,
    STATUS_ERROR,
    STATUS_OK,
    build_role_runner_dispatcher,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Shared session fixture — ``SimpleNamespace`` so ``session.extra``
# mutations can be observed without a real workflow_state row.
# ---------------------------------------------------------------------------


def _make_session(
    *,
    session_id: str = "sess-m13-1",
    prompt: str = "k8s 운영 자료 정리해줘",
    research_thread_id: int = 50001,
    active_roles: Tuple[str, ...] = ("tech-lead", "devops-engineer"),
    extra_overrides: Optional[Dict[str, Any]] = None,
) -> SimpleNamespace:
    extra: Dict[str, Any] = {
        "research_forum_thread_id": research_thread_id,
        "active_research_roles": list(active_roles),
    }
    if extra_overrides:
        extra.update(extra_overrides)
    return SimpleNamespace(
        session_id=session_id,
        prompt=prompt,
        thread_id=research_thread_id,
        extra=extra,
        role_sequence=active_roles,
    )


def _stub_snapshot() -> ThreadSnapshot:
    return ThreadSnapshot(
        messages=(
            ThreadMessage(
                author="masterway",
                content="k8s rolling update 정책 검토 필요",
                role=None,
            ),
        ),
        extracted_links=("https://kubernetes.io/docs/concepts/workloads/",),
        role_summaries={"tech-lead": "노드 풀 분리 + canary"},
        captured_at="2026-05-08T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Stub role runners for dispatcher verification
# ---------------------------------------------------------------------------


class _AlwaysOkRoleRunner(RoleRunner):
    """Mimics a configured Claude/Codex/Ollama backend that always
    returns text. Used to exercise the OK path without a real provider.
    """

    def __init__(self, provider: str = PROVIDER_CLAUDE) -> None:
        self.provider = provider
        self.calls: List[RoleRunnerInput] = []

    def is_available(self) -> bool:
        return True

    def generate(self, input_: RoleRunnerInput) -> RoleRunnerOutput:
        self.calls.append(input_)
        return RoleRunnerOutput(
            provider=self.provider,
            status=STATUS_OK,
            text=f"[{input_.role}] take from {self.provider}",
        )


class _AlwaysFailRoleRunner(RoleRunner):
    """Forces the dispatcher to walk to the deterministic terminal so
    the M11 fallback contract gets exercised.
    """

    provider = PROVIDER_CLAUDE

    def is_available(self) -> bool:
        return True

    def generate(self, input_: RoleRunnerInput) -> RoleRunnerOutput:
        return RoleRunnerOutput(
            provider=self.provider,
            status=STATUS_ERROR,
            text="",
            detail="forced fallback for test",
        )


# ---------------------------------------------------------------------------
# Worker / queue fixture (shared across tests that need an
# ObsidianWriterWorker the coordinator can enqueue against).
# ---------------------------------------------------------------------------


class _WorkerFixture(unittest.TestCase):
    """Spin up an in-memory queue + writer worker per test so each
    case has a clean slate. Render / write are stubbed — the M13
    coordinator only needs ``enqueue`` to succeed; vault writes are
    exercised separately by the M10b regression suite.
    """

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self._vault = Path(self._tmp.name) / "vault"
        self._vault.mkdir()
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)

        self.rendered: List[ObsidianWriteRequest] = []
        self.written: List[Tuple[Any, Path, ObsidianWriteRequest]] = []

        def render_fn(request: ObsidianWriteRequest):
            self.rendered.append(request)
            return SimpleNamespace(
                title=request.title,
                kind=request.note_kind,
                content=f"{request.title}\n\n[stub body]",
            )

        def write_fn(note: Any, vault: Path, request: ObsidianWriteRequest):
            self.written.append((note, vault, request))
            return SimpleNamespace(
                target_path=vault / f"{request.title}.md",
                written=True,
                dry_run=False,
                suffix_applied=False,
            )

        self.obsidian_worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=render_fn,
            write_fn=write_fn,
            vault_root_resolver=lambda _r: self._vault,
        )

    def _write_rows(self, session_id: str, *, kind: Optional[str] = None) -> List[Job]:
        rows = [
            j
            for j in self.queue.list_for_session(session_id)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        if kind is not None:
            rows = [
                r for r in rows if (r.payload or {}).get("note_kind") == kind
            ]
        return rows


# ---------------------------------------------------------------------------
# Happy path — research order produces ledger + audit + research-log
# ---------------------------------------------------------------------------


class ResearchOrderHappyPathTests(_WorkerFixture):
    def test_research_order_records_log_without_approval(self) -> None:
        # Definition of Done #1: 사용자가 리서치 오더를 내리면 승인 없이
        # research-log 가 남는다.
        session = _make_session()
        snapshot = _stub_snapshot()
        outcome = handle_research_order(
            session=session,
            research_thread_id=session.extra["research_forum_thread_id"],
            snapshot=snapshot,
            obsidian_writer_worker=self.obsidian_worker,
            source_thread_url="https://discord.com/channels/40000/50001/60001",
            source_thread_title="k8s 운영 자료",
            requested_by="masterway",
        )
        self.assertIsNotNone(outcome.research_log_job_id)
        self.assertTrue(outcome.research_log_created)

        # research-log row exists in queue, knowledge row does NOT —
        # the L3 approval gate is left untouched here.
        log_rows = self._write_rows(
            session.session_id, kind=NOTE_KIND_RESEARCH_LOG
        )
        knowledge_rows = self._write_rows(
            session.session_id, kind=NOTE_KIND_KNOWLEDGE
        )
        self.assertEqual(len(log_rows), 1)
        self.assertEqual(knowledge_rows, [])

        # research-log payload carries snapshot hydration so the
        # writer can render without a live session row.
        meta = (log_rows[0].payload or {}).get("metadata") or {}
        self.assertIn("thread_snapshot", meta)
        snapshot_payload = meta.get("thread_snapshot") or {}
        self.assertIn(
            "https://kubernetes.io/docs/concepts/workloads/",
            list(snapshot_payload.get("extracted_links") or ()),
        )
        self.assertEqual(meta.get("topic_key"), outcome.ledger_record.topic_key)

        # Audit trail: intake (L1) + per-role take + research-log save.
        actions = {e.action for e in outcome.audit_entries}
        self.assertIn("user_ordered_research", actions)
        self.assertIn("research_log_save", actions)
        # Final entry mirrors what's persisted on session.extra.
        persisted = read_agent_ops_audit(session)
        self.assertGreaterEqual(len(persisted), len(outcome.audit_entries))

    def test_runner_outputs_carry_provider_and_text(self) -> None:
        session = _make_session()
        runner = _AlwaysOkRoleRunner(provider=PROVIDER_CLAUDE)
        dispatcher = build_role_runner_dispatcher(
            candidates=(runner,),
        )
        outcome = handle_research_order(
            session=session,
            research_thread_id=session.extra["research_forum_thread_id"],
            snapshot=_stub_snapshot(),
            role_runner_dispatch=dispatcher,
            obsidian_writer_worker=self.obsidian_worker,
        )
        self.assertEqual(
            tuple(o.role for o in outcome.role_outputs),
            ("tech-lead", "devops-engineer"),
        )
        self.assertFalse(outcome.used_runner_fallback)
        for ro in outcome.role_outputs:
            self.assertEqual(ro.provider, PROVIDER_CLAUDE)
            self.assertEqual(ro.status, STATUS_OK)
            self.assertIn(ro.role, ro.text)


# ---------------------------------------------------------------------------
# Topic ledger continuity — same prompt + same thread → same key
# ---------------------------------------------------------------------------


class TopicLedgerContinuityTests(_WorkerFixture):
    def test_same_topic_reuses_ledger(self) -> None:
        # Definition of Done #2: 같은 주제는 topic ledger 로 이어진다.
        session = _make_session()
        first = handle_research_order(
            session=session,
            research_thread_id=session.extra["research_forum_thread_id"],
            snapshot=_stub_snapshot(),
            obsidian_writer_worker=self.obsidian_worker,
        )
        second = handle_research_order(
            session=session,
            research_thread_id=session.extra["research_forum_thread_id"],
            snapshot=_stub_snapshot(),
            obsidian_writer_worker=self.obsidian_worker,
        )
        self.assertEqual(
            first.ledger_record.topic_key, second.ledger_record.topic_key
        )
        # The ledger record is round-tripped through session.extra so a
        # third reader sees the same key.
        replayed = read_topic_ledger(session)
        self.assertIsNotNone(replayed)
        self.assertEqual(replayed.topic_key, first.ledger_record.topic_key)

        # research-log enqueue is idempotent — second call hits the
        # writer's find_active dedup, no new row.
        rows = self._write_rows(
            session.session_id, kind=NOTE_KIND_RESEARCH_LOG
        )
        self.assertEqual(len(rows), 1)
        self.assertFalse(second.research_log_created)


# ---------------------------------------------------------------------------
# Role-runner audit / fallback
# ---------------------------------------------------------------------------


class RunnerAuditTests(_WorkerFixture):
    def test_runner_fallback_recorded_on_audit(self) -> None:
        # Definition of Done #5: 역할별 runner 또는 명시적 fallback 이
        # 동작한다 — fallback 일 땐 audit 에 fallback 이 새겨진다.
        session = _make_session()
        # _AlwaysFailRoleRunner forces the dispatcher to fall through
        # to the deterministic terminal.
        dispatcher = build_role_runner_dispatcher(
            candidates=(_AlwaysFailRoleRunner(), DeterministicRoleRunner()),
        )
        outcome = handle_research_order(
            session=session,
            research_thread_id=session.extra["research_forum_thread_id"],
            snapshot=_stub_snapshot(),
            role_runner_dispatch=dispatcher,
            obsidian_writer_worker=self.obsidian_worker,
        )
        self.assertTrue(outcome.used_runner_fallback)
        for ro in outcome.role_outputs:
            self.assertEqual(ro.provider, PROVIDER_DETERMINISTIC)
            self.assertTrue(ro.used_fallback)
        # Each role's audit row carries the ``fallback`` token in its
        # outcome string so an operator scan can grep for fallback
        # incidents directly.
        role_take_rows = [
            e for e in outcome.audit_entries if e.action == "role_take_record"
        ]
        self.assertEqual(len(role_take_rows), 2)
        for entry in role_take_rows:
            self.assertIn("fallback", entry.outcome)
            self.assertEqual(entry.autonomy_level, "L1_AUTO_RECORD_REQUIRED")

    def test_inactive_role_left_silent(self) -> None:
        # active_research_roles=("tech-lead",) — the dispatcher's
        # active-role gate must skip non-listed roles.
        session = _make_session(active_roles=("tech-lead",))
        runner = _AlwaysOkRoleRunner()
        dispatcher = build_role_runner_dispatcher(candidates=(runner,))
        outcome = handle_research_order(
            session=session,
            research_thread_id=session.extra["research_forum_thread_id"],
            snapshot=_stub_snapshot(),
            role_runner_dispatch=dispatcher,
            obsidian_writer_worker=self.obsidian_worker,
            active_roles=("tech-lead", "devops-engineer"),
        )
        # devops-engineer is not in the active list → dispatcher returns
        # status="inactive_role". The coordinator records the audit but
        # the runner was never invoked.
        roles_invoked = {input_.role for input_ in runner.calls}
        self.assertEqual(roles_invoked, {"tech-lead"})
        statuses = {ro.role: ro.status for ro in outcome.role_outputs}
        self.assertEqual(statuses["tech-lead"], STATUS_OK)
        self.assertEqual(statuses["devops-engineer"], "inactive_role")


# ---------------------------------------------------------------------------
# L3 approval gate — knowledge final 은 별도 forum-handoff 가
# 처리한다. M13 e2e 는 그 경로가 살아 있는지만 확인.
# ---------------------------------------------------------------------------


class L3ApprovalGateTests(_WorkerFixture):
    def test_save_request_creates_l3_card_and_dedups_duplicate(self) -> None:
        # Definition of Done #4: 중복 approval 이 생기지 않는다.
        # Definition of Done #7: 위험한 작업은 승인 대기로 간다.
        from yule_orchestrator.agents.job_queue.approval_worker import (
            APPROVAL_KIND_OBSIDIAN_WRITE,
            ApprovalWorker,
        )
        from yule_orchestrator.agents.job_queue.forum_obsidian_handoff import (
            SKIPPED_DUPLICATE_APPROVAL,
            SKIPPED_TOPIC_PENDING_APPROVAL,
            route_forum_obsidian_save_request,
        )

        APPROVAL_CHANNEL_ID = 80001
        posted: List[Tuple[Any, str]] = []

        async def post_fn(request, rendered_text):
            posted.append((request, rendered_text))
            return {
                "posted_message_id": 90000 + len(posted),
                "channel_id": APPROVAL_CHANNEL_ID,
            }

        approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: APPROVAL_CHANNEL_ID,
        )

        session = _make_session(session_id="sess-m13-l3")
        # First, run a research-order pass so the topic ledger is
        # persisted ahead of the save request — same shape as
        # production where the agent has already collected sources.
        handle_research_order(
            session=session,
            research_thread_id=session.extra["research_forum_thread_id"],
            snapshot=_stub_snapshot(),
            obsidian_writer_worker=self.obsidian_worker,
        )

        channel = SimpleNamespace(
            id=session.extra["research_forum_thread_id"],
            parent_id=50000,
            parent=SimpleNamespace(id=50000, name="운영-리서치"),
            name="k8s 운영 자료",
            guild=SimpleNamespace(id=40000),
        )
        author = SimpleNamespace(
            id=7, name="masterway", global_name="masterway"
        )
        message = SimpleNamespace(
            id=60001,
            channel=channel,
            author=author,
            content="Obsidian에 정리하고 싶어",
            guild=SimpleNamespace(id=40000),
            jump_url="https://discord.com/channels/40000/50001/60001",
        )
        first = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertTrue(first.handled)
        self.assertIsNotNone(first.approval_job_id)
        self.assertEqual(len(posted), 1)
        approval_request, _rendered = posted[0]
        self.assertEqual(
            approval_request.approval_kind, APPROVAL_KIND_OBSIDIAN_WRITE
        )
        # extra carries hydration the writer renders later.
        self.assertEqual(approval_request.extra["source_thread_url"], message.jump_url)

        # Second message with a fresh id but same thread → topic
        # dedup blocks a second card.
        message2 = SimpleNamespace(**{**message.__dict__, "id": 60002})
        message2.channel = channel
        message2.author = author
        message2.guild = SimpleNamespace(id=40000)
        second = _run(
            route_forum_obsidian_save_request(
                message=message2,
                text=message2.content,
                queue=self.queue,
                approval_worker=approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertIn(
            second.skipped_reason,
            {SKIPPED_TOPIC_PENDING_APPROVAL, SKIPPED_DUPLICATE_APPROVAL},
        )
        # Still exactly one approval card posted.
        self.assertEqual(len(posted), 1)


# ---------------------------------------------------------------------------
# Empty-snapshot guard — 빈 문서가 저장되지 않는다.
# ---------------------------------------------------------------------------


class EmptyKnowledgeGuardTests(_WorkerFixture):
    """Definition of Done #3: 빈 문서가 저장되지 않는다.

    The renderer (default_render_fn for knowledge) raises
    ObsidianRenderError when nothing hydrates the body. We exercise
    that here against a bare session (no pack / snapshot / synthesis)
    to prove the guard is still in place after the M13 wiring.
    """

    def test_knowledge_render_refuses_empty_hydration(self) -> None:
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
            save_session,
        )

        env_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(env_tmp.cleanup)
        env = mock.patch.dict(
            os.environ,
            {
                "YULE_CACHE_DB_PATH": str(Path(env_tmp.name) / "q.sqlite3"),
                "YULE_REPO_ROOT": str(env_tmp.name),
                "OBSIDIAN_VAULT_PATH": str(env_tmp.name),
            },
        )
        env.start()
        self.addCleanup(env.stop)

        when = datetime.now(tz=timezone.utc)
        session = WorkflowSession(
            session_id="sess-m13-empty",
            prompt="empty",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=when,
            updated_at=when,
            role_sequence=(),
            extra={},
        )
        save_session(session)

        request = ObsidianWriteRequest(
            session_id=session.session_id,
            note_kind=NOTE_KIND_KNOWLEDGE,
            title="hollow",
            approval_id="apv-empty",
            approved_by="masterway",
            approved_at=when.replace(microsecond=0).isoformat(),
        )
        with self.assertRaises(ObsidianRenderError) as ctx:
            default_render_fn(request)
        self.assertIn("hydration", str(ctx.exception))


# ---------------------------------------------------------------------------
# Self-improvement proposal on failure
# ---------------------------------------------------------------------------


class SelfImprovementProposalTests(_WorkerFixture):
    def test_failed_retryable_pileup_emits_proposal(self) -> None:
        # Definition of Done #6: 실패하면 improvement proposal 이 생성된다.
        session = _make_session(session_id="sess-m13-improve")

        # Synthesise four FAILED_RETRYABLE jobs above the threshold.
        failing_jobs = [
            SimpleNamespace(
                job_id=f"job-{i}",
                job_type="obsidian_write",
                state=SimpleNamespace(value="failed_retryable"),
                payload={},
                result={"error": "hydration 부족 — 빈 노트 거부"},
            )
            for i in range(4)
        ]
        outcome = emit_self_improvement_proposal(
            session=session,
            jobs=failing_jobs,
            failed_jobs=failing_jobs,
            obsidian_writer_worker=self.obsidian_worker,
            failed_retryable_threshold=3,
        )
        self.assertGreater(len(outcome.signals), 0)
        self.assertIn(
            SIGNAL_FAILED_RETRYABLE_PILEUP,
            {s.signal for s in outcome.signals},
        )
        self.assertIsNotNone(outcome.proposal_job_id)
        self.assertTrue(outcome.proposal_created)
        self.assertIsNotNone(outcome.audit_entry)
        self.assertEqual(
            outcome.audit_entry.action, "self_improvement_proposal"
        )
        self.assertEqual(
            outcome.audit_entry.autonomy_level, "L2_AUTO_POST_REPORT"
        )

        # Vault row landed with note_kind=self-improvement-proposal +
        # body containing the signal markdown.
        rows = self._write_rows(
            session.session_id, kind=NOTE_KIND_SELF_IMPROVEMENT_PROPOSAL
        )
        self.assertEqual(len(rows), 1)
        meta = (rows[0].payload or {}).get("metadata") or {}
        self.assertIn(SIGNAL_FAILED_RETRYABLE_PILEUP, meta.get("body", ""))

    def test_no_signals_is_no_op(self) -> None:
        # Healthy queue → coordinator stays silent (and most importantly
        # doesn't create a daily flood of empty proposal notes).
        session = _make_session(session_id="sess-m13-quiet")
        outcome = emit_self_improvement_proposal(
            session=session,
            jobs=(),
            obsidian_writer_worker=self.obsidian_worker,
        )
        self.assertEqual(outcome.signals, ())
        self.assertIsNone(outcome.proposal_job_id)
        self.assertFalse(outcome.proposal_created)
        rows = self._write_rows(session.session_id)
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# replay_audit_entries surface
# ---------------------------------------------------------------------------


class ReplayAuditTests(_WorkerFixture):
    def test_replay_returns_persisted_audit_in_order(self) -> None:
        session = _make_session(session_id="sess-m13-replay")
        handle_research_order(
            session=session,
            research_thread_id=session.extra["research_forum_thread_id"],
            snapshot=_stub_snapshot(),
            obsidian_writer_worker=self.obsidian_worker,
        )
        replayed = replay_audit_entries(session)
        # Intake audit lands first by construction.
        self.assertGreaterEqual(len(replayed), 2)
        actions_in_order = [e.action for e in replayed]
        self.assertEqual(actions_in_order[0], "user_ordered_research")
        self.assertIn("research_log_save", actions_in_order)


# ---------------------------------------------------------------------------
# M8-M12 import sanity — surfaces the coordinator depends on must
# all import cleanly when this module loads.
# ---------------------------------------------------------------------------


class RegressionImportSanityTests(unittest.TestCase):
    def test_m8_to_m12_surfaces_import(self) -> None:
        # M8 — runtime status / gateway env (readiness path).
        from yule_orchestrator.runtime import status as _m8_status  # noqa: F401
        from yule_orchestrator.runtime import gateway_env as _m8_env  # noqa: F401
        # M9 — research_topic ledger.
        from yule_orchestrator.agents.lifecycle import research_topic as _m9  # noqa: F401
        # M10a — autonomy + agent_ops_log.
        from yule_orchestrator.agents.lifecycle import autonomy_policy as _m10a_policy  # noqa: F401
        from yule_orchestrator.agents.lifecycle import agent_ops_log as _m10a_audit  # noqa: F401
        # M10b — Obsidian hydration.
        from yule_orchestrator.agents.job_queue import (  # noqa: F401
            forum_obsidian_handoff as _m10b_handoff,
            obsidian_writer_worker as _m10b_writer,
        )
        # M10c — research-log auto save / autonomous producers.
        from yule_orchestrator.agents.lifecycle import autonomous_producers as _m10c  # noqa: F401
        # M11 — role-runner dispatcher.
        from yule_orchestrator.agents.runners import role_runner as _m11  # noqa: F401
        # M12 — self-improvement signals.
        from yule_orchestrator.agents.lifecycle import self_improvement as _m12  # noqa: F401
        # If we got here, the dependency graph holds.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
