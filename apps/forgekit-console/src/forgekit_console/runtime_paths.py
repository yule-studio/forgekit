"""Forgekit runtime paths — the installed product's data home. Pure, stdlib.

Everything forgekit persists (personal brain, starter pack, setup config) lives
under a single home dir so an install is self-contained and removable. Default is
``~/.forgekit``; ``FORGEKIT_HOME`` overrides it. Every helper takes an optional
``env`` mapping so tests point the whole tree at a tempdir — nothing here ever
writes; callers create dirs explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

ENV_HOME = "FORGEKIT_HOME"
_DEFAULT_HOME = "~/.forgekit"


def forgekit_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """The forgekit data home (``$FORGEKIT_HOME`` or ``~/.forgekit``)."""

    source = env if env is not None else os.environ
    raw = (source.get(ENV_HOME) or "").strip() or _DEFAULT_HOME
    return Path(os.path.expanduser(raw)).resolve()


def brain_root(env: Optional[Mapping[str, str]] = None) -> Path:
    return forgekit_home(env) / "brain"


def personal_brain_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """Read-write personal brain — the default write target."""

    return brain_root(env) / "personal"


def starter_pack_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """Read-only starter/shared brain pack (built from a source vault)."""

    return brain_root(env) / "starter"


def config_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """The forgekit setup config (provider/policy/brain selections)."""

    return forgekit_home(env) / "config.json"


__all__ = (
    "ENV_HOME",
    "forgekit_home",
    "brain_root",
    "personal_brain_dir",
    "starter_pack_dir",
    "config_path",
)
