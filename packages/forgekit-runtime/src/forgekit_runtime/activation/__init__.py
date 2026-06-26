"""Install/activation safety lane — external tool/skill/plugin governance.

Closes the org governance backbone for the **activation** path: an external capability
(a tool, skill, or plugin) carries an explicit lifecycle state — ``collected`` →
``curated`` → ``armory-registered`` → (``attachable`` | ``install-required`` |
``approval-needed``) → ``enabled`` → ``executed`` / ``blocked`` — and the ONLY way to
reach an active state is through the SAME real approval gate every other execution uses
(classify → PM→gateway→tech-lead → decision-lane runtime gate → receipt).

The supply-chain rules the lane enforces:

* **"추천됨" ≠ "설치됨" ≠ "실행됨"** — recommendation, enablement, and execution are
  distinct states; a candidate never silently becomes active.
* **install / global-write / external / unknown-safety → at least risky** → approval-gated;
  destructive wording → blocked, never auto.
* **fake "installed" 금지** — an ``enabled``/``executed`` receipt REQUIRES a real
  authorization; the ledger refuses to persist a fake.

Docs SSoT: ``docs/install-safety-lane.md``.
"""

from __future__ import annotations

from .states import (
    ST_APPROVAL_NEEDED,
    ST_ARMORY_REGISTERED,
    ST_ATTACHABLE,
    ST_BLOCKED,
    ST_COLLECTED,
    ST_CURATED,
    ST_ENABLED,
    ST_EXECUTED,
    ST_INSTALL_REQUIRED,
    ACTIVE_STATES,
    ALL_STATES,
    OUTCOME_STATES,
    READINESS_STATES,
    RECOMMENDATION_STATES,
    TERMINAL_STATES,
    ActivationCandidate,
    can_transition,
    derive_readiness_state,
    next_states,
)
from .classify import (
    ACT_ATTACH,
    ACT_ENABLE,
    ACT_EXECUTE,
    ACT_INSTALL,
    ACTIONS,
    BLOCKED,
    RISKY,
    SAFE,
    ActivationClassification,
    classify_activation,
)
from .receipt import (
    OUTCOME_AWAITING,
    OUTCOME_BLOCKED,
    OUTCOME_ENABLED,
    OUTCOME_ERROR,
    OUTCOME_EXECUTED,
    OUTCOME_TO_STATE,
    OUTCOMES,
    ActivationReceipt,
    validate_activation_receipt,
)
from .bridge import activate, authorize_activation
from .ledger import (
    FakeActivationRefused,
    activation_ledger_path,
    latest_states,
    read_activation_receipts,
    record_activation_receipt,
)
from .ledger_view import activation_ledger_lines

__all__ = (
    # states
    "ST_COLLECTED", "ST_CURATED", "ST_ARMORY_REGISTERED", "ST_ATTACHABLE",
    "ST_INSTALL_REQUIRED", "ST_APPROVAL_NEEDED", "ST_ENABLED", "ST_EXECUTED", "ST_BLOCKED",
    "ALL_STATES", "RECOMMENDATION_STATES", "READINESS_STATES", "OUTCOME_STATES",
    "TERMINAL_STATES", "ACTIVE_STATES",
    "ActivationCandidate", "can_transition", "next_states", "derive_readiness_state",
    # classify
    "SAFE", "RISKY", "BLOCKED", "ACT_ATTACH", "ACT_INSTALL", "ACT_ENABLE", "ACT_EXECUTE",
    "ACTIONS", "ActivationClassification", "classify_activation",
    # receipt
    "OUTCOME_ENABLED", "OUTCOME_EXECUTED", "OUTCOME_BLOCKED", "OUTCOME_AWAITING",
    "OUTCOME_ERROR", "OUTCOMES", "OUTCOME_TO_STATE",
    "ActivationReceipt", "validate_activation_receipt",
    # bridge
    "authorize_activation", "activate",
    # ledger
    "FakeActivationRefused", "activation_ledger_path", "record_activation_receipt",
    "read_activation_receipts", "latest_states", "activation_ledger_lines",
)
