"""Runtime governance hard rails — pure validators / deciders.

본 모듈이 정의하는 정책 (코드로 박혀 있어 docs 와 자동 동기화 검증
가능):

1. Branch — protected branch 직접 작업 금지, 표준 prefix (feat/fix/
   chore/refactor) 권장, issue 번호 anchor 권장.
2. PR — 5 섹션 (목적/범위/리스크/테스트/이슈 linkage) + audit block.
3. Curated note — frontmatter 필수 키 + 본문 필수 섹션 + inbox 직접
   승격 금지 + orphan/broken link 검출.
4. Retrieval eval — entry 스키마 + 최소 50 / 목표 100 + top-5 평가.
5. Post-test hardening — 8 opening criteria.

설계 원칙
--------
- pure: storage I/O / network 호출 없음.
- caller-driven gate: 본 함수가 거부 결정을 내리지 않는다 — 결과 객체
  (allowed + reasons + warnings + suggestions) 를 반환하고, **호출자**
  가 그 결과를 실제 게이트로 사용한다.
- single source of truth: branch_naming / curated_required / eval schema
  / hardening criteria 같은 상수는 모두 본 모듈에서 export. docs 측은
  본 모듈을 cross-link 해야 동기화가 깨지지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Branch policy
# ---------------------------------------------------------------------------


# 표준 prefix — repo contract 가 별도 규칙을 갖지 않을 때의 기본값. 호출자
# 는 repo contract 의 `branch_strategy` 를 우선 적용하고 본 prefix 는
# fallback 으로만 사용.
BRANCH_PREFIXES: Tuple[str, ...] = (
    "feat",
    "fix",
    "chore",
    "refactor",
    "docs",
    "test",
    "perf",
)


# 기존 :func:`agents.github_workos.branching.is_protected_branch` 의
# PROTECTED_BRANCHES 와 동일 — duplicate 회피를 위해 import.
_PROTECTED_BRANCHES: frozenset[str] = frozenset(
    {"main", "master", "develop", "release", "hotfix", "production", "prod"}
)


_BRANCH_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._\-/]*$")


@dataclass(frozen=True)
class BranchPolicyResult:
    """``validate_branch_name`` 결과.

    - ``allowed`` False: protected branch 또는 형식 위반 — 호출자는 push
      거부.
    - ``warnings``: 권장 prefix 가 안 붙었거나 issue 번호 anchor 가
      없는 경우. 호출자는 audit 에 기록 후 진행 가능.
    """

    allowed: bool
    name: str
    reason: str = ""
    warnings: Tuple[str, ...] = field(default_factory=tuple)
    suggested_prefix: Optional[str] = None


def validate_branch_name(
    name: Optional[str],
    *,
    issue_number: Optional[int] = None,
    require_standard_prefix: bool = False,
) -> BranchPolicyResult:
    """*name* 이 운영 정책에 부합하는지 검사.

    rules:
      - None / 빈 문자열 → ``allowed=False``.
      - protected branch (case-insensitive) → ``allowed=False``.
      - 소문자 + 영숫자 + ``.-_/`` 만 허용 — naming 형식 위반은
        ``allowed=False``.
      - ``feat/`` / ``fix/`` / ``chore/`` / ``refactor/`` / ``docs/`` /
        ``test/`` / ``perf/`` 중 하나로 시작하지 않으면 ``warnings`` 에
        suggestion. ``require_standard_prefix=True`` 일 때만 허용 거부.
      - issue_number 가 주어졌는데 branch name 에 ``issue-<n>`` 또는
        ``<n>`` 토큰이 없으면 warning.
    """

    candidate = (name or "").strip()
    if not candidate:
        return BranchPolicyResult(allowed=False, name="", reason="empty_branch_name")

    last_segment = candidate.split("/")[-1].lower()
    if last_segment in _PROTECTED_BRANCHES:
        return BranchPolicyResult(
            allowed=False,
            name=candidate,
            reason=f"protected_branch:{last_segment}",
        )
    # qualified ref ``refs/heads/main``
    full_lower = candidate.lower()
    for protected in _PROTECTED_BRANCHES:
        if (
            full_lower == protected
            or full_lower.endswith(f"/{protected}")
            or full_lower.startswith(f"refs/heads/{protected}")
            or full_lower.startswith(f"origin/{protected}")
        ):
            return BranchPolicyResult(
                allowed=False,
                name=candidate,
                reason=f"protected_branch:{protected}",
            )

    if not _BRANCH_NAME_RE.match(candidate):
        return BranchPolicyResult(
            allowed=False,
            name=candidate,
            reason="invalid_branch_chars",
        )

    warnings: list[str] = []
    suggested_prefix: Optional[str] = None
    first_segment = candidate.split("/", 1)[0].lower()
    if first_segment not in BRANCH_PREFIXES:
        # 기존 agent/<role> 패턴 (engineering-agent 의 derive_branch_name)
        # 은 회귀 없도록 허용 — 단 standard prefix suggestion 은 남김.
        if first_segment not in {"agent"}:
            warnings.append(
                f"non_standard_prefix:{first_segment} (권장: {', '.join(BRANCH_PREFIXES)})"
            )
            suggested_prefix = BRANCH_PREFIXES[0]
            if require_standard_prefix:
                return BranchPolicyResult(
                    allowed=False,
                    name=candidate,
                    reason=f"non_standard_prefix:{first_segment}",
                    warnings=tuple(warnings),
                    suggested_prefix=suggested_prefix,
                )

    if issue_number is not None and issue_number > 0:
        token = f"issue-{int(issue_number)}"
        if token not in candidate.lower() and f"-{int(issue_number)}-" not in candidate.lower() and not candidate.lower().endswith(f"-{int(issue_number)}"):
            warnings.append(
                f"missing_issue_anchor:#{int(issue_number)}"
            )

    return BranchPolicyResult(
        allowed=True,
        name=candidate,
        warnings=tuple(warnings),
        suggested_prefix=suggested_prefix,
    )


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def derive_standard_branch_name(
    *,
    kind: str,
    short_purpose: str,
    issue_number: Optional[int] = None,
) -> str:
    """표준 branch 이름 생성.

    형식: ``<kind>/<short-purpose>[-issue-<n>]``.

    - ``kind`` 는 :data:`BRANCH_PREFIXES` 중 하나여야 함 (예외 시
      ``ValueError``).
    - ``short_purpose`` 는 slugify — 소문자/숫자/하이픈만 남김.
    - ``issue_number`` 가 있으면 ``-issue-<n>`` 접미사 자동 추가.

    repo contract 가 별도 규칙을 제시하면 caller 가 본 함수를 건너뛰고
    그 규칙을 따른다.
    """

    kind_clean = (kind or "").strip().lower()
    if kind_clean not in BRANCH_PREFIXES:
        raise ValueError(
            f"branch kind {kind!r} 은 표준 prefix 목록에 없음: {BRANCH_PREFIXES}"
        )
    slug = _SLUG_RE.sub("-", (short_purpose or "").lower()).strip("-") or "work"
    suffix = f"-issue-{int(issue_number)}" if issue_number and int(issue_number) > 0 else ""
    return f"{kind_clean}/{slug}{suffix}"


# ---------------------------------------------------------------------------
# PR body policy
# ---------------------------------------------------------------------------


# repo PR template 가 있으면 그 섹션을 우선 따르되, 본 5 종이 어떤
# 형태로든 PR body 에 보여야 한다. 키워드 매칭 (한국어 + 영어) 으로
# 검증한다 — exact heading 강제는 너무 빡빡함.
PR_REQUIRED_SECTIONS: Mapping[str, Tuple[str, ...]] = {
    "purpose": ("목적", "과제 내용", "summary", "purpose", "what"),
    "scope": ("범위", "scope", "in_scope", "out_of_scope"),
    "risks": ("리스크", "위험", "risk"),
    "tests": ("테스트", "test plan", "테스트 계획", "validation"),
    "issue_linkage": ("관련 이슈", "issue", "closes #", "fixes #", "refs #"),
}


PR_AUDIT_BLOCK_MARKERS: Tuple[str, ...] = (
    "engineering-agent",
    "audit",
    "🤖",
    "Generated with",
    "Co-Authored-By",
)


@dataclass(frozen=True)
class PRBodyValidationResult:
    """:func:`validate_pr_body` 결과.

    - ``ok``: 누락 섹션이 없고 audit block 도 있음 → write 진행 가능.
    - ``missing_sections``: 누락 섹션 키 시퀀스 — caller 가 PR body 를
      보강하지 않으면 fake success.
    - ``audit_block_present``: 봇 정체성 마커가 보이는지 — operator 가
      "이 PR 누가 만든 거지?" 를 즉시 식별 가능해야 함.
    """

    ok: bool
    missing_sections: Tuple[str, ...] = field(default_factory=tuple)
    audit_block_present: bool = False
    warnings: Tuple[str, ...] = field(default_factory=tuple)


def validate_pr_body(body: Optional[str]) -> PRBodyValidationResult:
    """*body* 에 5 종 섹션 + audit block 가 모두 있는지 검사.

    검사 방식:
      - 본문을 소문자로 normalize 한 뒤 각 섹션의 한국어 + 영어 키워드
        시퀀스 중 하나라도 매칭되면 통과로 본다 (느슨한 매칭 — repo
        마다 다른 template heading 도 수용).
      - audit block: ``engineering-agent`` / ``audit`` / ``🤖`` 등 마커
        하나라도 있으면 통과.
    """

    text = (body or "").strip()
    if not text:
        return PRBodyValidationResult(
            ok=False,
            missing_sections=tuple(PR_REQUIRED_SECTIONS.keys()),
            audit_block_present=False,
            warnings=("empty_pr_body",),
        )

    haystack = text.lower()
    missing: list[str] = []
    for key, alternatives in PR_REQUIRED_SECTIONS.items():
        if not any(alt.lower() in haystack for alt in alternatives):
            missing.append(key)

    audit_present = any(marker.lower() in haystack for marker in PR_AUDIT_BLOCK_MARKERS)

    warnings: list[str] = []
    if not audit_present:
        warnings.append("missing_audit_block")

    return PRBodyValidationResult(
        ok=not missing and audit_present,
        missing_sections=tuple(missing),
        audit_block_present=audit_present,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Curated note policy
# ---------------------------------------------------------------------------


INBOX_PATH_PREFIX: str = "00-inbox"


# Curated note frontmatter 필수 키. ``home_hub`` 는 hub linkage 강제 — 모든
# curated note 가 1 개 이상의 hub 를 가져야 한다.
CURATED_REQUIRED_FRONTMATTER: Tuple[str, ...] = (
    "title",
    "kind",
    "status",
    "created_at",
    "tags",
    "related",
    "home_hub",
)


# Curated note 본문 필수 섹션 — heading 형태로 본문에 보여야 한다. 키워드
# 매칭은 PR body 와 동일하게 느슨한 alternative 방식.
CURATED_REQUIRED_SECTIONS: Mapping[str, Tuple[str, ...]] = {
    "summary": ("핵심 요약", "요약", "summary"),
    "interpretation": ("내 해석", "해석", "interpretation"),
    "context": ("적용 맥락", "맥락", "context"),
    "related": ("관련 노트", "관련", "related"),
    "references": ("참고", "references", "source"),
}


@dataclass(frozen=True)
class CuratedNoteValidationResult:
    """:func:`validate_curated_note` 결과."""

    ok: bool
    path: str
    missing_frontmatter: Tuple[str, ...] = field(default_factory=tuple)
    missing_sections: Tuple[str, ...] = field(default_factory=tuple)
    reason: Optional[str] = None
    warnings: Tuple[str, ...] = field(default_factory=tuple)


def is_inbox_path(path: Optional[str]) -> bool:
    """*path* 가 ``00-inbox/`` 아래인지 — true 면 curated note 로 인정 안 함.

    `00-inbox` 는 raw 자료 보관소. curated 승격은 새로운 노트 생성
    (`20-areas`, `40-patterns`, `60-troubleshooting`, `10-projects/*`) 으로
    이루어지지, inbox 안에서 자리만 바꾸는 것이 아니다.
    """

    if not path:
        return False
    text = str(path).replace("\\", "/").lstrip("./")
    return text == INBOX_PATH_PREFIX or text.startswith(f"{INBOX_PATH_PREFIX}/")


def validate_curated_note(
    *,
    path: str,
    frontmatter: Optional[Mapping[str, Any]] = None,
    body: Optional[str] = None,
) -> CuratedNoteValidationResult:
    """curated note candidate 가 정책을 만족하는지 검사.

    rules:
      - inbox path → ok=False, reason=``inbox_direct_promotion_forbidden``.
      - frontmatter 누락 키 / 본문 누락 섹션 → ok=False.
      - ``related`` 가 비어있거나 ``home_hub`` 가 비어있으면 ok=False
        (hub linkage 강제).
    """

    if is_inbox_path(path):
        return CuratedNoteValidationResult(
            ok=False,
            path=path or "",
            reason="inbox_direct_promotion_forbidden",
        )

    fm = dict(frontmatter or {})
    text = (body or "")

    missing_fm: list[str] = []
    for key in CURATED_REQUIRED_FRONTMATTER:
        value = fm.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing_fm.append(key)
        elif isinstance(value, (list, tuple)) and not any(
            str(item).strip() for item in value
        ):
            missing_fm.append(key)

    haystack = text.lower()
    missing_sections: list[str] = []
    for key, alternatives in CURATED_REQUIRED_SECTIONS.items():
        if not any(alt.lower() in haystack for alt in alternatives):
            missing_sections.append(key)

    warnings: list[str] = []
    if not missing_fm:
        # hub linkage / related 보강 — 빈 list 였으면 missing_fm 가 잡았지만
        # 단일 placeholder 만 있을 때를 추가 검증
        related = fm.get("related") or ()
        if isinstance(related, (list, tuple)) and len(related) == 0:
            warnings.append("related_empty")
        home_hub = str(fm.get("home_hub") or "").strip()
        if not home_hub:
            warnings.append("home_hub_empty")

    ok = not missing_fm and not missing_sections
    return CuratedNoteValidationResult(
        ok=ok,
        path=path or "",
        missing_frontmatter=tuple(missing_fm),
        missing_sections=tuple(missing_sections),
        reason=None if ok else "missing_required_fields",
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Orphan / broken-link detectors
# ---------------------------------------------------------------------------


_WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+?)(?:\|[^\]]*?)?\]\]")


def detect_orphan_note(
    *,
    note_path: str,
    home_hub: Optional[str],
    related: Sequence[str] = (),
    hub_paths: Sequence[str] = (),
) -> bool:
    """note 가 어떤 hub 에도 연결돼 있지 않은지 (= orphan) 검사.

    True 반환 = orphan — push 금지. 호출자는 hub 를 먼저 만들거나
    related 를 추가해 연결성을 확보해야 한다.

    조건 (orphan):
      - home_hub 가 비어있고
      - related 도 비어있음
      - OR home_hub 가 hub_paths 안에 없음
    """

    has_related = any(str(r).strip() for r in (related or ()))
    home_hub_clean = (home_hub or "").strip()
    if not home_hub_clean and not has_related:
        return True
    hub_set = {str(p).strip() for p in (hub_paths or ()) if str(p).strip()}
    if hub_set and home_hub_clean and home_hub_clean not in hub_set:
        # related 가 있어도 home_hub 가 untracked 면 orphan 의심
        return not has_related
    return False


def detect_broken_links(
    *,
    body: str,
    available_paths: Sequence[str],
) -> Tuple[str, ...]:
    """*body* 의 wikilink ``[[target]]`` 중 *available_paths* 에 없는
    target 시퀀스 반환.

    matching 은 basename 비교 + 정확 매칭 둘 다 시도. 둘 다 실패하면
    broken link 로 본다.
    """

    text = body or ""
    if not text:
        return ()
    available = {str(p).strip() for p in (available_paths or ()) if str(p).strip()}
    basenames = {p.rsplit("/", 1)[-1].rsplit(".", 1)[0] for p in available}
    broken: list[str] = []
    for match in _WIKILINK_RE.finditer(text):
        target = match.group(1).strip()
        if not target:
            continue
        if target in available:
            continue
        # basename 매칭
        tail = target.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if tail in basenames:
            continue
        broken.append(target)
    return tuple(broken)


# ---------------------------------------------------------------------------
# Retrieval eval policy
# ---------------------------------------------------------------------------


RETRIEVAL_EVAL_REQUIRED_KEYS: Tuple[str, ...] = (
    "question",
    "expected_notes",
    "allowed_alternatives",
    "failure_reason",
)


MIN_RETRIEVAL_EVAL_QUESTIONS: int = 50
TARGET_RETRIEVAL_EVAL_QUESTIONS: int = 100
RETRIEVAL_EVAL_TOP_K: int = 5


@dataclass(frozen=True)
class RetrievalEvalEntryResult:
    ok: bool
    entry_index: int
    missing_keys: Tuple[str, ...] = field(default_factory=tuple)
    reason: Optional[str] = None


def validate_retrieval_eval_entry(
    entry: Mapping[str, Any],
    *,
    index: int = 0,
) -> RetrievalEvalEntryResult:
    """retrieval eval fixture 의 한 entry 가 스키마를 만족하는지 검사.

    - ``question`` / ``expected_notes`` 는 비어있을 수 없음.
    - ``allowed_alternatives`` 는 빈 리스트라도 키 자체는 존재해야 한다
      (eval runner 가 None 과 빈 리스트를 구분).
    - ``failure_reason`` 은 eval 결과 분석을 위한 기록 — 빈 문자열은
      허용하되 키는 존재해야.
    """

    missing: list[str] = []
    for key in RETRIEVAL_EVAL_REQUIRED_KEYS:
        if key not in entry:
            missing.append(key)
    if missing:
        return RetrievalEvalEntryResult(
            ok=False,
            entry_index=index,
            missing_keys=tuple(missing),
            reason="missing_keys",
        )

    question = str(entry.get("question") or "").strip()
    if not question:
        return RetrievalEvalEntryResult(
            ok=False, entry_index=index, reason="empty_question"
        )
    expected = entry.get("expected_notes") or ()
    if not isinstance(expected, (list, tuple)) or not any(
        str(n).strip() for n in expected
    ):
        return RetrievalEvalEntryResult(
            ok=False, entry_index=index, reason="empty_expected_notes"
        )

    return RetrievalEvalEntryResult(ok=True, entry_index=index)


@dataclass(frozen=True)
class RetrievalEvalFixtureResult:
    ok: bool
    entry_count: int
    entry_failures: Tuple[RetrievalEvalEntryResult, ...] = field(default_factory=tuple)
    warnings: Tuple[str, ...] = field(default_factory=tuple)


def validate_retrieval_eval_fixture(
    entries: Sequence[Mapping[str, Any]],
) -> RetrievalEvalFixtureResult:
    """fixture 전체 — entry 스키마 + 최소 count.

    50 미만 → ok=False (regression 차단), 50~99 → warning, 100+ → 통과.
    """

    failures: list[RetrievalEvalEntryResult] = []
    for idx, entry in enumerate(entries or ()):
        result = validate_retrieval_eval_entry(entry, index=idx)
        if not result.ok:
            failures.append(result)
    count = len(entries or ())
    warnings: list[str] = []
    if count < MIN_RETRIEVAL_EVAL_QUESTIONS:
        return RetrievalEvalFixtureResult(
            ok=False,
            entry_count=count,
            entry_failures=tuple(failures),
            warnings=(f"below_min:{count}<{MIN_RETRIEVAL_EVAL_QUESTIONS}",),
        )
    if count < TARGET_RETRIEVAL_EVAL_QUESTIONS:
        warnings.append(
            f"below_target:{count}<{TARGET_RETRIEVAL_EVAL_QUESTIONS}"
        )
    return RetrievalEvalFixtureResult(
        ok=not failures,
        entry_count=count,
        entry_failures=tuple(failures),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Post-test hardening — opening criteria
# ---------------------------------------------------------------------------


class HardeningOpeningCriterion(str, Enum):
    """8 종 opening criteria — 이 중 하나라도 충족돼야 성능 개선 / 고도화
    작업 brunch 를 열 수 있다. correctness > visibility > maintainability >
    performance 순서."""

    QUEUE_BACKLOG = "queue_backlog"
    RUNTIME_STATUS_LATENCY = "runtime_status_latency"
    RETRIEVAL_EVAL_REGRESSION = "retrieval_eval_regression"
    PROMPT_SIZE_CEILING = "prompt_size_ceiling"
    LARGE_FILE_RULE = "large_file_rule"  # 700 warning / 1000 split
    DUPLICATE_WORK = "duplicate_work"
    CRITICAL_PATH_BOTTLENECK = "critical_path_bottleneck"
    FLAKY_OR_SLOW_TEST = "flaky_or_slow_test"


HARDENING_OPENING_CRITERIA: Tuple[str, ...] = tuple(
    c.value for c in HardeningOpeningCriterion
)


@dataclass(frozen=True)
class HardeningOpeningDecision:
    """:func:`decide_hardening_opening` 결과.

    - ``allowed`` True: 최소 1 개의 criterion 충족 + baseline 측정 의무
      + metric 명시 의무 (caller 가 PR 본문에 적어야 함).
    - ``allowed`` False: 충족된 criterion 없음 → 성능 개선 작업을 열면
      안 됨 (scope creep 방지).
    """

    allowed: bool
    matched_criteria: Tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
    required_artifacts: Tuple[str, ...] = field(default_factory=tuple)


_HARDENING_REQUIRED_ARTIFACTS: Tuple[str, ...] = (
    "baseline_measurement",
    "target_metric",
    "behavior_change_separated",
    "regression_test",
)


def decide_hardening_opening(
    observations: Mapping[str, Any],
) -> HardeningOpeningDecision:
    """*observations* 에서 매칭되는 opening criterion 을 추출.

    *observations* 는 자유 형태 mapping. 본 함수가 인식하는 키:

      - ``queue_backlog_jobs``: int — 0 초과면 :attr:`QUEUE_BACKLOG`
      - ``status_latency_seconds``: float — 30 이상이면
        :attr:`RUNTIME_STATUS_LATENCY`
      - ``retrieval_eval_regression``: bool/Truthy →
        :attr:`RETRIEVAL_EVAL_REGRESSION`
      - ``prompt_size_bytes`` / ``prompt_size_ceiling``: int — size 가
        ceiling 의 0.9 이상이면 :attr:`PROMPT_SIZE_CEILING`
      - ``large_files`` (시퀀스): 비어있지 않으면 :attr:`LARGE_FILE_RULE`
      - ``duplicate_work_signals`` (시퀀스): 비어있지 않으면
        :attr:`DUPLICATE_WORK`
      - ``critical_path_bottleneck``: bool/Truthy →
        :attr:`CRITICAL_PATH_BOTTLENECK`
      - ``flaky_tests`` / ``slow_tests`` (시퀀스): 비어있지 않으면
        :attr:`FLAKY_OR_SLOW_TEST`

    하나라도 매칭되지 않으면 ``allowed=False`` + 사유 — caller 는 성능
    개선 작업을 열지 말고 correctness/visibility 작업만 진행.
    """

    obs = dict(observations or {})
    matched: list[str] = []

    if _coerce_int(obs.get("queue_backlog_jobs")) > 0:
        matched.append(HardeningOpeningCriterion.QUEUE_BACKLOG.value)
    if _coerce_float(obs.get("status_latency_seconds")) >= 30.0:
        matched.append(HardeningOpeningCriterion.RUNTIME_STATUS_LATENCY.value)
    if bool(obs.get("retrieval_eval_regression")):
        matched.append(
            HardeningOpeningCriterion.RETRIEVAL_EVAL_REGRESSION.value
        )
    prompt_size = _coerce_int(obs.get("prompt_size_bytes"))
    ceiling = _coerce_int(obs.get("prompt_size_ceiling"))
    if prompt_size and ceiling and prompt_size >= int(ceiling * 0.9):
        matched.append(HardeningOpeningCriterion.PROMPT_SIZE_CEILING.value)
    if _has_non_empty_seq(obs, "large_files"):
        matched.append(HardeningOpeningCriterion.LARGE_FILE_RULE.value)
    if _has_non_empty_seq(obs, "duplicate_work_signals"):
        matched.append(HardeningOpeningCriterion.DUPLICATE_WORK.value)
    if bool(obs.get("critical_path_bottleneck")):
        matched.append(
            HardeningOpeningCriterion.CRITICAL_PATH_BOTTLENECK.value
        )
    if _has_non_empty_seq(obs, "flaky_tests") or _has_non_empty_seq(
        obs, "slow_tests"
    ):
        matched.append(HardeningOpeningCriterion.FLAKY_OR_SLOW_TEST.value)

    if not matched:
        return HardeningOpeningDecision(
            allowed=False,
            matched_criteria=(),
            reason="no_opening_criteria_met",
            required_artifacts=_HARDENING_REQUIRED_ARTIFACTS,
        )

    return HardeningOpeningDecision(
        allowed=True,
        matched_criteria=tuple(matched),
        reason=f"matched:{','.join(matched)}",
        required_artifacts=_HARDENING_REQUIRED_ARTIFACTS,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _has_non_empty_seq(obs: Mapping[str, Any], key: str) -> bool:
    value = obs.get(key)
    if not isinstance(value, (list, tuple)):
        return False
    return any(item not in (None, "") for item in value)


__all__ = (
    "BRANCH_PREFIXES",
    "BranchPolicyResult",
    "CURATED_REQUIRED_FRONTMATTER",
    "CURATED_REQUIRED_SECTIONS",
    "CuratedNoteValidationResult",
    "HARDENING_OPENING_CRITERIA",
    "HardeningOpeningCriterion",
    "HardeningOpeningDecision",
    "INBOX_PATH_PREFIX",
    "MIN_RETRIEVAL_EVAL_QUESTIONS",
    "PRBodyValidationResult",
    "PR_REQUIRED_SECTIONS",
    "PR_AUDIT_BLOCK_MARKERS",
    "RETRIEVAL_EVAL_REQUIRED_KEYS",
    "RETRIEVAL_EVAL_TOP_K",
    "RetrievalEvalEntryResult",
    "RetrievalEvalFixtureResult",
    "TARGET_RETRIEVAL_EVAL_QUESTIONS",
    "decide_hardening_opening",
    "derive_standard_branch_name",
    "detect_broken_links",
    "detect_orphan_note",
    "is_inbox_path",
    "validate_branch_name",
    "validate_curated_note",
    "validate_pr_body",
    "validate_retrieval_eval_entry",
    "validate_retrieval_eval_fixture",
)
