"""Design-to-code asset routing — P0-I stage 3 (#141).

Implements stage-1 policy ``policies/runtime/agents/engineering-agent/design-to-code-assets.md``:

  * **naming convention** — ``<surface>-<intent>-<modifier?>`` (e.g.
    ``logo-primary``, ``icon-status-success``, ``favicon-light``).
  * **5 차원** ownership — `product-designer` 가 의미/형태/컬러/비율/용도
    정의, `frontend-engineer` 가 SVG/컴포넌트 구현.
  * **SVG vs raster** boundary — single-color / flat → SVG, 실사 /
    텍스처 → raster. > 50KB SVG → raster 권장.
  * **handoff** — designer 산출 5차원 명세 → frontend 가 SVG source-
    of-truth + raster export 매트릭스.

This module is a **validator + router only**. Actual asset
storage / `<Icon>` 컴포넌트 / raster export 스크립트 는 frontend
production 이 생기는 시점에 wire — stage 1 정책의 "fake success
금지" 원칙을 본 commit 에서도 지킨다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple


# Asset surfaces — stage-1 정책 §1.
SURFACE_LOGO = "logo"
SURFACE_FAVICON = "favicon"
SURFACE_ICON = "icon"
SURFACE_ILLUSTRATION = "illustration"
SURFACES = (SURFACE_LOGO, SURFACE_FAVICON, SURFACE_ICON, SURFACE_ILLUSTRATION)

# Format primary recommendation per surface.
SURFACE_TO_PRIMARY_FORMAT: Mapping[str, str] = {
    SURFACE_LOGO: "svg",
    SURFACE_FAVICON: "svg",
    SURFACE_ICON: "svg",
    SURFACE_ILLUSTRATION: "svg",  # complex illustration may flip to raster
}

# Asset name regex — stage-1 정책 §2.1.
# Surface prefix is required + at least one hyphen-separated intent token.
# Modifier(s) optional. Lowercase, ASCII-only, no whitespace.
_NAME_RE = re.compile(
    r"^(?P<surface>logo|favicon|icon|illustration)-"
    r"(?P<intent>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)

# Raster size threshold for SVG → raster recommendation (>50KB).
SVG_RASTER_FALLBACK_BYTES = 50 * 1024

# Raster surfaces (favicon needs PNG export matrix).
FAVICON_RASTER_SIZES = (16, 32, 48, 192, 512)
APPLE_TOUCH_ICON_SIZE = 180
OG_IMAGE_SIZE = (1200, 630)


@dataclass(frozen=True)
class AssetValidation:
    """Result of :func:`validate_asset_name`."""

    name: str
    valid: bool
    surface: Optional[str] = None
    intent: Optional[str] = None
    failure_reasons: Tuple[str, ...] = ()
    suggestions: Tuple[str, ...] = ()

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "name": self.name,
            "valid": self.valid,
            "surface": self.surface,
            "intent": self.intent,
            "failure_reasons": list(self.failure_reasons),
            "suggestions": list(self.suggestions),
        }


@dataclass(frozen=True)
class FormatRecommendation:
    """SVG vs raster recommendation per stage-1 §4."""

    primary_format: str  # "svg" or "raster"
    raster_required_for: Tuple[str, ...] = ()
    reason: Optional[str] = None
    # Approximate size in bytes for the SVG candidate, when known.
    svg_estimated_bytes: Optional[int] = None
    # Whether this asset crosses the "complex illustration" boundary.
    boundary_crossing: bool = False

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "primary_format": self.primary_format,
            "raster_required_for": list(self.raster_required_for),
            "reason": self.reason,
            "svg_estimated_bytes": self.svg_estimated_bytes,
            "boundary_crossing": self.boundary_crossing,
        }


@dataclass(frozen=True)
class HandoffPacket:
    """Designer → frontend handoff envelope (stage-1 §2 + §3)."""

    name: str
    surface: str
    intent: str
    # 5 차원 ownership — designer 가 정의 (텍스트로 받아 들임).
    dimensions: Mapping[str, str] = field(default_factory=dict)
    # Token references — design-system token names (no hex).
    color_tokens: Tuple[str, ...] = ()
    # Recommended viewBox (e.g. "24 24" or "48 48").
    view_box: Optional[str] = None
    # SVG source path when committed.
    svg_source_path: Optional[str] = None
    # Raster outputs (path, format, size) — populated by export.
    raster_outputs: Tuple[Mapping[str, Any], ...] = ()
    format_recommendation: Optional[Mapping[str, Any]] = None
    blockers: Tuple[str, ...] = ()

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "name": self.name,
            "surface": self.surface,
            "intent": self.intent,
            "dimensions": dict(self.dimensions),
            "color_tokens": list(self.color_tokens),
            "view_box": self.view_box,
            "svg_source_path": self.svg_source_path,
            "raster_outputs": [dict(r) for r in self.raster_outputs],
            "format_recommendation": (
                dict(self.format_recommendation)
                if self.format_recommendation
                else None
            ),
            "blockers": list(self.blockers),
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_asset_name(name: str) -> AssetValidation:
    """Check *name* against stage-1 §2.1 naming convention.

    Valid examples: ``logo-primary``, ``logo-primary-light``,
    ``favicon-light``, ``icon-status-success``, ``illustration-empty-state``.

    Invalid examples: ``Logo`` (uppercase), ``my-asset``
    (unknown surface), ``logo`` (no intent), ``logo--primary``
    (double hyphen).
    """

    if not name or not isinstance(name, str):
        return AssetValidation(
            name=str(name or ""),
            valid=False,
            failure_reasons=("empty_or_invalid_type",),
        )
    text = name.strip()
    reasons: list = []
    suggestions: list = []

    if not text:
        return AssetValidation(name=name, valid=False, failure_reasons=("empty",))

    if text != text.lower():
        reasons.append("uppercase_characters")
        suggestions.append(f"lower-case → `{text.lower()}`")

    if " " in text or "\t" in text:
        reasons.append("contains_whitespace")
        suggestions.append("공백 → 하이픈으로 교체")

    if "--" in text:
        reasons.append("double_hyphen")

    if reasons:
        # Already failed surface-blind checks.
        return AssetValidation(
            name=name,
            valid=False,
            failure_reasons=tuple(reasons),
            suggestions=tuple(suggestions),
        )

    match = _NAME_RE.match(text)
    if not match:
        # Determine why — wrong surface or missing intent.
        if not any(text.startswith(f"{s}-") for s in SURFACES):
            reasons.append("unknown_surface_prefix")
            suggestions.append(
                "surface 는 `logo` / `favicon` / `icon` / `illustration` 중 하나"
            )
        elif "-" not in text:
            reasons.append("missing_intent")
            suggestions.append("`<surface>-<intent>` 형태 필요 (예: `logo-primary`)")
        elif not re.match(r"^[a-z0-9-]+$", text):
            reasons.append("invalid_characters")
        else:
            reasons.append("does_not_match_naming_pattern")
        return AssetValidation(
            name=name,
            valid=False,
            failure_reasons=tuple(reasons),
            suggestions=tuple(suggestions),
        )

    return AssetValidation(
        name=name,
        valid=True,
        surface=match.group("surface"),
        intent=match.group("intent"),
    )


# ---------------------------------------------------------------------------
# SVG vs raster recommendation
# ---------------------------------------------------------------------------


def recommend_format(
    *,
    surface: str,
    is_photographic: bool = False,
    is_complex_illustration: bool = False,
    svg_estimated_bytes: Optional[int] = None,
) -> FormatRecommendation:
    """Return SVG/raster guidance per stage-1 §4.

    * 실사 (`is_photographic=True`) → raster.
    * 단일 surface 가 favicon → SVG source + raster export 매트릭스 강제.
    * SVG 가 50KB 이상 → raster 검토 권장.
    """

    if is_photographic:
        return FormatRecommendation(
            primary_format="raster",
            reason="실사 사진 / 텍스처 → raster",
        )

    boundary = False
    if surface == SURFACE_ILLUSTRATION and is_complex_illustration:
        boundary = True

    if (
        svg_estimated_bytes is not None
        and svg_estimated_bytes > SVG_RASTER_FALLBACK_BYTES
    ):
        return FormatRecommendation(
            primary_format="raster",
            reason=f"SVG 가 {svg_estimated_bytes} bytes 로 {SVG_RASTER_FALLBACK_BYTES} bytes 임계 초과",
            svg_estimated_bytes=svg_estimated_bytes,
            boundary_crossing=True,
        )

    raster_required: Tuple[str, ...] = ()
    if surface == SURFACE_FAVICON:
        raster_required = tuple(
            f"favicon-{size}.png" for size in FAVICON_RASTER_SIZES
        ) + (
            f"apple-touch-icon-{APPLE_TOUCH_ICON_SIZE}.png",
        )

    return FormatRecommendation(
        primary_format="svg",
        raster_required_for=raster_required,
        reason=(
            "favicon 은 SVG source + PNG export 매트릭스 필요"
            if surface == SURFACE_FAVICON
            else None
        ),
        svg_estimated_bytes=svg_estimated_bytes,
        boundary_crossing=boundary,
    )


# ---------------------------------------------------------------------------
# Handoff packet builder
# ---------------------------------------------------------------------------


def build_handoff_packet(
    *,
    name: str,
    dimensions: Optional[Mapping[str, str]] = None,
    color_tokens: Sequence[str] = (),
    view_box: Optional[str] = None,
    is_photographic: bool = False,
    is_complex_illustration: bool = False,
    svg_estimated_bytes: Optional[int] = None,
    svg_source_path: Optional[str] = None,
) -> HandoffPacket:
    """Compose a designer→frontend handoff for *name*.

    When the name is invalid, returns a packet with ``blockers``
    populated so the caller can refuse the handoff.
    """

    validation = validate_asset_name(name)
    if not validation.valid:
        return HandoffPacket(
            name=name,
            surface=validation.surface or "",
            intent=validation.intent or "",
            blockers=validation.failure_reasons,
        )

    surface = validation.surface or ""
    intent = validation.intent or ""
    format_rec = recommend_format(
        surface=surface,
        is_photographic=is_photographic,
        is_complex_illustration=is_complex_illustration,
        svg_estimated_bytes=svg_estimated_bytes,
    )

    blockers: list = []
    # Stage-1 §3.2 — design tokens must be names, not hex literals.
    for token in color_tokens:
        if token and re.match(r"^#[0-9A-Fa-f]{3,8}$", token.strip()):
            blockers.append("hex_color_literal")
            break

    return HandoffPacket(
        name=name,
        surface=surface,
        intent=intent,
        dimensions=dict(dimensions or {}),
        color_tokens=tuple(color_tokens),
        view_box=view_box,
        svg_source_path=svg_source_path,
        format_recommendation=dict(format_rec.to_dict()),
        blockers=tuple(blockers),
    )


__all__ = (
    "APPLE_TOUCH_ICON_SIZE",
    "AssetValidation",
    "FAVICON_RASTER_SIZES",
    "FormatRecommendation",
    "HandoffPacket",
    "OG_IMAGE_SIZE",
    "SURFACES",
    "SURFACE_FAVICON",
    "SURFACE_ICON",
    "SURFACE_ILLUSTRATION",
    "SURFACE_LOGO",
    "SVG_RASTER_FALLBACK_BYTES",
    "build_handoff_packet",
    "recommend_format",
    "validate_asset_name",
)
