"""Forge governance ledger — operator-facing render (read-only projection).

The append-only ledger (:mod:`forgekit_runtime.forge.ledger`) is the durable decision
log; this module turns its raw entries into the lines an operator surface (e.g. the
console `/resolve ledger`) shows. Pure + best-effort: a render must never raise, and an
empty ledger renders an honest "no receipts yet" line — not a fake row.

Kept here (forge/*) so the router stays thin: it calls one helper and prints the lines.
"""

from __future__ import annotations

from typing import List, Mapping, Optional

from .ledger import read_forge_receipts

_EMPTY = "forge governance ledger — 기록된 receipt 없음 (`/resolve apply <요청>` 로 영속)"


def _entry_line(idx: int, entry: dict) -> str:
    """One receipt → one compact audit line (request / decision / level / signoff / ts)."""

    receipt = entry.get("receipt", {}) if isinstance(entry, dict) else {}
    request = (receipt.get("request", "") or "")[:48]
    outcome = receipt.get("outcome", "") or "-"
    authorized = receipt.get("authorized", False)
    decision = ("인가" if authorized else "차단") + f"/{outcome}"
    action_class = receipt.get("action_class", "") or "-"
    level = receipt.get("approval_level", "") or "-"
    signoff = (receipt.get("approval_metadata", "") or "-")[:40]
    ts = entry.get("recorded_at", "") if isinstance(entry, dict) else ""
    line = (f"{idx:>2}. {decision} [{action_class}/{level}] "
            f"req={request!r} signoff={signoff}")
    if ts:
        line += f" @ {ts}"
    return line


def forge_ledger_lines(
    *,
    env: Optional[Mapping[str, str]] = None,
    limit: int = 10,
) -> tuple:
    """Render the append-only forge ledger (newest last). Empty → honest single line."""

    try:
        entries = read_forge_receipts(env=env, limit=limit)
    except Exception:  # noqa: BLE001 — a render must never break the surface
        entries = []
    if not entries:
        return (_EMPTY,)
    out: List[str] = [f"forge governance ledger — 최근 {len(entries)}건 (오래된→최신):"]
    for i, entry in enumerate(entries, start=1):
        out.append("  " + _entry_line(i, entry))
    return tuple(out)


__all__ = ("forge_ledger_lines",)
