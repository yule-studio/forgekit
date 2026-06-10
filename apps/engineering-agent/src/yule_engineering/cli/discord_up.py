"""``yule discord up`` CLI entry point — dev / single-host launcher.

Composes :mod:`yule_discord.runtime.supervisor` with stdout output.
The supervisor builds the launch inventory and spawns the bots; this
module just handles CLI args, prints the summary, and surfaces a
non-zero exit code when nothing actually started.

A-M8: this command is a **development launcher**, kept working for local
smoke tests of the full Discord surface. The production / always-on path
is the standalone runtime — ``yule runtime up --profile engineering`` on
a single host, or systemd template units calling
``yule run-service <service-id>`` per worker. See
``docs/operations.md`` and ``policies/runtime/agents/engineering-agent/launcher.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

from yule_core import apply_ca_bundle_fallback
from yule_discord.runtime.supervisor import (
    ENGINEERING_AGENT_FAMILY,
    SupervisorInventory,
    build_inventory,
    render_inventory_summary,
    start_all,
)


def run_discord_up_command(
    repo_root: Path,
    *,
    agent_ids: Sequence[str] = (ENGINEERING_AGENT_FAMILY,),
    dry_run: bool = False,
) -> int:
    """Print the inventory, then either dry-run or actually launch.

    Exit codes:
    - ``0`` if at least one bot started (or dry-run completed).
    - ``2`` if every bot was skipped because of missing tokens.
    - ``3`` if a spawn raised.
    """

    inventory = build_inventory(repo_root=repo_root, agent_ids=agent_ids)
    if not dry_run:
        # A-M8: single-line dev framing so the operator notices this
        # is the dev launcher, not the production path. Kept short and
        # to stderr so the inventory summary still reads cleanly.
        print(
            "note: `yule discord up` is the development launcher. "
            "For always-on operation use `yule runtime up` "
            "(single-host) or systemd `yule run-service <id>`.",
            file=sys.stderr,
        )
    for line in render_inventory_summary(inventory):
        print(line, file=sys.stderr)

    if not dry_run:
        # Set up TLS once for the parent process — child processes will
        # inherit the env var that points to the bundled CA file.
        tls_bundle = apply_ca_bundle_fallback()
        if tls_bundle.source == "certifi-applied":
            print(f"info: {tls_bundle.detail} ({tls_bundle.cafile})", file=sys.stderr)

    report = start_all(inventory, dry_run=dry_run)

    for result in report.results:
        if result.started:
            print(f"started: {result.bot_id}", file=sys.stderr)
        elif result.error is not None:
            # Failures must surface the role + diagnostic (no token value
            # — start_all/_build_member_entry only put env-key names and
            # shape categories into the reason).
            print(f"failed:  {result.bot_id} — {result.error}", file=sys.stderr)
        elif result.skipped_reason == "dry-run":
            print(f"dry-run: {result.bot_id}", file=sys.stderr)
        else:
            print(f"skipped: {result.bot_id} ({result.skipped_reason})", file=sys.stderr)

    # Aggregate role-level breakdown so the operator sees at a glance how
    # many roles started vs skipped vs failed without scanning every line.
    started = report.started_count()
    skipped = report.skipped_count()
    failed = report.failed_count()
    if not dry_run:
        print(
            f"summary: {started} started / {skipped} skipped / {failed} failed",
            file=sys.stderr,
        )
        if failed:
            failed_ids = ", ".join(
                r.bot_id for r in report.results if r.error is not None
            )
            print(f"failed roles: {failed_ids}", file=sys.stderr)
        if skipped:
            skipped_ids = ", ".join(
                r.bot_id for r in report.results
                if not r.started and r.error is None
            )
            print(f"skipped roles: {skipped_ids}", file=sys.stderr)

    if dry_run:
        return 0
    if report.failed_count() > 0 and report.started_count() == 0:
        return 3
    if report.started_count() == 0:
        # Every bot was skipped due to missing tokens — fail clearly.
        return 2
    return 0


def parse_agent_ids(raw: Optional[str]) -> Sequence[str]:
    """Parse a CLI ``--agents`` value (comma-separated)."""

    if not raw:
        return (ENGINEERING_AGENT_FAMILY,)
    parts = tuple(item.strip() for item in raw.split(",") if item.strip())
    return parts or (ENGINEERING_AGENT_FAMILY,)
