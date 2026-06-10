"""Env-driven configuration for the autonomous research collector.

Extracted from ``collector.py`` so the core file keeps a thin
orchestration surface. This module owns the **config parsing**
responsibility: the env var names, provider identifiers / default
candidate lists, the forum-comment-mode resolver, the resolved
:class:`CollectorConfig` dataclass and its ``from_env`` parser, plus the
small env-coercion helpers (``_truthy`` / ``_positive_int`` /
``_strip_or_none`` / ``_parse_provider_list``).

This is a leaf module — it imports nothing from the collector core, so
``collector`` (and the provider/mock/format/loop siblings) can import
these symbols without any cycle. ``collector`` re-exports everything here
so the public surface is unchanged for callers and tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Tuple


ENV_AUTO_COLLECT_ENABLED = "ENGINEERING_RESEARCH_AUTO_COLLECT_ENABLED"
ENV_PROVIDER = "ENGINEERING_RESEARCH_PROVIDER"
ENV_PROVIDERS = "ENGINEERING_RESEARCH_PROVIDERS"  # auto-mode candidate list
ENV_MAX_RESULTS = "ENGINEERING_RESEARCH_MAX_RESULTS"
ENV_MAX_PROVIDER_CALLS = "ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS"
ENV_MAX_RESULTS_PER_ROLE = "ENGINEERING_RESEARCH_MAX_RESULTS_PER_ROLE"
ENV_FORUM_COMMENT_MODE = "ENGINEERING_RESEARCH_FORUM_COMMENT_MODE"


# Forum comment publishing modes:
# - "member-bots" (default): gateway posts the forum thread + one
#   research-open directive, and each member bot adds its own role
#   comment from its own account so the team feels real.
# - "gateway": legacy fallback — gateway posts every role comment
#   itself. Used during Phase 1 / when member bots aren't booted.
FORUM_COMMENT_MODE_MEMBER_BOTS = "member-bots"
FORUM_COMMENT_MODE_GATEWAY = "gateway"
FORUM_COMMENT_MODES: Tuple[str, ...] = (
    FORUM_COMMENT_MODE_MEMBER_BOTS,
    FORUM_COMMENT_MODE_GATEWAY,
)
DEFAULT_FORUM_COMMENT_MODE = FORUM_COMMENT_MODE_MEMBER_BOTS


def resolve_forum_comment_mode(env: Optional[Mapping[str, str]] = None) -> str:
    """Return ``"member-bots"`` or ``"gateway"`` from env, with safe fallback."""

    env_map: Mapping[str, str] = env if env is not None else os.environ
    raw = (env_map.get(ENV_FORUM_COMMENT_MODE) or "").strip().lower()
    if raw in FORUM_COMMENT_MODES:
        return raw
    return DEFAULT_FORUM_COMMENT_MODE

ENV_TAVILY_API_KEY = "TAVILY_API_KEY"
ENV_BRAVE_API_KEY = "BRAVE_SEARCH_API_KEY"


PROVIDER_MOCK = "mock"
PROVIDER_TAVILY = "tavily"
PROVIDER_BRAVE = "brave"
PROVIDER_AUTO = "auto"
# ``multi`` is accepted as an alias for ``auto`` so operators who think in
# "multi-provider" terms still get the same behaviour.
PROVIDER_MULTI = "multi"
KNOWN_PROVIDERS: Tuple[str, ...] = (
    PROVIDER_MOCK,
    PROVIDER_TAVILY,
    PROVIDER_BRAVE,
    PROVIDER_AUTO,
    PROVIDER_MULTI,
)
# Single-provider modes (i.e. not ``auto``/``multi``).
SINGLE_PROVIDER_MODES: Tuple[str, ...] = (PROVIDER_MOCK, PROVIDER_TAVILY, PROVIDER_BRAVE)
# External (network) providers we know how to dispatch in auto mode.
EXTERNAL_PROVIDERS: Tuple[str, ...] = (PROVIDER_TAVILY, PROVIDER_BRAVE)
# Default candidate set when ``ENGINEERING_RESEARCH_PROVIDERS`` is blank.
DEFAULT_AUTO_PROVIDERS: Tuple[str, ...] = (PROVIDER_TAVILY, PROVIDER_BRAVE)


# Per-role provider preference for ``auto`` / ``multi`` mode. The ordering
# matters: providers earlier in the tuple are queried first, and budget
# pressure stops the chain at the position the operator can afford.
#
# Trade-off:
# - Tavily ranks AI/RAG/agent material and synthesizes well — preferred for
#   tech-lead synthesis and ai-engineer.
# - Brave ranks official docs / GitHub / latest community signal — preferred
#   for backend / frontend / qa / devops / product-designer benchmarks.
DEFAULT_ROLE_PROVIDER_POLICY: Mapping[str, Tuple[str, ...]] = {
    # Gateway uses local memory; external search is opt-in. Returning ``()``
    # makes auto mode skip provider calls for this role entirely.
    "gateway": (),
    "tech-lead": (PROVIDER_TAVILY, PROVIDER_BRAVE),
    "ai-engineer": (PROVIDER_TAVILY, PROVIDER_BRAVE),
    "backend-engineer": (PROVIDER_BRAVE, PROVIDER_TAVILY),
    "frontend-engineer": (PROVIDER_BRAVE, PROVIDER_TAVILY),
    "product-designer": (PROVIDER_BRAVE, PROVIDER_TAVILY),
    "qa-engineer": (PROVIDER_BRAVE, PROVIDER_TAVILY),
    "devops-engineer": (PROVIDER_BRAVE, PROVIDER_TAVILY),
}

DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_PROVIDER_CALLS = 3
DEFAULT_MAX_RESULTS_PER_ROLE = 5


@dataclass(frozen=True)
class CollectorConfig:
    """Resolved env config for the auto-collector.

    ``enabled=False`` means "skip collection entirely; jump straight to
    the user-input fallback". ``provider`` and ``max_results`` are still
    resolved so observability commands can show the operator what would
    happen if they flipped the flag.

    ``provider="auto"`` (or ``"multi"``) activates the multi-provider
    composite: each role's query is dispatched to the providers listed in
    ``providers`` (default ``("tavily", "brave")``) following the per-role
    priority defined by :data:`DEFAULT_ROLE_PROVIDER_POLICY`. Each provider
    looks up its own API key from ``api_keys`` so missing keys can be
    skipped without disturbing the rest.
    """

    enabled: bool
    provider: str
    max_results: int
    api_key: Optional[str] = None
    max_provider_calls: int = DEFAULT_MAX_PROVIDER_CALLS
    max_results_per_role: int = DEFAULT_MAX_RESULTS_PER_ROLE
    # Auto-mode candidate provider list, e.g. ``("tavily", "brave")``.
    # Empty for single-provider modes — the factory still works because it
    # only consults ``providers`` when ``provider`` is ``auto``/``multi``.
    providers: Tuple[str, ...] = ()
    # Provider name → api key mapping. Populated for every external
    # provider whose API key is set in env regardless of mode, so future
    # observability/debug surfaces can report which keys were available.
    api_keys: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_auto(self) -> bool:
        return self.provider in {PROVIDER_AUTO, PROVIDER_MULTI}

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "CollectorConfig":
        env_map: Mapping[str, str] = env if env is not None else os.environ

        enabled = _truthy(env_map.get(ENV_AUTO_COLLECT_ENABLED))
        provider_raw = (env_map.get(ENV_PROVIDER) or "").strip().lower() or PROVIDER_MOCK
        if provider_raw not in KNOWN_PROVIDERS:
            provider_raw = PROVIDER_MOCK
        max_results = _positive_int(
            env_map.get(ENV_MAX_RESULTS), default=DEFAULT_MAX_RESULTS
        )
        max_provider_calls = _positive_int(
            env_map.get(ENV_MAX_PROVIDER_CALLS), default=DEFAULT_MAX_PROVIDER_CALLS
        )
        max_results_per_role = _positive_int(
            env_map.get(ENV_MAX_RESULTS_PER_ROLE), default=DEFAULT_MAX_RESULTS_PER_ROLE
        )

        # Always collect every known external API key so auto/multi mode
        # can dispatch to whichever providers are configured. Single-provider
        # modes only need their own key but populating both is harmless.
        api_keys_raw: dict[str, str] = {}
        tavily_key = _strip_or_none(env_map.get(ENV_TAVILY_API_KEY))
        if tavily_key:
            api_keys_raw[PROVIDER_TAVILY] = tavily_key
        brave_key = _strip_or_none(env_map.get(ENV_BRAVE_API_KEY))
        if brave_key:
            api_keys_raw[PROVIDER_BRAVE] = brave_key

        # Legacy ``api_key`` field — points at the chosen single provider's
        # key. Kept so existing callers that read ``cfg.api_key`` still work.
        api_key: Optional[str] = None
        if provider_raw == PROVIDER_TAVILY:
            api_key = api_keys_raw.get(PROVIDER_TAVILY)
        elif provider_raw == PROVIDER_BRAVE:
            api_key = api_keys_raw.get(PROVIDER_BRAVE)

        # Auto/multi mode parses the ``ENGINEERING_RESEARCH_PROVIDERS``
        # candidate list. Unknown entries are dropped silently so a typo
        # doesn't disable the whole pipeline; the factory fills the gap
        # with the default list when the parsed result is empty.
        providers: Tuple[str, ...] = ()
        if provider_raw in {PROVIDER_AUTO, PROVIDER_MULTI}:
            providers = _parse_provider_list(
                env_map.get(ENV_PROVIDERS),
                allowed=EXTERNAL_PROVIDERS,
            ) or DEFAULT_AUTO_PROVIDERS

        return cls(
            enabled=enabled,
            provider=provider_raw,
            max_results=max_results,
            api_key=api_key,
            max_provider_calls=max_provider_calls,
            max_results_per_role=max_results_per_role,
            providers=providers,
            api_keys=api_keys_raw,
        )


def _parse_provider_list(
    raw: Optional[str], *, allowed: Sequence[str]
) -> Tuple[str, ...]:
    """Parse a comma-separated provider list, filtering to *allowed*.

    Trims whitespace, lowercases entries, drops blanks/duplicates and any
    name not in ``allowed``. Returns ``()`` when the result would be empty
    so callers can fall back to a default list.
    """

    if not raw:
        return ()
    seen: dict[str, None] = {}
    for token in str(raw).split(","):
        cleaned = token.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        if cleaned not in allowed:
            continue
        seen[cleaned] = None
    return tuple(seen.keys())


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _positive_int(value: Optional[str], *, default: int) -> int:
    if value is None or not str(value).strip():
        return default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _strip_or_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
