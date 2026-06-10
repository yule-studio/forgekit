"""DiscussionResponse 렌더러 회귀 (#93).

* user surface 4-block 보장
* operator surface trace 가 user surface 에 누출되지 않음
* 어떤 surface 도 추가 secret 을 만들어 내지 않음 (PasteGuard 호출은
  caller 책임이며 본 모듈은 plain 출력)
"""

from __future__ import annotations

from yule_engineering.agents.conversation.discussion_response import (
    DiscussionResponse,
    EvidenceRef,
    MODE_HINT_IMPLEMENTATION_CANDIDATE,
    MODE_HINT_RESEARCH_ONLY,
    MemoryRef,
)
from yule_engineering.agents.conversation.response_format import (
    render_operator_surface,
    render_user_surface,
)


def _sample(mode_hint=None, evidence=(), memory=()) -> DiscussionResponse:
    return DiscussionResponse(
        conclusion="결론 한 줄 — 이대로 진행.",
        reasoning=("관련 이슈 #91", "역할 관점 — backend"),
        risks=("낮음 — 결정적 차단 요인 없음.",),
        next_actions=("결정 항목 하나씩 합의.", "권한 제안 단계로 이동."),
        evidence_refs=tuple(evidence),
        memory_refs=tuple(memory),
        mode_hint=mode_hint,
        metadata={
            "context_pack_id": "ctx-secret-001",
            "evidence_threshold": 0.3,
            "evidence_total": len(evidence),
            "evidence_accepted": len(evidence),
        },
    )


# ---------------------------------------------------------------------------
# 1. user surface — 4-block 만, trace 미포함
# ---------------------------------------------------------------------------


def test_user_surface_contains_four_blocks() -> None:
    text = render_user_surface(_sample())
    assert "**결론**" in text
    assert "**이유**" in text
    assert "**리스크**" in text
    assert "**다음 액션**" in text


def test_user_surface_omits_operator_trace_metadata() -> None:
    evidence = (
        EvidenceRef("note", "agg", "notes/agg.md", "snippet", 0.7),
    )
    text = render_user_surface(_sample(evidence=evidence))
    # operator-only 정보가 user surface 에 절대 노출 금지
    assert "ctx-secret-001" not in text
    assert "Operator trace" not in text
    assert "Classifier verdict" not in text
    assert "Retrieval trace" not in text
    assert "score=" not in text  # evidence score 도 user surface 노출 금지
    assert "Pack metadata" not in text


def test_user_surface_renders_mode_hint_when_present() -> None:
    text = render_user_surface(_sample(mode_hint=MODE_HINT_IMPLEMENTATION_CANDIDATE))
    assert "모드 전환 제안" in text
    assert "구현 후보" in text


def test_user_surface_omits_mode_hint_block_when_none() -> None:
    text = render_user_surface(_sample(mode_hint=None))
    assert "모드 전환 제안" not in text


# ---------------------------------------------------------------------------
# 2. operator surface — trace + classifier verdict 노출
# ---------------------------------------------------------------------------


def test_operator_surface_contains_user_surface_then_trace() -> None:
    evidence = (
        EvidenceRef("note", "agg", "notes/agg.md", "snippet", 0.7),
    )
    memory = (MemoryRef("shard", "recall", "ok", "claude-mem", 0.6),)
    text = render_operator_surface(
        _sample(
            mode_hint=MODE_HINT_RESEARCH_ONLY,
            evidence=evidence,
            memory=memory,
        ),
        classifier_verdict={
            "mode": "research_only",
            "confidence": 0.91,
            "source": "fast_path",
        },
        retrieval_trace=[
            {"provider": "fake-live", "hits": 3, "kept": 1},
            {"provider": "fake-live", "hits": 0, "kept": 0},
        ],
    )
    # 본문에 user surface 4-block 포함
    assert "**결론**" in text
    assert "**이유**" in text
    # operator trace 포함
    assert "Classifier verdict" in text
    assert "research_only" in text
    assert "Retrieval trace" in text
    assert "provider=" in text
    assert "Pack metadata" in text
    assert "ctx-secret-001" in text  # operator surface 에는 노출 OK
    assert "Evidence refs" in text
    assert "Memory refs" in text


# ---------------------------------------------------------------------------
# 3. 입력 가드 — 잘못된 타입은 TypeError
# ---------------------------------------------------------------------------


def test_render_helpers_reject_non_response_inputs() -> None:
    import pytest

    with pytest.raises(TypeError):
        render_user_surface({"conclusion": "x"})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        render_operator_surface({"conclusion": "x"})  # type: ignore[arg-type]
