from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

from ..core import ContextError, load_env_files
from ..integrations.calendar import CalendarIntegrationError
from ..integrations.github.issues import GitHubIssueError
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
from .memory import run_memory_reindex_command, run_memory_search_command
from .obsidian import run_obsidian_sync_command
from .planning import (
    run_planning_checkpoints_command,
    run_planning_daily_command,
    run_planning_snapshot_command,
)
from .supervisor import run_supervisor_run_once_command


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

    context_parser = subparsers.add_parser(
        "context",
        help="Render the loaded context for an agent.",
    )
    context_parser.add_argument(
        "agent_id",
        help="Agent id to load, for example: engineering-agent.",
    )
    context_parser.add_argument(
        "--output",
        help="Optional file path to write the rendered context to.",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check local tool, auth, and model readiness.",
    )
    doctor_parser.add_argument(
        "--agent-id",
        default="engineering-agent",
        help="Agent id to use for manifest-backed checks. Defaults to engineering-agent.",
    )

    github_parser = subparsers.add_parser(
        "github",
        help="Read GitHub data through the authenticated gh CLI.",
    )
    github_subparsers = github_parser.add_subparsers(dest="github_command", required=True)

    github_issues_parser = github_subparsers.add_parser(
        "issues",
        help="List open GitHub issues for the current account.",
    )
    github_issues_parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Maximum number of open issues to fetch. Defaults to 30.",
    )
    github_issues_parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore the local GitHub issue cache and fetch fresh issues.",
    )

    daily_parser = subparsers.add_parser(
        "daily",
        help="Run daily orchestration jobs for planning and Discord snapshots.",
    )
    daily_subparsers = daily_parser.add_subparsers(dest="daily_command", required=True)

    daily_warmup_parser = daily_subparsers.add_parser(
        "warmup",
        help="Sync calendar and GitHub data, then generate today's daily-plan snapshot.",
    )
    daily_warmup_parser.add_argument(
        "--date",
        help="Target date in YYYY-MM-DD format. Defaults to today.",
    )
    daily_warmup_parser.add_argument(
        "--github-limit",
        type=int,
        default=20,
        help="Maximum number of GitHub open issues to include. Defaults to 20.",
    )
    daily_warmup_parser.add_argument(
        "--reminders-file",
        help="Optional JSON file with reminder items.",
    )
    daily_warmup_parser.add_argument(
        "--skip-calendar",
        action="store_true",
        help="Skip calendar sync and build the snapshot from the remaining sources.",
    )
    daily_warmup_parser.add_argument(
        "--skip-github",
        action="store_true",
        help="Skip GitHub issue sync and build the snapshot from the remaining sources.",
    )
    daily_warmup_parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore calendar and GitHub caches during the warmup fetch steps.",
    )
    daily_warmup_parser.add_argument(
        "--reminder-lead-minutes",
        default="10,5",
        help=(
            "Comma-separated minutes before a parsed execution block ends to generate checkpoints. "
            "Defaults to 10,5."
        ),
    )
    _add_ollama_planning_arguments(daily_warmup_parser)
    daily_warmup_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of the default text view.",
    )

    calendar_parser = subparsers.add_parser(
        "calendar",
        help="Read calendar data through supported calendar integrations.",
    )
    calendar_subparsers = calendar_parser.add_subparsers(dest="calendar_command", required=True)

    calendar_events_parser = calendar_subparsers.add_parser(
        "events",
        help="Read Naver calendar items and convert them into structured data.",
    )
    calendar_events_parser.add_argument(
        "--start-date",
        help="Start date in YYYY-MM-DD format. Defaults to today.",
    )
    calendar_events_parser.add_argument(
        "--end-date",
        help="End date in YYYY-MM-DD format. Defaults to the same value as --start-date.",
    )
    calendar_events_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of the default text view.",
    )
    calendar_events_parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore the local cache and fetch fresh calendar data.",
    )

    calendar_warmup_parser = calendar_subparsers.add_parser(
        "warmup",
        help="Prefetch and store calendar data in the local cache.",
    )
    calendar_warmup_parser.add_argument(
        "--start-date",
        help="Start date in YYYY-MM-DD format. Defaults to today.",
    )
    calendar_warmup_parser.add_argument(
        "--end-date",
        help="End date in YYYY-MM-DD format. Defaults to the same value as --start-date.",
    )
    calendar_warmup_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of the default text view.",
    )
    calendar_warmup_parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore the local cache and fetch fresh calendar data.",
    )

    calendar_sync_parser = calendar_subparsers.add_parser(
        "sync",
        help="Fetch calendar data and sync it into the local cache/state database.",
    )
    calendar_sync_parser.add_argument(
        "--start-date",
        help="Start date in YYYY-MM-DD format. Defaults to today.",
    )
    calendar_sync_parser.add_argument(
        "--end-date",
        help="End date in YYYY-MM-DD format. Defaults to the same value as --start-date.",
    )
    calendar_sync_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of the default text view.",
    )
    calendar_sync_parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore the local cache and fetch fresh calendar data.",
    )

    calendar_categories_parser = calendar_subparsers.add_parser(
        "categories",
        help="Show Naver category color codes from the local calendar state database.",
    )
    calendar_categories_parser.add_argument(
        "--start-date",
        help="Start date in YYYY-MM-DD format. Defaults to today.",
    )
    calendar_categories_parser.add_argument(
        "--end-date",
        help="End date in YYYY-MM-DD format. Defaults to the same value as --start-date.",
    )
    calendar_categories_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of the default text view.",
    )
    calendar_categories_parser.add_argument(
        "--include-completed",
        action="store_true",
        help="Include completed calendar todos in the category summary.",
    )

    calendar_cache_parser = calendar_subparsers.add_parser(
        "cache",
        help="Inspect or clean up the local calendar cache.",
    )
    calendar_cache_subparsers = calendar_cache_parser.add_subparsers(dest="calendar_cache_command", required=True)

    calendar_cache_inspect_parser = calendar_cache_subparsers.add_parser(
        "inspect",
        help="Show cached calendar query entries.",
    )
    calendar_cache_inspect_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of the default text view.",
    )
    calendar_cache_inspect_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of cache entries to show. Defaults to 20.",
    )
    calendar_cache_inspect_parser.add_argument(
        "--fresh-only",
        action="store_true",
        help="Show only unexpired cache entries.",
    )

    calendar_cache_cleanup_parser = calendar_cache_subparsers.add_parser(
        "cleanup",
        help="Delete old cache entries and stale calendar state records.",
    )
    calendar_cache_cleanup_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of the default text view.",
    )
    calendar_cache_cleanup_parser.add_argument(
        "--cache-retention-days",
        type=int,
        default=7,
        help="Keep expired cache entries for this many days before deletion. Defaults to 7.",
    )
    calendar_cache_cleanup_parser.add_argument(
        "--state-retention-days",
        type=int,
        default=30,
        help="Keep unseen calendar state records for this many days before deletion. Defaults to 30.",
    )

    planning_parser = subparsers.add_parser(
        "planning",
        help="Build a daily plan from calendar, issues, and reminder inputs.",
    )
    planning_subparsers = planning_parser.add_subparsers(dest="planning_command", required=True)

    planning_daily_parser = planning_subparsers.add_parser(
        "daily",
        help="Generate a daily plan for the target date.",
    )
    planning_daily_parser.add_argument(
        "--date",
        help="Target date in YYYY-MM-DD format. Defaults to today.",
    )
    planning_daily_parser.add_argument(
        "--github-limit",
        type=int,
        default=20,
        help="Maximum number of GitHub open issues to include. Defaults to 20.",
    )
    planning_daily_parser.add_argument(
        "--reminders-file",
        help="Optional JSON file with reminder items.",
    )
    planning_daily_parser.add_argument(
        "--skip-calendar",
        action="store_true",
        help="Skip calendar inputs and build the plan from the remaining sources.",
    )
    planning_daily_parser.add_argument(
        "--skip-github",
        action="store_true",
        help="Skip GitHub issues and build the plan from the remaining sources.",
    )
    planning_daily_parser.add_argument(
        "--reminder-lead-minutes",
        default="10,5",
        help=(
            "Comma-separated minutes before a parsed execution block ends to generate checkpoints. "
            "Defaults to 10,5."
        ),
    )
    _add_ollama_planning_arguments(planning_daily_parser)
    planning_daily_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of the default text view.",
    )

    planning_checkpoints_parser = planning_subparsers.add_parser(
        "checkpoints",
        help="Show due planning checkpoints for the target time window.",
    )
    planning_checkpoints_parser.add_argument(
        "--date",
        help="Target date in YYYY-MM-DD format. Defaults to the date part of --at or today.",
    )
    planning_checkpoints_parser.add_argument(
        "--at",
        help="Reference time in ISO datetime format. Defaults to now.",
    )
    planning_checkpoints_parser.add_argument(
        "--reminder-lead-minutes",
        default="10,5",
        help=(
            "Comma-separated minutes before a parsed execution block ends to generate checkpoints. "
            "Defaults to 10,5."
        ),
    )
    planning_checkpoints_parser.add_argument(
        "--window-minutes",
        type=int,
        default=10,
        help="How many minutes ahead to scan for due checkpoints. Defaults to 10.",
    )
    planning_checkpoints_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of the default text view.",
    )

    planning_snapshot_parser = planning_subparsers.add_parser(
        "snapshot",
        help="Generate and store a daily-plan snapshot for Discord and automation use.",
    )
    planning_snapshot_parser.add_argument(
        "--date",
        help="Target date in YYYY-MM-DD format. Defaults to today.",
    )
    planning_snapshot_parser.add_argument(
        "--github-limit",
        type=int,
        default=20,
        help="Maximum number of GitHub open issues to include. Defaults to 20.",
    )
    planning_snapshot_parser.add_argument(
        "--reminders-file",
        help="Optional JSON file with reminder items.",
    )
    planning_snapshot_parser.add_argument(
        "--skip-calendar",
        action="store_true",
        help="Skip calendar inputs and build the snapshot from the remaining sources.",
    )
    planning_snapshot_parser.add_argument(
        "--skip-github",
        action="store_true",
        help="Skip GitHub issues and build the snapshot from the remaining sources.",
    )
    planning_snapshot_parser.add_argument(
        "--reminder-lead-minutes",
        default="10,5",
        help=(
            "Comma-separated minutes before a parsed execution block ends to generate checkpoints. "
            "Defaults to 10,5."
        ),
    )
    _add_ollama_planning_arguments(planning_snapshot_parser)
    planning_snapshot_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of the default text view.",
    )

    discord_parser = subparsers.add_parser(
        "discord",
        help="Run Discord integrations backed by the local orchestrator.",
    )
    discord_subparsers = discord_parser.add_subparsers(dest="discord_command", required=True)

    discord_bot_parser = discord_subparsers.add_parser(
        "bot",
        help="Run the Discord bot process.",
    )

    discord_member_parser = discord_subparsers.add_parser(
        "member",
        help="Run a single role/member persona Discord bot for a department.",
    )
    discord_member_parser.add_argument(
        "--agent",
        default="engineering-agent",
        help="Department agent id. Defaults to engineering-agent.",
    )
    discord_member_parser.add_argument(
        "--role",
        required=True,
        help="Role to launch. Use 'gateway' for the department gateway, or a member id (e.g. backend-engineer).",
    )
    discord_member_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate env wiring and print the activation summary without contacting Discord.",
    )

    discord_up_parser = discord_subparsers.add_parser(
        "up",
        help="Launch planning-bot + engineering-agent gateway/members in one shot.",
    )
    discord_up_parser.add_argument(
        "--agents",
        default=None,
        help=(
            "Comma-separated department agent ids whose gateway+members will be launched. "
            "Defaults to engineering-agent."
        ),
    )
    discord_up_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the launch inventory without contacting Discord.",
    )

    runtime_parser = subparsers.add_parser(
        "runtime",
        help="Always-on engineering runtime (queue workers + supervisor).",
    )
    runtime_subparsers = runtime_parser.add_subparsers(
        dest="runtime_command", required=True
    )
    runtime_up_parser = runtime_subparsers.add_parser(
        "up",
        help="Spawn every implemented worker for a profile under one parent process.",
    )
    runtime_up_parser.add_argument(
        "--profile",
        default="engineering",
        help="Service profile to launch. Defaults to engineering.",
    )
    runtime_up_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned services + commands without spawning.",
    )
    runtime_up_parser.add_argument(
        "--list",
        action="store_true",
        help="Alias for --dry-run.",
    )
    runtime_up_parser.add_argument(
        "--log-level", default="INFO", help="Python logging level."
    )

    run_service_parser = subparsers.add_parser(
        "run-service",
        help="Run a single long-running worker (called by systemd or runtime up).",
    )
    run_service_parser.add_argument(
        "service_id",
        help="Service id from the inventory (eng-research-worker, eng-role-tech-lead, ...).",
    )
    run_service_parser.add_argument("--log-level", default="INFO")
    run_service_parser.add_argument(
        "--db-path",
        default=None,
        help="Override SQLite cache path (defaults to YULE_CACHE_DB_PATH).",
    )

    engineer_parser = subparsers.add_parser(
        "engineer",
        help="Drive the engineering-agent Discord workflow (intake/approve/progress/complete).",
    )
    engineer_parser.add_argument(
        "--agent",
        default="engineering-agent",
        help="Department agent id. Defaults to engineering-agent.",
    )
    engineer_subparsers = engineer_parser.add_subparsers(dest="engineer_command", required=True)

    engineer_intake_parser = engineer_subparsers.add_parser(
        "intake",
        help="Accept a new task and produce the dispatcher plan + intake message.",
    )
    engineer_intake_parser.add_argument("--prompt", required=True, help="Natural-language task prompt.")
    engineer_intake_parser.add_argument(
        "--task-type",
        help="Explicit task type override (e.g. landing-page, backend-feature).",
    )
    engineer_intake_parser.add_argument(
        "--write",
        action="store_true",
        help="Mark the task as write-requested. Stays blocked until `engineer approve`.",
    )

    engineer_approve_parser = engineer_subparsers.add_parser(
        "approve",
        help="Approve a session that is waiting for write confirmation.",
    )
    engineer_approve_parser.add_argument("--session", required=True, help="Session id.")

    engineer_reject_parser = engineer_subparsers.add_parser(
        "reject",
        help="Reject a session and stop the workflow.",
    )
    engineer_reject_parser.add_argument("--session", required=True, help="Session id.")
    engineer_reject_parser.add_argument("--reason", required=True, help="Rejection reason.")

    engineer_progress_parser = engineer_subparsers.add_parser(
        "progress",
        help="Append a progress note to an approved session.",
    )
    engineer_progress_parser.add_argument("--session", required=True, help="Session id.")
    engineer_progress_parser.add_argument("--note", required=True, help="Progress note.")

    engineer_complete_parser = engineer_subparsers.add_parser(
        "complete",
        help="Finalize a session and produce the completion report.",
    )
    engineer_complete_parser.add_argument("--session", required=True, help="Session id.")
    engineer_complete_parser.add_argument(
        "--summary",
        required=True,
        help="Final summary text for the completion report.",
    )
    engineer_complete_parser.add_argument(
        "--references-used",
        help="Path to a JSON array of {title, source, url, rationale} reference items.",
    )

    engineer_show_parser = engineer_subparsers.add_parser(
        "show",
        help="Print a session's current state as JSON.",
    )
    engineer_show_parser.add_argument("--session", required=True, help="Session id.")

    obsidian_parser = subparsers.add_parser(
        "obsidian",
        help="Write engineering-agent research notes into a local Obsidian vault.",
    )
    obsidian_subparsers = obsidian_parser.add_subparsers(dest="obsidian_command", required=True)

    obsidian_sync_parser = obsidian_subparsers.add_parser(
        "sync",
        help="Render a session's research pack and write it under OBSIDIAN_VAULT_PATH.",
    )
    obsidian_sync_parser.add_argument(
        "--session",
        required=True,
        help="Workflow session id whose research_pack should be exported.",
    )
    obsidian_sync_parser.add_argument(
        "--kind",
        choices=("research", "decision", "reference", "knowledge"),
        help="Override export kind. Defaults to research (or decision when synthesis is present).",
    )
    obsidian_sync_parser.add_argument(
        "--vault-path",
        help="Override OBSIDIAN_VAULT_PATH for this run only.",
    )
    obsidian_sync_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the target file if it already exists. Default refuses to clobber.",
    )
    obsidian_sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render and validate the path without writing anything.",
    )
    obsidian_sync_parser.add_argument(
        "--git-commit",
        action="store_true",
        help=(
            "After writing, stage and commit the synced note in the Obsidian "
            "vault's git repository. Off by default. Never pushes. Refuses to "
            "run when the vault repo has pre-existing staged changes."
        ),
    )
    obsidian_sync_parser.add_argument(
        "--git-message",
        help="Custom commit message. Defaults to 'obsidian sync: <session_id> ...'.",
    )
    obsidian_sync_parser.add_argument(
        "--project",
        help=(
            "Project slug to use for the yule-agent-vault layout — note lands "
            "under 10-projects/<project>/<kind>/. Resolution order: this flag "
            "→ session.extra['project'] → OBSIDIAN_DEFAULT_PROJECT env → "
            "yule-studio-agent."
        ),
    )
    obsidian_sync_parser.add_argument(
        "--layout",
        choices=("yule-agent-vault", "legacy-agent"),
        help=(
            "Override OBSIDIAN_EXPORT_LAYOUT. Defaults to yule-agent-vault. "
            "Use legacy-agent only for vaults still on Agents/Engineering/..."
        ),
    )

    supervisor_parser = subparsers.add_parser(
        "supervisor",
        help="Read-only runtime status diagnostic for engineering-agent sessions.",
    )
    supervisor_subparsers = supervisor_parser.add_subparsers(
        dest="supervisor_command", required=True
    )
    supervisor_run_parser = supervisor_subparsers.add_parser(
        "run",
        help=(
            "Print a runtime status summary across recent sessions. "
            "Detect/report/propose only — never auto-writes."
        ),
    )
    supervisor_run_parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Run once and exit. Reserved for future continuous mode; "
            "currently the supervisor always runs once."
        ),
    )
    supervisor_run_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of recent sessions to inspect. Defaults to 20.",
    )
    supervisor_run_parser.add_argument(
        "--only-actionable",
        action="store_true",
        help=(
            "Only print sessions with at least one stale / blocked / failed signal."
        ),
    )

    memory_parser = subparsers.add_parser(
        "memory",
        help="Manage the local engineering-agent memory index.",
    )
    memory_subparsers = memory_parser.add_subparsers(dest="memory_command", required=True)

    memory_reindex_parser = memory_subparsers.add_parser(
        "reindex",
        help="Reindex Obsidian notes, policy docs, and workflow artifacts.",
    )
    memory_reindex_parser.add_argument(
        "--vault-path",
        help="Override OBSIDIAN_VAULT_PATH for this run only.",
    )
    memory_reindex_parser.add_argument(
        "--skip-obsidian",
        action="store_true",
        help="Skip Obsidian vault ingestion (e.g. when vault is unavailable).",
    )
    memory_reindex_parser.add_argument(
        "--skip-policies",
        action="store_true",
        help="Skip repo policy/README ingestion.",
    )
    memory_reindex_parser.add_argument(
        "--skip-workflow",
        action="store_true",
        help="Skip workflow session artifact ingestion.",
    )
    memory_reindex_parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable counts.",
    )

    memory_search_parser = memory_subparsers.add_parser(
        "search",
        help="Search the local memory index.",
    )
    memory_search_parser.add_argument("query", help="Free-text search query.")
    memory_search_parser.add_argument(
        "--limit", type=int, default=10, help="Maximum results. Defaults to 10."
    )
    memory_search_parser.add_argument(
        "--source-kind",
        choices=("obsidian", "policy", "workflow"),
        help="Restrict to one source kind.",
    )
    memory_search_parser.add_argument(
        "--role",
        help="Filter by author role (frontmatter roles[0]).",
    )
    memory_search_parser.add_argument(
        "--note-kind",
        choices=("research", "decision", "reference"),
        help="Filter by note kind.",
    )
    memory_search_parser.add_argument(
        "--task-type",
        help="Filter by task_type.",
    )
    memory_search_parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON results.",
    )

    return parser


def _add_ollama_planning_arguments(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(use_ollama=None)
    parser.add_argument(
        "--use-ollama",
        dest="use_ollama",
        action="store_true",
        help="Use Ollama to rewrite the morning briefing. Overrides OLLAMA_PLANNING_ENABLED.",
    )
    parser.add_argument(
        "--no-ollama",
        dest="use_ollama",
        action="store_false",
        help="Disable Ollama for this run even when OLLAMA_PLANNING_ENABLED=true.",
    )
    parser.add_argument(
        "--ollama-model",
        help="Ollama model to use. Defaults to OLLAMA_MODEL or gemma3:latest.",
    )
    parser.add_argument(
        "--ollama-endpoint",
        help="Ollama API endpoint. Defaults to OLLAMA_ENDPOINT or http://localhost:11434.",
    )
    parser.add_argument(
        "--ollama-timeout-seconds",
        type=int,
        help="Ollama request timeout. Defaults to OLLAMA_TIMEOUT_SECONDS or 20.",
    )


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
        if args.command == "runtime" and args.runtime_command == "up":
            from ..runtime.subprocess_supervisor import (
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
