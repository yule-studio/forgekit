"""Plugin + Agent extension architecture — manifest & registry layer (F11 / #102).

This package provides the **manifest** and **registry** primitives that let
plugins (F1/F2/F3 ...) and role-specific agents (tech-lead, backend-engineer,
...) declare their identity, hooks, and risk surface without binding the
runtime to specific implementations.

MVP scope (issue #102 — manifest registry only):

  * :class:`PluginManifest` / :class:`AgentManifest` — frozen dataclasses.
  * :class:`HookEvent` — canonical enum of hook points plugins may
    provide / consume.
  * :func:`load_plugin_manifest_from_dict` / :func:`load_agent_manifest_from_dict`
    — pure dict -> manifest factories. The caller is responsible for any
    file I/O (JSON / YAML), keeping this module dependency-free.
  * :func:`validate_plugin_manifest` / :func:`validate_agent_manifest` —
    pure functions that raise :class:`ManifestValidationError` when a
    manifest is malformed (unknown risk class, unknown hook name, bad
    module path, ...).
  * :class:`PluginRegistry` / :class:`AgentRegistry` — in-memory registries
    used by the engineering-agent runtime to look up plugins by hook and
    agents by role.

Out of scope (follow-up PRs):

  * HookChain invocation (manifests describe hooks; chain runtime is
    deliberately not yet implemented).
  * Manifest auto-discovery / loader from filesystem (caller registers
    manually for now).
  * F4 .. F10 manifests (kept out to avoid in-flight scope creep).
"""

from .manifest import (
    AgentManifest,
    HookEvent,
    ManifestValidationError,
    PluginManifest,
    RISK_CLASSES,
    AUTONOMY_LEVELS,
    PLUGIN_KINDS,
    load_agent_manifest_from_dict,
    load_plugin_manifest_from_dict,
    validate_agent_manifest,
    validate_plugin_manifest,
)
from .plugin_registry import PluginRegistry
from .agent_registry import AgentRegistry

__all__ = [
    "AgentManifest",
    "AgentRegistry",
    "AUTONOMY_LEVELS",
    "HookEvent",
    "ManifestValidationError",
    "PLUGIN_KINDS",
    "PluginManifest",
    "PluginRegistry",
    "RISK_CLASSES",
    "load_agent_manifest_from_dict",
    "load_plugin_manifest_from_dict",
    "validate_agent_manifest",
    "validate_plugin_manifest",
]
