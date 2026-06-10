"""DiscussionResponse 렌더러 — user surface vs operator surface 분리.

* :func:`render_user_surface` — Discord 에 노출되는 4-block 본문만.
  retrieval trace / classifier verdict / context pack id 등 운영자
  전용 정보는 절대 포함하지 않는다.
* :func:`render_operator_surface` — ``#봇-상태`` 등 운영자 surface 용.
  user surface 본문 + classifier verdict / retrieval trace / pack
  metadata 를 함께 노출.

PasteGuard 호출은 본 모듈 책임이 아니다. caller (status poster /
gateway) 가 outbound 전에 :func:`guard_outbound` 으로 한 번 더 감싼다.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Optional, Sequence

from .discussion_response import (
    DiscussionResponse,
    EVIDENCE_SCORE_THRESHOLD,
    EvidenceRef,
    MemoryRef,
    MODE_HINT_CLARIFICATION_NEEDED,
    MODE_HINT_DISCUSSION,
    MODE_HINT_IMPLEMENTATION_CANDIDATE,
    MODE_HINT_RESEARCH_ONLY,
)


# ---------------------------------------------------------------------------
# User surface
# ---------------------------------------------------------------------------


_MODE_HINT_LABEL: Mapping[str, str] = {
    MODE_HINT_DISCUSSION: "토의 (discussion)",
    MODE_HINT_RESEARCH_ONLY: "조사 (research_only)",
    MODE_HINT_IMPLEMENTATION_CANDIDATE: "구현 후보 (implementation_candidate)",
    MODE_HINT_CLARIFICATION_NEEDED: "추가 질문 필요 (clarification_needed)",
}


def render_user_surface(resp: DiscussionResponse) -> str:
    """4-block 사용자 surface 문자열.

    구성:

    ```
    **결론**
    <conclusion>

    **이유**
    - ...

    **리스크**
    - ...

    **다음 액션**
    - ...

    > 모드 전환 제안: <mode label> (선택)
    ```

    * retrieval_trace / classifier verdict / context_pack_id 등 운영자
      전용 정보는 절대 포함하지 않는다.
    * mode_hint 가 ``None`` 이면 모드 전환 한 줄을 생략한다.
    """

    if not isinstance(resp, DiscussionResponse):
        raise TypeError(
            f"render_user_surface expects DiscussionResponse, got {type(resp).__name__}"
        )

    lines: list = ["**결론**", resp.conclusion.strip(), ""]

    lines.append("**이유**")
    lines.extend(f"- {item}" for item in resp.reasoning)
    lines.append("")

    lines.append("**리스크**")
    lines.extend(f"- {item}" for item in resp.risks)
    lines.append("")

    lines.append("**다음 액션**")
    lines.extend(f"- {item}" for item in resp.next_actions)

    if resp.mode_hint and resp.mode_hint in _MODE_HINT_LABEL:
        label = _MODE_HINT_LABEL[resp.mode_hint]
        lines.append("")
        lines.append(f"> 모드 전환 제안: **{label}** 로 이어 갈까요?")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Operator surface
# ---------------------------------------------------------------------------


def render_operator_surface(
    resp: DiscussionResponse,
    *,
    classifier_verdict: Optional[Mapping[str, Any]] = None,
    retrieval_trace: Optional[Sequence[Mapping[str, Any]]] = None,
) -> str:
    """``#봇-상태`` operator surface 문자열.

    user surface 본문 + 다음을 추가:

    * `Classifier verdict` — :class:`DecisionResult.to_payload()` 형 dict.
    * `Retrieval trace` — provider 가 만든 trace entries.
    * `Pack metadata` — :class:`DiscussionResponse.metadata` (pack id, 임계값
      등).
    * `Evidence / Memory refs` — 채택된 ref 목록 (점수 포함).

    user surface 만 추출하려면 :func:`render_user_surface` 를 직접 호출.
    """

    if not isinstance(resp, DiscussionResponse):
        raise TypeError(
            f"render_operator_surface expects DiscussionResponse, got {type(resp).__name__}"
        )

    parts: list = [render_user_surface(resp), "---", "**Operator trace**", ""]

    if classifier_verdict:
        parts.append("Classifier verdict:")
        parts.extend(_render_kv(classifier_verdict))
        parts.append("")

    if retrieval_trace:
        parts.append("Retrieval trace:")
        for entry in retrieval_trace:
            parts.append(f"- {_compact_kv(entry)}")
        parts.append("")

    if resp.evidence_refs:
        parts.append(f"Evidence refs (threshold {EVIDENCE_SCORE_THRESHOLD}):")
        for ref in resp.evidence_refs:
            parts.append(f"- {_render_evidence(ref)}")
        parts.append("")

    if resp.memory_refs:
        parts.append("Memory refs:")
        for ref in resp.memory_refs:
            parts.append(f"- {_render_memory(ref)}")
        parts.append("")

    if resp.metadata:
        parts.append("Pack metadata:")
        parts.extend(_render_kv(resp.metadata))

    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _render_kv(mapping: Mapping[str, Any]) -> list:
    out: list = []
    for key, value in mapping.items():
        out.append(f"- {key}: {_format_value(value)}")
    return out


def _compact_kv(entry: Mapping[str, Any]) -> str:
    return ", ".join(f"{k}={_format_value(v)}" for k, v in entry.items())


def _format_value(value: Any) -> str:
    if is_dataclass(value):
        return repr(asdict(value))
    return repr(value)


def _render_evidence(ref: EvidenceRef) -> str:
    location = ref.url_or_path or "(no-url)"
    return (
        f"{ref.kind}: {ref.title} · {location} · score={ref.score}"
    )


def _render_memory(ref: MemoryRef) -> str:
    return (
        f"{ref.kind}: {ref.title} · source={ref.source} · score={ref.score}"
    )


__all__ = (
    "render_operator_surface",
    "render_user_surface",
)
