"""Live MVP regression — research-only requests must NOT show a coding executor.

Pin the live-bug ask:

  • The k8s research prompt ("오늘은 코드 수정 없이 자료 수집이 목표야")
    should produce a research-only proposal — no executor pick, no
    write scope, no approval prompt for code changes.
  • Display layer must show "조사 중심 역할" instead of "executor: ...".
  • Implementation requests (Spring Security API 인증) keep their
    legacy executor-mode behaviour — research-only is a strict subset.
  • Trying to flip a research-only proposal into a CodingJob via the
    approval path must raise so the gateway can ask the user to issue
    a fresh "수정 권한 제안" first.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.coding.authorization import (
    LIFECYCLE_MODE_IMPLEMENTATION,
    LIFECYCLE_MODE_RESEARCH_ONLY,
    format_authorization_message,
    recommend_authorization,
    reset_role_profile_cache,
)
from yule_orchestrator.agents.coding.job import (
    STATUS_READY,
    build_coding_job_from_proposal,
)


# Verbatim live prompt.
K8S_RESEARCH_PROMPT = (
    "오늘은 k8s 쿠버네티스에 대해서 다루고 싶어. "
    "어떤 지식들이 필요할까? "
    "오늘은 코드 수정 없이 자료 수집이 목표야."
)


class ResearchOnlyProposalShapeTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        reset_role_profile_cache()

    def test_k8s_research_prompt_returns_research_only_mode(self) -> None:
        proposal = recommend_authorization(
            user_request=K8S_RESEARCH_PROMPT,
            session_id="sess-k8s",
        )
        self.assertEqual(proposal.lifecycle_mode, LIFECYCLE_MODE_RESEARCH_ONLY)
        # No executor — that's the whole point of research-only mode.
        self.assertEqual(proposal.executor_role, "")
        # Research leads must include devops + backend (the rule bank
        # ranked them top for k8s in Phase 3); their order matches the
        # scored ranking.
        self.assertIn("devops-engineer", proposal.research_leads)
        self.assertIn("backend-engineer", proposal.research_leads)
        # tech-lead always reviews so the chain stays anchored.
        self.assertIn("tech-lead", proposal.review_roles)
        # No approval phrase needed for research — the user already
        # said no code changes.
        self.assertFalse(proposal.approval_required)
        # write_scope must be empty so the executor prompt builder
        # cannot accidentally hand a research-only proposal to a coder.
        self.assertEqual(proposal.write_scope, ())

    def test_research_only_metadata_carries_lifecycle_mode(self) -> None:
        proposal = recommend_authorization(
            user_request=K8S_RESEARCH_PROMPT,
        )
        # Metadata duplicates the proposal-level fields so the value
        # round-trips through ``_proposal_to_dict``'s metadata bag.
        self.assertEqual(
            proposal.metadata.get("lifecycle_mode"),
            LIFECYCLE_MODE_RESEARCH_ONLY,
        )
        leads = proposal.metadata.get("research_leads")
        self.assertIsInstance(leads, list)
        self.assertIn("devops-engineer", leads)

    def test_research_only_safety_rules_forbid_writes(self) -> None:
        proposal = recommend_authorization(
            user_request="자료 수집이 목표 — k8s ingress 운영 모범사례 정리해줘",
        )
        joined = "\n".join(proposal.safety_rules)
        self.assertIn("research_pack", joined)
        self.assertIn("수정하지 않는다", joined)
        # Forbidden scope must explicitly call out "코드/문서/설정 파일 수정"
        # so the safety hint is unambiguous.
        joined_forbidden = "\n".join(proposal.forbidden_scope)
        self.assertIn("수정", joined_forbidden)


class ResearchOnlyTriggerPhraseTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        reset_role_profile_cache()

    def test_jorae_keyword_triggers_research_only(self) -> None:
        proposal = recommend_authorization(
            user_request="RAG/CAG memory 구조 조사해줘"
        )
        self.assertEqual(proposal.lifecycle_mode, LIFECYCLE_MODE_RESEARCH_ONLY)
        self.assertIn("ai-engineer", proposal.research_leads)

    def test_jeongri_kkajiman_triggers_research_only(self) -> None:
        proposal = recommend_authorization(
            user_request="React 컴포넌트 조사해서 보고서로 정리까지만 해줘"
        )
        self.assertEqual(proposal.lifecycle_mode, LIFECYCLE_MODE_RESEARCH_ONLY)
        self.assertEqual(proposal.executor_role, "")

    def test_implementation_request_stays_in_implementation_mode(self) -> None:
        # Negative case — Spring Security API 인증 is a clear
        # implementation ask. Research-only must NOT trigger.
        proposal = recommend_authorization(
            user_request="Spring Security 기반 API 인증 흐름 추가해줘",
        )
        self.assertEqual(proposal.lifecycle_mode, LIFECYCLE_MODE_IMPLEMENTATION)
        self.assertEqual(proposal.executor_role, "backend-engineer")
        self.assertTrue(proposal.approval_required)


class ResearchOnlyDisplayTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        reset_role_profile_cache()

    def test_message_omits_executor_label(self) -> None:
        proposal = recommend_authorization(
            user_request=K8S_RESEARCH_PROMPT,
        )
        text = format_authorization_message(proposal)
        # The implementation-mode header is replaced.
        self.assertNotIn("코딩 권한 제안", text)
        self.assertIn("조사 단계", text)
        # User-visible label must be "조사 중심 역할", not "executor:".
        self.assertIn("조사 중심 역할", text)
        self.assertNotIn("executor:", text)
        # Research leads appear in the body.
        self.assertIn("devops-engineer", text)
        # The follow-up phrase asks for explicit opt-in to coding.
        self.assertIn("수정 권한 제안", text)


class ResearchOnlySessionExtraTests(unittest.TestCase):
    """Phase 2 bullet 5 — intake must stamp lifecycle_mode +
    executor_role=None + research_leads on session.extra so the work
    report builder / status diagnostic / member bot researchers all
    see the same answer without re-running detection."""

    def setUp(self) -> None:  # noqa: D401 - test setup
        reset_role_profile_cache()

    def _persist(self, prompt: str) -> dict:
        from yule_orchestrator.discord.engineering_channel_router import (
            _persist_lifecycle_mode,
        )

        # Minimal stand-in for WorkflowSession — _persist_lifecycle_mode
        # calls _persist_extra_keys which only needs `.extra` to be a
        # dict-like attribute.
        class _Session:
            def __init__(self) -> None:
                self.session_id = "sess-research-only"
                self.extra: dict = {}

        session = _Session()
        result = _persist_lifecycle_mode(session, prompt)
        return dict(getattr(result, "extra", {}) or {})

    def test_research_only_prompt_marks_session_extra(self) -> None:
        extra = self._persist(K8S_RESEARCH_PROMPT)
        self.assertEqual(extra.get("lifecycle_mode"), "research_only")
        self.assertIsNone(extra.get("executor_role"))
        leads = extra.get("research_leads")
        self.assertIsInstance(leads, list)
        self.assertIn("devops-engineer", leads)

    def test_implementation_prompt_marks_session_extra(self) -> None:
        extra = self._persist("Spring Security API 인증 흐름 추가해줘")
        self.assertEqual(extra.get("lifecycle_mode"), "implementation")
        # executor_role is not stamped at intake for implementation
        # mode — that flows through the coding-authorization proposal
        # path instead.
        self.assertNotIn("executor_role", extra)


class ResearchOnlyJobBuilderTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        reset_role_profile_cache()

    def test_build_coding_job_refuses_research_only_proposal(self) -> None:
        # If the gateway slips and tries to convert a research-only
        # proposal into a coding job (e.g. user types "구현 진행"
        # without re-running the proposal step), the builder must
        # raise so the gateway can fall back to "재제안 필요" instead
        # of silently giving an executor an empty write_scope.
        proposal = recommend_authorization(user_request=K8S_RESEARCH_PROMPT)
        with self.assertRaises(ValueError):
            build_coding_job_from_proposal(proposal, status=STATUS_READY)


if __name__ == "__main__":
    unittest.main()
