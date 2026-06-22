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

Honest boundary (see ``docs/forgekit-goal-roadmap.md`` GW2): this enforces commit
*message* shape (gitmoji + 3 sections) + the Co-Authored-By ban. Richer trailer
semantics (approval-metadata / agent-identity binding) are a declared follow-up
seam (GW2-B), NOT silently claimed here.

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

# A NUL/RS framed git log format so multi-line bodies survive parsing intact.
_GIT_FORMAT = "%H%x00%B%x1e"


@dataclass(frozen=True)
class CommitViolation:
    sha: str
    reason: str
    detail: str


def _load_validator() -> Callable[..., object]:
    """Import the shared commit-message policy (installed in CI via ``pip -e .``)."""

    src = REPO_ROOT / "apps" / "engineering-agent" / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from yule_engineering.agents.governance.repo_write_policy import (  # type: ignore
        validate_commit_message,
    )

    return validate_commit_message


def check_commit_messages(
    commits: Sequence[Tuple[str, str]],
    *,
    validate: Optional[Callable[..., object]] = None,
    is_initial: bool = False,
) -> List[CommitViolation]:
    """Pure core: validate ``(sha, message)`` pairs, return violations.

    ``validate`` is injectable so tests don't need the heavy import. The default
    loads the real shared policy.
    """

    if validate is None:
        validate = _load_validator()

    violations: List[CommitViolation] = []
    for sha, message in commits:
        if _CO_AUTHORED_BY.search(message or ""):
            violations.append(
                CommitViolation(sha, REASON_CO_AUTHORED_BY, "remove the Co-Authored-By trailer")
            )
        result = validate(message, is_initial=is_initial)
        if not getattr(result, "ok", False):
            reason = getattr(result, "reason", "invalid_commit_message")
            detail = getattr(result, "detail", "") or ""
            violations.append(CommitViolation(sha, str(reason), str(detail)))
    return violations


def _git_commits_in_range(base: str, head: str) -> List[Tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", "--no-merges", f"--format={_GIT_FORMAT}", f"{base}..{head}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    commits: List[Tuple[str, str]] = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, body = record.partition("\x00")
        commits.append((sha.strip(), body.strip("\n")))
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

    violations = check_commit_messages(commits)
    if not violations:
        print(f"ci_check_commit_messages: {len(commits)} commit(s) OK")
        return 0

    print("─" * 60, file=sys.stderr)
    print(f"commit governance: {len(violations)} violation(s)", file=sys.stderr)
    for v in violations:
        print(f"  {v.sha[:12]}  {v.reason}: {v.detail}", file=sys.stderr)
    print("  SSoT: policies/reference/COMMIT_CONVENTION.md", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
