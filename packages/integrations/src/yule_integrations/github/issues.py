from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .cache import (
    DEFAULT_GITHUB_ISSUE_CACHE_SECONDS,
    DEFAULT_GITHUB_VIEWER_CONTEXT_CACHE_SECONDS,
    build_github_cache_key,
    load_cached_issue_payload,
    load_cached_viewer_context_payload,
    load_stale_issue_payload,
    load_stale_viewer_context_payload,
    save_issue_payload,
    save_viewer_context_payload,
)


class GitHubIssueError(Exception):
    """Raised when open issues cannot be loaded from GitHub CLI."""


@dataclass(frozen=True)
class GitHubIssue:
    number: int
    repository: str
    title: str
    url: str
    owner: str
    scope: str
    labels: Tuple[str, ...] = ()
    body: str = ""
    assignees: Tuple[str, ...] = ()
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "repository": self.repository,
            "title": self.title,
            "url": self.url,
            "owner": self.owner,
            "scope": self.scope,
            "labels": list(self.labels),
            "body": self.body,
            "assignees": list(self.assignees),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GitHubIssue":
        return cls(
            number=int(payload["number"]),
            repository=str(payload["repository"]),
            title=str(payload["title"]),
            url=str(payload["url"]),
            owner=str(payload["owner"]),
            scope=str(payload["scope"]),
            labels=_normalize_string_tuple(payload.get("labels", ())),
            body=str(payload.get("body") or ""),
            assignees=_normalize_string_tuple(payload.get("assignees", ())),
            updated_at=_optional_string(payload.get("updated_at")),
        )


@dataclass(frozen=True)
class GitHubViewerContext:
    viewer_login: str
    org_logins: Tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "viewer_login": self.viewer_login,
            "org_logins": list(self.org_logins),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GitHubViewerContext":
        viewer_login = payload.get("viewer_login")
        org_logins = payload.get("org_logins", [])
        if not isinstance(viewer_login, str) or not viewer_login:
            raise ValueError("viewer_login is required.")
        if not isinstance(org_logins, list):
            raise ValueError("org_logins must be a list.")
        normalized_orgs = tuple(sorted(str(login) for login in org_logins if str(login)))
        return cls(viewer_login=viewer_login, org_logins=normalized_orgs)


def list_open_issues(limit: int = 30, force_refresh: bool = False) -> Sequence[GitHubIssue]:
    if not shutil.which("gh"):
        raise GitHubIssueError("GitHub CLI (`gh`) is not installed.")

    issue_cache_ttl_seconds = _load_issue_cache_seconds()
    viewer_context_ttl_seconds = max(
        issue_cache_ttl_seconds,
        DEFAULT_GITHUB_VIEWER_CONTEXT_CACHE_SECONDS,
    )

    viewer_context = _load_viewer_context(
        ttl_seconds=viewer_context_ttl_seconds,
        force_refresh=force_refresh,
    )
    owners = [viewer_context.viewer_login, *viewer_context.org_logins]
    scope_hash = build_github_cache_key(
        "github-open-issues-v1",
        viewer_context.viewer_login,
        *viewer_context.org_logins,
    )
    cache_key = build_github_cache_key(
        scope_hash,
        str(limit),
        "state=open",
        "sort=updated",
        "order=desc",
    )

    stale_cached_issues: Optional[Sequence[GitHubIssue]] = None
    if not force_refresh:
        cached_issues = _load_issue_cache(cache_key=cache_key, ttl_seconds=issue_cache_ttl_seconds)
        if cached_issues is not None:
            return cached_issues
        stale_cached_issues = _load_stale_issue_cache(cache_key=cache_key)

    command = [
        "gh",
        "search",
        "issues",
        "--state",
        "open",
        "--sort",
        "updated",
        "--order",
        "desc",
        "--limit",
        str(limit),
        "--json",
        "number,title,url,repository,labels,body,assignees,updatedAt",
    ]
    for owner in owners:
        command.extend(["--owner", owner])

    result = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        if stale_cached_issues is not None:
            return stale_cached_issues
        raise GitHubIssueError(_format_gh_error(result.stderr))

    payload = result.stdout.strip()
    if not payload:
        return []

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise GitHubIssueError(f"Could not parse GitHub CLI output: {exc}") from exc

    if not isinstance(data, list):
        raise GitHubIssueError("Unexpected GitHub CLI response format.")

    issues: List[GitHubIssue] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        number = item.get("number")
        title = item.get("title")
        url = item.get("url")
        repository = _extract_repository_name(item.get("repository"))
        owner = _extract_repository_owner(repository)
        scope = _classify_owner_scope(
            owner,
            viewer_context.viewer_login,
            set(viewer_context.org_logins),
        )

        if not isinstance(number, int):
            continue
        if not isinstance(title, str):
            continue
        if not isinstance(url, str):
            continue

        labels = _extract_labels(item.get("labels"))
        body = item.get("body")
        assignees = _extract_assignees(item.get("assignees"))
        updated_at = item.get("updatedAt")

        issues.append(
            GitHubIssue(
                number=number,
                repository=repository,
                title=title,
                url=url,
                owner=owner,
                scope=scope,
                labels=labels,
                body=str(body) if isinstance(body, str) else "",
                assignees=assignees,
                updated_at=str(updated_at) if isinstance(updated_at, str) and updated_at else None,
            )
        )

    save_issue_payload(
        cache_key=cache_key,
        scope_hash=scope_hash,
        ttl_seconds=issue_cache_ttl_seconds,
        payload=[issue.to_dict() for issue in issues],
    )
    return issues


def render_open_issues(issues: Sequence[GitHubIssue]) -> str:
    lines: List[str] = []

    if not issues:
        lines.append("No open GitHub issues found for the current account.")
        return "\n".join(lines) + "\n"

    lines.append("Open GitHub Issues")
    lines.append("")
    for issue in issues:
        lines.append(f"[{issue.scope}] #{issue.number} {issue.repository} - {issue.title}")
        lines.append(issue.url)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _extract_repository_name(value: Any) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, dict):
        name_with_owner = value.get("nameWithOwner")
        if isinstance(name_with_owner, str):
            return name_with_owner

        owner = value.get("owner")
        name = value.get("name")
        if isinstance(owner, dict):
            owner_login = owner.get("login")
            if isinstance(owner_login, str) and isinstance(name, str):
                return f"{owner_login}/{name}"

        if isinstance(name, str):
            return name

    return "unknown-repo"


def _format_gh_error(stderr: str) -> str:
    message = (stderr or "").strip()
    lowered = message.lower()

    if "authentication" in lowered or "gh auth login" in lowered or "not logged into any hosts" in lowered:
        return "GitHub CLI is not authenticated. Run `gh auth login`."

    if "error connecting to" in lowered or "dial tcp" in lowered or "no such host" in lowered:
        return "Could not reach GitHub. Check your network connection and GitHub availability."

    if "rate limit" in lowered:
        return "GitHub API rate limit reached."

    if message:
        first_line = message.splitlines()[0]
        return f"GitHub issue query failed: {first_line}"

    return "GitHub issue query failed."


def _load_viewer_context(ttl_seconds: int, force_refresh: bool = False) -> GitHubViewerContext:
    cache_key = build_github_cache_key("github-viewer-context-v1")
    stale_context: Optional[GitHubViewerContext] = None
    if not force_refresh:
        cached_context = _load_cached_viewer_context(cache_key=cache_key, ttl_seconds=ttl_seconds)
        if cached_context is not None:
            return cached_context
        stale_context = _load_stale_viewer_context(cache_key=cache_key)

    viewer = _run_gh_json_command(["gh", "api", "user"])
    viewer_login = viewer.get("login")
    if not isinstance(viewer_login, str) or not viewer_login:
        if stale_context is not None:
            return stale_context
        raise GitHubIssueError("Could not determine the authenticated GitHub user.")

    try:
        orgs_payload = _run_gh_json_command(["gh", "api", "user/orgs"])
    except GitHubIssueError:
        if stale_context is not None:
            return stale_context
        raise

    org_logins: list[str] = []
    if isinstance(orgs_payload, list):
        for item in orgs_payload:
            if isinstance(item, dict):
                login = item.get("login")
                if isinstance(login, str) and login:
                    org_logins.append(login)

    viewer_context = GitHubViewerContext(
        viewer_login=viewer_login,
        org_logins=tuple(sorted(set(org_logins))),
    )
    save_viewer_context_payload(
        cache_key=cache_key,
        ttl_seconds=ttl_seconds,
        payload=viewer_context.to_dict(),
    )
    return viewer_context


def _run_gh_json_command(command: Sequence[str]) -> Any:
    result = subprocess.run(
        list(command),
        check=False,
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        raise GitHubIssueError(_format_gh_error(result.stderr))

    payload = result.stdout.strip()
    if not payload:
        return {}

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise GitHubIssueError(f"Could not parse GitHub CLI output: {exc}") from exc


def _extract_repository_owner(repository: str) -> str:
    if "/" not in repository:
        return "unknown-owner"
    return repository.split("/", 1)[0]


def _extract_labels(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        elif isinstance(item, str) and item.strip():
            names.append(item.strip())
    return tuple(names)


def _extract_assignees(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    logins: list[str] = []
    for item in value:
        if isinstance(item, dict):
            login = item.get("login")
            if isinstance(login, str) and login.strip():
                logins.append(login.strip())
        elif isinstance(item, str) and item.strip():
            logins.append(item.strip())
    return tuple(logins)


def _normalize_string_tuple(value: Any) -> Tuple[str, ...]:
    if isinstance(value, tuple):
        return tuple(str(item) for item in value if isinstance(item, str) and item)
    if isinstance(value, list):
        return tuple(str(item) for item in value if isinstance(item, str) and item)
    return ()


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _classify_owner_scope(owner: str, viewer_login: str, org_logins: Set[str]) -> str:
    if owner == viewer_login:
        return "personal"
    if owner in org_logins:
        return f"org:{owner}"
    return f"external:{owner}"


def _load_issue_cache_seconds() -> int:
    raw_value = os.getenv("GITHUB_ISSUES_CACHE_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_GITHUB_ISSUE_CACHE_SECONDS

    try:
        ttl_seconds = int(raw_value)
    except ValueError as exc:
        raise GitHubIssueError(
            f"GITHUB_ISSUES_CACHE_SECONDS must be an integer, got: {raw_value!r}"
        ) from exc

    if ttl_seconds < 0:
        raise GitHubIssueError("GITHUB_ISSUES_CACHE_SECONDS must be 0 or greater.")

    return ttl_seconds


def _load_issue_cache(cache_key: str, ttl_seconds: int) -> Optional[Sequence[GitHubIssue]]:
    payload = load_cached_issue_payload(cache_key=cache_key, ttl_seconds=ttl_seconds)
    if payload is None:
        return None

    try:
        return [GitHubIssue.from_dict(item) for item in payload if isinstance(item, dict)]
    except Exception:
        return None


def _load_stale_issue_cache(cache_key: str) -> Optional[Sequence[GitHubIssue]]:
    payload = load_stale_issue_payload(cache_key=cache_key)
    if payload is None:
        return None

    try:
        return [GitHubIssue.from_dict(item) for item in payload if isinstance(item, dict)]
    except Exception:
        return None


def _load_cached_viewer_context(cache_key: str, ttl_seconds: int) -> Optional[GitHubViewerContext]:
    payload = load_cached_viewer_context_payload(cache_key=cache_key, ttl_seconds=ttl_seconds)
    if payload is None:
        return None

    try:
        return GitHubViewerContext.from_dict(payload)
    except Exception:
        return None


def _load_stale_viewer_context(cache_key: str) -> Optional[GitHubViewerContext]:
    payload = load_stale_viewer_context_payload(cache_key=cache_key)
    if payload is None:
        return None

    try:
        return GitHubViewerContext.from_dict(payload)
    except Exception:
        return None
