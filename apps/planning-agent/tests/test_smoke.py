"""Smoke tests for the relocated ``yule_planning`` app package.

Verifies the package imports standalone and that the old
``yule_planning`` path still resolves to the SAME objects via the
compatibility shims.
"""

from __future__ import annotations

import yule_planning


def test_public_surface_present() -> None:
    for name in (
        "build_daily_plan",
        "build_planning_inputs",
        "render_daily_plan",
        "select_due_checkpoints",
        "DailyPlan",
        "PlanningInputs",
        "DailyPlanSnapshot",
    ):
        assert hasattr(yule_planning, name), name


def test_legacy_path_aliases_same_objects() -> None:
    from yule_planning import build_daily_plan, DailyPlan
    from yule_planning import models, planner

    assert build_daily_plan is yule_planning.build_daily_plan
    assert DailyPlan is yule_planning.DailyPlan
    # submodule shims are sys.modules aliases → identical module objects
    assert models is yule_planning.models
    assert planner.build_daily_plan is yule_planning.planner.build_daily_plan
