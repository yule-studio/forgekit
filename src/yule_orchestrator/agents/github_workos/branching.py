"""Branch name policy — G3.

Picks a branch name for a triage plan that:

  * Encodes the originating role (``primary_role``) so multi-role
    parallel work doesn't trip over each other.
  * Carries the source identifier (issue number or Discord session id)
    so a reviewer can backtrack from a branch to the originating
    request without grepping commit messages.
  * Ascii-safe — Korean / emoji / runs of punctuation collapse into
    short hyphenated tokens so ``git branch`` stays readable on every
    OS.
  * Refuses to ever target a protected branch — the writer gates on
    :func:`is_protected_branch` before any ref creation.
  * Has a stable collision-suffix hook (``-2``, ``-3``, …) so a retry
    that hits an existing branch can pick a fresh name without the
    caller doing manual slug arithmetic.

Pure-Python — no GitHub client, no SQLite. The :class:`TriagePlanLike`
Protocol is the only G2 dependency, and it's structural so a partial
G2 install (or a test fake) drops in cleanly.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Optional, Protocol, Tuple


# ---------------------------------------------------------------------------
# Triage-plan Protocol — the loose G2 contract this module reads from
# ---------------------------------------------------------------------------


class TriagePlanLike(Protocol):
    """Structural typing for a G2-produced triage plan.

    Only the fields branching needs are listed. G2 may carry many
    more, but a structural typecheck keeps this module decoupled
    from G2's concrete dataclass.
    """

    issue_number: Optional[int]
    session_id: Optional[str]
    title: str
    primary_role: str
    source: str  # "github" | "discord"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


PROTECTED_BRANCHES: Tuple[str, ...] = (
    "main",
    "master",
    "develop",
    "development",
    "production",
    "prod",
    "release",
    "staging",
    "trunk",
    "default",
)

# Branch slugs cap at 60 chars so a full ``agent/<role>/issue-<n>-<slug>``
# stays under git's reasonable 200-char branch limit on every platform
# (Windows path-length is the tightest constraint we care about).
DEFAULT_SLUG_MAX_LEN: int = 60

# Maximum consecutive collision suffix attempts. Past this we raise so
# the caller surfaces "couldn't pick a branch name" instead of looping.
MAX_COLLISION_ATTEMPTS: int = 50


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


_SLUG_TOKEN_RE = re.compile(r"[^a-z0-9가-힣]+")
_HANGUL_RE = re.compile(r"[가-힣]+")
_TRAILING_HYPHEN_RE = re.compile(r"-+$")
_LEADING_HYPHEN_RE = re.compile(r"^-+")


def _strip_hangul_for_branch(text: str) -> str:
    """Replace runs of hangul characters with a short ascii marker.

    Branches need to be ascii-safe on every OS, so Korean characters
    are collapsed to ``ko`` so the slug still has *some* signal even
    after stripping. Numeric / english tokens around the hangul are
    preserved.
    """

    return _HANGUL_RE.sub("ko", text)


def slugify_for_branch(value: str, *, max_chars: int = DEFAULT_SLUG_MAX_LEN) -> str:
    """Return a lowercase, ascii, hyphen-separated slug suitable for a branch.

    Empty / whitespace-only / pure-symbol input returns ``""`` — the
    caller decides how to fall back (typically to a session/issue id).
    """

    if value is None:
        return ""
    # Hangul replacement runs *before* NFKD because NFKD decomposes
    # 통합 into a sequence of jamo characters (U+1100..U+1175) that
    # fall outside the [가-힣] precomposed-syllable range; replacing
    # first ensures the "ko" fallback survives the ascii cast.
    text = str(value).strip()
    if not text:
        return ""
    text = _strip_hangul_for_branch(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.encode("ascii", errors="ignore").decode("ascii", errors="ignore")
    text = text.lower()
    text = _SLUG_TOKEN_RE.sub("-", text)
    text = _LEADING_HYPHEN_RE.sub("", text)
    text = _TRAILING_HYPHEN_RE.sub("", text)
    if not text:
        return ""
    if len(text) > max_chars:
        # Cut at the last hyphen before the cap so the slug ends on
        # a token boundary rather than mid-word.
        cut = text.rfind("-", 0, max_chars)
        text = text[: cut if cut > 0 else max_chars]
        text = _TRAILING_HYPHEN_RE.sub("", text)
    return text


def _short_role(role: Optional[str]) -> str:
    """Return the short form of a role id (e.g. ``backend-engineer``)."""

    if not role:
        return "engineering"
    short = str(role).split("/", 1)[-1].strip()
    short = slugify_for_branch(short, max_chars=40)
    return short or "engineering"


# ---------------------------------------------------------------------------
# Protected branch guard
# ---------------------------------------------------------------------------


def is_protected_branch(name: Optional[str]) -> bool:
    """Return True when *name* matches a protected branch (case-insensitive).

    The check is literal — we don't try to interpret refs that *look*
    like protected branches (e.g. ``main-2``). The caller can stack
    a stricter org-level checker in the policy gate; this function
    only enforces the always-deny default list.
    """

    if not name:
        return False
    candidate = str(name).strip().lower()
    if candidate in PROTECTED_BRANCHES:
        return True
    # Treat ``refs/heads/<name>`` and ``origin/<name>`` shapes too —
    # a misconfigured caller might pass a fully-qualified ref.
    for protected in PROTECTED_BRANCHES:
        if candidate.endswith(f"/{protected}"):
            return True
    return False


# ---------------------------------------------------------------------------
# Branch name builder
# ---------------------------------------------------------------------------


def derive_branch_name(
    plan: TriagePlanLike,
    *,
    fallback_seed: Optional[str] = None,
) -> str:
    """Return the branch name for *plan*.

    ``github`` source → ``agent/<role>/issue-<n>-<slug>``.
    ``discord`` source → ``agent/<role>/discord-<sess>-<slug>``.
    Anything else falls back to ``agent/<role>/work-<seed>-<slug>``.

    *fallback_seed* is used when the plan has neither issue_number nor
    session_id (very rare — typically a manual triage). It must already
    be slug-safe; the caller passes the audit id.
    """

    role = _short_role(getattr(plan, "primary_role", None))
    title = getattr(plan, "title", "") or ""
    slug = slugify_for_branch(title)
    source = (getattr(plan, "source", "") or "").strip().lower()

    issue_number = getattr(plan, "issue_number", None)
    session_id = getattr(plan, "session_id", None)

    if source == "github" and issue_number:
        anchor = f"issue-{int(issue_number)}"
    elif source == "discord" and session_id:
        anchor_slug = slugify_for_branch(str(session_id), max_chars=24) or "session"
        anchor = f"discord-{anchor_slug}"
    elif issue_number:
        anchor = f"issue-{int(issue_number)}"
    elif session_id:
        anchor_slug = slugify_for_branch(str(session_id), max_chars=24) or "session"
        anchor = f"discord-{anchor_slug}"
    else:
        seed = slugify_for_branch(fallback_seed or "", max_chars=24) or "manual"
        anchor = f"work-{seed}"

    if slug:
        full = f"agent/{role}/{anchor}-{slug}"
    else:
        full = f"agent/{role}/{anchor}"
    # Final defense: if the assembled name (rare collision) ends up
    # matching a protected branch literally, raise.
    if is_protected_branch(full.split("/")[-1]):
        raise ValueError(
            f"branch name {full!r} collides with a protected branch — "
            "refuse to write."
        )
    return full


# ---------------------------------------------------------------------------
# Collision suffix
# ---------------------------------------------------------------------------


def derive_branch_with_collision_suffix(
    base_name: str,
    *,
    exists_fn: Callable[[str], bool],
    max_attempts: int = MAX_COLLISION_ATTEMPTS,
) -> str:
    """Return *base_name* (or ``<base>-<n>``) that doesn't yet exist.

    *exists_fn* is the caller-supplied "does this branch exist?"
    predicate — production wires it to a GitHub API check, tests pass
    a stub. The function appends ``-2``, ``-3`` etc. until it finds a
    free slot.

    Raises :class:`ValueError` when *base_name* itself is a protected
    branch (caller should use :func:`derive_branch_name` first), or
    when no free slot is found within ``max_attempts`` tries.
    """

    if is_protected_branch(base_name.split("/")[-1]):
        raise ValueError(
            f"branch {base_name!r} matches a protected branch — refuse to write"
        )
    if not exists_fn(base_name):
        return base_name
    for n in range(2, max_attempts + 2):
        candidate = f"{base_name}-{n}"
        if not exists_fn(candidate):
            return candidate
    raise ValueError(
        f"could not pick a free branch name within {max_attempts} attempts; "
        "manual cleanup needed"
    )


__all__ = (
    "DEFAULT_SLUG_MAX_LEN",
    "MAX_COLLISION_ATTEMPTS",
    "PROTECTED_BRANCHES",
    "TriagePlanLike",
    "derive_branch_name",
    "derive_branch_with_collision_suffix",
    "is_protected_branch",
    "slugify_for_branch",
)
