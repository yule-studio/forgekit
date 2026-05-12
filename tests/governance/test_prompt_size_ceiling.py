"""F14 — prompt / preamble / template size ceiling governance.

gstack 의 160KB 가드 차용. 다음 회귀를 막는다:
  - 한 정책 파일이 무한히 자라 preamble 이 토큰을 다 잡아먹는 경우
  - skill template `.tmpl` 이 부풀어 render 결과가 ceiling 초과
  - 전체 preamble 합산이 운영 한도 (320KB) 초과

운영 정책 (사용자 합의):
  - 한 정책 파일 단위: 80KB 권장 / 160KB hard ceiling
  - preamble 전체: 320KB hard ceiling (≈ 80K token, prompt cache 친화)
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.preamble import build_default_preamble


_REPO_ROOT = Path(__file__).resolve().parents[2]

_SINGLE_FILE_HARD_CEILING = 160 * 1024  # 160KB
_PREAMBLE_TOTAL_HARD_CEILING = 320 * 1024  # 320KB
_TEMPLATE_HARD_CEILING = 32 * 1024  # 32KB per .tmpl


class PolicyFileSizeTests(unittest.TestCase):
    """5 default policy 파일이 한도 안에 있는지 검사."""

    POLICIES = (
        "policies/runtime/agents/engineering-agent/issue-pr-conventions.md",
        "policies/runtime/agents/engineering-agent/governance.md",
        "policies/runtime/agents/engineering-agent/write-ownership.md",
        "policies/runtime/agents/engineering-agent/github-workflow.md",
        "policies/runtime/agents/engineering-agent/obsidian-governance.md",
    )

    def test_each_policy_file_under_hard_ceiling(self) -> None:
        for rel in self.POLICIES:
            p = _REPO_ROOT / rel
            if not p.is_file():
                continue
            size = p.stat().st_size
            self.assertLess(
                size, _SINGLE_FILE_HARD_CEILING,
                f"{rel} = {size} bytes > {_SINGLE_FILE_HARD_CEILING} hard ceiling",
            )


class PreambleTotalSizeTests(unittest.TestCase):
    def test_default_preamble_under_total_ceiling(self) -> None:
        p = build_default_preamble()
        self.assertLess(
            p.total_size_bytes, _PREAMBLE_TOTAL_HARD_CEILING,
            f"preamble {p.total_size_bytes} bytes > {_PREAMBLE_TOTAL_HARD_CEILING} ceiling",
        )

    def test_rendered_preamble_truncates_huge_section(self) -> None:
        # 한도 안에서 render — 의도적 절단이 작동하는지.
        p = build_default_preamble()
        rendered = p.render_markdown(max_section_chars=100)
        # 작은 max_section_chars → 모든 섹션이 절단 마커 포함
        # (5 default sources 각각 100자 초과)
        for section in p.sections:
            if section.size_bytes > 100:
                self.assertIn("truncated", rendered)
                return
        self.skipTest("all sections too small to test truncation")


class SkillTemplateSizeTests(unittest.TestCase):
    """`prompts/skills/*.tmpl` 의 크기 한도."""

    def test_all_skill_templates_under_ceiling(self) -> None:
        templates_dir = _REPO_ROOT / "prompts" / "skills"
        if not templates_dir.is_dir():
            self.skipTest("no skill templates dir")
        for tmpl in sorted(templates_dir.glob("*.md.tmpl")):
            size = tmpl.stat().st_size
            self.assertLess(
                size, _TEMPLATE_HARD_CEILING,
                f"{tmpl.relative_to(_REPO_ROOT)} = {size} bytes > {_TEMPLATE_HARD_CEILING}",
            )


if __name__ == "__main__":
    unittest.main()
