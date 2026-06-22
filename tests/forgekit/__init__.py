"""Forgekit console test package — ensures the app src is importable.

The forgekit_console package lives under apps/forgekit-console/src; after
``pip install -e .`` it's on the path via the editable finder, but inserting it
here lets the suite run before a reinstall too.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "apps" / "forgekit-console" / "src"
# The console src plus EVERY sibling package src (packages/*/src). Inserting them lets the
# WHOLE suite run before a `pip install -e` reinstall — without this, modules that lazily
# import a package (e.g. /self-improve → forgekit_runtime.selfimprove.goal_tick →
# forgekit_goal, or /toolchain → forgekit_toolchain) fail collection with ModuleNotFoundError.
_PKG_SRCS = sorted(str(p) for p in (_ROOT / "packages").glob("*/src") if p.is_dir())
for _p in (str(_SRC), *_PKG_SRCS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
