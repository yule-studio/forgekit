"""Unified control-plane bootstrap — composes the onboarding lanes into ONE honest /setup.

``docs/control-plane-architecture.md`` §4 describes ForgeKit setup as a *control-plane
bootstrap* (not a single provider wizard): provider + toolchain + nexus/vault each
"감지 → 검증 → 저장 → 정직한 상태" with the same shape, converging on the single canonical
``~/.forgekit/config.json``. The individual lanes already exist and persist —
``forgekit-provider-connect`` (provider), ``hephaistos.nexus_read`` (knowledge/vault),
``forgekit-toolchain`` (language runtime) — but ``/setup`` only ever showed the provider
lane. This module is the operator-facing COMPOSITION that the doc calls for.

It owns NO core logic: every stage delegates to its package's honest assessor and never
green-washes. Only the **provider live lane** flips overall readiness (a console live-submit
needs a real transport; claude/codex stay routing-only). Knowledge and toolchain are
surfaced as honest, *non-blocking* lanes — ``connected`` / ``not_connected`` / ``detected``
/ ``not_configured`` / ``unavailable`` — so the operator sees the whole control plane in one
screen without any lane being faked into green.

Pure given (config, env, probe, repo_root) so it is fully unit-testable with fakes — the
console (surface) renders :func:`bootstrap_lines`; the data lives in
:class:`ControlPlaneBootstrap`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

from forgekit_provider.policy import provider_ops as ops
from forgekit_provider.policy import provider_surface as psurf
from forgekit_provider_connect import surface, wizard
from forgekit_provider_connect.probe import ConnectionProbe

# stage ids — the control-plane bootstrap lanes (§4) we currently compose.
STAGE_PROVIDER = "provider"
STAGE_KNOWLEDGE = "knowledge"
STAGE_TOOLCHAIN = "toolchain"


@dataclass(frozen=True)
class BootstrapStage:
    """One onboarding lane's honest verdict. ``connected`` is the verified truth for the
    lane; ``blocking`` says whether it gates overall readiness (only provider does)."""

    id: str
    label: str
    status: str            # honest status word (live / not_connected / detected / ...)
    connected: bool        # verified connected/live for this lane (never faked)
    blocking: bool         # gates overall readiness?
    detail: str = ""
    next_action: str = ""

    @property
    def glyph(self) -> str:
        # ● verified-and-counts · ◐ present-but-not-live/optional · ○ not connected
        if self.connected:
            return "●" if self.blocking else "◐"
        return "○"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "status": self.status,
            "connected": self.connected, "blocking": self.blocking,
            "detail": self.detail, "next_action": self.next_action,
        }


@dataclass(frozen=True)
class ControlPlaneBootstrap:
    """The composed control-plane bootstrap report across every onboarding lane."""

    stages: Tuple[BootstrapStage, ...]
    provider: wizard.BootstrapStatus
    config_path: str = ""                       # where the canonical config persists
    live_lane: Tuple[str, ...] = field(default_factory=tuple)
    # honest per-provider 5-state taxonomy (setup-required/configured/linked/live/unsupported).
    provider_states: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        """Ready = every BLOCKING lane is connected (today only the provider lane)."""
        return all(s.connected for s in self.stages if s.blocking)

    @property
    def verdict(self) -> str:
        return "ready" if self.ready else "setup-required"

    def stage(self, sid: str) -> Optional[BootstrapStage]:
        return next((s for s in self.stages if s.id == sid), None)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict, "ready": self.ready,
            "config_path": self.config_path, "live_lane": list(self.live_lane),
            "provider_states": [{"provider": p, "state": s} for p, s in self.provider_states],
            "stages": [s.to_dict() for s in self.stages],
        }


# ── per-lane assessors (each delegates to its package; none fakes a connection) ──────


def _provider_stage(prov: wizard.BootstrapStatus) -> BootstrapStage:
    live = tuple(prov.live_lane)
    if live:
        detail = f"live lane(검증됨): {', '.join(live)} · claude/codex 는 routing/brain participant"
        nxt = ""
    else:
        detail = "live 전송 가능한 provider 없음 — CLI(claude/codex)는 routing-only"
        nxt = "`/setup apply` 로 추천 4-provider preset 저장 · `/provider connect <id>`"
    return BootstrapStage(
        id=STAGE_PROVIDER, label="provider",
        status="live" if live else "setup-required",
        connected=bool(live), blocking=True, detail=detail, next_action=nxt,
    )


def _knowledge_stage(env: Optional[Mapping[str, str]], config: Mapping) -> BootstrapStage:
    # delegate to the honest nexus connection status (connected only when root set AND readable).
    from hephaistos import nexus_read as nx

    cs = nx.connection_status(env, config)
    status = cs["status"]            # not_connected / missing / blocked / exists
    connected = bool(cs["connected"])
    detail = cs["reason"]
    if connected:
        # honest "is this actually an Obsidian vault?" hint — never fakes connection state.
        root = Path(cs["root"])
        try:
            obsidian = (root / ".obsidian").is_dir()
        except OSError:
            obsidian = False
        detail = f"{cs['root']}" + (" · Obsidian vault 감지(.obsidian)" if obsidian else "")
        nxt = ""
    else:
        nxt = "`/nexus set <vault/repo 경로>` 로 지식 source 연결"
    return BootstrapStage(
        id=STAGE_KNOWLEDGE, label="knowledge",
        # "connected" word only when verified; else surface the real status verbatim.
        status="connected" if connected else status,
        connected=connected, blocking=False, detail=detail, next_action=nxt,
    )


def _toolchain_stage(repo_root: Optional[Path]) -> BootstrapStage:
    # lazy: forgekit-toolchain is optional infra — the console must boot without it.
    try:
        from forgekit_toolchain.detect import detect_requirements
    except ImportError:
        return BootstrapStage(
            id=STAGE_TOOLCHAIN, label="toolchain", status="unavailable",
            connected=False, blocking=False,
            detail="forgekit-toolchain 미설치",
            next_action="`pip install -e packages/forgekit-toolchain`",
        )
    root = repo_root or Path(".")
    reqs = detect_requirements(root)
    if reqs:
        tools = ", ".join(f"{r.tool}{('@' + r.version) if r.version else ''}" for r in reqs)
        return BootstrapStage(
            id=STAGE_TOOLCHAIN, label="toolchain", status="detected",
            connected=True, blocking=False,
            detail=f"{len(reqs)} tool (repo-local manifest, 추측 없음): {tools}",
            next_action="`/toolchain verify` 로 mise 기반 검증(설치 시)",
        )
    return BootstrapStage(
        id=STAGE_TOOLCHAIN, label="toolchain", status="not_configured",
        connected=False, blocking=False,
        detail="repo-local 버전 manifest 없음 (.tool-versions/.mise.toml/.nvmrc/...)",
        next_action="`/toolchain recommend <loadout>` 로 loadout 기반 프로파일 제안",
    )


def assess_bootstrap(config: Optional[Mapping] = None, *,
                     env: Optional[Mapping[str, str]] = None,
                     probe: Optional[ConnectionProbe] = None,
                     repo_root: Optional[Path] = None) -> ControlPlaneBootstrap:
    """Compose every onboarding lane into one honest control-plane bootstrap report.

    Reads the same canonical config the lanes persist to (``provider_ops.load_raw_config``)
    so the report reflects what survives an operator restart — no separate state."""

    cfg = dict(config) if config is not None else ops.load_raw_config(env=env)
    prov = wizard.assess(cfg, probe=probe, env=env)
    stages = (
        _provider_stage(prov),
        _knowledge_stage(env, cfg),
        _toolchain_stage(repo_root),
    )
    # honest per-provider taxonomy — live asserted only from the VERIFIED probe (no fake).
    live_map = {s.provider_id: bool(s.live_capable) for s in prov.statuses}
    provider_states = psurf.provider_state_map(cfg, live_map=live_map)
    from forgekit_config.paths import config_path
    return ControlPlaneBootstrap(
        stages=stages, provider=prov,
        config_path=str(config_path(env)), live_lane=tuple(prov.live_lane),
        provider_states=provider_states,
    )


# ── operator surface (the console renders these; logic stays here) ───────────────────


def bootstrap_lines(config: Optional[Mapping] = None, *,
                    env: Optional[Mapping[str, str]] = None,
                    probe: Optional[ConnectionProbe] = None,
                    repo_root: Optional[Path] = None) -> Tuple[str, ...]:
    """`/setup` — the unified control-plane bootstrap: one honest screen across every lane."""

    cfg = dict(config) if config is not None else ops.load_raw_config(env=env)
    bs = assess_bootstrap(cfg, env=env, probe=probe, repo_root=repo_root)

    out = [
        "ForgeKit 컨트롤플레인 부트스트랩 — 한 화면 정직 집계 (provider · knowledge · toolchain)",
        f"  canonical config: {bs.config_path}  (재실행 후에도 유지)",
        "",
    ]
    for st in bs.stages:
        out.append(f"  {st.glyph} {st.label:<10} {st.status:<14} — {st.detail}")
        if st.next_action:
            out.append(f"      다음: {st.next_action}")
    out.append("")

    # honest per-provider state taxonomy (setup-required / configured / linked / live / unsupported).
    out.append("[provider 상태 — 정직 taxonomy]")
    out.append("  " + " · ".join(f"{pid}={state}" for pid, state in bs.provider_states))
    out.append("")

    # the authoritative per-provider connection rows (brain vs live transport, no greenwash).
    out.append("[provider 상세]")
    out.extend("  " + ln for ln in surface.setup_status_lines(cfg, probe=probe, env=env))
    out.append("")

    if bs.ready:
        out.append(f"verdict: {bs.verdict} — provider live lane 있음. "
                   "`/daemon` always-on · `/goal` 연속 작업으로 진행.")
    else:
        out.append(f"verdict: {bs.verdict} — provider live lane 없음(필수). "
                   "`/setup apply` 로 추천 preset 저장 후 재점검.")
    out.append("  지식/toolchain 은 non-blocking 정직 표면 — 미연결도 console 은 동작.")
    return tuple(out)


__all__ = (
    "STAGE_PROVIDER", "STAGE_KNOWLEDGE", "STAGE_TOOLCHAIN",
    "BootstrapStage", "ControlPlaneBootstrap",
    "assess_bootstrap", "bootstrap_lines",
)
