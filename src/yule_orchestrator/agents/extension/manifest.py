"""Plugin / Agent manifest dataclasses + validation (F11 / #102 MVP).

A *manifest* describes the static surface of a plugin or a role-specific
agent: identity, version, hook points, env keys, autonomy level, and risk
class. The runtime never executes a plugin / agent without first parsing
its manifest, so the rules here are intentionally strict.

Hard rails (regression-tested):

  * ``module_path`` must be a dotted Python identifier path — never a
    filesystem path. The actual import happens elsewhere (lazily, only
    after a registry lookup); this module *never* imports the target.
  * ``risk_class`` must be one of :data:`RISK_CLASSES`.
  * ``autonomy_level`` must be one of :data:`AUTONOMY_LEVELS`.
  * ``kind`` (plugin) must be one of :data:`PLUGIN_KINDS`.
  * ``hooks_provided`` / ``hooks_consumed`` entries must be valid
    :class:`HookEvent` names.
  * Manifests are :func:`dataclasses.dataclass(frozen=True)` — they are
    pure data, never mutated post-load.

The loaders accept a plain ``dict`` (typically produced by ``json.load``
or a YAML library at the caller's discretion) so this module stays
free of optional third-party dependencies.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Tuple


class ManifestValidationError(ValueError):
    """Raised when a manifest payload is malformed or violates a hard rail."""


class HookEvent(enum.Enum):
    """Canonical hook points the engineering-agent runtime will fire.

    The set is deliberately small and append-only — adding a new hook
    is a governance-level change because plugins may bind to it and the
    runtime must guarantee fire ordering.
    """

    PREFLIGHT = "preflight"
    OUTBOUND_LLM = "outbound_llm"
    OUTBOUND_DISCORD = "outbound_discord"
    OUTBOUND_GITHUB = "outbound_github"
    OUTBOUND_VAULT = "outbound_vault"
    COMPLETION = "completion"
    POSTMORTEM = "postmortem"


#: Risk taxonomy a manifest must declare. ``HIGH`` is reserved for plugins
#: that touch outbound payloads, secrets, or credentials.
RISK_CLASSES: Tuple[str, ...] = ("LOW", "MEDIUM", "HIGH")

#: Autonomy levels mirror the engineering-agent autonomy ladder.
AUTONOMY_LEVELS: Tuple[str, ...] = (
    "advisory",
    "supervised",
    "autonomous",
)

#: Plugin kinds — the runtime uses this to decide which surface the
#: plugin attaches to. Append-only.
PLUGIN_KINDS: Tuple[str, ...] = (
    "guard",
    "learning",
    "exploration",
    "observability",
    "delivery",
)


_MODULE_PATH_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _hook_names() -> Tuple[str, ...]:
    return tuple(member.name for member in HookEvent)


@dataclass(frozen=True)
class PluginManifest:
    """Static description of a plugin (F1 / F2 / F3 / ...).

    Fields:
      id: machine-stable identifier (kebab-case, ASCII).
      name: human-readable display name.
      version: semver-ish ``MAJOR.MINOR.PATCH[-pre|+build]``.
      kind: one of :data:`PLUGIN_KINDS`.
      hooks_provided: hook names this plugin implements.
      hooks_consumed: hook names this plugin subscribes to (read-only).
      env_keys: env var keys the plugin reads at runtime.
      autonomy_level: minimum autonomy level required to run this plugin.
      paste_guard_required: whether outbound payloads must pass through
        PasteGuard before this plugin is invoked.
      risk_class: one of :data:`RISK_CLASSES`.
      module_path: dotted Python import path. **Never** loaded by this
        module — recorded for later lazy import by the runtime.
    """

    id: str
    name: str
    version: str
    kind: str
    hooks_provided: Tuple[str, ...] = field(default_factory=tuple)
    hooks_consumed: Tuple[str, ...] = field(default_factory=tuple)
    env_keys: Tuple[str, ...] = field(default_factory=tuple)
    autonomy_level: str = "advisory"
    paste_guard_required: bool = True
    risk_class: str = "LOW"
    module_path: str = ""


@dataclass(frozen=True)
class AgentManifest:
    """Static description of a role-specific agent.

    Fields:
      id: machine-stable identifier (kebab-case, ASCII).
      name: human-readable display name.
      role: canonical role slug (``tech-lead``, ``backend-engineer`` ...).
      version: semver-ish version string.
      capabilities: free-form tags the runtime uses to match a request
        against an agent (informational; not part of routing today).
      plugins_required: plugin ids that must be registered for this
        agent to be usable.
      prompt_template_ref: opaque identifier pointing at the prompt
        template the agent expects (resolved elsewhere).
      github_app_env_prefix: env var prefix for the role's GitHub App
        credentials (multi-bot env keys land via #87 / .env.example).
      autonomy_level: one of :data:`AUTONOMY_LEVELS`.
      risk_class: one of :data:`RISK_CLASSES`.
      module_path: dotted Python import path for the agent entrypoint.
    """

    id: str
    name: str
    role: str
    version: str
    capabilities: Tuple[str, ...] = field(default_factory=tuple)
    plugins_required: Tuple[str, ...] = field(default_factory=tuple)
    prompt_template_ref: str = ""
    github_app_env_prefix: str = ""
    autonomy_level: str = "advisory"
    risk_class: str = "LOW"
    module_path: str = ""


# --------------------------------------------------------------------------- #
# Dict -> manifest loaders
# --------------------------------------------------------------------------- #


def _require_mapping(payload: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ManifestValidationError(f"{what} payload must be a mapping, got {type(payload).__name__}")
    return payload


def _str_tuple(value: Any, field_name: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not hasattr(value, "__iter__"):
        raise ManifestValidationError(f"{field_name} must be a list of strings")
    out = []
    for item in value:
        if not isinstance(item, str):
            raise ManifestValidationError(f"{field_name} entries must be strings, got {type(item).__name__}")
        out.append(item)
    return tuple(out)


def _required_str(payload: Mapping[str, Any], key: str, what: str) -> str:
    if key not in payload:
        raise ManifestValidationError(f"{what} missing required field '{key}'")
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise ManifestValidationError(f"{what} field '{key}' must be a non-empty string")
    return value


def load_plugin_manifest_from_dict(payload: Any) -> PluginManifest:
    """Build a :class:`PluginManifest` from a dict-like payload.

    The caller is responsible for any file I/O. Validation is performed
    eagerly via :func:`validate_plugin_manifest` so a returned manifest
    is always well-formed.
    """

    data = _require_mapping(payload, "plugin manifest")
    manifest = PluginManifest(
        id=_required_str(data, "id", "plugin manifest"),
        name=_required_str(data, "name", "plugin manifest"),
        version=_required_str(data, "version", "plugin manifest"),
        kind=_required_str(data, "kind", "plugin manifest"),
        hooks_provided=_str_tuple(data.get("hooks_provided"), "hooks_provided"),
        hooks_consumed=_str_tuple(data.get("hooks_consumed"), "hooks_consumed"),
        env_keys=_str_tuple(data.get("env_keys"), "env_keys"),
        autonomy_level=data.get("autonomy_level", "advisory"),
        paste_guard_required=bool(data.get("paste_guard_required", True)),
        risk_class=data.get("risk_class", "LOW"),
        module_path=data.get("module_path", ""),
    )
    validate_plugin_manifest(manifest)
    return manifest


def load_agent_manifest_from_dict(payload: Any) -> AgentManifest:
    """Build an :class:`AgentManifest` from a dict-like payload."""

    data = _require_mapping(payload, "agent manifest")
    manifest = AgentManifest(
        id=_required_str(data, "id", "agent manifest"),
        name=_required_str(data, "name", "agent manifest"),
        role=_required_str(data, "role", "agent manifest"),
        version=_required_str(data, "version", "agent manifest"),
        capabilities=_str_tuple(data.get("capabilities"), "capabilities"),
        plugins_required=_str_tuple(data.get("plugins_required"), "plugins_required"),
        prompt_template_ref=data.get("prompt_template_ref", ""),
        github_app_env_prefix=data.get("github_app_env_prefix", ""),
        autonomy_level=data.get("autonomy_level", "advisory"),
        risk_class=data.get("risk_class", "LOW"),
        module_path=data.get("module_path", ""),
    )
    validate_agent_manifest(manifest)
    return manifest


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _validate_module_path(value: str, what: str) -> None:
    # Empty module path is permitted for manifest-only registrations
    # (the runtime fails later if it tries to import an empty path).
    if value == "":
        return
    if not _MODULE_PATH_RE.match(value):
        raise ManifestValidationError(
            f"{what} module_path '{value}' must be a dotted Python identifier path"
        )


def _validate_id(value: str, what: str) -> None:
    if not _ID_RE.match(value):
        raise ManifestValidationError(
            f"{what} id '{value}' must be kebab-case ASCII (^[a-z0-9][a-z0-9_-]{{0,63}}$)"
        )


def _validate_version(value: str, what: str) -> None:
    if not _VERSION_RE.match(value):
        raise ManifestValidationError(
            f"{what} version '{value}' must be MAJOR.MINOR.PATCH[-pre|+build]"
        )


def _validate_hook_names(names: Tuple[str, ...], what: str, field_name: str) -> None:
    valid = set(_hook_names())
    for name in names:
        if name not in valid:
            raise ManifestValidationError(
                f"{what} {field_name} contains unknown hook '{name}'. "
                f"Valid hooks: {sorted(valid)}"
            )


def validate_plugin_manifest(manifest: PluginManifest) -> None:
    """Raise :class:`ManifestValidationError` if the plugin manifest is invalid."""

    if not isinstance(manifest, PluginManifest):
        raise ManifestValidationError("validate_plugin_manifest expects a PluginManifest")
    _validate_id(manifest.id, "plugin manifest")
    _validate_version(manifest.version, "plugin manifest")
    if manifest.kind not in PLUGIN_KINDS:
        raise ManifestValidationError(
            f"plugin manifest kind '{manifest.kind}' must be one of {PLUGIN_KINDS}"
        )
    if manifest.risk_class not in RISK_CLASSES:
        raise ManifestValidationError(
            f"plugin manifest risk_class '{manifest.risk_class}' must be one of {RISK_CLASSES}"
        )
    if manifest.autonomy_level not in AUTONOMY_LEVELS:
        raise ManifestValidationError(
            f"plugin manifest autonomy_level '{manifest.autonomy_level}' must be one of {AUTONOMY_LEVELS}"
        )
    _validate_hook_names(manifest.hooks_provided, "plugin manifest", "hooks_provided")
    _validate_hook_names(manifest.hooks_consumed, "plugin manifest", "hooks_consumed")
    _validate_module_path(manifest.module_path, "plugin manifest")


def validate_agent_manifest(manifest: AgentManifest) -> None:
    """Raise :class:`ManifestValidationError` if the agent manifest is invalid."""

    if not isinstance(manifest, AgentManifest):
        raise ManifestValidationError("validate_agent_manifest expects an AgentManifest")
    _validate_id(manifest.id, "agent manifest")
    _validate_version(manifest.version, "agent manifest")
    if manifest.risk_class not in RISK_CLASSES:
        raise ManifestValidationError(
            f"agent manifest risk_class '{manifest.risk_class}' must be one of {RISK_CLASSES}"
        )
    if manifest.autonomy_level not in AUTONOMY_LEVELS:
        raise ManifestValidationError(
            f"agent manifest autonomy_level '{manifest.autonomy_level}' must be one of {AUTONOMY_LEVELS}"
        )
    if not manifest.role or not _ID_RE.match(manifest.role):
        raise ManifestValidationError(
            f"agent manifest role '{manifest.role}' must be kebab-case ASCII"
        )
    _validate_module_path(manifest.module_path, "agent manifest")
