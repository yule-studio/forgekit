"""User-facing labels + forum summary rendering for the research collector.

Extracted from ``collector.py`` so the core file keeps a thin
orchestration surface. This module owns the *formatting* responsibility:

- Centralised Korean labels (``SOURCE_TYPE_LABELS`` / ``PROVIDER_LABELS``
  / ``TASK_TYPE_LABELS`` / ``CONFIDENCE_LABELS``) re-used by the
  conversation / forum / deliberation layers.
- The ``pretty_*`` translators and their backwards-compatible aliases.
- :func:`format_collection_summary` — the autonomous-collection block in
  the team-lead voice, dropped into ``format_research_post_body``.

Import direction is one-way: this module imports the collector *core*
(``CONFIDENCE_*`` constants, ``short_role``) and the provider adapters'
``extract_domain``. The core re-exports the symbols here for its public
surface — collector core → format is the legal direction.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .pack import ResearchPack, SourceType
from .collector import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    short_role,
)
from .collector_providers import extract_domain


# ---------------------------------------------------------------------------
# Centralised user-facing labels (re-used by conversation/forum/deliberation)
# ---------------------------------------------------------------------------


SOURCE_TYPE_LABELS: Mapping[str, str] = {
    "user_message": "사용자 요청",
    "url": "사용자 링크",
    "web_result": "웹 검색",
    "image_reference": "이미지 레퍼런스",
    "file_attachment": "첨부 파일",
    "github_issue": "GitHub 이슈",
    "github_pr": "GitHub PR",
    "code_context": "코드 맥락",
    "official_docs": "공식 문서",
    "community_signal": "커뮤니티 글",
    "design_reference": "디자인 레퍼런스",
    "unknown": "기타",
}


PROVIDER_LABELS: Mapping[str, str] = {
    "mock": "기본 검색(mock)",
    "tavily": "Tavily 검색",
    "brave": "Brave 검색",
    "noop": "비활성",
    "live": "외부 검색",
    "?": "알 수 없음",
}


TASK_TYPE_LABELS: Mapping[str, str] = {
    "landing-page": "랜딩 페이지",
    "onboarding-flow": "온보딩 흐름",
    "visual-polish": "비주얼 정리",
    "email-campaign": "이메일 캠페인",
    "qa-test": "QA 테스트",
    "platform-infra": "플랫폼/인프라",
    "frontend-feature": "프론트엔드",
    "backend-feature": "백엔드",
    "unknown": "일반",
}


CONFIDENCE_LABELS: Mapping[str, str] = {
    CONFIDENCE_HIGH: "신뢰도 높음",
    CONFIDENCE_MEDIUM: "신뢰도 보통",
    CONFIDENCE_LOW: "신뢰도 낮음",
}


def pretty_source_type(source_type: Any) -> str:
    """Translate a :class:`SourceType` (or its string value) into Korean.

    Unknown values fall through unchanged so a future enum addition still
    renders something readable instead of crashing.
    """

    if source_type is None:
        return SOURCE_TYPE_LABELS["unknown"]
    if isinstance(source_type, SourceType):
        value = source_type.value
    else:
        value = str(source_type)
    return SOURCE_TYPE_LABELS.get(value, value or SOURCE_TYPE_LABELS["unknown"])


def pretty_provider(name: Optional[str]) -> str:
    """Translate a collector provider id into Korean. Unknown → passthrough."""

    if not name:
        return PROVIDER_LABELS["?"]
    return PROVIDER_LABELS.get(name, name)


def pretty_task_type(value: Optional[str]) -> str:
    """Translate a dispatcher ``TaskType.value`` into Korean.

    Falls back to "일반" for missing/blank input and to the raw value
    otherwise (so ``"design-system"`` stays readable instead of crashing).
    """

    if not value:
        return TASK_TYPE_LABELS["unknown"]
    return TASK_TYPE_LABELS.get(value, value)


def pretty_confidence(value: Optional[str]) -> str:
    """Translate a confidence label (``high|medium|low``) into Korean."""

    label = (value or CONFIDENCE_MEDIUM).lower()
    return CONFIDENCE_LABELS.get(label, CONFIDENCE_LABELS[CONFIDENCE_MEDIUM])


# Backwards-compatible aliases (used internally before centralisation).
_pretty_source_type = pretty_source_type
_pretty_confidence = pretty_confidence
_pretty_provider_summary = pretty_provider


def _summarize_topic_for_summary(text: Optional[str], max_chars: int = 60) -> str:
    cleaned = [line.strip() for line in (text or "").splitlines() if line.strip()]
    head = cleaned[0] if cleaned else ""
    if not head:
        return "(요청 본문 없음)"
    if len(head) <= max_chars:
        return head
    return head[: max(1, max_chars - 1)].rstrip() + "…"


def format_collection_summary(
    pack: ResearchPack,
    *,
    collector_name: str,
    query: str,
    role: str,
    next_steps: Sequence[str] = (),
) -> str:
    """Render the autonomous-collection block in the team-lead voice.

    Designed to be dropped into ``format_research_post_body``. Internal
    jargon (collector / query / source_type values) is translated into
    human-friendly Korean labels. The raw user prompt is summarised to a
    short topic so it doesn't bloat the forum thread.

    Sections (each keeps 2~4 sentences):
    - 1차 자료 정리 — <역할 한국어>
    - 참고 자료 (count): per-source 짧은 라벨 + URL
    - 활용 방향: why_relevant 모음
    - 유의 사항: risk_or_limit + budget note
    - 다음 단계: 역할별 검토 흐름 안내
    - 수집 정보: 수집 방식 / 수집 자료
    """

    short = short_role(role)
    request_topic = (
        getattr(pack.request, "topic", None) if pack.request is not None else None
    ) or pack.title
    topic = _summarize_topic_for_summary(request_topic)

    body_count = sum(
        1 for s in pack.sources if s.source_type != SourceType.USER_MESSAGE
    )

    lines: list[str] = []
    lines.append(f"**📚 1차 자료 정리 — {short}**")
    lines.append("")
    lines.append(f"이번 정리는 “{topic}”에 대한 검토예요.")

    # 참고 자료
    lines.append("")
    lines.append(f"**참고 자료** ({body_count}건)")
    risks: list[str] = []
    why_relevants: list[str] = []
    if body_count == 0:
        lines.append(
            "- 아직 자동 수집된 자료가 없어요. 사용자에게 자료를 요청한 뒤 다시 정리할게요."
        )
    else:
        for source in pack.sources:
            if source.source_type == SourceType.USER_MESSAGE:
                continue
            domain = (source.extra or {}).get("domain") or extract_domain(source.source_url)
            title = source.title or "(제목 없음)"
            type_label = _pretty_source_type(source.source_type)
            confidence_label = _pretty_confidence(source.confidence)
            head_bits = [f"- **{title}** · {type_label} · {confidence_label}"]
            if domain:
                head_bits.append(f" · `{domain}`")
            lines.append("".join(head_bits))
            if source.source_url:
                lines.append(f"  ↪ {source.source_url}")
            if source.why_relevant:
                why_relevants.append(f"{title}: {source.why_relevant}")
            if source.risk_or_limit:
                risks.append(f"{title}: {source.risk_or_limit}")

    # 활용 방향
    if why_relevants:
        lines.append("")
        lines.append("**활용 방향**")
        for item in why_relevants:
            lines.append(f"- {item}")

    # 유의 사항
    budget_note = (pack.extra or {}).get("budget_note") if pack.extra else None
    if risks or budget_note:
        lines.append("")
        lines.append("**유의 사항**")
        for risk in risks:
            lines.append(f"- {risk}")
        if budget_note:
            lines.append(f"- {budget_note}")

    # 다음 단계
    lines.append("")
    lines.append("**다음 단계**")
    if next_steps:
        for step in next_steps:
            lines.append(f"- {step}")
    elif body_count > 0:
        lines.append("- 각 역할이 자기 관점으로 검토 → tech-lead가 합의안 정리")
    else:
        lines.append("- 사용자에게 추가 자료를 요청한 뒤 재수집")

    # 수집 정보 (메타)
    lines.append("")
    lines.append("수집 정보:")
    lines.append(f"- 수집 방식: {_pretty_provider_summary(collector_name)}")
    lines.append(f"- 수집 자료: {body_count}건")

    return "\n".join(lines)
