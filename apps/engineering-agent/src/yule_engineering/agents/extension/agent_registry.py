"""In-memory agent registry (F11 / #102 MVP).

An :class:`AgentRegistry` is the deterministic lookup the engineering
runtime uses to resolve a role slug (``tech-lead``, ``backend-engineer``,
...) into one or more :class:`~yule_engineering.agents.extension.manifest.AgentManifest`
records.

The registry is intentionally minimal:

  * Manifests are registered explicitly (no auto-discovery).
  * Each registered agent is keyed by ``manifest.id``; multiple agents
    may share the same ``role`` (e.g. a "qa-engineer-v2" successor),
    so :meth:`agents_for_role` returns a tuple.
  * No plugin / module import happens here.
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

from .manifest import AgentManifest, validate_agent_manifest


class AgentRegistry:
    """Stores :class:`AgentManifest` records keyed by id."""

    def __init__(self) -> None:
        self._agents: Dict[str, AgentManifest] = {}

    def register(self, manifest: AgentManifest) -> None:
        """Register an agent manifest. Re-registering the same id raises."""

        validate_agent_manifest(manifest)
        if manifest.id in self._agents:
            raise ValueError(f"agent '{manifest.id}' is already registered")
        self._agents[manifest.id] = manifest

    def get(self, agent_id: str) -> AgentManifest:
        """Return the manifest for ``agent_id`` or raise :class:`KeyError`."""

        if agent_id not in self._agents:
            raise KeyError(f"agent '{agent_id}' is not registered")
        return self._agents[agent_id]

    def agents_for_role(self, role: str) -> Tuple[AgentManifest, ...]:
        """Return all manifests whose ``role`` matches, sorted by id."""

        if not isinstance(role, str):
            raise TypeError("role must be a string")
        matching = [m for m in self._agents.values() if m.role == role]
        matching.sort(key=lambda m: m.id)
        return tuple(matching)

    def all(self) -> Tuple[AgentManifest, ...]:
        """Return every registered agent manifest, sorted by id."""

        return tuple(sorted(self._agents.values(), key=lambda m: m.id))

    def __contains__(self, agent_id: object) -> bool:
        return isinstance(agent_id, str) and agent_id in self._agents

    def __len__(self) -> int:
        return len(self._agents)

    def __iter__(self) -> Iterable[AgentManifest]:
        return iter(self.all())
