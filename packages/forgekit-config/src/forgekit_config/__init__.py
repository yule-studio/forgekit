"""forgekit-config — ForgeKit config/paths/identity core (WT2).

The owner of ForgeKit's on-disk shape: runtime data home (`paths`), and — as WT2
progresses — config schema/persistence and agent identity. Pure, stdlib-first, so
every app (`forgekit-console` and the sibling execution apps) shares one config
contract instead of each reaching into the console.

Currently extracted: `paths` (was `forgekit_console.runtime_paths`) and `identity`
(agent git-author / app attribution, was `forgekit_console.identity`). Roadmap and
owner matrix: `docs/forgekit-architecture-ownership.md`.
"""

from __future__ import annotations

from . import identity, paths

__all__ = ("identity", "paths")
