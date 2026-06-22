"""forgekit-goal — ForgeKit goal plane (GW1).

The spine that turns ForgeKit from a bag of capabilities into a control plane
that manages its own long-term goals: a persisted ``Goal`` model with status
transitions, append-only evidence, child-goal trees, and work-packet linkage.

GW1 scope is **model + transitions + store only**. The autonomous tick/collect/
propose/approve/execute/verify loop is GW4 (``forgekit_runtime``), and the
``/goal`` operator surface is GW5 (``forgekit_console``). Nothing here executes
anything or talks to providers — by design (honest boundary).

Roadmap/acceptance: ``docs/forgekit-goal-roadmap.md``.
"""

from __future__ import annotations

from . import models, store, transitions
from .models import EvidenceRecord, Goal, GoalStatus
from .store import GoalStore
from .transitions import InvalidTransition

__all__ = (
    "models",
    "store",
    "transitions",
    "Goal",
    "GoalStatus",
    "EvidenceRecord",
    "GoalStore",
    "InvalidTransition",
)
