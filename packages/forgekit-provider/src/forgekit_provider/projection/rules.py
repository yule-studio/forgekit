"""Projection target rules — deterministic ``ToolCandidate`` → ``ProjectionVerdict``.

The rule table mirrors ``docs/provider-capability-matrix.md`` §2/§3: each capability class
has a primary projection target and optional secondary targets (a capability has ONE SSoT
and N projections). The taxonomy kind then *constrains* the targets (a hook can't reach
Gemini; an MCP server can't run on Ollama; a backend never projects). Backend capabilities
route to the Ollama slot and produce NO projection targets — the two stay separate.

No fake connector: the per-target plan reports ``has_connector=False`` honestly when ForgeKit
has no generated attach path for that (kind, target) pair yet.
"""

from __future__ import annotations

from typing import Dict, Tuple

from . import models as m
from .models import (
    BACKEND_OLLAMA,
    ProjectionVerdict,
    TargetPlan,
    ToolCandidate,
    TARGET_CLAUDE,
    TARGET_CODEX,
    TARGET_GEMINI,
)

# capability class → (primary, secondary targets). Backend classes are handled separately.
_CAP_TARGETS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    m.CAP_SECURITY_GATE: (TARGET_CLAUDE, (TARGET_CODEX, TARGET_GEMINI)),
    m.CAP_ENFORCEMENT:   (TARGET_CLAUDE, ()),
    m.CAP_VERIFICATION:  (TARGET_CLAUDE, (TARGET_CODEX,)),
    m.CAP_COMPACTION:    (TARGET_CLAUDE, (TARGET_CODEX, TARGET_GEMINI)),
    m.CAP_MEMORY:        (TARGET_CLAUDE, ()),
    m.CAP_EXPLORATION:   (TARGET_CLAUDE, (TARGET_CODEX,)),
    m.CAP_DELIVERY:      (TARGET_CLAUDE, (TARGET_CODEX,)),
    m.CAP_EXECUTION:     (TARGET_CODEX, (TARGET_CLAUDE,)),
    m.CAP_TOOL_USE:      (TARGET_CODEX, (TARGET_GEMINI,)),
    m.CAP_INTEGRATION:   (TARGET_CODEX, (TARGET_CLAUDE,)),
    m.CAP_RESEARCH:      (TARGET_GEMINI, (TARGET_CLAUDE,)),
    m.CAP_ANALYSIS:      (TARGET_GEMINI, ()),
}

# MCP-capable harnesses (Ollama excluded — it is a backend, not an MCP host).
_MCP_TARGETS = (TARGET_CLAUDE, TARGET_CODEX, TARGET_GEMINI)
# Hooks express natively on Claude (primary) / Codex; Gemini has no pre/post tool hook plane.
_HOOK_TARGETS = (TARGET_CLAUDE, TARGET_CODEX)


# ── per-target attach / connect / verify condition builders ─────────────────────────────

def _connect(target: str) -> str:
    if target == TARGET_GEMINI:
        return "Gemini API key 연결 (live lane) — `/provider connect gemini`"
    if target in (TARGET_CLAUDE, TARGET_CODEX):
        return (f"{target} CLI 인증 (routing/brain participant — console live-submit 미지원, "
                f"native harness 로만 실행)")
    if target == BACKEND_OLLAMA:
        return "ollama 데몬 reachable + 모델 pull (live backend slot) — `/provider test ollama`"
    return "(연결 경로 미정)"


def _attach(kind: str, target: str, cid: str) -> Tuple[str, bool]:
    """Return (attach instruction, has_connector). has_connector=False → honest 'no generated path'."""

    if kind == m.KIND_MCP:
        layout = {
            TARGET_CLAUDE: ".mcp.json",
            TARGET_CODEX: ".codex-plugin/mcp.toml",
            TARGET_GEMINI: ".gemini-plugin/mcp.json",
        }[target]
        return (f"integrations/mcp/{cid}.json SSoT → {layout} (scripts/sync_mcp_projection.py, "
                f"env 참조만·secret 값 금지)", True)
    if kind == m.KIND_SKILL:
        layout = {
            TARGET_CLAUDE: ".claude/skills/<id>/SKILL.md + .claude-plugin/",
            TARGET_CODEX: ".agents/skills/<id>/SKILL.md + .codex-plugin/",
            TARGET_GEMINI: ".gemini/commands/<id>.toml + .gemini-plugin/",
        }[target]
        return (f"grant harness 에 '{target}' 추가 → {layout} (scripts/sync_harness_skills.py 단방향 생성)",
                True)
    if kind == m.KIND_HOOK:
        if target == TARGET_CLAUDE:
            return ("Claude Code pre/post tool hook 로 native 매핑 (runtime plugin hooks_provided)", True)
        if target == TARGET_CODEX:
            return ("Codex hook 평면으로 투영 (현재 생성기 미배선 — runtime plugin 으로 중립 실행 권장)", False)
    if kind == m.KIND_HARNESS_PROJECTION:
        return (f"{target} 번들 생성물 (손 편집 금지 — SSoT 에서 재생성)", True)
    if kind == m.KIND_RUNTIME_PLUGIN:
        return (f"vendor-neutral runtime plugin — {target} native hook 매핑은 선택 (기본은 runtime 실행)", False)
    # KIND_BACKEND or unknown
    return (f"{target} 로 직접 연계 (생성 connector 없음 — 수동)", False)


def _verify(kind: str, target: str, candidate: ToolCandidate) -> str:
    if candidate.verify_command:
        return f"presence: `{candidate.verify_command}`"
    if kind == m.KIND_MCP:
        return "agents/harness/mcp_registry.validate_mcp_server + server transport reachable"
    if kind == m.KIND_SKILL:
        return ("harness projection drift guard (tests/agents/test_harness_projection.py) + "
                "`/provider` 로 declared→actual 확인")
    if kind == m.KIND_HOOK:
        return "runtime plugin hook 발화 회귀 + lifecycle 진입점 확인"
    if target == TARGET_GEMINI:
        return "`/setup` 에서 gemini live 검증 (probe verified)"
    return "`/provider test <id>` 로 연결 검증"


def _plan(kind: str, target: str, candidate: ToolCandidate) -> TargetPlan:
    attach, has = _attach(kind, target, candidate.id)
    return TargetPlan(target=target, attach=attach, connect=_connect(target),
                      verify=_verify(kind, target, candidate), has_connector=has)


def _backend_plan(candidate: ToolCandidate) -> TargetPlan:
    return TargetPlan(
        target=BACKEND_OLLAMA,
        attach="agents/<agent>/manifest.json participants 에 ollama + runner; 모델 pull (plugin 아님)",
        connect=_connect(BACKEND_OLLAMA),
        verify=_verify(m.KIND_BACKEND, BACKEND_OLLAMA, candidate),
        has_connector=True,
    )


def project(candidate: ToolCandidate) -> ProjectionVerdict:
    """Route a candidate to its provider ecosystem(s). Deterministic, pure."""

    kind = candidate.taxonomy_kind
    cap = candidate.capability_class

    # 1) backend slot — a local-inference capability OR an explicit backend kind. Ollama is
    #    NEVER a projection target; projection_targets stays empty.
    if kind == m.KIND_BACKEND or cap in m.BACKEND_CAPABILITIES:
        role = (f"local-inference backend (Ollama slot) — capability '{cap}' 는 분류/요약/압축/"
                f"fallback 계열이라 plugin 이 아니라 backend engine 에 배치")
        return ProjectionVerdict(
            candidate=candidate, primary_target="", projection_targets=(),
            backend_role=BACKEND_OLLAMA, plans=(_backend_plan(candidate),), rationale=role)

    # 2) MCP — projects to all MCP-capable harnesses (never Ollama).
    if kind == m.KIND_MCP:
        targets = _MCP_TARGETS
        plans = tuple(_plan(kind, t, candidate) for t in targets)
        return ProjectionVerdict(
            candidate=candidate, primary_target=TARGET_CODEX, projection_targets=targets,
            backend_role="", plans=plans,
            rationale="MCP server — backend 가 붙는 외부 도구 채널, MCP-capable harness(claude/codex/"
                      "gemini)로 투영, Ollama 는 MCP host 아님(제외)")

    # 3) capability-routed projection, then constrained by the taxonomy kind.
    primary, secondary = _CAP_TARGETS.get(cap, (TARGET_CODEX, ()))
    targets = (primary, *secondary)

    if kind == m.KIND_HOOK:
        targets = tuple(t for t in targets if t in _HOOK_TARGETS) or (TARGET_CLAUDE,)
        primary = targets[0]
        rationale = (f"hook — lifecycle 개입점. capability '{cap}' → {primary} primary "
                     f"(Gemini 는 pre/post tool hook 평면 없음, 제외)")
    elif kind == m.KIND_RUNTIME_PLUGIN:
        # vendor-neutral: it runs in the Yule runtime; provider projection is optional.
        plans = tuple(_plan(kind, t, candidate) for t in targets)
        return ProjectionVerdict(
            candidate=candidate, primary_target="", projection_targets=targets, backend_role="",
            plans=plans,
            rationale=f"runtime plugin — vendor-neutral(runtime 실행). native hook 매핑은 선택: "
                      f"{', '.join(targets)} 후보")
    else:
        rationale = (f"capability '{cap}' → {primary} primary "
                     f"({'+'+ '/'.join(secondary) if secondary else 'single'} projection)")

    plans = tuple(_plan(kind, t, candidate) for t in targets)
    return ProjectionVerdict(
        candidate=candidate, primary_target=primary, projection_targets=targets,
        backend_role="", plans=plans, rationale=rationale)


__all__ = ("project",)
