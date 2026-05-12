"""F14 skill template + codegen 회귀."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import gen_skill_docs  # noqa: E402


class CodegenTests(unittest.TestCase):
    def test_template_file_exists(self) -> None:
        self.assertTrue(
            (_REPO_ROOT / "prompts" / "skills" / "agent_spawn.md.tmpl").is_file()
        )

    def test_render_replaces_role(self) -> None:
        out = gen_skill_docs.render_agent_spawn(role="backend-engineer")
        self.assertIn("backend-engineer", out)
        # placeholder 가 모두 해석돼야 함
        self.assertNotIn("{{role}}", out)
        self.assertNotIn("{{preamble_summary}}", out)

    def test_render_includes_preamble_sections(self) -> None:
        out = gen_skill_docs.render_agent_spawn(role="tech-lead")
        # preamble cache 가 5 default sources 의 fingerprint 매트릭스 inject
        self.assertIn("preamble cache:", out)

    def test_render_under_token_ceiling_by_default(self) -> None:
        # gstack 의 160KB 가드 — 정상 사용 시 한도 안.
        out = gen_skill_docs.render_agent_spawn(role="backend-engineer")
        self.assertFalse(
            gen_skill_docs.check_ceiling(out),
            f"rendered {len(out.encode('utf-8'))} bytes > ceiling",
        )

    def test_check_ceiling_flags_oversized(self) -> None:
        oversized = "x" * (gen_skill_docs.TOKEN_CEILING_BYTES + 1)
        self.assertTrue(gen_skill_docs.check_ceiling(oversized))

    def test_unknown_template_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            gen_skill_docs._load_template("does_not_exist")


if __name__ == "__main__":
    unittest.main()
