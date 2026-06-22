"""forgekit-goal — ForgeKit goal plane (GW1).

The spine that turns ForgeKit from a bag of capabilities into a control plane
that manages its own long-term goals: a persisted ``Goal`` model with status
transitions, append-only evidence, child-goal trees, and work-packet linkage.

GW1 scope is **model + transitions + store**; GW-EXEC adds **planning** (pure
decomposition + progress + continuation rules). The autonomous tick/collect/
propose/approve/execute/verify loop is GW4 (``forgekit_runtime``), and the
``/goal`` operator surface is GW5 (``forgekit_console``). Nothing here executes
anything or talks to providers — by design (honest boundary): planning only
creates plan records and derives progress from append-only evidence.

Roadmap/acceptance: ``docs/forgekit-goal-roadmap.md``.
"""

from __future__ import annotations

from . import models, planning, store, transitions
from .models import EvidenceRecord, Goal, GoalStatus
from .planning import (
    ContinuationAction,
    GoalProgress,
    PlanStep,
    continuation_action,
    decompose,
    is_goal_complete,
    progress,
)
from .store import GoalStore
from .transitions import InvalidTransition

__all__ = (
    "models",
    "planning",
    "store",
    "transitions",
    "Goal",
    "GoalStatus",
    "EvidenceRecord",
    "GoalStore",
    "InvalidTransition",
    "PlanStep",
    "GoalProgress",
    "ContinuationAction",
    "decompose",
    "progress",
    "continuation_action",
    "is_goal_complete",
)
