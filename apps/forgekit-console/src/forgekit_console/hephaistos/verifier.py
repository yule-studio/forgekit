"""Loadout / weapon readiness verifier — checks the local env, no install. Pure-ish.

``verify_loadout`` looks up the loadout's required weapons and probes each with an
injected ``which`` (defaults to :func:`shutil.which` on the weapon's verify binary),
returning a structured readiness verdict (ready / partial / missing / blocked) with
next steps. It never installs anything — install is an explicit, planned seam.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

from . import armory

READY = "ready"
PARTIAL = "partial"
MISSING = "missing"
BLOCKED = "blocked"        # unknown loadout


@dataclass(frozen=True)
class LoadoutReadiness:
    loadout: str
    status: str
    present: Tuple[str, ...] = ()
    missing: Tuple[str, ...] = ()
    next_steps: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"loadout": self.loadout, "status": self.status, "present": list(self.present),
                "missing": list(self.missing), "next_steps": list(self.next_steps)}


def _binary(verify_command: str) -> str:
    return (verify_command or "").strip().split(" ")[0]


def verify_loadout(loadout_id: str, *, which: Optional[Callable[[str], Optional[str]]] = None
                   ) -> LoadoutReadiness:
    which = which or shutil.which
    lo = armory.loadout(loadout_id)
    if lo is None:
        return LoadoutReadiness(loadout_id, BLOCKED, next_steps=(f"알 수 없는 loadout: {loadout_id}",))
    present, missing, steps = [], [], []
    for wid in lo.required_weapons:
        w = armory.weapon(wid)
        binary = _binary(w.verify_command) if w else wid
        if binary and which(binary):
            present.append(wid)
        else:
            missing.append(wid)
            if w and w.install_hint:
                steps.append(f"{w.display_name} 설치: {w.install_hint}")
    if not missing:
        status = READY
    elif present:
        status = PARTIAL
    else:
        status = MISSING
    return LoadoutReadiness(loadout_id, status, tuple(present), tuple(missing), tuple(steps))


def readiness_lines(r: LoadoutReadiness) -> Tuple[str, ...]:
    lines = [f"loadout {r.loadout}: {r.status}",
             f"  present: {', '.join(r.present) or '-'}",
             f"  missing: {', '.join(r.missing) or '-'}"]
    lines += [f"  → {s}" for s in r.next_steps]
    return tuple(lines)


__all__ = ("READY", "PARTIAL", "MISSING", "BLOCKED", "LoadoutReadiness",
           "verify_loadout", "readiness_lines")
