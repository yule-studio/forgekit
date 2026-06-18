"""Intro state — decide HERO vs COMPACT header, purely.

The console's first impression is BIG (the wide hero art); once you start working
it folds to a Claude-Code-style COMPACT header (small brand/icon + short meta) so
the composer is the star and the hero art never sits on top eating space.

Two states:

* ``hero``     — first impression: empty transcript + idle, or the ``/about`` ·
  ``/welcome`` surface. The wide 56-col hero art is shown.
* ``compact``  — the working state: typing, palette open, an agent mode, or a
  transcript that already has content. Only the small header shows.

This module is the single PURE decision (no widgets, no IO besides the env map) so
the transition rule is unit-testable. Two operator overrides:

* ``FORGEKIT_HERO_ART=on|off|auto``   — off disables hero art entirely (always
  compact); on/auto allow it.
* ``FORGEKIT_INTRO_MODE=hero|compact|auto`` — hero forces the big art always,
  compact forces the small header always, auto is the state-driven default.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

INTRO_HERO = "hero"
INTRO_COMPACT = "compact"

ENV_INTRO_MODE = "FORGEKIT_INTRO_MODE"
ENV_HERO_ART = "FORGEKIT_HERO_ART"

_MODE_VALUES = (INTRO_HERO, INTRO_COMPACT, "auto")
_ART_VALUES = ("on", "off", "auto")


def intro_mode_setting(env: Optional[Mapping[str, str]] = None) -> str:
    environ = os.environ if env is None else env
    val = (environ.get(ENV_INTRO_MODE) or "").strip().lower()
    return val if val in _MODE_VALUES else "auto"


def hero_art_setting(env: Optional[Mapping[str, str]] = None) -> str:
    environ = os.environ if env is None else env
    val = (environ.get(ENV_HERO_ART) or "").strip().lower()
    return val if val in _ART_VALUES else "auto"


def resolve_intro_mode(
    env: Optional[Mapping[str, str]] = None,
    *,
    hero_available: bool,
    transcript_empty: bool,
    typing: bool = False,
    palette_open: bool = False,
    in_agent: bool = False,
    help_open: bool = False,
    about_open: bool = False,
) -> str:
    """Return ``"hero"`` or ``"compact"`` for the current state + overrides.

    Precedence: ``FORGEKIT_HERO_ART=off`` or no asset → always compact. Then explicit
    ``FORGEKIT_INTRO_MODE`` (hero/compact). Otherwise AUTO: hero on the ``/about``
    surface or a fresh, idle, empty session; compact the moment real work starts
    (typing, palette, an agent mode, help, or any transcript content).
    """

    # Can't show hero art we don't have, and `off` disables it outright.
    if not hero_available or hero_art_setting(env) == "off":
        return INTRO_COMPACT

    mode = intro_mode_setting(env)
    if mode == INTRO_COMPACT:
        return INTRO_COMPACT
    if mode == INTRO_HERO:
        return INTRO_HERO  # operator forces the big art everywhere

    # AUTO — the about surface always gets the hero; otherwise only a fresh session.
    if about_open:
        return INTRO_HERO
    fresh = (
        transcript_empty
        and not typing
        and not palette_open
        and not in_agent
        and not help_open
    )
    return INTRO_HERO if fresh else INTRO_COMPACT


__all__ = (
    "INTRO_HERO",
    "INTRO_COMPACT",
    "ENV_INTRO_MODE",
    "ENV_HERO_ART",
    "intro_mode_setting",
    "hero_art_setting",
    "resolve_intro_mode",
)
