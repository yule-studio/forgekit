"""Quantitative eval gate — fixed task-set, multi-axis, variant-comparable.

WT3. The token benchmark answers "how many input tokens did we shave"; this
harness answers the broader operational question: *given a fixed set of
representative tasks, how well does the current routing/minimization policy do
on success / tokens / cost / latency / rule-first ratio, and how does that
compare across policy variants?*

It is deterministic and offline — it drives the **real** resolution policy
(:mod:`llm_minimization`) over a fixed task-set and a fixed latency fixture, so
the same inputs always produce the same metrics. No live provider call.

Generalization (design-eval ready): every task carries an extensible
``dimensions`` expectation map and every per-task result an extensible
``dimensions`` score map. The aggregator means *any* registered dimension
generically — so a future "design adherence / reference fidelity" axis is added
by registering a scorer, not by changing this schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from .cost_model import estimate_cost, estimate_tokens_from_text
from .llm_minimization import (
    RESOLUTION_LLM_OPTIONAL,
    RESOLUTION_LLM_REQUIRED,
    RESOLUTION_RULE_FIRST,
    resolve_from_metadata,
)

# Metrics-schema version — bump when the aggregate shape changes so a consumer
# (CI gate / dashboard) can detect drift. A test pins this.
EVAL_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class EvalTask:
    """One fixed task. ``expected_llm`` is whether a live LLM *should* run."""

    task_id: str
    prompt: str
    capability_class: str
    expected_mode: str
    expected_llm: bool
    # extensible per-task expectations for future dimensions (e.g. design)
    dimensions: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalTaskResult:
    task_id: str
    capability_class: str
    resolution_mode: str
    rule_first: bool
    llm_used: bool
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    success: bool
    dimensions: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "capability_class": self.capability_class,
            "resolution_mode": self.resolution_mode,
            "rule_first": self.rule_first,
            "llm_used": self.llm_used,
            "provider": self.provider,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "latency_ms": self.latency_ms,
            "success": self.success,
            "dimensions": {k: round(v, 4) for k, v in self.dimensions.items()},
        }


@dataclass(frozen=True)
class EvalReport:
    variant: str
    n_tasks: int
    success_rate_pct: float
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    avg_latency_ms: float
    rule_first_ratio_pct: float
    llm_used_runs: int
    provider_breakdown: Mapping[str, int]
    dimension_scores: Mapping[str, float]
    results: Tuple[EvalTaskResult, ...]
    schema_version: str = EVAL_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "variant": self.variant,
            "n_tasks": self.n_tasks,
            "success_rate_pct": self.success_rate_pct,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "avg_latency_ms": self.avg_latency_ms,
            "rule_first_ratio_pct": self.rule_first_ratio_pct,
            "llm_used_runs": self.llm_used_runs,
            "provider_breakdown": dict(self.provider_breakdown),
            "dimension_scores": dict(self.dimension_scores),
            "results": [r.to_dict() for r in self.results],
        }


# Fixed task-set — representative spread across capability classes. Stable ids.
DEFAULT_TASK_SET: Tuple[EvalTask, ...] = (
    EvalTask("t01-classify", "이 메시지가 버그 리포트인지 분류", "classification", RESOLUTION_RULE_FIRST, False),
    EvalTask("t02-enforce", "grant 정책 위반 여부 판정", "enforcement", RESOLUTION_RULE_FIRST, False),
    EvalTask("t03-secgate", "이 diff 가 보안 리뷰 필요?", "security_gate", RESOLUTION_RULE_FIRST, False),
    EvalTask("t04-verify", "테스트 결과가 통과 기준 충족?", "verification", RESOLUTION_RULE_FIRST, False),
    EvalTask("t05-summary", "이 스레드를 3줄 요약", "summarization", RESOLUTION_LLM_OPTIONAL, True),
    EvalTask("t06-research", "이 라이브러리 대안 3개 조사", "research", RESOLUTION_LLM_REQUIRED, True),
    EvalTask("t07-execute", "이 함수를 리팩토링하고 테스트 추가", "execution", RESOLUTION_LLM_REQUIRED, True),
    EvalTask("t08-deliver", "PR 본문 작성 + 이슈 링크", "delivery", RESOLUTION_LLM_REQUIRED, True),
)

# Deterministic latency fixture per provider (ms) — keeps the latency axis
# reproducible offline. Rule path is cheap; live LLM is slow.
_LATENCY_FIXTURE: Mapping[str, float] = {
    "deterministic": 5.0,
    "ollama": 400.0,
    "codex": 1200.0,
    "claude": 1500.0,
}
# Deterministic output-size proxy per resolution mode (chars).
_OUTPUT_PROXY_CHARS: Mapping[str, int] = {
    RESOLUTION_RULE_FIRST: 80,
    RESOLUTION_LLM_OPTIONAL: 600,
    RESOLUTION_LLM_REQUIRED: 1200,
}

# A "variant" maps (resolution_mode) -> provider. Three axes the user asked for:
#   baseline  — minimization OFF: everything is treated as a live LLM call.
#   current   — minimization ON: rule_first bypasses to deterministic.
#   cheap_llm — routing variant: llm_optional prefers a local backend.
VARIANTS: Mapping[str, Mapping[str, str]] = {
    "baseline": {
        RESOLUTION_RULE_FIRST: "claude",
        RESOLUTION_LLM_OPTIONAL: "claude",
        RESOLUTION_LLM_REQUIRED: "claude",
    },
    "current": {
        RESOLUTION_RULE_FIRST: "deterministic",
        RESOLUTION_LLM_OPTIONAL: "ollama",
        RESOLUTION_LLM_REQUIRED: "claude",
    },
    "cheap_llm": {
        RESOLUTION_RULE_FIRST: "deterministic",
        RESOLUTION_LLM_OPTIONAL: "ollama",
        RESOLUTION_LLM_REQUIRED: "ollama",
    },
}

_NON_LLM_PROVIDERS = frozenset({"deterministic", "grant-gate"})

# Dimension scorer signature: (task, partial_result_fields) -> score in [0,1].
DimensionScorer = Callable[[EvalTask, Mapping[str, Any]], float]


def _routing_correctness(task: EvalTask, fields: Mapping[str, Any]) -> float:
    """Built-in dimension: did the run resolve + route as the task expects?"""

    ok = (
        fields["resolution_mode"] == task.expected_mode
        and fields["llm_used"] == task.expected_llm
    )
    return 1.0 if ok else 0.0


# Registry of dimensions aggregated generically. Future "design_adherence" /
# "reference_fidelity" scorers register here without touching the schema.
DEFAULT_DIMENSIONS: Mapping[str, DimensionScorer] = {
    "routing_correctness": _routing_correctness,
}


def run_eval(
    variant: str = "current",
    *,
    tasks: Sequence[EvalTask] = DEFAULT_TASK_SET,
    dimensions: Mapping[str, DimensionScorer] = DEFAULT_DIMENSIONS,
    minimization_enabled: Optional[bool] = None,
) -> EvalReport:
    """Run *tasks* under *variant* and aggregate the multi-axis metrics.

    When ``minimization_enabled`` is False (the implicit case for the
    ``baseline`` variant), every task is treated as a live LLM call regardless
    of capability — modelling the pre-minimization policy.
    """

    provider_map = VARIANTS.get(variant, VARIANTS["current"])
    if minimization_enabled is None:
        minimization_enabled = variant != "baseline"

    results: list[EvalTaskResult] = []
    for task in tasks:
        if minimization_enabled:
            decision = resolve_from_metadata({}, task.capability_class)
            mode = decision.resolution_mode
        else:
            mode = RESOLUTION_LLM_REQUIRED  # pre-minimization: always live
        provider = provider_map.get(mode, "claude")
        llm_used = provider not in _NON_LLM_PROVIDERS
        rule_first = mode == RESOLUTION_RULE_FIRST

        input_tokens = estimate_tokens_from_text(task.prompt)
        output_tokens = estimate_tokens_from_text("x" * _OUTPUT_PROXY_CHARS.get(mode, 400))
        cost = estimate_cost(provider, input_tokens=input_tokens, output_tokens=output_tokens)
        latency = _LATENCY_FIXTURE.get(provider, 1000.0)

        fields = {
            "resolution_mode": mode,
            "rule_first": rule_first,
            "llm_used": llm_used,
            "provider": provider,
        }
        dim_scores = {name: scorer(task, fields) for name, scorer in dimensions.items()}
        success = dim_scores.get("routing_correctness", 1.0) >= 1.0

        results.append(
            EvalTaskResult(
                task_id=task.task_id,
                capability_class=task.capability_class,
                resolution_mode=mode,
                rule_first=rule_first,
                llm_used=llm_used,
                provider=provider,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost.total_cost_usd,
                latency_ms=latency,
                success=success,
                dimensions=dim_scores,
            )
        )

    return _aggregate(variant, results, dimensions.keys())


def _aggregate(variant: str, results: Sequence[EvalTaskResult], dim_names) -> EvalReport:
    n = len(results)
    succ = sum(1 for r in results if r.success)
    in_tok = sum(r.input_tokens for r in results)
    out_tok = sum(r.output_tokens for r in results)
    cost = sum(r.cost_usd for r in results)
    latency = sum(r.latency_ms for r in results)
    rule_first = sum(1 for r in results if r.rule_first)
    llm_used = sum(1 for r in results if r.llm_used)
    providers: dict[str, int] = {}
    for r in results:
        providers[r.provider] = providers.get(r.provider, 0) + 1
    dim_scores: dict[str, float] = {}
    for name in dim_names:
        vals = [r.dimensions.get(name, 0.0) for r in results]
        dim_scores[name] = round(sum(vals) / n, 4) if n else 0.0
    return EvalReport(
        variant=variant,
        n_tasks=n,
        success_rate_pct=round(succ / n * 100.0, 1) if n else 0.0,
        total_input_tokens=in_tok,
        total_output_tokens=out_tok,
        total_cost_usd=cost,
        avg_latency_ms=round(latency / n, 1) if n else 0.0,
        rule_first_ratio_pct=round(rule_first / n * 100.0, 1) if n else 0.0,
        llm_used_runs=llm_used,
        provider_breakdown=providers,
        dimension_scores=dim_scores,
        results=tuple(results),
    )


def compare_variants(variants: Sequence[str] = ("baseline", "current", "cheap_llm")) -> dict:
    """Run several variants and return a comparison payload (md/json friendly)."""

    reports = {v: run_eval(v) for v in variants}
    return {
        "schema_version": EVAL_SCHEMA_VERSION,
        "variants": {v: r.to_dict() for v, r in reports.items()},
        "comparison": [
            {
                "variant": v,
                "success_rate_pct": r.success_rate_pct,
                "total_cost_usd": round(r.total_cost_usd, 6),
                "avg_latency_ms": r.avg_latency_ms,
                "rule_first_ratio_pct": r.rule_first_ratio_pct,
                "llm_used_runs": r.llm_used_runs,
                "total_tokens": r.total_input_tokens + r.total_output_tokens,
            }
            for v, r in reports.items()
        ],
    }


def render_comparison_markdown(comparison: Mapping[str, Any]) -> str:
    lines = [
        "# Eval gate — variant comparison",
        "",
        f"- schema_version: {comparison.get('schema_version')}",
        "",
        "| variant | success | tokens | cost (proxy $) | avg latency ms | rule-first % | llm runs |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in comparison.get("comparison", []):
        lines.append(
            f"| {row['variant']} | {row['success_rate_pct']}% | {row['total_tokens']} | "
            f"{row['total_cost_usd']} | {row['avg_latency_ms']} | "
            f"{row['rule_first_ratio_pct']}% | {row['llm_used_runs']} |"
        )
    return "\n".join(lines).rstrip() + "\n"


__all__ = (
    "EVAL_SCHEMA_VERSION",
    "EvalTask",
    "EvalTaskResult",
    "EvalReport",
    "DEFAULT_TASK_SET",
    "VARIANTS",
    "DEFAULT_DIMENSIONS",
    "run_eval",
    "compare_variants",
    "render_comparison_markdown",
)
