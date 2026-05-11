"""DiscussionResponse builder 회귀 (#93).

4 mode x fake retrieval/memory provider 매트릭스 ≤18 case. F5 / F10
live wiring 은 후속 PR — 본 테스트는 deterministic fake 만 사용한다.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

import pytest

from yule_orchestrator.agents.conversation.discussion_response import (
    DiscussionResponse,
    EVIDENCE_SCORE_THRESHOLD,
    EvidenceRef,
    MODE_HINT_CLARIFICATION_NEEDED,
    MODE_HINT_DISCUSSION,
    MODE_HINT_IMPLEMENTATION_CANDIDATE,
    MODE_HINT_RESEARCH_ONLY,
    MODE_HINTS,
    MemoryRef,
    NullMemoryProvider,
    NullRetrievalProvider,
    build_discussion_response,
)
from yule_orchestrator.agents.decision.context_pack import ContextPack
from yule_orchestrator.agents.decision.router import DecisionRequest


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRetrievalProvider:
    """Deterministic fake — 호출 인자를 record + 미리 셋된 evidence 반환."""

    def __init__(self, evidence: Tuple[EvidenceRef, ...]) -> None:
        self._evidence = evidence
        self.calls: list = []

    def for_request(
        self,
        *,
        role: Optional[str],
        keywords: Sequence[str],
        limit: int,
    ) -> Tuple[EvidenceRef, ...]:
        self.calls.append({"role": role, "keywords": tuple(keywords), "limit": limit})
        return self._evidence[:limit]


class FakeMemoryProvider:
    """Deterministic fake — recent_shards 호출 record."""

    def __init__(self, shards: Tuple[MemoryRef, ...]) -> None:
        self._shards = shards
        self.calls: list = []

    def recent_shards(
        self,
        *,
        role: Optional[str],
        topic: str,
        limit: int,
    ) -> Tuple[MemoryRef, ...]:
        self.calls.append({"role": role, "topic": topic, "limit": limit})
        return self._shards[:limit]


def _pack(
    *,
    notes=(),
    issues=(),
    prs=(),
    hints=(),
    threads=(),
) -> ContextPack:
    return ContextPack(
        id="ctx-test-001",
        related_notes=tuple(notes),
        recent_threads=tuple(threads),
        related_issues=tuple(issues),
        related_prs=tuple(prs),
        code_hints=tuple(hints),
        created_at="2026-05-11T00:00:00+00:00",
        metadata={},
    )


def _request(prompt: str) -> DecisionRequest:
    return DecisionRequest(prompt=prompt, session_id="sess-1", channel="dm")


# ---------------------------------------------------------------------------
# 1. Acceptance — DiscussionResponse 가 4 필드 명시
# ---------------------------------------------------------------------------


def test_discussion_response_has_four_blocks() -> None:
    resp = DiscussionResponse(
        conclusion="결론 1줄.",
        reasoning=("이유",),
        risks=("리스크",),
        next_actions=("다음 액션",),
    )
    assert resp.conclusion
    assert resp.reasoning
    assert resp.risks
    assert resp.next_actions


def test_discussion_response_rejects_empty_fields() -> None:
    with pytest.raises(ValueError):
        DiscussionResponse(
            conclusion="",
            reasoning=("이유",),
            risks=("리스크",),
            next_actions=("다음",),
        )
    with pytest.raises(ValueError):
        DiscussionResponse(
            conclusion="결론",
            reasoning=(),
            risks=("리스크",),
            next_actions=("다음",),
        )
    with pytest.raises(ValueError):
        DiscussionResponse(
            conclusion="결론",
            reasoning=("이유",),
            risks=(),
            next_actions=("다음",),
        )
    with pytest.raises(ValueError):
        DiscussionResponse(
            conclusion="결론",
            reasoning=("이유",),
            risks=("리스크",),
            next_actions=(),
        )


def test_discussion_response_rejects_unknown_mode_hint() -> None:
    with pytest.raises(ValueError):
        DiscussionResponse(
            conclusion="결론",
            reasoning=("이유",),
            risks=("리스크",),
            next_actions=("다음",),
            mode_hint="bogus_mode",
        )


def test_mode_hints_exposed_constants_match() -> None:
    assert set(MODE_HINTS) == {
        MODE_HINT_DISCUSSION,
        MODE_HINT_RESEARCH_ONLY,
        MODE_HINT_IMPLEMENTATION_CANDIDATE,
        MODE_HINT_CLARIFICATION_NEEDED,
    }


# ---------------------------------------------------------------------------
# 2. 4 mode x fake provider 매트릭스
# ---------------------------------------------------------------------------


def test_build_discussion_mode_with_evidence_accepted() -> None:
    evidence = (
        EvidenceRef(
            kind="note",
            title="aggregator-spec",
            url_or_path="notes/agg.md",
            snippet="aggregator spec",
            score=0.9,
        ),
    )
    retrieval = FakeRetrievalProvider(evidence)
    memory = FakeMemoryProvider(())
    resp = build_discussion_response(
        request=_request("다음 스프린트 방향 토의해줘"),
        context_pack=_pack(notes=("notes/agg.md",)),
        retrieval=retrieval,
        memory=memory,
        role_perspective="engineering-agent/tech-lead",
    )
    assert resp.mode_hint is None  # 명확한 discussion — 전환 제안 없음
    assert len(resp.evidence_refs) == 1
    assert any("근거(note)" in r for r in resp.reasoning)
    assert any("낮음" in r for r in resp.risks)
    assert retrieval.calls and retrieval.calls[0]["role"] == "engineering-agent/tech-lead"


def test_build_clarification_when_prompt_empty_and_pack_empty() -> None:
    resp = build_discussion_response(
        request=_request(""),
        context_pack=_pack(),
        retrieval=NullRetrievalProvider(),
        memory=NullMemoryProvider(),
    )
    assert resp.mode_hint == MODE_HINT_CLARIFICATION_NEEDED
    assert any("근거 부족" in r for r in resp.risks)
    assert "정보만으로" in resp.conclusion


def test_build_research_only_via_keyword() -> None:
    resp = build_discussion_response(
        request=_request("이 부분은 코드 변경 전에 조사부터 부탁해"),
        context_pack=_pack(notes=("notes/foo.md",)),
        retrieval=NullRetrievalProvider(),
        memory=NullMemoryProvider(),
    )
    assert resp.mode_hint == MODE_HINT_RESEARCH_ONLY
    assert "자료부터" in resp.conclusion
    assert any("research collector" in a for a in resp.next_actions)


def test_build_implementation_candidate_via_keyword() -> None:
    resp = build_discussion_response(
        request=_request("이 버그 수정해줘 — PR 만들어 줘"),
        context_pack=_pack(issues=(91, 93)),
        retrieval=NullRetrievalProvider(),
        memory=NullMemoryProvider(),
    )
    assert resp.mode_hint == MODE_HINT_IMPLEMENTATION_CANDIDATE
    assert "구현 후보" in resp.conclusion
    assert any("권한 제안" in a for a in resp.next_actions)
    assert any("권한 제안" in r for r in resp.risks)


def test_build_explicit_mode_hint_overrides_derivation() -> None:
    resp = build_discussion_response(
        request=_request("그냥 잡담 같은 메시지"),
        context_pack=_pack(),
        retrieval=NullRetrievalProvider(),
        memory=NullMemoryProvider(),
        mode_hint=MODE_HINT_IMPLEMENTATION_CANDIDATE,
    )
    assert resp.mode_hint == MODE_HINT_IMPLEMENTATION_CANDIDATE


# ---------------------------------------------------------------------------
# 3. Hard rails — evidence score threshold + memory threshold
# ---------------------------------------------------------------------------


def test_low_score_evidence_dropped_and_risk_noted() -> None:
    low = (
        EvidenceRef(
            kind="note",
            title="low-quality",
            url_or_path="notes/low.md",
            snippet=None,
            score=EVIDENCE_SCORE_THRESHOLD - 0.01,
        ),
    )
    resp = build_discussion_response(
        request=_request("토의 이어 가자"),
        context_pack=_pack(),
        retrieval=FakeRetrievalProvider(low),
        memory=NullMemoryProvider(),
    )
    assert resp.evidence_refs == ()
    assert resp.metadata["evidence_total"] == 1
    assert resp.metadata["evidence_accepted"] == 0
    assert any("근거 부족" in r for r in resp.risks)


def test_mixed_score_evidence_only_accepts_passing() -> None:
    refs = (
        EvidenceRef("note", "high", "n/h.md", "h", 0.8),
        EvidenceRef("note", "low", "n/l.md", "l", 0.1),
    )
    resp = build_discussion_response(
        request=_request("토의"),
        context_pack=_pack(),
        retrieval=FakeRetrievalProvider(refs),
        memory=NullMemoryProvider(),
    )
    titles = [ref.title for ref in resp.evidence_refs]
    assert titles == ["high"]
    assert resp.metadata["evidence_accepted"] == 1


def test_memory_refs_threshold_applied() -> None:
    shards = (
        MemoryRef("shard", "ok", "summary", "memory", 0.7),
        MemoryRef("shard", "drop", "summary", "memory", 0.05),
    )
    resp = build_discussion_response(
        request=_request("토의"),
        context_pack=_pack(),
        retrieval=NullRetrievalProvider(),
        memory=FakeMemoryProvider(shards),
    )
    assert [ref.title for ref in resp.memory_refs] == ["ok"]


# ---------------------------------------------------------------------------
# 4. Provider seam — keyword extraction + topic passing
# ---------------------------------------------------------------------------


def test_keywords_passed_to_retrieval_provider() -> None:
    retrieval = FakeRetrievalProvider(())
    memory = FakeMemoryProvider(())
    build_discussion_response(
        request=_request("아키텍처 회의 일정 잡자 — 다음 주 화요일 어떨까?"),
        context_pack=_pack(),
        retrieval=retrieval,
        memory=memory,
        role_perspective="engineering-agent/backend-engineer",
    )
    assert retrieval.calls
    kw = retrieval.calls[0]["keywords"]
    assert "아키텍처" in kw
    assert retrieval.calls[0]["role"] == "engineering-agent/backend-engineer"
    assert memory.calls
    assert memory.calls[0]["topic"]


def test_pack_with_only_code_hints_yields_reasoning() -> None:
    resp = build_discussion_response(
        request=_request("이 모듈 만져 보자"),
        context_pack=_pack(hints=("src/foo.py", "src/bar.py")),
        retrieval=NullRetrievalProvider(),
        memory=NullMemoryProvider(),
    )
    assert any("코드 힌트" in r for r in resp.reasoning)


def test_pack_issues_and_prs_in_reasoning() -> None:
    resp = build_discussion_response(
        request=_request("이슈 91 관련"),
        context_pack=_pack(issues=(91,), prs=(105,)),
        retrieval=NullRetrievalProvider(),
        memory=NullMemoryProvider(),
    )
    joined = "\n".join(resp.reasoning)
    assert "#91" in joined
    assert "#105" in joined


def test_to_dict_round_trip_does_not_lose_fields() -> None:
    resp = build_discussion_response(
        request=_request("토의"),
        context_pack=_pack(notes=("n.md",)),
        retrieval=NullRetrievalProvider(),
        memory=NullMemoryProvider(),
    )
    payload = resp.to_dict()
    assert payload["conclusion"] == resp.conclusion
    assert payload["reasoning"] == list(resp.reasoning)
    assert payload["risks"] == list(resp.risks)
    assert payload["next_actions"] == list(resp.next_actions)
    assert payload["mode_hint"] == resp.mode_hint
    assert payload["metadata"]["context_pack_id"] == "ctx-test-001"


# ---------------------------------------------------------------------------
# 5. mode 전환 가시화 — clarification 만 빈 prompt 에서 발동
# ---------------------------------------------------------------------------


def test_ambiguous_prompt_with_empty_pack_yields_clarification() -> None:
    resp = build_discussion_response(
        request=_request("음... 모르겠다 — 어떻게 갈까?"),
        context_pack=_pack(),
        retrieval=NullRetrievalProvider(),
        memory=NullMemoryProvider(),
    )
    assert resp.mode_hint == MODE_HINT_CLARIFICATION_NEEDED


def test_ambiguous_prompt_with_pack_does_not_clarify() -> None:
    resp = build_discussion_response(
        request=_request("음... 모르겠다 — 어떻게 갈까?"),
        context_pack=_pack(notes=("notes/x.md",), issues=(10,)),
        retrieval=NullRetrievalProvider(),
        memory=NullMemoryProvider(),
    )
    # pack 이 채워져 있으면 clarification 으로 강제되지 않음.
    assert resp.mode_hint != MODE_HINT_CLARIFICATION_NEEDED
