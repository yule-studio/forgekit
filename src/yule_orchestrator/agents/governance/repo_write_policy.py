"""P1-N — Cross-repo GitHub write policy hard guards.

봇이 commit / issue / branch / PR 을 만드는 모든 write path 에서 호출되는
**중앙 enforcement layer**. 본 모듈이 reject 한 작업은 live path 에서
raise 되어 실제로 차단된다 (advisory / warning 아님).

코드 SSoT — 본 모듈. 사람용 SSoT — [`policies/reference/COMMIT_CONVENTION.md`](
../../../../policies/reference/COMMIT_CONVENTION.md) + [`policies/runtime/agents/
engineering-agent/issue-pr-conventions.md`](../../../../policies/runtime/agents/
engineering-agent/issue-pr-conventions.md).

검증 항목
  1. ``validate_commit_message`` — gitmoji whitelist + 3-section format
  2. ``validate_issue_title`` — 한국어 + [기능]/[구현]/... prefix 강제
  3. ``validate_pr_title``    — 동일 패턴 + 모드 토큰 금지
  4. ``validate_issue_anchor``— branch / PR body 에 ``issue-N`` anchor 필수
  5. ``is_initial_commit_context`` + ``validate_initial_commit_message``
     — 첫 커밋 special case (`:tada: initial commit`)

각 validator 는 ``PolicyResult`` 를 반환 — caller 가 ``ok`` 확인 후
``reason`` / ``detail`` 로 raise 메시지 구성. live path 에는
``PolicyViolation`` 예외를 raise 하는 ``enforce_*`` 헬퍼 사용 권장.

cross-repo 적용 — 본 모듈은 yule-studio-agent / naver-search-clone / 향후
어떤 target repo 에도 동일하게 동작. repo-local override 가 필요하면
caller 가 ``repo_full_name`` 으로 stricter 규칙을 덧붙인다.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Reason tokens — operator surface 가 한눈에 보는 blocker
# ---------------------------------------------------------------------------

REASON_INVALID_COMMIT_GITMOJI: str = "invalid_commit_gitmoji"
REASON_INVALID_COMMIT_BODY_SECTIONS: str = "invalid_commit_body_sections"
REASON_INVALID_INITIAL_COMMIT_TITLE: str = "invalid_initial_commit_title"
REASON_TADA_OUTSIDE_INITIAL_COMMIT: str = "tada_used_outside_initial_commit"
REASON_INITIAL_COMMIT_DETECTION_AMBIGUOUS: str = (
    "initial_commit_detection_ambiguous"
)

REASON_INVALID_ISSUE_TITLE: str = "invalid_issue_title_not_human_readable_korean"
REASON_INVALID_PR_TITLE: str = "invalid_pr_title_not_human_readable_korean"
REASON_ISSUE_REQUIRED_FOR_REPO_WORK: str = "issue_required_for_repo_work"


# ---------------------------------------------------------------------------
# Gitmoji whitelist — SSoT 와 동일
# ---------------------------------------------------------------------------

_BASE_GITMOJI: tuple = ("✨", "🐛", "♻️", "📝", "✅", "🔧")
_OPTIONAL_GITMOJI: tuple = ("🚚", "🔥", "⚡️", "👷", "💚", "🚑️")
ALLOWED_GITMOJI: frozenset = frozenset(_BASE_GITMOJI + _OPTIONAL_GITMOJI)

# initial commit 전용 special-case
INITIAL_COMMIT_TITLE_EXACT: str = ":tada: initial commit"
INITIAL_GITMOJI_TEXT: str = ":tada:"


# ---------------------------------------------------------------------------
# Commit body 섹션 규칙
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS: tuple = ("변경 이유", "주요 변경 사항", "비고")
_MD_HEADER_SECTIONS: tuple = (
    "## 변경 이유",
    "## 주요 변경 사항",
    "## 비고",
)


@dataclass(frozen=True)
class PolicyResult:
    ok: bool
    reason: Optional[str] = None
    detail: str = ""
    fields: Mapping[str, Any] = field(default_factory=dict)


class PolicyViolation(RuntimeError):
    """live path 에서 raise — operator surface 가 reason 으로 분기.

    `.reason` 은 위의 ``REASON_*`` 토큰 중 하나. `.detail` 은 사람이 읽는
    한 줄 설명. `.fields` 는 추가 진단 dict.
    """

    def __init__(self, *, reason: str, detail: str = "", fields: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
        self.fields = dict(fields or {})


def _raise(result: PolicyResult) -> None:
    if not result.ok and result.reason:
        raise PolicyViolation(
            reason=result.reason, detail=result.detail, fields=result.fields
        )


# ---------------------------------------------------------------------------
# Commit message validation
# ---------------------------------------------------------------------------


def _strip_gitmoji_prefix(title_line: str) -> tuple:
    """첫 토큰이 알려진 gitmoji 면 (gitmoji, 나머지) 반환."""

    stripped = title_line.strip()
    for emoji in sorted(ALLOWED_GITMOJI, key=len, reverse=True):
        if stripped.startswith(emoji + " "):
            return emoji, stripped[len(emoji) + 1 :].lstrip()
        if stripped == emoji:
            return emoji, ""
    # :tada: special case (initial commit shortcode form)
    if stripped.lower().startswith(INITIAL_GITMOJI_TEXT.lower()):
        return INITIAL_GITMOJI_TEXT, stripped[len(INITIAL_GITMOJI_TEXT) :].lstrip()
    return "", stripped


def validate_commit_message(
    text: str, *, is_initial: bool = False
) -> PolicyResult:
    """commit message 검증.

    *is_initial=True* 이면 ``:tada: initial commit`` 정확 매칭 + 본문 규칙.
    *is_initial=False* 이면 일반 whitelist + 본문 규칙 + ``:tada:`` 금지.
    """

    if not text or not text.strip():
        return PolicyResult(
            ok=False,
            reason=REASON_INVALID_COMMIT_BODY_SECTIONS,
            detail="empty commit message",
        )

    lines = text.splitlines()
    title = (lines[0] if lines else "").strip()

    gitmoji, rest = _strip_gitmoji_prefix(title)

    # Initial commit 분기
    if is_initial:
        if title.strip() != INITIAL_COMMIT_TITLE_EXACT:
            return PolicyResult(
                ok=False,
                reason=REASON_INVALID_INITIAL_COMMIT_TITLE,
                detail=(
                    f"initial commit must be exactly {INITIAL_COMMIT_TITLE_EXACT!r}, "
                    f"got {title!r}"
                ),
                fields={"expected": INITIAL_COMMIT_TITLE_EXACT, "got": title},
            )
        return _validate_body_sections(text, gitmoji=gitmoji)

    # Non-initial: ``:tada:`` 금지
    if gitmoji == INITIAL_GITMOJI_TEXT or title.startswith("🎉"):
        return PolicyResult(
            ok=False,
            reason=REASON_TADA_OUTSIDE_INITIAL_COMMIT,
            detail=(
                f"`{INITIAL_GITMOJI_TEXT}` / 🎉 is reserved for the first commit"
            ),
            fields={"got_title": title},
        )

    # gitmoji whitelist 확인
    if gitmoji not in ALLOWED_GITMOJI:
        return PolicyResult(
            ok=False,
            reason=REASON_INVALID_COMMIT_GITMOJI,
            detail=(
                f"first token must be one of allowed gitmoji {sorted(ALLOWED_GITMOJI)}; "
                f"got title={title!r}"
            ),
            fields={"allowed": sorted(ALLOWED_GITMOJI), "got_title": title},
        )

    # 제목 끝 마침표 금지
    if rest.endswith(".") or rest.endswith("。"):
        return PolicyResult(
            ok=False,
            reason=REASON_INVALID_COMMIT_BODY_SECTIONS,
            detail="commit title must not end with a period",
            fields={"got_title": title},
        )

    return _validate_body_sections(text, gitmoji=gitmoji)


def _validate_body_sections(text: str, *, gitmoji: str) -> PolicyResult:
    """본문에 변경 이유 / 주요 변경 사항 / 비고 3 섹션이 plain text 헤더로 있는지."""

    # markdown header (##) 사용 시 명확히 거부 — SSoT 와 충돌
    for md_header in _MD_HEADER_SECTIONS:
        if md_header in text:
            return PolicyResult(
                ok=False,
                reason=REASON_INVALID_COMMIT_BODY_SECTIONS,
                detail=(
                    f"section header must be plain text per SSoT — found markdown "
                    f"header {md_header!r}. Use {md_header.lstrip('#').strip()!r} instead."
                ),
                fields={"bad_header": md_header},
            )

    missing: List[str] = []
    for section in REQUIRED_SECTIONS:
        # plain-text header on its own line (allow trailing whitespace)
        pattern = rf"(?m)^\s*{re.escape(section)}\s*$"
        if not re.search(pattern, text):
            missing.append(section)
    if missing:
        return PolicyResult(
            ok=False,
            reason=REASON_INVALID_COMMIT_BODY_SECTIONS,
            detail=(
                "commit body missing required section(s): "
                + ", ".join(missing)
                + ". Each must appear as a plain-text header followed by `- ...` bullets "
                + "(or `- 없음` if empty)."
            ),
            fields={"missing_sections": missing},
        )

    # 각 section 직후에 최소 한 줄의 bullet 또는 `- 없음` 이 있어야 함
    for section in REQUIRED_SECTIONS:
        m = re.search(
            rf"(?ms)^\s*{re.escape(section)}\s*$\n(.*?)(?=^\s*(?:{'|'.join(re.escape(s) for s in REQUIRED_SECTIONS)})\s*$|\Z)",
            text,
        )
        if m is None:
            continue
        body_block = m.group(1).strip()
        if not body_block:
            return PolicyResult(
                ok=False,
                reason=REASON_INVALID_COMMIT_BODY_SECTIONS,
                detail=(
                    f"section {section!r} is empty — write `- 없음` when intentionally empty"
                ),
                fields={"empty_section": section},
            )

    return PolicyResult(ok=True, fields={"gitmoji": gitmoji})


def enforce_commit_message(text: str, *, is_initial: bool = False) -> None:
    _raise(validate_commit_message(text, is_initial=is_initial))


# ---------------------------------------------------------------------------
# Initial commit context detection
# ---------------------------------------------------------------------------


_MODE_TOKEN_RE = re.compile(
    r"\b(autonomous_merge|approval_required|single_repo|multi_repo"
    r"|full_stack_single_repo|single_scope|layer_scoped|cross_repo_program)\b",
    re.IGNORECASE,
)
_MACHINE_TITLE_RE = re.compile(
    r"(coding[- ]?executor(?:\s+draft)?|draft\s+pr|coding-executor draft|"
    r"agent[/\\-][^\s]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InitialCommitDecision:
    is_initial: bool
    ambiguous: bool = False
    reason: str = ""


def is_initial_commit_context(
    *,
    repo_root: Optional[str] = None,
    explicit_hint: Optional[bool] = None,
    branch_hint: Optional[str] = None,
) -> InitialCommitDecision:
    """초기 commit 인지 결정.

    1. ``explicit_hint=True`` → initial. ``False`` → not initial.
    2. ``repo_root`` 가 주어지면 git log -1 이 없을 때 initial 로 본다.
    3. 둘 다 단서가 없으면 ambiguous (caller 가 surface 해야 함).
    """

    if explicit_hint is True:
        return InitialCommitDecision(is_initial=True, reason="explicit_hint")
    if explicit_hint is False:
        return InitialCommitDecision(is_initial=False, reason="explicit_hint")

    if repo_root:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_root), "rev-list", "--count", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, OSError):
            return InitialCommitDecision(
                is_initial=False,
                ambiguous=True,
                reason="git_unavailable",
            )
        if result.returncode != 0:
            # 'HEAD' resolve 실패 → repo 에 commit 이 아직 없다는 신호
            stderr = (result.stderr or "").lower()
            if "unknown revision" in stderr or "ambiguous argument" in stderr:
                return InitialCommitDecision(is_initial=True, reason="no_head_yet")
            return InitialCommitDecision(
                is_initial=False,
                ambiguous=True,
                reason="git_command_failed",
            )
        try:
            count = int((result.stdout or "0").strip())
        except ValueError:
            return InitialCommitDecision(
                is_initial=False, ambiguous=True, reason="non_integer_count"
            )
        if count == 0:
            return InitialCommitDecision(is_initial=True, reason="zero_commits")
        # branch_hint 에 'bootstrap' 또는 'initial' 이 있어도 이미 commit 이
        # 있으면 ambiguous 로 분류 (caller 가 명시적 hint 로 결정해야 함).
        if branch_hint and any(
            token in branch_hint.lower() for token in ("bootstrap", "initial", "scaffold")
        ):
            return InitialCommitDecision(
                is_initial=False,
                ambiguous=True,
                reason="branch_says_bootstrap_but_commits_exist",
            )
        return InitialCommitDecision(is_initial=False, reason=f"commit_count={count}")

    # repo_root 도 없고 hint 도 없음
    return InitialCommitDecision(
        is_initial=False, ambiguous=True, reason="no_signal"
    )


def validate_initial_commit_decision(
    decision: InitialCommitDecision,
) -> PolicyResult:
    """ambiguous 면 honest blocker — caller 가 surface 해야 함."""

    if decision.ambiguous:
        return PolicyResult(
            ok=False,
            reason=REASON_INITIAL_COMMIT_DETECTION_AMBIGUOUS,
            detail=(
                f"cannot determine if this is the initial commit "
                f"(reason={decision.reason}). Pass explicit_hint=True/False."
            ),
            fields={"detection_reason": decision.reason},
        )
    return PolicyResult(ok=True, fields={"is_initial": decision.is_initial})


# ---------------------------------------------------------------------------
# Issue / PR title validation
# ---------------------------------------------------------------------------


_KOREAN_RE = re.compile(r"[가-힣]")  # Hangul syllables
_ALLOWED_TITLE_PREFIXES: tuple = ("[기능]", "[구현]", "[수정]", "[문서]", "[설정]", "[테스트]", "[리팩토링]")


def _title_has_korean(text: str) -> bool:
    return bool(_KOREAN_RE.search(text or ""))


def _title_has_machine_pattern(text: str) -> bool:
    if not text:
        return False
    if _MACHINE_TITLE_RE.search(text):
        return True
    return False


def _title_has_mode_token(text: str) -> bool:
    return bool(_MODE_TOKEN_RE.search(text or ""))


def _validate_human_title(
    text: str, *, kind: str, allow_machine: bool = False
) -> PolicyResult:
    """공통 title 검증.

    kind: 'issue' or 'pr' — reason 토큰 분기.
    """

    if not text or not text.strip():
        return PolicyResult(
            ok=False,
            reason=(
                REASON_INVALID_ISSUE_TITLE
                if kind == "issue"
                else REASON_INVALID_PR_TITLE
            ),
            detail="title is empty",
        )
    text = text.strip()
    if len(text) > 100:
        return PolicyResult(
            ok=False,
            reason=(
                REASON_INVALID_ISSUE_TITLE
                if kind == "issue"
                else REASON_INVALID_PR_TITLE
            ),
            detail=f"title length {len(text)} > 100 — must be <= 100 chars",
            fields={"title": text, "length": len(text)},
        )

    if _title_has_mode_token(text):
        return PolicyResult(
            ok=False,
            reason=(
                REASON_INVALID_ISSUE_TITLE
                if kind == "issue"
                else REASON_INVALID_PR_TITLE
            ),
            detail=(
                "title contains mode tokens (autonomous_merge / single_repo / "
                "full_stack_single_repo 등) — these are session metadata, not features"
            ),
            fields={"title": text},
        )

    if not allow_machine and _title_has_machine_pattern(text):
        return PolicyResult(
            ok=False,
            reason=(
                REASON_INVALID_ISSUE_TITLE
                if kind == "issue"
                else REASON_INVALID_PR_TITLE
            ),
            detail=(
                "title is machine-like (e.g. 'coding-executor draft', 'agent/...') — "
                "rewrite using the Korean humanizer (agents/coding/human_titles.py)"
            ),
            fields={"title": text},
        )

    # Korean 문자 갯수 — prefix 만 한국어이고 본문은 영문인 경우 reject.
    # 예: `[구현] fix authentication bug` → 한국어 chars 2 (구현) 만 있으면
    # 본문이 영문이므로 사람이 작업 내용을 한눈에 못 본다.
    korean_chars = sum(1 for ch in text if _KOREAN_RE.match(ch))
    if korean_chars < 4:
        return PolicyResult(
            ok=False,
            reason=(
                REASON_INVALID_ISSUE_TITLE
                if kind == "issue"
                else REASON_INVALID_PR_TITLE
            ),
            detail=(
                f"title must contain at least 4 Korean (한국어) chars describing "
                f"the work — got {korean_chars}. Move non-Korean implementation "
                f"detail to the body."
            ),
            fields={"title": text, "korean_char_count": korean_chars},
        )

    if not any(text.startswith(p) for p in _ALLOWED_TITLE_PREFIXES):
        return PolicyResult(
            ok=False,
            reason=(
                REASON_INVALID_ISSUE_TITLE
                if kind == "issue"
                else REASON_INVALID_PR_TITLE
            ),
            detail=(
                "title must start with one of "
                + ", ".join(_ALLOWED_TITLE_PREFIXES)
                + " (use agents/coding/human_titles.py to build correctly)"
            ),
            fields={"title": text, "allowed_prefixes": list(_ALLOWED_TITLE_PREFIXES)},
        )

    return PolicyResult(ok=True, fields={"title": text})


def validate_issue_title(text: str) -> PolicyResult:
    return _validate_human_title(text, kind="issue")


def validate_pr_title(text: str) -> PolicyResult:
    return _validate_human_title(text, kind="pr")


def enforce_issue_title(text: str) -> None:
    _raise(validate_issue_title(text))


def enforce_pr_title(text: str) -> None:
    _raise(validate_pr_title(text))


# ---------------------------------------------------------------------------
# Issue anchor validation
# ---------------------------------------------------------------------------


_BRANCH_ISSUE_RE = re.compile(r"\bissue[-/_](\d+)\b", re.IGNORECASE)
_BODY_ISSUE_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|관련)\s*#(\d+)",
    re.IGNORECASE,
)
_PLAIN_HASH_ISSUE_RE = re.compile(r"#(\d+)")


# repo work 가 아니라고 인정할 narrow 경우 — docs-only 같은 small chore
_NO_ISSUE_EXEMPT_PATHS: tuple = ("docs/", "README.md", "CLAUDE.md", "AGENTS.md")


@dataclass(frozen=True)
class IssueAnchorContext:
    branch: Optional[str] = None
    pr_body: Optional[str] = None
    issue_number_hint: Optional[int] = None
    is_docs_only: bool = False


def validate_issue_anchor(ctx: IssueAnchorContext) -> PolicyResult:
    """PR 또는 commit 작업에 issue anchor 가 붙어있는지.

    1. ``issue_number_hint`` 가 양수면 OK (caller 가 이미 알고 있음).
    2. branch name 에 ``issue-N`` 형식이 있으면 OK.
    3. pr_body 에 ``close #N``, ``refs #N`` 또는 단순 ``#N`` 이 있으면 OK.
    4. docs-only 변경이면 OK (caller 가 ``is_docs_only=True`` 로 표시).
    그 외에는 fail.
    """

    if ctx.issue_number_hint and ctx.issue_number_hint > 0:
        return PolicyResult(ok=True, fields={"issue_number": ctx.issue_number_hint})

    if ctx.is_docs_only:
        return PolicyResult(ok=True, fields={"docs_only": True})

    if ctx.branch:
        m = _BRANCH_ISSUE_RE.search(ctx.branch)
        if m:
            return PolicyResult(
                ok=True,
                fields={"issue_number": int(m.group(1)), "source": "branch"},
            )

    if ctx.pr_body:
        m = _BODY_ISSUE_RE.search(ctx.pr_body)
        if m:
            return PolicyResult(
                ok=True,
                fields={"issue_number": int(m.group(1)), "source": "pr_body"},
            )
        m = _PLAIN_HASH_ISSUE_RE.search(ctx.pr_body)
        if m:
            return PolicyResult(
                ok=True,
                fields={"issue_number": int(m.group(1)), "source": "pr_body_plain"},
            )

    return PolicyResult(
        ok=False,
        reason=REASON_ISSUE_REQUIRED_FOR_REPO_WORK,
        detail=(
            "no issue anchor found in branch name OR PR body. "
            "Create an issue first and reference it via `issue-<n>` in branch or "
            "`close #<n>` / `refs #<n>` in PR body."
        ),
        fields={
            "branch": ctx.branch,
            "has_pr_body": bool(ctx.pr_body),
        },
    )


def enforce_issue_anchor(ctx: IssueAnchorContext) -> None:
    _raise(validate_issue_anchor(ctx))


__all__ = (
    "ALLOWED_GITMOJI",
    "INITIAL_COMMIT_TITLE_EXACT",
    "InitialCommitDecision",
    "IssueAnchorContext",
    "PolicyResult",
    "PolicyViolation",
    "REASON_INITIAL_COMMIT_DETECTION_AMBIGUOUS",
    "REASON_INVALID_COMMIT_BODY_SECTIONS",
    "REASON_INVALID_COMMIT_GITMOJI",
    "REASON_INVALID_INITIAL_COMMIT_TITLE",
    "REASON_INVALID_ISSUE_TITLE",
    "REASON_INVALID_PR_TITLE",
    "REASON_ISSUE_REQUIRED_FOR_REPO_WORK",
    "REASON_TADA_OUTSIDE_INITIAL_COMMIT",
    "REQUIRED_SECTIONS",
    "enforce_commit_message",
    "enforce_issue_anchor",
    "enforce_issue_title",
    "enforce_pr_title",
    "is_initial_commit_context",
    "validate_commit_message",
    "validate_initial_commit_decision",
    "validate_issue_anchor",
    "validate_issue_title",
    "validate_pr_title",
)
