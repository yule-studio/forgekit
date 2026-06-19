"""forgekit-provider — ForgeKit provider/model routing core (WT2).

The owner of "which provider answers, under what policy, at what usage cost":
- ``providers`` — provider spec catalog (builtins / contract / registry)
- ``policy``    — provider config/ops/policy/surface, routing, recommend, setup_state,
                  main_profile, usage_policy, runtime_mode posture, auto_mode
- ``chat``      — the submit service (routing → real call), models, policy gate, usage parse
- ``usage``     — usage ledger (live vs estimate, per provider/model/mode)
- ``brain``     — brain(=primary+linked) construction / packs

Pure/stdlib-first so every ForgeKit app shares one provider contract instead of
reaching into the console. Depends only on ``forgekit-config`` (paths). Submodules
are imported lazily by callers. Owner matrix: ``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

__all__ = ("providers", "policy", "chat", "usage", "brain")
