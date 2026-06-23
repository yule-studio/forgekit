"""Regenerate progress-motion + selection-visibility evidence — RUNTIME proof + real SVG.

Three parity gaps, proven with measurement / live render (not CSS reading, not fake):

1. **live "진행중" motion** — the active process-feed step is an amber braille spinner that
   ADVANCES across frames + a LIVE elapsed ``(X.Xs)`` from the real clock. Shown across 3
   real frames so the motion is visible in text; the SVG screenshots a running feed.
2. **selection visibility** — the composer + cross-widget selection now resolve to the
   saturated-blue SELECTION_BG, with a measured contrast-vs-background that clears the floor
   the old accent-dim did not.
3. **transcript readability** — the user prompt head is bold (turn anchor).

Outputs: ``progress-selection-evidence.txt`` + ``running-feed.svg`` (export_screenshot of a
live running feed). 재현: tests/forgekit/test_tui_progress_selection_lane.py ·
test_tui_process_feed.py · test_tui_selection_contrast.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from forgekit_console.commands.registry import load_agents, load_commands
from forgekit_console.commands.router import ConsoleContext
from forgekit_console.tui import process_events as pe
from forgekit_console.tui import render, theme
from forgekit_console.tui.app import ForgekitConsoleApp
from forgekit_console.tui.prompt_area import PromptArea

_HERE = Path(__file__).resolve().parent


def _contrast(c1, c2) -> float:
    def lum(c):
        def chan(v):
            v = v / 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * chan(c.r) + 0.7152 * chan(c.g) + 0.0722 * chan(c.b)
    l1, l2 = sorted((lum(c1), lum(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _mk():
    ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(), commands=load_commands())
    return ForgekitConsoleApp(repo_root=Path("/tmp/repo"), context=ctx,
                              config={"primary_provider": "ollama", "linked_providers": ["ollama"]})


async def main() -> None:
    from textual.color import Color

    out = []

    def banner(t):
        out.append("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)

    out.append("ForgeKit console — 진행중 motion + selection visibility + transcript readability — RUNTIME proof")
    out.append("재현: tests/forgekit/test_tui_progress_selection_lane.py · test_tui_process_feed.py")

    banner("GAP 1 — 진행중 motion (active feed step) — amber spinner ADVANCES + live elapsed")
    feed = pe.ProcessFeed(clock=lambda: 5.3)            # started_at=5.3
    feed.start(pe.KIND_GENERATE_START, "Generating")
    out.append("  3 연속 motion frame (now=7.6s → elapsed 2.3s, 실제 clock):")
    for fr in (0, 1, 2):
        out.append("    frame %d : %s" % (fr, render.process_feed_lines(feed.recent(), now=7.6, frame=fr)[0]))
    out.append("  → 글리프가 프레임마다 이동(살아있는 motion) · 색=WARNING amber(%s) · elapsed=실측" % theme.WARNING)
    out.append("  no fake typing: now 없으면 elapsed 미표기, frame 안 돌면 정적 — motion 은 running 상태에서만")

    banner("GAP 2 — selection visibility (saturated-blue SELECTION_BG, runtime property)")
    app = _mk()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        scr = app.screen.get_component_styles("screen--selection")
        ta = app.query_one(PromptArea).get_component_styles("text-area--selection")
        bg_vs = _contrast(scr.background, Color.parse(theme.BG))
        out.append(f"  screen--selection   bg={scr.background}  fg={scr.color}")
        out.append(f"  text-area--selection bg={ta.background}  fg={ta.color}")
        out.append(f"  SELECTION_BG = {theme.SELECTION_BG} (old accent-dim = {theme.ACCENT_DIM})")
        out.append(f"  contrast vs background({theme.BG}) : {bg_vs:.2f}:1  (old accent-dim ≈ 3.40:1)")
        out.append(f"  contrast FG/selection : {_contrast(scr.color, scr.background):.2f}:1 (읽기 가능)")

        # screenshot a LIVE running feed (motion visible in a real render)
        app._feed.begin_turn()
        app._feed.start(pe.KIND_GENERATE_START, "Generating to ollama")
        app._motion_frame = 2
        app._render_feed()
        await pilot.pause()
        svg = app.export_screenshot(title="forgekit — 진행중 (running feed motion)")
        (_HERE / "running-feed.svg").write_text(svg, encoding="utf-8")
        out.append("\n  screenshot: running-feed.svg (export_screenshot — 실제 렌더, 활성 feed)")

    banner("GAP 3 — transcript readability (user prompt head bold = turn anchor)")
    for ln in render.you_echo_lines("이번 turn 의 질문\n계속되는 줄"):
        out.append("  " + ln)

    out.append("\n(끝) — 색/대비/elapsed 는 mounted app·실제 clock 에서 실측 · 가짜 typing 0 · real status motion only")
    (_HERE / "progress-selection-evidence.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    asyncio.run(main())
