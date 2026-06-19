"""Env-aware brain orchestration — the surface CLI / console / setup call.

Thin glue over :mod:`brain.personal` + :mod:`brain.pack` that resolves paths from
:mod:`runtime_paths` (so a default install targets ``~/.forgekit`` and tests point
``FORGEKIT_HOME`` at a tempdir). Holds no policy of its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from forgekit_config import paths as runtime_paths
from . import pack as pack_mod
from . import personal as personal_mod
from .models import PackManifest


def ensure_personal_brain(
    env: Optional[Mapping[str, str]] = None, *, created_at: str = "1970-01-01T00:00:00Z"
) -> personal_mod.PersonalBrain:
    """Auto-create the personal brain under the forgekit home. Idempotent."""

    return personal_mod.init_personal_brain(
        runtime_paths.personal_brain_dir(env), created_at=created_at
    )


def build_pack_from(
    source: Path,
    env: Optional[Mapping[str, str]] = None,
    *,
    built_at: str = "1970-01-01T00:00:00Z",
) -> PackManifest:
    """Build the read-only starter pack from *source* into the forgekit home."""

    return pack_mod.build_starter_pack(
        Path(source), runtime_paths.starter_pack_dir(env), built_at=built_at
    )


def brain_status(env: Optional[Mapping[str, str]] = None) -> dict:
    """A combined brain status for CLI / console / doctor."""

    personal_dir = runtime_paths.personal_brain_dir(env)
    brain = personal_mod.open_personal_brain(personal_dir)
    starter = pack_mod.pack_status(runtime_paths.starter_pack_dir(env))
    return {
        "personal_path": str(personal_dir),
        "personal_initialized": brain is not None,
        "personal_notes": brain.stats()["total"] if brain else 0,
        "starter_path": str(runtime_paths.starter_pack_dir(env)),
        "starter_built": starter is not None,
        "starter": starter,
    }


__all__ = ("ensure_personal_brain", "build_pack_from", "brain_status")
