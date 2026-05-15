"""Runtime governance policy gates — P0-T 시리즈.

본 패키지는 engineering-agent 가 실제 코딩 작업을 굴릴 때 무너지지 말아야
할 hard rail 을 한 자리에 모은다:

  * branch / commit / PR / tag — git workflow 정책
  * vault / curated note / inbox / hub linkage — 지식 정책
  * retrieval eval — 평가 정책
  * post-test hardening — 성능 개선 / 고도화 작업의 opening criteria

각 정책은 pure 함수 — 호출자가 결과 (allow/deny + 사유 + 권고) 를 읽어
실제 게이트로 사용. 본 패키지 안에서 storage I/O / network 호출 없음.

상세 docs: `docs/engineering-agent-governance.md`, `docs/memory.md`,
`docs/github-agent-workos.md` §1.5.
"""

from .runtime_policy import (
    BRANCH_PREFIXES,
    BranchPolicyResult,
    CURATED_REQUIRED_FRONTMATTER,
    CURATED_REQUIRED_SECTIONS,
    CuratedNoteValidationResult,
    HARDENING_OPENING_CRITERIA,
    HardeningOpeningDecision,
    INBOX_PATH_PREFIX,
    MIN_RETRIEVAL_EVAL_QUESTIONS,
    PRBodyValidationResult,
    PR_REQUIRED_SECTIONS,
    RETRIEVAL_EVAL_REQUIRED_KEYS,
    RETRIEVAL_EVAL_TOP_K,
    RetrievalEvalEntryResult,
    TARGET_RETRIEVAL_EVAL_QUESTIONS,
    decide_hardening_opening,
    derive_standard_branch_name,
    detect_broken_links,
    detect_orphan_note,
    is_inbox_path,
    validate_branch_name,
    validate_curated_note,
    validate_pr_body,
    validate_retrieval_eval_entry,
    validate_retrieval_eval_fixture,
)


__all__ = (
    "BRANCH_PREFIXES",
    "BranchPolicyResult",
    "CURATED_REQUIRED_FRONTMATTER",
    "CURATED_REQUIRED_SECTIONS",
    "CuratedNoteValidationResult",
    "HARDENING_OPENING_CRITERIA",
    "HardeningOpeningDecision",
    "INBOX_PATH_PREFIX",
    "MIN_RETRIEVAL_EVAL_QUESTIONS",
    "PRBodyValidationResult",
    "PR_REQUIRED_SECTIONS",
    "RETRIEVAL_EVAL_REQUIRED_KEYS",
    "RETRIEVAL_EVAL_TOP_K",
    "RetrievalEvalEntryResult",
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
