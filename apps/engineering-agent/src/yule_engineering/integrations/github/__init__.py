"""Compatibility shim — GitHub integration now lives in
``yule_integrations.github``.

Re-exports the same public names so existing
``from yule_engineering.integrations.github import ...`` imports keep
resolving to the identical objects. Submodule shims (``cache``,
``issues``, ``pulls``) alias the new modules via ``sys.modules``.
"""

from yule_integrations.github import (
    GitHubIssue,
    GitHubIssueError,
    list_open_issues,
    render_open_issues,
)


__all__ = ["GitHubIssue", "GitHubIssueError", "list_open_issues", "render_open_issues"]
