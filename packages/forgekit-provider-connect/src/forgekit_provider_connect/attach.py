"""Provider attach/connect surface — render a projection verdict for the operator.

Sits on top of ``forgekit_provider.projection`` (the deterministic rules engine). Turns a
``ToolCandidate`` into honest console lines that answer the operator's question: *"이 도구는
claude/codex/gemini 중 어디에 붙고(projection), 무엇을 연결해야 하며(connect), 어떻게
검증하나(verify)? 아니면 그냥 ollama backend 인가?"*

Honest by construction: Ollama is shown as a **backend slot** (never a projection target),
and a (kind, target) pair with no generated connector is flagged ``manual`` instead of
green-washed. fake-live wording is impossible — the lines reflect the verdict's conditions.
"""

from __future__ import annotations

from typing import Tuple

from forgekit_provider.projection import models as pm
from forgekit_provider.projection.rules import project

# the legend shown on /setup so the operator reads the projection-vs-backend split once.
PROJECTION_LEGEND = (
    "도구 투영 규칙: Claude/Codex/Gemini = projection 대상(native hook/skill/command/MCP), "
    "Ollama = backend slot(local inference — plugin host 아님). `/provider attach <도구>` 로 확인."
)


def _target_lines(verdict: pm.ProjectionVerdict) -> Tuple[str, ...]:
    lines = []
    for plan in verdict.plans:
        is_primary = plan.target == verdict.primary_target
        mark = "★" if is_primary else ("◇" if plan.target in pm.PROJECTION_TARGETS else "▣")
        kind_word = "backend" if plan.target == pm.BACKEND_OLLAMA else "projection"
        conn = "" if plan.has_connector else " [dim](connector 미생성 — manual)[/dim]"
        lines.append(f"  {mark} {plan.target:<7} ({kind_word}){conn}")
        lines.append(f"      attach : {plan.attach}")
        lines.append(f"      connect: {plan.connect}")
        lines.append(f"      verify : {plan.verify}")
    return tuple(lines)


def attach_lines(candidate: pm.ToolCandidate) -> Tuple[str, ...]:
    """`/attach <도구>` — project one candidate onto its provider ecosystem(s)."""

    verdict = project(candidate)
    head = (f"attach — [b]{candidate.name}[/b]  "
            f"(kind={candidate.taxonomy_kind} · capability={candidate.capability_class})")
    if verdict.is_backend:
        verdict_word = f"backend slot → [b]{verdict.backend_role}[/b] (projection 대상 아님)"
    elif verdict.is_neutral_runtime:
        verdict_word = ("vendor-neutral runtime plugin (단일 primary 없음 — runtime 실행, "
                        f"투영 후보: {', '.join(verdict.projection_targets) or '-'})")
    else:
        verdict_word = (f"primary projection → [b]{verdict.primary_target}[/b]  ·  "
                        f"targets: {', '.join(verdict.projection_targets)}")
    lines = [head, f"  verdict : {verdict_word}", f"  근거    : {verdict.rationale}"]
    lines += list(_target_lines(verdict))
    return tuple(lines)


def project_candidate(*, id: str, name: str, kind: str = pm.KIND_SKILL,
                      capability: str = pm.CAP_EXECUTION, summary: str = "",
                      verify_command: str = "", source: str = "") -> pm.ProjectionVerdict:
    """Thin constructor entry — callers pass neutral fields, get a verdict (no model import)."""

    return project(pm.ToolCandidate(id=id, name=name, taxonomy_kind=kind,
                                    capability_class=capability, summary=summary,
                                    verify_command=verify_command, source=source))


__all__ = ("PROJECTION_LEGEND", "attach_lines", "project_candidate")
