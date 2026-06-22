"""Discovery sweep loop — wire free-first collectors → idea pipeline → operator digest.

The missing seam between the source registry (cost-first live vs planned) and the
idea-discovery pipeline (signals → gap map → idea briefs). One sweep:

  1. collects from the **LIVE free-first** sources only — planned seams (YouTube /
     Instagram / paid Google) stay honestly empty, never faked;
  2. runs the idea-discovery pipeline over the collected signals + any operator text;
  3. frames the result as an **operator digest** that answers
     "어떤 아이디어가 왜 올라왔는지 / 다음에 무엇을 물어봐야 하는지".

A high-value brief promotes to a PM→gateway→tech-lead handoff (:func:`promote_brief`),
and the top brief persists as a retrieval-friendly **authored vault note** so the
discovery output accumulates into the knowledge plane (:func:`persist_brief`).

Pure + deterministic: with no ``fetcher`` the network collectors return ``[]``
(honest), so the sweep runs offline in CI on the repo-local source alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import models as M
from .pipeline import promote_to_handoff, run_idea_discovery, shape_signals


# --- "next question" — what the operator must decide to advance a brief --------
def next_questions_for(brief: M.IdeaBrief) -> Tuple[str, ...]:
    """Deterministic follow-ups an operator should answer to move a brief forward.

    These are decisions the brief can't make for itself — they gate promotion to a
    real PM packet. Derived from what the brief is still *missing*, never asserting a
    certainty the discovery pass doesn't have."""

    qs: List[str] = []
    if not brief.target_user or brief.target_user == "일반 사용자":
        qs.append("핵심 사용자(target_user)는 누구인가? — 지금은 기본값 '일반 사용자'")
    if brief.references:
        qs.append("어떤 경쟁/대체재를 기준으로 차별화 가설을 검증할까?")
    else:
        qs.append("근거가 될 1차 레퍼런스(경쟁/사용자 신호)를 더 모을까?")
    qs.append("이 문제에 사용자가 비용을 낼 의향(pricing 가설)은?")
    if brief.next_experiment.experiment:
        qs.append(f"다음 실험을 실제로 돌릴까? — {brief.next_experiment.experiment[:60]}")
    return tuple(qs)


def _why_surfaced(brief: M.IdeaBrief, by_text: Dict[str, M.OpportunitySignal]) -> str:
    sig = by_text.get(brief.problem)
    src = (sig.source_id if sig else "") or "operator"
    kind = sig.kind if sig else M.SIGNAL_PAIN
    return f"{kind} 신호 · 출처 {src} · score {brief.score}"


# --- digest --------------------------------------------------------------------
@dataclass(frozen=True)
class DiscoveryDigest:
    """Operator-facing digest: which idea surfaced, WHY, and what to ask next."""

    live_sources: Tuple[str, ...] = ()
    planned_sources: Tuple[str, ...] = ()
    collected: int = 0
    competitor_count: int = 0
    gap_count: int = 0
    brief_count: int = 0
    self_improve_count: int = 0
    entries: Tuple[dict, ...] = ()   # {title, problem, why, score, next_questions}

    def to_dict(self) -> dict:
        return {
            "live_sources": list(self.live_sources),
            "planned_sources": list(self.planned_sources),
            "collected": self.collected,
            "competitor_count": self.competitor_count,
            "gap_count": self.gap_count,
            "brief_count": self.brief_count,
            "self_improve_count": self.self_improve_count,
            "entries": list(self.entries),
        }

    def lines(self) -> Tuple[str, ...]:
        out: List[str] = [
            "discovery — operator digest",
            f"- live 수집원(무료 우선): {', '.join(self.live_sources) or '(없음)'}",
            f"- planned(미연결 — fake-live 아님): {', '.join(self.planned_sources) or '(없음)'}",
            f"- 수집 신호: {self.collected}건 · 경쟁 {self.competitor_count} · gap {self.gap_count}",
            f"- 아이디어 brief: {self.brief_count}건 · self-improve 신호: {self.self_improve_count}건",
        ]
        if not self.entries:
            out.append("  (아직 승격할 brief 없음 — 수집원을 연결하거나 신호를 추가하세요)")
        for i, e in enumerate(self.entries, 1):
            out.append(f"[{i}] {e['title']}")
            out.append(f"    왜: {e['why']}")
            for q in e.get("next_questions", ()):
                out.append(f"    물어볼 것: {q}")
        out.append("주의: planned 수집원은 미연결이라 신호 0 — 절대 가짜로 채우지 않음. "
                   "brief 승격은 PM→gateway→tech-lead 제안일 뿐, 실행 아님.")
        return tuple(out)


@dataclass(frozen=True)
class DiscoverySweep:
    """One discovery pass: the pipeline result + source health + operator digest."""

    result: M.DiscoveryResult
    digest: DiscoveryDigest
    source_rows: Tuple[dict, ...] = ()

    @property
    def top_brief(self) -> Optional[M.IdeaBrief]:
        return self.result.top_brief

    @property
    def briefs(self) -> Tuple[M.IdeaBrief, ...]:
        return self.result.idea_briefs

    def to_dict(self) -> dict:
        return {
            "result": self.result.to_dict(),
            "digest": self.digest.to_dict(),
            "source_rows": list(self.source_rows),
        }


def run_discovery_sweep(
    repo_root,
    *,
    fetcher=None,
    rss_feeds: Tuple[Tuple[str, str], ...] = (),
    extra_signals: Sequence[str] = (),
    limit_per: int = 8,
    max_briefs: int = 3,
    title: str = "discovery sweep",
) -> DiscoverySweep:
    """Collect from live free-first sources → idea-discovery → operator digest.

    Network collectors use *fetcher* (None → real urllib; offline → honest empty).
    Planned seams contribute nothing (never faked). *extra_signals* are operator
    text folded in alongside the collected items.
    """

    from ..sources import default_registry

    registry = default_registry(repo_root, fetcher=fetcher, rss_feeds=rss_feeds)
    collected = registry.collect_all(limit_per=limit_per)
    # free-first order is preserved by cost_ordered_live() inside collect_all().
    items: List[object] = [it for bucket in collected.values() for it in bucket]
    items.extend(extra_signals)

    result = run_idea_discovery(items, title=title)
    by_text = {s.text: s for s in shape_signals(items)}

    entries: List[dict] = []
    for brief in result.idea_briefs[:max_briefs]:
        entries.append({
            "title": brief.title,
            "problem": brief.problem,
            "why": _why_surfaced(brief, by_text),
            "score": brief.score,
            "next_questions": list(next_questions_for(brief)),
        })

    digest = DiscoveryDigest(
        live_sources=tuple(c.spec.id for c in registry.cost_ordered_live()),
        planned_sources=tuple(c.spec.id for c in registry.planned()),
        collected=len(items),
        competitor_count=len(result.gap_map.competitors),
        gap_count=len(result.gap_map.gaps),
        brief_count=len(result.idea_briefs),
        self_improve_count=len(result.self_improve_signals),
        entries=tuple(entries),
    )
    return DiscoverySweep(result=result, digest=digest,
                          source_rows=registry.status_rows())


# --- promotion + knowledge-plane persistence ----------------------------------
def promote_brief(brief: M.IdeaBrief, *, project: str = ""):
    """Promote a brief to a PM→gateway→tech-lead handoff packet (proposal only)."""

    return promote_to_handoff(brief, project=project)


def _slug(text: str, *, limit: int = 40) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("-" if c in bad or c.isspace() else c for c in (text or "").strip())
    out = "-".join(p for p in out.split("-") if p)  # collapse runs
    return (out[:limit] or "idea").rstrip("-")


def brief_to_authored_note(
    brief: M.IdeaBrief,
    *,
    author: str = "user-researcher",
    created_at: str = "",
    related: Sequence[str] = (),
) -> str:
    """A retrieval-friendly authored vault note for a discovery brief.

    Authored (author/role/color/cssclass/callout via the identity registry) so the
    vault visibly records WHO surfaced the idea. Body keeps the repo's note shape —
    핵심 요약 / 문제·근거 / 차별화 가설 / 다음 실험 / 참고 — with links + tags so it
    stays retrieval-friendly (not a hollow stub)."""

    from nexus.vault.note import build_authored_note

    refs = brief.references or ()
    ref_lines = [f"- [{r.get('source_id', '?')}] {r.get('title', '')}"
                 + (f" — {r.get('url')}" if r.get("url") else "")
                 for r in refs] or ["- (수집된 1차 레퍼런스 없음 — 추가 수집 후보)"]
    qs = next_questions_for(brief)
    body = "\n".join([
        "## 핵심 요약",
        f"- {brief.title} (score {brief.score})",
        f"- 대상 사용자: {brief.target_user}",
        "",
        "## 문제 · 근거",
        f"- 문제: {brief.problem}",
        f"- 근거: {brief.differentiation.rationale or '(gap 관측 기반)'}",
        "",
        "## 차별화 가설",
        f"- {brief.differentiation.hypothesis or '(미정)'}",
        "",
        "## 다음 실험",
        f"- {brief.next_experiment.experiment or '(미정)'}",
        f"- 성공 지표: {brief.next_experiment.success_metric or '(미정)'}",
        "",
        "## operator 결정 대기",
        *[f"- {q}" for q in qs],
        "",
        "## 참고",
        *ref_lines,
    ])
    return build_authored_note(
        author,
        title=brief.title,
        body=body,
        kind="idea-brief",
        status="draft",
        created_at=created_at,
        phase="discovery",
        source_flow="discovery-sweep",
        handoff_from="discovery",
        handoff_to="pm",
        tags=("forgekit", "discovery", "idea-brief"),
        related=tuple(related),
    )


def persist_brief(
    brief: M.IdeaBrief,
    vault_root,
    *,
    author: str = "user-researcher",
    created_at: str = "",
    subdir: str = "00-inbox/discovery",
):
    """Write an authored brief note under the connected vault (None if not writable).

    Lands in ``00-inbox`` (raw intake — honest: not claimed as a curated note). The
    note still carries full frontmatter + body sections so it is retrieval-friendly.
    Returns the written path, or ``None`` if there is no vault root / write fails."""

    if not vault_root:
        return None
    from nexus.vault.note import write_note

    content = brief_to_authored_note(brief, author=author, created_at=created_at)
    subpath = f"{subdir}/idea-{_slug(brief.problem)}.md"
    return write_note(content, vault_root, subpath)


__all__ = (
    "DiscoveryDigest", "DiscoverySweep", "run_discovery_sweep",
    "next_questions_for", "promote_brief", "brief_to_authored_note", "persist_brief",
)
