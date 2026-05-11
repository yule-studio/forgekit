"""Plugin + Agent extension architecture — manifest & registry layer (F11 / #102).

This package provides the **manifest** and **registry** primitives that let
plugins (F1/F2/F3 ...) and role-specific agents (tech-lead, backend-engineer,
...) declare their identity, hooks, and risk surface without binding the
runtime to specific implementations.

F11 MVP (issue #102) landed manifest + registry; F11.1 (issue #107)
adds the HookChain dispatch + filesystem manifest discovery loader.

Surfaces exposed:

  * :class:`PluginManifest` / :class:`AgentManifest` — frozen dataclasses.
  * :class:`HookEvent` — canonical enum of hook points plugins may
    provide / consume.
  * :func:`load_plugin_manifest_from_dict` / :func:`load_agent_manifest_from_dict`
    — pure dict -> manifest factories.
  * :func:`validate_plugin_manifest` / :func:`validate_agent_manifest` —
    pure validators that raise :class:`ManifestValidationError`.
  * :class:`PluginRegistry` / :class:`AgentRegistry` — in-memory registries.
  * :func:`invoke_hook` / :class:`HookResult` / :class:`HookLevel` —
    deterministic chain dispatch over a registry.
  * :func:`discover_manifests` / :func:`load_plugin_module` — filesystem
    discovery + lazy importlib loader.

Out of scope (follow-up PRs): F4..F10 manifests; the wider routing
runtime that maps a role request onto an :class:`AgentManifest`.
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
from .hook_chain import (
    HookHandler,
    HookLevel,
    HookResult,
    ModuleLoader,
    invoke_hook,
)
from .loader import (
    DiscoveryReport,
    ManifestDiscoveryError,
    discover_manifests,
    discover_manifests_with_report,
    load_plugin_module,
)

__all__ = [
    "AgentManifest",
    "AgentRegistry",
    "AUTONOMY_LEVELS",
    "DiscoveryReport",
    "HookEvent",
    "HookHandler",
    "HookLevel",
    "HookResult",
    "ManifestDiscoveryError",
    "ManifestValidationError",
    "ModuleLoader",
    "PLUGIN_KINDS",
    "PluginManifest",
    "PluginRegistry",
    "RISK_CLASSES",
    "discover_manifests",
    "discover_manifests_with_report",
    "invoke_hook",
    "load_agent_manifest_from_dict",
    "load_plugin_manifest_from_dict",
    "load_plugin_module",
    "validate_agent_manifest",
    "validate_plugin_manifest",
]
