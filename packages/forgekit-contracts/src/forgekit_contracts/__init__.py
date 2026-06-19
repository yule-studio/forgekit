"""forgekit-contracts — ForgeKit command/result/work-packet schema (WT2).

The pure, stdlib-only dataclasses + kind constants that form the contract between
ForgeKit's core and its surfaces: command-result kinds the router emits, console
interaction/layout modes, and the structured result/packet shapes the TUI renders.
Keeping them free of textual/IO means the whole core is unit-testable without a
terminal, and every app shares one schema instead of redefining it.

Distinct from ``packages/agent-contracts`` (``yule_agent_contracts`` = agent↔agent
command/event/status messages) — this is the operator/console-facing layer. Owner
matrix: ``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

from . import models

__all__ = ("models",)
