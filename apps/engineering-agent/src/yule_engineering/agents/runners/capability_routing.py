"""Capability-aware backend routing (provider-capability-matrix.md §5 → code).

The role-runner dispatcher tries backends in a fixed priority order
(``YULE_ROLE_RUNNER_PROVIDERS`` → claude/codex/ollama/deterministic). This
module lets a *task's capability class* reorder that preference so cheap/local
work routes to Ollama, research to Gemini, execution to Codex, and
safety/enforcement to Claude — exactly the matrix §5 policy.

Pure + deterministic: :func:`order_providers` reorders the *available* provider
ids by preference and **never drops** a provider — non-preferred ones keep their
original relative order and ``deterministic`` always stays last (terminal
fallback). Unknown capability class → original order unchanged (no-op).
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence, Tuple

PROVIDER_DETERMINISTIC = "deterministic"

# Capability class → ordered backend preference (matrix §5). Provider ids match
# role_runner.PROVIDER_* and the manifest participants. Gemini is listed for
# forward-compat (no role-runner adapter yet → simply absent from `available`).
CAPABILITY_BACKEND_PREFERENCE: Mapping[str, Tuple[str, ...]] = {
    # cheap / local inference → Ollama first
    "classification": ("ollama", "gemini"),
    "summarization": ("ollama", "gemini"),
    "compaction": ("ollama", "gemini"),
    # research / large-context → Gemini first, Claude backup
    "research": ("gemini", "claude"),
    # execution / tool ops → Codex first, Claude backup
    "execution": ("codex", "claude"),
    "delivery": ("codex", "claude"),
    # safety / audit / enforcement / verification → Claude
    "security_gate": ("claude",),
    "enforcement": ("claude",),
    "verification": ("claude",),
    "memory": ("claude",),
    "exploration": ("claude", "codex"),
}

# Task type → capability class (coarse inference when an explicit class is absent).
TASK_TYPE_TO_CAPABILITY: Mapping[str, str] = {
    "classification": "classification",
    "intent": "classification",
    "routing": "classification",
    "summary": "summarization",
    "summarize": "summarization",
    "compress": "compaction",
    "research": "research",
    "analysis": "research",
    "deploy": "execution",
    "coding": "execution",
    "implementation": "execution",
}


def capability_for(
    *, capability_class: Optional[str] = None, task_type: Optional[str] = None
) -> Optional[str]:
    """Resolve a capability class from an explicit value or a task type."""

    if capability_class:
        cc = capability_class.strip().lower()
        if cc:
            return cc
    if task_type:
        return TASK_TYPE_TO_CAPABILITY.get(task_type.strip().lower())
    return None


def order_providers(
    capability_class: Optional[str], available: Sequence[str]
) -> list[str]:
    """Reorder *available* provider ids by the capability preference.

    Stable + lossless: preferred providers (that are available) come first in
    preference order, the rest keep their original relative order, and
    ``deterministic`` is always pinned last. Unknown/empty capability → original
    order (deterministic still pinned last).
    """

    avail = [p for p in available]
    # always pin the terminal fallback last
    terminal = [p for p in avail if p == PROVIDER_DETERMINISTIC]
    body = [p for p in avail if p != PROVIDER_DETERMINISTIC]

    pref = CAPABILITY_BACKEND_PREFERENCE.get((capability_class or "").strip().lower())
    if not pref:
        return body + terminal

    seen = set()
    ordered: list[str] = []
    for p in pref:
        if p in body and p not in seen:
            ordered.append(p)
            seen.add(p)
    for p in body:  # preserve original order for the rest
        if p not in seen:
            ordered.append(p)
            seen.add(p)
    return ordered + terminal


def capability_from_input(input_: object) -> Optional[str]:
    """Best-effort capability class from a RoleRunnerInput's metadata.

    Reads ``metadata['capability_class']`` first, then infers from
    ``metadata['task_type']``. Returns None when nothing is declared (→ no-op
    routing, original order preserved).
    """

    md = getattr(input_, "metadata", None) or {}
    if not isinstance(md, Mapping):
        return None
    return capability_for(
        capability_class=_opt_str(md.get("capability_class")),
        task_type=_opt_str(md.get("task_type")),
    )


def _opt_str(value: object) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


# Opt-in flag for live capability-aware routing on the gateway dispatch path.
ENV_CAPABILITY_ROUTING = "YULE_CAPABILITY_ROUTING_ENABLED"


def capability_routing_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    import os

    env_map = env if env is not None else os.environ
    return (env_map.get(ENV_CAPABILITY_ROUTING) or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def build_capability_provider_router():
    """Return a ``ProviderRouter`` (``(input_, available) -> ordered``).

    Reorders the available backends by the input's capability class. No-op
    ordering (original order) when the input declares no capability.
    """

    def _router(input_: object, available: Sequence[str]) -> Sequence[str]:
        return order_providers(capability_from_input(input_), available)

    return _router


# ---------------------------------------------------------------------------
# Rule-first LLM minimization (layered on top of capability routing)
# ---------------------------------------------------------------------------

# Opt-in flag for resolution-aware routing (rule_first → skip live LLM). Supersedes
# capability routing when on; both default off.
ENV_LLM_MINIMIZATION = "YULE_LLM_MINIMIZATION_ENABLED"


def llm_minimization_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    import os

    env_map = env if env is not None else os.environ
    return (env_map.get(ENV_LLM_MINIMIZATION) or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def order_providers_for_resolution(
    capability_class: Optional[str], available: Sequence[str], *, resolution_mode: str
) -> list[str]:
    """Order backends by capability preference, then apply the resolution mode.

    * ``rule_first``  → pin ``deterministic`` FIRST so the rule/deterministic
      path resolves and live providers are skipped (deterministic still produces
      a take; live is only reached if it somehow doesn't).
    * ``llm_optional`` / ``llm_required`` → capability preference order
      (cheap-first / strong-first), ``deterministic`` last.
    """

    base = order_providers(capability_class, available)
    from .role_runner import PROVIDER_DETERMINISTIC as _DET  # local id

    if resolution_mode == "rule_first" and _DET in base:
        return [_DET] + [p for p in base if p != _DET]
    return base


def build_resolution_provider_router():
    """Return a ``ProviderRouter`` that applies rule-first LLM minimization.

    Resolves the input's capability class → resolution mode (lazy-importing the
    policy) and orders providers accordingly. rule_first inputs route to the
    deterministic/rule path first (live LLM skipped); others keep capability
    ordering. No capability / unknown → llm_required default (unchanged order).
    """

    def _router(input_: object, available: Sequence[str]) -> Sequence[str]:
        from ..harness.llm_minimization import resolve_from_metadata

        cc = capability_from_input(input_)
        md = getattr(input_, "metadata", None) or {}
        decision = resolve_from_metadata(md if isinstance(md, Mapping) else {}, cc)
        return order_providers_for_resolution(
            cc, available, resolution_mode=decision.resolution_mode
        )

    return _router


__all__ = (
    "CAPABILITY_BACKEND_PREFERENCE",
    "TASK_TYPE_TO_CAPABILITY",
    "ENV_CAPABILITY_ROUTING",
    "ENV_LLM_MINIMIZATION",
    "capability_for",
    "order_providers",
    "order_providers_for_resolution",
    "capability_from_input",
    "capability_routing_enabled",
    "llm_minimization_enabled",
    "build_capability_provider_router",
    "build_resolution_provider_router",
)
