"""Smoke tests for the relocated ``yule_discord`` app package.

Verifies the package imports standalone and that the old
``yule_discord`` path still resolves to the SAME module objects via
the nested ``sys.modules`` compat shims (identity preserved across the move).
"""

from __future__ import annotations

import yule_discord


def test_package_imports() -> None:
    assert yule_discord is not None


def test_legacy_path_aliases_same_objects() -> None:
    # Shallow module identity.
    from yule_discord.ui import formatter as old_formatter
    from yule_discord.ui import formatter as new_formatter

    assert old_formatter is new_formatter

    # Deep / nested module identity (engineering_channel_router is the largest
    # surface — verify the sys.modules alias reaches the bottom of the tree).
    from yule_discord.engineering_channel_router import (
        main as old_main,
    )
    from yule_discord.engineering_channel_router import main as new_main

    assert old_main is new_main


def test_top_package_aliases_same_object() -> None:
    import yule_discord as old_pkg

    assert old_pkg is yule_discord


def test_shim_subpackages_still_point_at_agents() -> None:
    # engineering_team_runtime / engineering_conversation / research_forum were
    # already thin shims re-exporting ``yule_engineering.agents.*``; they moved
    # along with the tree and must keep pointing at agents. The package re-exports
    # the agents symbols, so symbol identity (not module identity) is preserved.
    from yule_discord import engineering_team_runtime
    from yule_engineering.agents import engineering_team_runtime as agents_etr

    assert (
        engineering_team_runtime.kickoff_directive is agents_etr.kickoff_directive
    )
    # research_forum aliases each submodule to the canonical agents package.
    from yule_discord.research_forum import (  # noqa: F401
        config as old_rf_config,
    )
    from yule_discord.research_forum import config as new_rf_config

    assert old_rf_config is new_rf_config
