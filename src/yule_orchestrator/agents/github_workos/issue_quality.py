"""Issue auto-create quality layer — Korean title + deterministic labels + template hierarchy.

배경
====
초기 :mod:`issue_auto_create` 의 fallback 은 "title = request_summary 첫 줄
truncated, body = 4 섹션 minimal, labels = caller 가 준 extra 만" 이었다.
라이브 스모크에서 생성된 issue #1 이 그 약점을 그대로 노출:

* title = raw intake prompt 잘린 형태
* labels = 빈 배열
* body = `no_repo_template` audit 만 있는 짧은 fallback

이 모듈은 사용자가 명시한 §A~§F 를 코드로 박는다:

* :func:`synthesize_korean_title` — intent 분류 기반 한국어 명확 제목
  (예: ``[Feat] 네이버 검색형 풀스택 MVP 구축 (인증/검색/블로그/메일)``)
* :func:`derive_default_labels` — deterministic label mapping
  (full-stack/feature → ``✨ Feature``, docs → ``📃 Docs``, …)
* :func:`build_quality_default_body` — 운영 규칙에 맞는 default body
  (목표 / 범위 / 작업 항목 / 검증 기준 / engineering-agent audit)
* :func:`resolve_template_source` — repo template > loader > Obsidian
  template > Yule default 우선순위 결정. ``template_source`` audit token.

모든 함수는 **deterministic** — 같은 input → 같은 output. LLM 호출 없음.
같은 prompt 가 다시 들어와도 같은 title / labels / body 가 나오므로 회귀
가능.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Intent vocabulary
# ---------------------------------------------------------------------------


INTENT_FULL_STACK_MVP: str = "full_stack_mvp"
INTENT_FEATURE: str = "feature"
INTENT_DOCS: str = "docs"
INTENT_TEST: str = "test"
INTENT_REFACTOR: str = "refactor"
INTENT_BUGFIX: str = "bugfix"
INTENT_CHORE: str = "chore"


# Korean-aware keyword detection. The order matters — first match wins
# (full-stack before generic feature, etc.).
_INTENT_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        INTENT_FULL_STACK_MVP,
        (
            "풀스택", "full-stack", "fullstack", "full stack",
            "mvp", "monorepo 풀스택", "검색 풀스택",
            "단일 repo 풀스택", "full_stack_single_repo",
        ),
    ),
    (
        INTENT_BUGFIX,
        ("버그", "bug fix", "bugfix", "고쳐줘", "회귀", "regression", "수정해줘"),
    ),
    (
        INTENT_REFACTOR,
        ("리팩터", "리팩토링", "refactor", "재구조화", "rename"),
    ),
    (
        INTENT_DOCS,
        ("문서", "docs", "readme", "튜토리얼", "guide"),
    ),
    (
        INTENT_TEST,
        ("테스트만", "테스트 추가", "test only", "회귀 테스트", "unit test"),
    ),
    (
        INTENT_FEATURE,
        ("구현", "기능 추가", "implement", "build the", "add feature", "신규"),
    ),
    (
        INTENT_CHORE,
        ("dependency", "deps", "chore", "config", "wiring"),
    ),
)


# Scope tokens 감지 — full-stack 케이스에서 어떤 sub-범위인지 인식해
# 한국어 title 에 `(인증/검색/블로그/메일)` 같은 sub-scope 라벨을 붙인다.
_SCOPE_HINTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("인증", ("회원가입", "로그인", "로그아웃", "auth", "sign up", "sign-in", "session")),
    ("검색", ("검색", "search", "통합 검색")),
    ("블로그", ("블로그", "blog", "내부 글")),
    ("메일", ("메일", "mailbox", "mail", "inbox", "compose")),
    ("API", ("api ", "rest api", "graphql")),
    ("UI", ("ui", "frontend", "화면", "레이아웃")),
    ("DB", ("db ", "schema", "스키마", "데이터베이스")),
    ("docker", ("docker compose", "docker-compose", "compose")),
)


# ---------------------------------------------------------------------------
# Intent + scope detection
# ---------------------------------------------------------------------------


def detect_intent(request_text: str) -> str:
    """가장 적합한 intent 토큰 반환. 매칭 없으면 ``INTENT_FEATURE``.

    full-stack 매칭이 있어도 docs / test 가 함께 매칭되면 도메인 우선순위
    상위 (full-stack) 가 이긴다 — first-match-wins 순서가 보장.
    """

    haystack = (request_text or "").lower()
    for intent, keywords in _INTENT_KEYWORDS:
        for kw in keywords:
            if kw.lower() in haystack:
                return intent
    return INTENT_FEATURE


def detect_scopes(request_text: str, *, max_scopes: int = 4) -> Tuple[str, ...]:
    """request_text 안에서 감지된 sub-scope 토큰들.

    full-stack 같은 광범위한 작업의 title 에 `(인증/검색/블로그/메일)`
    형태로 붙기 위한 데이터. 알 수 없는 텍스트면 빈 튜플.
    """

    if not request_text:
        return ()
    haystack = request_text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for label, keywords in _SCOPE_HINTS:
        for kw in keywords:
            if kw.lower() in haystack and label not in seen:
                found.append(label)
                seen.add(label)
                break
        if len(found) >= max_scopes:
            break
    return tuple(found)


# ---------------------------------------------------------------------------
# Title synthesis
# ---------------------------------------------------------------------------


_INTENT_TITLE_PREFIX: Mapping[str, str] = {
    INTENT_FULL_STACK_MVP: "[Feat]",
    INTENT_FEATURE: "[Feat]",
    INTENT_DOCS: "[Docs]",
    INTENT_TEST: "[Test]",
    INTENT_REFACTOR: "[Refactor]",
    INTENT_BUGFIX: "[Fix]",
    INTENT_CHORE: "[Chore]",
}


_INTENT_TITLE_PHRASE: Mapping[str, str] = {
    INTENT_FULL_STACK_MVP: "풀스택 MVP 구축",
    INTENT_FEATURE: "신규 기능 추가",
    INTENT_DOCS: "문서 보강",
    INTENT_TEST: "회귀 테스트 보강",
    INTENT_REFACTOR: "리팩터링",
    INTENT_BUGFIX: "버그 수정",
    INTENT_CHORE: "운영 작업",
}


_DOMAIN_HINTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("네이버 검색형", ("네이버", "naver")),
    ("Google 검색형", ("google search", "구글 검색")),
    ("docs/runbook", ("runbook",)),
)


_KOREAN_TITLE_MAX: int = 110


def _detect_domain_label(request_text: str) -> Optional[str]:
    haystack = (request_text or "").lower()
    for label, keywords in _DOMAIN_HINTS:
        for kw in keywords:
            if kw.lower() in haystack:
                return label
    return None


def synthesize_korean_title(
    *,
    request_text: str,
    intent: Optional[str] = None,
    scopes: Optional[Sequence[str]] = None,
    repo_slug: Optional[str] = None,
    fallback_summary: str = "",
) -> Tuple[str, str]:
    """한국어 명확 issue 제목 + ``title_strategy`` audit 토큰 반환.

    Returns ``(title, strategy)``. strategy 값은 다음 중 하나:

    * ``intent_template`` — intent + scope 가 정확히 분류돼 deterministic
      template 로 생성된 케이스 (가장 흔한 경로).
    * ``intent_fallback_summary`` — intent 만 잡혔고 scope 가 없어서
      template phrase + (있다면) fallback_summary 한 줄 prefix 로 구성.
    * ``raw_summary_truncated`` — intent 미감지 + summary 도 비어있는 극단
      케이스. caller 에게 follow-up 경고가 필요. 이 경우 strategy 가
      ``raw_summary_truncated`` 로 표기되어 audit 가 가능.
    """

    resolved_intent = (intent or detect_intent(request_text)).strip().lower()
    resolved_scopes = tuple(scopes) if scopes is not None else detect_scopes(request_text)

    prefix = _INTENT_TITLE_PREFIX.get(resolved_intent, "[Feat]")
    phrase = _INTENT_TITLE_PHRASE.get(resolved_intent, "신규 기능 추가")
    domain = _detect_domain_label(request_text)

    if resolved_intent == INTENT_FULL_STACK_MVP:
        domain_part = f"{domain} " if domain else ""
        scope_part = (
            f" ({'/'.join(resolved_scopes)})" if resolved_scopes else ""
        )
        title = f"{prefix} {domain_part}{phrase}{scope_part}".strip()
        return _truncate_title(title), "intent_template"

    if resolved_scopes:
        scope_part = f" ({'/'.join(resolved_scopes)})"
        title = f"{prefix} {phrase}{scope_part}"
        return _truncate_title(title), "intent_template"

    summary = (fallback_summary or "").strip()
    if summary:
        # 첫 줄을 prefix 와 결합. 너무 길면 truncate.
        first_line = summary.splitlines()[0].strip()
        if first_line:
            title = f"{prefix} {phrase} — {first_line}"
            return _truncate_title(title), "intent_fallback_summary"

    # 마지막 fallback — request_text 도 비어있는 극단 케이스.
    title = f"{prefix} {phrase}"
    return _truncate_title(title), "raw_summary_truncated"


def _truncate_title(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= _KOREAN_TITLE_MAX:
        return text
    # Soft truncate at last space within budget.
    cut = text[:_KOREAN_TITLE_MAX]
    sep = cut.rfind(" ")
    if sep > 60:
        return cut[:sep].rstrip(",.;:") + "…"
    return cut.rstrip(",.;:") + "…"


# ---------------------------------------------------------------------------
# Label derivation
# ---------------------------------------------------------------------------


LABEL_FEATURE: str = "✨ Feature"
LABEL_DOCS: str = "📃 Docs"
LABEL_TEST: str = "✅ Test"
LABEL_REFACTOR: str = "🔨 Refactor"
LABEL_BUG: str = "🐞 Bug"
LABEL_CHORE: str = "🧹 Chore"
LABEL_FULL_STACK: str = "🌐 Full-stack"
LABEL_AUTO_CREATED: str = "🤖 engineering-agent"


LABEL_SOURCE_TEMPLATE: str = "template"
LABEL_SOURCE_CONTRACT: str = "contract"
LABEL_SOURCE_YULE_FALLBACK: str = "yule_fallback"
LABEL_SOURCE_OPERATOR_EXTRA: str = "operator_extra"
LABEL_SOURCE_NONE: str = "none"


_INTENT_TO_LABELS: Mapping[str, Tuple[str, ...]] = {
    INTENT_FULL_STACK_MVP: (LABEL_FULL_STACK, LABEL_FEATURE),
    INTENT_FEATURE: (LABEL_FEATURE,),
    INTENT_DOCS: (LABEL_DOCS,),
    INTENT_TEST: (LABEL_TEST,),
    INTENT_REFACTOR: (LABEL_REFACTOR,),
    INTENT_BUGFIX: (LABEL_BUG,),
    INTENT_CHORE: (LABEL_CHORE,),
}


@dataclass(frozen=True)
class LabelResolution:
    """:func:`derive_default_labels` 결과 + 어디서 왔는지 audit 토큰.

    ``primary_source`` 는 *labels 의 핵심* 이 어디서 왔는지 한 줄로
    표현 — operator 가 카드 / 노트에서 즉시 확인 가능.
    """

    labels: Tuple[str, ...]
    primary_source: str
    sources_per_label: Mapping[str, str] = field(default_factory=dict)


def derive_default_labels(
    *,
    request_text: str,
    template_labels: Sequence[str] = (),
    contract_label_hints: Sequence[str] = (),
    extra_labels: Sequence[str] = (),
    intent: Optional[str] = None,
) -> LabelResolution:
    """우선순위 합집합으로 labels 결정 + 출처 audit.

    우선순위 (사용자 §B 그대로):
      1. ``template_labels`` (target repo issue template frontmatter)
      2. ``contract_label_hints`` (repo contract / governance mapping)
      3. ``extra_labels`` (caller / operator 추가)
      4. intent → Yule fallback mapping (template 없을 때만)

    각 label 의 source 도 stamp — operator 가 "왜 이 label 이 들어왔는지"
    한 눈에 볼 수 있다.
    """

    out: list[str] = []
    sources: dict[str, str] = {}

    def _add(value: Any, source: str) -> None:
        text = str(value or "").strip()
        if not text or text in sources:
            return
        out.append(text)
        sources[text] = source

    for label in template_labels or ():
        _add(label, LABEL_SOURCE_TEMPLATE)
    for label in contract_label_hints or ():
        _add(label, LABEL_SOURCE_CONTRACT)
    for label in extra_labels or ():
        _add(label, LABEL_SOURCE_OPERATOR_EXTRA)

    # Yule fallback — template 도 contract 도 라벨을 주지 않았을 때만
    # intent 기반 추천을 추가. operator 가 명시한 label 만 있으면
    # primary_source 는 그대로 'operator_extra'.
    intent_fallback_added = False
    if not template_labels and not contract_label_hints:
        resolved_intent = (intent or detect_intent(request_text)).strip().lower()
        for label in _INTENT_TO_LABELS.get(resolved_intent, ()):
            _add(label, LABEL_SOURCE_YULE_FALLBACK)
            intent_fallback_added = True

    # Auto-created marker — 항상 추가 (audit-friendly + operator triage).
    _add(LABEL_AUTO_CREATED, LABEL_SOURCE_YULE_FALLBACK)

    if template_labels:
        primary = LABEL_SOURCE_TEMPLATE
    elif contract_label_hints:
        primary = LABEL_SOURCE_CONTRACT
    elif extra_labels and not intent_fallback_added:
        primary = LABEL_SOURCE_OPERATOR_EXTRA
    elif intent_fallback_added:
        primary = LABEL_SOURCE_YULE_FALLBACK
    else:
        primary = LABEL_SOURCE_NONE

    return LabelResolution(
        labels=tuple(out), primary_source=primary, sources_per_label=sources
    )


# ---------------------------------------------------------------------------
# Body generation — higher-quality Yule default
# ---------------------------------------------------------------------------


TEMPLATE_SOURCE_REPO: str = "repo_contract"
TEMPLATE_SOURCE_LOADER: str = "external_loader"
TEMPLATE_SOURCE_OBSIDIAN: str = "obsidian_fallback"
TEMPLATE_SOURCE_YULE_DEFAULT: str = "yule_default"


def build_quality_default_body(
    *,
    request_summary: str,
    intent: str,
    scopes: Sequence[str] = (),
    repo_slug: Optional[str] = None,
    session_id: Optional[str] = None,
    template_source: str = TEMPLATE_SOURCE_YULE_DEFAULT,
    audit_reason: str = "yule_default_template",
    label_source: str = LABEL_SOURCE_YULE_FALLBACK,
    title_strategy: str = "intent_template",
) -> str:
    """Higher-quality fallback body 생성.

    이전 :func:`build_default_issue_body` 의 4 섹션 minimum 대신, 운영 규칙
    에 맞는 6 섹션 표준 (목표 / 범위 / 작업 항목 / 검증 기준 / 운영 규칙 /
    audit) + intent 별 sub-bullets. 같은 input 이면 같은 body — deterministic.

    body 끝에는 ``engineering-agent audit`` 섹션이 *반드시* 들어가
    ``template_source`` / ``audit_reason`` / ``label_source`` /
    ``title_strategy`` 가 한 곳에 stamp. operator 가 issue 화면에서 즉시
    확인 가능.
    """

    summary = (request_summary or "— 요약 없음 —").strip()
    scope_line = ", ".join(scopes) if scopes else "(자동 감지 안됨)"

    work_items = _intent_work_items(intent, scopes)
    verification = _intent_verification(intent)

    lines: list[str] = []
    lines.append("## 목표")
    lines.append("")
    lines.append(f"> {summary}")
    lines.append("")
    lines.append("## 범위")
    lines.append("")
    lines.append(f"- 자동 감지된 sub-scope: **{scope_line}**")
    if repo_slug:
        lines.append(f"- 대상 repo: `{repo_slug}`")
    lines.append("")
    lines.append("## 작업 항목 (placeholder)")
    lines.append("")
    for item in work_items:
        lines.append(f"- [ ] {item}")
    lines.append("")
    lines.append("## 검증 기준")
    lines.append("")
    for item in verification:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 운영 규칙")
    lines.append("")
    lines.append("- 자동 머지 절대 금지 — 모든 PR 은 draft 까지만 진행, 머지는 사람 승인")
    lines.append("- 라이브 secret / 외부 발송 / 운영 DB 접근이 필요하면 별도 operator action 으로 escalate")
    lines.append("- 본 issue 가 closure 되기 전 troubleshooting / decision / work-report 노트를 vault 에 mirror")
    lines.append("")
    lines.append("## engineering-agent audit")
    lines.append("")
    lines.append(f"- audit_reason: `{audit_reason}`")
    lines.append(f"- template_source: `{template_source}`")
    lines.append(f"- label_source: `{label_source}`")
    lines.append(f"- title_strategy: `{title_strategy}`")
    if session_id:
        lines.append(f"- engineering-agent session: `{session_id}`")
    lines.append("- 자동 생성된 issue 이며, 사람이 본 issue 의 작업 항목 / 검증 기준을 보강할 수 있습니다.")
    return "\n".join(lines).rstrip() + "\n"


def _intent_work_items(intent: str, scopes: Sequence[str]) -> Tuple[str, ...]:
    if intent == INTENT_FULL_STACK_MVP:
        base: list[str] = ["repo contract / branch / PR template 점검 후 base branch 결정"]
        if "인증" in scopes:
            base.append("회원가입 / 로그인 / 로그아웃 / 세션 / 보호된 라우트")
        if "검색" in scopes:
            base.append("검색 홈 + 결과 탭 (통합/블로그/메일) + 내부 DB 인덱싱")
        if "블로그" in scopes:
            base.append("블로그 글 목록 / 상세 / 작성")
        if "메일" in scopes:
            base.append("내부 mailbox inbox / sent / detail / compose")
        if "docker" in scopes:
            base.append("docker compose 로 web/api/db 동시 기동 + /health 엔드포인트")
        base.append("이번 PR 의 작업 분해 (semantic CRUD-like slices)")
        return tuple(base)
    if intent == INTENT_FEATURE:
        return (
            "요구사항 명세 / 수용 기준 (acceptance criteria)",
            "구현 분해 (UI / API / DB / docs / test)",
            "회귀 테스트 추가",
        )
    if intent == INTENT_DOCS:
        return (
            "변경 대상 문서 / 섹션 식별",
            "최소 1 개 cross-link 갱신",
            "운영 규칙 (5 섹션 구조) 적용 확인",
        )
    if intent == INTENT_TEST:
        return (
            "회귀 케이스 / 시나리오 정의",
            "fixture / mock 데이터 준비",
            "PR 본문에 회귀 테스트 매핑 라인 추가",
        )
    if intent == INTENT_REFACTOR:
        return (
            "범위 / 의도 / 비변경 (behavior preserving) 명시",
            "before/after 책임 비교 + 회귀 테스트",
            "관련 docs / CODE_LAYOUT 갱신",
        )
    if intent == INTENT_BUGFIX:
        return (
            "재현 케이스 확보",
            "fix + 회귀 테스트 (signature 차단)",
            "관련 troubleshooting note 1 건",
        )
    if intent == INTENT_CHORE:
        return (
            "변경 동기 + 영향도",
            "회귀 가능성 점검",
            "관련 docs / config 갱신",
        )
    return (
        "작업 분해 (placeholder)",
        "회귀 테스트",
        "관련 노트 mirror",
    )


def _intent_verification(intent: str) -> Tuple[str, ...]:
    if intent in (INTENT_FULL_STACK_MVP, INTENT_FEATURE):
        return (
            "기존 회귀 통과 + 새 회귀 추가",
            "수동 smoke (필요한 경우)",
            "approval reply 흐름이 끝까지 working 함",
        )
    if intent == INTENT_DOCS:
        return ("link check 통과", "5 섹션 구조 준수", "cross-link 정확")
    if intent == INTENT_TEST:
        return ("새 회귀가 PASS", "기존 회귀 회귀 없음")
    if intent == INTENT_REFACTOR:
        return ("기존 회귀 통과", "behavior change 없음 확인", "관련 docs 갱신")
    if intent == INTENT_BUGFIX:
        return ("재현 케이스가 새 회귀에서 차단됨", "기존 회귀 통과")
    return ("기존 회귀 통과",)


# ---------------------------------------------------------------------------
# Template source resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateSourceDecision:
    """Template hierarchy 의 어느 단계가 선택됐는지 audit.

    ``source`` 가 :data:`TEMPLATE_SOURCE_*` 중 하나. ``template_path``
    은 source 가 repo / loader / Obsidian 일 때만 채워짐.
    """

    source: str
    template_path: Optional[str] = None
    template_text: Optional[str] = None
    notes: Tuple[str, ...] = ()


def resolve_template_source(
    *,
    repo_contract_templates: Sequence[str] = (),
    template_loader: Optional[Any] = None,
    obsidian_template_loader: Optional[Any] = None,
) -> TemplateSourceDecision:
    """우선순위 결정.

    우선순위 (사용자 §C):
      1. repo_contract_templates 가 비어있지 않으면 ``repo_contract``
         (실제 template 텍스트는 caller 가 template_loader 로 읽음)
      2. template_loader 가 callable 이면 *load 가능 여부만 audit* —
         실제 텍스트는 caller 가 따로 호출. 본 함수는 source 만 결정.
      3. obsidian_template_loader 가 텍스트를 주면 ``obsidian_fallback``
      4. 그 외 — ``yule_default``

    Obsidian fallback 은 호출자가 vault 의 `80-templates/github-issue/*.md`
    같은 경로에서 텍스트를 끌어오는 callable. 없으면 None → Yule default.
    """

    notes: list[str] = []
    if repo_contract_templates:
        return TemplateSourceDecision(
            source=TEMPLATE_SOURCE_REPO,
            notes=("repo contract issue_templates 가 발견됨",),
        )
    if template_loader is not None:
        # caller 가 외부 loader 를 wire — 실제 텍스트는 caller 가 검사.
        return TemplateSourceDecision(
            source=TEMPLATE_SOURCE_LOADER,
            notes=("external template_loader 가 주입됨",),
        )
    if obsidian_template_loader is not None:
        try:
            text = obsidian_template_loader()
        except Exception:  # noqa: BLE001
            text = None
        if isinstance(text, str) and text.strip():
            return TemplateSourceDecision(
                source=TEMPLATE_SOURCE_OBSIDIAN,
                template_text=text,
                notes=("Obsidian vault 의 fallback template 적용",),
            )
        notes.append("Obsidian loader 가 빈 텍스트 반환 — vault 에 GitHub issue template 없음")
    notes.append("Yule deterministic default template 적용")
    return TemplateSourceDecision(
        source=TEMPLATE_SOURCE_YULE_DEFAULT, notes=tuple(notes)
    )


__all__ = (
    "INTENT_BUGFIX",
    "INTENT_CHORE",
    "INTENT_DOCS",
    "INTENT_FEATURE",
    "INTENT_FULL_STACK_MVP",
    "INTENT_REFACTOR",
    "INTENT_TEST",
    "LABEL_AUTO_CREATED",
    "LABEL_BUG",
    "LABEL_CHORE",
    "LABEL_DOCS",
    "LABEL_FEATURE",
    "LABEL_FULL_STACK",
    "LABEL_REFACTOR",
    "LABEL_SOURCE_CONTRACT",
    "LABEL_SOURCE_NONE",
    "LABEL_SOURCE_OPERATOR_EXTRA",
    "LABEL_SOURCE_TEMPLATE",
    "LABEL_SOURCE_YULE_FALLBACK",
    "LABEL_TEST",
    "LabelResolution",
    "TEMPLATE_SOURCE_LOADER",
    "TEMPLATE_SOURCE_OBSIDIAN",
    "TEMPLATE_SOURCE_REPO",
    "TEMPLATE_SOURCE_YULE_DEFAULT",
    "TemplateSourceDecision",
    "build_quality_default_body",
    "derive_default_labels",
    "detect_intent",
    "detect_scopes",
    "resolve_template_source",
    "synthesize_korean_title",
)
