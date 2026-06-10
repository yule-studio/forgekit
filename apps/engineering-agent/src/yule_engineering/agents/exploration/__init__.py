"""Role-specific repository exploration map (Issue #90 / F3).

Provides read-only catalogs that tell each engineering role where to
look first in the repo (preferred path prefixes, hot files, risky
files) and a deterministic scorer that ranks candidate paths by their
relevance to a task. The map is *read-only*: nothing in this package
mutates the file system or reads file contents — it works on paths and
keywords alone so it can never become a leakage channel.

Public surface (see :mod:`yule_engineering.agents.exploration.repo_map`):

  * :class:`RoleRepoProfile` — per-role catalog.
  * :class:`RepoMap` — bundle of role profiles for one repo root.
  * :class:`ScoredFile` — ranking output (path + numeric score + reason).
  * :func:`build_repo_map` — produce a RepoMap from a repo root.
  * :func:`build_default_repo_map` — RepoMap for *this* repo (helper).
  * :func:`score_file_relevance` — single-path score in 0..1.
  * :func:`rank_files_for_task` — sort candidates by score (desc).

Used by :mod:`agents.decision.context_pack.CodeHintProvider` to feed
role-aware path hints into Discord UX (F6 / #93), but this PR lands
the helpers + regression tests without wiring the provider — that
hand-off ships in a follow-up.
"""

from .repo_map import (
    RepoMap,
    RoleRepoProfile,
    ScoredFile,
    build_default_repo_map,
    build_repo_map,
    rank_files_for_task,
    score_file_relevance,
)

__all__ = (
    "RepoMap",
    "RoleRepoProfile",
    "ScoredFile",
    "build_default_repo_map",
    "build_repo_map",
    "rank_files_for_task",
    "score_file_relevance",
)
