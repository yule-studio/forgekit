"""nexus — ForgeKit's knowledge source boundary (WT3).

Nexus is the mine/library: where knowledge lives and is read from. It is the
read/projection/retrieval boundary that Hephaistos *reads* (never copies) to forge
work. A standalone ForgeKit pillar (Nexus / ForgeKit / Hephaistos / Armory), not a
console-private module and not a single slash command — see ``docs/vision.md``.

Currently extracted:
- ``sources`` — discovery source collectors (GitHub / HN / Reddit / RSS, free-first)
- ``vault``   — Obsidian vault read + authorship (registry-backed agent identity)

Follow-ups (entangled, later increments): ``discovery`` (needs handoff/contracts),
``design`` (restricted source; UI projection splits to console), and the bounded
``nexus_read`` path (currently in hephaistos). Depends only on ``forgekit-config``.
Owner matrix: ``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

__all__ = ("sources", "vault")
