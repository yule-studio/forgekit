from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from ..agents import (
    Dispatcher,
    TaskType,
    WorkflowError,
    WorkflowOrchestrator,
    build_participants_pool,
)


def _build_orchestrator(repo_root: Path, agent_id: str) -> WorkflowOrchestrator:
    pool = build_participants_pool(repo_root, agent_id)
    return WorkflowOrchestrator(Dispatcher(pool))


def run_engineer_intake_command(
    repo_root: Path,
    agent_id: str,
    prompt: str,
    *,
    task_type: Optional[str],
    write: bool,
) -> int:
    if not prompt.strip():
        raise ValueError("--prompt must not be empty")

    parsed_task_type: Optional[TaskType] = None
    if task_type:
        try:
            parsed_task_type = TaskType(task_type)
        except ValueError as exc:
            raise ValueError(
                f"--task-type must be one of {[t.value for t in TaskType]}, got {task_type!r}"
            ) from exc

    orchestrator = _build_orchestrator(repo_root, agent_id)
    result = orchestrator.intake(
        prompt=prompt,
        task_type=parsed_task_type,
        write_requested=write,
    )
    print(result.message)
    print(f"\nsession_id={result.session.session_id}", file=sys.stderr)
    return 0


def run_engineer_approve_command(repo_root: Path, agent_id: str, session_id: str) -> int:
    orchestrator = _build_orchestrator(repo_root, agent_id)
    session = orchestrator.approve(session_id)
    print(f"approved session={session.session_id} state={session.state.value}", file=sys.stderr)
    return 0


def run_engineer_reject_command(
    repo_root: Path,
    agent_id: str,
    session_id: str,
    reason: str,
) -> int:
    orchestrator = _build_orchestrator(repo_root, agent_id)
    session = orchestrator.reject(session_id, reason=reason)
    print(
        f"rejected session={session.session_id} reason={session.rejection_reason}",
        file=sys.stderr,
    )
    return 0


def run_engineer_progress_command(
    repo_root: Path,
    agent_id: str,
    session_id: str,
    note: str,
) -> int:
    orchestrator = _build_orchestrator(repo_root, agent_id)
    result = orchestrator.progress(session_id, note=note)
    print(result.message)
    return 0


def run_engineer_complete_command(
    repo_root: Path,
    agent_id: str,
    session_id: str,
    summary: str,
    references_used_path: Optional[str],
) -> int:
    references = []
    if references_used_path:
        path = Path(references_used_path)
        if not path.exists():
            raise ValueError(f"--references-used file not found: {references_used_path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"--references-used is not valid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise ValueError("--references-used must be a JSON array of objects")
        references = [item for item in data if isinstance(item, dict)]

    orchestrator = _build_orchestrator(repo_root, agent_id)
    result = orchestrator.complete(session_id, summary=summary, references_used=references)
    print(result.message)
    return 0


def run_engineer_show_command(repo_root: Path, agent_id: str, session_id: str) -> int:
    orchestrator = _build_orchestrator(repo_root, agent_id)
    session = orchestrator.get(session_id)
    if session is None:
        raise ValueError(f"session {session_id} not found")
    payload = {
        "session_id": session.session_id,
        "state": session.state.value,
        "task_type": session.task_type,
        "executor_role": session.executor_role,
        "executor_runner": session.executor_runner,
        "write_requested": session.write_requested,
        "write_blocked_reason": session.write_blocked_reason,
        "references_user": list(session.references_user),
        "references_suggested": list(session.references_suggested),
        "references_used": [dict(item) for item in session.references_used],
        "progress_notes": list(session.progress_notes),
        "summary": session.summary,
        "rejection_reason": session.rejection_reason,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def adapt_workflow_error(exc: WorkflowError) -> ValueError:
    return ValueError(str(exc))
