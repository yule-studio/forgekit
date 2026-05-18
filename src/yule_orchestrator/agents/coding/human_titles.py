"""P1-M D — Issue / PR 제목 한국어 humanizer.

배경 — 옛 wiring 은 PR 제목을 ``"📝 #3 coding-executor draft"`` 같이
기계형으로 만들었다. 사람이 GitHub timeline 만 보면 무엇을 하는 PR 인지
알 수 없다.

본 모듈은 모든 issue / PR 제목을 **명확한 한국어 + 카테고리 prefix +
slice 영역** 으로 만든다. 같은 helper 를 issue creator (work_order) /
PR creator (coding executor) 가 공유.

규칙
  * Issue 제목: ``[Feature] <세션 prompt 요약> — <slice 영역>``
  * PR 제목:    ``[구현][<영역>] <slice title>``  (slice 가 있을 때)
                 ``[구현] <세션 prompt 요약>``    (slice 없을 때)
  * 100자 cap (GitHub UI truncation 방지) + 줄바꿈 제거.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional


_TITLE_MAX_LEN: int = 100
_AREA_LABEL: Mapping[str, str] = {
    "auth": "인증",
    "search": "검색",
    "blog": "블로그",
    "mail": "메일",
    "runtime": "런타임",
    "polish": "마무리",
    "backend": "백엔드",
    "frontend": "프론트엔드",
    "platform": "플랫폼",
}


def _strip_to_summary(text: str, *, max_len: int = 60) -> str:
    """프롬프트에서 첫 줄 + 한 문장 추출, 줄바꿈 / URL 제거."""

    if not text:
        return ""
    first_line = text.strip().splitlines()[0].strip()
    # URL / github 토큰 제거
    first_line = re.sub(r"https?://\S+", "", first_line)
    first_line = re.sub(r"github\.com\S+", "", first_line)
    # 모드 토큰 제거 (autonomous_merge / approval_required / single_repo / full_stack_single_repo)
    first_line = re.sub(
        r"\b(autonomous_merge|approval_required|single_repo|multi_repo"
        r"|full_stack_single_repo|single_scope|layer_scoped|cross_repo_program)\b",
        "",
        first_line,
        flags=re.IGNORECASE,
    )
    # 첫 문장만
    first_line = re.split(r"[.。!?\n]", first_line, maxsplit=1)[0]
    cleaned = re.sub(r"\s+", " ", first_line).strip(" ,，·-")
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def _resolve_area(slice_spec: Optional[Mapping[str, Any]]) -> str:
    if not slice_spec:
        return ""
    area = str(slice_spec.get("area") or "").strip().lower()
    if area in _AREA_LABEL:
        return _AREA_LABEL[area]
    role = str(slice_spec.get("executor_role") or "").strip().lower()
    if role.startswith("frontend"):
        return _AREA_LABEL["frontend"]
    if role.startswith("backend"):
        return _AREA_LABEL["backend"]
    if role.startswith("platform"):
        return _AREA_LABEL["platform"]
    return area or ""


def build_issue_title(
    *,
    session_prompt: Optional[str],
    slice_spec: Optional[Mapping[str, Any]] = None,
    fallback_short_purpose: Optional[str] = None,
) -> str:
    """Issue 제목 — ``[Feature] <요약> — <영역>`` 한국어.

    slice_spec 이 있으면 slice 의 title 을 우선 사용.
    """

    if slice_spec:
        title = str(slice_spec.get("title") or "").strip()
        if title:
            area = _resolve_area(slice_spec)
            if area and area not in title:
                composed = f"[Feature][{area}] {title}"
            else:
                composed = f"[Feature] {title}"
            return composed[:_TITLE_MAX_LEN]

    summary = _strip_to_summary(session_prompt or "")
    if not summary:
        summary = (fallback_short_purpose or "코딩 작업").strip()
    return f"[Feature] {summary}"[:_TITLE_MAX_LEN]


_KOREAN_RE_BUILDER = re.compile(r"[가-힣]")


def _has_min_korean(text: str, *, min_chars: int = 4) -> bool:
    """P1-S — validator (repo_write_policy.validate_pr_title) 와 동일
    기준으로 한국어 문자 갯수 사전 검사.  builder 가 validator 와 정합."""

    return sum(1 for ch in text or "" if _KOREAN_RE_BUILDER.match(ch)) >= min_chars


def _korean_fallback_title(
    *, issue_number: Optional[int], area: Optional[str] = None
) -> str:
    """P1-S — validator 통과를 보장하는 한국어 default title.

    issue_number 있으면 issue 번호를 포함, area 있으면 area 라벨 포함.
    어느 경우든 ``[구현]`` 또는 ``[구현][area]`` prefix + 4+ 한국어 chars +
    branch / 영문 detail 없이 사람이 읽는 줄.
    """

    if area:
        base = f"[구현][{area}] 작업 자동 진행"
    else:
        base = "[구현] 코딩 작업 자동 진행"
    if issue_number:
        return f"{base} (#{int(issue_number)})"
    return base


def build_pr_title(
    *,
    session_prompt: Optional[str],
    slice_spec: Optional[Mapping[str, Any]] = None,
    branch_hint: Optional[str] = None,
    issue_number: Optional[int] = None,
    fallback_short_purpose: Optional[str] = None,
) -> str:
    """PR 제목 — ``[구현][영역] <slice title>`` 또는
    ``[구현] <요약>`` (slice 없을 때).

    P1-S 강화 — builder 가 절대로 machine-like / 영문-only / 4-한국어-chars-미만
    출력을 emit 하지 않음.  본 분기 결과가 validator 의 한국어 4 자 검사
    불통이면 ``_korean_fallback_title`` 로 self-correct.

    issue_number 가 있으면 끝에 ``(#N)`` 추가 — GitHub 가 자동으로 PR 을
    issue 에 link 하지만 시각 단서로도 노출.  ``branch_hint`` 는 더 이상
    fallback 출력 source 로 사용 안 함 (machine-like 회귀 차단).
    """

    candidate: Optional[str] = None
    area_label: Optional[str] = None

    if slice_spec:
        slice_title = str(slice_spec.get("title") or "").strip()
        area_label = _resolve_area(slice_spec) or None
        if slice_title:
            prefix = f"[구현][{area_label}]" if area_label else "[구현]"
            base = f"{prefix} {slice_title}"
            if issue_number:
                base = f"{base} (#{int(issue_number)})"
            candidate = base[:_TITLE_MAX_LEN]

    if candidate is None:
        summary = _strip_to_summary(session_prompt or "")
        if not summary:
            summary = (fallback_short_purpose or "").strip()
        # P1-S — branch_hint 기반 fallback 제거.  옛 wiring 은 branch
        # 이름의 마지막 segment (e.g. ``issue-5-coding-execute``) 를
        # 사용했지만 그 결과는 한국어 0 자라 validator reject 의 직접
        # 원인이었다.  branch 정보는 PR body 의 메타 블록에서 노출.
        if summary:
            base = f"[구현] {summary}"
            if issue_number:
                base = f"{base} (#{int(issue_number)})"
            candidate = base[:_TITLE_MAX_LEN]

    # P1-S 정합성 강제 — 어떤 분기를 거쳐도 validator 와 동일 한국어 4 자
    # 기준을 만족 못 하면 deterministic 한국어 fallback 으로 교체.
    if candidate is None or not _has_min_korean(candidate):
        candidate = _korean_fallback_title(
            issue_number=issue_number, area=area_label
        )

    return candidate[:_TITLE_MAX_LEN]


def build_pr_body_intro(
    *,
    session_id: Optional[str],
    repo_full_name: Optional[str],
    work_mode: Optional[str],
    slice_spec: Optional[Mapping[str, Any]] = None,
    branch: Optional[str] = None,
    backlog_remaining: Optional[int] = None,
) -> str:
    """PR body 상단의 한국어 메타 블록 — operator 가 첫 눈에 단계/모드 파악."""

    lines = ["## 🧭 현재 단계", ""]
    if slice_spec:
        lines.append(f"- slice: **{slice_spec.get('title') or '(no title)'}**")
        if slice_spec.get("summary"):
            lines.append(f"- 요약: {slice_spec.get('summary')}")
        area = _resolve_area(slice_spec)
        if area:
            lines.append(f"- 영역: {area}")
    if session_id:
        lines.append(f"- session_id: `{session_id}`")
    if repo_full_name:
        lines.append(f"- repo: `{repo_full_name}`")
    if work_mode:
        lines.append(f"- work_mode: `{work_mode}`")
    if branch:
        lines.append(f"- branch: `{branch}`")
    if backlog_remaining is not None:
        lines.append(f"- 남은 slice: **{int(backlog_remaining)} 개**")
    lines.append("")
    return "\n".join(lines)


__all__ = (
    "build_issue_title",
    "build_pr_body_intro",
    "build_pr_title",
)
