"""Intake → Armory bridge (composition layer).

Connects the two halves of the tool-intake pipeline so they are NOT two disconnected
candidate models:

  nexus.intake.ExternalCandidate  (discovery vetting: trust/license/maintenance/repo)
        │  to_armory_candidate()   ← THIS module (app composition root)
        ▼
  armory.ArmoryCandidate          (catalog proposal: selection contract)
        │  armory.promote_candidate
        ▼
  armory.SkillSpec                (registered catalog entry)

Lives in the console app — the only layer allowed to depend on BOTH ``nexus`` and
``armory`` (the packages stay decoupled: ``nexus`` never imports ``armory``).

Honesty: the bridge maps only what discovery actually knows. It deliberately leaves
the catalog *selection contract* (when_to_use / signals / unsafe_boundary / install
requirements) EMPTY, so ``promote_candidate`` rejects the draft until a curator
enriches it. A freshly-discovered repo is never auto-promoted into the catalog.
"""

from __future__ import annotations

from armory import promote_candidate
from armory.candidate import ArmoryCandidate
from armory.models import KIND_MCP, KIND_PLUGIN, KIND_SKILL, KIND_TOOL
from nexus.intake import candidate as K
from nexus.intake.candidate import ExternalCandidate

# install_shape (discovery vocab) → Armory entry kind (catalog vocab).
_KIND_BY_SHAPE = {
    K.SHAPE_SKILL: KIND_SKILL,
    K.SHAPE_PLUGIN: KIND_PLUGIN,
    K.SHAPE_HOOK: KIND_PLUGIN,   # a lifecycle hook is a harness plugin
    K.SHAPE_MCP: KIND_MCP,
    K.SHAPE_CLI: KIND_TOOL,
    K.SHAPE_LIB: KIND_TOOL,      # a library the executor installs/uses
    # SHAPE_BACKEND has no catalog kind — the curation gate blocks it upstream.
}


def _slug(name: str) -> str:
    out = "".join(ch if ch.isalnum() else "-" for ch in (name or "").strip().lower())
    out = "-".join(p for p in out.split("-") if p)
    return out or "candidate"


def to_armory_candidate(c: ExternalCandidate) -> ArmoryCandidate:
    """Map a (curated/promoted) ExternalCandidate → an Armory catalog *proposal* draft.

    Carries provenance + what discovery knows; leaves the selection contract empty on
    purpose (see module docstring) so the catalog gate enforces enrichment.
    """

    return ArmoryCandidate(
        id=_slug(c.name),
        name=c.name,
        kind=_KIND_BY_SHAPE.get(c.install_shape, KIND_TOOL),
        category=c.capability_class,
        summary=c.why_it_matters,
        capability_note=c.capability_class,   # vendor-neutral lens (never a provider name)
        provider_affinity=(c.provider_affinity,) if c.provider_affinity else (),
        source="discovery-intake",
        source_ref=c.repo_url,
        status="draft",
    )


def propose_to_armory(c: ExternalCandidate):
    """Bridge → run the existing Armory promotion gate. Returns its ``PromotionResult``.

    Typically REJECTED for a raw discovery candidate (missing selection contract) — that
    rejection, with its explicit reasons, IS the honest "needs curation" signal. No
    catalog entry is created until the contract is filled.
    """

    return promote_candidate(to_armory_candidate(c))


__all__ = ("to_armory_candidate", "propose_to_armory")
