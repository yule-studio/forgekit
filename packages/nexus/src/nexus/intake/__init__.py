"""nexus.intake — external skill/plugin/tool intake lane (pre-Armory).

Free-first discovery of external skills/plugins/tools/MCP servers → vendor-neutral
curated intake packets, gated BEFORE anything is added to the Armory. Reuses
``nexus.sources`` for collection (no new collector/scheduler); adds only the
candidate schema, the SourceItem→candidate extract, and the curation gate.

Pipeline: ``collect.run_intake(repo_root)`` → free-first collect → ``extract`` →
``curate`` → :class:`~nexus.intake.curate.IntakePacket`. SSoT: ``docs/external-intake-lane.md``.
"""

from __future__ import annotations

from .candidate import (
    DISPOSITION_BLOCKED,
    DISPOSITION_PROMOTE,
    DISPOSITION_RAW,
    ExternalCandidate,
)
from .collect import collect_candidates, intake_source_registry, run_intake
# NOTE: we re-export ``curate_all`` (the batch API) but NOT the singular ``curate``
# function — re-exporting it here would shadow the ``nexus.intake.curate`` SUBMODULE
# (getattr on the package would return the function), breaking
# ``from nexus.intake import curate``. The single-item gate stays reachable as
# ``nexus.intake.curate.curate``.
from .curate import CurationVerdict, IntakePacket, curate_all
from .extract import candidate_from_item, extract_candidates

__all__ = (
    "ExternalCandidate",
    "DISPOSITION_PROMOTE", "DISPOSITION_RAW", "DISPOSITION_BLOCKED",
    "candidate_from_item", "extract_candidates",
    "curate_all", "CurationVerdict", "IntakePacket",
    "intake_source_registry", "collect_candidates", "run_intake",
)
