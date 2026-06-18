"""Usage reports (WT2) — txt / md / json from a rollup. Human surfaces, JSON for reuse.

txt: operator-at-a-glance numbers. md: explainable structure. json: machine-reusable.
All re-generatable from the ledger (ledger is the SSoT, these are views).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Sequence, Tuple


def to_txt(roll, top: Sequence[dict] = ()) -> str:
    d = roll.to_dict()
    lines = [
        f"forgekit usage — {d['scope']}",
        f"  events            : {d['events']}",
        f"  total tokens      : {d['total_tokens']}  (in {d['input_tokens']} / out {d['output_tokens']})",
        f"  live vs estimate  : live {d['live_tokens']} / estimate {d['estimate_tokens']} "
        f"(live ratio {d['live_ratio']})",
        f"  throttled / fallback: {d['throttled']} / {d['fallback']}",
        "  by provider       : " + (", ".join(f"{k}={v}" for k, v in d["by_provider"].items()) or "-"),
        "  by mode           : " + (", ".join(f"{k}={v}" for k, v in d["by_mode"].items()) or "-"),
    ]
    if top:
        lines.append("  top by tokens     :")
        for t in top:
            lines.append(f"    - {t.get('total_tokens',0)}tok {t.get('provider','')}/{t.get('mode','')} "
                         f"({t.get('usage_basis','')})")
    return "\n".join(lines) + "\n"


def to_md(roll) -> str:
    d = roll.to_dict()
    return "\n".join([
        f"# forgekit usage — {d['scope']}",
        "",
        "## 관측",
        f"- 총 토큰: **{d['total_tokens']}** (in {d['input_tokens']} / out {d['output_tokens']}), events {d['events']}",
        f"- live/estimate: {d['live_tokens']}/{d['estimate_tokens']} (live ratio {d['live_ratio']})",
        "",
        "## 많이 쓴 곳",
        "- provider: " + (", ".join(f"{k} {v}" for k, v in d["by_provider"].items()) or "-"),
        "- mode: " + (", ".join(f"{k} {v}" for k, v in d["by_mode"].items()) or "-"),
        "",
        "## 이상 징후 / 절감 포인트",
        f"- throttled {d['throttled']} · fallback {d['fallback']}",
        "- estimate 비중이 높으면 vendor-native usage 연결 시 정확도↑ (현재 honest estimate)",
    ]) + "\n"


def to_json(roll) -> str:
    return json.dumps(roll.to_dict(), ensure_ascii=False, indent=2)


def write_reports(roll, out_dir, *, top: Sequence[dict] = ()) -> Tuple[Path, ...]:
    """Write summary.txt / summary.md / summary.json to *out_dir*. Best-effort."""

    written = []
    try:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary.txt").write_text(to_txt(roll, top), encoding="utf-8")
        (d / "summary.md").write_text(to_md(roll), encoding="utf-8")
        (d / "summary.json").write_text(to_json(roll), encoding="utf-8")
        written = [d / "summary.txt", d / "summary.md", d / "summary.json"]
    except OSError:
        pass
    return tuple(written)


__all__ = ("to_txt", "to_md", "to_json", "write_reports")
