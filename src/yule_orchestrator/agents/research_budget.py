"""Task-aware research collection budget policy.

Until now the collector loop ran the same budget for a one-line bug
report and a multi-agent architecture review. Operators noticed the
forum getting only ~2 sources per role even on heavy "design X" tasks.
This module classifies a task into a budget tier (small/medium/large/
deep_research) based on prompt keywords + task_type + role_sequence and
exposes a :class:`ResearchBudgetPolicy` the collector loop consumes for
``max_provider_calls`` / ``max_results_per_role`` / per-role minimum
targets.

Operators can still cap costs via ``CollectorConfig.max_provider_calls``
— the policy is a *recommendation* the loop rounds down to that hard
cap. Costs are kept safe by defaulting to ``medium`` and only escalating
to ``large`` / ``deep_research`` on explicit signals (``architecture``,
``RAG``, ``multi-agent``, ``깊게``, ``리서치 먼저``…).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Tuple


TIER_SMALL = "small"
TIER_MEDIUM = "medium"
TIER_LARGE = "large"
TIER_DEEP = "deep_research"


# Keywords that escalate to ``large``. Compiled as a single regex so the
# match is one cheap pass over the prompt.
_LARGE_TIER_KEYWORDS = (
    "architecture",
    "rag",
    "cag",
    "multi-agent",
    "multi agent",
    "multi-bot",
    "agent runtime",
    "agent orchestrat",
    "infra",
    "infrastructure",
    "memory",
    "observabil",
    "deployment",
    "release",
    "리서치",
    "조사",
    "설계",
    "아키텍처",
    "비교",
    "검토",
)

# Stronger phrases that escalate to ``deep_research`` when present.
_DEEP_TIER_KEYWORDS = (
    "deep research",
    "deep dive",
    "thorough",
    "exhaustive",
    "깊게",
    "충분히",
    "자료 많이",
    "리서치 먼저",
    "리서치부터",
    "전부 조사",
    "전체 조사",
)

# Small / quick-fix style prompts stay at ``small`` even when the rest
# of the heuristic would push them up.
_SMALL_TIER_KEYWORDS = (
    "버그",
    "오타",
    "typo",
    "quick fix",
    "간단",
    "minor",
)


@dataclass(frozen=True)
class RoleTarget:
    """Per-role minimum reference target the loop tries to meet."""

    role: str
    min_sources: int


@dataclass(frozen=True)
class ResearchBudgetPolicy:
    """The collector loop's view of how hard to dig for one task.

    ``max_provider_calls`` and ``max_results_per_role`` are the
    iteration-level caps. ``role_targets`` is the per-role minimum
    coverage target the sufficiency loop uses as its stop condition.
    ``tier`` is the human-readable label the forum body / outcome
    metadata surfaces so the operator can see why budget was big or
    small.
    """

    tier: str
    max_provider_calls: int
    max_results_per_role: int
    role_targets: Tuple[RoleTarget, ...] = field(default_factory=tuple)
    reason: str = ""

    def role_target(self, role: str) -> int:
        short = role.split("/", 1)[-1].strip()
        for target in self.role_targets:
            if target.role == short:
                return target.min_sources
        return 0


# Default per-tier budgets — the user-supplied recommended ranges, set
# to the conservative side of each window so cost stays predictable.
_TIER_DEFAULTS: Mapping[str, Tuple[int, int]] = {
    TIER_SMALL: (4, 2),
    TIER_MEDIUM: (8, 3),
    TIER_LARGE: (16, 5),
    TIER_DEEP: (28, 8),
}


# Per-tier scaling for the role-target table. ``small`` shrinks
# ``medium`` targets by 1; ``large`` keeps the medium minimums (the
# extra budget shows up via per-role results); ``deep_research`` adds
# +2 to every role.
_BASE_ROLE_TARGETS: Tuple[RoleTarget, ...] = (
    RoleTarget(role="tech-lead", min_sources=4),
    RoleTarget(role="ai-engineer", min_sources=5),
    RoleTarget(role="backend-engineer", min_sources=4),
    RoleTarget(role="frontend-engineer", min_sources=3),
    RoleTarget(role="product-designer", min_sources=3),
    RoleTarget(role="qa-engineer", min_sources=3),
    RoleTarget(role="devops-engineer", min_sources=4),
)


def _scaled_targets(tier: str) -> Tuple[RoleTarget, ...]:
    delta = {
        TIER_SMALL: -1,
        TIER_MEDIUM: 0,
        TIER_LARGE: 1,
        TIER_DEEP: 3,
    }.get(tier, 0)
    return tuple(
        RoleTarget(role=t.role, min_sources=max(1, t.min_sources + delta))
        for t in _BASE_ROLE_TARGETS
    )


def decide_budget(
    *,
    prompt: str,
    task_type: Optional[str] = None,
    role_sequence: Sequence[str] = (),
    hard_cap_provider_calls: Optional[int] = None,
    hard_cap_results_per_role: Optional[int] = None,
) -> ResearchBudgetPolicy:
    """Classify a task into a budget tier and return its policy.

    Order:
      1. Explicit small-tier keywords (``버그`` / ``typo``) win — never
         escalate a quick-fix to large.
      2. Deep-research phrases (``깊게``, ``deep dive``…) → ``deep_research``.
      3. Architecture/RAG/multi-agent/infra/`리서치`/`설계`/`아키텍처`
         keywords or task_type ``platform-infra`` → ``large``.
      4. Default → ``medium``.

    ``hard_cap_*`` come from ``CollectorConfig.from_env`` so operator
    cost gates always win — the policy never asks for more than the env
    ceiling.
    """

    tier, reason = _classify(prompt=prompt, task_type=task_type)

    base_calls, base_results = _TIER_DEFAULTS[tier]
    if hard_cap_provider_calls is not None and hard_cap_provider_calls > 0:
        max_calls = min(base_calls, hard_cap_provider_calls)
    else:
        max_calls = base_calls
    if hard_cap_results_per_role is not None and hard_cap_results_per_role > 0:
        max_results = min(base_results, hard_cap_results_per_role)
    else:
        max_results = base_results

    return ResearchBudgetPolicy(
        tier=tier,
        max_provider_calls=max_calls,
        max_results_per_role=max_results,
        role_targets=_scaled_targets(tier),
        reason=reason,
    )


def _classify(
    *, prompt: str, task_type: Optional[str]
) -> Tuple[str, str]:
    text = (prompt or "").lower()
    if any(keyword in text for keyword in _SMALL_TIER_KEYWORDS):
        return TIER_SMALL, "small-tier keywords (버그/quick fix/typo)"
    if any(keyword in text for keyword in _DEEP_TIER_KEYWORDS):
        return TIER_DEEP, "deep-research signal in prompt"
    if any(keyword in text for keyword in _LARGE_TIER_KEYWORDS):
        return TIER_LARGE, "architecture/research keyword detected"
    if (task_type or "").lower() in ("platform-infra", "platform_infra"):
        return TIER_LARGE, "task_type=platform-infra"
    return TIER_MEDIUM, "default medium tier (no large/deep signal)"


def role_targets_to_sufficiency_targets(
    policy: ResearchBudgetPolicy,
):
    """Bridge ``ResearchBudgetPolicy.role_targets`` into the
    ``research_sufficiency`` module's ``RoleSufficiencyTarget`` shape.

    Imports lazily so this module stays usable even when the
    sufficiency module is unavailable (partial install).
    """

    try:
        from .research_sufficiency import (
            RoleSufficiencyTarget,
            DEFAULT_ROLE_TARGETS,
        )
    except Exception:  # noqa: BLE001 - defensive
        return ()
    by_role = {t.role: t for t in DEFAULT_ROLE_TARGETS}
    out = []
    for role_target in policy.role_targets:
        existing = by_role.get(role_target.role)
        required_types = existing.required_types if existing else ()
        out.append(
            RoleSufficiencyTarget(
                role=role_target.role,
                min_sources=role_target.min_sources,
                required_types=required_types,
            )
        )
    return tuple(out)
