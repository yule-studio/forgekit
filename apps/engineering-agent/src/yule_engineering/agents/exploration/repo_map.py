"""Role-specific repo exploration map + relevance scorer (Issue #90 / F3).

Each engineering role (backend / frontend / qa / devops / tech-lead /
ai-engineer / product-designer) gets a :class:`RoleRepoProfile`
listing:

  * ``preferred_prefixes`` — repo-relative path prefixes the role
    should look in first.
  * ``hot_files`` — paths the role tends to touch often (boosts score).
  * ``risky_files`` — paths the role must touch carefully (smaller
    boost than hot_files; the boost flags relevance, not safety).
  * ``test_glob`` / ``docs_glob`` — informational glob hints.

:func:`score_file_relevance` combines these signals with the task's
keywords into a single 0..1 number. :func:`rank_files_for_task` runs
it across an iterable of candidates and returns a tuple sorted by
score (desc).

The module is **read-only**: it never imports ``os.remove`` /
``shutil`` / ``Path.unlink`` and never reads file contents. Tests in
``tests/engineering/test_repo_map_governance.py`` enforce this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Optional, Tuple


# ---------------------------------------------------------------------------
# Score weights (kept module-level so tests can assert exact values).
# ---------------------------------------------------------------------------


PREFIX_WEIGHT: float = 0.6
HOT_FILE_WEIGHT: float = 0.8
RISKY_FILE_WEIGHT: float = 0.7
KEYWORD_OVERLAP_BONUS: float = 0.2
SCORE_CAP: float = 1.0


# ---------------------------------------------------------------------------
# Canonical role ids (must stay aligned with role_profiles_data.py).
# ---------------------------------------------------------------------------


ROLE_BACKEND_ENGINEER: str = "backend-engineer"
ROLE_FRONTEND_ENGINEER: str = "frontend-engineer"
ROLE_QA_ENGINEER: str = "qa-engineer"
ROLE_DEVOPS_ENGINEER: str = "devops-engineer"
ROLE_TECH_LEAD: str = "tech-lead"
ROLE_AI_ENGINEER: str = "ai-engineer"
ROLE_PRODUCT_DESIGNER: str = "product-designer"

ALL_ROLE_IDS: Tuple[str, ...] = (
    ROLE_BACKEND_ENGINEER,
    ROLE_FRONTEND_ENGINEER,
    ROLE_QA_ENGINEER,
    ROLE_DEVOPS_ENGINEER,
    ROLE_TECH_LEAD,
    ROLE_AI_ENGINEER,
    ROLE_PRODUCT_DESIGNER,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleRepoProfile:
    """Per-role exploration catalog.

    All path values are stored as POSIX-style repo-relative strings so
    the scorer can compare them on any platform without re-normalising.
    """

    role: str
    preferred_prefixes: Tuple[str, ...] = ()
    hot_files: Tuple[str, ...] = ()
    risky_files: Tuple[str, ...] = ()
    test_glob: Tuple[str, ...] = ()
    docs_glob: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoMap:
    """Bundle of role profiles for one repo root.

    ``repo_root`` is stored only for callers that want to display the
    base path back to the user; the scorer itself does not touch the
    file system.
    """

    repo_root: Path
    profiles: Mapping[str, RoleRepoProfile] = field(default_factory=dict)

    def profile_for(self, role: str) -> Optional[RoleRepoProfile]:
        """Return the profile for *role*, or ``None`` when unknown.

        Accepts either a canonical short id (``"backend-engineer"``)
        or a fully-qualified address (``"engineering-agent/backend-engineer"``)
        so callers don't have to normalise before lookup.
        """

        if not role:
            return None
        short = role.split("/", 1)[-1].strip()
        return self.profiles.get(short)

    @property
    def roles(self) -> Tuple[str, ...]:
        """Canonical role ids covered by this map (insertion order)."""

        return tuple(self.profiles.keys())


@dataclass(frozen=True)
class ScoredFile:
    """One candidate path with its score and the matching reason.

    ``matched_prefix`` / ``matched_keyword`` are populated only when
    that signal contributed to the score so callers can surface a
    human-readable "why this file" without re-running the scorer.
    """

    path: str
    role: str
    score: float
    matched_prefix: Optional[str] = None
    matched_keyword: Optional[str] = None


# ---------------------------------------------------------------------------
# Profile catalog
# ---------------------------------------------------------------------------


def _backend_engineer_profile() -> RoleRepoProfile:
    return RoleRepoProfile(
        role=ROLE_BACKEND_ENGINEER,
        preferred_prefixes=(
            "apps/engineering-agent/src/yule_engineering/agents",
            "apps/engineering-agent/src/yule_engineering/storage",
            "apps/engineering-agent/src/yule_engineering/integrations",
            "apps/engineering-agent/src/yule_engineering/github_workos",
            "apps/engineering-agent/src/yule_engineering/github_app",
            "apps/engineering-agent/src/yule_engineering/memory",
            "apps/engineering-agent/src/yule_engineering/planning",
        ),
        hot_files=(
            "apps/engineering-agent/src/yule_engineering/agents/job_queue/store.py",
            "apps/engineering-agent/src/yule_engineering/agents/job_queue/worker_loop.py",
            "apps/engineering-agent/src/yule_engineering/agents/job_queue/state_machine.py",
            "apps/engineering-agent/src/yule_engineering/agents/workflow.py",
            "apps/engineering-agent/src/yule_engineering/agents/routing.py",
        ),
        risky_files=(
            "apps/engineering-agent/src/yule_engineering/storage/",
            "apps/engineering-agent/src/yule_engineering/agents/job_queue/store.py",
            "apps/engineering-agent/src/yule_engineering/github_workos/",
            "apps/engineering-agent/src/yule_engineering/agents/job_queue/coding_executor_live.py",
            "apps/engineering-agent/src/yule_engineering/agents/job_queue/coding_executor_worker.py",
        ),
        test_glob=(
            "tests/agents/**",
            "tests/integrations/**",
            "tests/github_app/**",
        ),
        docs_glob=(),
    )


def _frontend_engineer_profile() -> RoleRepoProfile:
    # The repo currently has no UI package — this slot is reserved so
    # the role still appears in the map and downstream consumers don't
    # have to special-case a missing role.
    return RoleRepoProfile(
        role=ROLE_FRONTEND_ENGINEER,
        preferred_prefixes=(),
        hot_files=(),
        risky_files=(),
        test_glob=(),
        docs_glob=(),
    )


def _qa_engineer_profile() -> RoleRepoProfile:
    return RoleRepoProfile(
        role=ROLE_QA_ENGINEER,
        preferred_prefixes=(
            "tests",
            "tests/engineering",
            "tests/agents",
            "tests/runtime",
        ),
        hot_files=(
            "tests/agents/test_coding_executor_worker.py",
            "tests/agents/test_role_selection.py",
            "tests/engineering/test_issue_73_round2_governance.py",
        ),
        risky_files=(
            "tests/agents/test_role_take_live_regression.py",
        ),
        test_glob=("tests/**",),
        docs_glob=(),
    )


def _devops_engineer_profile() -> RoleRepoProfile:
    return RoleRepoProfile(
        role=ROLE_DEVOPS_ENGINEER,
        preferred_prefixes=(
            "apps/engineering-agent/src/yule_engineering/runtime",
            ".github/workflows",
            "deploy",
            "scripts",
        ),
        hot_files=(
            "apps/engineering-agent/src/yule_engineering/runtime/services.py",
            "apps/engineering-agent/src/yule_engineering/runtime/subprocess_supervisor.py",
            ".github/workflows/ci.yml",
        ),
        risky_files=(
            "apps/engineering-agent/src/yule_engineering/runtime/subprocess_supervisor.py",
            "apps/engineering-agent/src/yule_engineering/runtime/services.py",
            ".github/workflows/ci.yml",
        ),
        test_glob=("tests/runtime/**",),
        docs_glob=(),
    )


def _tech_lead_profile() -> RoleRepoProfile:
    return RoleRepoProfile(
        role=ROLE_TECH_LEAD,
        preferred_prefixes=(
            "src",
            "tests",
            "docs",
            "policies",
            "notes/vault-mirror",
        ),
        hot_files=(
            "CLAUDE.md",
            "policies/runtime/agents/engineering-agent/governance.md",
            "apps/engineering-agent/src/yule_engineering/agents/tech_lead_aggregator.py",
            "apps/engineering-agent/src/yule_engineering/agents/role_profiles_data.py",
        ),
        risky_files=(
            "policies/runtime/agents/engineering-agent/governance.md",
        ),
        test_glob=("tests/engineering/**",),
        docs_glob=(
            "notes/vault-mirror/**",
            "docs/**",
            "policies/**",
        ),
    )


def _ai_engineer_profile() -> RoleRepoProfile:
    return RoleRepoProfile(
        role=ROLE_AI_ENGINEER,
        preferred_prefixes=(
            "apps/engineering-agent/src/yule_engineering/agents/decision",
            "apps/engineering-agent/src/yule_engineering/agents/runners",
            "apps/engineering-agent/src/yule_engineering/agents/research",
        ),
        hot_files=(
            "apps/engineering-agent/src/yule_engineering/agents/decision/classifier_factory.py",
            "apps/engineering-agent/src/yule_engineering/agents/decision/router.py",
            "apps/engineering-agent/src/yule_engineering/agents/decision/context_pack.py",
            "apps/engineering-agent/src/yule_engineering/agents/runners/ollama.py",
            "apps/engineering-agent/src/yule_engineering/agents/runners/claude_code.py",
        ),
        risky_files=(
            "apps/engineering-agent/src/yule_engineering/agents/decision/classifier_factory.py",
        ),
        test_glob=("tests/agents/**",),
        docs_glob=(),
    )


def _product_designer_profile() -> RoleRepoProfile:
    return RoleRepoProfile(
        role=ROLE_PRODUCT_DESIGNER,
        preferred_prefixes=(
            "docs",
            "notes/vault-mirror",
        ),
        hot_files=(),
        risky_files=(),
        test_glob=(),
        docs_glob=(
            "notes/vault-mirror/10-projects/yule-studio-agent/decisions/**",
            "docs/**",
        ),
    )


_PROFILE_BUILDERS: Tuple = (
    _backend_engineer_profile,
    _frontend_engineer_profile,
    _qa_engineer_profile,
    _devops_engineer_profile,
    _tech_lead_profile,
    _ai_engineer_profile,
    _product_designer_profile,
)


def build_repo_map(repo_root: Path) -> RepoMap:
    """Produce a :class:`RepoMap` for the given *repo_root*.

    The map is purely declarative — no filesystem scan happens. The
    *repo_root* argument is recorded for display only.
    """

    profiles: dict[str, RoleRepoProfile] = {}
    for builder in _PROFILE_BUILDERS:
        profile = builder()
        profiles[profile.role] = profile
    return RepoMap(repo_root=Path(repo_root), profiles=profiles)


def build_default_repo_map() -> RepoMap:
    """Convenience helper returning a :class:`RepoMap` rooted at this repo.

    The repo root is inferred from this file's location
    (``apps/engineering-agent/src/yule_engineering/agents/exploration/repo_map.py`` → 4 parents up).
    """

    repo_root = Path(__file__).resolve().parents[6]
    return build_repo_map(repo_root)


# ---------------------------------------------------------------------------
# Path / keyword helpers
# ---------------------------------------------------------------------------


_TOKEN_SPLIT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def _normalise_path(path: object) -> str:
    """Return a POSIX-style repo-relative string for *path*.

    Accepts ``str`` or ``Path``. Leading ``./`` is stripped so paths
    coming from ``Path("./src/foo.py")`` match prefix entries that
    don't carry the dot-slash.
    """

    if isinstance(path, Path):
        text = path.as_posix()
    else:
        text = str(path).replace("\\", "/")
    text = text.strip()
    if text.startswith("./"):
        text = text[2:]
    return text


def _tokenise(value: str) -> Tuple[str, ...]:
    """Lowercase tokens extracted from *value*.

    Splits on whitespace and underscores/hyphens, then breaks
    camelCase / PascalCase using a small regex. The result is a tuple
    of unique lowercase tokens of length >= 2 — short noise (1-char)
    is dropped so a single "a" in a path doesn't tilt the overlap.
    """

    if not value:
        return ()
    raw_parts = re.split(r"[\s/_\-\.]+", value)
    tokens: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        if not part:
            continue
        for sub in _TOKEN_SPLIT_RE.findall(part):
            t = sub.lower()
            if len(t) < 2:
                continue
            if t in seen:
                continue
            seen.add(t)
            tokens.append(t)
    return tuple(tokens)


def _path_starts_with(path: str, prefix: str) -> bool:
    """Strict-prefix match that respects directory boundaries.

    ``"apps/engineering-agent/src/yule_engineering/agents"`` matches
    ``"apps/engineering-agent/src/yule_engineering/agents/job_queue/store.py"`` but not
    ``"apps/engineering-agent/src/yule_engineering/agents_data/foo.py"``.
    """

    if not prefix:
        return False
    norm_prefix = prefix.rstrip("/")
    if path == norm_prefix:
        return True
    return path.startswith(norm_prefix + "/")


def _matches_file_entry(path: str, entry: str) -> bool:
    """Match *path* against a hot/risky file entry.

    Directory-shaped entries (trailing ``/``) match any descendant
    path; file-shaped entries match by equality.
    """

    if not entry:
        return False
    if entry.endswith("/"):
        return _path_starts_with(path, entry.rstrip("/"))
    return path == entry


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_file_relevance(
    repo_map: RepoMap,
    *,
    path: object,
    role: str,
    task_keywords: Iterable[str] = (),
) -> float:
    """Return the 0..1 relevance score for *path* under *role*.

    Composition (capped at 1.0):

    * Hot-file hit ⇒ ``HOT_FILE_WEIGHT`` (0.8) — strongest signal.
    * Else, risky-file hit ⇒ ``RISKY_FILE_WEIGHT`` (0.7).
    * Else, preferred-prefix hit ⇒ ``PREFIX_WEIGHT`` (0.6).
    * Plus, if any task_keyword overlaps the path's tokens,
      a flat ``KEYWORD_OVERLAP_BONUS`` (0.2) is added.

    An unknown *role* returns ``0.0`` — the helper is safe to call
    even when the caller hasn't validated the role id.
    """

    profile = repo_map.profile_for(role)
    if profile is None:
        return 0.0

    norm_path = _normalise_path(path)
    if not norm_path:
        return 0.0

    base = 0.0
    if any(_matches_file_entry(norm_path, entry) for entry in profile.hot_files):
        base = HOT_FILE_WEIGHT
    elif any(
        _matches_file_entry(norm_path, entry) for entry in profile.risky_files
    ):
        base = RISKY_FILE_WEIGHT
    elif any(
        _path_starts_with(norm_path, prefix)
        for prefix in profile.preferred_prefixes
    ):
        base = PREFIX_WEIGHT

    if base == 0.0:
        return 0.0

    if _keyword_overlap(norm_path, task_keywords):
        base += KEYWORD_OVERLAP_BONUS

    if base > SCORE_CAP:
        return SCORE_CAP
    return base


def _keyword_overlap(path: str, task_keywords: Iterable[str]) -> Optional[str]:
    """Return the first keyword that overlaps *path*'s tokens, else None."""

    if not task_keywords:
        return None
    path_tokens = set(_tokenise(path))
    if not path_tokens:
        return None
    for kw in task_keywords:
        if kw is None:
            continue
        for token in _tokenise(str(kw)):
            if token in path_tokens:
                return token
    return None


def _matched_prefix(profile: RoleRepoProfile, path: str) -> Optional[str]:
    """Return the entry that drove the score for *path* under *profile*.

    Mirrors the precedence used by :func:`score_file_relevance`:
    hot → risky → preferred_prefix. The returned string is whichever
    catalog entry first matched the path, suitable for surfacing a
    "why this file" hint to the operator.
    """

    for entry in profile.hot_files:
        if _matches_file_entry(path, entry):
            return entry
    for entry in profile.risky_files:
        if _matches_file_entry(path, entry):
            return entry
    for prefix in profile.preferred_prefixes:
        if _path_starts_with(path, prefix):
            return prefix
    return None


def rank_files_for_task(
    repo_map: RepoMap,
    *,
    role: str,
    task_keywords: Iterable[str] = (),
    candidates: Iterable[object] = (),
) -> Tuple[ScoredFile, ...]:
    """Sort *candidates* by score (desc) for the given *role*.

    Entries scoring exactly 0.0 are dropped — the consumer only cares
    about positively-relevant files. Tie-breaks fall back to the path's
    lexicographic order so the ordering is stable across runs.
    """

    profile = repo_map.profile_for(role)
    if profile is None or not candidates:
        return ()

    keyword_list = tuple(task_keywords or ())
    scored: list[ScoredFile] = []
    for raw in candidates:
        norm_path = _normalise_path(raw)
        if not norm_path:
            continue
        score = score_file_relevance(
            repo_map,
            path=norm_path,
            role=role,
            task_keywords=keyword_list,
        )
        if score <= 0.0:
            continue
        scored.append(
            ScoredFile(
                path=norm_path,
                role=profile.role,
                score=score,
                matched_prefix=_matched_prefix(profile, norm_path),
                matched_keyword=_keyword_overlap(norm_path, keyword_list),
            )
        )

    scored.sort(key=lambda sf: (-sf.score, sf.path))
    return tuple(scored)


# ---------------------------------------------------------------------------
# Re-export PurePosixPath so tests / callers wanting to pass a string
# helper have one obvious type — kept here so the module surface is
# self-contained.
# ---------------------------------------------------------------------------


__all__ = (
    "ALL_ROLE_IDS",
    "HOT_FILE_WEIGHT",
    "KEYWORD_OVERLAP_BONUS",
    "PREFIX_WEIGHT",
    "PurePosixPath",
    "RISKY_FILE_WEIGHT",
    "RepoMap",
    "RoleRepoProfile",
    "ROLE_AI_ENGINEER",
    "ROLE_BACKEND_ENGINEER",
    "ROLE_DEVOPS_ENGINEER",
    "ROLE_FRONTEND_ENGINEER",
    "ROLE_PRODUCT_DESIGNER",
    "ROLE_QA_ENGINEER",
    "ROLE_TECH_LEAD",
    "SCORE_CAP",
    "ScoredFile",
    "build_default_repo_map",
    "build_repo_map",
    "rank_files_for_task",
    "score_file_relevance",
)
