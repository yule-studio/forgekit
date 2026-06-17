"""Render readiness — is THIS environment ready for a true-raster avatar/brand?

A small, operator-facing surface (the ``/render`` command, and reusable by doctor)
that answers, **without the debug flag**, four separated questions:

  1. Python  — is the interpreter ≥3.10 (``textual-image`` needs ``types.NoneType``)?
  2. Library — is ``textual-image`` importable, and which backend did it bind?
  3. Terminal — what did capability detection guess, and what is the realized backend?
  4. Policy  — true-raster / managed-fallback / hard-fallback, plus the recommended
     terminal when the current one cannot do a true raster.

Pure given an ``env`` mapping (it only reads ``image_renderer.diagnose_renderers``
+ ``sys.version_info``), so it is unit-testable without a terminal. ``true raster``
means TGP (Kitty) or Sixel (iTerm2/WezTerm/foot/…); halfcell/unicode are
textual-image's own cell fallbacks and forgekit treats them as managed fallback.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from . import image_renderer as ir
from . import theme

# Python floor for the real-raster path (textual-image imports ``types.NoneType``).
MIN_RASTER_PYTHON = (3, 10)

# The officially RECOMMENDED true-raster terminals (probe runs before Textual
# grabs stdin → tgp/sixel resolve). VS Code's integrated terminal is supported as
# a FALLBACK-first path (no sixel/tgp response → halfcell).
RECOMMENDED_RASTER_TERMINALS = ("iTerm2", "WezTerm", "Kitty")


@dataclass(frozen=True)
class TerminalSupport:
    """One row of the official support matrix."""

    terminal: str
    expected_backend: str   # what backend we expect there
    recommended: bool       # is it a recommended true-raster path?
    support: str            # human note


def terminal_support_matrix() -> Tuple[TerminalSupport, ...]:
    """The official forgekit terminal support matrix (static expectations)."""

    return (
        TerminalSupport("VS Code integrated", "halfcell", False,
                        "fallback-first: no sixel/tgp response → managed fallback"),
        TerminalSupport("iTerm2", "sixel", True, "true raster (sixel inline images)"),
        TerminalSupport("WezTerm", "sixel", True, "true raster (sixel; also reports tgp)"),
        TerminalSupport("Kitty", "tgp", True, "true raster (Terminal Graphics Protocol)"),
    )


@dataclass(frozen=True)
class RenderReadiness:
    """Whether the current environment can show a true-raster avatar/brand."""

    python_version: str
    python_ok: bool
    lib_ok: bool
    lib_reason: str
    lib_backend: str
    capability_reason: str
    avatar_asset: str          # terminal-icon (default) vs portrait (opt-in)
    avatar_backend: str
    avatar_policy: str
    brand_backend: str
    brand_policy: str
    true_raster_ready: bool


def render_readiness_report(env: Optional[Mapping[str, str]] = None) -> RenderReadiness:
    """Build the readiness report for *env* (defaults to the live environment)."""

    diag = ir.diagnose_renderers(env)
    python_ok = sys.version_info[:2] >= MIN_RASTER_PYTHON
    pyver = ".".join(str(p) for p in sys.version_info[:3])
    true_ready = python_ok and diag.lib_ok and ir.is_true_raster(diag.avatar_backend)
    return RenderReadiness(
        python_version=pyver,
        python_ok=python_ok,
        lib_ok=diag.lib_ok,
        lib_reason=diag.lib_reason,
        lib_backend=diag.lib_backend,
        capability_reason=diag.capability_reason,
        avatar_asset=ir.avatar_asset_mode(env),
        avatar_backend=diag.avatar_backend,
        avatar_policy=diag.avatar_policy,
        brand_backend=diag.brand_backend,
        brand_policy=diag.brand_policy,
        true_raster_ready=true_ready,
    )


def _ok(flag: bool) -> str:
    return f"[{theme.SUCCESS}]✓[/{theme.SUCCESS}]" if flag else f"[{theme.WARNING}]✗[/{theme.WARNING}]"


def render_readiness_lines(report: Optional[RenderReadiness] = None,
                           env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """Human-readable readiness lines (Rich markup) for ``/render`` / doctor."""

    r = report or render_readiness_report(env)
    lib = f"import ok · backend={r.lib_backend}" if r.lib_ok else f"[{theme.ERROR}]import 실패[/{theme.ERROR}]: {r.lib_reason}"
    head = (
        f"[{theme.SUCCESS}]true-raster ready[/{theme.SUCCESS}]"
        if r.true_raster_ready
        else f"[{theme.WARNING}]fallback mode[/{theme.WARNING}] (true raster 아님)"
    )
    lines = [
        f"[b]forgekit render readiness[/b] — {head}",
        "",
        f"  {_ok(r.python_ok)} python        {r.python_version}  (textual-image 는 ≥3.10)",
        f"  {_ok(r.lib_ok)} textual-image {lib}",
        f"  · terminal     {r.capability_reason}",
        f"  · avatar asset {r.avatar_asset}  (기본=terminal-icon · 상세 portrait 는 FORGEKIT_AVATAR=portrait)",
        f"  · avatar       {r.avatar_backend} ({r.avatar_policy})",
        f"  · brand        {r.brand_backend} ({r.brand_policy})",
        "",
    ]
    if r.true_raster_ready:
        lines.append(f"  [{theme.SUCCESS}]→ 이 환경은 true raster 를 그립니다.[/{theme.SUCCESS}]")
    else:
        lines.append(
            "  → 지금은 managed fallback (브랜드 배지/워드마크). 깨끗하지만 픽셀 이미지는 아닙니다."
        )
        if not r.python_ok:
            lines.append("  → Python 3.10+ console env 필요 (.venv-console).")
        lines.append(
            "  → true raster 권장 터미널: " + " · ".join(RECOMMENDED_RASTER_TERMINALS)
            + "  (probe 가 Textual 시작 전에 돌아야 함)"
        )
    lines.append("")
    lines.append("[dim]terminal 지원 매트릭스:[/dim]")
    for row in terminal_support_matrix():
        tag = "권장" if row.recommended else "fallback"
        lines.append(f"  [dim]{row.terminal:<20}[/dim] {row.expected_backend:<9} [{tag}] {row.support}")
    return tuple(lines)


__all__ = (
    "MIN_RASTER_PYTHON",
    "RECOMMENDED_RASTER_TERMINALS",
    "TerminalSupport",
    "terminal_support_matrix",
    "RenderReadiness",
    "render_readiness_report",
    "render_readiness_lines",
)
