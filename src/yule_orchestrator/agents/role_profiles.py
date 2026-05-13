"""Engineering-agent role profile + participation contracts.

A :class:`RoleProfile` describes one engineering role as a *contract*
the rest of the system can consume deterministically:

- ``mission`` / ``responsibilities`` — what the role exists to do.
- ``required_context`` — facts the role must know to make a useful
  decision; the runtime input builder lifts these into the role's
  prompt / deliberation context.
- ``must_review`` — checklist the role applies before signing off.
- ``output_sections`` — the section headings the role's deliberation
  comment should produce.
- ``forbidden_actions`` — patterns the role must refuse so the
  tech-lead aggregator has a stable veto surface.
- ``activation_keywords`` / ``explicit_patterns`` — selector signals
  used by :mod:`agents.lifecycle.role_selection`. The selector reads
  these instead of an embedded keyword bank so adding a new domain
  (k8s, CRDT, browser perf …) means editing one profile, not a bunch
  of selector branches.
- ``escalation_rules`` — when this role should hand off to another
  role or back to tech-lead.
- ``done_criteria`` — the role's "I'm done" signal so the aggregator
  can tell whether to keep the conversation open.

The module is pure-Python and IO-free so tests, the selector, and the
deliberation runtime can all import it without dragging in Discord or
the workflow cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple


# ---------------------------------------------------------------------------
# Participation level vocabulary
# ---------------------------------------------------------------------------


# Free-form string literals (no Python ``Literal`` import to keep this
# module 3.9-friendly). The selector + aggregator both treat these as
# the canonical bucket names.
PARTICIPATION_REQUIRED: str = "required"
PARTICIPATION_PRIMARY: str = "primary"
PARTICIPATION_REVIEWER: str = "reviewer"
PARTICIPATION_OPTIONAL: str = "optional"
PARTICIPATION_EXCLUDED: str = "excluded"

PARTICIPATION_LEVELS: Tuple[str, ...] = (
    PARTICIPATION_REQUIRED,
    PARTICIPATION_PRIMARY,
    PARTICIPATION_REVIEWER,
    PARTICIPATION_OPTIONAL,
    PARTICIPATION_EXCLUDED,
)

# Levels that count as "actively participating" — UI and persistence
# code should look at this set when deciding whether to render a role
# in the selected_roles list.
PARTICIPATING_LEVELS: Tuple[str, ...] = (
    PARTICIPATION_REQUIRED,
    PARTICIPATION_PRIMARY,
    PARTICIPATION_REVIEWER,
    PARTICIPATION_OPTIONAL,
)


# ---------------------------------------------------------------------------
# Role profile dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleProfile:
    """Static description of one engineering role.

    Every field is a tuple of strings (or a plain string for scalars)
    so the dataclass is hashable, JSON-serialisable, and immutable.
    Extending the contract later means adding a new field with a
    sensible default — never replacing an existing one in place.
    """

    role_id: str
    display_name: str
    mission: str
    responsibilities: Tuple[str, ...] = ()
    required_context: Tuple[str, ...] = ()
    must_review: Tuple[str, ...] = ()
    output_sections: Tuple[str, ...] = ()
    forbidden_actions: Tuple[str, ...] = ()
    activation_keywords: Tuple[str, ...] = ()
    explicit_patterns: Tuple[str, ...] = ()
    escalation_rules: Tuple[str, ...] = ()
    done_criteria: Tuple[str, ...] = ()

    def to_contract_dict(self) -> dict:
        """Return the input/output/forbidden/done subset as a dict.

        Used by the aggregator and runtime context builder when only
        the contract piece matters (the selector / status surfaces
        consume the full profile separately).
        """

        return {
            "role_id": self.role_id,
            "mission": self.mission,
            "required_context": list(self.required_context),
            "must_review": list(self.must_review),
            "output_sections": list(self.output_sections),
            "forbidden_actions": list(self.forbidden_actions),
            "escalation_rules": list(self.escalation_rules),
            "done_criteria": list(self.done_criteria),
        }


@dataclass(frozen=True)
class RoleContract:
    """Slim view of a profile carrying just the contract surfaces.

    Built from a :class:`RoleProfile` via :meth:`RoleProfile.to_contract_dict`
    when callers want to pass a small payload into a runtime stage
    without exposing the activation_keywords / explicit_patterns the
    selector uses internally.
    """

    role_id: str
    mission: str
    required_context: Tuple[str, ...] = ()
    must_review: Tuple[str, ...] = ()
    output_sections: Tuple[str, ...] = ()
    forbidden_actions: Tuple[str, ...] = ()
    escalation_rules: Tuple[str, ...] = ()
    done_criteria: Tuple[str, ...] = ()


def role_contract_from_profile(profile: RoleProfile) -> RoleContract:
    """Project a :class:`RoleProfile` down to a :class:`RoleContract`."""

    return RoleContract(
        role_id=profile.role_id,
        mission=profile.mission,
        required_context=profile.required_context,
        must_review=profile.must_review,
        output_sections=profile.output_sections,
        forbidden_actions=profile.forbidden_actions,
        escalation_rules=profile.escalation_rules,
        done_criteria=profile.done_criteria,
    )


# ---------------------------------------------------------------------------
# Profile registry — populated in role_profiles_data.py for clarity
# ---------------------------------------------------------------------------


# Lazy import dance: the data file lives next door so this module stays
# small. Using a function keeps the import side-effect free and lets
# tests stub the registry per case.
def _load_default_registry() -> Mapping[str, RoleProfile]:
    from . import role_profiles_data

    return role_profiles_data.ROLE_PROFILES


_REGISTRY_CACHE: Optional[Mapping[str, RoleProfile]] = None


def all_role_profiles() -> Mapping[str, RoleProfile]:
    """Return the canonical role registry (cached after first call)."""

    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = _load_default_registry()
    return _REGISTRY_CACHE


def get_role_profile(role_id: str) -> Optional[RoleProfile]:
    """Look up *role_id* in the registry. Returns ``None`` when missing.

    Accepts either the canonical short id (``"backend-engineer"``) or
    a fully-qualified address (``"engineering-agent/backend-engineer"``)
    so callers don't have to normalise before lookup.
    """

    if not role_id:
        return None
    short = role_id.split("/", 1)[-1].strip()
    return all_role_profiles().get(short)


def reset_registry_cache_for_tests() -> None:
    """Drop the cached registry so a test can monkey-patch the module."""

    global _REGISTRY_CACHE
    _REGISTRY_CACHE = None
    _AGENT_JSON_CACHE.clear()


# ---------------------------------------------------------------------------
# Output / forbidden helpers — consumed by deliberation prefaces and
# work_report templates so each role's comment / row uses the canonical
# section list defined on its profile.
# ---------------------------------------------------------------------------


def output_template_for_role(role_id: str) -> Tuple[str, ...]:
    """Return the canonical output section list for *role_id*.

    Returns ``()`` when the role isn't registered so the caller can
    fall back to the legacy unbounded format. The role-runtime preface
    feeds this list into the bot's prompt as "이번 take는 다음 섹션
    구조로 정리해 주세요".
    """

    profile = get_role_profile(role_id)
    if profile is None:
        return ()
    return tuple(profile.output_sections)


def forbidden_actions_for_role(role_id: str) -> Tuple[str, ...]:
    """Return the role's hard-veto list. Used by the aggregator so
    forbidden patterns come from a single source of truth."""

    profile = get_role_profile(role_id)
    if profile is None:
        return ()
    return tuple(profile.forbidden_actions)


def required_context_for_role(role_id: str) -> Tuple[str, ...]:
    """Return the role's required-context checklist."""

    profile = get_role_profile(role_id)
    if profile is None:
        return ()
    return tuple(profile.required_context)


# ---------------------------------------------------------------------------
# Disk-backed role contract reader (manifest.json) — F15 unified schema.
#
# The Python ``RoleProfile`` registry above is the selector / aggregator
# surface. The richer contract fields (default_response_template /
# stop_conditions / standards / catalogs) live on the on-disk
# ``manifest.json`` per role (F15 commit 4 absorbed the legacy
# ``agent.json`` payload) so operators can edit them without a code
# deploy. This loader exposes those fields read-only so deliberation
# fallbacks and runtime preface builders can reference them without
# duplicating the JSON shape.
#
# Behaviour notes:
# - Returns empty / None on missing file / missing field — callers fall
#   back to legacy behaviour rather than raise.
# - Caches per-role payloads in process to avoid re-reading on every
#   role take. ``reset_registry_cache_for_tests()`` also clears the
#   contract cache so tests can monkey-patch a temp profile.
# ---------------------------------------------------------------------------


_AGENT_JSON_CACHE: dict[str, dict] = {}


def _agents_dir() -> Path:
    # role_profiles.py lives at src/yule_orchestrator/agents/, so the repo
    # root is three parents up. Resolved once per call (cheap) so tests
    # that move CWD don't break path lookup.
    return Path(__file__).resolve().parents[3] / "agents" / "engineering-agent"


def _load_agent_json(role_id: str) -> Optional[Mapping[str, Any]]:
    """Read ``agents/engineering-agent/<role>/manifest.json`` lazily.

    Returns ``None`` when the file is missing or unreadable so callers
    can fall back to legacy defaults instead of crashing. JSON parse
    errors are also swallowed — a malformed profile must not wedge the
    runtime; supervisor surfaces it through other channels.
    """

    if not role_id:
        return None
    short = role_id.split("/", 1)[-1].strip()
    cached = _AGENT_JSON_CACHE.get(short)
    if cached is not None:
        return cached
    path = _agents_dir() / short / "manifest.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(payload, dict):
        _AGENT_JSON_CACHE[short] = payload
        return payload
    return None


def default_response_template_for(role_id: str) -> Tuple[str, ...]:
    """Return the role's contract-v1 ``default_response_template``.

    Falls back to :func:`output_template_for_role` when the JSON file
    has no ``default_response_template`` yet — keeps roles still on the
    legacy registry-only contract working unchanged.
    """

    payload = _load_agent_json(role_id) or {}
    template = payload.get("default_response_template")
    if isinstance(template, list) and template:
        return tuple(str(item) for item in template if str(item).strip())
    # Legacy fallback so roles without a contract-v1 template still get
    # a sensible section list from the in-process registry.
    return output_template_for_role(role_id)


def stop_conditions_for(role_id: str) -> Tuple[str, ...]:
    """Return the role's contract-v1 ``stop_conditions``.

    Empty tuple on miss — caller uses the legacy ``forbidden_actions``
    surface (already exposed via :func:`forbidden_actions_for_role`).
    """

    payload = _load_agent_json(role_id) or {}
    stops = payload.get("stop_conditions")
    if isinstance(stops, list) and stops:
        return tuple(str(item) for item in stops if str(item).strip())
    return ()


def review_checklist_for_role(
    role_id: str, *, category: Optional[str] = None
) -> Tuple[str, ...]:
    """Return the role's review_checklist_by_category (optionally per-category).

    When *category* is None, returns a flat tuple of every checklist
    item across all categories so a generic review preface can show the
    full list. When provided, returns only that category's items.
    """

    payload = _load_agent_json(role_id) or {}
    checklist = payload.get("review_checklist_by_category")
    if not isinstance(checklist, dict):
        return ()
    if category is not None:
        items = checklist.get(category)
        if isinstance(items, list):
            return tuple(str(item) for item in items if str(item).strip())
        return ()
    flat: list[str] = []
    for items in checklist.values():
        if isinstance(items, list):
            flat.extend(str(item) for item in items if str(item).strip())
    return tuple(flat)


def required_context_catalog_for_role(role_id: str) -> Mapping[str, Tuple[str, ...]]:
    """Return the role's required_context_catalog grouped by category.

    Empty mapping on miss — callers fall back to the flat
    :func:`required_context_for_role` list.
    """

    payload = _load_agent_json(role_id) or {}
    catalog = payload.get("required_context_catalog")
    if not isinstance(catalog, dict):
        return {}
    grouped: dict[str, Tuple[str, ...]] = {}
    for cat, items in catalog.items():
        if not isinstance(items, list):
            continue
        cleaned = tuple(str(item) for item in items if str(item).strip())
        if cleaned:
            grouped[str(cat)] = cleaned
    return grouped


def reset_contract_cache_for_tests() -> None:
    """Drop the on-disk contract cache so a test can use a temp profile."""

    _AGENT_JSON_CACHE.clear()


__all__ = (
    "PARTICIPATION_REQUIRED",
    "PARTICIPATION_PRIMARY",
    "PARTICIPATION_REVIEWER",
    "PARTICIPATION_OPTIONAL",
    "PARTICIPATION_EXCLUDED",
    "PARTICIPATION_LEVELS",
    "PARTICIPATING_LEVELS",
    "RoleContract",
    "RoleProfile",
    "all_role_profiles",
    "default_response_template_for",
    "forbidden_actions_for_role",
    "get_role_profile",
    "output_template_for_role",
    "required_context_catalog_for_role",
    "required_context_for_role",
    "reset_contract_cache_for_tests",
    "reset_registry_cache_for_tests",
    "review_checklist_for_role",
    "role_contract_from_profile",
    "stop_conditions_for",
)
