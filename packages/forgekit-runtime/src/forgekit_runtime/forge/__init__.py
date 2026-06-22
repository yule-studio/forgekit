"""Hephaistos forge → governance execution binding (the "execution core" gated).

Closes the org governance backbone for the forging path: a Hephaistos forge plan is
classified (safe/risky/destructive, incl. weapon safety), run through the SAME real
approval gate as every other execution (PM→gateway→tech-lead → decision-lane runtime gate
→ validate_execution), and proven by a :class:`ForgeExecutionReceipt`. No fake approval /
no fake receipt: a blocked plan is never trailer-stamped and never "executed".

Docs SSoT: ``docs/hephaistos-governance.md``.
"""

from __future__ import annotations

from .classify import (
    DESTRUCTIVE,
    RISKY,
    SAFE,
    ForgeClassification,
    classify_forge_plan,
)
from .receipt import (
    OUTCOME_AWAITING,
    OUTCOME_BLOCKED,
    OUTCOME_ERROR,
    OUTCOME_EXECUTED,
    ForgeExecutionReceipt,
    validate_forge_receipt,
)
from .bridge import authorize_forge_plan, forge_execute
from .ledger import (
    FakeReceiptRefused,
    forge_receipt_ledger_path,
    read_forge_receipts,
    record_forge_receipt,
)
from .ledger_view import forge_ledger_lines

__all__ = (
    "SAFE", "RISKY", "DESTRUCTIVE", "ForgeClassification", "classify_forge_plan",
    "OUTCOME_EXECUTED", "OUTCOME_BLOCKED", "OUTCOME_AWAITING", "OUTCOME_ERROR",
    "ForgeExecutionReceipt", "validate_forge_receipt",
    "authorize_forge_plan", "forge_execute",
    "FakeReceiptRefused", "forge_receipt_ledger_path",
    "record_forge_receipt", "read_forge_receipts",
    "forge_ledger_lines",
)
