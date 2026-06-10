from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

from yule_core import ContextError, load_env_files
from yule_integrations.calendar import CalendarIntegrationError
from yule_integrations.github.issues import GitHubIssueError
from .calendar import (
    run_calendar_cache_cleanup_command,
    run_calendar_cache_inspect_command,
    run_calendar_categories_command,
    run_calendar_events_command,
    run_calendar_sync_command,
    run_calendar_warmup_command,
)
from .context import run_context_command
from .daily import run_daily_warmup_command
from .discord import run_discord_bot_command
from .discord_member import run_discord_member_command
from .discord_up import parse_agent_ids, run_discord_up_command
from .engineer import (
    adapt_workflow_error,
    run_engineer_approve_command,
    run_engineer_complete_command,
    run_engineer_intake_command,
    run_engineer_progress_command,
    run_engineer_reject_command,
    run_engineer_show_command,
)
from ..agents.workflow import WorkflowError
from .doctor import run_doctor_command
from .github import run_github_issues_command
from .github_workos import (
    run_github_doctor_command,
    run_github_plan_pr_command,
    run_github_smoke_pr_command,
    run_github_triage_command,
)
from .memory import run_memory_reindex_command, run_memory_search_command
from .obsidian import run_obsidian_sync_command
from .planning import (
    run_planning_checkpoints_command,
    run_planning_daily_command,
    run_planning_snapshot_command,
)
from .supervisor import run_supervisor_run_once_command
from .parsers import (
    add_calendar_parser,
    add_context_parser,
    add_daily_parser,
    add_discord_parser,
    add_doctor_parser,
    add_engineer_parser,
    add_github_parser,
    add_memory_parser,
    add_obsidian_parser,
    add_planning_parser,
    add_run_service_parser,
    add_runtime_parser,
    add_supervisor_parser,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yule",
        description="Yule Studio Agent orchestrator.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root to read agent configuration from. Defaults to the current directory.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_context_parser(subparsers)
    add_doctor_parser(subparsers)
    add_github_parser(subparsers)
    add_daily_parser(subparsers)
    add_calendar_parser(subparsers)
    add_planning_parser(subparsers)
    add_discord_parser(subparsers)
    add_runtime_parser(subparsers)
    add_run_service_parser(subparsers)
    add_engineer_parser(subparsers)
    add_obsidian_parser(subparsers)
    add_supervisor_parser(subparsers)
    add_memory_parser(subparsers)

    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    repo_root = Path(args.repo_root).resolve()
    os.environ["YULE_REPO_ROOT"] = str(repo_root)
    load_env_files(repo_root)

    try:
        if args.command == "context":
            return run_context_command(repo_root, args.agent_id, args.output)
        if args.command == "doctor":
            return run_doctor_command(repo_root, args.agent_id)
        if args.command == "github" and args.github_command == "issues":
            return run_github_issues_command(args.limit, args.force_refresh)
        if args.command == "github" and args.github_command == "doctor":
            return run_github_doctor_command(
                json_output=args.json_output,
                live=args.live,
            )
        if args.command == "github" and args.github_command == "triage":
            return run_github_triage_command(
                args.issue_number,
                dry_run=args.dry_run,
                json_output=args.json_output,
                repo=args.repo,
            )
        if args.command == "github" and args.github_command == "plan-pr":
            return run_github_plan_pr_command(
                args.issue_number,
                dry_run=args.dry_run,
                base_branch=args.base_branch,
                repo=args.repo,
                json_output=args.json_output,
            )
        if args.command == "github" and args.github_command == "smoke-pr":
            return run_github_smoke_pr_command(
                live=args.live,
                issue_number=args.issue_number,
                repo=args.repo,
                base_branch=args.base_branch,
                branch_name_override=args.branch_name,
                json_output=args.json_output,
            )
        if args.command == "daily" and args.daily_command == "warmup":
            return run_daily_warmup_command(
                args.date,
                args.github_limit,
                args.reminders_file,
                args.skip_calendar,
                args.skip_github,
                args.force_refresh,
                args.reminder_lead_minutes,
                args.json,
                args.use_ollama,
                args.ollama_model,
                args.ollama_endpoint,
                args.ollama_timeout_seconds,
            )
        if args.command == "calendar" and args.calendar_command == "events":
            return run_calendar_events_command(
                args.start_date,
                args.end_date,
                args.json,
                args.force_refresh,
            )
        if args.command == "calendar" and args.calendar_command == "warmup":
            return run_calendar_warmup_command(
                args.start_date,
                args.end_date,
                args.json,
                args.force_refresh,
            )
        if args.command == "calendar" and args.calendar_command == "sync":
            return run_calendar_sync_command(
                args.start_date,
                args.end_date,
                args.json,
                args.force_refresh,
            )
        if args.command == "calendar" and args.calendar_command == "categories":
            return run_calendar_categories_command(
                args.start_date,
                args.end_date,
                args.json,
                args.include_completed,
            )
        if args.command == "calendar" and args.calendar_command == "cache":
            if args.calendar_cache_command == "inspect":
                return run_calendar_cache_inspect_command(
                    args.json,
                    args.limit,
                    args.fresh_only,
                )
            if args.calendar_cache_command == "cleanup":
                return run_calendar_cache_cleanup_command(
                    args.json,
                    args.cache_retention_days,
                    args.state_retention_days,
                )
        if args.command == "planning" and args.planning_command == "daily":
            return run_planning_daily_command(
                args.date,
                args.github_limit,
                args.reminders_file,
                args.skip_calendar,
                args.skip_github,
                args.reminder_lead_minutes,
                args.use_ollama,
                args.ollama_model,
                args.ollama_endpoint,
                args.ollama_timeout_seconds,
                args.json,
            )
        if args.command == "planning" and args.planning_command == "checkpoints":
            return run_planning_checkpoints_command(
                args.date,
                args.at,
                args.reminder_lead_minutes,
                args.window_minutes,
                args.json,
            )
        if args.command == "planning" and args.planning_command == "snapshot":
            return run_planning_snapshot_command(
                args.date,
                args.github_limit,
                args.reminders_file,
                args.skip_calendar,
                args.skip_github,
                args.reminder_lead_minutes,
                args.use_ollama,
                args.ollama_model,
                args.ollama_endpoint,
                args.ollama_timeout_seconds,
                args.json,
            )
        if args.command == "discord" and args.discord_command == "bot":
            return run_discord_bot_command(repo_root)
        if args.command == "discord" and args.discord_command == "member":
            return run_discord_member_command(
                repo_root,
                args.agent,
                args.role,
                dry_run=args.dry_run,
            )
        if args.command == "discord" and args.discord_command == "up":
            return run_discord_up_command(
                repo_root,
                agent_ids=parse_agent_ids(args.agents),
                dry_run=args.dry_run,
            )
        if args.command == "runtime" and args.runtime_command == "status":
            from ..runtime.status_cli import run_runtime_status_command

            return run_runtime_status_command(
                profile=args.profile,
                emit_json=args.json,
                db_path=Path(args.db_path) if args.db_path else None,
                failed_limit=args.failed_limit,
                post_discord=args.post_discord,
                force_post=args.force_post,
            )
        if (
            args.command == "runtime"
            and args.runtime_command == "circuit"
            and args.runtime_circuit_command == "reset"
        ):
            from ..runtime.circuit_cli import run_circuit_reset_command

            return run_circuit_reset_command(
                service_id=args.service_id,
                db_path=Path(args.db_path) if args.db_path else None,
                emit_json=args.json,
            )
        if args.command == "runtime" and args.runtime_command == "up":
            from yule_runtime.subprocess_supervisor import (
                build_dry_run_plan,
                render_dry_run_plan,
                run_runtime_up,
            )
            import asyncio as _asyncio
            import logging as _logging

            _logging.basicConfig(
                level=args.log_level,
                format="[%(name)s] %(levelname)s %(message)s",
            )
            if args.dry_run or args.list:
                plan = build_dry_run_plan(profile=args.profile)
                print(render_dry_run_plan(plan))
                return 0
            try:
                return _asyncio.run(run_runtime_up(profile=args.profile))
            except KeyboardInterrupt:
                return 0

        if args.command == "run-service":
            from ..runtime.run_service import run_service_main

            db = Path(args.db_path) if args.db_path else None
            return run_service_main(
                args.service_id, db_path=db, log_level=args.log_level
            )

        if args.command == "engineer":
            return _dispatch_engineer_command(repo_root, args)
        if args.command == "supervisor" and args.supervisor_command == "run":
            return run_supervisor_run_once_command(
                limit=args.limit,
                only_actionable=args.only_actionable,
            )
        if args.command == "memory" and args.memory_command == "reindex":
            return run_memory_reindex_command(
                repo_root,
                vault_path=args.vault_path,
                skip_obsidian=args.skip_obsidian,
                skip_policies=args.skip_policies,
                skip_workflow=args.skip_workflow,
                json_output=args.json,
            )
        if args.command == "memory" and args.memory_command == "search":
            return run_memory_search_command(
                repo_root,
                query=args.query,
                limit=args.limit,
                source_kind=args.source_kind,
                role=args.role,
                note_kind=args.note_kind,
                task_type=args.task_type,
                json_output=args.json,
            )
        if args.command == "obsidian" and args.obsidian_command == "sync":
            return run_obsidian_sync_command(
                args.session,
                kind=args.kind,
                vault_path=args.vault_path,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                git_commit=args.git_commit,
                git_message=args.git_message,
                project=args.project,
                layout=args.layout,
            )
    except ContextError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except GitHubIssueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except CalendarIntegrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 2


def _dispatch_engineer_command(repo_root: Path, args) -> int:
    if args.engineer_command == "intake":
        return run_engineer_intake_command(
            repo_root,
            args.agent,
            args.prompt,
            task_type=args.task_type,
            write=args.write,
        )
    if args.engineer_command == "approve":
        return run_engineer_approve_command(repo_root, args.agent, args.session)
    if args.engineer_command == "reject":
        return run_engineer_reject_command(repo_root, args.agent, args.session, args.reason)
    if args.engineer_command == "progress":
        return run_engineer_progress_command(repo_root, args.agent, args.session, args.note)
    if args.engineer_command == "complete":
        return run_engineer_complete_command(
            repo_root,
            args.agent,
            args.session,
            args.summary,
            args.references_used,
        )
    if args.engineer_command == "show":
        return run_engineer_show_command(repo_root, args.agent, args.session)
    raise ValueError(f"unknown engineer command: {args.engineer_command}")
