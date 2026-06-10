"""GitHub work-os audit record — G3.

The GitHub adapter writes one audit row per attempted action (whether
the action succeeded, was denied by policy, or was skipped because of
``dry_run``). The row format is **agent_ops_audit-compatible** so the
existing supervisor / status / Obsidian indexing pipeline picks it up
without bespoke wiring.

Compatibility with :mod:`agents.lifecycle.agent_ops_log.AgentOpsEntry`:

  * ``entry_id`` / ``session_id`` / ``action`` / ``autonomy_level`` /
    ``summary`` / ``reasoning`` / ``outcome`` / ``recorded_at`` /
    ``actor`` keys match exactly.
  * GitHub-specific fields ride along under stable extra keys so a
    reader that only knows the base shape can still ingest them via
    ``references`` (we stuff the GitHub URL set into the references
    list when present).

Secret hygiene:
- :func:`redact_secrets` is the **only** sanctioned way to strip
  tokens / Authorization headers from a payload before it lands in
  an audit row. Every public surface in :mod:`github_writer` runs
  detail strings through this function before constructing the
  audit.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Action kinds (free-form constants — caller can extend)
# ---------------------------------------------------------------------------


ACTION_GITHUB_ISSUE_COMMENT: str = "github_issue_comment"
ACTION_GITHUB_LABEL_ADD: str = "github_label_add"
ACTION_GITHUB_BRANCH_CREATE: str = "github_branch_create"
ACTION_GITHUB_COMMIT_CREATE: str = "github_commit_create"
ACTION_GITHUB_PR_DRAFT_CREATE: str = "github_pr_draft_create"
ACTION_GITHUB_PR_READY: str = "github_pr_ready"
ACTION_GITHUB_PUSH: str = "github_push"
ACTION_GITHUB_MERGE: str = "github_merge"
# Issue auto-create (P0-S end-to-end) — target repo 의 ISSUE_TEMPLATE 을
# 채워 새 issue 를 생성. issue 자체는 reversible (operator 가 close 가능)
# 이라 PR draft / branch create 와 동일한 L2 등급으로 매핑된다.
ACTION_GITHUB_ISSUE_CREATE: str = "github_issue_create"


# Outcome buckets
OUTCOME_DRY_RUN: str = "dry_run"
OUTCOME_OK: str = "ok"
OUTCOME_DENIED_BY_POLICY: str = "denied_by_policy"
OUTCOME_DENIED_PROTECTED_BRANCH: str = "denied_protected_branch"
OUTCOME_FAILED: str = "failed"


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


# Patterns that look like sensitive material — we never let these
# survive into an audit row. Order matters: more specific first.
_SECRET_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(authorization)\s*[:=]\s*\S+"), r"\1: <redacted>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), "Bearer <redacted>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{15,}\b"), "<redacted-gh-token>"),
    (re.compile(r"\bghu_[A-Za-z0-9]{15,}\b"), "<redacted-gh-user-token>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{15,}\b"), "<redacted-github-pat>"),
    (
        re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----", re.DOTALL),
        "<redacted-private-key>",
    ),
    # Generic high-entropy tokens prefixed with common keys.
    (
        re.compile(r"(?i)(token|api[_-]?key|secret|client[_-]?secret)\s*[:=]\s*[\"']?[A-Za-z0-9._\-/+]{16,}[\"']?"),
        r"\1=<redacted>",
    ),
)


def redact_secrets(value: Any, *, max_len: int = 800) -> Any:
    """Return *value* with token-shaped substrings replaced.

    * Strings: every secret pattern collapses to ``<redacted>`` /
      ``Bearer <redacted>`` etc. The result is also length-capped to
      *max_len* so a runaway exception message can't blow up the
      audit row.
    * Mappings: every value is redacted recursively. Keys named
      ``authorization`` / ``token`` / ``api_key`` / ``secret`` /
      ``private_key`` etc. (case-insensitive) collapse their value to
      ``<redacted>`` regardless of content.
    * Lists / tuples: redacted element-wise.
    * Other types: returned unchanged.
    """

    if isinstance(value, str):
        text = value
        for pattern, replacement in _SECRET_PATTERNS:
            text = pattern.sub(replacement, text)
        if len(text) > max_len:
            text = text[: max_len - 3] + "..."
        return text
    if isinstance(value, Mapping):
        out: dict = {}
        for key, sub in value.items():
            if _is_sensitive_key(str(key)):
                out[key] = "<redacted>"
                continue
            out[key] = redact_secrets(sub, max_len=max_len)
        return out
    if isinstance(value, (list, tuple)):
        cleaned = [redact_secrets(item, max_len=max_len) for item in value]
        if isinstance(value, tuple):
            return tuple(cleaned)
        return cleaned
    return value


_SENSITIVE_KEY_RE = re.compile(
    r"(authorization|access[_-]?token|api[_-]?key|client[_-]?secret|"
    r"private[_-]?key|password|x[-_]github[-_]token|\btoken\b|\bsecret\b)",
    re.IGNORECASE,
)


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(key or ""))


# ---------------------------------------------------------------------------
# Audit dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GithubWriteAudit:
    """One agent_ops_audit row describing a GitHub adapter action.

    The dataclass fields map 1:1 to keys in :meth:`as_payload`. The
    payload shape is a strict superset of
    :class:`agents.lifecycle.agent_ops_log.AgentOpsEntry`'s payload —
    every base key is present, and GitHub-specific fields are namespaced
    under ``github`` so a generic reader doesn't trip on them.
    """

    entry_id: str
    action: str
    actor_role: str
    autonomy_level: str
    policy_reason: str
    target_repo: Optional[str]
    issue_number: Optional[int]
    session_id: Optional[str]
    pr_number: Optional[int]
    branch: Optional[str]
    dry_run: bool
    outcome: str
    summary: str = ""
    references: Tuple[str, ...] = field(default_factory=tuple)
    job_id: Optional[str] = None
    decision_id: Optional[str] = None
    actor: str = "github-agent-workos"
    recorded_at: str = ""

    def as_payload(self) -> Mapping[str, Any]:
        """JSON-friendly dict matching agent_ops_audit shape."""

        return {
            # Base agent_ops_audit keys (matches AgentOpsEntry).
            "entry_id": self.entry_id,
            "session_id": self.session_id or "",
            "action": self.action,
            "autonomy_level": self.autonomy_level,
            "summary": self.summary,
            "reasoning": self.policy_reason,
            "outcome": self.outcome,
            "references": list(self.references),
            "topic_key": None,
            "job_id": self.job_id,
            "decision_id": self.decision_id,
            "actor": self.actor,
            "recorded_at": self.recorded_at,
            # GitHub-namespace extension — readers that don't know
            # about it can ignore the whole sub-object.
            "github": {
                "actor_role": self.actor_role,
                "target_repo": self.target_repo,
                "issue_number": self.issue_number,
                "pr_number": self.pr_number,
                "branch": self.branch,
                "dry_run": self.dry_run,
            },
        }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_github_audit_record(
    *,
    action: str,
    actor_role: str,
    autonomy_level: str,
    policy_reason: str,
    target_repo: Optional[str] = None,
    issue_number: Optional[int] = None,
    session_id: Optional[str] = None,
    pr_number: Optional[int] = None,
    branch: Optional[str] = None,
    dry_run: bool = True,
    outcome: str = OUTCOME_DRY_RUN,
    summary: str = "",
    references: Iterable[str] = (),
    job_id: Optional[str] = None,
    decision_id: Optional[str] = None,
    actor: str = "github-agent-workos",
    recorded_at: Optional[str] = None,
    entry_id: Optional[str] = None,
) -> GithubWriteAudit:
    """Compose a :class:`GithubWriteAudit` from the writer's args.

    All string inputs run through :func:`redact_secrets` before
    landing in the record so an accidental Authorization header in
    a caller-supplied detail can't poison the audit log.
    """

    when = recorded_at or _utc_now_iso()
    refs = tuple(
        redact_secrets(str(r))
        for r in references
        if isinstance(r, str) and str(r).strip()
    )
    return GithubWriteAudit(
        entry_id=entry_id or _new_entry_id(),
        action=str(action or ""),
        actor_role=str(actor_role or ""),
        autonomy_level=str(autonomy_level or ""),
        policy_reason=str(redact_secrets(policy_reason or "")),
        target_repo=_optional_str(target_repo),
        issue_number=_optional_int(issue_number),
        session_id=_optional_str(session_id),
        pr_number=_optional_int(pr_number),
        branch=_optional_str(branch),
        dry_run=bool(dry_run),
        outcome=str(outcome or ""),
        summary=str(redact_secrets(summary or "")),
        references=refs,
        job_id=_optional_str(job_id),
        decision_id=_optional_str(decision_id),
        actor=str(actor or "github-agent-workos"),
        recorded_at=when,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _new_entry_id() -> str:
    return uuid.uuid4().hex[:12]


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = (
    "ACTION_GITHUB_BRANCH_CREATE",
    "ACTION_GITHUB_COMMIT_CREATE",
    "ACTION_GITHUB_ISSUE_COMMENT",
    "ACTION_GITHUB_ISSUE_CREATE",
    "ACTION_GITHUB_LABEL_ADD",
    "ACTION_GITHUB_MERGE",
    "ACTION_GITHUB_PR_DRAFT_CREATE",
    "ACTION_GITHUB_PR_READY",
    "ACTION_GITHUB_PUSH",
    "GithubWriteAudit",
    "OUTCOME_DENIED_BY_POLICY",
    "OUTCOME_DENIED_PROTECTED_BRANCH",
    "OUTCOME_DRY_RUN",
    "OUTCOME_FAILED",
    "OUTCOME_OK",
    "build_github_audit_record",
    "redact_secrets",
)
