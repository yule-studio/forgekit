"""Merge receipt — proof a PR was merged under the governance chain with an identity trail.

The decision lane decides; the engineer executes; this is the artifact that closes the
loop at the **merge** boundary. It binds one merged PR to the tech-lead decision /
approval that authorized it and to the **agent identity trail** (commit trailers), so an
audit can answer "which PR closed which decision, merged by whom, with what CI/QA, under
what approval".

Anti-fake (:func:`validate_merge_receipt`) — no fake merge:

* a ``merged`` outcome REQUIRES a registry-known executor, approval metadata, an identity
  trail (commit trailers), and a passing CI status — a merge claimed without those is
  rejected;
* a non-merged (blocked) receipt MUST carry blocking reasons and MUST NOT carry an
  identity trail (no fabricated approval on a blocked merge).

So a "merged" receipt can never assert an approval/CI state that did not happen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from forgekit_config.identity.registry import is_known

# merge outcomes
MERGE_MERGED = "merged"
MERGE_BLOCKED = "blocked"
MERGE_AWAITING = "awaiting"        # nothing to merge yet (honest empty)
MERGE_OUTCOMES: Tuple[str, ...] = (MERGE_MERGED, MERGE_BLOCKED, MERGE_AWAITING)

# CI/QA states that count as green.
CI_PASS = "passing"
QA_PASS = "passing"


def _blank(s: str) -> bool:
    return not (s or "").strip()


@dataclass(frozen=True)
class MergeReceipt:
    """Governance proof that one PR was merged (or blocked) under the approval chain."""

    pr_ref: str                              # PR number / url
    issue_ref: str = ""                      # closed issue
    branch: str = ""
    base: str = "main"
    merge_commit: str = ""
    executor: str = ""                       # who merged — canonical identity
    decision_ref: str = ""                   # TechLeadDecision.decision_id this closes
    approval_metadata: str = ""              # the verdict's approval metadata
    commit_trailers: Tuple[str, ...] = ()    # Forgekit-Agent/Approval/... identity trail
    ci_status: str = ""                      # passing / failing / pending
    qa_status: str = ""                      # passing / ... (optional)
    outcome: str = MERGE_AWAITING
    blocking_reasons: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "pr_ref": self.pr_ref, "issue_ref": self.issue_ref, "branch": self.branch,
            "base": self.base, "merge_commit": self.merge_commit, "executor": self.executor,
            "decision_ref": self.decision_ref, "approval_metadata": self.approval_metadata,
            "commit_trailers": list(self.commit_trailers), "ci_status": self.ci_status,
            "qa_status": self.qa_status, "outcome": self.outcome,
            "blocking_reasons": list(self.blocking_reasons),
        }

    def lines(self) -> Tuple[str, ...]:
        status = ("머지됨" if self.outcome == MERGE_MERGED else "차단됨"
                  if self.outcome == MERGE_BLOCKED else "대기")
        out = [
            f"merge receipt — {status} (PR {self.pr_ref})",
            f"- closes : issue {self.issue_ref or '-'} / decision {self.decision_ref or '-'}",
            f"- merge  : {self.branch or '-'} → {self.base} @ {self.merge_commit or '-'}",
            f"- by     : {self.executor or '-'}  ci={self.ci_status or '-'} qa={self.qa_status or '-'}",
            f"- approval: {self.approval_metadata or '-'}",
        ]
        if self.outcome != MERGE_MERGED and self.blocking_reasons:
            out.append(f"- blocked : {'; '.join(self.blocking_reasons)}")
        return tuple(out)


def validate_merge_receipt(receipt: MergeReceipt) -> Tuple[str, ...]:
    """Reject a fake merge receipt. ``()`` = its claims match a real, approved, green merge."""

    v = []
    if receipt.outcome not in MERGE_OUTCOMES:
        v.append(f"merge: outcome '{receipt.outcome}' 알 수 없음")
    if receipt.outcome != MERGE_AWAITING and _blank(receipt.pr_ref):
        v.append("merge: pr_ref 비어 있음")

    if receipt.outcome == MERGE_MERGED:
        if not is_known(receipt.executor):
            v.append(f"merge: executor '{receipt.executor}' 레지스트리에 없음")
        if _blank(receipt.approval_metadata):
            v.append("merge: merged 인데 approval_metadata 없음 — fake 승인")
        if not receipt.commit_trailers:
            v.append("merge: merged 인데 identity trail(commit trailer) 없음")
        if receipt.ci_status != CI_PASS:
            v.append(f"merge: merged 인데 ci_status='{receipt.ci_status}' (passing 아님) — fake green merge")
        if _blank(receipt.merge_commit):
            v.append("merge: merged 인데 merge_commit 없음")
    else:
        if receipt.outcome == MERGE_BLOCKED and not receipt.blocking_reasons:
            v.append("merge: blocked 인데 blocking_reasons 없음 — 침묵 거부")
        if receipt.commit_trailers:
            v.append("merge: 미머지인데 identity trail 존재 — fake approval metadata")
    return tuple(v)


__all__ = (
    "MERGE_MERGED", "MERGE_BLOCKED", "MERGE_AWAITING", "MERGE_OUTCOMES",
    "CI_PASS", "QA_PASS", "MergeReceipt", "validate_merge_receipt",
)
