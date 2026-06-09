from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from .cache import (
    DEFAULT_GITHUB_PULL_REQUEST_CACHE_SECONDS,
    build_github_cache_key,
    load_cached_pull_request_payload,
    load_stale_pull_request_payload,
    save_pull_request_payload,
)
from .issues import (
    GitHubIssueError,
    GitHubViewerContext,
    _classify_owner_scope,
    _extract_assignees,
    _extract_labels,
    _extract_repository_name,
    _extract_repository_owner,
    _format_gh_error,
    _load_viewer_context,
    _normalize_string_tuple,
    _optional_string,
)


@dataclass(frozen=True)
class GitHubPullRequest:
    number: int
    repository: str
    title: str
    url: str
    owner: str
    scope: str
    state: str = "open"
    draft: bool = False
    labels: Tuple[str, ...] = ()
    body: str = ""
    assignees: Tuple[str, ...] = ()
    updated_at: Optional[str] = None
    review_decision: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "repository": self.repository,
            "title": self.title,
            "url": self.url,
            "owner": self.owner,
            "scope": self.scope,
            "state": self.state,
            "draft": self.draft,
            "labels": list(self.labels),
            "body": self.body,
            "assignees": list(self.assignees),
            "updated_at": self.updated_at,
            "review_decision": self.review_decision,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GitHubPullRequest":
        return cls(
            number=int(payload["number"]),
            repository=str(payload["repository"]),
            title=str(payload["title"]),
            url=str(payload["url"]),
            owner=str(payload["owner"]),
            scope=str(payload["scope"]),
            state=str(payload.get("state") or "open"),
            draft=bool(payload.get("draft") or False),
            labels=_normalize_string_tuple(payload.get("labels", ())),
            body=str(payload.get("body") or ""),
            assignees=_normalize_string_tuple(payload.get("assignees", ())),
            updated_at=_optional_string(payload.get("updated_at")),
            review_decision=_optional_string(payload.get("review_decision")),
        )


def list_open_pull_requests(limit: int = 30, force_refresh: bool = False) -> Sequence[GitHubPullRequest]:
    if not shutil.which("gh"):
        raise GitHubIssueError("GitHub CLI (`gh`) is not installed.")

    pull_request_cache_ttl_seconds = _load_pull_request_cache_seconds()
    viewer_context_ttl_seconds = max(
        pull_request_cache_ttl_seconds,
        1800,
    )

    viewer_context = _load_viewer_context(
        ttl_seconds=viewer_context_ttl_seconds,
        force_refresh=force_refresh,
    )
    owners = [viewer_context.viewer_login, *viewer_context.org_logins]
    scope_hash = build_github_cache_key(
        "github-open-pull-requests-v1",
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

    stale_cached_pulls: Optional[Sequence[GitHubPullRequest]] = None
    if not force_refresh:
        cached_pulls = _load_pull_request_cache(
            cache_key=cache_key, ttl_seconds=pull_request_cache_ttl_seconds
        )
        if cached_pulls is not None:
            return cached_pulls
        stale_cached_pulls = _load_stale_pull_request_cache(cache_key=cache_key)

    command = [
        "gh",
        "search",
        "prs",
        "--state",
        "open",
        "--sort",
        "updated",
        "--order",
        "desc",
        "--limit",
        str(limit),
        "--json",
        "number,title,url,repository,labels,body,assignees,updatedAt,isDraft,state",
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
        if stale_cached_pulls is not None:
            return stale_cached_pulls
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

    pulls: List[GitHubPullRequest] = []
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
        state = item.get("state") or "open"
        draft = bool(item.get("isDraft") or False)

        pulls.append(
            GitHubPullRequest(
                number=number,
                repository=repository,
                title=title,
                url=url,
                owner=owner,
                scope=scope,
                state=str(state).lower() if isinstance(state, str) else "open",
                draft=draft,
                labels=labels,
                body=str(body) if isinstance(body, str) else "",
                assignees=assignees,
                updated_at=str(updated_at) if isinstance(updated_at, str) and updated_at else None,
            )
        )

    save_pull_request_payload(
        cache_key=cache_key,
        scope_hash=scope_hash,
        ttl_seconds=pull_request_cache_ttl_seconds,
        payload=[pr.to_dict() for pr in pulls],
    )
    return pulls


def render_open_pull_requests(pulls: Sequence[GitHubPullRequest]) -> str:
    lines: List[str] = []

    if not pulls:
        lines.append("No open GitHub pull requests found for the current account.")
        return "\n".join(lines) + "\n"

    lines.append("Open GitHub Pull Requests")
    lines.append("")
    for pr in pulls:
        draft_marker = " (draft)" if pr.draft else ""
        lines.append(f"[{pr.scope}] #{pr.number} {pr.repository} - {pr.title}{draft_marker}")
        lines.append(pr.url)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _load_pull_request_cache_seconds() -> int:
    raw_value = os.getenv("GITHUB_PULL_REQUESTS_CACHE_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_GITHUB_PULL_REQUEST_CACHE_SECONDS

    try:
        ttl_seconds = int(raw_value)
    except ValueError as exc:
        raise GitHubIssueError(
            f"GITHUB_PULL_REQUESTS_CACHE_SECONDS must be an integer, got: {raw_value!r}"
        ) from exc

    if ttl_seconds < 0:
        raise GitHubIssueError("GITHUB_PULL_REQUESTS_CACHE_SECONDS must be 0 or greater.")

    return ttl_seconds


def _load_pull_request_cache(cache_key: str, ttl_seconds: int) -> Optional[Sequence[GitHubPullRequest]]:
    payload = load_cached_pull_request_payload(cache_key=cache_key, ttl_seconds=ttl_seconds)
    if payload is None:
        return None

    try:
        return [GitHubPullRequest.from_dict(item) for item in payload if isinstance(item, dict)]
    except Exception:
        return None


def _load_stale_pull_request_cache(cache_key: str) -> Optional[Sequence[GitHubPullRequest]]:
    payload = load_stale_pull_request_payload(cache_key=cache_key)
    if payload is None:
        return None

    try:
        return [GitHubPullRequest.from_dict(item) for item in payload if isinstance(item, dict)]
    except Exception:
        return None
