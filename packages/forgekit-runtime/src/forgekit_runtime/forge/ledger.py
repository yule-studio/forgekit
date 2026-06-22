"""Forge governance decision log — append-only receipt ledger (the "영속" seam).

A receipt proves one forge plan's trip through the gate; the LEDGER makes that proof
**accumulate** so the decision log is real and durable, not ephemeral console output. It
is an append-only JSONL under the local runtime state dir (NOT the vault — that's a
separate evidence track), one line per recorded receipt.

Anti-fake at the persistence boundary: :func:`record_forge_receipt` re-runs
:func:`validate_forge_receipt` and REFUSES to persist a receipt that fails — so a fake
approval/execution can never enter the durable log. Best-effort on I/O (a store failure
never corrupts the decision), but a fake receipt is a hard refusal, not a silent skip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Mapping, Optional

from forgekit_config.paths import state_dir

from .receipt import ForgeExecutionReceipt, validate_forge_receipt

_LEDGER_NAME = "forge_receipts.jsonl"


class FakeReceiptRefused(ValueError):
    """Raised when a caller tries to persist a receipt that fails validation."""


def forge_receipt_ledger_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """The append-only forge-receipt decision log (JSONL) under the runtime state dir."""

    return state_dir(env) / _LEDGER_NAME


def record_forge_receipt(
    receipt: ForgeExecutionReceipt,
    *,
    env: Optional[Mapping[str, str]] = None,
    recorded_at: str = "",
) -> Optional[Path]:
    """Append *receipt* to the decision log. Refuses a fake (validation-failing) receipt.

    Returns the ledger path on success, ``None`` on a best-effort I/O failure (so the
    decision is never corrupted by a store problem). A FAKE receipt is a hard refusal."""

    violations = validate_forge_receipt(receipt)
    if violations:
        raise FakeReceiptRefused("; ".join(violations))

    entry = {"receipt": receipt.to_dict()}
    if recorded_at:
        entry["recorded_at"] = recorded_at
    line = json.dumps(entry, ensure_ascii=False)
    try:
        path = forge_receipt_ledger_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return path
    except OSError:
        return None


def read_forge_receipts(
    *,
    env: Optional[Mapping[str, str]] = None,
    limit: int = 0,
) -> List[dict]:
    """Read recorded receipt entries (newest last). ``limit>0`` returns the last N."""

    path = forge_receipt_ledger_path(env)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: List[dict] = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            entries.append(json.loads(ln))
        except ValueError:
            continue
    return entries[-limit:] if limit > 0 else entries


__all__ = (
    "FakeReceiptRefused", "forge_receipt_ledger_path",
    "record_forge_receipt", "read_forge_receipts",
)
