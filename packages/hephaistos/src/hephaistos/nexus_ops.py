"""Nexus connection ops — persist / clear the ``nexus_root`` in the console config.

The READ path (:mod:`hephaistos.nexus_read`) decides status from ``FORGEKIT_NEXUS_ROOT``
(env) or ``config['nexus_root']``. This module is the operator-driven WRITE seam so
``/nexus set <path>`` actually connects Nexus (persists ``nexus_root`` into the same
``config.json`` the provider surface uses) and ``/nexus clear`` disconnects it.

It never fakes a connection: after writing, it reports the REAL resulting status
(``exists`` / ``missing`` / ``blocked``) computed by :func:`nexus_read.connection_status`,
so setting a not-yet-cloned path honestly shows ``missing``, not ``connected``.

Pure stdlib (json + Path) → testable in the bare CI install.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Optional, Tuple

from forgekit_config.paths import config_path
from . import nexus_read as nx


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def apply_set_root(path_str: str, *, env: Optional[Mapping[str, str]] = None,
                   config_file: Optional[Path] = None) -> Tuple[bool, str]:
    """Persist ``nexus_root`` and report the HONEST resulting connection status."""

    raw = (path_str or "").strip()
    if not raw:
        return False, "경로를 입력하세요 — `/nexus set <nexus repo 경로>`"
    p = Path(config_file) if config_file is not None else config_path(env)
    data = _load(p)
    data["nexus_root"] = raw
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return False, f"config write 실패: {exc}"
    cs = nx.connection_status(env, {"nexus_root": raw})
    # honest: 'exists' means a real readable root; 'missing'/'blocked' say so plainly.
    return True, f"nexus_root = {raw} (저장됨) · 상태: [{cs['status']}] {cs['reason']}"


def apply_clear_root(*, env: Optional[Mapping[str, str]] = None,
                     config_file: Optional[Path] = None) -> Tuple[bool, str]:
    """Remove ``nexus_root`` from config → back to not_connected."""

    p = Path(config_file) if config_file is not None else config_path(env)
    data = _load(p)
    if "nexus_root" not in data:
        return True, "nexus_root 가 이미 설정되어 있지 않습니다 (not_connected)"
    data.pop("nexus_root", None)
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return False, f"config write 실패: {exc}"
    return True, "nexus_root 해제됨 — not_connected (지식 source 미연결)"


__all__ = ("apply_set_root", "apply_clear_root")
