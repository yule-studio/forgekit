from __future__ import annotations

from pathlib import Path

from ..core import apply_ca_bundle_fallback


def run_discord_bot_command(repo_root: Path) -> int:
    tls_bundle = apply_ca_bundle_fallback()
    if tls_bundle.source == "certifi-applied":
        print(f"info: {tls_bundle.detail} ({tls_bundle.cafile})")

    try:
        from ..discord.bot import run_discord_bot
    except ImportError as exc:
        raise ValueError(
            "discord.py is required to run the Discord bot. Install project dependencies again with `python -m pip install -e .`."
        ) from exc

    run_discord_bot(repo_root=repo_root)
    return 0
