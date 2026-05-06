from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def ensure_src_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_path = repo_root / "src"
    resolved = str(src_path)
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


def isolate_default_cache_db() -> None:
    """Point YULE_CACHE_DB_PATH at a per-process temp file by default.

    The repo's ``.cache/yule/cache.sqlite3`` carries real workflow
    sessions from operator runs; tests that don't manage their own
    cache (e.g. router behaviour tests) would otherwise see those
    sessions and route differently. Tests that need a specific cache
    set this env var themselves and the per-process default just keeps
    the live operator data out of test land.
    """

    if os.environ.get("YULE_CACHE_DB_PATH"):
        return  # operator already set it (e.g. e2e tests with their own dir)
    handle = tempfile.NamedTemporaryFile(
        prefix="yule-tests-cache-",
        suffix=".sqlite3",
        delete=False,
    )
    handle.close()
    os.environ["YULE_CACHE_DB_PATH"] = handle.name


ensure_src_on_path()
isolate_default_cache_db()
