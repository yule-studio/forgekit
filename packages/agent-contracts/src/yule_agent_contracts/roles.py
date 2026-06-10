"""Agent role identity contract.

A department-agnostic, structured form of the ``"<agent>/<role>"`` address
strings used throughout the message protocol (see :func:`role_address`). This
is intentionally thin: the *behavioural* role engine (mission, responsibilities,
forbidden actions, …) lives in ``yule_engineering.agents.role_profiles`` and is
domain logic, not a wire contract. ``AgentRole`` only models *who* an actor is so
that other agents and the Agent Town front-end can address it unambiguously.
"""

from __future__ import annotations

from dataclasses import dataclass

# Canonical agent ids (departments). Free-form is still allowed on the wire;
# these are the well-known values so callers do not hand-roll typos.
ENGINEERING_AGENT = "engineering-agent"
PLANNING_AGENT = "planning-agent"
GATEWAY = "gateway"


@dataclass(frozen=True)
class AgentRole:
    """A structured ``<agent>/<role>`` identity.

    ``agent_id`` is the department (``"engineering-agent"``), ``role`` the seat
    within it (``"tech-lead"``). The string form (``address``) is what the
    message protocol carries, so this type round-trips losslessly to/from the
    free-form ``from_role`` / ``to_role`` fields on :class:`AgentMessage`.
    """

    agent_id: str
    role: str

    @property
    def address(self) -> str:
        """The ``"<agent>/<role>"`` wire form."""

        return f"{self.agent_id}/{self.role}"

    @classmethod
    def parse(cls, address: str) -> "AgentRole":
        """Parse a ``"<agent>/<role>"`` address back into an :class:`AgentRole`.

        Addresses without a ``/`` (e.g. ``"gateway"``) are treated as a bare
        agent id with an empty role, so the surface never raises on the
        well-known single-token addresses already used in the protocol.
        """

        agent_id, sep, role = address.partition("/")
        if not sep:
            return cls(agent_id=agent_id, role="")
        return cls(agent_id=agent_id, role=role)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.address
