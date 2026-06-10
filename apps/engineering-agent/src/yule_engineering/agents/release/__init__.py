"""Release-time decision modules (F7 / #98).

This package owns *post-implementation, pre-merge* decisions —
specifically the auto-merge decider that classifies a PR's risk
class and evaluates the 8 conventions §5 auto-merge conditions
deterministically.

The decider is intentionally a **pure data → verdict** module:
no GitHub I/O, no clock reads, no env reads beyond the cycle
authorization flag. Callers (gh-cli adapters, the tech-lead
agent, ops review) build :class:`PrDiffSummary` /
:class:`PrMetadata` from their own data sources and feed them
into :func:`evaluate_auto_merge`. That keeps the policy
auditable and unit-testable in isolation while live integration
lives in a thin adapter layer that we will land in a follow-up
PR.
"""

from __future__ import annotations

from .auto_merge_decider import (
    AutoMergeVerdict,
    ENV_AUTOMERGE_CYCLE,
    PROTECTED_BRANCHES,
    PrDiffSummary,
    PrMetadata,
    RiskClass,
    RiskSignal,
    classify_risk,
    evaluate_auto_merge,
    is_cycle_authorized_from_env,
    record_automerge_signature,
)


__all__ = (
    "AutoMergeVerdict",
    "ENV_AUTOMERGE_CYCLE",
    "PROTECTED_BRANCHES",
    "PrDiffSummary",
    "PrMetadata",
    "RiskClass",
    "RiskSignal",
    "classify_risk",
    "evaluate_auto_merge",
    "is_cycle_authorized_from_env",
    "record_automerge_signature",
)
