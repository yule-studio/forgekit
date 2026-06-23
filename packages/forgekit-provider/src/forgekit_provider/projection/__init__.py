"""forgekit_provider.projection — vendor-neutral tool candidate → provider ecosystem routing.

The *provider projection lane* (issue #406): a collected skill/plugin/tool candidate is
described once (``ToolCandidate``) and deterministically routed to the provider ecosystem(s)
it belongs in (``ProjectionVerdict``) — Claude/Codex/Gemini as projection targets, Ollama as
a backend slot, the two NEVER mixed. See ``docs/plugin-taxonomy.md`` §6.5 +
``docs/provider-capability-matrix.md`` §9b.
"""

from __future__ import annotations

from . import models, rules
from .models import ProjectionVerdict, TargetPlan, ToolCandidate
from .rules import project

__all__ = ("models", "rules", "project", "ToolCandidate", "ProjectionVerdict", "TargetPlan")
