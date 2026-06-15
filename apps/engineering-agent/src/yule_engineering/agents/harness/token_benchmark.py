"""Token-efficiency benchmark — deterministic scenarios + before/after evidence.

Measurement precedes optimization. This module runs 2-3 reproducible scenarios
over *synthetic fixtures* (no live LLM, no live DB) so baseline vs after are
fully deterministic for every token metric. It exists to produce the evidence
package the task requires:

  * Scenario ``dispatch`` — long role-runner input (previous_decisions +
    source_context). after = compact_decisions + reference_sources.
  * Scenario ``recall`` — memory search results (decision/canonical/reusable
    mixed). after = retrieval boost re-rank + reference-mode (snippet, no body).
  * Scenario ``context`` — context documents (policy-heavy). after = digest
    policy bundle instead of full text.

Each scenario emits :class:`ScenarioMetrics` (the minimum metric set). The
report renders to JSON + Markdown; :func:`compute_delta` produces the
before/after table the evidence package needs. Only ``wall_time_ms`` is
non-deterministic; every token metric is reproducible.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .token_budget import (
    build_policy_bundle,
    compact_decisions,
    estimate_mapping_tokens,
    estimate_tokens,
    reference_sources,
)
from .retrieval_boost import rerank

SCENARIOS: Tuple[str, ...] = ("dispatch", "recall", "context", "bundle")
# A representative deterministic take size — identical across modes, so the
# benchmark never claims output savings it cannot prove.
FIXED_OUTPUT_TOKENS = 256


@dataclass
class ScenarioMetrics:
    scenario: str
    mode: str
    loaded_docs_count: int = 0
    loaded_policies_count: int = 0
    input_tokens_est: int = 0
    output_tokens_est: int = FIXED_OUTPUT_TOKENS
    previous_decisions_size: int = 0
    source_context_size: int = 0
    retrieved_artifacts_count: int = 0
    saved_tokens_by_compaction: int = 0
    selected_runner: str = "deterministic"
    wall_time_ms: int = 0
    warnings_count: int = 0
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        d = {
            "scenario": self.scenario,
            "mode": self.mode,
            "loaded_docs_count": self.loaded_docs_count,
            "loaded_policies_count": self.loaded_policies_count,
            "input_tokens_est": self.input_tokens_est,
            "output_tokens_est": self.output_tokens_est,
            "previous_decisions_size": self.previous_decisions_size,
            "source_context_size": self.source_context_size,
            "retrieved_artifacts_count": self.retrieved_artifacts_count,
            "saved_tokens_by_compaction": self.saved_tokens_by_compaction,
            "selected_runner": self.selected_runner,
            "wall_time_ms": self.wall_time_ms,
            "warnings_count": self.warnings_count,
            "notes": list(self.notes),
        }
        return d


@dataclass
class BenchmarkReport:
    mode: str
    slug: str
    generated_at: str
    scenarios: Tuple[ScenarioMetrics, ...]

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens_est for s in self.scenarios)

    @property
    def total_saved_tokens(self) -> int:
        return sum(s.saved_tokens_by_compaction for s in self.scenarios)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "slug": self.slug,
            "generated_at": self.generated_at,
            "estimator": "chars/4 (ceil)",
            "totals": {
                "input_tokens_est": self.total_input_tokens,
                "saved_tokens_by_compaction": self.total_saved_tokens,
                "output_tokens_est": sum(s.output_tokens_est for s in self.scenarios),
            },
            "scenarios": [s.to_dict() for s in self.scenarios],
        }


# ---------------------------------------------------------------------------
# Deterministic fixtures
# ---------------------------------------------------------------------------


def _fixture_decisions(n: int = 12) -> List[Mapping[str, Any]]:
    out: List[Mapping[str, Any]] = []
    roles = ["backend-engineer", "frontend-engineer", "qa-engineer", "devops-engineer", "ai-engineer"]
    for i in range(n):
        role = roles[i % len(roles)]
        # one early decision (outside the recent-K window) to show the protected
        # region survives folding; the rest are ordinary role takes.
        kind = "decision" if i == 1 else "take"
        out.append(
            {
                "role": role,
                "kind": kind,
                "summary": f"[{role} 의견 {i}] " + ("세부 분석 및 트레이드오프 설명 항목. " * 30),
                "entry_id": f"aud-{i:02d}",
            }
        )
    return out


def _fixture_source_context() -> Mapping[str, Any]:
    return {
        "title": "결제 모듈 연동 리서치 팩",
        "summary": "PG 연동 방식, 멱등성 키 전략, 웹훅 검증, 재시도/보상 트랜잭션 설계. " * 14,
        "sources": [f"https://docs.example.com/payments/{i}" for i in range(8)],
    }


def _fixture_retrieval_results() -> List[Any]:
    """Synthetic MemorySearchResult-like objects (mix of reuse markers)."""

    body = "결정 본문과 근거, 대안, 트레이드오프, 적용 맥락이 길게 적힌 노트. " * 16
    snippet = "결정 본문과 근거, 대안…"
    specs = [
        # (title, note_kind, tags, extra, bm25)
        ("일반 리서치 노트 A", "research", (), {}, -1.20),
        ("canonical 런북", "reference", ("canonical",), {"canonical": "true"}, -0.80),
        ("결정 노트 JWT", "decision", (), {"status": "decided"}, -0.95),
        ("회고 노트", "retrospective", (), {}, -0.70),
        ("reusable 패턴", "reference", ("reusable",), {"reusable": "true"}, -0.60),
        ("일반 리서치 노트 B", "research", (), {}, -1.10),
    ]
    results: List[Any] = []
    for title, note_kind, tags, extra, score in specs:
        doc = SimpleNamespace(
            title=title,
            path=f"10-projects/p/{note_kind}/{title}.md",
            source_kind="obsidian",
            note_kind=note_kind,
            tags=tags,
            extra=extra,
            body=body,
        )
        results.append(SimpleNamespace(document=doc, score=score, snippet=snippet))
    return results


def _fixture_context_docs() -> List[Any]:
    """Synthetic context documents (entrypoint + root + agent + N policies)."""

    long_body = "# 정책 제목\n\n이 정책은 운영 규칙을 정의한다. " + ("상세 규칙 항목. " * 60)
    docs = [
        SimpleNamespace(label="entrypoint", path="AGENTS.md", content="# 진입점\n\n문서 내비게이션. " * 30),
        SimpleNamespace(label="root_instructions", path="CLAUDE.md", content="# 전역 규칙\n\n안전/컨벤션. " * 40),
        SimpleNamespace(label="agent_instructions", path="agents/engineering-agent/CLAUDE.md", content="# 에이전트 규칙\n\n작업 맥락. " * 40),
    ]
    for i in range(10):
        docs.append(
            SimpleNamespace(
                label="policy",
                path=f"policies/runtime/agents/engineering-agent/policy-{i}.md",
                content=long_body,
            )
        )
    return docs


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def _clock_ms(clock: Callable[[], float], start: float) -> int:
    return int(max(0.0, (clock() - start)) * 1000)


def run_dispatch_scenario(mode: str, *, clock: Callable[[], float] = time.perf_counter) -> ScenarioMetrics:
    start = clock()
    decisions = _fixture_decisions()
    source = _fixture_source_context()
    prompt = "결제 모듈 멱등성 설계 검토"
    base = estimate_tokens(prompt)
    warnings = 0
    notes: List[str] = []

    if mode == "baseline":
        prev_tokens = sum(estimate_tokens(str(d.get("summary") or "")) for d in decisions)
        src_tokens = estimate_mapping_tokens(source)
        saved = 0
    else:
        comp = compact_decisions(decisions, threshold_tokens=1200, keep_recent=4)
        ref = reference_sources(source)
        prev_tokens = comp.post_tokens
        src_tokens = ref.post_tokens
        saved = comp.saved_tokens + ref.saved_tokens
        if comp.applied:
            notes.append(f"compaction applied: folded {comp.folded_count} decisions")
            warnings += 1
        notes.append("source_context → reference-mode")

    return ScenarioMetrics(
        scenario="dispatch",
        mode=mode,
        input_tokens_est=base + prev_tokens + src_tokens,
        previous_decisions_size=prev_tokens,
        source_context_size=src_tokens,
        saved_tokens_by_compaction=saved,
        wall_time_ms=_clock_ms(clock, start),
        warnings_count=warnings,
        notes=tuple(notes),
    )


def run_recall_scenario(mode: str, *, limit: int = 3, clock: Callable[[], float] = time.perf_counter) -> ScenarioMetrics:
    start = clock()
    results = _fixture_retrieval_results()
    notes: List[str] = []

    if mode == "baseline":
        # bm25 order (lower score first), full body fed
        ordered = sorted(results, key=lambda r: r.score)[:limit]
        input_tokens = sum(estimate_tokens(getattr(r.document, "body", "")) for r in ordered)
        baseline_tokens = input_tokens
        saved = 0
        top_titles = [r.document.title for r in ordered]
    else:
        boosted = rerank(results)[:limit]
        refs = [b.to_reference() for b in boosted]
        input_tokens = sum(estimate_mapping_tokens(ref) for ref in refs)
        # compute baseline for saved delta (same top-K by bm25, full body)
        bm25_top = sorted(results, key=lambda r: r.score)[:limit]
        baseline_tokens = sum(estimate_tokens(getattr(r.document, "body", "")) for r in bm25_top)
        saved = max(0, baseline_tokens - input_tokens)
        top_titles = [b.title for b in boosted]
        notes.append("boost re-rank + reference-mode (snippet, no body)")

    notes.append("top: " + ", ".join(top_titles))
    return ScenarioMetrics(
        scenario="recall",
        mode=mode,
        input_tokens_est=input_tokens,
        retrieved_artifacts_count=limit,
        saved_tokens_by_compaction=saved,
        wall_time_ms=_clock_ms(clock, start),
        warnings_count=0,
        notes=tuple(notes),
    )


def run_context_scenario(mode: str, *, clock: Callable[[], float] = time.perf_counter) -> ScenarioMetrics:
    start = clock()
    docs = _fixture_context_docs()
    bundle = build_policy_bundle(docs, mode="full" if mode == "baseline" else "digest")
    policy_count = sum(1 for d in docs if getattr(d, "label", "") == "policy")
    notes: List[str] = []
    if mode != "baseline":
        notes.append("policy bundle → digest (heading + first paragraph + pointer)")
    return ScenarioMetrics(
        scenario="context",
        mode=mode,
        loaded_docs_count=len(docs),
        loaded_policies_count=policy_count,
        input_tokens_est=bundle.fed_tokens,
        saved_tokens_by_compaction=bundle.saved_tokens,
        wall_time_ms=_clock_ms(clock, start),
        warnings_count=0,
        notes=tuple(notes),
    )


def _fixture_bundle_docs() -> List[Any]:
    """Instruction layers + named policies (real-ish stems for the selector)."""

    long_body = "# 정책 제목\n\n운영 규칙을 정의한다. " + ("상세 규칙 항목. " * 120)
    instr = [
        SimpleNamespace(label="entrypoint", path="AGENTS.md", content="# 진입점\n\n내비게이션."),
        SimpleNamespace(label="root_instructions", path="CLAUDE.md", content="# 전역 규칙\n\n안전."),
        SimpleNamespace(label="agent_instructions", path="agents/engineering-agent/CLAUDE.md", content="# 에이전트."),
    ]
    stems = [
        "safety", "context-loading", "testing", "version-control", "workflow",
        "role-profiles", "role-weights-v0", "memory-policy", "recall-policy",
        "context-compression", "dispatcher", "message-protocol",
    ]
    pol = [
        SimpleNamespace(
            label="policy",
            path=f"policies/runtime/agents/engineering-agent/{s}.md",
            content=long_body,
        )
        for s in stems
    ]
    return instr + pol


def run_bundle_scenario(mode: str, *, clock: Callable[[], float] = time.perf_counter) -> ScenarioMetrics:
    start = clock()
    docs = _fixture_bundle_docs()
    policy_docs = [d for d in docs if d.label == "policy"]
    all_bundle = build_policy_bundle(policy_docs, mode="digest")  # all policies, digest
    notes: List[str] = []
    if mode == "baseline":
        fed = all_bundle.fed_tokens
        saved = 0
        selected = len(policy_docs)
    else:
        from .policy_bundle import build_selected_policy_bundle

        # a concrete task: qa-engineer running a testing task → minimal bundle
        sb = build_selected_policy_bundle(docs, role="qa-engineer", task_type="testing")
        fed = sb.bundle.fed_tokens
        saved = max(0, all_bundle.fed_tokens - fed)
        selected = sb.selection.selected_policies
        notes.append(
            f"selected {selected}/{sb.selection.total_policies} policies (task=testing, role=qa-engineer)"
        )
    return ScenarioMetrics(
        scenario="bundle",
        mode=mode,
        loaded_policies_count=len(policy_docs),
        input_tokens_est=fed,
        saved_tokens_by_compaction=saved,
        wall_time_ms=_clock_ms(clock, start),
        notes=tuple(notes),
    )


_RUNNERS: Dict[str, Callable[..., ScenarioMetrics]] = {
    "dispatch": run_dispatch_scenario,
    "recall": run_recall_scenario,
    "context": run_context_scenario,
    "bundle": run_bundle_scenario,
}


def run_benchmark(
    mode: str,
    *,
    scenarios: Sequence[str] = SCENARIOS,
    slug: str = "token-efficiency-core",
    generated_at: str = "",
    clock: Callable[[], float] = time.perf_counter,
) -> BenchmarkReport:
    metrics = [_RUNNERS[name](mode, clock=clock) for name in scenarios if name in _RUNNERS]
    return BenchmarkReport(
        mode=mode, slug=slug, generated_at=generated_at, scenarios=tuple(metrics)
    )


# ---------------------------------------------------------------------------
# Delta
# ---------------------------------------------------------------------------


def _pct(before: int, after: int) -> float:
    if before <= 0:
        return 0.0
    return round((before - after) / before * 100.0, 1)


def compute_delta(baseline: BenchmarkReport, after: BenchmarkReport) -> dict:
    by_after = {s.scenario: s for s in after.scenarios}
    rows = []
    for b in baseline.scenarios:
        a = by_after.get(b.scenario)
        if a is None:
            continue
        rows.append(
            {
                "scenario": b.scenario,
                "input_tokens_before": b.input_tokens_est,
                "input_tokens_after": a.input_tokens_est,
                "input_tokens_saved": b.input_tokens_est - a.input_tokens_est,
                "input_reduction_pct": _pct(b.input_tokens_est, a.input_tokens_est),
                "previous_decisions_before": b.previous_decisions_size,
                "previous_decisions_after": a.previous_decisions_size,
                "source_context_before": b.source_context_size,
                "source_context_after": a.source_context_size,
                "retrieved_artifacts": a.retrieved_artifacts_count,
                "saved_tokens_by_compaction": a.saved_tokens_by_compaction,
            }
        )
    total_before = baseline.total_input_tokens
    total_after = after.total_input_tokens
    return {
        "slug": baseline.slug,
        "estimator": "chars/4 (ceil)",
        "totals": {
            "input_tokens_before": total_before,
            "input_tokens_after": total_after,
            "input_tokens_saved": total_before - total_after,
            "input_reduction_pct": _pct(total_before, total_after),
        },
        "scenarios": rows,
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_report_markdown(report: BenchmarkReport) -> str:
    lines = [
        f"# Token efficiency benchmark — {report.mode}",
        "",
        f"- slug: {report.slug}",
        f"- generated_at: {report.generated_at or '(unset)'}",
        f"- estimator: chars/4 (ceil)",
        f"- total input_tokens_est: {report.total_input_tokens}",
        f"- total saved_by_compaction: {report.total_saved_tokens}",
        "",
        "| scenario | input_tokens | prev_decisions | source_ctx | retrieved | saved | runner | warns |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for s in report.scenarios:
        lines.append(
            f"| {s.scenario} | {s.input_tokens_est} | {s.previous_decisions_size} | "
            f"{s.source_context_size} | {s.retrieved_artifacts_count} | "
            f"{s.saved_tokens_by_compaction} | {s.selected_runner} | {s.warnings_count} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_delta_markdown(delta: dict) -> str:
    t = delta["totals"]
    lines = [
        "# Token efficiency — baseline vs after (delta)",
        "",
        f"- estimator: {delta['estimator']}",
        f"- **total input tokens: {t['input_tokens_before']} → {t['input_tokens_after']} "
        f"(−{t['input_tokens_saved']}, −{t['input_reduction_pct']}%)**",
        "",
        "| scenario | before | after | saved | reduction |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for r in delta["scenarios"]:
        lines.append(
            f"| {r['scenario']} | {r['input_tokens_before']} | {r['input_tokens_after']} | "
            f"{r['input_tokens_saved']} | −{r['input_reduction_pct']}% |"
        )
    return "\n".join(lines).rstrip() + "\n"


__all__ = (
    "SCENARIOS",
    "FIXED_OUTPUT_TOKENS",
    "ScenarioMetrics",
    "BenchmarkReport",
    "run_dispatch_scenario",
    "run_recall_scenario",
    "run_context_scenario",
    "run_benchmark",
    "compute_delta",
    "render_report_markdown",
    "render_delta_markdown",
)
