"""role_selection — A-M7.5 additions: all-team detection, effective-roles
helper, role-change parser, routing summary.

Pin the user spec's role-selection contract:

  * No keyword-only hardcode — selector ranks via the existing
    rule bank and only fans out on explicit "전체 팀" / "all roles".
  * unknown / vague prompts collapse to a small fallback (never
    full-team).
  * Eight prompt × {selected, primary, excluded} contract test.
  * effective active roles helper resolves session.extra first,
    role_sequence second, tech-lead-only minimum third.
  * Korean role-add / role-remove parser + audit shape.
  * Routing summary 3-5 line render.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.role_selection import (
    ALL_ENGINEERING_ROLES,
    ROLE_TECH_LEAD,
    SOURCE_USER_ALL_TEAM,
    SOURCE_USER_EXPLICIT,
    RoleChangeRequest,
    apply_role_change,
    append_role_change_audit,
    format_routing_summary,
    get_effective_active_roles,
    parse_role_change_request,
    recommend_active_roles,
)


# ---------------------------------------------------------------------------
# Eight-prompt contract matrix (user spec section "테스트 matrix")
# ---------------------------------------------------------------------------


class PromptMatrixTests(unittest.TestCase):
    def _select(self, prompt: str):
        return recommend_active_roles(user_prompt=prompt)

    def test_devops_learning_prompt_selects_devops_only(self) -> None:
        sel = self._select(
            "오늘은 DevOps 엔지니어가 되려면 어떤걸 어떻게 공부해야 될지 알고 싶어"
        )
        self.assertIn(ROLE_TECH_LEAD, sel.selected_roles)
        self.assertIn("devops-engineer", sel.selected_roles)
        # The other role bots must not be in the selected list.
        for role in (
            "backend-engineer",
            "frontend-engineer",
            "product-designer",
            "qa-engineer",
            "ai-engineer",
        ):
            with self.subTest(role=role):
                self.assertIn(role, sel.excluded_roles)
                self.assertNotIn(role, sel.selected_roles)

    def test_spring_backend_prompt_selects_backend_only(self) -> None:
        sel = self._select(
            "Spring Boot 로 백엔드 API 설계를 어떻게 잡는 게 좋을까?"
        )
        self.assertIn("backend-engineer", sel.selected_roles)
        for role in (
            "frontend-engineer",
            "product-designer",
            "devops-engineer",
            "qa-engineer",
            "ai-engineer",
        ):
            with self.subTest(role=role):
                self.assertNotIn(role, sel.selected_roles)

    def test_qa_strategy_prompt_selects_qa_only(self) -> None:
        sel = self._select(
            "테스트 전략을 짜줘. 회귀 테스트도 고려해줘"
        )
        self.assertIn("qa-engineer", sel.selected_roles)
        for role in (
            "product-designer",
            "frontend-engineer",
            "backend-engineer",
            "devops-engineer",
            "ai-engineer",
        ):
            with self.subTest(role=role):
                self.assertNotIn(role, sel.selected_roles)

    def test_ai_research_prompt_selects_ai_engineer(self) -> None:
        sel = self._select("AI/RAG 구조를 검토해줘 — embedding 도 포함해서")
        self.assertIn("ai-engineer", sel.selected_roles)

    def test_unknown_vague_prompt_does_not_fan_out_to_all_roles(
        self,
    ) -> None:
        # The user spec's hard rule: unknown != all-roles. The
        # selector should keep participation small — definitely
        # not fan-out to every role.
        sel = self._select("그냥 안녕하세요")
        # tech-lead always present
        self.assertIn(ROLE_TECH_LEAD, sel.selected_roles)
        # Strict: a vague greeting must not produce more than the
        # legacy quartet (tech-lead + ai + backend + qa is the
        # historical safety net the existing code already
        # enforces for vague / non-engineering prompts).
        self.assertLessEqual(
            len(sel.selected_roles),
            4,
            f"unknown prompt fanned out: {sel.selected_roles}",
        )
        # And specifically NOT all 7 roles.
        self.assertLess(
            len(sel.selected_roles), len(ALL_ENGINEERING_ROLES)
        )

    def test_explicit_all_team_prompt_selects_every_role(self) -> None:
        sel = self._select(
            "전체 팀 관점에서 이 기능 출시 전에 리스크 리뷰해줘"
        )
        self.assertEqual(sel.selection_source, SOURCE_USER_ALL_TEAM)
        # Every role from the inventory is selected.
        for role in ALL_ENGINEERING_ROLES:
            with self.subTest(role=role):
                self.assertIn(role, sel.selected_roles)
        self.assertEqual(sel.excluded_roles, ())

    def test_explicit_role_mention_marks_user_explicit_source(self) -> None:
        sel = self._select(
            "backend-engineer 관점에서 transaction 격리 설명해줘"
        )
        self.assertEqual(sel.selection_source, SOURCE_USER_EXPLICIT)
        self.assertIn("backend-engineer", sel.selected_roles)


# ---------------------------------------------------------------------------
# get_effective_active_roles — canonical helper
# ---------------------------------------------------------------------------


class EffectiveActiveRolesTests(unittest.TestCase):
    def test_session_extra_active_roles_wins_over_role_sequence(
        self,
    ) -> None:
        session = SimpleNamespace(
            extra={"active_research_roles": ["tech-lead", "qa-engineer"]},
            role_sequence=("tech-lead", "backend-engineer", "frontend-engineer"),
        )
        active = get_effective_active_roles(session)
        self.assertEqual(active, ("tech-lead", "qa-engineer"))

    def test_role_sequence_used_when_active_research_roles_empty(
        self,
    ) -> None:
        session = SimpleNamespace(
            extra={},
            role_sequence=("tech-lead", "backend-engineer"),
        )
        active = get_effective_active_roles(session)
        self.assertEqual(active, ("tech-lead", "backend-engineer"))

    def test_minimum_fallback_is_tech_lead_only(self) -> None:
        # Neither active_research_roles NOR role_sequence — fallback
        # MUST be tech-lead only, not the legacy 4-role quartet.
        session = SimpleNamespace(extra={}, role_sequence=())
        active = get_effective_active_roles(session)
        self.assertEqual(active, ("tech-lead",))

    def test_tech_lead_always_first(self) -> None:
        # Even when the persisted list omits tech-lead, the helper
        # puts it at index 0 so the synthesis runner has a closer.
        session = SimpleNamespace(
            extra={"active_research_roles": ["backend-engineer", "qa-engineer"]},
            role_sequence=(),
        )
        active = get_effective_active_roles(session)
        self.assertEqual(active[0], "tech-lead")
        self.assertIn("backend-engineer", active)
        self.assertIn("qa-engineer", active)

    def test_fallback_role_sequence_disabled_skips_step_2(self) -> None:
        session = SimpleNamespace(
            extra={},
            role_sequence=("tech-lead", "backend-engineer"),
        )
        active = get_effective_active_roles(
            session, fallback_role_sequence=False
        )
        self.assertEqual(active, ("tech-lead",))


# ---------------------------------------------------------------------------
# parse_role_change_request — Korean / English role-add / remove
# ---------------------------------------------------------------------------


class RoleChangeParserTests(unittest.TestCase):
    def test_qa_join_korean_phrase(self) -> None:
        change = parse_role_change_request("QA도 참여시켜줘")
        self.assertIsNotNone(change)
        assert change is not None
        self.assertEqual(change.action, "add")
        self.assertEqual(change.roles, ("qa-engineer",))

    def test_backend_call_korean_phrase(self) -> None:
        change = parse_role_change_request("백엔드도 불러줘")
        assert change is not None
        self.assertEqual(change.action, "add")
        self.assertEqual(change.roles, ("backend-engineer",))

    def test_frontend_join_with_together_phrase(self) -> None:
        change = parse_role_change_request("프론트도 같이 봐줘")
        assert change is not None
        self.assertEqual(change.action, "add")
        self.assertEqual(change.roles, ("frontend-engineer",))

    def test_all_team_request_marks_replace_all_team(self) -> None:
        change = parse_role_change_request("전체 팀 관점으로 봐줘")
        assert change is not None
        self.assertEqual(change.action, "replace_all_team")
        # Every engineering role is in the request payload.
        for role in ALL_ENGINEERING_ROLES:
            with self.subTest(role=role):
                self.assertIn(role, change.roles)

    def test_remove_korean_phrase(self) -> None:
        change = parse_role_change_request("디자이너는 빼줘")
        assert change is not None
        self.assertEqual(change.action, "remove")
        self.assertEqual(change.roles, ("product-designer",))

    def test_unrelated_message_returns_none(self) -> None:
        # Plain conversation — no routing intent → no change.
        self.assertIsNone(parse_role_change_request("이거 좀 보여줘"))
        self.assertIsNone(parse_role_change_request("응"))
        self.assertIsNone(parse_role_change_request(""))

    def test_english_add_phrase(self) -> None:
        change = parse_role_change_request("please add qa")
        assert change is not None
        self.assertEqual(change.action, "add")
        self.assertEqual(change.roles, ("qa-engineer",))


class ApplyRoleChangeTests(unittest.TestCase):
    def test_add_appends_role_after_tech_lead(self) -> None:
        outcome = apply_role_change(
            current_active=("tech-lead", "backend-engineer"),
            change=RoleChangeRequest(
                action="add", roles=("qa-engineer",), raw_text="QA도 참여시켜"
            ),
            requested_by="masterway",
            requested_at="2026-05-08T10:00:00+00:00",
        )
        self.assertEqual(
            outcome.new_active_roles,
            ("tech-lead", "backend-engineer", "qa-engineer"),
        )
        self.assertEqual(outcome.added_roles, ("qa-engineer",))
        # Audit captures the raw text + diff.
        self.assertEqual(outcome.audit["action"], "add")
        self.assertEqual(outcome.audit["roles_added"], ["qa-engineer"])
        self.assertEqual(outcome.audit["requested_by"], "masterway")

    def test_remove_drops_role_but_keeps_tech_lead(self) -> None:
        # Even if the user says "remove tech-lead", the helper
        # preserves it — tech-lead is the synthesis closer.
        outcome = apply_role_change(
            current_active=("tech-lead", "backend-engineer", "qa-engineer"),
            change=RoleChangeRequest(
                action="remove",
                roles=("tech-lead", "backend-engineer"),
                raw_text="tech-lead 도 backend 도 빼",
            ),
            requested_by="masterway",
        )
        # tech-lead survives, backend-engineer is gone.
        self.assertIn("tech-lead", outcome.new_active_roles)
        self.assertNotIn("backend-engineer", outcome.new_active_roles)
        self.assertIn("qa-engineer", outcome.new_active_roles)
        self.assertEqual(outcome.removed_roles, ("backend-engineer",))

    def test_replace_all_team_replaces_with_full_team(self) -> None:
        outcome = apply_role_change(
            current_active=("tech-lead", "backend-engineer"),
            change=RoleChangeRequest(
                action="replace_all_team",
                roles=ALL_ENGINEERING_ROLES,
                raw_text="전체 팀 관점으로 봐줘",
            ),
            requested_by="masterway",
        )
        # Every role is present.
        for role in ALL_ENGINEERING_ROLES:
            with self.subTest(role=role):
                self.assertIn(role, outcome.new_active_roles)


class RoleChangeAuditTests(unittest.TestCase):
    def test_append_role_change_audit_caps_history(self) -> None:
        # Pre-seed > cap entries so the next append must trim.
        primed = [{"action": "add", "i": i} for i in range(40)]
        out = append_role_change_audit(
            extra={"role_changes": primed},
            audit={"action": "add", "i": 999},
            cap=32,
        )
        self.assertEqual(len(out["role_changes"]), 32)
        # Latest entry is the just-appended one.
        self.assertEqual(out["role_changes"][-1]["i"], 999)


# ---------------------------------------------------------------------------
# format_routing_summary — tech-lead kickoff text
# ---------------------------------------------------------------------------


class RoutingSummaryTests(unittest.TestCase):
    def test_summary_lists_selected_and_excluded(self) -> None:
        sel = recommend_active_roles(
            user_prompt="DevOps 엔지니어가 되려면 어떻게 공부해야 할까"
        )
        text = format_routing_summary(sel, request_label="DevOps 학습 로드맵")
        self.assertIn("참여 역할", text)
        self.assertIn("devops-engineer", text)
        self.assertIn("대기 역할", text)
        # Frontend / designer / backend / qa / ai are dispatched as
        # "대기" so they don't speak.
        for role in (
            "frontend-engineer",
            "product-designer",
            "backend-engineer",
            "qa-engineer",
            "ai-engineer",
        ):
            with self.subTest(role=role):
                self.assertIn(role, text)
        # User-add hint included so the operator knows how to extend.
        self.assertIn("참여시켜", text)

    def test_all_team_summary_says_user_requested(self) -> None:
        sel = recommend_active_roles(
            user_prompt="전체 팀 관점에서 출시 전 리스크 리뷰"
        )
        text = format_routing_summary(sel, request_label="출시 리스크 리뷰")
        # The summary must surface "사용자가 명시" so the operator
        # knows fan-out wasn't system-driven.
        self.assertIn("전체 팀", text)
        self.assertIn("사용자", text)


if __name__ == "__main__":
    unittest.main()
