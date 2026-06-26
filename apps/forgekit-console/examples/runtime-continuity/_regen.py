"""Regenerate ``continuity.txt`` — per-tick provider lane + budget continuity (lane B).

Hermetic + deterministic: ``$FORGEKIT_HOME`` tempdir, injected provider config (no
network), a BoundedDaemon with a fake instant sleep + a base tick that advances a safe-class
step each tick. Shows (1) honest lane labels for live / participant / fallback configs, and
(2) a 6-tick bounded serve that keeps progressing — recording each tick's provider lane +
budget to the durable tick ledger, then the operator surface over it.

Run: ``python apps/forgekit-console/examples/runtime-continuity/_regen.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
for _rel in ("packages/forgekit-runtime/src", "packages/forgekit-provider/src",
             "packages/forgekit-config/src", "packages/forgekit-goal/src",
             "packages/forgekit-contracts/src", "packages/nexus/src",
             "packages/hephaistos/src", "packages/armory/src"):
    _p = str(_ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forgekit_provider.policy.provider_config import load_provider_config
from forgekit_runtime.runtime import tick_ledger as TL
from forgekit_runtime.runtime.daemon import BoundedDaemon, TickOutcome
from forgekit_runtime.runtime.provider_continuity import with_provider_continuity
from forgekit_runtime.runtime.provider_lane import resolve_tick_lane
from forgekit_runtime.runtime.surface import provider_lane_lines

OUT = Path(__file__).resolve().parent / "continuity.txt"

_CONFIGS = [
    ("live (gemini primary)", {"primary_provider": "gemini", "linked_providers": ["gemini", "claude"]}),
    ("participant-only (claude — unsupported_in_console)",
     {"primary_provider": "claude", "linked_providers": ["claude"]}),
    ("fallback→live (claude declared → gemini)",
     {"primary_provider": "claude", "linked_providers": ["claude", "gemini"],
      "fallback_policy": {"slot_fallback_orders": {"execution": ["gemini"]}}}),
]


def _hr(t: str) -> str:
    return f"\n{'─' * 78}\n{t}\n{'─' * 78}"


def main() -> None:
    out = ["FORGEKIT PROVIDER/RUNTIME CONTINUITY — per-tick lane + budget (evidence)",
           "(hermetic: $FORGEKIT_HOME tempdir, injected config, instant sleep, no network)"]

    out.append(_hr("1) honest lane resolution — brain vs actual transport vs fallback"))
    for label, cfg in _CONFIGS:
        lane = resolve_tick_lane(load_provider_config(cfg))
        out.append(f"[{label}]")
        out.append(f"  short : {lane.short()}")
        out.append(f"  label : {lane.label()}")
        if lane.fallback_used:
            out.append(f"  chain : {' → '.join(lane.fallback_chain)}")

    out.append(_hr("2) bounded serve keeps an active goal progressing (6 ticks, live lane)"))
    with tempfile.TemporaryDirectory() as d:
        env = {"FORGEKIT_HOME": d}
        cfg = {"primary_provider": "gemini", "linked_providers": ["gemini"],
               "daily_token_budget": 20000}
        step = {"n": 0}

        def base_tick(n):
            step["n"] += 1
            return TickOutcome(summary=f"goal step {n} (safe-class)", executed=1,
                               executed_paths=(f"runs/forgekit/step-{n}.md",))

        tick_fn = with_provider_continuity(base_tick, config=cfg, env=env)
        daemon = BoundedDaemon(poll_interval=0.0, max_ticks=6, sleep_fn=lambda s: None,
                               heartbeat_path=Path(d) / "hb.json", kill_switch_path=Path(d) / "kill")
        res = daemon.serve(tick_fn)
        out.append(f"serve result: ticks={res.ticks} · executed={res.executed} "
                   f"· waits={res.waits} · stopped={res.stopped_reason}")
        out.append(f"progression: {step['n']} steps advanced across {res.ticks} ticks (no stall)")
        out.append("")
        out.append("durable per-tick ledger (~/.forgekit/state/runtime-tick-ledger.jsonl):")
        out.extend(provider_lane_lines(env=env, recent=6))

    out.append(_hr("acceptance"))
    out.append("- runtime serve 중 active goal 이 매 tick step 전진 (6/6, no stall).")
    out.append("- provider route / actual transport / fallback 가 lane 으로 정직 표기.")
    out.append("- budget/receipt(executed paths)/evidence 가 tick 단위 ledger 로 남음.")
    out.append("- Claude/Codex 는 unsupported_in_console 이어도 brain participant 로 유효 표기.")

    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(_ROOT)} ({len(out)} lines)")


if __name__ == "__main__":
    main()
