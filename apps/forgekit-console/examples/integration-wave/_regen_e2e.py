"""Regenerate ``e2e.txt`` — cross-lane wave integration evidence.

Deterministic + hermetic + NETWORK-FREE: threads the whole wave path
(intake → Armory → Hephaistos → Nexus → provider projection → runtime receipt)
on representative scenarios with a temp FORGEKIT_HOME and stubbed collectors, then
runs the consult merge gate over the wave's changes. Mirrors
``tests/forgekit/test_integration_wave_e2e.py`` — same scenarios, same honest seams.

Run: ``python apps/forgekit-console/examples/integration-wave/_regen_e2e.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
for _rel in (
    "packages/forgekit-runtime/src", "packages/forgekit-goal/src",
    "packages/forgekit-config/src", "packages/forgekit-contracts/src",
    "packages/forgekit-provider/src", "packages/hephaistos/src",
    "packages/nexus/src", "packages/armory/src",
    "apps/forgekit-console/src",
):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from armory import catalog
from forgekit_console import discovery as D
from forgekit_console.policy import provider_ops as ops
from forgekit_console.policy import provider_surface as ps
from forgekit_runtime import forge as F
from forgekit_runtime.decision_lane import ChangeUnderReview, consult_gate_report
from hephaistos import nexus_read as nx, resolve

OUT = Path(__file__).resolve().parent / "e2e.txt"

_OFFLINE_CFG = {"discovery": {"hackernews_query": "", "subreddits": [], "github_query": ""}}
SCENARIOS = [
    ("Spring Boot JWT 인증 API 추가", "safe engineering"),
    ("Next.js 디자인 시스템 컴포넌트 라이브러리 구축", "design / non-engineering"),
    ("Terraform + ECS + GitHub Actions 배포 파이프라인 구성", "deploy / destructive"),
]


def main() -> None:
    home = Path(tempfile.mkdtemp())
    repo = Path(tempfile.mkdtemp())
    (repo / "apps").mkdir()
    (repo / "apps" / "m.py").write_text("# TODO auth\n# FIXME token\n", encoding="utf-8")
    env = {"FORGEKIT_HOME": str(home)}
    out = ["forgekit 웨이브 통합 E2E — intake → Armory → Hephaistos → Nexus → provider → receipt",
           "=" * 78, ""]

    # gw1 intake — discovery ledger lifecycle (dedup + persist)
    led = D.DiscoveryLedger.load(env)
    sweep = D.run_discovery_sweep(repo, fetcher=lambda u: "{}", config=_OFFLINE_CFG,
                                  extra_signals=["JWT refresh 토큰 회전 누락 보안 약점"])
    new, _ = led.record_sweep(sweep, now="2026-06-23T10:00:00")
    led.record_sweep(sweep, now="2026-06-23T11:00:00")          # re-sweep → dedup
    if led.pending():
        led.mark(D.fingerprint(led.pending()[0].problem), D.ST_PROMOTED)
    led.save(env)
    s = led.summary()
    out.append(f"[gw1 intake ] sweep new={len(new)} · re-sweep dedup · promoted={s['promoted']} (영속 ledger)")
    out.append("")

    # per-scenario: Armory + Hephaistos + Nexus + provider + receipt
    for req, label in SCENARIOS:
        plan = resolve(req)
        loadout_ok = catalog.loadout(plan.selected_loadout) is not None
        read = nx.read_plan_sources(plan, env={}, config={})
        receipt = F.forge_execute(req, weapon_safety=lambda w: "safe",
                                  env=env, persist=True, recorded_at="2026-06-23")
        verr = F.validate_forge_receipt(receipt)
        out.append(f"### 시나리오 [{label}]: {req}")
        out.append(f"  [gw2 armory  ] agent={plan.selected_agent} loadout={plan.selected_loadout} "
                   f"(catalog 실존={loadout_ok}) skills={len(plan.selected_skills)}")
        out.append(f"  [gw3 nexus   ] not_connected={read.not_connected} (미연결 정직, 날조 없음)")
        out.append(f"  [gw5 receipt ] authorized={receipt.authorized} outcome={receipt.outcome} "
                   f"class={receipt.action_class} valid={not verr}")
        if not receipt.authorized:
            out.append(f"               blocked: {receipt.blocking_reasons[0]}")
        else:
            out.append(f"               approval: {receipt.approval_metadata}")
        out.append("")

    # gw4 provider projection — persist + reload
    cfg_path = home / "config.json"
    ps.apply_set_primary("ollama", path=cfg_path)
    ps.apply_link("gemini", path=cfg_path)
    ps.apply_route_set("execution", "ollama", path=cfg_path)
    cfg = ops.load_raw_config(path=cfg_path)
    out.append(f"[gw4 provider] primary={cfg['primary_provider']} linked={cfg['linked_providers']} "
               f"execution→{cfg['slot_routing']['execution']} (영속+reload)")

    # runtime receipt ledger — append-only, fake refused
    entries = F.read_forge_receipts(env=env)
    outcomes = [e["receipt"]["outcome"] for e in entries]
    fake = F.ForgeExecutionReceipt(request="x", authorized=True, outcome=F.OUTCOME_EXECUTED,
                                   approval_metadata="", selected_agent="backend-engineer")
    try:
        F.record_forge_receipt(fake, env=env)
        refused = False
    except F.FakeReceiptRefused:
        refused = True
    out.append(f"[gw5 ledger  ] receipts={len(entries)} outcomes={outcomes} · fake_refused={refused}")
    out.append("")

    # consult merge gate over the wave
    changes = [
        ChangeUnderReview("integration-qa-lane", change_kinds=("integration", "test", "docs")),
        ChangeUnderReview("consult-gate", change_kinds=("test", "qa")),
        ChangeUnderReview("hypothetical-unconsulted-api", change_kinds=("api-contract",)),
    ]
    rep = consult_gate_report(changes)
    out.append("[consult merge gate]")
    out.extend("  " + ln for ln in rep.lines())
    out.append("")
    out.append("honesty rails: Armory 카탈로그 curated(런타임 무변경) · Nexus 미연결 날조 없음 · "
               "deploy=destructive/L4 차단 · non-engineering=exec slot 없음 · safe-eng만 receipt · "
               "fake receipt 거부 · consult required+missing=merge 금지")

    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
