"""GitHub URL parsing — repo / issue / PR / commit / compare / branch context.

P0-H stage 2 (#140) — extends the previous issue/PR-only parser at
``research/collector.py:parse_github_url`` to support the full set
of shapes the gateway needs when a user pastes a GitHub link into a
coding session.

Network-free. Pure regex / urlparse. Returns ``None`` for any non-
GitHub URL so callers can fall through to generic classification.

The 5 supported shapes:

  * ``repo``       — ``https://github.com/<owner>/<repo>``
  * ``issue``      — ``https://github.com/<owner>/<repo>/issues/<n>``
  * ``pull_request`` — ``https://github.com/<owner>/<repo>/pull/<n>``
  * ``commit``     — ``https://github.com/<owner>/<repo>/commit/<sha>``
  * ``compare``    — ``https://github.com/<owner>/<repo>/compare/<from>...<to>``
  * ``tree``       — ``https://github.com/<owner>/<repo>/tree/<branch_or_sha>``  (branch context)
  * ``blob``       — ``https://github.com/<owner>/<repo>/blob/<branch_or_sha>/<path>``  (branch context + path)

Compatibility:

  * ``parse_github_url`` (legacy shape) still returns ``{kind, owner,
    repo, number}`` for issue / pull_request — the 3 existing callers
    in collector.py keep working. New callers should prefer
    :func:`parse_github_target` which returns a :class:`GithubTarget`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import urlparse


# Path-shape regexes. Anchored to repo prefix; suffix shapes parsed below.
_GITHUB_HOST = "github.com"

_REPO_PATH_RE = re.compile(
    r"^/(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+)(?P<suffix>/.*)?$"
)

_NUMBER_RE = re.compile(r"^(?P<number>\d+)(?:[/?#].*)?$")
_SHA_RE = re.compile(r"^(?P<sha>[0-9a-f]{7,40})(?:[/?#].*)?$", re.IGNORECASE)
_COMPARE_RE = re.compile(
    r"^(?P<from>[^.]+(?:\.[^.]+)*)\.\.\.(?P<to>[^?#]+?)(?:[?#].*)?$"
)
_TREE_OR_BLOB_RE = re.compile(
    r"^(?P<ref>[^/?#]+)(?P<path>/[^?#]*)?(?:[?#].*)?$"
)


@dataclass(frozen=True)
class GithubTarget:
    """Parsed shape of a GitHub URL.

    ``kind`` identifies the surface (repo / issue / pull_request /
    commit / compare / tree / blob). The rest of the fields are
    populated when relevant; everything is optional so a partial
    parse (e.g. repo root) still emits a usable target.
    """

    kind: str
    owner: str
    repo: str
    number: Optional[int] = None  # issue / PR
    sha: Optional[str] = None  # commit
    compare_from: Optional[str] = None  # compare
    compare_to: Optional[str] = None  # compare
    branch_or_sha: Optional[str] = None  # tree / blob (branch context)
    file_path: Optional[str] = None  # blob
    raw_url: str = ""

    def to_dict(self) -> Mapping[str, Any]:
        """Plain-dict round-trip for ``session.extra['github_target']``."""

        out: dict = {
            "kind": self.kind,
            "owner": self.owner,
            "repo": self.repo,
        }
        if self.number is not None:
            out["number"] = self.number
        if self.sha is not None:
            out["sha"] = self.sha
        if self.compare_from is not None:
            out["compare_from"] = self.compare_from
        if self.compare_to is not None:
            out["compare_to"] = self.compare_to
        if self.branch_or_sha is not None:
            out["branch_or_sha"] = self.branch_or_sha
        if self.file_path is not None:
            out["file_path"] = self.file_path
        if self.raw_url:
            out["raw_url"] = self.raw_url
        return out

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GithubTarget":
        return cls(
            kind=str(payload.get("kind") or ""),
            owner=str(payload.get("owner") or ""),
            repo=str(payload.get("repo") or ""),
            number=_coerce_int(payload.get("number")),
            sha=_coerce_str(payload.get("sha")),
            compare_from=_coerce_str(payload.get("compare_from")),
            compare_to=_coerce_str(payload.get("compare_to")),
            branch_or_sha=_coerce_str(payload.get("branch_or_sha")),
            file_path=_coerce_str(payload.get("file_path")),
            raw_url=str(payload.get("raw_url") or ""),
        )


def parse_github_target(url: Optional[str]) -> Optional[GithubTarget]:
    """Return a :class:`GithubTarget` for *url* or ``None``.

    Accepts http or https; case-insensitive on the host. Returns
    ``None`` for any non-github.com URL.
    """

    if not url:
        return None
    text = str(url).strip()
    try:
        parsed = urlparse(text)
    except Exception:  # noqa: BLE001 - defensive
        return None
    host = (parsed.hostname or "").lower()
    if host != _GITHUB_HOST and not host.endswith(f".{_GITHUB_HOST}"):
        return None
    path = parsed.path or "/"
    match = _REPO_PATH_RE.match(path)
    if not match:
        return None
    owner = match.group("owner")
    repo = match.group("repo")
    suffix = match.group("suffix") or ""

    # Strip leading "/" from suffix for sub-parsing.
    if suffix.startswith("/"):
        suffix = suffix[1:]

    # Trim .git suffix on the repo segment (some clone URLs).
    if repo.endswith(".git"):
        repo = repo[:-4]

    if not suffix:
        return GithubTarget(kind="repo", owner=owner, repo=repo, raw_url=text)

    # issues/<n>
    if suffix.startswith("issues/"):
        rest = suffix[len("issues/") :]
        match = _NUMBER_RE.match(rest)
        if match:
            return GithubTarget(
                kind="issue",
                owner=owner,
                repo=repo,
                number=int(match.group("number")),
                raw_url=text,
            )
        return None

    # pull/<n>  (also accept pulls/<n> defensively)
    for prefix in ("pull/", "pulls/"):
        if suffix.startswith(prefix):
            rest = suffix[len(prefix) :]
            match = _NUMBER_RE.match(rest)
            if match:
                return GithubTarget(
                    kind="pull_request",
                    owner=owner,
                    repo=repo,
                    number=int(match.group("number")),
                    raw_url=text,
                )
            return None

    # commit/<sha>
    if suffix.startswith("commit/"):
        rest = suffix[len("commit/") :]
        match = _SHA_RE.match(rest)
        if match:
            return GithubTarget(
                kind="commit",
                owner=owner,
                repo=repo,
                sha=match.group("sha").lower(),
                raw_url=text,
            )
        return None

    # compare/<from>...<to>
    if suffix.startswith("compare/"):
        rest = suffix[len("compare/") :]
        match = _COMPARE_RE.match(rest)
        if match:
            return GithubTarget(
                kind="compare",
                owner=owner,
                repo=repo,
                compare_from=match.group("from"),
                compare_to=match.group("to"),
                raw_url=text,
            )
        return None

    # tree/<branch_or_sha>(/<path>)
    if suffix.startswith("tree/"):
        rest = suffix[len("tree/") :]
        match = _TREE_OR_BLOB_RE.match(rest)
        if match:
            return GithubTarget(
                kind="tree",
                owner=owner,
                repo=repo,
                branch_or_sha=match.group("ref"),
                file_path=match.group("path") or None,
                raw_url=text,
            )
        return None

    # blob/<branch_or_sha>/<path>
    if suffix.startswith("blob/"):
        rest = suffix[len("blob/") :]
        match = _TREE_OR_BLOB_RE.match(rest)
        if match:
            file_path = match.group("path")
            # blob always carries a path; if missing, fall through to None.
            return GithubTarget(
                kind="blob",
                owner=owner,
                repo=repo,
                branch_or_sha=match.group("ref"),
                file_path=file_path or None,
                raw_url=text,
            )
        return None

    # Unrecognized suffix — treat as repo-level so caller still gets
    # owner/repo (e.g. /settings, /releases). kind stays "repo".
    return GithubTarget(kind="repo", owner=owner, repo=repo, raw_url=text)


def parse_github_targets(urls) -> Tuple[GithubTarget, ...]:
    """Parse a sequence of URLs into the GithubTargets that match."""

    out: list[GithubTarget] = []
    for url in urls or ():
        target = parse_github_target(url)
        if target is not None:
            out.append(target)
    return tuple(out)


# ---------------------------------------------------------------------------
# Legacy compatibility — keep ``parse_github_url`` from collector.py
# ---------------------------------------------------------------------------


def parse_github_url(url: Optional[str]) -> Optional[Mapping[str, Any]]:
    """Issue / PR only — preserved for legacy callers in ``collector.py``.

    Returns ``{kind, owner, repo, number}`` like the previous parser.
    For other shapes (commit / compare / tree / blob / repo root) we
    return ``None`` so the existing collector classification keeps
    its old fallthrough behavior. New callers should use
    :func:`parse_github_target` instead.
    """

    target = parse_github_target(url)
    if target is None:
        return None
    if target.kind == "issue" or target.kind == "pull_request":
        return {
            "kind": target.kind,
            "owner": target.owner,
            "repo": target.repo,
            "number": target.number,
        }
    return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = (
    "GithubTarget",
    "parse_github_target",
    "parse_github_targets",
    "parse_github_url",
)
