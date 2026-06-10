"""``yule runtime circuit`` CLI adapter — A-M7-final.

Operator-facing recovery for circuit-open services. The
supervisor parent will keep refusing to restart a service whose
breaker is open until either:

  * the supervisor process restarts (in-memory state cleared), or
  * an operator runs ``yule runtime circuit reset <service_id>``.

The second path lives here. The CLI clears the persisted row,
prints what was cleared, and exits 0 even when the row didn't
exist (idempotent — running it twice is safe).

Inventory awareness: we validate the service_id against the
engineering profile so a typo lands a clear error instead of
silently succeeding on a bogus id.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from yule_runtime.circuit_breaker import (
    CircuitBreakerPersistence,
    CircuitBreakerRegistry,
)
from yule_runtime.services import resolve_service


# Exit codes — match the rest of the runtime CLI surface.
EXIT_OK: int = 0
EXIT_UNKNOWN_SERVICE: int = 78  # systemd EX_CONFIG


def run_circuit_reset_command(
    *,
    service_id: str,
    db_path: Optional[Path] = None,
    emit_json: bool = False,
    persistence: Optional[CircuitBreakerPersistence] = None,
) -> int:
    """Reset the persisted circuit-open state for one service.

    Returns ``EXIT_UNKNOWN_SERVICE`` (78) when *service_id* isn't
    in any inventory profile so an operator's typo can't quietly
    corrupt state. When the row exists we clear it; when it
    doesn't we still print a clear "no circuit was open" message
    and exit 0 (operator might be fixing config and just wants to
    confirm the breaker isn't keeping the service down).
    """

    spec = resolve_service(service_id)
    if spec is None:
        message = (
            f"yule runtime circuit reset: unknown service "
            f"{service_id!r}; check the inventory."
        )
        if emit_json:
            sys.stdout.write(
                json.dumps(
                    {
                        "ok": False,
                        "service_id": service_id,
                        "error": "unknown_service",
                        "message": message,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        else:
            sys.stderr.write(message + "\n")
        return EXIT_UNKNOWN_SERVICE

    store = persistence or CircuitBreakerPersistence(db_path=db_path)
    registry = CircuitBreakerRegistry(persistence=store)
    cleared = registry.reset(service_id)

    payload: dict[str, Any] = {
        "ok": True,
        "service_id": service_id,
        "cleared": bool(cleared),
        "message": (
            f"circuit-open state cleared for {service_id!r}"
            if cleared
            else f"no open circuit was recorded for {service_id!r}"
        ),
    }
    if emit_json:
        sys.stdout.write(
            json.dumps(payload, ensure_ascii=False) + "\n"
        )
    else:
        sys.stdout.write(payload["message"] + "\n")
    return EXIT_OK


__all__ = ("run_circuit_reset_command",)
