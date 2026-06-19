"""Daemon heartbeat + kill switch (WT4) — operator-visible runtime state.

The long-running runtime writes a heartbeat JSON each tick (status / tick / ts / pid)
so ``forgekit runtime status`` can report liveness, and reads a kill-switch file so an
operator can stop it without signals. Pure stdlib; paths under FORGEKIT_HOME/state.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from forgekit_config.paths import state_dir

STATUS_RUNNING = "running"
STATUS_IDLE = "idle"
STATUS_STOPPED = "stopped"


def heartbeat_path(env: Optional[Mapping[str, str]] = None) -> Path:
    return state_dir(env) / "runtime-heartbeat.json"


def kill_switch_path(env: Optional[Mapping[str, str]] = None) -> Path:
    return state_dir(env) / "runtime.kill"


@dataclass(frozen=True)
class Heartbeat:
    status: str = STATUS_STOPPED
    tick: int = 0
    ts: str = ""
    pid: int = 0
    note: str = ""

    @property
    def alive(self) -> bool:
        return self.status in (STATUS_RUNNING, STATUS_IDLE)

    def to_dict(self) -> dict:
        return {"status": self.status, "tick": self.tick, "ts": self.ts,
                "pid": self.pid, "note": self.note}


def write_heartbeat(hb: Heartbeat, *, path: Optional[Path] = None,
                    env: Optional[Mapping[str, str]] = None) -> bool:
    p = path or heartbeat_path(env)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(hb.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def read_heartbeat(*, path: Optional[Path] = None,
                   env: Optional[Mapping[str, str]] = None) -> Heartbeat:
    p = path or heartbeat_path(env)
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Heartbeat(status=STATUS_STOPPED, note="no heartbeat")
    return Heartbeat(status=d.get("status", STATUS_STOPPED), tick=int(d.get("tick", 0) or 0),
                     ts=d.get("ts", ""), pid=int(d.get("pid", 0) or 0), note=d.get("note", ""))


def is_killed(*, path: Optional[Path] = None, env: Optional[Mapping[str, str]] = None) -> bool:
    return (path or kill_switch_path(env)).exists()


def request_kill(*, path: Optional[Path] = None, env: Optional[Mapping[str, str]] = None) -> bool:
    p = path or kill_switch_path(env)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("kill\n", encoding="utf-8")
        return True
    except OSError:
        return False


def clear_kill(*, path: Optional[Path] = None, env: Optional[Mapping[str, str]] = None) -> None:
    try:
        (path or kill_switch_path(env)).unlink()
    except OSError:
        pass


__all__ = (
    "STATUS_RUNNING", "STATUS_IDLE", "STATUS_STOPPED",
    "heartbeat_path", "kill_switch_path", "Heartbeat",
    "write_heartbeat", "read_heartbeat", "is_killed", "request_kill", "clear_kill",
)
