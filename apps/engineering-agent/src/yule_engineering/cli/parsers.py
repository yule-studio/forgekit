"""Subcommand argparse builders for the ``yule`` CLI.

Pure parser-construction split out of :mod:`yule_engineering.cli.main` along
the "subcommand per module" axis. Each ``add_<group>_parser`` helper takes the
top-level ``subparsers`` action and registers one command group's parser tree.

These builders only mutate the ``argparse`` structures passed in — they contain
no dispatch logic. ``main.build_parser`` wires them together; dispatch stays in
``main.main``.
"""

from __future__ import annotations

import argparse


def add_context_parser(subparsers: argparse._SubParsersAction) -> None:
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


def add_doctor_parser(subparsers: argparse._SubParsersAction) -> None:
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check local tool, auth, and model readiness.",
    )
    doctor_parser.add_argument(
        "--agent-id",
        default="engineering-agent",
        help="Agent id to use for manifest-backed checks. Defaults to engineering-agent.",
    )


def add_github_parser(subparsers: argparse._SubParsersAction) -> None:
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

    # G1~G6 GitHub App / WorkOS subcommands.
    github_doctor_parser = github_subparsers.add_parser(
        "doctor",
        help="Diagnose the GitHub App env contract + (optional) live install access.",
    )
    github_doctor_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the doctor result as JSON (redacted).",
    )
    github_doctor_parser.add_argument(
        "--live",
        action="store_true",
        help="Issue an installation token and verify repo access (network call).",
    )

    github_triage_parser = github_subparsers.add_parser(
        "triage",
        help="Senior-engineer triage for a GitHub issue (dry-run only).",
    )
    github_triage_parser.add_argument("issue_number", type=int)
    github_triage_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Required flag — triage currently runs only in dry-run.",
    )
    github_triage_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the triage plan as JSON (redacted).",
    )
    github_triage_parser.add_argument(
        "--repo",
        default=None,
        help="Override owner/repo (defaults to YULE_GITHUB_OWNER/YULE_GITHUB_REPO).",
    )

    github_plan_pr_parser = github_subparsers.add_parser(
        "plan-pr",
        help="Preview branch / draft-PR plan for a GitHub issue (dry-run only).",
    )
    github_plan_pr_parser.add_argument("issue_number", type=int)
    github_plan_pr_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Required flag — plan-pr currently runs only in dry-run.",
    )
    github_plan_pr_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the plan as JSON (redacted).",
    )
    github_plan_pr_parser.add_argument(
        "--base-branch",
        default=None,
        help="Override base branch (defaults to main).",
    )
    github_plan_pr_parser.add_argument(
        "--repo",
        default=None,
        help="Override owner/repo.",
    )

    github_smoke_pr_parser = github_subparsers.add_parser(
        "smoke-pr",
        help="GitHub App live smoke: branch + smoke marker file + draft PR (merge 금지).",
    )
    github_smoke_pr_parser.add_argument(
        "--live",
        action="store_true",
        help="Required flag — performs real GitHub App writes against the repo.",
    )
    github_smoke_pr_parser.add_argument(
        "--issue",
        dest="issue_number",
        type=int,
        default=None,
        help="Optional issue number to triage + reference in the smoke PR.",
    )
    github_smoke_pr_parser.add_argument(
        "--repo",
        default=None,
        help="Override owner/repo.",
    )
    github_smoke_pr_parser.add_argument(
        "--base-branch",
        default=None,
        help="Override base branch (defaults to main).",
    )
    github_smoke_pr_parser.add_argument(
        "--branch-name",
        default=None,
        help="Override smoke branch name (must NOT be a protected branch).",
    )
    github_smoke_pr_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the smoke outcome as JSON.",
    )


def add_daily_parser(subparsers: argparse._SubParsersAction) -> None:
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


def add_calendar_parser(subparsers: argparse._SubParsersAction) -> None:
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


def add_planning_parser(subparsers: argparse._SubParsersAction) -> None:
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


def add_discord_parser(subparsers: argparse._SubParsersAction) -> None:
    discord_parser = subparsers.add_parser(
        "discord",
        help="Run Discord integrations backed by the local orchestrator.",
    )
    discord_subparsers = discord_parser.add_subparsers(dest="discord_command", required=True)

    discord_subparsers.add_parser(
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
        help=(
            "[DEV-ONLY] Spawn planning-bot + engineering gateway + member bots "
            "in one process tree. NOT a production path — does not run the "
            "queue workers (research/role/approval/obsidian-writer) so jobs "
            "enqueued by the gateway will sit unpicked. Production: use "
            "`yule runtime up` (single-host) or systemd `yule run-service`."
        ),
        description=(
            "DEVELOPMENT / SINGLE-HOST DISCORD LAUNCHER (NOT PRODUCTION).\n\n"
            "Spawns each implemented Discord bot (planning + engineering "
            "gateway + 7 member bots) as its own multiprocessing.Process "
            "under one parent. Useful for local smoke tests of the full "
            "Discord surface — but it does NOT spawn the queue workers, so "
            "the gateway will enqueue research_collect / role_take / "
            "approval_post / obsidian_write jobs that nothing picks up.\n\n"
            "If you want a working end-to-end runtime, run `yule runtime up "
            "--profile engineering` (single-host parent supervising every "
            "worker + the gateway) or use systemd template units calling "
            "`yule run-service <service-id>` (production).\n\n"
            "See docs/operations.md for the supported runtime topology and "
            "docs/discord.md §4 for the dev-launcher contract."
        ),
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


def add_runtime_parser(subparsers: argparse._SubParsersAction) -> None:
    runtime_parser = subparsers.add_parser(
        "runtime",
        help=(
            "[PRODUCTION] Always-on engineering runtime. "
            "Subcommands: up (spawn all workers) / status (diagnostic) / "
            "circuit reset (clear breaker). Sibling `yule run-service "
            "<id>` runs a single worker (used by systemd)."
        ),
        description=(
            "ALWAYS-ON ENGINEERING RUNTIME — production / single-host path.\n\n"
            "This is the path that actually processes jobs. Pick one of:\n"
            "  • `yule runtime up --profile engineering` — single-host. One "
            "parent supervises the supervisor + research worker + 7 role "
            "workers + approval worker + obsidian writer + Discord gateway.\n"
            "  • `systemctl start yule.target` — production. Same workers, "
            "each as its own systemd unit invoking `yule run-service "
            "<service-id>`.\n\n"
            "Subcommand reference:\n"
            "  yule runtime up [--dry-run]         # spawn / list services\n"
            "  yule runtime status [--json]        # read-only diagnostic\n"
            "  yule runtime circuit reset <id>     # clear tripped breaker\n"
            "  yule run-service <id>               # single-worker entry "
            "(sibling subcommand; systemd / `runtime up` both call this)\n\n"
            "Dev-only Discord launcher (`yule discord up`) does NOT replace "
            "this — it spawns Discord bots without the queue workers. See "
            "docs/operations.md and deploy/systemd/README.md."
        ),
    )
    runtime_subparsers = runtime_parser.add_subparsers(
        dest="runtime_command", required=True
    )
    runtime_up_parser = runtime_subparsers.add_parser(
        "up",
        help=(
            "Spawn every implemented worker for a profile under one parent "
            "process (single-host production / dev). Engineering profile = "
            "supervisor + research_worker + 7 role workers + approval_worker "
            "+ obsidian_writer + discord_gateway. Use `--dry-run` to list "
            "without spawning. For multi-host / systemd installs run each "
            "service via the sibling `yule run-service <id>` command instead."
        ),
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

    runtime_status_parser = runtime_subparsers.add_parser(
        "status",
        help=(
            "Read-only runtime diagnostic. Per-service health "
            "(ALIVE/STALE/UNKNOWN/CIRCUIT_OPEN/RESERVED) + queue counts "
            "+ recent failures + actionable warnings (with concrete "
            "restart commands) + a 6-step live smoke checklist."
        ),
        description=(
            "Read-only snapshot of the always-on runtime. Output sections:\n"
            "  services      health + heartbeat age + handles description\n"
            "  queue         per-job-type queued / in_progress / failed\n"
            "  recent failures  most-recent FAILED rows w/ error string\n"
            "  warnings      STALE/UNKNOWN/circuit-open with the exact "
            "command to recover (`yule run-service <id>` / `systemctl "
            "restart …` / `yule runtime circuit reset <id>`)\n"
            "  live smoke checklist  6-step verification block to copy "
            "from one screen.\n\n"
            "Use `--post-discord` to mirror the markdown summary to "
            "#봇-상태 (idempotent — dedup-key skips identical reposts)."
        ),
    )
    runtime_status_parser.add_argument(
        "--profile",
        default="engineering",
        help="Service profile to summarise. Defaults to engineering.",
    )
    runtime_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a stable JSON payload instead of the text render.",
    )
    runtime_status_parser.add_argument(
        "--db-path",
        default=None,
        help="Override SQLite cache path (defaults to YULE_CACHE_DB_PATH).",
    )
    runtime_status_parser.add_argument(
        "--failed-limit",
        type=int,
        default=10,
        help="How many recent failed_retryable / failed_terminal rows to list.",
    )
    runtime_status_parser.add_argument(
        "--post-discord",
        action="store_true",
        help=(
            "Also post the markdown summary to #봇-상태. "
            "Idempotent: a state-hash dedup skips identical reposts."
        ),
    )
    runtime_status_parser.add_argument(
        "--force-post",
        action="store_true",
        help="Bypass the dedup-key check for the next post.",
    )

    runtime_circuit_parser = runtime_subparsers.add_parser(
        "circuit",
        help="Inspect / reset persisted circuit-breaker state.",
    )
    runtime_circuit_subparsers = runtime_circuit_parser.add_subparsers(
        dest="runtime_circuit_command", required=True
    )
    runtime_circuit_reset_parser = runtime_circuit_subparsers.add_parser(
        "reset",
        help=(
            "Clear the open-circuit state for one service so the next "
            "supervisor restart can spawn it again."
        ),
    )
    runtime_circuit_reset_parser.add_argument(
        "service_id",
        help="Service id from the inventory (e.g. eng-role-backend-engineer).",
    )
    runtime_circuit_reset_parser.add_argument(
        "--db-path",
        default=None,
        help="Override SQLite cache path (defaults to YULE_CACHE_DB_PATH).",
    )
    runtime_circuit_reset_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a stable JSON payload instead of human-readable text.",
    )


def add_run_service_parser(subparsers: argparse._SubParsersAction) -> None:
    run_service_parser = subparsers.add_parser(
        "run-service",
        help=(
            "Run a single long-running worker by service id (canonical "
            "entrypoint for systemd template units AND `yule runtime up`). "
            "Examples: `yule run-service eng-research-worker`, "
            "`yule run-service eng-role-backend-engineer`, "
            "`yule run-service eng-obsidian-writer`. Use "
            "`yule runtime up --dry-run` to list every service id."
        ),
        description=(
            "Single-worker entrypoint. Both `yule runtime up` and the "
            "systemd template `yule-run-service@<id>.service` invoke this "
            "command — the surrounding supervision (parent process vs. "
            "systemd) is the only thing that differs.\n\n"
            "Service ids come from the inventory in "
            "apps/engineering-agent/src/yule_engineering/runtime/services.py. List them with "
            "`yule runtime up --dry-run`."
        ),
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


def add_engineer_parser(subparsers: argparse._SubParsersAction) -> None:
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


def add_obsidian_parser(subparsers: argparse._SubParsersAction) -> None:
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


def add_supervisor_parser(subparsers: argparse._SubParsersAction) -> None:
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


def add_memory_parser(subparsers: argparse._SubParsersAction) -> None:
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
