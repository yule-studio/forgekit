"""engineering_conversation — autonomous research collection + intake body.

Two responsibilities live here:

1. **Collector wiring** — :func:`_maybe_run_auto_collect` is the single
   call site that bridges the conversation layer to
   ``agents.research.collector.auto_collect_or_request_more_input``.
   The function defensively guards against:

   - ``auto_collect=False`` callers,
   - blank / command-only prompts (P0-K hard rule —
     ``is_non_actionable_prompt`` blocks the live MVP echo loop),
   - collector import failures (partial install / refactor in progress),
   - any exception in the collector itself (silent skip — conversation
     layer must never crash on collector wiring).

   Future bugfix surface for "when does auto_collect actually run" lives
   in this function only.

2. **ResearchPack candidate classification + intake body formatting** —
   ``classify_attachment``, ``classify_url``,
   ``collect_research_candidates_from_message``,
   ``suggest_role_research_assignments``,
   ``format_insufficient_research_prompt``,
   ``build_research_pack_from_candidates``, plus the collection-aware
   intake responders ``_format_intake_with_collection``,
   ``_format_collection_announcement``, ``_format_collection_meta_block``,
   ``_format_coding_bootstrap_body``.

Note: ``_summarize_topic`` / ``_pretty_task_type`` / ``_pretty_provider``
belong to ``response_formatters`` (audit doc §2). Until that module is
extracted in step 8, the few call sites here import them lazily from
``._legacy`` to avoid a temporary circular dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

from ...agents.messaging.dispatcher import TaskType
from ...agents.research.pack import (
    ResearchAttachment,
    ResearchPack,
    ResearchSource,
    extract_urls,
)


# ---------------------------------------------------------------------------
# Source type registry + host suffix maps
# ---------------------------------------------------------------------------


SOURCE_TYPE_USER_MESSAGE = "user_message"
SOURCE_TYPE_URL = "url"
SOURCE_TYPE_WEB_RESULT = "web_result"
SOURCE_TYPE_IMAGE_REFERENCE = "image_reference"
SOURCE_TYPE_FILE_ATTACHMENT = "file_attachment"
SOURCE_TYPE_GITHUB_ISSUE = "github_issue"
SOURCE_TYPE_GITHUB_PR = "github_pr"
SOURCE_TYPE_CODE_CONTEXT = "code_context"
SOURCE_TYPE_OFFICIAL_DOCS = "official_docs"
SOURCE_TYPE_COMMUNITY_SIGNAL = "community_signal"
SOURCE_TYPE_DESIGN_REFERENCE = "design_reference"


ALL_SOURCE_TYPES: tuple[str, ...] = (
    SOURCE_TYPE_USER_MESSAGE,
    SOURCE_TYPE_URL,
    SOURCE_TYPE_WEB_RESULT,
    SOURCE_TYPE_IMAGE_REFERENCE,
    SOURCE_TYPE_FILE_ATTACHMENT,
    SOURCE_TYPE_GITHUB_ISSUE,
    SOURCE_TYPE_GITHUB_PR,
    SOURCE_TYPE_CODE_CONTEXT,
    SOURCE_TYPE_OFFICIAL_DOCS,
    SOURCE_TYPE_COMMUNITY_SIGNAL,
    SOURCE_TYPE_DESIGN_REFERENCE,
)


# 이미지 확장자: 요구사항에 따라 png, jpg, jpeg, webp, gif 는 image_reference.
IMAGE_EXTENSIONS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".gif")


# Discord 첨부의 content_type 이 image/* 로 오는 경우도 있으므로 같이 본다.
_IMAGE_CONTENT_TYPE_PREFIX = "image/"


# 디자인 reference 도메인 (자동 fetch 금지 소스 포함). URL 분류 단계에서
# source_type 만 design_reference 로 라우팅하고, 실제 자동 수집은 안 한다
# (engineering-agent/discord-workflow.md §4.3, env-strategy.md §7).
_DESIGN_REFERENCE_HOSTS: tuple[str, ...] = (
    "pinterest.com",
    "pinterest.co.kr",
    "kr.pinterest.com",
    "notefolio.net",
    "behance.net",
    "awwwards.com",
    "dribbble.com",
    "mobbin.com",
    "pageflows.com",
    "canva.com",
    "wix.com",
    "wixstudio.com",
    "templates.wix.com",
)


# 공식 문서 도메인 (휴리스틱). 부분 일치 (endswith) 로 본다.
_OFFICIAL_DOCS_HOST_SUFFIXES: tuple[str, ...] = (
    "developer.mozilla.org",
    "docs.python.org",
    "react.dev",
    "reactjs.org",
    "vuejs.org",
    "nextjs.org",
    "vitejs.dev",
    "nodejs.org",
    "go.dev",
    "kubernetes.io",
    "docs.docker.com",
    "developers.google.com",
    "cloud.google.com",
    "docs.aws.amazon.com",
    "learn.microsoft.com",
    "docs.microsoft.com",
    "developer.apple.com",
    "developer.android.com",
    "developer.chrome.com",
    "web.dev",
    "owasp.org",
    "ecma-international.org",
    "rfc-editor.org",
    "tools.ietf.org",
)


# 커뮤니티 신호 도메인. forum/discussion/Q&A 류.
_COMMUNITY_SIGNAL_HOST_SUFFIXES: tuple[str, ...] = (
    "reddit.com",
    "stackoverflow.com",
    "stackexchange.com",
    "news.ycombinator.com",
    "ycombinator.com",
    "lobste.rs",
    "discord.com",
    "discord.gg",
    "twitter.com",
    "x.com",
    "medium.com",
    "dev.to",
    "qiita.com",
    "velog.io",
    "tistory.com",
)


# 역할별 우선 수집 source_type 순서 (앞쪽이 가장 중요).
ROLE_RESEARCH_PROFILES: Mapping[str, tuple[str, ...]] = {
    "product-designer": (
        SOURCE_TYPE_IMAGE_REFERENCE,
        SOURCE_TYPE_DESIGN_REFERENCE,
        SOURCE_TYPE_FILE_ATTACHMENT,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_USER_MESSAGE,
        SOURCE_TYPE_COMMUNITY_SIGNAL,
    ),
    "backend-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_USER_MESSAGE,
    ),
    "frontend-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_IMAGE_REFERENCE,
        SOURCE_TYPE_DESIGN_REFERENCE,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_USER_MESSAGE,
    ),
    "qa-engineer": (
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_USER_MESSAGE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_URL,
    ),
    "tech-lead": (
        SOURCE_TYPE_USER_MESSAGE,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_DESIGN_REFERENCE,
        SOURCE_TYPE_IMAGE_REFERENCE,
    ),
    "ai-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_COMMUNITY_SIGNAL,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_USER_MESSAGE,
    ),
    "devops-engineer": (
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_CODE_CONTEXT,
        SOURCE_TYPE_URL,
        SOURCE_TYPE_USER_MESSAGE,
    ),
}


# task_type 별로 "이건 꼭 있어야 함" 인 source_type. 부족 판정에 사용.
_REQUIRED_SOURCE_TYPES_BY_TASK_TYPE: Mapping[str, tuple[str, ...]] = {
    TaskType.LANDING_PAGE.value: (SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE),
    TaskType.VISUAL_POLISH.value: (SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE),
    TaskType.ONBOARDING_FLOW.value: (SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE),
    TaskType.EMAIL_CAMPAIGN.value: (SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE),
    TaskType.FRONTEND_FEATURE.value: (SOURCE_TYPE_OFFICIAL_DOCS, SOURCE_TYPE_IMAGE_REFERENCE),
    TaskType.BACKEND_FEATURE.value: (SOURCE_TYPE_OFFICIAL_DOCS, SOURCE_TYPE_GITHUB_ISSUE),
    TaskType.QA_TEST.value: (SOURCE_TYPE_GITHUB_ISSUE, SOURCE_TYPE_CODE_CONTEXT),
    TaskType.PLATFORM_INFRA.value: (SOURCE_TYPE_OFFICIAL_DOCS, SOURCE_TYPE_CODE_CONTEXT),
    # P0-J (#145): full-stack 앱은 docs + code context 둘 다 권장하지만
    # github_target / write intent 가 있으면 commit 5 의 coding bootstrap
    # 우회가 insufficiency 를 막아줌. 본 표는 정보 제공용.
    TaskType.FULL_STACK_APP.value: (SOURCE_TYPE_OFFICIAL_DOCS, SOURCE_TYPE_CODE_CONTEXT),
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchCandidate:
    """One unit of research collected from a Discord conversation turn.

    ``ResearchPack`` (in ``agents/research_pack.py``) is the long-lived neutral
    artifact, but it lacks a few engineering-loop fields (source_type,
    why_relevant, risk_or_limit, confidence). ``ResearchCandidate`` carries
    those explicitly and is what the conversation/forum layers feed into a
    pack via :func:`build_research_pack_from_candidates`.
    """

    source_type: str
    title: str
    summary: str
    collected_by_role: str
    why_relevant: str
    risk_or_limit: Optional[str] = None
    confidence: str = "medium"  # "high" / "medium" / "low"
    url: Optional[str] = None
    attachment_id: Optional[str] = None
    collected_at: Optional[datetime] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchCollectionResult:
    """Outcome of a single message → research collection pass.

    ``insufficient`` is True when the layer thinks the user must add more
    context before deliberation can start (no URLs, no attachments, and the
    text alone is too thin or task_type demands missing categories).
    ``role_assignments`` maps role → tuple of source_types the role still
    lacks given the role's profile. Empty mapping when nothing is missing.
    """

    candidates: Sequence[ResearchCandidate]
    insufficient: bool = False
    insufficient_reason: Optional[str] = None
    follow_up_prompt: Optional[str] = None
    role_assignments: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Attachment shape (discord.py-agnostic)
# ---------------------------------------------------------------------------


def _attachment_field(attachment: Any, *names: str) -> Any:
    """Read the first available attribute or mapping key from an attachment.

    Discord.py exposes attachments as objects with attributes; tests pass
    plain ``SimpleNamespace`` or dicts. We accept both.
    """

    if isinstance(attachment, Mapping):
        for name in names:
            if name in attachment and attachment[name] is not None:
                return attachment[name]
        return None
    for name in names:
        value = getattr(attachment, name, None)
        if value is not None:
            return value
    return None


def _attachment_filename(attachment: Any) -> str:
    raw = _attachment_field(attachment, "filename", "name")
    return str(raw or "").strip()


def _attachment_url(attachment: Any) -> Optional[str]:
    raw = _attachment_field(attachment, "url", "proxy_url")
    if raw is None:
        return None
    cleaned = str(raw).strip()
    return cleaned or None


def _attachment_content_type(attachment: Any) -> str:
    raw = _attachment_field(attachment, "content_type", "mime_type")
    return str(raw or "").strip().lower()


def _attachment_id(attachment: Any) -> Optional[str]:
    raw = _attachment_field(attachment, "id", "attachment_id")
    if raw is None:
        return None
    return str(raw).strip() or None


def _attachment_size(attachment: Any) -> Optional[int]:
    raw = _attachment_field(attachment, "size", "size_bytes")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Attachment / URL classification
# ---------------------------------------------------------------------------


def classify_attachment(
    *,
    filename: str = "",
    content_type: str = "",
) -> str:
    """Decide whether a Discord attachment is an image_reference or generic file.

    Image is detected by extension (``.png/.jpg/.jpeg/.webp/.gif``) OR by an
    ``image/*`` content_type. Anything else falls back to file_attachment.
    """

    name = (filename or "").strip().lower()
    if name.endswith(IMAGE_EXTENSIONS):
        return SOURCE_TYPE_IMAGE_REFERENCE
    ctype = (content_type or "").strip().lower()
    if ctype.startswith(_IMAGE_CONTENT_TYPE_PREFIX):
        return SOURCE_TYPE_IMAGE_REFERENCE
    return SOURCE_TYPE_FILE_ATTACHMENT


def classify_url(url: str) -> str:
    """Bucket a URL into a source_type by host heuristic.

    GitHub ``/issues/<n>`` and ``/pull/<n>`` short-circuit to the dedicated
    types so qa/backend roles can see them without a network call. Pinterest
    / Behance / Awwwards / Wix / Canva style hosts are flagged as
    design_reference. Documentation-flavored hosts become official_docs;
    Reddit/HN/Stack* become community_signal. Anything else is the generic
    ``url`` bucket.
    """

    if not url:
        return SOURCE_TYPE_URL
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if not host:
        return SOURCE_TYPE_URL

    if host.endswith("github.com"):
        path = parsed.path or ""
        if re.search(r"/issues/\d+", path):
            return SOURCE_TYPE_GITHUB_ISSUE
        if re.search(r"/pull/\d+", path):
            return SOURCE_TYPE_GITHUB_PR

    for design_host in _DESIGN_REFERENCE_HOSTS:
        if host == design_host or host.endswith("." + design_host):
            return SOURCE_TYPE_DESIGN_REFERENCE

    for docs_suffix in _OFFICIAL_DOCS_HOST_SUFFIXES:
        if host == docs_suffix or host.endswith("." + docs_suffix):
            return SOURCE_TYPE_OFFICIAL_DOCS

    for community_suffix in _COMMUNITY_SIGNAL_HOST_SUFFIXES:
        if host == community_suffix or host.endswith("." + community_suffix):
            return SOURCE_TYPE_COMMUNITY_SIGNAL

    return SOURCE_TYPE_URL


# ---------------------------------------------------------------------------
# Per-candidate metadata helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, *, max_chars: int = 200) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _why_relevant_for(source_type: str, *, task_type: Optional[str]) -> str:
    if source_type == SOURCE_TYPE_USER_MESSAGE:
        return "사용자가 직접 적어 준 요구사항이라 모든 역할의 출발점이다."
    if source_type == SOURCE_TYPE_IMAGE_REFERENCE:
        return "디자이너/프론트엔드의 시각 reference 1순위. moodboard 후보."
    if source_type == SOURCE_TYPE_FILE_ATTACHMENT:
        return "사용자가 첨부한 파일 — 컨텍스트로 그대로 인용 가능."
    if source_type == SOURCE_TYPE_DESIGN_REFERENCE:
        return "디자인 참고 자료 (Pinterest/Behance/Awwwards 등). 자동 수집 금지 소스이므로 사용자 제공 링크만 인정."
    if source_type == SOURCE_TYPE_OFFICIAL_DOCS:
        return "공식 문서. 백엔드/프론트엔드/인프라 역할의 1순위 신뢰원."
    if source_type == SOURCE_TYPE_GITHUB_ISSUE:
        return "GitHub issue. QA/백엔드 회귀/요구사항 추적의 직접 근거."
    if source_type == SOURCE_TYPE_GITHUB_PR:
        return "GitHub PR. 변경 이력과 리뷰 흐름의 직접 근거."
    if source_type == SOURCE_TYPE_COMMUNITY_SIGNAL:
        return "커뮤니티 신호. 비공식이지만 사용자 페인포인트나 사례 빠르게 본다."
    if source_type == SOURCE_TYPE_URL:
        if task_type:
            return f"{task_type} 후속 검토용 일반 URL. 도메인 분류 미지정."
        return "도메인 분류 없는 일반 URL. 후속 단계에서 재분류한다."
    if source_type == SOURCE_TYPE_CODE_CONTEXT:
        return "현재 레포 코드/문서 맥락. backend/qa 가 회귀 기준으로 활용."
    if source_type == SOURCE_TYPE_WEB_RESULT:
        return "검색 결과. 후속 fetch 단계에서 채워질 슬롯."
    return "후속 분류 대기."


def _risk_or_limit_for(source_type: str) -> Optional[str]:
    if source_type == SOURCE_TYPE_DESIGN_REFERENCE:
        return "Pinterest/Notefolio/Behance/Mobbin/Page Flows/Awwwards 등은 약관상 자동 수집 금지. 사용자 제공 링크로만 사용한다."
    if source_type == SOURCE_TYPE_COMMUNITY_SIGNAL:
        return "비공식 신호. 단독 근거로는 부족하므로 official_docs 또는 code_context 와 교차 검증해야 한다."
    if source_type == SOURCE_TYPE_USER_MESSAGE:
        return "원문 그대로의 요구사항이므로 해석 차이가 생길 수 있다. 1차 deliberation 에서 명확화 질문을 동반해야 한다."
    if source_type == SOURCE_TYPE_FILE_ATTACHMENT:
        return "Discord CDN URL 은 만료될 수 있으므로 본문 발췌나 hash 를 함께 보존하는 게 안전하다."
    if source_type == SOURCE_TYPE_IMAGE_REFERENCE:
        return "Discord CDN URL 은 만료될 수 있으므로 캡션/텍스트 설명을 함께 적어두는 게 좋다."
    return None


def _confidence_for(source_type: str) -> str:
    if source_type in (
        SOURCE_TYPE_USER_MESSAGE,
        SOURCE_TYPE_OFFICIAL_DOCS,
        SOURCE_TYPE_GITHUB_ISSUE,
        SOURCE_TYPE_GITHUB_PR,
        SOURCE_TYPE_FILE_ATTACHMENT,
        SOURCE_TYPE_IMAGE_REFERENCE,
    ):
        return "high"
    if source_type in (
        SOURCE_TYPE_URL,
        SOURCE_TYPE_DESIGN_REFERENCE,
        SOURCE_TYPE_CODE_CONTEXT,
    ):
        return "medium"
    return "low"


def _url_title(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or ""
    if host and path and path != "/":
        return f"{host}{path}"
    return host or url


# ---------------------------------------------------------------------------
# Candidate collection
# ---------------------------------------------------------------------------


def collect_research_candidates_from_message(
    message_text: str,
    *,
    attachments: Sequence[Any] = (),
    author_role: str = "tech-lead",
    posted_at: Optional[datetime] = None,
    task_type: Optional[str] = None,
) -> ResearchCollectionResult:
    """Pull research candidates out of a single Discord message.

    Builds, in order:

    1. one ``user_message`` candidate from *message_text* (always, when the
       text is non-empty),
    2. one candidate per URL found inside the text, classified by host into
       url / design_reference / official_docs / github_issue / github_pr /
       community_signal,
    3. one candidate per attachment, classified into image_reference or
       file_attachment.

    If the result has only the user message (no URL, no attachment) and the
    text is short, the result is flagged ``insufficient`` and a Korean
    follow-up prompt is filled in. ``role_assignments`` reports per-role
    missing source_types whenever *task_type* is known.
    """

    candidates: list[ResearchCandidate] = []
    text = (message_text or "").strip()

    if text:
        candidates.append(
            ResearchCandidate(
                source_type=SOURCE_TYPE_USER_MESSAGE,
                title=_truncate(text, max_chars=80),
                summary=_truncate(text, max_chars=400),
                collected_by_role=author_role,
                why_relevant=_why_relevant_for(SOURCE_TYPE_USER_MESSAGE, task_type=task_type),
                risk_or_limit=_risk_or_limit_for(SOURCE_TYPE_USER_MESSAGE),
                confidence=_confidence_for(SOURCE_TYPE_USER_MESSAGE),
                collected_at=posted_at,
            )
        )

    for url in extract_urls(text):
        url_type = classify_url(url)
        candidates.append(
            ResearchCandidate(
                source_type=url_type,
                title=_url_title(url),
                summary=_truncate(url, max_chars=400),
                collected_by_role=author_role,
                why_relevant=_why_relevant_for(url_type, task_type=task_type),
                risk_or_limit=_risk_or_limit_for(url_type),
                confidence=_confidence_for(url_type),
                url=url,
                collected_at=posted_at,
            )
        )

    for attachment in attachments:
        filename = _attachment_filename(attachment)
        content_type = _attachment_content_type(attachment)
        url = _attachment_url(attachment)
        attachment_id = _attachment_id(attachment)
        size_bytes = _attachment_size(attachment)
        kind = classify_attachment(filename=filename, content_type=content_type)
        title = filename or (f"attachment-{attachment_id}" if attachment_id else "(attachment)")
        summary_parts: list[str] = []
        if filename:
            summary_parts.append(filename)
        if content_type:
            summary_parts.append(content_type)
        if size_bytes is not None:
            summary_parts.append(f"{size_bytes} bytes")
        summary = " · ".join(summary_parts) or "(no metadata)"
        candidates.append(
            ResearchCandidate(
                source_type=kind,
                title=title,
                summary=summary,
                collected_by_role=author_role,
                why_relevant=_why_relevant_for(kind, task_type=task_type),
                risk_or_limit=_risk_or_limit_for(kind),
                confidence=_confidence_for(kind),
                url=url,
                attachment_id=attachment_id,
                collected_at=posted_at,
                extra={
                    "filename": filename or None,
                    "content_type": content_type or None,
                    "size_bytes": size_bytes,
                },
            )
        )

    insufficient, reason = _evaluate_research_sufficiency(
        candidates=candidates,
        text=text,
        task_type=task_type,
    )
    follow_up = format_insufficient_research_prompt(reason) if insufficient else None
    role_assignments = (
        suggest_role_research_assignments(
            task_type=task_type,
            collected_source_types=tuple(c.source_type for c in candidates),
        )
        if task_type
        else {}
    )

    return ResearchCollectionResult(
        candidates=tuple(candidates),
        insufficient=insufficient,
        insufficient_reason=reason,
        follow_up_prompt=follow_up,
        role_assignments=role_assignments,
    )


def suggest_role_research_assignments(
    *,
    task_type: Optional[str],
    collected_source_types: Sequence[str],
    roles: Sequence[str] = (
        "product-designer",
        "frontend-engineer",
        "backend-engineer",
        "qa-engineer",
        "tech-lead",
    ),
    max_per_role: int = 3,
) -> Mapping[str, tuple[str, ...]]:
    """Return per-role lists of source_types still missing.

    Iterates each role's ``ROLE_RESEARCH_PROFILES`` ranking, drops
    source_types we already have, and trims to *max_per_role* items so the
    operator gets a small actionable nudge instead of the whole catalogue.

    A role is omitted from the returned mapping if it has nothing to ask
    for. *task_type* is currently advisory — it informs which categories
    are required (see ``_REQUIRED_SOURCE_TYPES_BY_TASK_TYPE``) but the
    role's personal profile drives the ordering.
    """

    have = {st for st in collected_source_types if st}
    required: tuple[str, ...] = ()
    if task_type and task_type in _REQUIRED_SOURCE_TYPES_BY_TASK_TYPE:
        required = _REQUIRED_SOURCE_TYPES_BY_TASK_TYPE[task_type]

    assignments: dict[str, tuple[str, ...]] = {}
    for role in roles:
        profile = ROLE_RESEARCH_PROFILES.get(role)
        if not profile:
            continue
        ordered: list[str] = []
        # Required-by-task_type first (if not yet collected) — but only for
        # roles whose profile actually values that source_type.
        for source_type in required:
            if source_type in have:
                continue
            if source_type in profile and source_type not in ordered:
                ordered.append(source_type)
        for source_type in profile:
            if source_type in have:
                continue
            if source_type == SOURCE_TYPE_USER_MESSAGE:
                # 사용자가 직접 발화한 case 가 아닌 한 user_message 는 이미
                # 채워지므로 추천에서 빼준다. 비어 있으면 자연스럽게 노출.
                continue
            if source_type not in ordered:
                ordered.append(source_type)
            if len(ordered) >= max_per_role:
                break
        if ordered:
            assignments[role] = tuple(ordered[:max_per_role])
    return assignments


def format_insufficient_research_prompt(reason: Optional[str] = None) -> str:
    """Return the Korean follow-up question we send when the pack is too thin.

    Always opens with "자료가 부족합니다." per spec so the operator can rely
    on string matching in tests / instrumentation.
    """

    body = (
        "자료가 부족합니다. 참고할 링크나 이미지를 올려주실까요?"
    )
    if reason:
        body += f"\n사유: {reason}"
    body += (
        "\n다음 중 하나라도 함께 주시면 deliberation 단계로 바로 넘어갈 수 있어요."
        "\n- 참고 화면이나 스크린샷"
        "\n- 관련 이슈 / PR / 공식 문서 링크"
        "\n- 비슷한 사례를 본 경쟁 서비스 URL"
    )
    return body


def build_research_pack_from_candidates(
    *,
    title: str,
    candidates: Sequence[ResearchCandidate],
    channel_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    message_id: Optional[int] = None,
    posted_at: Optional[datetime] = None,
    tags: Sequence[str] = (),
    extra: Optional[Mapping[str, Any]] = None,
) -> ResearchPack:
    """Materialise a ``ResearchPack`` from collected candidates.

    Each candidate becomes one ``ResearchSource``. The engineering-loop
    fields (source_type, why_relevant, risk_or_limit, confidence,
    attachment_id) are stashed in ``ResearchSource.extra`` so the neutral
    research_pack data model never has to grow per-department fields.
    """

    if not candidates:
        raise ValueError("build_research_pack_from_candidates requires at least one candidate")

    sources: list[ResearchSource] = []
    primary_url: Optional[str] = None
    for candidate in candidates:
        if primary_url is None and candidate.url:
            primary_url = candidate.url
        attachments: tuple[ResearchAttachment, ...] = ()
        if candidate.source_type in (SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_FILE_ATTACHMENT):
            kind = "image" if candidate.source_type == SOURCE_TYPE_IMAGE_REFERENCE else "file"
            attachment_url = candidate.url or candidate.attachment_id or ""
            attachments = (
                ResearchAttachment(
                    kind=kind,
                    url=attachment_url,
                    filename=str(candidate.extra.get("filename") or "") or candidate.title,
                    content_type=str(candidate.extra.get("content_type") or "") or None,
                    size_bytes=candidate.extra.get("size_bytes"),
                    description=candidate.summary or None,
                ),
            )
        sources.append(
            ResearchSource(
                source_url=candidate.url,
                title=candidate.title,
                summary=candidate.summary,
                author_role=candidate.collected_by_role,
                channel_id=channel_id,
                thread_id=thread_id,
                message_id=message_id,
                posted_at=candidate.collected_at or posted_at,
                attachments=attachments,
                extra={
                    "source_type": candidate.source_type,
                    "why_relevant": candidate.why_relevant,
                    "risk_or_limit": candidate.risk_or_limit,
                    "confidence": candidate.confidence,
                    "attachment_id": candidate.attachment_id,
                    **{k: v for k, v in candidate.extra.items() if k not in {
                        "filename",
                        "content_type",
                        "size_bytes",
                    }},
                },
            )
        )

    return ResearchPack(
        title=(title or "(untitled)").strip() or "(untitled)",
        summary=candidates[0].summary,
        primary_url=primary_url,
        sources=tuple(sources),
        tags=tuple(tags),
        created_at=posted_at,
        extra=dict(extra or {}),
    )


def _evaluate_research_sufficiency(
    *,
    candidates: Sequence[ResearchCandidate],
    text: str,
    task_type: Optional[str],
) -> tuple[bool, Optional[str]]:
    has_url = any(c.url for c in candidates)
    has_attachment = any(c.attachment_id for c in candidates)
    has_user_message = any(c.source_type == SOURCE_TYPE_USER_MESSAGE for c in candidates)

    if not candidates:
        return True, "메시지 본문도 첨부도 없어 수집된 자료가 없습니다."

    if not has_user_message and not has_url and not has_attachment:
        return True, "참고 링크와 첨부 파일이 모두 비어 있습니다."

    if has_user_message and not has_url and not has_attachment:
        word_count = len(text.split())
        if word_count < 6 or len(text) < 25:
            return True, "사용자 메시지만 있고 너무 짧아 deliberation 단서가 부족합니다."

    if task_type and task_type in _REQUIRED_SOURCE_TYPES_BY_TASK_TYPE:
        required = _REQUIRED_SOURCE_TYPES_BY_TASK_TYPE[task_type]
        collected = {c.source_type for c in candidates}
        if SOURCE_TYPE_IMAGE_REFERENCE in required and SOURCE_TYPE_DESIGN_REFERENCE in required:
            if not (collected & {SOURCE_TYPE_IMAGE_REFERENCE, SOURCE_TYPE_DESIGN_REFERENCE}):
                return True, f"task_type `{task_type}` 은 시각 reference(이미지 또는 디자인 링크)가 1개 이상 필요합니다."
        else:
            missing = [st for st in required if st not in collected]
            if missing:
                return True, (
                    f"task_type `{task_type}` 에 필요한 자료가 빠져 있습니다: "
                    + ", ".join(missing)
                )

    return False, None


# ---------------------------------------------------------------------------
# Auto-collector wiring + intake body formatters
# ---------------------------------------------------------------------------


def _maybe_run_auto_collect(
    *,
    message_text: str,
    suggested_task_type: Optional[str],
    auto_collect: bool,
    user_links: Sequence[str],
    user_attachments: Sequence[Any],
    role_for_research: str,
    session_id: Optional[str],
    collector_config: Optional[Any],
    collector: Optional[Any],
):
    """Run the autonomous collector and return its outcome (or None).

    Returns ``None`` when:
    - ``auto_collect`` is False, or
    - the message text is too short / blank to query usefully, or
    - importing the collector module fails (defensive).

    Otherwise returns a ``CollectionOutcome``. The caller decides how to
    splice it into the response body.
    """

    if not auto_collect:
        return None
    if not (message_text or "").strip():
        return None
    # Bot-echo / command-only guard — the gateway's own template lines
    # ("좋습니다. 이대로 작업을 등록할게요…" / "자료가 부족합니다…")
    # and bare confirm phrases ("새 작업으로 진행" / "이대로 진행")
    # must never be queried as fresh research material. Without this
    # guard the live MVP loop fires: user pastes the bot's own line
    # back, gateway auto-collects 11 sources, gateway then asks for
    # confirmation, user replies with another command-only phrase,
    # repeat. See ``routing.is_non_actionable_prompt`` for the
    # canonical predicate.
    try:
        from ...agents.routing import is_non_actionable_prompt as _is_blocked
    except Exception:  # noqa: BLE001 - never block conversation on import wiring
        _is_blocked = None
    if _is_blocked is not None and _is_blocked(message_text):
        return None
    try:
        from ...agents.research.collector import (
            CollectorConfig as _CollectorConfig,
            auto_collect_or_request_more_input,
        )
    except Exception:  # noqa: BLE001 - never block conversation on collector wiring
        return None

    cfg = collector_config
    if cfg is None:
        try:
            cfg = _CollectorConfig.from_env()
        except Exception:  # noqa: BLE001
            return None

    try:
        return auto_collect_or_request_more_input(
            role=role_for_research,
            prompt=message_text,
            task_type=suggested_task_type,
            user_links=user_links,
            user_attachments=user_attachments,
            session_id=session_id,
            config=cfg,
            collector=collector,
        )
    except Exception:  # noqa: BLE001
        return None


def _format_intake_with_collection(
    *,
    message_text: str,
    suggested_task_type: Optional[str],
    write_likely: bool,
    collection: Any,
) -> str:
    """Unified intake response when the auto-collector ran.

    Output structure (matches the team-lead voice spec):

    1. Greeting that names what we're doing.
    2. Understanding paragraph echoing a short topic + classification.
    3. Action paragraph describing what just happened or what's next.
    4. (auto_collected / user_provided only) compact meta tail.
    5. Confirmation prompt — except in NEEDS_USER_INPUT where we wait
       for the user's reply instead of asking them to confirm.
    """

    # response_formatters owns _summarize_topic / _pretty_task_type per
    # audit §2 — lazy-import to dodge the step-7 → step-8 ordering.
    from ._legacy import _pretty_task_type, _summarize_topic

    mode = getattr(collection, "mode", None)
    mode_value = getattr(mode, "value", str(mode))
    topic = _summarize_topic(message_text)

    paragraphs: list[str] = []

    # 1. greeting
    if mode_value == "auto_collected":
        paragraphs.append("좋아요. 먼저 1차 자료를 모아볼게요.")
    elif mode_value == "user_provided":
        paragraphs.append("받았어요. 보내주신 자료를 1순위로 두고 시작할게요.")
    elif mode_value == "needs_user_input":
        paragraphs.append("받았어요. 다만 더 정확하게 도와드리려면 자료가 조금 더 필요해요.")
    else:
        paragraphs.append("작업 내용을 받았어요.")

    # 2. understanding
    understand = [f"이번 요청은 “{topic}”으로 이해했어요."]
    if write_likely:
        understand.append(
            "코드나 문서 쓰기가 동반되는 작업으로 보여서, 진행 전에 한 번 확인할게요."
        )
    elif suggested_task_type:
        understand.append(
            f"분석·검토 위주의 {_pretty_task_type(suggested_task_type)} 작업으로 이해하고 있습니다."
        )
    paragraphs.append("\n".join(understand))

    # 3. action — depends on mode
    count = getattr(collection, "auto_collected_count", 0) or 0
    if mode_value == "auto_collected":
        paragraphs.append(
            f"방금 {count}개의 참고 자료 후보를 수집했어요.\n"
            "이 자료들은 운영-리서치에 정리해두고, 이어서 각 역할이 자기 관점으로 검토하게 할게요."
        )
    elif mode_value == "user_provided":
        paragraphs.append(
            "보내주신 자료로 바로 검토를 시작하고, 정리된 결과는 운영-리서치에 함께 남길게요."
        )
    elif mode_value == "needs_user_input":
        prompt = getattr(collection, "user_prompt", None) or (
            "관련 자료를 한두 개 붙여 주시면 더 정확하게 도와드릴 수 있어요."
        )
        paragraphs.append(
            "자동 수집이 비어 있어서, 자료를 한 번 같이 보고 가는 게 좋겠어요.\n"
            f"{prompt}"
        )

    # 4. meta tail (auto_collected / user_provided only)
    if mode_value in ("auto_collected", "user_provided"):
        paragraphs.append(_format_collection_meta_block(collection))

    # 5. confirm — skip when we're waiting for more user input
    if mode_value != "needs_user_input":
        paragraphs.append(
            "맞으면 `이대로 진행`이라고 답해 주세요. 빠진 부분이 있으면 추가로 알려주셔도 좋아요."
        )

    return "\n\n".join(paragraphs)


def _format_coding_bootstrap_body(
    *,
    message_text: str,
    bootstrap: Any,
    suggested_task_type: Optional[str],
) -> str:
    """P0-J (#145) — replace 'NEEDS_USER_INPUT' surface with bootstrap ack.

    When the gateway has repo + stack + write intent, the autonomous
    collector's "자료 부족" follow-up is wrong: the *anchor* is the
    repo itself. This body explains what the gateway will do next
    (seed docs + coding handoff) so the user knows we're proceeding,
    not stalling.
    """

    from ._legacy import _pretty_task_type, _summarize_topic

    topic = _summarize_topic(message_text)
    stacks = ", ".join(getattr(bootstrap, "stacks_mentioned", ()) or ())
    seeded = ", ".join(getattr(bootstrap, "seeded_docs", ()) or ())
    task_label = (
        _pretty_task_type(suggested_task_type) if suggested_task_type else None
    )
    paragraphs: list[str] = [
        "🚀 coding bootstrap 활성 — repo target + stack mention + write intent 조합으로 "
        "추가 자료 요청 없이 coding handoff 로 진행합니다.",
        f"이번 요청은 “{topic}” 으로 이해했고,"
        + (f" `{task_label}` 작업으로 분류했어요." if task_label else ""),
    ]
    if stacks:
        paragraphs.append(f"📚 감지된 스택: {stacks}")
    if seeded:
        paragraphs.append(f"📖 official docs 자동 seed: {seeded}")
    paragraphs.append(
        "코드 컨텍스트는 repo target 으로부터 부트스트랩될 예정입니다. "
        "다른 자료가 필요해지면 그때 다시 알려주세요."
    )
    return "\n\n".join(paragraphs)


def _format_collection_announcement(collection: Any) -> str:
    """Conversational paragraph(s) added when auto-collection ran.

    Tone follows the team-lead voice: 1) what we just did, 2) what's
    next. Internal jargon (collector / query / forum / deliberation) is
    rephrased — collector → 수집 방식, forum → 운영-리서치, deliberation →
    역할별 검토.

    Three modes:
    - AUTO_COLLECTED → "방금 N개의 참고 자료 후보를 수집했어요. ..." + meta
    - USER_PROVIDED → "보내주신 자료를 1순위로 두고 검토할게요." + meta
    - NEEDS_USER_INPUT → 사용자에게 자료 요청 (collector가 빈 결과)
    """

    mode = getattr(collection, "mode", None)
    mode_value = getattr(mode, "value", str(mode))

    if mode_value == "auto_collected":
        count = getattr(collection, "auto_collected_count", 0) or 0
        body = (
            f"먼저 1차 자료를 모아 봤어요. 방금 {count}개의 참고 자료 후보를 찾았습니다.\n"
            "이 자료들은 운영-리서치에 정리해두고, 이어서 각 역할이 자기 관점으로 검토하게 할게요."
        )
        return body + "\n\n" + _format_collection_meta_block(collection)

    if mode_value == "user_provided":
        body = (
            "사용자 제공 자료를 1순위로 두고 검토를 시작할게요.\n"
            "정리한 결과는 운영-리서치에 함께 남길 예정이에요."
        )
        return body + "\n\n" + _format_collection_meta_block(collection)

    if mode_value == "needs_user_input":
        prompt = getattr(collection, "user_prompt", None) or (
            "관련 자료를 한두 개 붙여 주시면 더 정확하게 도와드릴 수 있어요."
        )
        return (
            "자동 수집이 비어 있어, 자료를 한 번 같이 보고 가는 게 좋겠어요.\n"
            f"{prompt}"
        )

    return ""


def _format_collection_meta_block(collection: Any) -> str:
    """Compact key-value tail used under the collection announcement.

    Format:
        수집 정보:
        - 수집 방식: 기본 검색(mock)
        - 수집 자료: N건
        - 다음 단계: 역할별 검토
    """

    from ._legacy import _pretty_provider

    count = getattr(collection, "auto_collected_count", 0) or 0
    name = getattr(collection, "collector_name", "?")
    return (
        "수집 정보:\n"
        f"- 수집 방식: {_pretty_provider(name)}\n"
        f"- 수집 자료: {count}건\n"
        "- 다음 단계: 역할별 검토"
    )


__all__ = (
    # constants
    "SOURCE_TYPE_USER_MESSAGE",
    "SOURCE_TYPE_URL",
    "SOURCE_TYPE_WEB_RESULT",
    "SOURCE_TYPE_IMAGE_REFERENCE",
    "SOURCE_TYPE_FILE_ATTACHMENT",
    "SOURCE_TYPE_GITHUB_ISSUE",
    "SOURCE_TYPE_GITHUB_PR",
    "SOURCE_TYPE_CODE_CONTEXT",
    "SOURCE_TYPE_OFFICIAL_DOCS",
    "SOURCE_TYPE_COMMUNITY_SIGNAL",
    "SOURCE_TYPE_DESIGN_REFERENCE",
    "ALL_SOURCE_TYPES",
    "IMAGE_EXTENSIONS",
    "ROLE_RESEARCH_PROFILES",
    "_REQUIRED_SOURCE_TYPES_BY_TASK_TYPE",
    # dataclasses
    "ResearchCandidate",
    "ResearchCollectionResult",
    # public funcs
    "classify_attachment",
    "classify_url",
    "collect_research_candidates_from_message",
    "suggest_role_research_assignments",
    "format_insufficient_research_prompt",
    "build_research_pack_from_candidates",
    # private formatters / wiring used by response_formatters + _legacy
    "_maybe_run_auto_collect",
    "_format_intake_with_collection",
    "_format_coding_bootstrap_body",
    "_format_collection_announcement",
    "_format_collection_meta_block",
    "_evaluate_research_sufficiency",
)
