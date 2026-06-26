"""Activation ledger — operator-facing render (read-only projection, no UI layer).

The append-only ledger (:mod:`forgekit_runtime.activation.ledger`) is the durable record;
this turns its raw entries into the lines an operator surface (e.g. a console
`/activation ledger`) prints. Deliberately a thin line projection — no new widget,
wrapper, or animation abstraction — mirroring ``forge.ledger_view``. Pure + best-effort:
a render never raises, and an empty ledger renders an honest "no receipts yet" line.
"""

from __future__ import annotations

from typing import List, Mapping, Optional

from .ledger import read_activation_receipts

_EMPTY = "activation ledger — 기록된 receipt 없음 (도구를 활성화하면 영속됨)"


def _entry_line(idx: int, entry: dict) -> str:
    """One receipt → one compact audit line (decision / class / candidate / why / ts)."""

    receipt = entry.get("receipt", {}) if isinstance(entry, dict) else {}
    cand = f"{receipt.get('kind', 'tool')}:{receipt.get('candidate_id', '') or '-'}"
    outcome = receipt.get("outcome", "") or "-"
    authorized = receipt.get("authorized", False)
    decision = ("인가" if authorized else "차단") + f"/{outcome}"
    disposition = receipt.get("disposition", "") or "-"
    action = receipt.get("action", "") or "-"
    why = (receipt.get("evidence", "") or "-")[:40]
    ts = entry.get("recorded_at", "") if isinstance(entry, dict) else ""
    line = (f"{idx:>2}. {decision} [{disposition}] {action} {cand} why={why!r}")
    if ts:
        line += f" @ {ts}"
    return line


def activation_ledger_lines(
    *,
    env: Optional[Mapping[str, str]] = None,
    limit: int = 10,
) -> tuple:
    """Render the append-only activation ledger (newest last). Empty → honest single line."""

    try:
        entries = read_activation_receipts(env=env, limit=limit)
    except Exception:  # noqa: BLE001 — a render must never break the surface
        entries = []
    if not entries:
        return (_EMPTY,)
    out: List[str] = [f"activation ledger — 최근 {len(entries)}건 (오래된→최신):"]
    for i, entry in enumerate(entries, start=1):
        out.append("  " + _entry_line(i, entry))
    return tuple(out)


__all__ = ("activation_ledger_lines",)
