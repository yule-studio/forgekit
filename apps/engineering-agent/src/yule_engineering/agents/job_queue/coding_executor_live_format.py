"""Formatting / rendering helpers for the live coding executor.

Split out of :mod:`coding_executor_live` (responsibility: *formatting*).
These are **pure** functions that render operator-facing markdown —
plan notes, commit messages, and draft PR bodies. They have no
dependency on the live-runner classes, so they live one-way: the
runner modules import these, never the reverse.

Behavior-preserving move — see ``coding_executor_live`` for the
original docstrings and governance rationale of each renderer.
"""

from __future__ import annotations

from typing import Any, Mapping

from .coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)


def _render_bootstrap_plan_markdown(
    request: "CodingExecuteRequest",
    context: "WorktreeContext",
    plan: Any,
    result: Any,
) -> str:
    lines = [
        f"# greenfield-bootstrap plan — {context.branch}",
        "",
        f"- session_id: `{request.session_id}`",
        f"- executor_role: `{request.executor_role}`",
        f"- repo: `{request.repo_full_name or '(unset)'}`",
        f"- bootstrap_mode: `{plan.mode}`",
        f"- summary: {plan.summary}",
        "",
        "## scaffold result",
        "",
        f"- files_created ({len(result.files_created)}): {list(result.files_created)}",
        f"- files_skipped_exists ({len(result.files_skipped_exists)}): {list(result.files_skipped_exists)}",
        f"- files_refused_by_scope ({len(result.files_refused_by_scope)}): {list(result.files_refused_by_scope)}",
        f"- write_errors: {list(result.write_errors)}",
        "",
        "## next step",
        "",
        "이 scaffold 는 stack signal (package.json / pyproject.toml / docker-compose) 만",
        "만들어 두는 minimal viable shape 입니다. 실제 product 구현은 후속 coding",
        "job 들이 같은 repo 에 PR 단위로 land 합니다.",
        "",
    ]
    return "\n".join(lines) + "\n"


def _render_plan_markdown(
    request: CodingExecuteRequest, context: WorktreeContext
) -> str:
    lines = [
        f"# coding-executor plan — {context.branch}",
        "",
        f"- session_id: `{request.session_id}`",
        f"- executor_role: `{request.executor_role}`",
        f"- repo: `{request.repo_full_name or '(unset)'}`",
        f"- issue: `#{request.issue_number}`" if request.issue_number else "- issue: (none)",
        f"- base_branch: `{request.base_branch}` @ `{context.base_commit_sha[:10]}`",
        "",
        "## 사용자 요청",
        "",
        request.user_request or "_(empty)_",
        "",
        "## write_scope",
    ]
    for entry in request.write_scope or ("(unspecified)",):
        lines.append(f"- {entry}")
    lines.append("")
    lines.append("## forbidden_scope")
    for entry in request.forbidden_scope or ("(none)",):
        lines.append(f"- {entry}")
    lines.append("")
    lines.append("## safety_rules")
    for entry in request.safety_rules or ("(none)",):
        lines.append(f"- {entry}")
    lines.append("")
    lines.append("## planned executor prompt")
    lines.append("")
    lines.append("```text")
    lines.append((request.generated_prompt or "(empty prompt)").strip())
    lines.append("```")
    lines.append("")
    lines.append(
        "> **Note:** Real LLM-driven edits require operator authorization "
        "(live `claude` / `codex` CLI + secret). This file is the dry record "
        "the executor produced via `RecordOnlyCodeEditor` so the rest of the "
        "pipeline (tests / commit / push / draft PR) can be exercised end-to-end."
    )
    lines.append("")
    return "\n".join(lines)


def _commit_message(
    request: CodingExecuteRequest, context: WorktreeContext
) -> str:
    head = (
        f"📝 #{request.issue_number} coding-executor 계획 기록"
        if request.issue_number
        else "📝 coding-executor 계획 기록"
    )
    return (
        f"{head}\n"
        "\n변경 이유\n"
        f"- coding_execute job (executor={request.executor_role}) 의 RecordOnly editor 산출\n"
        "\n주요 변경 사항\n"
        f"- branch={context.branch} (from {request.base_branch}) 생성\n"
        f"- 계획 markdown 1 건 추가\n"
        "\n비고\n"
        "- 본 commit 은 RecordOnly editor 의 dry 산출. 실 LLM 편집은 후속 PR 의 운영자 승인 + secret 확인 후."
    )


def _draft_pr_body(
    request: CodingExecuteRequest, context: WorktreeContext
) -> str:
    """draft PR body. P0-T runtime_policy.validate_pr_body 통과하도록
    5 섹션 (purpose / scope / risks / tests / issue_linkage) + audit block
    을 모두 갖춘다."""

    test_summary = context.test_summary or {}
    test_status = (
        test_summary.get("status")
        if isinstance(test_summary, Mapping)
        else None
    ) or ("dry_run" if test_summary.get("dry_run") else "unknown")

    parts = [
        "## 📌 관련 이슈",
        f"- close #{request.issue_number}" if request.issue_number else "- (no issue)",
        "",
        "## ✨ 과제 내용 (목적)",
        f"- coding_execute job (executor=`{request.executor_role}`) 산출.",
        "- 본 PR 은 `RecordOnlyCodeEditor` 가 만든 계획 markdown 만 포함합니다 — 실 LLM 편집은 운영자 승인 후 별도.",
        "",
        "## 🎯 범위 (scope)",
        f"- in_scope: write_scope={list(request.write_scope) or '(미지정)'}",
        f"- out_of_scope: forbidden_scope={list(request.forbidden_scope) or '(미지정)'}",
        "",
        "## ⚠️ 리스크 (risks)",
        "- safety_rules 준수: " + (", ".join(request.safety_rules) if request.safety_rules else "(미지정)"),
        "- live editor 미연결 — 본 PR 은 record-only. operator 검토 후 후속 PR 에서 실 편집 land 예정.",
        "",
        "## ✅ 테스트 (tests)",
        f"- test_status: `{test_status}`",
        f"- test_summary: `{dict(test_summary) if isinstance(test_summary, Mapping) else test_summary}`",
        "",
        "## :camera_with_flash: 스크린샷(선택)",
        "_(N/A)_",
        "",
        "## 📚 참고 (references)",
        f"- session_id: `{request.session_id}`",
        f"- branch: `{context.branch}` (from `{request.base_branch}`)",
        f"- commit: `{context.commit_sha[:10] if context.commit_sha else '-'}`",
        "",
        "## 🤖 Agent WorkOS Audit",
        f"- branch: `{context.branch}` (from `{request.base_branch}`)",
        f"- repo: `{request.repo_full_name}`",
        f"- role: `{request.executor_role}`",
        f"- engineering-agent runtime_policy: branch/PR/tag hard rails 적용",
        "- mode: `live` (G6 LiveGithubAppClient — RecordOnly editor)",
        "- merge: do-not-merge until operator review",
    ]
    return "\n".join(parts) + "\n"
