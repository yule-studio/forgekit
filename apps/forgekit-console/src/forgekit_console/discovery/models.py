"""Idea-discovery contracts (WT3) — signals → gap map → idea brief (pure).

Turns collected source signals into a structured idea pipeline: opportunity signals,
a competitor gap map, a reference bundle, idea briefs with a differentiation
hypothesis and a next experiment. Pure dataclasses → serialisable + testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

# signal kinds ----------------------------------------------------------------
SIGNAL_PAIN = "pain"            # a problem / discomfort signal
SIGNAL_TREND = "trend"          # a rising interest / trend signal
SIGNAL_COMPETITOR = "competitor"  # an existing product / alternative
SIGNAL_SELF_IMPROVE = "self-improve"  # "forgekit itself should improve" signal


@dataclass(frozen=True)
class OpportunitySignal:
    text: str
    source_id: str = ""
    kind: str = SIGNAL_PAIN
    score: float = 0.0

    def to_dict(self) -> dict:
        return {"text": self.text, "source_id": self.source_id, "kind": self.kind,
                "score": self.score}


@dataclass(frozen=True)
class CompetitorGapMap:
    competitors: Tuple[str, ...] = ()
    gaps: Tuple[str, ...] = ()       # unmet needs / weaknesses across competitors

    def to_dict(self) -> dict:
        return {"competitors": list(self.competitors), "gaps": list(self.gaps)}


@dataclass(frozen=True)
class ReferenceBundle:
    title: str
    items: Tuple[dict, ...] = ()     # {source_id,title,url} refs (NOT raw payloads)
    summary: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "items": list(self.items), "summary": self.summary}


@dataclass(frozen=True)
class DifferentiationHypothesis:
    hypothesis: str
    rationale: str = ""

    def to_dict(self) -> dict:
        return {"hypothesis": self.hypothesis, "rationale": self.rationale}

    @classmethod
    def from_dict(cls, d: dict) -> "DifferentiationHypothesis":
        d = d or {}
        return cls(hypothesis=d.get("hypothesis", ""), rationale=d.get("rationale", ""))


@dataclass(frozen=True)
class NextExperiment:
    experiment: str
    success_metric: str = ""

    def to_dict(self) -> dict:
        return {"experiment": self.experiment, "success_metric": self.success_metric}

    @classmethod
    def from_dict(cls, d: dict) -> "NextExperiment":
        d = d or {}
        return cls(experiment=d.get("experiment", ""),
                   success_metric=d.get("success_metric", ""))


@dataclass(frozen=True)
class IdeaBrief:
    title: str
    problem: str
    target_user: str = "일반 사용자"
    differentiation: DifferentiationHypothesis = field(
        default_factory=lambda: DifferentiationHypothesis(""))
    next_experiment: NextExperiment = field(default_factory=lambda: NextExperiment(""))
    references: Tuple[dict, ...] = ()
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "title": self.title, "problem": self.problem, "target_user": self.target_user,
            "differentiation": self.differentiation.to_dict(),
            "next_experiment": self.next_experiment.to_dict(),
            "references": list(self.references), "score": self.score,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IdeaBrief":
        d = d or {}
        return cls(
            title=d.get("title", ""), problem=d.get("problem", ""),
            target_user=d.get("target_user", "일반 사용자"),
            differentiation=DifferentiationHypothesis.from_dict(d.get("differentiation")),
            next_experiment=NextExperiment.from_dict(d.get("next_experiment")),
            references=tuple(d.get("references", ()) or ()),
            score=float(d.get("score", 0.0) or 0.0),
        )


@dataclass(frozen=True)
class DiscoveryResult:
    reference_bundle: ReferenceBundle
    gap_map: CompetitorGapMap
    idea_briefs: Tuple[IdeaBrief, ...] = ()
    self_improve_signals: Tuple[OpportunitySignal, ...] = ()

    @property
    def top_brief(self):
        return max(self.idea_briefs, key=lambda b: b.score) if self.idea_briefs else None

    def to_dict(self) -> dict:
        return {
            "reference_bundle": self.reference_bundle.to_dict(),
            "gap_map": self.gap_map.to_dict(),
            "idea_briefs": [b.to_dict() for b in self.idea_briefs],
            "self_improve_signals": [s.to_dict() for s in self.self_improve_signals],
        }


__all__ = (
    "SIGNAL_PAIN", "SIGNAL_TREND", "SIGNAL_COMPETITOR", "SIGNAL_SELF_IMPROVE",
    "OpportunitySignal", "CompetitorGapMap", "ReferenceBundle",
    "DifferentiationHypothesis", "NextExperiment", "IdeaBrief", "DiscoveryResult",
)
