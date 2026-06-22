#!/usr/bin/env python3
"""GW2 — CI commit-governance guard.

The local ``commit-msg`` hook (``scripts/validate_commit_msg.py``) is opt-in: it
only runs for contributors who symlinked it into ``.git/hooks``. Nothing enforced
the commit convention on a PR, so a non-conforming commit could merge. This guard
closes that gap: it validates **every commit introduced by a PR** in CI, reusing
the SAME policy (``repo_write_policy.validate_commit_message``) — no duplicate
rule set — and additionally bans the ``Co-Authored-By`` trailer (project rule:
commits are single-authored; attribution is via git author identity, not a
trailer).

GW2-B (this extension): agent-identity binding. A commit MAY carry a
``Forgekit-Agent: <agent-id>`` trailer (and optionally ``Approved-By: <agent-id>``
for approval-metadata). When such a trailer is present, the claimed id MUST resolve
via ``forgekit_config.identity.is_known`` (a known canonical id or alias). An
unknown claim FAILS. A commit with NO such trailer is treated as operator/human and
PASSES — this is purely additive and never false-positives on existing commits.
The git author email may be cross-checked against ``git_identity_for`` and a
*warning* (never a hard fail) is recorded on mismatch, to stay safe.

Usage:
  ci_check_commit_messages.py <base-ref> <head-ref>   # explicit range
  ci_check_commit_messages.py                         # infer from GITHUB_BASE_REF
Exit code 0 = all commits pass; 1 = at least one violation; 2 = usage/env error.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

# Co-Authored-By is banned project-wide; attribution is the git author identity.
_CO_AUTHORED_BY = re.compile(r"(?im)^\s*co-authored-by\s*:")
REASON_CO_AUTHORED_BY = "co_authored_by_forbidden"

# GW2-B — agent-identity binding trailers (case-insensitive, line-anchored).
# A commit MAY claim an agent identity via these trailers; when present the id must
# resolve in the forgekit identity registry. Absence == operator/human == pass.
_FORGEKIT_AGENT = re.compile(r"(?im)^\s*forgekit-agent\s*:\s*(?P<id>.+?)\s*$")
_APPROVED_BY = re.compile(r"(?im)^\s*approved-by\s*:\s*(?P<id>.+?)\s*$")
REASON_UNKNOWN_AGENT = "unknown_forgekit_agent"
REASON_UNKNOWN_APPROVER = "unknown_approved_by_agent"

# A NUL/RS framed git log format so multi-line bodies survive parsing intact.
# Body + author email (for the optional, warn-only author cross-check).
_GIT_FORMAT = "%H%x00%B%x00%ae%x1e"


@dataclass(frozen=True)
class CommitViolation:
    sha: str
    reason: str
    detail: str
    severity: str = "error"  # "error" fails CI; "warning" is reported but non-blocking


def _load_validator() -> Callable[..., object]:
    """Import the shared commit-message policy (installed in CI via ``pip -e .``)."""

    src = REPO_ROOT / "apps" / "engineering-agent" / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from yule_engineering.agents.governance.repo_write_policy import (  # type: ignore
        validate_commit_message,
    )

    return validate_commit_message


def _ensure_identity_paths() -> None:
    """Make ``forgekit_config.identity`` importable (installed in CI via ``pip -e .``)."""

    cfg_src = REPO_ROOT / "packages" / "forgekit-config" / "src"
    if cfg_src.is_dir() and str(cfg_src) not in sys.path:
        sys.path.insert(0, str(cfg_src))


def _load_identity() -> Tuple[Callable[[str], bool], Callable[[str], dict]]:
    """Import the identity registry seam (``is_known`` + ``git_identity_for``)."""

    _ensure_identity_paths()
    from forgekit_config.identity.registry import (  # type: ignore
        git_identity_for,
        is_known,
    )

    return is_known, git_identity_for


def _parse_agent_claims(message: str) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(forgekit_agent_id, approved_by_id)`` claimed in trailers (or None)."""

    text = message or ""
    fa = _FORGEKIT_AGENT.search(text)
    ab = _APPROVED_BY.search(text)
    return (
        fa.group("id").strip() if fa else None,
        ab.group("id").strip() if ab else None,
    )


def check_identity_binding(
    commits: Sequence[Tuple[str, ...]],
    *,
    is_known: Optional[Callable[[str], bool]] = None,
    git_identity_for: Optional[Callable[[str], dict]] = None,
) -> List[CommitViolation]:
    """Pure core (GW2-B): enforce the agent-identity trailer binding.

    Each entry is ``(sha, message)`` or ``(sha, message, author_email)``. Rules:

    * No ``Forgekit-Agent`` / ``Approved-By`` trailer → operator/human → PASS.
    * Trailer present but the claimed id is NOT ``is_known`` → FAIL (hard).
    * Trailer present, id known, and an author email is available → optionally
      cross-check against ``git_identity_for(id)['email']``; a mismatch is recorded
      as a *warning* violation (``severity='warning'``) — never a hard fail — so a
      legitimate commit is never blocked on email alone.

    Both seams are injectable so tests need no heavy import / git.
    """

    if is_known is None or git_identity_for is None:
        loaded_known, loaded_git = _load_identity()
        is_known = is_known or loaded_known
        git_identity_for = git_identity_for or loaded_git

    violations: List[CommitViolation] = []
    for entry in commits:
        sha = entry[0]
        message = entry[1] if len(entry) > 1 else ""
        author_email = entry[2] if len(entry) > 2 else None

        agent_id, approver_id = _parse_agent_claims(message)

        if agent_id is not None and not is_known(agent_id):
            violations.append(
                CommitViolation(
                    sha,
                    REASON_UNKNOWN_AGENT,
                    f"Forgekit-Agent '{agent_id}' is not a known forgekit identity",
                )
            )
        if approver_id is not None and not is_known(approver_id):
            violations.append(
                CommitViolation(
                    sha,
                    REASON_UNKNOWN_APPROVER,
                    f"Approved-By '{approver_id}' is not a known forgekit identity",
                )
            )

        # warn-only author cross-check for a known claimed agent.
        if agent_id is not None and is_known(agent_id) and author_email:
            expected = str(git_identity_for(agent_id).get("email", "")).strip()
            if expected and author_email.strip().lower() != expected.lower():
                violations.append(
                    CommitViolation(
                        sha,
                        "agent_author_email_mismatch",
                        f"author '{author_email}' != expected '{expected}' "
                        f"for '{agent_id}' (warning, not blocking)",
                        severity="warning",
                    )
                )
    return violations


def check_commit_messages(
    commits: Sequence[Tuple[str, ...]],
    *,
    validate: Optional[Callable[..., object]] = None,
    is_initial: bool = False,
    is_known: Optional[Callable[[str], bool]] = None,
    git_identity_for: Optional[Callable[[str], dict]] = None,
    check_identity: bool = True,
) -> List[CommitViolation]:
    """Pure core: validate ``(sha, message[, author_email])`` entries.

    Runs (1) the Co-Authored-By ban, (2) the shared message policy (gitmoji + 3
    sections), and (3) the GW2-B agent-identity trailer binding. ``validate`` and
    the identity seams are injectable so tests don't need the heavy imports.

    Set ``check_identity=False`` to run message-only checks (e.g. when the identity
    package is intentionally unavailable). The identity binding is purely additive:
    a commit with no agent trailer always passes it.
    """

    if validate is None:
        validate = _load_validator()

    violations: List[CommitViolation] = []
    for entry in commits:
        sha = entry[0]
        message = entry[1] if len(entry) > 1 else ""
        if _CO_AUTHORED_BY.search(message or ""):
            violations.append(
                CommitViolation(sha, REASON_CO_AUTHORED_BY, "remove the Co-Authored-By trailer")
            )
        result = validate(message, is_initial=is_initial)
        if not getattr(result, "ok", False):
            reason = getattr(result, "reason", "invalid_commit_message")
            detail = getattr(result, "detail", "") or ""
            violations.append(CommitViolation(sha, str(reason), str(detail)))

    if check_identity:
        violations.extend(
            check_identity_binding(
                commits, is_known=is_known, git_identity_for=git_identity_for
            )
        )
    return violations


def _git_commits_in_range(base: str, head: str) -> List[Tuple[str, str, str]]:
    out = subprocess.run(
        ["git", "log", "--no-merges", f"--format={_GIT_FORMAT}", f"{base}..{head}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    commits: List[Tuple[str, str, str]] = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, rest = record.partition("\x00")
        body, _, author_email = rest.rpartition("\x00")
        commits.append((sha.strip(), body.strip("\n"), author_email.strip()))
    return commits


def _resolve_range(argv: Sequence[str]) -> Optional[Tuple[str, str]]:
    if len(argv) >= 3:
        return argv[1], argv[2]
    base_ref = (os.environ.get("GITHUB_BASE_REF") or "").strip()
    if base_ref:
        return f"origin/{base_ref}", "HEAD"
    return None


def main(argv: Sequence[str]) -> int:
    rng = _resolve_range(argv)
    if rng is None:
        print(
            "ci_check_commit_messages: no range — pass <base> <head> or set GITHUB_BASE_REF",
            file=sys.stderr,
        )
        return 2
    base, head = rng
    try:
        commits = _git_commits_in_range(base, head)
    except subprocess.CalledProcessError as exc:
        print(f"ci_check_commit_messages: git log failed: {exc.stderr}", file=sys.stderr)
        return 2

    if not commits:
        print(f"ci_check_commit_messages: no commits in {base}..{head} — nothing to check")
        return 0

    # Identity binding is additive; skip it gracefully if the package is absent.
    check_identity = True
    try:
        _load_identity()
    except Exception as exc:  # noqa: BLE001 — env without forgekit_config
        print(
            f"ci_check_commit_messages: identity registry unavailable, "
            f"skipping GW2-B binding ({exc})",
            file=sys.stderr,
        )
        check_identity = False

    violations = check_commit_messages(commits, check_identity=check_identity)
    errors = [v for v in violations if v.severity != "warning"]
    warnings = [v for v in violations if v.severity == "warning"]

    for v in warnings:
        print(f"  warning {v.sha[:12]}  {v.reason}: {v.detail}", file=sys.stderr)

    if not errors:
        print(f"ci_check_commit_messages: {len(commits)} commit(s) OK")
        return 0

    print("─" * 60, file=sys.stderr)
    print(f"commit governance: {len(errors)} violation(s)", file=sys.stderr)
    for v in errors:
        print(f"  {v.sha[:12]}  {v.reason}: {v.detail}", file=sys.stderr)
    print("  SSoT: policies/reference/COMMIT_CONVENTION.md", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
