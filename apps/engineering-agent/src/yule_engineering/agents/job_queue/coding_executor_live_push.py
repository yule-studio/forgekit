"""Pusher + draft PR creator for the live coding executor.

Split out of :mod:`coding_executor_live` (responsibility: *live
runner — push / draft PR via GitHub App git data API*).
Behavior-preserving move; the original module re-exports both public
classes so importers stay unchanged.

Dependency direction is one-way: this module imports the rendering
helpers (``_commit_message`` / ``_draft_pr_body``) from
:mod:`coding_executor_live_format`, never from
:mod:`coding_executor_live`, so there is no import-time cycle.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from .coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)
from .coding_executor_live_format import (
    _commit_message,
    _draft_pr_body,
)


logger = logging.getLogger(__name__)


class GithubAppPusher:
    """Pushes the branch + commit via the GitHub App git data API.

    Avoids local ``git push`` so we never need credential setup
    inside the worker. Reads the worktree's commit objects and
    re-creates them on origin via blob → tree → commit → ref.
    """

    def __init__(self, *, live_client: Any) -> None:
        self._live = live_client

    def push(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:
        repo = request.repo_full_name
        if not repo:
            raise ValueError("GithubAppPusher requires CodingExecuteRequest.repo_full_name")
        if not context.worktree_path:
            raise ValueError("GithubAppPusher requires a worktree_path")

        base_branch = request.base_branch or "main"
        base_sha = self._live.get_branch_head_sha(repo=repo, branch=base_branch)
        base_tree = self._live.get_commit_tree_sha(repo=repo, commit_sha=base_sha)

        entries = []
        for rel in context.edited_files or ():
            full = Path(context.worktree_path) / rel
            content = full.read_text(encoding="utf-8")
            blob_sha = self._live.create_blob(repo=repo, content=content)
            entries.append(
                {"path": rel, "mode": "100644", "type": "blob", "sha": blob_sha}
            )
        if not entries:
            # Nothing to push — degenerate but valid.
            return replace(context, pushed=False)

        tree = self._live.create_tree(repo=repo, base_tree=base_tree, entries=entries)
        tree_sha = (
            tree if isinstance(tree, str) else (tree.get("sha") if isinstance(tree, Mapping) else str(tree))
        )

        # Branch ref must exist before create_commit_via_data_api PATCHes it.
        try:
            self._live.create_branch_ref(
                repo=repo, branch=context.branch, base_sha=base_sha
            )
        except Exception as exc:  # noqa: BLE001 - already-exists is acceptable
            if "already exists" not in str(exc).lower():
                raise

        actor_name = "yule-studio engineering-agent"
        actor_email = "engineering-agent[bot]@users.noreply.github.com"
        commit_obj = self._live.create_commit_via_data_api(
            repo=repo,
            branch=context.branch,
            message=_commit_message(request, context),
            tree=str(tree_sha),
            author={"name": actor_name, "email": actor_email},
            committer={"name": actor_name, "email": actor_email},
            parents=[base_sha],
        )
        commit_sha = str(commit_obj.get("sha") or "")
        return replace(context, commit_sha=commit_sha or context.commit_sha, pushed=True)


class GithubAppDraftPRCreator:
    """Opens a draft PR via :class:`LiveGithubAppClient`."""

    def __init__(self, *, live_client: Any) -> None:
        self._live = live_client

    def open(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:
        repo = request.repo_full_name
        if not repo:
            raise ValueError("GithubAppDraftPRCreator requires repo_full_name")
        # P1-M D — 한국어 humanizer 가 slice/세션 정보로 명확한 제목 생성.
        # slice_spec / session_prompt 는 dispatcher 가 request.metadata 에 stamp.
        try:
            from ..coding.human_titles import build_pr_title

            metadata = request.metadata or {}
            slice_spec = (
                metadata.get("slice_spec")
                if isinstance(metadata, Mapping)
                else None
            )
            session_prompt = (
                metadata.get("session_prompt")
                if isinstance(metadata, Mapping)
                else None
            )
            title = build_pr_title(
                session_prompt=str(session_prompt or request.user_request or ""),
                slice_spec=slice_spec if isinstance(slice_spec, Mapping) else None,
                branch_hint=context.branch,
                issue_number=request.issue_number,
            )
        except Exception:  # noqa: BLE001 — never block PR on title helper
            title = (
                f"📝 #{request.issue_number} coding-executor draft"
                if request.issue_number
                else f"📝 coding-executor draft — {context.branch}"
            )
        body = _draft_pr_body(request, context)

        # P1-N — cross-repo PR title + issue anchor hard guard.
        # 옛 wiring 은 "coding-executor draft #4" 같은 기계 제목 / issue
        # 없는 PR 가 그대로 GitHub 로 흘러갔다. 본 가드가 PR 생성 직전
        # raise 해서 다음 PR 부터는 위반 자체가 막힌다.
        try:
            from ..governance.repo_write_policy import (
                IssueAnchorContext,
                enforce_issue_anchor,
                enforce_pr_title,
            )

            enforce_pr_title(title)
            enforce_issue_anchor(
                IssueAnchorContext(
                    branch=context.branch,
                    pr_body=body,
                    issue_number_hint=request.issue_number,
                )
            )
        except Exception as policy_exc:  # noqa: BLE001 — surface as RuntimeError
            # Re-raise so worker maps to REASON_PR_FAILED with policy detail.
            # PolicyViolation 의 reason/detail 이 그대로 worker progress
            # marker 에 노출됨.
            raise

        # P0-T: runtime governance policy gate — PR body 가 5 섹션 +
        # audit block 을 갖는지 검사. caller-driven gate 원칙: validation
        # 결과를 로그/audit 으로 남기되 PR 생성 자체는 진행한다 (operator
        # 가 status 에서 즉시 확인 후 후속 PR 에서 보강 가능).
        try:
            from ..governance.runtime_policy import validate_pr_body

            pr_validation = validate_pr_body(body)
            if not pr_validation.ok:
                logger.warning(
                    "draft PR body policy warning — missing=%s, audit=%s, warnings=%s",
                    pr_validation.missing_sections,
                    pr_validation.audit_block_present,
                    pr_validation.warnings,
                )
        except Exception:  # noqa: BLE001 — never block PR on validator
            pass

        pr_response = self._live.create_draft_pull_request(
            repo=repo,
            head=context.branch,
            base=request.base_branch or "main",
            title=title,
            body=body,
            draft=True,
        )
        pr_number = int(pr_response.get("number") or 0)
        pr_url = str(pr_response.get("html_url") or "")
        return replace(context, pr_number=pr_number or None, pr_url=pr_url)
