"""Obsidian engineering-knowledge note renderer.

Given a :class:`EngineeringKnowledgeItem`, produce:

  * A YAML frontmatter block with the contract id + role + audience +
    source_url + topic_key + RAG/CAG metadata.
  * A markdown body that follows the official-doc style template
    (목차 + 13 sections, including 학습 난이도 / 검색 질문 / CAG
    의사결정 컨텍스트 / 프로젝트 적용 / 재검토 시점 / 실습 검증).

Hard rules:

  * Empty body refused — :class:`RendererError`.
  * Missing source_url refused — :class:`RendererError`.
  * Missing practice section (topic / steps / completion / common
    mistakes) refused — :class:`RendererError`.
  * Original full text NEVER reproduced verbatim — the renderer
    refuses to embed bodies longer than :data:`_MAX_QUOTATION_CHARS`
    (defensive sanity check only — the quality gate is the real
    enforcement).
  * Secrets are redacted at render time via a simple guard against
    obvious token shapes (defence in depth — collectors should not
    pass them through but if a malformed source slips one in, the
    note never lands with the literal value).

The renderer does NOT touch disk — it returns a string. The Obsidian
bridge :mod:`.obsidian` is what hands it to the writer worker.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence

from .models import (
    ENGINEERING_KNOWLEDGE_CONTRACT,
    EngineeringKnowledgeItem,
    KnowledgeShareScope,
)
from .title_normalizer import display_title_for


_MAX_QUOTATION_CHARS = 1200


class RendererError(Exception):
    """Raised when the item violates a hard contract for vault save."""


# ---------------------------------------------------------------------------
# Secret-redaction guard
# ---------------------------------------------------------------------------


_SECRET_PATTERNS = (
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}", re.I), "[redacted-github-pat]"),
    (re.compile(r"\bgh[psor]_[A-Za-z0-9]{20,}"), "[redacted-github-token]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[redacted-slack-token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "[redacted-api-key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted-aws-key]"),
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"
            r"[\s\S]+?"
            r"-----END (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"
        ),
        "[redacted-private-key-block]",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
        "[redacted-jwt]",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{20,}"), "Bearer [redacted-bearer]"),
)


def _redact(text: str) -> str:
    if not text:
        return ""
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def _yaml_escape(value: str) -> str:
    if value is None:
        return ""
    s = str(value).replace("\\", "\\\\").replace("\"", "\\\"")
    return s


def _quote(value: str) -> str:
    return f"\"{_yaml_escape(value)}\""


def _yaml_list(values: Sequence[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(_quote(_redact(v)) for v in values) + "]"


def render_frontmatter(item: EngineeringKnowledgeItem) -> str:
    """Build the YAML frontmatter block (no leading/trailing markers).

    ``share_scope`` is stamped into the frontmatter so vault tooling
    (memory indexer, archive sync, Discord digest) can decide whether
    an item's body is safe to ship outside the vault. The body
    renderer separately blanks restricted sections — frontmatter alone
    is the discovery hint, never the enforcement.

    The ``title`` line is the **canonical visible title** (date /
    aggregator label / Re: / 이슈: scrubbed). The filename written by
    the path resolver still keeps its own ``YYYY-MM-DD_`` prefix —
    only the human-facing title is rewritten here.
    """

    lines: List[str] = [
        f"title: {_quote(display_title_for(item))}",
        "kind: engineering-knowledge",
        f"status: {item.knowledge_status.value}",
        f"role: {_quote(item.role)}",
        f"audience: {_quote(item.audience.value)}",
        f"importance: {_quote(item.importance.value)}",
        f"learning_level: {_quote(item.learning_level.value)}",
        f"source_kind: {_quote(item.source_kind.value)}",
        f"source_url: {_quote(item.source_url)}",
        f"topic_key: {_quote(item.topic_key)}",
        f"rag_tags: {_yaml_list(item.rag_tags)}",
        f"cag_context_key: {_quote(item.cag_context_key)}",
        f"retrieval_queries: {_yaml_list(item.retrieval_queries)}",
        f"collected_at: {_quote(item.collected_at)}",
        f"review_after_days: {int(item.review_after_days)}",
        f"share_scope: {_quote(item.share_scope.value)}",
        f"share_scope_reason: {_quote(item.share_scope_reason)}",
        f"project: {_quote('yule-studio-agent')}",
        f"contract: {ENGINEERING_KNOWLEDGE_CONTRACT}",
        f"dedup_key: {_quote(item.dedup_key)}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Body sections
# ---------------------------------------------------------------------------


_REQUIRED_SECTIONS: tuple[str, ...] = (
    "1. 문서 개요",
    "2. 핵심 요약",
    "3. 배경",
    "4. 무엇이 바뀌었나",
    "5. 왜 중요한가",
    "6. 실무 영향",
    "7. 권장 대응",
    "8. 실습 주제",
    "9. 실습 과정",
    "10. 완료 기준",
    "11. 자주 하는 실수",
    "12. RAG/CAG 메타데이터",
    "13. 공유 가능 범위",
    "14. 참고 자료",
)


# Sections we redact (replace body with a one-line placeholder) when
# ``share_scope == RESTRICTED``. The L1 vault note still exists so the
# operator can find it, but external surface payloads that copy any of
# these sections by index won't accidentally leak content.
_RESTRICTED_REDACTED_SECTION_INDEXES: tuple[int, ...] = (
    1,   # 핵심 요약
    2,   # 배경
    3,   # 무엇이 바뀌었나
    4,   # 왜 중요한가
    5,   # 실무 영향
    6,   # 권장 대응
    7,   # 실습 주제
    8,   # 실습 과정
    9,   # 완료 기준
    10,  # 자주 하는 실수
)


_RESTRICTED_PLACEHOLDER = (
    "_공개 제한된 자료입니다 — 본문은 vault 내부 채널에서만 공유하세요._"
)


def required_sections() -> tuple[str, ...]:
    return _REQUIRED_SECTIONS


def _section_header(title: str) -> str:
    return f"## {title}"


def _bullets(values: Sequence[str], *, fallback: str = "- (정보 없음)") -> str:
    cleaned = [v.strip() for v in values if v and v.strip()]
    if not cleaned:
        return fallback
    return "\n".join(f"- {_redact(v)}" for v in cleaned)


def _paragraph(value: str, *, fallback: str = "(정보 없음)") -> str:
    text = _redact((value or "").strip())
    if not text:
        return fallback
    return text


def _quotation_guard(value: str) -> str:
    """Hard-cap the length of any single body field to discourage
    full-text reproduction. The quality gate is the real enforcement;
    this is a defence-in-depth ceiling."""

    if not value:
        return ""
    if len(value) <= _MAX_QUOTATION_CHARS:
        return value
    return value[:_MAX_QUOTATION_CHARS].rstrip() + " […]"


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_overview(item: EngineeringKnowledgeItem) -> str:
    lines: List[str] = [_section_header(_REQUIRED_SECTIONS[0])]
    lines.append(
        f"이 문서는 **{_redact(item.role)}** 역할이 챙겨야 할 기술 이슈 "
        f"`{_redact(item.topic_key)}` 를 다룬다."
    )
    if item.stack_tags:
        lines.append(f"관련 스택: {', '.join(_redact(t) for t in item.stack_tags)}.")
    return "\n".join(lines)


def _render_summary(item: EngineeringKnowledgeItem) -> str:
    body = _paragraph(_quotation_guard(item.summary))
    return f"{_section_header(_REQUIRED_SECTIONS[1])}\n{body}"


def _render_background(item: EngineeringKnowledgeItem) -> str:
    body = _paragraph(_quotation_guard(item.why_it_matters))
    return f"{_section_header(_REQUIRED_SECTIONS[2])}\n{body}"


def _render_what_changed(item: EngineeringKnowledgeItem) -> str:
    body = _paragraph(_quotation_guard(item.what_changed))
    return f"{_section_header(_REQUIRED_SECTIONS[3])}\n{body}"


def _render_why_important(item: EngineeringKnowledgeItem) -> str:
    body = _paragraph(_quotation_guard(item.why_it_matters))
    return f"{_section_header(_REQUIRED_SECTIONS[4])}\n{body}"


def _render_practical_impact(item: EngineeringKnowledgeItem) -> str:
    body = _paragraph(_quotation_guard(item.practical_impact))
    return f"{_section_header(_REQUIRED_SECTIONS[5])}\n{body}"


def _render_recommended(item: EngineeringKnowledgeItem) -> str:
    body = _paragraph(_quotation_guard(item.recommended_action))
    return f"{_section_header(_REQUIRED_SECTIONS[6])}\n{body}"


def _render_practice_topic(item: EngineeringKnowledgeItem) -> str:
    parts: List[str] = [_section_header(_REQUIRED_SECTIONS[7])]
    parts.append(_paragraph(item.practice_topic))
    if item.practice_goal:
        parts.append(f"\n**실습 목표**: {_redact(item.practice_goal)}")
    return "\n".join(parts)


def _render_practice_steps(item: EngineeringKnowledgeItem) -> str:
    parts: List[str] = [_section_header(_REQUIRED_SECTIONS[8])]
    if not item.practice_steps:
        parts.append("- (실습 단계가 비어 있다 — 저장 차단됨)")
    else:
        for index, step in enumerate(item.practice_steps, start=1):
            parts.append(f"{index}. {_redact(step)}")
    if item.estimated_practice_time:
        parts.append(f"\n**예상 소요 시간**: {_redact(item.estimated_practice_time)}")
    return "\n".join(parts)


def _render_completion(item: EngineeringKnowledgeItem) -> str:
    parts: List[str] = [_section_header(_REQUIRED_SECTIONS[9])]
    if item.practice_checklist:
        parts.append("**실습 완료 체크리스트**")
        for line in item.practice_checklist:
            parts.append(f"- [ ] {_redact(line)}")
    else:
        parts.append("- [ ] (체크리스트가 비어 있다)")
    if item.expected_output:
        parts.append(f"\n**기대 결과**: {_redact(item.expected_output)}")
    if item.practice_verification is not None:
        v = item.practice_verification
        parts.append("\n### 실습 검증")
        parts.append(f"- **expected_result**: {_redact(v.expected_result)}")
        if v.command_to_run:
            parts.append(f"- **command_to_run**: `{_redact(v.command_to_run)}`")
        if v.failure_symptoms:
            parts.append("- **failure_symptoms**:")
            for sym in v.failure_symptoms:
                parts.append(f"  - {_redact(sym)}")
        if v.troubleshooting_hint:
            parts.append(
                f"- **troubleshooting_hint**: {_redact(v.troubleshooting_hint)}"
            )
    return "\n".join(parts)


def _render_common_mistakes(item: EngineeringKnowledgeItem) -> str:
    parts: List[str] = [_section_header(_REQUIRED_SECTIONS[10])]
    parts.append(_bullets(item.common_mistakes, fallback="- (자주 하는 실수가 비어 있다)"))
    return "\n".join(parts)


def _render_rag_cag(item: EngineeringKnowledgeItem) -> str:
    parts: List[str] = [_section_header(_REQUIRED_SECTIONS[11])]
    parts.append(f"- **rag_tags**: {', '.join(_redact(t) for t in item.rag_tags) or '(none)'}")
    parts.append(f"- **cag_context_key**: `{_redact(item.cag_context_key) or '(none)'}`")
    if item.retrieval_summary:
        parts.append(f"- **retrieval_summary**: {_redact(item.retrieval_summary)}")
    if item.retrieval_queries:
        parts.append("- **검색될 질문 예시**:")
        for q in item.retrieval_queries:
            parts.append(f"  - {_redact(q)}")
    if item.cag_context is not None:
        ctx = item.cag_context
        parts.append("\n### CAG 의사결정 컨텍스트")
        parts.append(f"- **when_to_use**: {_redact(ctx.when_to_use)}")
        if ctx.constraints:
            parts.append("- **constraints**:")
            for c in ctx.constraints:
                parts.append(f"  - {_redact(c)}")
        if ctx.decision_hint:
            parts.append(f"- **decision_hint**: {_redact(ctx.decision_hint)}")
        if ctx.avoid_if:
            parts.append("- **avoid_if**:")
            for a in ctx.avoid_if:
                parts.append(f"  - {_redact(a)}")

    parts.append("\n### 학습 난이도와 선수 지식")
    parts.append(f"- **learning_level**: {item.learning_level.value}")
    if item.prerequisites:
        parts.append("- **선수 지식**:")
        for p in item.prerequisites:
            parts.append(f"  - {_redact(p)}")
    if item.next_topics:
        parts.append("- **다음 학습 주제**:")
        for t in item.next_topics:
            parts.append(f"  - {_redact(t)}")

    if item.project_applicability is not None:
        pa = item.project_applicability
        parts.append("\n### 프로젝트 적용 후보")
        if pa.related_repo:
            parts.append(f"- **related_repo**: {_redact(pa.related_repo)}")
        if pa.related_module:
            parts.append(f"- **related_module**: `{_redact(pa.related_module)}`")
        if pa.possible_issue_title:
            parts.append(
                f"- **possible_issue_title**: {_redact(pa.possible_issue_title)}"
            )
        parts.append(f"- **implementation_risk**: {_redact(pa.implementation_risk)}")

    parts.append(
        f"\n### 재검토 시점\n- **review_after_days**: {int(item.review_after_days)} 일"
    )
    if item.staleness_reason:
        parts.append(f"- **staleness_reason**: {_redact(item.staleness_reason)}")
    return "\n".join(parts)


_SHARE_SCOPE_LABELS: dict[KnowledgeShareScope, str] = {
    KnowledgeShareScope.PUBLIC: (
        "공개 가능 — 외부 surface(Discord 다이제스트, PR 본문, 합성 응답)에 "
        "본문 요약과 함께 인용해도 된다"
    ),
    KnowledgeShareScope.TEAM_INTERNAL: (
        "팀 내부 한정 — Obsidian vault 내에서만 본문 열람. 외부 채널에는 "
        "제목 + 출처 링크 + share_scope 표시까지만 노출"
    ),
    KnowledgeShareScope.RESTRICTED: (
        "공개 제한 — 본문 전체를 외부 surface 로 옮기지 않는다. "
        "vault 안에서도 요약 1~2줄과 share_scope_reason 만 보존"
    ),
}


_SHARE_SCOPE_EXTERNAL_RULES: dict[KnowledgeShareScope, tuple[str, ...]] = {
    KnowledgeShareScope.PUBLIC: (
        "Discord digest / 합성 응답에 제목·요약·source_url 인용 가능",
        "PR 본문에 references 항목으로 그대로 추가 가능",
    ),
    KnowledgeShareScope.TEAM_INTERNAL: (
        "외부 surface 에는 제목 + source_url + 'team-internal' 라벨만 노출",
        "본문 요약은 합성 응답에 직접 인용하지 않는다 — vault link 로 우회",
        "PR 본문 references 에 포함할 때 'internal-only' 노트와 함께",
    ),
    KnowledgeShareScope.RESTRICTED: (
        "외부 surface 에는 '공개 제한된 자료 1건' 신호만 노출",
        "본문/실습 단계 어떤 형태로도 chat·PR·로그에 인용 금지",
        "공유가 필요하면 운영자가 별도 보안 채널에서 수동 전달",
    ),
}


def _render_share_scope(item: EngineeringKnowledgeItem) -> str:
    parts: List[str] = [_section_header(_REQUIRED_SECTIONS[12])]
    label = _SHARE_SCOPE_LABELS.get(
        item.share_scope, item.share_scope.value
    )
    parts.append(f"- **share_scope**: `{item.share_scope.value}` — {label}")
    if item.share_scope_reason:
        parts.append(
            f"- **share_scope_reason**: {_redact(item.share_scope_reason)}"
        )
    parts.append("- **외부 surface 노출 규칙**:")
    for rule in _SHARE_SCOPE_EXTERNAL_RULES.get(item.share_scope, ()):
        parts.append(f"  - {rule}")
    return "\n".join(parts)


def _render_references(item: EngineeringKnowledgeItem) -> str:
    parts: List[str] = [_section_header(_REQUIRED_SECTIONS[13])]
    parts.append(f"- 출처: [{_redact(item.source_name)}]({_redact(item.source_url)})")
    for ref in item.references:
        if ref:
            parts.append(f"- {_redact(ref)}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Header / TOC
# ---------------------------------------------------------------------------


def _doc_header(item: EngineeringKnowledgeItem) -> str:
    today = item.collected_at.split("T", 1)[0] if item.collected_at else ""
    # H1 is the canonical visible title — date / aggregator label /
    # Re: / 이슈: scrubbed. The collected_at date still surfaces in the
    # version table row below.
    lines = [
        f"# {display_title_for(item)}",
        "",
        "| 문서 버전 | 작성일 | 작성자 | 주요 변경 사항 |",
        "| --- | --- | --- | --- |",
        f"| v.1.0.0 | {today} | {_redact(item.role)} | 최초 수집 및 학습 문서화 |",
    ]
    return "\n".join(lines)


def _toc() -> str:
    lines = ["## 목차"]
    for index, title in enumerate(_REQUIRED_SECTIONS, start=1):
        # Strip the "N. " prefix from the section title for the TOC
        # (keep the numbering on the link line itself).
        lines.append(f"{index}. {title.split('. ', 1)[1]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_engineering_knowledge_note(item: EngineeringKnowledgeItem) -> str:
    """Return the full markdown document for *item*.

    Raises :class:`RendererError` when the item violates one of the
    hard contracts (empty body, missing source, missing practice).

    When ``item.share_scope == KnowledgeShareScope.RESTRICTED`` the
    learning-body sections are replaced with a one-line placeholder so
    the vault note still tracks the topic but no body content can be
    copied to an external surface. The Overview / RAG-CAG metadata /
    공유 가능 범위 / References sections still render — they are
    structural pointers, not body content.
    """

    if not item.title.strip():
        raise RendererError("title is empty — cannot render note")
    if not item.source_url.strip():
        raise RendererError("source_url is empty — cannot render note")
    if not item.summary.strip():
        raise RendererError("summary is empty — cannot render note")
    if not item.practice_topic.strip():
        raise RendererError("practice_topic is empty — cannot render note")
    if not item.practice_steps or len([s for s in item.practice_steps if s.strip()]) < 2:
        raise RendererError("practice_steps must have at least 2 non-empty entries")
    if not item.common_mistakes or not any(
        m.strip() for m in item.common_mistakes
    ):
        raise RendererError("common_mistakes must have at least 1 non-empty entry")
    if (
        item.share_scope == KnowledgeShareScope.RESTRICTED
        and not item.share_scope_reason.strip()
    ):
        raise RendererError(
            "share_scope=RESTRICTED requires share_scope_reason"
        )

    body_renderers = [
        _render_overview,        # 0 — structural, keep
        _render_summary,         # 1 — body
        _render_background,      # 2 — body
        _render_what_changed,    # 3 — body
        _render_why_important,   # 4 — body
        _render_practical_impact,# 5 — body
        _render_recommended,     # 6 — body
        _render_practice_topic,  # 7 — body
        _render_practice_steps,  # 8 — body
        _render_completion,      # 9 — body
        _render_common_mistakes, # 10 — body
        _render_rag_cag,         # 11 — metadata, keep
        _render_share_scope,     # 12 — share-scope, keep
        _render_references,      # 13 — references, keep
    ]

    blocks: List[str] = []
    blocks.append("---")
    blocks.append(render_frontmatter(item))
    blocks.append("---")
    blocks.append("")
    blocks.append(_doc_header(item))
    blocks.append("")
    blocks.append(_toc())
    blocks.append("")

    restricted = item.share_scope == KnowledgeShareScope.RESTRICTED
    for index, renderer in enumerate(body_renderers):
        if restricted and index in _RESTRICTED_REDACTED_SECTION_INDEXES:
            blocks.append(
                f"{_section_header(_REQUIRED_SECTIONS[index])}\n"
                f"{_RESTRICTED_PLACEHOLDER}"
            )
        else:
            blocks.append(renderer(item))
        blocks.append("")
    return "\n".join(blocks)


__all__ = [
    "RendererError",
    "render_engineering_knowledge_note",
    "render_frontmatter",
    "required_sections",
]
