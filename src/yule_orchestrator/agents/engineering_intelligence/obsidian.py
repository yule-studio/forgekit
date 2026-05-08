"""Engineering-knowledge → ObsidianWriteRequest bridge with quality gate.

The L1 auto-save flow goes:

  1. Collector produces a :class:`EngineeringKnowledgeItem`.
  2. :func:`evaluate_quality_gate` checks the item against the
     mandatory contract (title / source_url / summary /
     why_it_matters / practical_impact / recommended_action /
     practice_topic / >=2 practice_steps / >=1 references / >=1
     rag_tags / cag_context_key / >=2 retrieval_queries /
     cag_context.when_to_use / learning_level / practice_verification.
     expected_result / review_after_days).
  3. If the gate passes, :func:`build_engineering_knowledge_write_request`
     produces an :class:`ObsidianWriteRequest` with note_kind
     ``engineering-knowledge`` (L1 — no operator approval) carrying
     the rendered markdown body in ``metadata['body']``.
  4. If the gate fails, the function returns ``None`` and the caller
     uses :func:`build_rejected_quality_gate_audit` to record why.

Production wiring queues that request via the existing
:class:`ObsidianWriterWorker.enqueue` — that worker is *not* modified
here (note_kind ``engineering-knowledge`` is not in
``_APPROVAL_REQUIRED_KINDS`` so it will save automatically).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Tuple

from .models import (
    NOTE_KIND_ENGINEERING_KNOWLEDGE,
    EngineeringKnowledgeItem,
)
from .renderer import RendererError, render_engineering_knowledge_note


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityGateResult:
    passed: bool
    reasons: Tuple[str, ...]

    def to_payload(self) -> Mapping[str, Any]:
        return {"passed": self.passed, "reasons": list(self.reasons)}


def evaluate_quality_gate(item: EngineeringKnowledgeItem) -> QualityGateResult:
    """Return the gate verdict for *item*.

    Each missing requirement contributes one entry to ``reasons`` so
    the operator-facing audit can list every block in one pass.
    """

    reasons: List[str] = []

    if not item.title.strip():
        reasons.append("missing:title")
    if not item.source_url.strip():
        reasons.append("missing:source_url")
    if not item.summary.strip():
        reasons.append("missing:summary")
    if not item.why_it_matters.strip():
        reasons.append("missing:why_it_matters")
    if not item.practical_impact.strip():
        reasons.append("missing:practical_impact")
    if not item.recommended_action.strip():
        reasons.append("missing:recommended_action")
    if not item.practice_topic.strip():
        reasons.append("missing:practice_topic")
    non_empty_steps = [s for s in item.practice_steps if s and s.strip()]
    if len(non_empty_steps) < 2:
        reasons.append("missing:practice_steps_min_2")
    non_empty_refs = [r for r in item.references if r and r.strip()]
    if len(non_empty_refs) < 1:
        reasons.append("missing:references_min_1")
    non_empty_rag = [t for t in item.rag_tags if t and t.strip()]
    if len(non_empty_rag) < 1:
        reasons.append("missing:rag_tags_min_1")
    if not item.cag_context_key.strip():
        reasons.append("missing:cag_context_key")
    non_empty_queries = [q for q in item.retrieval_queries if q and q.strip()]
    if len(non_empty_queries) < 2:
        reasons.append("missing:retrieval_queries_min_2")
    if item.cag_context is None or not item.cag_context.when_to_use.strip():
        reasons.append("missing:cag_context.when_to_use")
    if not item.learning_level.value.strip():
        reasons.append("missing:learning_level")
    if (
        item.practice_verification is None
        or not item.practice_verification.expected_result.strip()
    ):
        reasons.append("missing:practice_verification.expected_result")
    if item.review_after_days is None or int(item.review_after_days) <= 0:
        reasons.append("missing:review_after_days")

    return QualityGateResult(passed=not reasons, reasons=tuple(reasons))


# ---------------------------------------------------------------------------
# Write request builder
# ---------------------------------------------------------------------------


def build_engineering_knowledge_write_request(
    item: EngineeringKnowledgeItem,
    *,
    session_id: str = "",
    project: Optional[str] = "yule-studio-agent",
    layout: Optional[str] = None,
) -> Optional[Any]:
    """Produce an :class:`ObsidianWriteRequest` for *item*.

    Returns ``None`` when the quality gate fails — the caller should
    log via :func:`build_rejected_quality_gate_audit`.
    """

    gate = evaluate_quality_gate(item)
    if not gate.passed:
        return None

    try:
        body = render_engineering_knowledge_note(item)
    except RendererError:
        # The gate already enforces every renderer hard contract; this
        # branch is purely defence-in-depth so a mismatched contract
        # still surfaces as "no request" instead of an unhandled
        # exception in production.
        return None

    from ..job_queue.obsidian_writer_worker import ObsidianWriteRequest

    metadata: dict[str, Any] = {
        "autonomy_level": "L1_AUTO_RECORD_REQUIRED",
        "body": body,
        "engineering_intelligence": {
            "topic_key": item.topic_key,
            "role": item.role,
            "stack_tags": list(item.stack_tags),
            "rag_tags": list(item.rag_tags),
            "cag_context_key": item.cag_context_key,
            "retrieval_queries": list(item.retrieval_queries),
            "learning_level": item.learning_level.value,
            "knowledge_status": item.knowledge_status.value,
            "review_after_days": int(item.review_after_days),
            "source_url": item.source_url,
            "source_kind": item.source_kind.value,
            "source_name": item.source_name,
            "dedup_key": item.dedup_key,
            "importance": item.importance.value,
            "audience": item.audience.value,
        },
        "engineering_knowledge_item": dict(item.to_payload()),
    }

    return ObsidianWriteRequest(
        session_id=session_id,
        note_kind=NOTE_KIND_ENGINEERING_KNOWLEDGE,
        title=item.title[:80],
        project=project,
        layout=layout,
        metadata=metadata,
    )


def build_rejected_quality_gate_audit(
    item: EngineeringKnowledgeItem,
    gate: QualityGateResult,
) -> Mapping[str, Any]:
    """Produce a payload describing the rejection.

    Shape mirrors what ``agent_ops_audit`` rows already use elsewhere
    (action / outcome / summary) so the supervisor / status surface
    can ingest it without a translation step.
    """

    return {
        "action": "engineering_knowledge_quality_gate",
        "outcome": "rejected_quality_gate",
        "topic_key": item.topic_key,
        "role": item.role,
        "source_url": item.source_url,
        "reasons": list(gate.reasons),
        "summary": (
            f"engineering-knowledge item rejected — role={item.role} "
            f"topic={item.topic_key} reasons={','.join(gate.reasons)}"
        ),
    }


__all__ = [
    "QualityGateResult",
    "build_engineering_knowledge_write_request",
    "build_rejected_quality_gate_audit",
    "evaluate_quality_gate",
]
