"""Persistent goal store (GW1) — one JSON file per goal under the forgekit home.

Goals live at ``<forgekit_home>/goals/<goal-id>.json`` (``forgekit_home`` from
``forgekit_config.paths`` — ``$FORGEKIT_HOME`` or ``~/.forgekit``). Persistence
properties that matter for a control plane that survives restarts:

- **Round-trip.** ``save`` then ``load`` returns an equal ``Goal`` — including
  status, child tree, packet links, and append-only evidence. This is what makes
  primary/long-term goals "actually saved and restored after re-run".
- **Atomic write.** We write to a temp file in the same dir and ``os.replace``
  it into place, so a crash mid-write never leaves a half-written goal file.
- **Versioned.** Each file carries ``schema_version``; loading a newer version
  than this build supports fails loudly (``models.Goal.from_dict``) instead of
  silently dropping fields.

Pure-ish: only depends on ``forgekit-config`` (paths) and the stdlib. The store
creates its own directory; nothing else writes outside the forgekit home.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Mapping, Optional

from forgekit_config import paths as fk_paths

from .models import Goal


def goals_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """Directory holding one ``<goal-id>.json`` per goal. Caller-agnostic."""

    return fk_paths.forgekit_home(env) / "goals"


class GoalStore:
    """File-backed goal store. Construct with a ``root`` (test tempdir) or an
    ``env`` mapping (so ``$FORGEKIT_HOME`` points the whole tree at a tempdir),
    or neither to use the real ``~/.forgekit/goals``.
    """

    def __init__(
        self,
        root: Optional[Path] = None,
        *,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._dir = Path(root) if root is not None else goals_dir(env)

    @property
    def directory(self) -> Path:
        return self._dir

    def _path(self, goal_id: str) -> Path:
        goal_id = (goal_id or "").strip()
        if not goal_id or "/" in goal_id or os.sep in goal_id or goal_id in (".", ".."):
            raise ValueError(f"unsafe goal id {goal_id!r}")
        return self._dir / f"{goal_id}.json"

    def save(self, goal: Goal) -> Path:
        """Persist ``goal`` atomically; returns the file path."""

        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._path(goal.id)
        payload = json.dumps(goal.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return target

    def exists(self, goal_id: str) -> bool:
        return self._path(goal_id).exists()

    def load(self, goal_id: str) -> Goal:
        path = self._path(goal_id)
        if not path.exists():
            raise KeyError(f"no goal {goal_id!r} at {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Goal.from_dict(raw)

    def get(self, goal_id: str) -> Optional[Goal]:
        try:
            return self.load(goal_id)
        except KeyError:
            return None

    def load_all(self) -> List[Goal]:
        """All goals, newest-updated first. Missing dir → empty list."""

        if not self._dir.exists():
            return []
        goals: List[Goal] = []
        for path in sorted(self._dir.glob("*.json")):
            if path.name.startswith("."):
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            goals.append(Goal.from_dict(raw))
        goals.sort(key=lambda g: g.updated_at, reverse=True)
        return goals

    def delete(self, goal_id: str) -> bool:
        path = self._path(goal_id)
        if path.exists():
            path.unlink()
            return True
        return False


__all__ = ("GoalStore", "goals_dir")
