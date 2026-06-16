"""Product-intake models — the structured packet a PM produces before engineering.

The product-manager acts as the *intake gate* in front of the engineering
gateway: it turns a raw user ask ("영상 업로드 서비스 구현해줘") into a
:class:`ProductIntentPacket` — auto-filled defaults + a small set of decision
questions + implied features + acceptance criteria + non-goals — so tech-lead and
engineering roles move on a structured spec, not a raw request.

All pure / stdlib so the shaping + question policy are unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple

# Readiness verdicts the gate emits.
READINESS_CLARIFICATION = "clarification_needed"
READINESS_SPEC_READY = "spec_ready"
READINESS_IMPLEMENTATION_CANDIDATE = "implementation_candidate"
READINESS_RESEARCH_ONLY = "research_only"
READINESS_BLOCKED = "blocked"

# FeatureGap kinds.
GAP_IMPLIED = "implied"            # must exist for a real service; auto-added
GAP_RECOMMENDED_DEFAULT = "recommended_default"  # PM picks a safe default
GAP_BASELINE = "baseline"         # cross-cutting (loading/empty/error/validation…)

# DecisionQuestion categories, highest-priority first (ask these, auto-fill rest).
CATEGORY_PRIORITY: Tuple[str, ...] = (
    "destructive",
    "billing",
    "permission",
    "visibility",
    "publish",
    "ordering",
    "external_integration",
)


@dataclass(frozen=True)
class QuestionOption:
    label: str
    recommended: bool = False


@dataclass(frozen=True)
class DecisionQuestion:
    """A short, option-shaped business decision the user must make."""

    id: str
    prompt: str
    category: str
    options: Tuple[QuestionOption, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "category": self.category,
            "options": [{"label": o.label, "recommended": o.recommended} for o in self.options],
        }


@dataclass(frozen=True)
class FeatureGap:
    """A feature the raw ask didn't mention but a real service needs."""

    name: str
    kind: str          # GAP_*
    family: str = ""   # feature family that implied it
    note: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "family": self.family, "note": self.note}


@dataclass(frozen=True)
class FeatureFamily:
    """A recognised product surface (media_upload, admin_crud, …)."""

    key: str
    label: str
    implied: Tuple[str, ...] = ()
    ask: Tuple[str, ...] = ()              # decision keys to surface as questions
    recommended_defaults: Tuple[str, ...] = ()
    suggested_roles: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductReadinessVerdict:
    readiness: str
    rationale: str
    source_signals: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "readiness": self.readiness,
            "rationale": self.rationale,
            "source_signals": list(self.source_signals),
        }


@dataclass(frozen=True)
class ProductIntentPacket:
    """The structured product spec handed to tech-lead / engineering."""

    user_goal: str
    target_user: str
    problem_statement: str
    core_flow: Tuple[str, ...] = ()
    required_features: Tuple[str, ...] = ()
    implied_features: Tuple[FeatureGap, ...] = ()
    recommended_defaults: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    user_decisions_needed: Tuple[str, ...] = ()
    decision_questions: Tuple[DecisionQuestion, ...] = ()
    non_goals: Tuple[str, ...] = ()
    acceptance_criteria: Tuple[str, ...] = ()
    suggested_roles: Tuple[str, ...] = ()
    detected_families: Tuple[str, ...] = ()
    readiness: ProductReadinessVerdict = field(
        default_factory=lambda: ProductReadinessVerdict(READINESS_CLARIFICATION, "")
    )

    def to_dict(self) -> dict:
        return {
            "user_goal": self.user_goal,
            "target_user": self.target_user,
            "problem_statement": self.problem_statement,
            "core_flow": list(self.core_flow),
            "required_features": list(self.required_features),
            "implied_features": [g.to_dict() for g in self.implied_features],
            "recommended_defaults": list(self.recommended_defaults),
            "assumptions": list(self.assumptions),
            "user_decisions_needed": list(self.user_decisions_needed),
            "decision_questions": [q.to_dict() for q in self.decision_questions],
            "non_goals": list(self.non_goals),
            "acceptance_criteria": list(self.acceptance_criteria),
            "suggested_roles": list(self.suggested_roles),
            "detected_families": list(self.detected_families),
            "readiness": self.readiness.to_dict(),
        }


__all__ = (
    "READINESS_CLARIFICATION", "READINESS_SPEC_READY", "READINESS_IMPLEMENTATION_CANDIDATE",
    "READINESS_RESEARCH_ONLY", "READINESS_BLOCKED",
    "GAP_IMPLIED", "GAP_RECOMMENDED_DEFAULT", "GAP_BASELINE", "CATEGORY_PRIORITY",
    "QuestionOption", "DecisionQuestion", "FeatureGap", "FeatureFamily",
    "ProductReadinessVerdict", "ProductIntentPacket",
)
