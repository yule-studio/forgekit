"""Compatibility shim — the planning domain moved to ``apps/planning-agent``.

The canonical home is now the ``yule_planning`` package
(``apps/planning-agent/src/yule_planning``). This package re-exports its public
surface so existing imports such as::

    from yule_orchestrator.planning import build_daily_plan
    from yule_orchestrator.planning.planner import build_daily_plan

keep working. The submodules under this package are thin ``sys.modules`` aliases
to the matching ``yule_planning`` modules (identity preserved). New code should
import from ``yule_planning`` directly.

Transitional note: ``yule_planning`` still imports shared infrastructure
(``yule_orchestrator.core`` / ``.integrations`` / ``.storage``) that has not yet
been extracted to ``packages/*``. That ``apps → monolith`` edge is temporary and
collapses once the shared libs are packaged.
"""

from yule_planning import (
    PlanningBlockBriefing,
    DailyPlan,
    DailyPlanEnvelope,
    PlanningCheckpoint,
    PlanningExecutionBlock,
    PlanningInputs,
    PlanningScheduledBriefing,
    PlanningSourceStatus,
    PlanningTaskCandidate,
    PlanningTimeBlock,
    ReminderItem,
    DailyPlanSnapshot,
    build_daily_plan,
    build_planning_inputs,
    collect_planning_inputs,
    load_daily_plan_snapshot,
    load_reminder_items,
    render_daily_plan,
    save_daily_plan_snapshot,
    select_due_checkpoints,
)

__all__ = [
    "PlanningBlockBriefing",
    "DailyPlan",
    "DailyPlanEnvelope",
    "PlanningCheckpoint",
    "PlanningExecutionBlock",
    "PlanningInputs",
    "PlanningScheduledBriefing",
    "PlanningSourceStatus",
    "PlanningTaskCandidate",
    "PlanningTimeBlock",
    "ReminderItem",
    "DailyPlanSnapshot",
    "build_daily_plan",
    "build_planning_inputs",
    "collect_planning_inputs",
    "load_daily_plan_snapshot",
    "load_reminder_items",
    "render_daily_plan",
    "save_daily_plan_snapshot",
    "select_due_checkpoints",
]
