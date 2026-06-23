"""Packet → provider projection bridge (console).

When a Hephaistos forge plan selects tools (armory skills + weapons), the operator must see
WHERE each one attaches in the provider ecosystem and under WHAT condition. This bridge maps
each selected armory ``SkillSpec`` / ``WeaponSpec`` to a vendor-neutral ``ToolCandidate`` and
runs the deterministic projection engine (``forgekit_provider.projection``), then renders a
compact attach/connect/verify block onto ``/resolve``. Full per-tool detail is available via
``/attach <skill-id>``.

The armory catalog stays vendor-neutral (a skill never names a provider — ``capability_note``
carries the lens); the mapping here DERIVES the provider affinity, so SSoT and projection
remain separate. Honest: Ollama-bound capabilities render as a backend slot, not a target.
"""

from __future__ import annotations

from typing import Optional, Tuple

from forgekit_provider.projection import models as pm

# ── armory capability_note / category → vendor-neutral (taxonomy_kind, capability_class) ──
# Deterministic keyword routing. Ordered: first matching rule wins.
_NOTE_RULES = (
    ("figma", pm.KIND_MCP, pm.CAP_TOOL_USE),
    ("design reference", pm.KIND_MCP, pm.CAP_TOOL_USE),
    ("design consistency", pm.KIND_SKILL, pm.CAP_ANALYSIS),
    ("executor", pm.KIND_SKILL, pm.CAP_EXECUTION),
    ("reader", pm.KIND_SKILL, pm.CAP_RESEARCH),
    ("retrieval", pm.KIND_SKILL, pm.CAP_RESEARCH),
    ("long-context", pm.KIND_SKILL, pm.CAP_RESEARCH),
    ("security review", pm.KIND_SKILL, pm.CAP_VERIFICATION),
    ("secret hygiene", pm.KIND_SKILL, pm.CAP_SECURITY_GATE),
    ("auth", pm.KIND_SKILL, pm.CAP_VERIFICATION),
    ("evaluation", pm.KIND_SKILL, pm.CAP_VERIFICATION),
    ("ui layout", pm.KIND_SKILL, pm.CAP_ANALYSIS),
    ("ui component", pm.KIND_SKILL, pm.CAP_ANALYSIS),
    ("api client", pm.KIND_SKILL, pm.CAP_INTEGRATION),
)
# category fallback when no note keyword matches.
_CATEGORY_CAP = {
    "security": pm.CAP_VERIFICATION,
    "design-support": pm.CAP_RESEARCH,
    "ai": pm.CAP_RESEARCH,
}


def candidate_from_skill(spec) -> pm.ToolCandidate:
    """Map an armory SkillSpec to a vendor-neutral ToolCandidate (derived affinity)."""

    note = (spec.capability_note or "").lower()
    kind, cap = pm.KIND_SKILL, None
    for needle, k, c in _NOTE_RULES:
        if needle in note:
            kind, cap = k, c
            break
    if cap is None:
        cap = _CATEGORY_CAP.get(spec.category, pm.CAP_EXECUTION)
    return pm.ToolCandidate(id=spec.id, name=spec.name, taxonomy_kind=kind,
                            capability_class=cap, summary=spec.summary or spec.capability_note,
                            source="armory")


def candidate_from_weapon(spec) -> pm.ToolCandidate:
    """Map an armory WeaponSpec (a CLI/runtime/service tool) to a candidate.

    A weapon is an *executor-environment dependency*, not an LLM backend — it is wielded on
    the execution (Codex) plane and verified locally by its ``verify_command``. We tag it
    KIND_SKILL/EXECUTION so it projects to the executor plane rather than the Ollama slot.
    """

    return pm.ToolCandidate(id=spec.id, name=spec.display_name, taxonomy_kind=pm.KIND_SKILL,
                            capability_class=pm.CAP_EXECUTION,
                            summary=f"{spec.kind} weapon", verify_command=spec.verify_command,
                            source="armory")


def _skill_lookup(skill_id: str):
    from armory import catalog
    return catalog.skill(skill_id)


def _weapon_lookup(weapon_id: str):
    from armory import catalog
    return catalog.weapon(weapon_id)


def attach_detail_lines(tool_id: str) -> Tuple[str, ...]:
    """`/attach <id>` — full projection verdict for one selected armory skill or weapon."""

    from forgekit_provider_connect import attach as att

    spec = _skill_lookup(tool_id)
    if spec is not None:
        return att.attach_lines(candidate_from_skill(spec))
    wspec = _weapon_lookup(tool_id)
    if wspec is not None:
        return att.attach_lines(candidate_from_weapon(wspec))
    return (f"attach — '{tool_id}' 는 armory skill/weapon 이 아님. "
            "`/resolve <요청>` 의 skills/weapons 목록에서 id 를 확인하세요.",)


def _compact_line(verdict: pm.ProjectionVerdict) -> str:
    c = verdict.candidate
    if verdict.is_backend:
        where = f"backend:{verdict.backend_role}"
    elif verdict.is_neutral_runtime:
        where = "runtime(neutral)"
    else:
        where = f"→{verdict.primary_target}"
    primary_plan = verdict.plan_for(verdict.primary_target) if verdict.primary_target else (
        verdict.plans[0] if verdict.plans else None)
    verify = primary_plan.verify if primary_plan else "-"
    return f"  • {c.name} [{where}] verify: {verify}"


def packet_projection_lines(plan) -> Tuple[str, ...]:
    """Compact provider-projection block for the `/resolve` packet — attach/connect/verify
    per selected tool. Empty when the plan selected no tools (honest shallow)."""

    from forgekit_provider.projection.rules import project

    skills = tuple(plan.selected_skills or ())
    weapons = tuple(plan.required_weapons or ())
    if not skills and not weapons:
        return ()
    lines = ["", "── provider projection (selected tool → 생태계 attach) ──"]
    for sid in skills:
        spec = _skill_lookup(sid)
        if spec is None:
            continue
        lines.append(_compact_line(project(candidate_from_skill(spec))))
    for wid in weapons:
        spec = _weapon_lookup(wid)
        if spec is None:
            continue
        lines.append(_compact_line(project(candidate_from_weapon(spec))))
    lines.append("  [dim]상세 attach/connect/verify: `/provider attach <id>` · 규칙: claude/codex/gemini="
                 "projection · ollama=backend[/dim]")
    return tuple(lines)


__all__ = (
    "candidate_from_skill", "candidate_from_weapon",
    "attach_detail_lines", "packet_projection_lines",
)
