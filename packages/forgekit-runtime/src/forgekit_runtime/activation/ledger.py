"""Activation decision log — append-only receipt ledger (the runtime-loop 흔적 + 영속).

Two requirements meet here:

1. **execution receipt/evidence가 남아야 함** — every activation verdict (granted OR
   blocked) is appended as one line, so the decision is durable, not ephemeral console
   output.
2. **operator가 나중에 "왜 이 도구를 썼는지" 알 수 있어야 함** — each entry carries the
   candidate, source, classification, approval metadata, and the ``evidence`` (why).

It is an append-only JSONL under the runtime state dir (NOT the vault — that is a separate
evidence track), mirroring :mod:`forgekit_runtime.forge.ledger`. Anti-fake at the
persistence boundary: :func:`record_activation_receipt` re-runs the validator and REFUSES
to persist a fake (e.g. an "installed" claim with no authorization) — a fake never enters
the durable log. Best-effort on I/O; a hard refusal on a fake.

:func:`latest_states` folds the log into each candidate's last known lifecycle state — the
runtime's activation memory, with no second store file to keep in sync.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from forgekit_config.paths import state_dir

from .receipt import ActivationReceipt, validate_activation_receipt

_LEDGER_NAME = "activation_receipts.jsonl"


class FakeActivationRefused(ValueError):
    """Raised when a caller tries to persist a receipt that fails validation."""


def activation_ledger_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """The append-only activation decision log (JSONL) under the runtime state dir."""

    return state_dir(env) / _LEDGER_NAME


def record_activation_receipt(
    receipt: ActivationReceipt,
    *,
    env: Optional[Mapping[str, str]] = None,
    recorded_at: str = "",
) -> Optional[Path]:
    """Append *receipt* to the activation log. Refuses a fake (validation-failing) receipt.

    Returns the ledger path on success, ``None`` on a best-effort I/O failure (the
    decision is never corrupted by a store problem). A FAKE receipt is a hard refusal."""

    violations = validate_activation_receipt(receipt)
    if violations:
        raise FakeActivationRefused("; ".join(violations))

    entry = {"receipt": receipt.to_dict()}
    if recorded_at:
        entry["recorded_at"] = recorded_at
    line = json.dumps(entry, ensure_ascii=False)
    try:
        path = activation_ledger_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return path
    except OSError:
        return None


def read_activation_receipts(
    *,
    env: Optional[Mapping[str, str]] = None,
    limit: int = 0,
) -> List[dict]:
    """Read recorded receipt entries (newest last). ``limit>0`` returns the last N."""

    path = activation_ledger_path(env)
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


def latest_states(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Fold the log → each candidate's LAST recorded lifecycle state (its current state).

    The runtime's activation memory: re-reading the append-only log in order, the last
    entry for a candidate wins. Answers "what state is this tool in right now" without a
    second persisted store."""

    states: Dict[str, str] = {}
    for entry in read_activation_receipts(env=env):
        receipt = entry.get("receipt", {}) if isinstance(entry, dict) else {}
        cid = receipt.get("candidate_id", "")
        to_state = receipt.get("to_state", "")
        if cid and to_state:
            states[cid] = to_state
    return states


__all__ = (
    "FakeActivationRefused", "activation_ledger_path", "record_activation_receipt",
    "read_activation_receipts", "latest_states",
)
