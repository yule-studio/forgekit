"""Lint-style smoke test — 문서 계층 / 읽기 우선순위 / 파일 분리 규칙.

본 test 는 다음 정책의 *contract* 자체를 강제한다. 통과 = 문서 계층이
살아있다는 뜻.

검사 대상:
  * `AGENTS.md`              — 진입점 / 작업 맥락 → 문서 매핑 표
  * `CLAUDE.md`              — 전역 규칙 / 코딩 컨벤션 / 1000줄 분리 규칙
  * `agents/engineering-agent/CLAUDE.md`     — 작업 맥락별 읽기 가이드
  * `agents/engineering-agent/CODE_LAYOUT.md` — 700/1000줄 분리 + 책임 신호

핵심 회귀 라인이 silently 사라지면 fail. 같은 규칙이 여러 문서에
중복돼 한쪽만 갱신되는 사고를 가장 먼저 잡아낸다.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    path = _REPO_ROOT / rel
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


class AgentsEntryPointTests(unittest.TestCase):
    """`AGENTS.md` 가 진입점 + 문서 매핑 표 + 외부 에이전트 안내를 가짐."""

    def setUp(self) -> None:
        self.text = _read("AGENTS.md")

    def test_navigation_section_present(self) -> None:
        for needle in (
            "문서 내비게이션",
            "문서 계층",
            "읽기 우선순위",
        ):
            self.assertIn(needle, self.text, needle)

    def test_does_not_assume_all_docs_read(self) -> None:
        # 핵심 가정 — 매번 모든 md 를 읽는다고 가정하지 않는다.
        self.assertIn("매번 모든 md 를 읽는다고 가정하지 않는다", self.text)

    def test_priority_table_links_root_claude_md(self) -> None:
        # 항상 읽는 두 문서 명시
        self.assertIn("`AGENTS.md`", self.text)
        self.assertIn("CLAUDE.md", self.text)

    def test_codex_role_section_present(self) -> None:
        # 기존 Codex 역할 안내가 합쳐졌는지 확인 (안전 가드)
        for needle in (
            "advisor",
            "patch proposer",
            ".codex/",
        ):
            self.assertIn(needle, self.text, needle)

    def test_cross_links_to_engineering_agent(self) -> None:
        # engineering-agent 도메인 진입 경로
        self.assertIn("agents/engineering-agent/CLAUDE.md", self.text)
        self.assertIn("agents/engineering-agent/CODE_LAYOUT.md", self.text)

    def test_synchronization_note_present(self) -> None:
        # 새 규칙 추가 시 동기화 안내
        self.assertIn("동기화", self.text)


class RootClaudeMdTests(unittest.TestCase):
    """root `CLAUDE.md` 가 전역 규칙 + 코딩 컨벤션 + 1000 줄 규칙을 가짐."""

    def setUp(self) -> None:
        self.text = _read("CLAUDE.md")

    def test_purpose_and_platform_direction_kept(self) -> None:
        # 기존 안전 룰이 회귀 없이 살아있는지
        self.assertIn("Purpose", self.text)
        self.assertIn("Platform Direction", self.text)
        self.assertIn("Core Safety Rules", self.text)
        self.assertIn("secret", self.text)
        self.assertIn("파괴적 명령", self.text)

    def test_reading_priority_summary_present(self) -> None:
        self.assertIn("읽기 우선순위", self.text)

    def test_coding_convention_summary_present(self) -> None:
        self.assertIn("전역 코딩 컨벤션", self.text)
        self.assertIn("파일 크기", self.text)

    def test_file_size_thresholds_present(self) -> None:
        # 700 줄 warning + 1000 줄 split 두 임계값 모두 명시
        self.assertIn("700", self.text)
        self.assertIn("1000", self.text)

    def test_responsibility_split_signal_present(self) -> None:
        self.assertIn("책임 분리 신호", self.text)

    def test_router_thin_principle_present(self) -> None:
        self.assertIn("router", self.text)
        self.assertIn("얇은 orchestration", self.text)

    def test_commit_pr_branch_links(self) -> None:
        # 도메인 한정 규칙은 cross-link
        self.assertIn("policies/reference/COMMIT_CONVENTION.md", self.text)
        self.assertIn("policies/reference/BRANCH_STRATEGY.md", self.text)

    def test_operator_action_inbox_present(self) -> None:
        self.assertIn("Operator Action Inbox", self.text)
        self.assertIn("approval-matrix", self.text)


class EngineeringAgentClaudeMdTests(unittest.TestCase):
    """`agents/engineering-agent/CLAUDE.md` 가 작업 맥락별 읽기 가이드를 가짐."""

    def setUp(self) -> None:
        self.text = _read("agents/engineering-agent/CLAUDE.md")

    def test_entry_point_reference_present(self) -> None:
        # AGENTS.md / root CLAUDE.md 로 올라가는 cross-link
        self.assertIn("AGENTS.md", self.text)
        self.assertIn("/CLAUDE.md", self.text)

    def test_context_reading_guide_section_present(self) -> None:
        self.assertIn("작업 맥락별 읽기 가이드", self.text)

    def test_code_layout_link_present(self) -> None:
        self.assertIn("CODE_LAYOUT.md", self.text)

    def test_priority_chain_documented(self) -> None:
        # 같은 규칙 충돌 시 우선순위가 명시돼있어야 silent regression 방지
        self.assertIn("`/CLAUDE.md` ≻ 본 파일 ≻ `CODE_LAYOUT.md`", self.text)

    def test_engineering_specific_rules_present(self) -> None:
        # router 얇게 / lifecycle ownership / 700-1000 신호
        for needle in (
            "router 는 얇게",
            "lifecycle stage 마다 ownership",
            "회귀 테스트 우선",
        ):
            self.assertIn(needle, self.text, needle)


class CodeLayoutMdTests(unittest.TestCase):
    """`agents/engineering-agent/CODE_LAYOUT.md` 가 분리 규칙 + 예외 정책 보유."""

    def setUp(self) -> None:
        self.text = _read("agents/engineering-agent/CODE_LAYOUT.md")

    def test_legacy_lifecycle_table_kept(self) -> None:
        # 기존 lifecycle stage 표가 회귀 없이 살아있는지
        self.assertIn("Lifecycle stages → 책임 모듈", self.text)
        self.assertIn("intake / triage", self.text)

    def test_size_section_present(self) -> None:
        self.assertIn("파일 크기 / 책임 분리 규칙", self.text)

    def test_threshold_table_present(self) -> None:
        # 700 / 1000 두 등급 + default split 명시
        self.assertIn("700", self.text)
        self.assertIn("1000", self.text)
        self.assertIn("default split", self.text)
        self.assertIn("분리 필수", self.text)

    def test_responsibility_signals_present(self) -> None:
        for needle in (
            "intake",
            "intent classification",
            "routing",
            "state persistence",
            "phrase patch",
        ):
            self.assertIn(needle, self.text, needle)

    def test_exception_policy_present(self) -> None:
        self.assertIn("예외 정책", self.text)
        self.assertIn("generated file", self.text)
        self.assertIn("_legacy.py", self.text)

    def test_current_exception_table_present(self) -> None:
        # in-flight 모놀리스 명시 — 분리 진행 중인 파일이 silently 더 자라
        # 면 표를 갱신해야 한다는 신호
        self.assertIn("현재 예외", self.text)
        self.assertIn("_legacy.py", self.text)

    def test_governance_smoke_test_link_present(self) -> None:
        self.assertIn("test_prompt_size_ceiling.py", self.text)
        self.assertIn("test_engineering_agent_governance_doc.py", self.text)


class CrossDocumentConsistencyTests(unittest.TestCase):
    """문서 간 핵심 라인이 어긋나지 않는지 lint-style 검사."""

    def test_root_and_engineering_agent_agree_on_size_threshold(self) -> None:
        root = _read("CLAUDE.md")
        layout = _read("agents/engineering-agent/CODE_LAYOUT.md")
        # 두 문서 모두 700 / 1000 두 임계값을 가짐 (한 쪽만 바뀌면 어긋남)
        for needle in ("700", "1000"):
            self.assertIn(needle, root, f"root CLAUDE missing {needle}")
            self.assertIn(needle, layout, f"CODE_LAYOUT missing {needle}")

    def test_agents_md_lists_engineering_agent_claude(self) -> None:
        agents = _read("AGENTS.md")
        self.assertIn("agents/engineering-agent/CLAUDE.md", agents)

    def test_agents_md_lists_engineering_agent_code_layout(self) -> None:
        agents = _read("AGENTS.md")
        self.assertIn("agents/engineering-agent/CODE_LAYOUT.md", agents)

    def test_engineering_claude_lists_external_doc_set(self) -> None:
        ec = _read("agents/engineering-agent/CLAUDE.md")
        # 외부 cross-link 핵심 5종이 작업 맥락 매핑에 모두 등장
        for needle in (
            "approval-matrix.md",
            "autonomy-policy.md",
            "operations.md",
            "engineering-agent-governance.md",
            "testing.md",
        ):
            self.assertIn(needle, ec, f"engineering CLAUDE missing {needle}")


if __name__ == "__main__":
    unittest.main()
