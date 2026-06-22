"""Regenerate cli.txt — `/provider budget` operator surface, through the REAL router.

Deterministic (temp FORGEKIT_HOME, no net): set a per-provider daily token limit, show
the configured limit + today's spent/over honestly (from a seeded usage ledger), and the
honest errors (unknown id / non-integer limit). Run from repo root with every package src
+ apps/forgekit-console/src on PYTHONPATH; redirect stdout into cli.txt.
Regression: tests/forgekit/test_provider_budget_cli.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import ConsoleContext, route
from forgekit_provider.usage import UsageEvent, append_event, today, usage_ledger_path


def banner(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main() -> None:
    print("ForgeKit `/provider budget` 운영 콘솔 표면 — deterministic evidence (no fake, no net)")
    print("재현: tests/forgekit/test_provider_budget_cli.py")

    with tempfile.TemporaryDirectory() as home:
        env = {"FORGEKIT_HOME": home}

        def run(raw: str) -> None:
            res = route(parse_input(raw), ConsoleContext(repo_root=Path("."), env=env))
            print(f"$ {raw}   [{res.kind}]")
            for ln in res.lines:
                print(f"  {ln}")

        banner("STEP 0 — 브레인 구성 (budget 는 기존 브레인의 ring-fencing — 빈 config 영속 거부)")
        run("/provider set gemini")
        run("/provider link ollama")

        banner("STEP 1 — 한도 설정 + 영속 (set → reload show)")
        run("/provider budget gemini 50000")
        run("/provider budget show")

        banner("STEP 2 — 오늘 spent/over 정직 표시 (usage ledger 기반, 가짜 숫자 없음)")
        for tok in (20000, 35000):
            append_event(UsageEvent(ts=today(), provider="gemini", total_tokens=tok, success=True),
                         path=usage_ledger_path(env))
        run("/provider budget show")  # 55000/50000 → OVER

        banner("STEP 3 — 해제 = unbounded (0 이하 → 한도 제거, 절대 0 으로 invent 안 함)")
        run("/provider budget gemini 0")
        run("/provider budget show")

        banner("STEP 4 — 정직한 에러 (unknown id / non-integer / missing limit, silent no-op 없음)")
        run("/provider budget bogus 1000")
        run("/provider budget gemini lots")
        run("/provider budget gemini")


if __name__ == "__main__":
    main()
