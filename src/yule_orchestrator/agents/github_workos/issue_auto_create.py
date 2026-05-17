"""Issue auto-create — target repo 의 ISSUE_TEMPLATE 를 읽어 body 를 채움.

배경
====
engineering-agent 가 coding request 를 받았는데 issue 번호가 안 들어왔을
때, 그냥 PR 로 직진하면 안 된다 — target repo 의 운영 규칙 (label,
template, sub-issue 구조) 을 무시하게 된다. 본 모듈은:

1. :class:`RepoContract.issue_templates` 가 찾은 후보 파일을 읽어
2. 가장 의미가 가까운 template 1 건을 골라 (다중일 때 confidence 계산)
3. request_summary 로 본문을 채운
4. :class:`IssueAutoCreatePlan` 를 만든다.

본문 채움 규칙
-----------
- YAML frontmatter (`---\n…\n---`) 가 있으면 `title:` 와 `labels:` 를
  추출해 출력 plan 에 반영.
- `## ` 헤더 아래의 placeholder (`<!-- ... -->`, `> 추가하려는 기능에…`)
  는 그대로 두되, 그 다음 비어있는 본문 자리에 request_summary 를 삽입.
- 알아서 꾸며내지 않는다 — placeholder/HTML 주석은 보존.
- 모든 변형은 **deterministic** — 같은 input 이면 같은 output.

confidence
----------
- template 가 1 개 → ``confidence='high'`` (해당 template 사용).
- template 가 여러 개일 때 request 와의 키워드 매칭 점수를 계산:
  - 매칭 점수 ≥ 2 → ``'high'``, 1 → ``'medium'``, 0 → ``'low'``.
- 모두 0 점이면 :func:`should_request_decision` 이 True 를 반환해 호출자가
  `DECISION_REQUIRED` operator action 카드를 게시.

fallback (template 없음)
----------------------
- repo contract 가 template 를 못 찾으면 :func:`build_default_issue_body`
  가 호출돼 안전한 기본 body 를 만든다.
- 단 ``audit_reason`` 에 ``no_repo_template`` 을 새겨 fake success 금지.

본 모듈은 pure — GitHub API 호출 없음. body 만 만들고, 실제 호출은
:class:`GithubWriter.create_issue` 가 담당.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from ..git.repo_contract import RepoContract


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


CONFIDENCE_HIGH: str = "high"
CONFIDENCE_MEDIUM: str = "medium"
CONFIDENCE_LOW: str = "low"


AUDIT_TEMPLATE_USED: str = "template_used"
AUDIT_TEMPLATE_FALLBACK: str = "no_repo_template"
AUDIT_TEMPLATE_AMBIGUOUS: str = "ambiguous_template"
AUDIT_EXISTING_ISSUE_REUSED: str = "existing_issue_reused"


# ---------------------------------------------------------------------------
# Template parsing
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)$",
    re.DOTALL,
)

# `key: value` 또는 `key:\n  - value` 두 형태를 받는 단순 파서. 본 모듈은
# PyYAML 의존을 피하기 위해 GitHub issue template 에서 흔히 쓰는 6개 키만
# 인식한다.
_FM_KEY_RE = re.compile(r"^(name|about|title|labels|assignees|description)\s*:\s*(.*)$")


@dataclass(frozen=True)
class IssueTemplate:
    """파싱된 GitHub issue template.

    필드
    ----
    path
        repo-relative path (예: ``.github/ISSUE_TEMPLATE/bug_report.md``).
    name
        frontmatter `name:` — 사람 친화 라벨. 없으면 파일명 stem.
    title_prefix
        frontmatter `title:` — issue 제목에 자동 prefix 됨. 예: ``[Feat]``.
    labels
        frontmatter `labels:` 에 명시된 라벨 시퀀스.
    assignees
        frontmatter `assignees:`.
    body
        frontmatter 를 제거한 markdown 본문 (placeholder 포함).
    """

    path: str
    name: str
    title_prefix: str = ""
    labels: Tuple[str, ...] = ()
    assignees: Tuple[str, ...] = ()
    body: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.body.strip() or self.title_prefix or self.labels)


def parse_issue_template(*, path: str, text: str) -> IssueTemplate:
    """*text* (issue template 파일 내용) → :class:`IssueTemplate`.

    YAML frontmatter 파싱은 보수적 — 본 함수는 PyYAML 을 쓰지 않고
    common case 만 추출한다 (name/title/labels/assignees/description).
    잘못된 frontmatter 는 그냥 body 로 처리.
    """

    name_default = Path(path).stem.replace("-", " ").replace("_", " ").strip() or path
    raw = (text or "").strip()
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return IssueTemplate(
            path=path,
            name=name_default,
            body=raw,
        )
    fm_block = match.group("fm")
    body = match.group("body").lstrip("\n")

    name = name_default
    title_prefix = ""
    labels: list[str] = []
    assignees: list[str] = []

    # 매우 보수적 line 파서. 들여쓰기 list (- item) 도 지원.
    pending_list_key: Optional[str] = None
    for line in fm_block.splitlines():
        stripped = line.rstrip()
        if not stripped:
            pending_list_key = None
            continue
        # 들여쓰기 list item
        if pending_list_key and stripped.lstrip().startswith("- "):
            item = stripped.lstrip()[2:].strip().strip('"').strip("'")
            if not item:
                continue
            if pending_list_key == "labels":
                labels.append(item)
            elif pending_list_key == "assignees":
                assignees.append(item)
            continue
        pending_list_key = None
        m = _FM_KEY_RE.match(stripped)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        value = value.strip('"').strip("'")
        if key == "name" and value:
            name = value
        elif key == "title" and value:
            title_prefix = value
        elif key in ("labels", "assignees"):
            if value:
                # 한 줄 list: ``labels: a, b, c`` 또는 ``labels: ["a", "b"]``
                cleaned = (
                    value.strip("[]")
                    .replace('"', "")
                    .replace("'", "")
                )
                items = [
                    chunk.strip()
                    for chunk in cleaned.split(",")
                    if chunk.strip()
                ]
                if key == "labels":
                    labels.extend(items)
                else:
                    assignees.extend(items)
            else:
                pending_list_key = key

    return IssueTemplate(
        path=path,
        name=name,
        title_prefix=title_prefix,
        labels=tuple(labels),
        assignees=tuple(assignees),
        body=body,
    )


# ---------------------------------------------------------------------------
# Template selection (multi-template repo)
# ---------------------------------------------------------------------------


_KEYWORD_RE = re.compile(r"[A-Za-z가-힣]{2,}")


def _normalize_keywords(text: str) -> set[str]:
    return {token.lower() for token in _KEYWORD_RE.findall(text or "")}


def score_template_against_request(
    *, template: IssueTemplate, request_text: str
) -> int:
    """*request_text* 와 *template* 의 키워드 매칭 점수 (양수).

    매칭 토큰: template.name + title_prefix + 본문의 ``## 헤더`` 들.
    request_text 와의 교집합 token 수가 점수. confidence 분류에 사용.
    """

    haystack_tokens: set[str] = set()
    haystack_tokens.update(_normalize_keywords(template.name))
    haystack_tokens.update(_normalize_keywords(template.title_prefix))
    # ## 헤더 + labels 도 매칭 신호
    for line in (template.body or "").splitlines():
        line = line.strip()
        if line.startswith("#"):
            haystack_tokens.update(_normalize_keywords(line.lstrip("# ").strip()))
    for label in template.labels:
        haystack_tokens.update(_normalize_keywords(label))

    request_tokens = _normalize_keywords(request_text)
    if not haystack_tokens or not request_tokens:
        return 0
    return len(haystack_tokens & request_tokens)


def select_issue_template(
    *,
    templates: Sequence[IssueTemplate],
    request_text: str,
) -> Tuple[Optional[IssueTemplate], int, str]:
    """multi-template repo 에서 가장 적합한 template + score + confidence 반환.

    반환값:
      - ``(None, 0, CONFIDENCE_LOW)`` — templates 가 비어있음.
      - ``(template, score, confidence)`` — 선택된 template.
        templates 가 1 개면 무조건 ``CONFIDENCE_HIGH`` (deterministic
        single choice).

    동점일 때는 input 순서 (즉 repo_contract 가 정렬한 alphabetical) 가
    tie-break.
    """

    if not templates:
        return None, 0, CONFIDENCE_LOW
    if len(templates) == 1:
        return templates[0], score_template_against_request(
            template=templates[0], request_text=request_text
        ), CONFIDENCE_HIGH

    scored: list[Tuple[int, int, IssueTemplate]] = []
    for idx, template in enumerate(templates):
        scored.append(
            (
                score_template_against_request(
                    template=template, request_text=request_text
                ),
                idx,
                template,
            )
        )
    scored.sort(key=lambda item: (-item[0], item[1]))
    top_score, _, top_template = scored[0]
    if top_score >= 2:
        confidence = CONFIDENCE_HIGH
    elif top_score == 1:
        confidence = CONFIDENCE_MEDIUM
    else:
        confidence = CONFIDENCE_LOW
    return top_template, top_score, confidence


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssueAutoCreatePlan:
    """Issue auto-create 실행 직전의 결정.

    필드
    ----
    title
        실제 issue 제목 (template prefix + canonical summary 결합).
    body
        rendered markdown body. placeholder 보존.
    labels / assignees
        template 에서 받은 + 호출자 override 합집합.
    template_path
        사용된 template 의 repo-relative path. fallback 시 ``None``.
    confidence
        :data:`CONFIDENCE_HIGH` / ``MEDIUM`` / ``LOW``.
    audit_reason
        :data:`AUDIT_TEMPLATE_USED` / ``AUDIT_TEMPLATE_FALLBACK` /
        ``AUDIT_TEMPLATE_AMBIGUOUS``.
    needs_operator_decision
        confidence 가 LOW 이면 True — 호출자는 DECISION_REQUIRED 카드를
        게시한 뒤 응답을 기다린다.
    """

    title: str
    body: str
    labels: Tuple[str, ...] = ()
    assignees: Tuple[str, ...] = ()
    template_path: Optional[str] = None
    confidence: str = CONFIDENCE_HIGH
    audit_reason: str = AUDIT_TEMPLATE_USED
    needs_operator_decision: bool = False
    template_score: int = 0

    def to_dict(self) -> Mapping[str, object]:
        return {
            "title": self.title,
            "body": self.body,
            "labels": list(self.labels),
            "assignees": list(self.assignees),
            "template_path": self.template_path,
            "confidence": self.confidence,
            "audit_reason": self.audit_reason,
            "needs_operator_decision": self.needs_operator_decision,
            "template_score": self.template_score,
        }


def fill_issue_template(
    *,
    template: IssueTemplate,
    request_summary: str,
    repo_contract: Optional[RepoContract] = None,
    session_id: Optional[str] = None,
    audit_reason: str = AUDIT_TEMPLATE_USED,
    confidence: str = CONFIDENCE_HIGH,
    template_score: int = 0,
    title_override: Optional[str] = None,
    extra_labels: Iterable[str] = (),
) -> IssueAutoCreatePlan:
    """*template* 본문을 *request_summary* 로 채운 plan 반환.

    채움 규칙
    --------
    - **placeholder 보존**: HTML 주석 ``<!-- ... -->`` 및 ``> ...``
      안내문은 그대로.
    - **첫 ``## `` 헤더 직후의 빈 줄** 자리에 request_summary 한 줄을
      `> ` quote 형식으로 prepend. 본 자리는 GitHub issue template 의
      가장 흔한 "추가하려는 기능에 대해 간결하게 설명해주세요" 라인.
    - placeholder 가 없으면 본문 끝에 `## 작업 컨텍스트` 섹션을 append.
    - frontmatter title prefix 가 있으면 결합: ``[Feat] <summary>``.
    """

    summary = (request_summary or "").strip()
    title = (title_override or "").strip() or summary or template.name
    if template.title_prefix:
        prefix = template.title_prefix.strip()
        # GitHub default templates 자주 ``[Feat]`` 같은 prefix 만 두고 빈
        # 자리를 남김. 사용자가 요청 본문에 같은 prefix 를 안 가지고 있다면
        # 결합.
        if prefix and not title.lower().startswith(prefix.lower()):
            title = f"{prefix} {title}".strip()

    body_lines = (template.body or "").splitlines()
    # 첫 ``## `` 헤더 다음 비어있는 줄을 찾아 거기에 summary 삽입.
    inserted = False
    out: list[str] = []
    for idx, line in enumerate(body_lines):
        out.append(line)
        if (
            not inserted
            and summary
            and line.strip().startswith("## ")
        ):
            # 다음 줄이 empty 거나 안내문이면 그 자리에 quote 삽입.
            out.append("")
            out.append(f"> {summary}")
            out.append("")
            inserted = True
    if not inserted and summary:
        out.append("")
        out.append("## 작업 컨텍스트")
        out.append("")
        out.append(f"> {summary}")

    # repo_contract / session reference 가 있으면 audit 라인 추가 (
    # template 의 placeholder 와 충돌하지 않도록 마지막 별도 섹션).
    audit_lines: list[str] = []
    if repo_contract is not None:
        audit_lines.append(
            f"- repo contract: {repo_contract.summary_line()}"
        )
    if session_id:
        audit_lines.append(f"- engineering-agent session: `{session_id}`")
    if audit_reason:
        audit_lines.append(f"- audit_reason: `{audit_reason}`")
    if audit_lines:
        out.append("")
        out.append("## engineering-agent audit")
        out.append("")
        out.extend(audit_lines)

    body = "\n".join(out).rstrip() + "\n"

    labels = tuple(
        dict.fromkeys(
            [*template.labels, *[str(l).strip() for l in extra_labels if str(l).strip()]]
        )
    )
    return IssueAutoCreatePlan(
        title=title,
        body=body,
        labels=labels,
        assignees=template.assignees,
        template_path=template.path,
        confidence=confidence,
        audit_reason=audit_reason,
        needs_operator_decision=(confidence == CONFIDENCE_LOW),
        template_score=template_score,
    )


def build_default_issue_body(
    *,
    request_summary: str,
    repo_contract: Optional[RepoContract] = None,
    session_id: Optional[str] = None,
    title_override: Optional[str] = None,
    extra_labels: Iterable[str] = (),
    obsidian_template_loader: Optional[Any] = None,
) -> IssueAutoCreatePlan:
    """target repo 에 template 가 없을 때의 *고품질* fallback plan.

    이전 회귀: title 이 raw request_summary 첫 줄을 잘랐고 labels 는 caller
    가 준 extra 만 사용해 비어있기 십상이었다. 라이브에서 생성된 issue #1
    이 그 약점을 그대로 노출. 현재는 :mod:`issue_quality` 로 라우팅해:

    * 한국어 명확 title 자동 합성 (intent + scope 기반)
    * deterministic label mapping (full-stack / feature / docs / refactor /
      bug / test / chore + auto-created marker)
    * 운영 규칙에 맞는 6 섹션 body (목표 / 범위 / 작업 항목 / 검증 기준 /
      운영 규칙 / engineering-agent audit)
    * audit 토큰 (template_source / audit_reason / label_source /
      title_strategy) 이 body 끝에 stamp

    Obsidian fallback template loader 가 주입되면 그 텍스트를 그대로 body
    로 사용하고 ``template_source="obsidian_fallback"`` 으로 audit. 없으면
    Yule deterministic default 로 진행하고 그 사실도 audit 한다 (fake
    success 금지).

    ``title_override`` / ``extra_labels`` 는 기존 호출자가 명시적으로 줄
    수 있는 override 슬롯. 우선순위는 quality layer 가 알아서 처리.
    """

    # Lazy import — issue_quality 가 본 모듈에 의존하지 않게.
    from .issue_quality import (
        LABEL_SOURCE_YULE_FALLBACK,
        TEMPLATE_SOURCE_OBSIDIAN,
        TEMPLATE_SOURCE_YULE_DEFAULT,
        build_quality_default_body,
        derive_default_labels,
        detect_intent,
        detect_scopes,
        resolve_template_source,
        synthesize_korean_title,
    )

    summary = (request_summary or "").strip()
    intent = detect_intent(summary)
    scopes = detect_scopes(summary)
    repo_slug = (
        f"{repo_contract.owner}/{repo_contract.repo}"
        if repo_contract is not None
        else None
    )

    # Title — title_override 가 있으면 그것을 그대로 (호출자가 강제), 없으면
    # Korean synthesis.
    if (title_override or "").strip():
        title = (title_override or "").strip()
        title_strategy = "operator_override"
    else:
        title, title_strategy = synthesize_korean_title(
            request_text=summary,
            intent=intent,
            scopes=scopes,
            repo_slug=repo_slug,
            fallback_summary=summary,
        )

    # Template source — Obsidian fallback 있으면 그 텍스트로 body 대체.
    template_decision = resolve_template_source(
        repo_contract_templates=(
            repo_contract.issue_templates if repo_contract is not None else ()
        ),
        obsidian_template_loader=obsidian_template_loader,
    )

    label_resolution = derive_default_labels(
        request_text=summary,
        extra_labels=tuple(extra_labels or ()),
        intent=intent,
    )

    if template_decision.source == TEMPLATE_SOURCE_OBSIDIAN and template_decision.template_text:
        body = template_decision.template_text
        if "engineering-agent audit" not in body:
            body = body.rstrip() + (
                "\n\n## engineering-agent audit\n\n"
                f"- audit_reason: `{AUDIT_TEMPLATE_FALLBACK}`\n"
                f"- template_source: `{template_decision.source}`\n"
                f"- label_source: `{label_resolution.primary_source}`\n"
                f"- title_strategy: `{title_strategy}`\n"
                + (f"- engineering-agent session: `{session_id}`\n" if session_id else "")
            )
        template_source_value = TEMPLATE_SOURCE_OBSIDIAN
    else:
        body = build_quality_default_body(
            request_summary=summary,
            intent=intent,
            scopes=scopes,
            repo_slug=repo_slug,
            session_id=session_id,
            template_source=TEMPLATE_SOURCE_YULE_DEFAULT,
            audit_reason=AUDIT_TEMPLATE_FALLBACK,
            label_source=label_resolution.primary_source,
            title_strategy=title_strategy,
        )
        template_source_value = TEMPLATE_SOURCE_YULE_DEFAULT

    return IssueAutoCreatePlan(
        title=title,
        body=body,
        labels=label_resolution.labels,
        assignees=(),
        template_path=None,
        confidence=CONFIDENCE_HIGH,  # fallback 자체는 deterministic, 사람 입력 불필요
        audit_reason=AUDIT_TEMPLATE_FALLBACK,
        needs_operator_decision=False,
    )


# ---------------------------------------------------------------------------
# Top-level plan helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssueAutoCreateOutcome:
    """:func:`build_issue_auto_create_plan` 결과.

    필드
    ----
    plan
        :class:`IssueAutoCreatePlan` — None 이면 ``existing_issue_number``
        가 있어 새로 생성하지 않는다.
    existing_issue_number
        호출자가 issue 번호를 이미 알고 있으면 그 번호. 이 경우 plan 은
        None 이고 audit_reason 은 ``existing_issue_reused``.
    audit_reason
        :data:`AUDIT_*` 중 하나. plan 이 있어도 confidence 와 무관하게
        호출자가 audit 에 쓰는 라벨.
    candidate_templates
        대상 repo 의 ISSUE_TEMPLATE 후보 목록 (사람이 확인 가능하도록).
    selected_score
        top template 점수. confidence 결정에 사용된 raw 값.
    """

    plan: Optional[IssueAutoCreatePlan]
    existing_issue_number: Optional[int] = None
    audit_reason: str = AUDIT_TEMPLATE_USED
    candidate_templates: Tuple[IssueTemplate, ...] = ()
    selected_score: int = 0


def build_issue_auto_create_plan(
    *,
    repo_contract: RepoContract,
    request_summary: str,
    existing_issue_number: Optional[int] = None,
    template_loader=None,
    session_id: Optional[str] = None,
    title_override: Optional[str] = None,
    extra_labels: Iterable[str] = (),
) -> IssueAutoCreateOutcome:
    """end-to-end plan 생성.

    Steps:
      1. ``existing_issue_number`` 가 있으면 즉시 outcome 반환 — 중복 생성 금지.
      2. repo_contract 의 issue_templates 후보를 ``template_loader`` 로
         읽어 :class:`IssueTemplate` 시퀀스 구성.
         - ``template_loader`` 는 ``(path: str) -> Optional[str]``. 실패 시
           해당 후보를 건너뜀.
      3. :func:`select_issue_template` 으로 best template 선택.
      4. confidence:
         - HIGH/MEDIUM → :func:`fill_issue_template`
         - LOW (다중 template 인데 매칭 없음) → fill_issue_template +
           ``needs_operator_decision=True``
         - 후보 자체가 없음 → :func:`build_default_issue_body` (fallback)
    """

    if existing_issue_number is not None and int(existing_issue_number) > 0:
        return IssueAutoCreateOutcome(
            plan=None,
            existing_issue_number=int(existing_issue_number),
            audit_reason=AUDIT_EXISTING_ISSUE_REUSED,
        )

    candidate_paths = tuple(repo_contract.issue_templates or ())
    candidates: list[IssueTemplate] = []
    if template_loader is not None:
        for path in candidate_paths:
            try:
                text = template_loader(path)
            except Exception:  # noqa: BLE001
                text = None
            if not text:
                continue
            parsed = parse_issue_template(path=path, text=text)
            if parsed.is_empty and not parsed.title_prefix:
                # 빈 template 는 후보에서 제외 — fallback 으로 떨어뜨림
                continue
            candidates.append(parsed)

    if not candidates:
        plan = build_default_issue_body(
            request_summary=request_summary,
            repo_contract=repo_contract,
            session_id=session_id,
            title_override=title_override,
            extra_labels=extra_labels,
        )
        return IssueAutoCreateOutcome(
            plan=plan,
            existing_issue_number=None,
            audit_reason=AUDIT_TEMPLATE_FALLBACK,
            candidate_templates=(),
            selected_score=0,
        )

    selected, score, confidence = select_issue_template(
        templates=candidates, request_text=request_summary
    )
    assert selected is not None  # candidates non-empty guard
    audit_reason = (
        AUDIT_TEMPLATE_AMBIGUOUS if confidence == CONFIDENCE_LOW else AUDIT_TEMPLATE_USED
    )
    plan = fill_issue_template(
        template=selected,
        request_summary=request_summary,
        repo_contract=repo_contract,
        session_id=session_id,
        audit_reason=audit_reason,
        confidence=confidence,
        template_score=score,
        title_override=title_override,
        extra_labels=extra_labels,
    )
    return IssueAutoCreateOutcome(
        plan=plan,
        existing_issue_number=None,
        audit_reason=audit_reason,
        candidate_templates=tuple(candidates),
        selected_score=score,
    )


__all__ = (
    "AUDIT_EXISTING_ISSUE_REUSED",
    "AUDIT_TEMPLATE_AMBIGUOUS",
    "AUDIT_TEMPLATE_FALLBACK",
    "AUDIT_TEMPLATE_USED",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "IssueAutoCreateOutcome",
    "IssueAutoCreatePlan",
    "IssueTemplate",
    "build_default_issue_body",
    "build_issue_auto_create_plan",
    "fill_issue_template",
    "parse_issue_template",
    "score_template_against_request",
    "select_issue_template",
)
