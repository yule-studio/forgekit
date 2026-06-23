"""Operator projection (Hephaistos PR2) — core results → console-friendly lines. Pure.

A thin projection layer: it reads resolver / verifier / nexus_read results and renders
summary-first, honest operator lines. No core logic, no UI framework — just strings the
router/console surfaces. fake-live wording is impossible here: it reflects whatever the
core returned (not_connected / missing / restricted / shallow shown as-is).
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple

from . import armory, nexus_read as nx, resolver, verifier
from .models import SRC_RESTRICTED, ResolvedForgePlan


def resolve_with_sources(request: str, *, env: Optional[Mapping[str, str]] = None,
                         config: Optional[Mapping] = None, role: str = ""):
    """Resolve + read the plan's Nexus refs (honest status). Returns (plan, read_result)."""

    plan = resolver.resolve(request)
    read = nx.read_plan_sources(plan, env=env, config=config, role=role)
    return plan, read


def nexus_status_lines(read: nx.NexusReadResult) -> Tuple[str, ...]:
    if read.not_connected:
        return ("  nexus     : not_connected (FORGEKIT_NEXUS_ROOT 미설정 — 지식 source 미연결)",)
    return (f"  nexus     : connected · read {len(read.resolved_docs)} / "
            f"missing {len(read.missing_refs)} / blocked {len(read.blocked_refs)} / "
            f"restricted {len(read.restricted_refs)}",)


def resolve_summary_lines(plan: ResolvedForgePlan, read: nx.NexusReadResult) -> Tuple[str, ...]:
    """`/resolve` — summary-first equip plan + honest source/shallow state."""

    shallow = "" if plan.selected_skills else "  ⚠ shallow — 이 스택의 armory skill 미존재(정직)"
    lines = [
        f"hephaistos resolve — {plan.request[:60]}",
        f"  infer     : {plan.domain or '-'}/{plan.language or '-'}/{plan.framework or '-'}/{plan.topic or '-'}",
        f"  agent     : {plan.selected_agent or '(미정)'}",
        f"  skills    : {', '.join(plan.selected_skills) or '(없음)'}",
        f"  loadout   : {plan.selected_loadout or '-'}",
        f"  weapons   : {', '.join(plan.required_weapons) or '-'}",
    ]
    if plan.excluded_skills:
        lines.append(f"  excluded  : {', '.join(plan.excluded_skills)}  (프로젝트 제약으로 제외)")
    lines += list(nexus_status_lines(read))
    if plan.packet_draft:
        pk = plan.packet_draft
        lines.append(f"  packet    : scope {len(pk.scope)} / forbidden {len(pk.forbidden_scope)} / "
                     f"verify {len(pk.verification)} / approval {pk.approval_level}")
        if pk.selected_tools:
            lines.append(f"  tools     : {', '.join(pk.selected_tools)}")
        if pk.constraints:
            lines.append(f"  constraints: {', '.join(pk.constraints)}")
        if pk.harness:
            lines.append(f"  harness   : {pk.harness}")
    if shallow:
        lines.append(shallow)
    lines.append("  [dim]상세: /skills <요청> · /loadout <id> · /hephaistos · 선택근거: 아래 evidence[/dim]")
    lines += list(selection_evidence_lines(plan, limit=8))
    return tuple(lines)


def selection_evidence_lines(plan: ResolvedForgePlan, *, limit: int = 0) -> Tuple[str, ...]:
    """Project the anti-fake selection trail — WHY each pick/exclusion happened. Read-only.

    Reflects whatever the resolver recorded; no choice is invented here. A plan with no
    evidence renders an honest '(근거 없음)' rather than a fabricated rationale.
    """

    ev = plan.selection_evidence
    if not ev:
        return ("  evidence  : (근거 없음 — shallow/미매칭)",)
    rows = ev if not limit else ev[:limit]
    lines = ["  evidence  : 선택/제외 근거 (no fake smart-selection)"]
    mark = {"selected": "✓", "excluded": "✗"}
    for e in rows:
        lines.append(f"    {mark.get(e.decision, '·')} [{e.kind}] {e.target} — {e.reason or '-'}")
    if limit and len(ev) > limit:
        lines.append(f"    … +{len(ev) - limit} more")
    return tuple(lines)


def skills_lines(plan: ResolvedForgePlan, read: nx.NexusReadResult) -> Tuple[str, ...]:
    if not plan.selected_skills:
        return (f"hephaistos skills — '{plan.request[:40]}'",
                "  (선택된 skill 없음 — armory 가 이 스택을 아직 커버 안 함, 정직 shallow)")
    lines = [f"hephaistos skills — '{plan.request[:40]}'"]
    for sid in plan.selected_skills:
        sk = armory.skill(sid)
        if not sk:
            continue
        why = []
        if plan.domain in sk.domains:
            why.append(f"domain={plan.domain}")
        if plan.topic in sk.topics:
            why.append(f"topic={plan.topic}")
        if plan.framework in sk.frameworks:
            why.append(f"fw={plan.framework}")
        unsafe = f" · unsafe: {sk.forbidden[0]}" if sk.forbidden else ""
        lines.append(f"  • {sk.name} [{', '.join(sk.domains) or '-'}] — 선택이유: "
                     f"{', '.join(why) or 'related'}{unsafe}")
    lines += list(nexus_status_lines(read))
    return tuple(lines)


def execution_lines(ep) -> Tuple[str, ...]:
    """Project a Hephaistos ExecutionPlan — equip(adopted vs equipped) / nexus / ponytail /
    verification / expected outputs / runtime-approval. Read-only: reflects the core result
    as-is (honest equip gaps, honest not_connected, ponytail verdict shown, never invented)."""

    p = ep.plan
    eq = ep.equip
    lines = [f"hephaistos execute — {ep.request[:60]}",
             f"  skills   : {', '.join(p.selected_skills) or '(shallow — armory 미커버)'}",
             f"  loadout  : {p.selected_loadout or '(없음 — skill-only)'}",
             f"  equip    : {eq.readiness} · adopted {len(eq.adopted_skills)} / "
             f"equipped [{', '.join(eq.equipped_tools) or '-'}] / "
             f"not_equipped [{', '.join(eq.not_equipped) or '-'}]"]
    if eq.not_equipped:
        lines.append("    ⚠ adopted≠equipped — 실행 전 설치 필요: " + "; ".join(eq.install_steps or eq.not_equipped))
    if p.rejected_candidates:
        lines.append("  rejected : " + ", ".join(f"{r.target}({r.category})" for r in p.rejected_candidates))
    lines.append(f"  nexus    : {'connected' if ep.nexus_connected else 'not_connected'} · "
                 f"project facts {len(ep.nexus_facts)}")
    for f in ep.nexus_facts[:4]:
        lines.append(f"    • {f}")
    if ep.expected_outputs:
        lines.append("  outputs  : " + " | ".join(ep.expected_outputs[:3]))
    if ep.verification_plan:
        lines.append("  verify   : " + " → ".join(ep.verification_plan[:4]))
    if ep.ponytail:
        lines.append(f"  ponytail : {ep.ponytail.verdict} — {ep.ponytail.reason[:80]}")
        if ep.ponytail.needs_escalation:
            lines.append("    → ponytail 은 lens(승인자 아님): tech-lead review / cross-role consult 필요")
    for impl in ep.runtime_approval:
        lines.append(f"  runtime  : {impl}")
    return tuple(lines)


def loadout_lines(loadout_id: str, *, which=None) -> Tuple[str, ...]:
    if not loadout_id:
        return ("hephaistos loadout — 선택된 loadout 없음 (`/resolve <요청>` 먼저, 또는 `/loadout <id>`)",)
    r = verifier.verify_loadout(loadout_id, which=which)
    return verifier.readiness_lines(r)


def hephaistos_status_lines(*, env: Optional[Mapping[str, str]] = None,
                            config: Optional[Mapping] = None) -> Tuple[str, ...]:
    """`/hephaistos` — identity + armory availability + nexus connection + next actions."""

    root = nx.nexus_root(env, config)
    nexus = "connected" if root else "not_connected (FORGEKIT_NEXUS_ROOT 미설정)"
    return (
        "Hephaistos — ForgeKit 의 skill-forging core (요청→skill/loadout/weapon/work packet).",
        f"  armory    : skills {len(armory.all_skills())} · loadouts {len(armory.all_loadouts())} "
        f"· weapons {len(armory.all_weapons())}  (현재 backend-java 중심 MVP)",
        f"  nexus     : {nexus} — 미연결이면 source 는 not_connected 로 정직 표면(fake-read 없음)",
        "  resolver  : rule-first·deterministic (요청→equip plan)",
        "  loadout   : verify 가능(실 env which 기반)",
        "  다음       : /resolve <요청> · /skills <요청> · /loadout <id>",
        "  [dim]Nexus source 상세 상태는 /resolve·/skills 출력의 nexus 줄 참고 (/sources 는 discovery 전용)[/dim]",
    )


def nexus_surface_lines(*, env: Optional[Mapping[str, str]] = None,
                        config: Optional[Mapping] = None) -> Tuple[str, ...]:
    """`/nexus` — Nexus live connection status (connected / not_connected / missing / blocked)."""

    cs = nx.connection_status(env, config)
    lines = [f"nexus: [{cs['status']}] {cs['reason']}"]
    if cs["connected"]:
        lines += [f"  root : {cs['root']} (connected · live read 가능)",
                  "  restricted source 는 design/privacy role 만 raw, 그외 projection_only.",
                  "  `/resolve <요청>` 의 nexus 줄에서 ref 별 read 결과 확인."]
    else:
        lines += [f"  root : {cs['root'] or '(미설정)'}",
                  f"  연결: export {nx.ENV_NEXUS_ROOT}=<nexus repo 경로>  또는 config 의 nexus_root 설정.",
                  "  미연결 동안 source 는 not_connected 로 정직 표면(fake-read 없음)."]
    return tuple(lines)


__all__ = (
    "resolve_with_sources", "nexus_status_lines", "resolve_summary_lines",
    "selection_evidence_lines", "execution_lines",
    "skills_lines", "loadout_lines", "hephaistos_status_lines", "nexus_surface_lines",
)
