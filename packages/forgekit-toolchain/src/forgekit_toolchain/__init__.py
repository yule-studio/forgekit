"""forgekit-toolchain — language/framework/runtime version switching (control plane P0).

Repo-local version detection (``.tool-versions`` / ``.mise.toml`` / ``.nvmrc`` …),
loadout → toolchain profile, and mise-backed switch / verify / drift. ``mise`` is the
first-class manager. Destructive / global writes are approval-gated; the switch path
NEVER fakes — with no manager it refuses honestly. See ``docs/control-plane-architecture.md`` §5.2.

Layering: ``models`` (vocab) → ``detect`` (manifests) / ``profile`` (loadout) →
``manager`` (mise seam) → ``plan`` (plan/verify) → ``surface`` (console lines + gate).
"""

from __future__ import annotations

from . import detect, manager, models, plan, profile, surface

__all__ = ("models", "detect", "profile", "manager", "plan", "surface")
