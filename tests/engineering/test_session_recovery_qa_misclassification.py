"""Canonical-session recovery from qa-test misclassification — P0-W.

Live live-smoke (canonical session ``11917bf1e75d``):

  * issue anchor 까지는 생성됨 (`github_work_order_issue.issue_number=1`)
  * 그러나 session.task_type 이 ``qa-test`` 로 잘못 분류되어 있고
  * session.extra['coding_proposal'] 가 비어있어
  * continuation 이 `no_coding_proposal` noop 으로 멈춤.

본 모듈은 사용자가 명시한 5 케이스를 커버:

  1. issue-less full-stack request 가 qa-test 로 잘못 분류되지 않아야 함
  2. coding/full-stack path 에서 coding_proposal 이 session.extra 에 남아야 함
  3. 이미 잘못 분류된 session 도 bug fix 후 복구 가능해야 함 (anchor 보존)
  4. continuation 이 coding_execute 단계로 이어져야 함
  5. operator 가 새 intake 를 다시 넣을 필요 없어야 함 (repair-only path)
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_engineering.agents.coding.stack_detector import detect_stacks
from yule_engineering.agents.job_queue.work_order_coding_continuation import (
    REPAIR_OUTCOME_NO_ANCHOR,
    REPAIR_OUTCOME_NO_SESSION,
    REPAIR_OUTCOME_REPAIRED,
    SESSION_EXTRA_CODING_JOB_KEY,
    SESSION_EXTRA_CODING_PROPOSAL_KEY,
    SessionRepairOutcome,
    repair_session_for_coding_dispatch,
)
from yule_engineering.agents.messaging.dispatcher import (
    Dispatcher,
    DispatchRequest,
    TaskType,
)
from yule_engineering.agents.messaging.registry import ParticipantsPool


_LIVE_PROMPT_KOREAN = (
    "repo: https://github.com/yule-studio/naver-search-clone.git\n"
    "목표: 네이버 검색 풀스택 MVP 구현해줘. "
    "프론트 / 백엔드 / 데이터베이스 / 도커 / 회원가입 + 검색 + 블로그 + 메일."
)


def _empty_dispatcher() -> Dispatcher:
    return Dispatcher(
        ParticipantsPool(agent_id="engineering-agent", runners={}, warnings=())
    )


# ---------------------------------------------------------------------------
# 1. issue-less full-stack request 가 qa-test 로 분류되지 않음
# ---------------------------------------------------------------------------


class IssuelessFullStackClassificationTests(unittest.TestCase):
    def test_korean_full_stack_prompt_classified_as_full_stack(self) -> None:
        dispatcher = _empty_dispatcher()
        request = DispatchRequest(
            prompt=_LIVE_PROMPT_KOREAN, write_requested=True
        )
        self.assertEqual(dispatcher.classify(request), TaskType.FULL_STACK_APP)

    def test_english_stack_prompt_still_full_stack(self) -> None:
        dispatcher = _empty_dispatcher()
        request = DispatchRequest(
            prompt="Next.js + NestJS + PostgreSQL + Docker Compose MVP",
            write_requested=True,
        )
        self.assertEqual(dispatcher.classify(request), TaskType.FULL_STACK_APP)

    def test_bare_qa_substring_in_unrelated_word_does_not_trigger_qa_test(
        self,
    ) -> None:
        dispatcher = _empty_dispatcher()
        # naver-quasar 패키지 — 옛 heuristic 은 "qa" substring 으로 trip 함
        request = DispatchRequest(
            prompt="naver-quasar 패키지 디버깅 한 번 해줘", write_requested=False
        )
        self.assertNotEqual(dispatcher.classify(request), TaskType.QA_TEST)

    def test_explicit_qa_phrase_still_classified_as_qa_test(self) -> None:
        """진짜 QA 요청은 여전히 qa-test 로 분류돼야 한다 (false negative 회귀)."""
        dispatcher = _empty_dispatcher()
        for prompt in (
            "회귀 테스트 시나리오를 짜줘",
            "qa engineer 가 새 test plan 짜줘",
            "테스트 자동화 커버리지 확인",
            "regression test 시나리오 정리",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(
                    dispatcher.classify(
                        DispatchRequest(prompt=prompt, write_requested=False)
                    ),
                    TaskType.QA_TEST,
                )

    def test_korean_pulstack_hint_with_write_request_forces_full_stack(self) -> None:
        """`풀스택` 단어가 있고 write_requested=True 면 FULL_STACK_APP."""

        dispatcher = _empty_dispatcher()
        request = DispatchRequest(
            prompt="풀스택 MVP 구현해줘. 회원가입 + 검색.",
            write_requested=True,
        )
        self.assertEqual(dispatcher.classify(request), TaskType.FULL_STACK_APP)


# ---------------------------------------------------------------------------
# 2. stack_detector — Korean tier aliases + explicit hint
# ---------------------------------------------------------------------------


class StackDetectorKoreanTests(unittest.TestCase):
    def test_korean_tier_words_register_tiers(self) -> None:
        det = detect_stacks("프론트 / 백엔드 / 데이터베이스 / 도커")
        self.assertIn("frontend", det.tiers_present)
        self.assertIn("backend", det.tiers_present)
        self.assertIn("database", det.tiers_present)
        self.assertIn("infra", det.tiers_present)
        self.assertTrue(det.is_full_stack)

    def test_korean_auth_words_register_auth_tier(self) -> None:
        det = detect_stacks("회원가입 + 로그인 + 소셜 로그인")
        self.assertIn("auth", det.tiers_present)

    def test_explicit_full_stack_hint_with_single_tier(self) -> None:
        det = detect_stacks("풀스택 MVP — 프론트만 다듬어줘")
        self.assertTrue(det.explicit_full_stack_hint)
        self.assertTrue(det.is_full_stack)  # explicit hint + 1 app tier OK

    def test_no_hint_no_tiers_returns_false(self) -> None:
        det = detect_stacks("그냥 잡담")
        self.assertFalse(det.is_full_stack)
        self.assertFalse(det.explicit_full_stack_hint)


# ---------------------------------------------------------------------------
# 3. slash intake 가 coding_proposal 을 session.extra 에 남김
# ---------------------------------------------------------------------------


@dataclass
class _SessionStub:
    session_id: str
    prompt: str
    extra: Dict[str, Any] = field(default_factory=dict)


class IntakePersistsCodingProposalTests(unittest.TestCase):
    """``_ensure_coding_proposal_on_session`` 헬퍼는 idempotent + safe."""

    def test_stamps_coding_proposal_for_coding_intent_prompt(self) -> None:
        from yule_discord.commands import (
            _ensure_coding_proposal_on_session,
        )

        session = _SessionStub(
            session_id="sess-new",
            prompt=_LIVE_PROMPT_KOREAN,
        )
        _ensure_coding_proposal_on_session(session, _LIVE_PROMPT_KOREAN)

        proposal = session.extra.get("coding_proposal")
        self.assertIsInstance(proposal, Mapping)
        self.assertIn("executor_role", proposal)
        self.assertIn("review_roles", proposal)

    def test_idempotent_when_proposal_already_present(self) -> None:
        from yule_discord.commands import (
            _ensure_coding_proposal_on_session,
        )

        existing = {"executor_role": "tech-lead", "marker": "do_not_overwrite"}
        session = _SessionStub(
            session_id="sess-prev",
            prompt=_LIVE_PROMPT_KOREAN,
            extra={"coding_proposal": existing},
        )
        _ensure_coding_proposal_on_session(session, _LIVE_PROMPT_KOREAN)

        # existing payload 그대로 (덮어쓰지 않음)
        self.assertEqual(
            session.extra["coding_proposal"]["marker"], "do_not_overwrite"
        )


# ---------------------------------------------------------------------------
# 4 + 5. repair_session_for_coding_dispatch — anchor 있는 stranded session
# ---------------------------------------------------------------------------


class SessionRepairTests(unittest.TestCase):
    def _make_stranded_session(
        self, session_id: str = "11917bf1e75d"
    ) -> _SessionStub:
        return _SessionStub(
            session_id=session_id,
            prompt=_LIVE_PROMPT_KOREAN,
            extra={
                "github_work_order_issue": {
                    "issue_number": 1,
                    "repo": "yule-studio/naver-search-clone",
                    "created_via": "auto_create",
                    "dry_run": False,
                    "approval_id": "approval-1",
                    "approved_by": "operator",
                    "approved_at": "2026-05-17T11:00:00+00:00",
                    "html_url": "https://github.com/yule-studio/naver-search-clone/issues/1",
                },
                # coding_proposal 의도적으로 비어있음 — 이게 핵심 회귀
            },
        )

    def test_repair_rebuilds_proposal_and_promotes_coding_job(self) -> None:
        session = self._make_stranded_session()
        store: Dict[str, _SessionStub] = {session.session_id: session}

        def _load(sid: str) -> Optional[_SessionStub]:
            return store.get(sid)

        update_calls: List = []

        def _update(s: _SessionStub, new_extra: Mapping[str, Any]) -> None:
            store[s.session_id] = s
            update_calls.append((s.session_id, dict(new_extra)))

        outcome = repair_session_for_coding_dispatch(
            session_id=session.session_id,
            load_session_fn=_load,
            update_session_fn=_update,
        )

        self.assertEqual(outcome.outcome, REPAIR_OUTCOME_REPAIRED)
        self.assertTrue(outcome.coding_proposal_rebuilt)
        self.assertTrue(outcome.promoted)
        self.assertIsNotNone(outcome.continuation)
        self.assertIsNone(outcome.continuation.noop_reason)

        # session.extra 가 갱신됨
        new_session = store[session.session_id]
        proposal = new_session.extra.get(SESSION_EXTRA_CODING_PROPOSAL_KEY)
        self.assertIsInstance(proposal, Mapping)
        # coding_job 까지 ready 로 promote
        coding_job = new_session.extra.get(SESSION_EXTRA_CODING_JOB_KEY)
        self.assertIsInstance(coding_job, Mapping)
        self.assertEqual(str(coding_job.get("status") or "").lower(), "ready")
        # anchor metadata 가 coding_job.metadata 에 stamp
        metadata = coding_job.get("metadata") or {}
        self.assertEqual(metadata.get("issue_number"), 1)
        self.assertEqual(
            metadata.get("repo_full_name"),
            "yule-studio/naver-search-clone",
        )

    def test_repair_reclassifies_task_type_when_wrongly_qa_test(self) -> None:
        session = self._make_stranded_session()
        session.task_type = "qa-test"
        session.executor_role = "qa-engineer"
        store = {session.session_id: session}

        def _load(sid):
            return store.get(sid)

        def _update(s, _extra):
            store[s.session_id] = s

        outcome = repair_session_for_coding_dispatch(
            session_id=session.session_id,
            load_session_fn=_load,
            update_session_fn=_update,
            reclassify_task_type=True,
        )

        self.assertEqual(outcome.outcome, REPAIR_OUTCOME_REPAIRED)
        self.assertTrue(outcome.task_type_reclassified)
        # session.task_type / executor_role 가 갱신됨
        new_session = store[session.session_id]
        self.assertEqual(new_session.task_type, TaskType.FULL_STACK_APP.value)
        self.assertEqual(new_session.executor_role, "backend-engineer")

    def test_repair_returns_no_session_when_id_missing(self) -> None:
        def _load(sid):
            return None

        def _update(s, e):
            pass

        outcome = repair_session_for_coding_dispatch(
            session_id="not-found",
            load_session_fn=_load,
            update_session_fn=_update,
        )
        self.assertEqual(outcome.outcome, REPAIR_OUTCOME_NO_SESSION)

    def test_repair_returns_no_anchor_when_no_github_work_order_issue(self) -> None:
        session = _SessionStub(
            session_id="sess-no-anchor",
            prompt=_LIVE_PROMPT_KOREAN,
            extra={},
        )
        store = {session.session_id: session}

        outcome = repair_session_for_coding_dispatch(
            session_id=session.session_id,
            load_session_fn=lambda sid: store.get(sid),
            update_session_fn=lambda s, e: None,
        )
        self.assertEqual(outcome.outcome, REPAIR_OUTCOME_NO_ANCHOR)

    def test_repair_preserves_existing_coding_proposal(self) -> None:
        """이미 proposal 이 있으면 새로 만들지 않고 promote 만 시도."""
        session = self._make_stranded_session()
        # 기존 proposal — 직접 minimal payload 주입
        session.extra["coding_proposal"] = {
            "session_id": session.session_id,
            "user_request": _LIVE_PROMPT_KOREAN,
            "executor_role": "backend-engineer",
            "review_roles": ["tech-lead"],
            "participant_roles": ["backend-engineer", "tech-lead"],
            "write_scope": [],
            "forbidden_scope": [],
            "reason": "preserved",
            "safety_rules": [],
            "approval_required": True,
            "metadata": {"preserved_marker": "yes"},
            "lifecycle_mode": "implementation",
            "research_leads": [],
        }
        store = {session.session_id: session}

        outcome = repair_session_for_coding_dispatch(
            session_id=session.session_id,
            load_session_fn=lambda sid: store.get(sid),
            update_session_fn=lambda s, e: store.__setitem__(s.session_id, s),
        )
        self.assertEqual(outcome.outcome, REPAIR_OUTCOME_REPAIRED)
        # 새로 build 하지 않음
        self.assertFalse(outcome.coding_proposal_rebuilt)
        # promote 는 시도
        self.assertTrue(outcome.promoted)
        new_session = store[session.session_id]
        proposal = new_session.extra[SESSION_EXTRA_CODING_PROPOSAL_KEY]
        self.assertEqual(proposal["metadata"]["preserved_marker"], "yes")

    def test_repair_idempotent_when_called_twice(self) -> None:
        session = self._make_stranded_session()
        store = {session.session_id: session}

        def _load(sid):
            return store.get(sid)

        def _update(s, _extra):
            store[s.session_id] = s

        first = repair_session_for_coding_dispatch(
            session_id=session.session_id,
            load_session_fn=_load,
            update_session_fn=_update,
        )
        second = repair_session_for_coding_dispatch(
            session_id=session.session_id,
            load_session_fn=_load,
            update_session_fn=_update,
        )

        # 첫 호출: proposal 재구성 + promote
        self.assertTrue(first.coding_proposal_rebuilt)
        self.assertTrue(first.promoted)
        # 두 번째 호출: proposal 이미 있음 → 재구성 안 함, promote 도 noop
        self.assertFalse(second.coding_proposal_rebuilt)
        self.assertEqual(second.outcome, REPAIR_OUTCOME_REPAIRED)
        # 두 번째 promote 는 already_ready noop 가 정상
        if second.continuation is not None:
            self.assertIn(
                second.continuation.noop_reason,
                (None, "coding_job_already_ready_same_anchor"),
            )


if __name__ == "__main__":
    unittest.main()
