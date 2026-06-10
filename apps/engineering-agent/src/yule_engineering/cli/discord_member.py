from __future__ import annotations

import sys
from pathlib import Path

from yule_core import apply_ca_bundle_fallback
from yule_discord.member.bots import (
    load_member_bot_config,
    render_startup_summary,
    select_profile_for_role,
)


def run_discord_member_command(
    repo_root: Path,
    agent_id: str,
    role: str,
    *,
    dry_run: bool = False,
) -> int:
    """Start one member-persona Discord bot by role name.

    --dry-run validates env wiring and prints the activation summary
    without contacting Discord, so we can verify the launcher before
    real tokens are issued.
    """

    config = load_member_bot_config(repo_root=repo_root, agent_id=agent_id)
    for line in render_startup_summary(config):
        print(line, file=sys.stderr)

    profile = select_profile_for_role(config, role, require_token=not dry_run)

    if dry_run:
        status = "active" if profile.active else "skipped (token missing)"
        print(
            f"dry-run: would start {profile.display_label} [{status}] from {profile.env_key}",
            file=sys.stderr,
        )
        return 0

    tls_bundle = apply_ca_bundle_fallback()
    if tls_bundle.source == "certifi-applied":
        print(f"info: {tls_bundle.detail} ({tls_bundle.cafile})", file=sys.stderr)

    try:
        from yule_discord.member.bot import run_member_bot
    except ImportError as exc:
        raise ValueError(
            "discord.py is required to run member bots. "
            "Install project dependencies again with `python -m pip install -e .`."
        ) from exc

    run_member_bot(profile)
    return 0
