#!/usr/bin/env python3
"""F14 skill prompt codegen — gstack `gen-skill-docs.ts` 패턴.

`skills/*.md.tmpl` + PreambleCache → 최종 합성된 prompt markdown.
CI / manual run 으로 prompt drift 감지 가능.

사용:
    python3 scripts/gen_skill_docs.py --role backend-engineer
    python3 scripts/gen_skill_docs.py --role tech-lead --output -
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Mapping, Optional

# repo root 경로 — scripts/ 의 부모
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "engineering-agent" / "src"))

from yule_engineering.agents.preamble import PreambleBuilder  # noqa: E402


SKILL_TEMPLATES_DIR = _REPO_ROOT / "skills"
SKILL_OUTPUT_DIR = _REPO_ROOT / "skills" / "generated"

# 토큰 ceiling — gstack 의 160KB 가드 차용. agent prompt 가 이보다 크면 경고.
TOKEN_CEILING_BYTES = 160 * 1024  # 160KB


def _load_template(name: str) -> str:
    path = SKILL_TEMPLATES_DIR / f"{name}.md.tmpl"
    if not path.is_file():
        raise FileNotFoundError(f"template not found: {path}")
    return path.read_text(encoding="utf-8")


def _render(template: str, replacements: Mapping[str, str]) -> str:
    """{{key}} → replacements[key]. 누락 시 빈 문자열."""

    out = template
    for key, value in replacements.items():
        out = out.replace("{{" + key + "}}", value)
    # 미해석 placeholder 비우기
    import re
    out = re.sub(r"\{\{[^}]+\}\}", "", out)
    return out


def _preamble_summary(builder: PreambleBuilder, *, max_chars: int = 3000) -> str:
    """preamble 의 1-page 요약. 토큰 절약 목적."""

    p = builder.build()
    lines: list = [
        f"_(preamble cache: {len(p.sections)} sections, "
        f"{p.total_size_bytes} bytes, built {p.built_at_iso})_",
        "",
    ]
    for s in p.sections:
        lines.append(f"- **{s.title}** (`{s.path}`, fp={s.short_fingerprint()})")
    summary = "\n".join(lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "\n... (truncated)"
    return summary


def render_agent_spawn(
    *,
    role: str,
    task_brief: str = "(provide a concrete task description)",
    repo_root: Optional[Path] = None,
) -> str:
    """`agent_spawn.md.tmpl` 에 role + preamble 합성."""

    builder = PreambleBuilder(repo_root=repo_root or _REPO_ROOT)
    template = _load_template("agent_spawn")
    return _render(template, {
        "role": role,
        "preamble_summary": _preamble_summary(builder),
        "task_brief": task_brief,
    })


def check_ceiling(rendered: str, *, ceiling: int = TOKEN_CEILING_BYTES) -> bool:
    """gstack 스타일 ceiling 가드. True → 한도 초과 (caller 경고)."""

    return len(rendered.encode("utf-8")) > ceiling


def _cli() -> int:
    parser = argparse.ArgumentParser(prog="gen_skill_docs")
    parser.add_argument("--role", required=True)
    parser.add_argument("--task", default="(probe)")
    parser.add_argument(
        "--output", default="-",
        help="`-` for stdout, otherwise file path",
    )
    args = parser.parse_args()

    rendered = render_agent_spawn(role=args.role, task_brief=args.task)
    if check_ceiling(rendered):
        sys.stderr.write(
            f"⚠️  rendered prompt {len(rendered.encode('utf-8'))} bytes > "
            f"{TOKEN_CEILING_BYTES} ceiling. 토큰 누수 점검.\n"
        )

    if args.output == "-":
        sys.stdout.write(rendered)
        return 0

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    sys.stderr.write(f"wrote {len(rendered)} chars to {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
