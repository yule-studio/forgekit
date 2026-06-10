"""In-memory plugin registry (F11 / #102 MVP).

A :class:`PluginRegistry` is a thin, deterministic lookup over a set of
:class:`~yule_engineering.agents.extension.manifest.PluginManifest`
records. It is the *only* surface the engineering-agent runtime should
use to discover plugins by id or by hook event.

Design rails:

  * Registration is explicit — there is no filesystem auto-discovery
    in this MVP. Callers register manifests after loading them from
    JSON / YAML.
  * The registry never imports the plugin's module. ``module_path``
    is recorded for later lazy import by a HookChain runtime that is
    out of scope here.
  * Lookups are deterministic and return tuples (immutable snapshots),
    so callers can iterate without risking mid-iteration mutation.
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

from .manifest import HookEvent, PluginManifest, validate_plugin_manifest


class PluginRegistry:
    """Stores :class:`PluginManifest` records keyed by id."""

    def __init__(self) -> None:
        self._plugins: Dict[str, PluginManifest] = {}

    def register(self, manifest: PluginManifest) -> None:
        """Register a manifest. Re-registering the same id is an error."""

        validate_plugin_manifest(manifest)
        if manifest.id in self._plugins:
            raise ValueError(f"plugin '{manifest.id}' is already registered")
        self._plugins[manifest.id] = manifest

    def get(self, plugin_id: str) -> PluginManifest:
        """Return the manifest for ``plugin_id`` or raise :class:`KeyError`."""

        if plugin_id not in self._plugins:
            raise KeyError(f"plugin '{plugin_id}' is not registered")
        return self._plugins[plugin_id]

    def plugins_for_hook(self, hook: HookEvent) -> Tuple[PluginManifest, ...]:
        """Return all manifests that *provide* ``hook``, sorted by id.

        Hooks plugins merely *consume* are intentionally excluded — the
        runtime needs the providers when it builds a chain.
        """

        if not isinstance(hook, HookEvent):
            raise TypeError("plugins_for_hook expects a HookEvent enum member")
        name = hook.name
        matching = [m for m in self._plugins.values() if name in m.hooks_provided]
        matching.sort(key=lambda m: m.id)
        return tuple(matching)

    def all(self) -> Tuple[PluginManifest, ...]:
        """Return every registered manifest, sorted by id (test helper)."""

        return tuple(sorted(self._plugins.values(), key=lambda m: m.id))

    def __contains__(self, plugin_id: object) -> bool:
        return isinstance(plugin_id, str) and plugin_id in self._plugins

    def __len__(self) -> int:
        return len(self._plugins)

    def __iter__(self) -> Iterable[PluginManifest]:
        return iter(self.all())
