"""Idea-discovery pipeline (WT3) — signals → gap map + idea briefs → handoff.

Consumes collected source items (WT2) or operator-provided text, classifies them
into opportunity signals, builds a competitor gap map + reference bundle, and emits
idea briefs (with a differentiation hypothesis + next experiment). High-scoring
briefs can be promoted to a PM/gateway handoff (WT2); "forgekit itself should
improve" signals are split out for the self-improvement loop (WT4). Pure +
deterministic so it runs offline in CI.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from . import models as M

# keyword cues for classifying a raw signal -----------------------------------
_PAIN = ("불편", "안 됨", "느림", "어렵", "pain", "problem", "issue", "버그", "막힘")
_TREND = ("트렌드", "인기", "급상승", "trend", "growing", "hot", "rising")
_COMPETITOR = ("경쟁", "competitor", "alternative", "vs ", "대체", "기존 제품")
_SELF = ("forgekit", "콘솔 자체", "우리 도구", "self-improve", "내부 개선")


def _classify(text: str) -> str:
    low = (text or "").lower()
    if any(k in low for k in _SELF):
        return M.SIGNAL_SELF_IMPROVE
    if any(k in low for k in _COMPETITOR):
        return M.SIGNAL_COMPETITOR
    if any(k in low for k in _TREND):
        return M.SIGNAL_TREND
    return M.SIGNAL_PAIN


def shape_signals(items: Sequence) -> Tuple[M.OpportunitySignal, ...]:
    """Map collected source items (or strings) → classified opportunity signals."""

    out: List[M.OpportunitySignal] = []
    for it in items:
        if isinstance(it, str):
            text, sid, score = it, "operator", 0.0
        else:
            text = getattr(it, "title", "") or getattr(it, "summary", "")
            sid = getattr(it, "source_id", "")
            score = float(getattr(it, "score", 0.0) or 0.0)
        if text:
            out.append(M.OpportunitySignal(text, source_id=sid, kind=_classify(text), score=score))
    return tuple(out)


def build_reference_bundle(items: Sequence, *, title: str = "reference bundle") -> M.ReferenceBundle:
    """Reference bundle = lightweight refs (id/title/url) — NOT raw payloads."""

    refs = []
    for it in items:
        if isinstance(it, str):
            refs.append({"source_id": "operator", "title": it, "url": ""})
        else:
            refs.append({"source_id": getattr(it, "source_id", ""),
                         "title": getattr(it, "title", ""), "url": getattr(it, "url", "")})
    summary = f"{len(refs)}개 참고 신호 수집"
    return M.ReferenceBundle(title=title, items=tuple(refs), summary=summary)


def build_gap_map(signals: Sequence[M.OpportunitySignal]) -> M.CompetitorGapMap:
    """Competitors (from competitor signals) + gaps (from pain signals)."""

    competitors = tuple(dict.fromkeys(
        s.text for s in signals if s.kind == M.SIGNAL_COMPETITOR))
    gaps = tuple(dict.fromkeys(
        s.text for s in signals if s.kind == M.SIGNAL_PAIN))
    return M.CompetitorGapMap(competitors=competitors, gaps=gaps)


def build_idea_briefs(signals: Sequence[M.OpportunitySignal], gap_map: M.CompetitorGapMap,
                      *, bundle: M.ReferenceBundle) -> Tuple[M.IdeaBrief, ...]:
    """Generate idea briefs from pain/trend signals + the gap map (deterministic)."""

    briefs: List[M.IdeaBrief] = []
    seeds = [s for s in signals if s.kind in (M.SIGNAL_PAIN, M.SIGNAL_TREND)]
    for s in seeds[:5]:
        diff = M.DifferentiationHypothesis(
            hypothesis=f"'{s.text}' 를 기존 대체재보다 더 단순/저비용으로 해결",
            rationale=f"경쟁 {len(gap_map.competitors)}개 대비 gap {len(gap_map.gaps)}개 관측",
        )
        exp = M.NextExperiment(
            experiment=f"'{s.text}' 핵심 흐름의 최소 프로토타입 + 5명 사용자 인터뷰",
            success_metric="핵심 작업 완료율 / 재방문 의향",
        )
        # score: signal score + a small bump per observed gap (deterministic)
        score = round(s.score + 0.5 * len(gap_map.gaps), 2)
        briefs.append(M.IdeaBrief(
            title=f"아이디어: {s.text[:40]}", problem=s.text, differentiation=diff,
            next_experiment=exp, references=bundle.items[:3], score=score))
    return tuple(sorted(briefs, key=lambda b: b.score, reverse=True))


def run_idea_discovery(items: Sequence, *, title: str = "idea discovery") -> M.DiscoveryResult:
    """Full pipeline: items → signals → gap map + bundle → idea briefs (+ self-improve)."""

    signals = shape_signals(items)
    bundle = build_reference_bundle(items, title=title)
    gap_map = build_gap_map(signals)
    briefs = build_idea_briefs(signals, gap_map, bundle=bundle)
    self_improve = tuple(s for s in signals if s.kind == M.SIGNAL_SELF_IMPROVE)
    return M.DiscoveryResult(reference_bundle=bundle, gap_map=gap_map,
                             idea_briefs=briefs, self_improve_signals=self_improve)


def promote_to_handoff(brief: M.IdeaBrief, *, project: str = ""):
    """Promote a high-value idea brief into a PM→gateway→tech-lead handoff (WT2)."""

    from ..handoff import run_handoff

    return run_handoff(brief.problem, project=project or brief.title[:24])


__all__ = (
    "shape_signals", "build_reference_bundle", "build_gap_map", "build_idea_briefs",
    "run_idea_discovery", "promote_to_handoff",
)
