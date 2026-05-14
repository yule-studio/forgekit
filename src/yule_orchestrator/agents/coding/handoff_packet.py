"""Coding-capable handoff packet — P0-H stage 2 (#140).

When the gateway detects a coding-capable request, it bundles the
context that the tech-lead (or coding executor) needs into a single
:class:`CodingHandoffPacket` envelope. This is the canonical shape
for *all* handoffs from the gateway to coding surfaces.

Composition:

  * ``canonical_request`` — the user's request, normalized (typo
    correction via P0-F canonicalizer + URL stripped).
  * ``github_target`` — parsed GitHub URL when present (P0-H commit 2).
  * ``repo_contract_summary`` — one-line summary of repo conventions
    (P0-H commit 3 RepoContract.summary_line).
  * ``mode`` — work_mode (autonomous_merge / approval_required).
  * ``topology`` — single_repo / multi_repo.
  * ``scope_mode`` — single_scope / full_stack_single_repo /
    layer_scoped / cross_repo_program.
  * ``tracking_mode`` — how the work is tracked (issue / PR /
    standalone). Derived from github_target.kind.
  * ``existing_session_match`` — when the gateway found an existing
    session anchored to the same target, its session_id.
  * ``next_action`` — the immediate next step (open_issue /
    open_pr_branch / continue_existing / ask_user).

The packet is purely descriptive. The coding executor decides what
to do with it; tests pin the packet's shape, not the executor's
behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


# Tracking mode constants — describe how the work item is anchored
# in GitHub. Derived from GithubTarget.kind, but the caller may
# override (e.g. when the user pastes a repo root URL but wants
# issue-tracked work).
TRACKING_ISSUE = "issue"
TRACKING_PR = "pull_request"
TRACKING_COMMIT = "commit"
TRACKING_COMPARE = "compare"
TRACKING_BRANCH = "branch"
TRACKING_REPO_ROOT = "repo_root"
TRACKING_STANDALONE = "standalone"  # no GitHub link

TRACKING_MODES = (
    TRACKING_ISSUE,
    TRACKING_PR,
    TRACKING_COMMIT,
    TRACKING_COMPARE,
    TRACKING_BRANCH,
    TRACKING_REPO_ROOT,
    TRACKING_STANDALONE,
)


# Next-action constants — what the gateway recommends right after handoff.
NEXT_OPEN_ISSUE = "open_issue"
NEXT_OPEN_PR_BRANCH = "open_pr_branch"
NEXT_CONTINUE_EXISTING = "continue_existing"
NEXT_ASK_USER = "ask_user"
NEXT_ANALYZE_PR = "analyze_pr"  # user pasted a PR URL — review/analyze
NEXT_ANALYZE_COMMIT = "analyze_commit"  # user pasted a commit URL
NEXT_ANALYZE_COMPARE = "analyze_compare"

NEXT_ACTIONS = (
    NEXT_OPEN_ISSUE,
    NEXT_OPEN_PR_BRANCH,
    NEXT_CONTINUE_EXISTING,
    NEXT_ASK_USER,
    NEXT_ANALYZE_PR,
    NEXT_ANALYZE_COMMIT,
    NEXT_ANALYZE_COMPARE,
)


@dataclass(frozen=True)
class CodingHandoffPacket:
    """All the context the coding executor needs from the gateway.

    Field shape mirrors the user's stage-2 request literally. The
    packet is meant to be cheap to round-trip via JSON (``to_dict``)
    so it can land in ``session.extra["coding_handoff_packet"]`` or
    be posted as a structured PR Audit block.
    """

    canonical_request: str
    github_target: Optional[Mapping[str, Any]] = None  # GithubTarget.to_dict()
    repo_contract_summary: Optional[str] = None
    repo_contract: Optional[Mapping[str, Any]] = None  # full RepoContract.to_dict()
    mode: Optional[str] = None  # autonomous_merge | approval_required
    topology: Optional[str] = None  # single_repo | multi_repo
    scope_mode: Optional[str] = None  # single_scope | full_stack | layer | cross_repo
    tracking_mode: str = TRACKING_STANDALONE
    existing_session_match: Optional[str] = None  # session_id
    next_action: str = NEXT_ASK_USER
    notes: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "canonical_request": self.canonical_request,
            "github_target": dict(self.github_target) if self.github_target else None,
            "repo_contract_summary": self.repo_contract_summary,
            "repo_contract": dict(self.repo_contract) if self.repo_contract else None,
            "mode": self.mode,
            "topology": self.topology,
            "scope_mode": self.scope_mode,
            "tracking_mode": self.tracking_mode,
            "existing_session_match": self.existing_session_match,
            "next_action": self.next_action,
            "notes": dict(self.notes) if self.notes else {},
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CodingHandoffPacket":
        return cls(
            canonical_request=str(payload.get("canonical_request") or ""),
            github_target=_coerce_dict(payload.get("github_target")),
            repo_contract_summary=_coerce_optional_str(
                payload.get("repo_contract_summary")
            ),
            repo_contract=_coerce_dict(payload.get("repo_contract")),
            mode=_coerce_optional_str(payload.get("mode")),
            topology=_coerce_optional_str(payload.get("topology")),
            scope_mode=_coerce_optional_str(payload.get("scope_mode")),
            tracking_mode=_coerce_str(payload.get("tracking_mode"), TRACKING_STANDALONE),
            existing_session_match=_coerce_optional_str(
                payload.get("existing_session_match")
            ),
            next_action=_coerce_str(payload.get("next_action"), NEXT_ASK_USER),
            notes=_coerce_dict(payload.get("notes")) or {},
        )

    def summary_line(self) -> str:
        """One-line human summary for status / Discord echo."""

        parts: list[str] = [f"📦 {self.tracking_mode}"]
        if self.github_target:
            gt = self.github_target
            tag = f"{gt.get('owner')}/{gt.get('repo')}"
            if gt.get("number") is not None:
                tag += f"#{gt['number']}"
            elif gt.get("sha"):
                tag += f"@{str(gt['sha'])[:7]}"
            parts.append(tag)
        if self.mode:
            parts.append(f"mode={self.mode}")
        if self.topology:
            parts.append(f"topology={self.topology}")
        if self.scope_mode:
            parts.append(f"scope={self.scope_mode}")
        if self.existing_session_match:
            parts.append(f"resume:{self.existing_session_match}")
        parts.append(f"→ {self.next_action}")
        return " · ".join(parts)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_coding_handoff_packet(
    *,
    canonical_request: str,
    github_target: Any = None,  # GithubTarget or dict or None
    repo_contract: Any = None,  # RepoContract or dict or None
    work_mode: Optional[str] = None,
    topology: Optional[str] = None,
    scope: Optional[str] = None,
    existing_session_id: Optional[str] = None,
    next_action_override: Optional[str] = None,
    notes: Optional[Mapping[str, Any]] = None,
) -> CodingHandoffPacket:
    """Compose the packet from the gateway's discovered context.

    *github_target* / *repo_contract* may be the rich dataclasses
    (with ``to_dict``) or already serialized dicts. The builder
    accepts either to avoid forcing import order on callers.

    *next_action_override* lets the caller short-circuit the
    derivation rule (e.g. caller already knows there's an existing
    session ID and wants ``continue_existing``).
    """

    target_dict = _normalize_target(github_target)
    contract_dict = _normalize_contract(repo_contract)
    summary = _contract_summary(contract_dict)
    tracking_mode = _derive_tracking_mode(target_dict)
    next_action = next_action_override or _derive_next_action(
        target_dict=target_dict,
        tracking_mode=tracking_mode,
        existing_session_id=existing_session_id,
    )
    return CodingHandoffPacket(
        canonical_request=canonical_request or "",
        github_target=target_dict,
        repo_contract_summary=summary,
        repo_contract=contract_dict,
        mode=work_mode,
        topology=topology,
        scope_mode=scope,
        tracking_mode=tracking_mode,
        existing_session_match=existing_session_id,
        next_action=next_action,
        notes=dict(notes or {}),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _normalize_target(target: Any) -> Optional[Mapping[str, Any]]:
    if target is None:
        return None
    if isinstance(target, Mapping):
        return dict(target) if target else None
    to_dict = getattr(target, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        return dict(result) if result else None
    return None


def _normalize_contract(contract: Any) -> Optional[Mapping[str, Any]]:
    if contract is None:
        return None
    if isinstance(contract, Mapping):
        return dict(contract) if contract else None
    to_dict = getattr(contract, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        return dict(result) if result else None
    return None


def _contract_summary(contract: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not contract:
        return None
    # Prefer the rich summary_line method if the source dataclass was
    # passed in via __init__'s normalization; otherwise build a
    # minimal one from the dict.
    owner = contract.get("owner") or ""
    repo = contract.get("repo") or ""
    if contract.get("fallback"):
        reason = contract.get("failure_mode") or "no_backend"
        return f"⚠️ {owner}/{repo} — RepoContract fallback ({reason})"
    parts: list[str] = []
    if contract.get("issue_templates"):
        parts.append(f"issue_templates={len(contract['issue_templates'])}")
    if contract.get("pr_templates"):
        parts.append(f"pr_templates={len(contract['pr_templates'])}")
    if contract.get("contributing"):
        parts.append("contributing")
    if contract.get("codeowners"):
        parts.append("codeowners")
    if contract.get("workflows"):
        parts.append(f"workflows={len(contract['workflows'])}")
    backend = contract.get("backend")
    tag = f" [{backend}]" if backend else ""
    detail = ", ".join(parts) if parts else "no convention files found"
    return f"✅ {owner}/{repo} — {detail}{tag}"


def _derive_tracking_mode(target: Optional[Mapping[str, Any]]) -> str:
    if not target:
        return TRACKING_STANDALONE
    kind = target.get("kind")
    if kind == "issue":
        return TRACKING_ISSUE
    if kind == "pull_request":
        return TRACKING_PR
    if kind == "commit":
        return TRACKING_COMMIT
    if kind == "compare":
        return TRACKING_COMPARE
    if kind in ("tree", "blob"):
        return TRACKING_BRANCH
    if kind == "repo":
        return TRACKING_REPO_ROOT
    return TRACKING_STANDALONE


def _derive_next_action(
    *,
    target_dict: Optional[Mapping[str, Any]],
    tracking_mode: str,
    existing_session_id: Optional[str],
) -> str:
    if existing_session_id:
        return NEXT_CONTINUE_EXISTING
    if tracking_mode == TRACKING_PR:
        return NEXT_ANALYZE_PR
    if tracking_mode == TRACKING_COMMIT:
        return NEXT_ANALYZE_COMMIT
    if tracking_mode == TRACKING_COMPARE:
        return NEXT_ANALYZE_COMPARE
    if tracking_mode == TRACKING_ISSUE:
        # Existing issue → continue (resume) when no session id is
        # known; here we treat it as "next: open PR branch off this
        # issue", since the user came in *with* an issue URL.
        return NEXT_OPEN_PR_BRANCH
    if tracking_mode in (TRACKING_REPO_ROOT, TRACKING_BRANCH):
        return NEXT_OPEN_ISSUE
    return NEXT_ASK_USER


def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_str(value: Any, default: str) -> str:
    text = _coerce_optional_str(value)
    return text or default


def _coerce_dict(value: Any) -> Optional[Mapping[str, Any]]:
    if not value:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    return None


__all__ = (
    "CodingHandoffPacket",
    "NEXT_ACTIONS",
    "NEXT_ANALYZE_COMMIT",
    "NEXT_ANALYZE_COMPARE",
    "NEXT_ANALYZE_PR",
    "NEXT_ASK_USER",
    "NEXT_CONTINUE_EXISTING",
    "NEXT_OPEN_ISSUE",
    "NEXT_OPEN_PR_BRANCH",
    "TRACKING_BRANCH",
    "TRACKING_COMMIT",
    "TRACKING_COMPARE",
    "TRACKING_ISSUE",
    "TRACKING_MODES",
    "TRACKING_PR",
    "TRACKING_REPO_ROOT",
    "TRACKING_STANDALONE",
    "build_coding_handoff_packet",
)
