"""Regenerate route-resolution-evidence.txt — deterministic (pure, no net, no temp home).

`/provider route show` resolves each slot to its ACTUAL live provider via the explicit
fallback — so a non-chat work slot that DECLARES a CLI brain (codex/claude = routing-only)
is shown reaching its live transport (gemini/ollama), never left looking broken. Run from
repo root with every package src on PYTHONPATH; redirect stdout into
route-resolution-evidence.txt. Regression: tests/forgekit/test_provider_surface.py
(RouteShowResolutionTests).
"""

from __future__ import annotations

from forgekit_provider.policy import provider_ops as ops
from forgekit_provider.policy import provider_surface as ps


def banner(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main() -> None:
    print("ForgeKit `/provider route show` — slot 별 declared → actual (fallback 반영) evidence")
    print("재현: tests/forgekit/test_provider_surface.py::RouteShowResolutionTests")

    banner("[A] four-brain preset — work slot 이 CLI brain 을 declare 해도 live 로 해소")
    print("config: primary=claude · default_chat=gemini · execution=codex · safety/synthesis=claude")
    print("        fallback: execution=[codex,gemini,ollama] · safety=[claude,gemini] · synthesis=[claude,gemini,ollama]")
    print()
    for ln in ps.route_show_lines(ops.preset_four_brain({})):
        print(ln)
    print()
    print("→ execution/safety/synthesis 는 declared(codex/claude, routing-only)이지만 fallback 이")
    print("  gemini 로 닿아 `declared → actual` 로 표기된다. unsupported_in_console 로 끝나지 않는다.")

    banner("[B] fallback 이 전부 routing-only → 정직한 'live 경로 없음' + 다음 액션")
    cfg = {
        "primary_provider": "claude",
        "linked_providers": ["claude", "codex"],
        "slot_routing": {"safety": "claude"},
        "fallback_policy": {"slot_fallback_orders": {"safety": ["claude", "codex"]}},
    }
    for ln in ps.route_show_lines(cfg):
        if "safety" in ln or "범례" in ln:
            print(ln)
    print()
    print("→ claude/codex 만으로는 live 불가 → ○ (live 경로 없음) + `/provider route set safety <gemini|ollama>`.")

    banner("[C] primary 미설정 → setup-required (dead-end 아님)")
    for ln in ps.route_show_lines({}):
        print(ln)


if __name__ == "__main__":
    main()
