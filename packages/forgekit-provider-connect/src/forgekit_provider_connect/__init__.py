"""forgekit-provider-connect — provider onboarding / connect / doctor (control plane).

The onboarding layer ON TOP of the provider policy/routing core (``forgekit-provider``):
detect & honestly diagnose each provider's connection (CLI attach / API key / local
daemon), run the ``/setup`` bootstrap flow, and persist through the single config writer.
Never fakes a connection or a live transport. See ``docs/control-plane-architecture.md`` §5.1.
"""

from __future__ import annotations

from . import diagnose, probe, status, surface, wizard

__all__ = ("status", "probe", "diagnose", "wizard", "surface")
