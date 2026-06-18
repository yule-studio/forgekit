"""PM intake → gateway → tech-lead handoff (WT2).

Forgekit must not throw a raw ask straight at implementation. This package closes
the path: a vague request ("bkurs-fe / bkurs-be 완성해줘") becomes a structured
**ProductIntentPacket** (reusing the existing ``yule_engineering`` product-intake
engine — implied features, recommended defaults, decision questions, readiness),
which the **gateway** forwards to **tech-lead**, who **splits** it into per-role
tasks (FE / BE / DevOps / QA / Security). Each hop records an authorship trace
(who / role / phase / from → to), and areas with no execution permission (deploy /
IAM / infra apply) are surfaced as ``blocked`` tasks that need an operator + a
runbook — never faked as done.

The intake engine is reused when importable (it is, via the root install); a small
local fallback keeps the contract working in a minimal env.
"""

from __future__ import annotations

from .packet import (
    PHASE_INTAKE,
    PHASE_GATEWAY,
    PHASE_TECH_LEAD,
    ROLE_TASK_BLOCKED,
    ROLE_TASK_READY,
    HandoffTrace,
    RoleTask,
    TechLeadSplit,
    Handoff,
)
from .gateway import intake_packet, forward_to_tech_lead, tech_lead_split, run_handoff

__all__ = (
    "PHASE_INTAKE",
    "PHASE_GATEWAY",
    "PHASE_TECH_LEAD",
    "ROLE_TASK_BLOCKED",
    "ROLE_TASK_READY",
    "HandoffTrace",
    "RoleTask",
    "TechLeadSplit",
    "Handoff",
    "intake_packet",
    "forward_to_tech_lead",
    "tech_lead_split",
    "run_handoff",
)
