"""Repo-improvement packet + risk class (WT4) — bounded self-improvement contract.

A :class:`RepoImprovementPacket` is what self-improvement produces from an observed
gap. Each is risk-classified into SAFE (docs/tests/lint/small refactor — auto-OK
within approval), RISKY (broad change — approval-wait), or BLOCKED (deploy/secret/
infra/migration — never auto, runbook). Pure → testable + serialisable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

RISK_SAFE = "safe"
RISK_RISKY = "risky"
RISK_BLOCKED = "blocked"

# areas that are NEVER auto (privileged) → always BLOCKED
_BLOCKED_AREAS = ("deploy", "secret", "infra", "iam", "migration", "production", "배포", "인프라")
# areas that are broad/risky → approval-wait
_RISKY_AREAS = ("rewrite", "broad", "schema", "auth", "권한", "대규모")


@dataclass(frozen=True)
class RepoImprovementPacket:
    finding: str
    why_it_matters: str = ""
    affected_area: str = ""
    risk: str = RISK_SAFE
    proposed_change: str = ""
    confidence: float = 0.5
    approval_needed: bool = False
    recommended_owner: str = "tech-lead"
    source_origin: str = "repo-local"
    user_discomfort: str = ""

    def to_dict(self) -> dict:
        return {
            "finding": self.finding, "why_it_matters": self.why_it_matters,
            "affected_area": self.affected_area, "risk": self.risk,
            "proposed_change": self.proposed_change, "confidence": self.confidence,
            "approval_needed": self.approval_needed, "recommended_owner": self.recommended_owner,
            "source_origin": self.source_origin, "user_discomfort": self.user_discomfort,
        }


def classify_risk(finding: str, area: str = "") -> str:
    """Classify an improvement into safe / risky / blocked by its area/wording."""

    blob = f"{finding} {area}".lower()
    if any(k in blob for k in _BLOCKED_AREAS):
        return RISK_BLOCKED
    if any(k in blob for k in _RISKY_AREAS):
        return RISK_RISKY
    return RISK_SAFE


def make_packet(finding: str, *, why: str = "", area: str = "", change: str = "",
                confidence: float = 0.6, owner: str = "tech-lead",
                origin: str = "repo-local", discomfort: str = "") -> RepoImprovementPacket:
    risk = classify_risk(finding, area)
    return RepoImprovementPacket(
        finding=finding, why_it_matters=why, affected_area=area, risk=risk,
        proposed_change=change, confidence=confidence,
        approval_needed=risk != RISK_SAFE,   # only SAFE is auto-OK (within approval chain)
        recommended_owner=owner, source_origin=origin, user_discomfort=discomfort,
    )


__all__ = (
    "RISK_SAFE", "RISK_RISKY", "RISK_BLOCKED",
    "RepoImprovementPacket", "classify_risk", "make_packet",
)
