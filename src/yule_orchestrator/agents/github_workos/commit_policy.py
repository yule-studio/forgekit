"""Commit author / committer policy — G3.

A GitHub App writing on behalf of a user must split *who wrote the
change* from *who actually pushed it*:

  * **author** = the human (repo owner / requesting user) — their name
    appears in ``git log --format=%an``.
  * **committer** = the GitHub App bot — verified signature + audit
    trail point at the App, not the user.

Splitting cleanly means:

  * Commits surface in the user's GitHub contributions graph.
  * The push event audit on GitHub side names the App, so a compromised
    user account can be revoked without wiping the App's history.
  * GPG / sigstore verification (when configured) keys off the App's
    identity, not a per-commit user signature.

Email validation enforces GitHub-acceptable shapes — verified primary
email or the ``<id>+<login>@users.noreply.github.com`` form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Sequence


# ---------------------------------------------------------------------------
# Account Protocols
# ---------------------------------------------------------------------------


class AccountLike(Protocol):
    """Structural typing for either an owner or App account input."""

    name: Optional[str]
    email: Optional[str]
    login: Optional[str]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitAuthor:
    """One side of the commit identity (author OR committer).

    ``login`` is the GitHub login (e.g. ``codwithyc``) — used to
    construct the noreply email when ``email`` is not provided. It's
    optional to support test fakes, but production wiring should
    always pass it so the noreply fallback is deterministic.
    """

    name: str
    email: str
    login: Optional[str] = None

    @property
    def is_app_bot(self) -> bool:
        """True when this identity points at a GitHub App bot account."""

        if not self.login:
            return False
        return self.login.endswith("[bot]") or self.login.endswith("-bot")


@dataclass(frozen=True)
class CommitIdentity:
    """The (author, committer) pair used for one GitHub commit.

    The :class:`GithubWriter` reads this when calling the Git Data API
    to set ``commit.author`` and ``commit.committer`` separately.
    """

    author: CommitAuthor
    committer: CommitAuthor
    warnings: Sequence[str] = field(default_factory=tuple)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)


# ---------------------------------------------------------------------------
# Email validation
# ---------------------------------------------------------------------------


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NOREPLY_RE = re.compile(
    r"^(?:(?P<id>\d+)\+)?(?P<login>[A-Za-z0-9-]+)@users\.noreply\.github\.com$"
)


def is_acceptable_commit_email(email: Optional[str]) -> bool:
    """Return True for an email shape GitHub will accept on a commit.

    Accepts:
      * the ``<id>+<login>@users.noreply.github.com`` form,
      * the ``<login>@users.noreply.github.com`` legacy form,
      * any well-formed email (the API itself decides if it's verified
        — we can't check that locally without an API call).

    Rejects empty / malformed / single-token / control-character
    strings.
    """

    if not email:
        return False
    text = email.strip()
    if not text or "\n" in text or "\r" in text:
        return False
    if _NOREPLY_RE.match(text):
        return True
    return bool(_EMAIL_RE.match(text))


def _noreply_email_for(login: Optional[str], user_id: Optional[int] = None) -> str:
    """Build a noreply email from *login* (and optional numeric *user_id*).

    Returns ``""`` when *login* is empty — caller must surface that as
    a warning so the operator notices.
    """

    if not login:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9-]", "", str(login)).strip("-")
    if not cleaned:
        return ""
    if user_id:
        return f"{int(user_id)}+{cleaned}@users.noreply.github.com"
    return f"{cleaned}@users.noreply.github.com"


# ---------------------------------------------------------------------------
# Identity builders
# ---------------------------------------------------------------------------


def derive_commit_identity(
    *,
    owner: AccountLike,
    app: AccountLike,
    owner_user_id: Optional[int] = None,
    app_user_id: Optional[int] = None,
) -> CommitIdentity:
    """Compose a :class:`CommitIdentity` from owner + app accounts.

    * Author = owner (the human / repo owner who triggered the
      triage); falls back to the App account if owner is missing
      entirely (with a warning so the operator notices the
      misconfiguration).
    * Committer = app bot.

    Email resolution: explicit ``email`` wins; otherwise we synthesise
    the noreply form from ``login``. If neither yields an email a
    warning is added so the operator sees the gap before any GitHub
    API call.
    """

    warnings: List[str] = []

    owner_name = (getattr(owner, "name", None) or "").strip()
    owner_login = (getattr(owner, "login", None) or "").strip() or None
    owner_email = (getattr(owner, "email", None) or "").strip()
    if not owner_email:
        owner_email = _noreply_email_for(owner_login, owner_user_id)
    if not owner_name:
        owner_name = owner_login or "yule-studio-agent"
        warnings.append(
            "owner author name missing; falling back to login "
            f"{owner_name!r}. Set the owner's display name explicitly "
            "for cleaner git log output."
        )
    if not is_acceptable_commit_email(owner_email):
        warnings.append(
            "owner author email is not acceptable for GitHub commits — "
            "use a verified primary email or the "
            "`<id>+<login>@users.noreply.github.com` form."
        )

    app_name = (getattr(app, "name", None) or "").strip()
    app_login = (getattr(app, "login", None) or "").strip() or None
    app_email = (getattr(app, "email", None) or "").strip()
    if not app_email:
        app_email = _noreply_email_for(app_login, app_user_id)
    if not app_name:
        app_name = app_login or "yule-studio-agent[bot]"
        warnings.append(
            "App committer name missing; falling back to "
            f"{app_name!r}."
        )
    if not is_acceptable_commit_email(app_email):
        warnings.append(
            "App committer email is not acceptable for GitHub commits — "
            "use the App's noreply form "
            "`<id>+<login>[bot]@users.noreply.github.com`."
        )

    author = CommitAuthor(
        name=owner_name, email=owner_email, login=owner_login
    )
    committer = CommitAuthor(
        name=app_name, email=app_email, login=app_login
    )

    return CommitIdentity(
        author=author, committer=committer, warnings=tuple(warnings)
    )


def validate_commit_identity(identity: CommitIdentity) -> Sequence[str]:
    """Return a (possibly empty) list of operator-facing warning strings.

    Distinct from :class:`CommitIdentity.warnings` because that field
    captures only *construction-time* warnings. Once an identity has
    survived a round trip (e.g. through audit), this function lets the
    caller re-validate against the same email rules without re-walking
    :func:`derive_commit_identity`.
    """

    issues: List[str] = list(identity.warnings or ())
    if not is_acceptable_commit_email(identity.author.email):
        issues.append(
            "author.email failed validation — re-derive identity or "
            "fix the owner record."
        )
    if not is_acceptable_commit_email(identity.committer.email):
        issues.append(
            "committer.email failed validation — App noreply email "
            "must use the `<id>+<login>[bot]@users.noreply.github.com` form."
        )
    if (
        identity.author.email
        and identity.author.email == identity.committer.email
    ):
        issues.append(
            "author.email and committer.email are identical — App-as-bot "
            "audit benefit is lost. Use the owner's email for author and "
            "the App's noreply for committer."
        )
    return tuple(issues)


__all__ = (
    "AccountLike",
    "CommitAuthor",
    "CommitIdentity",
    "derive_commit_identity",
    "is_acceptable_commit_email",
    "validate_commit_identity",
)
