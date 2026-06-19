"""forgekit-runtime — ForgeKit's bounded execution core (WT2).

The owner of "what ForgeKit does over time, under operator gating":
- ``runtime``     — bounded always-on loop (observe→classify→packet→handoff→wait),
                    daemon control, heartbeat, runbook fallback
- ``autopilot``   — safe-class autopilot (observe/tick)
- ``lifecycle``   — failure escalation + operator action inbox
- ``notify``      — approval/alert inbox surface
- ``selfimprove`` — self-improvement loop
- ``security``    — red/blue planning (plan-only)

Depends only on ForgeKit packages (``forgekit-config`` paths, ``forgekit-provider``
usage, ``forgekit-contracts`` models, ``nexus`` sources) — never on an app. Two app
seams are honest, documented boundaries, NOT package→app imports:
- the intake→packet **handoff** is injected by the operator app via
  ``runtime.loop.register_handoff_runner`` (the bridge lives in the app);
- ``lifecycle`` mirrors into the heavy ``yule_engineering`` troubleshooting ledger as a
  **best-effort, lazy, try/excepted** call that degrades to a no-op when absent
  (remaining debt → agent-contracts event, WT4).

Owner matrix: ``docs/forgekit-architecture-ownership.md``.
"""

from __future__ import annotations

__all__ = ("runtime", "autopilot", "lifecycle", "notify", "selfimprove", "security")
