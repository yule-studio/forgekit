"""Forgekit console test package — ensures the app src is importable.

The forgekit_console package lives under apps/forgekit-console/src; after
``pip install -e .`` it's on the path via the editable finder, but inserting it
here lets the suite run before a reinstall too.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "apps" / "forgekit-console" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
