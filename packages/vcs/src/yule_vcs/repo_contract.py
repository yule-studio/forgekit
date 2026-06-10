"""RepoContract discovery — P0-H stage 2 (#140).

Implements the dataclass + discovery defined in
``policies/runtime/agents/engineering-agent/repo-contract-discovery.md``
(land in stage 1 #139). When the gateway parses a GitHub URL we call
:func:`discover_repo_contract` to scan the *target* repo's conventions
(issue/PR templates, CONTRIBUTING, README, CODEOWNERS, workflows) so
subsequent workflow / commit / PR decisions follow that repo's rules
instead of Yule defaults.

Two backends are supported:

  * **gh CLI** (preferred when authenticated) — uses ``gh api`` to
    fetch tree listings without cloning. Honors private repo auth.
  * **local clone** — when the user already has the repo on disk
    (``workspace_root/<owner>/<repo>``), we scan that path directly.

When neither backend is available, the discoverer returns a
:class:`RepoContract` with ``fallback=True`` and a populated
``failure_mode`` so the caller can surface "no contract found" to
the user instead of pretending Yule defaults apply.

**Fake success forbidden** (stage-1 policy §6).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Tuple


# Discovery uses these priority paths from stage-1 policy §3 (repo-contract-discovery.md).
_ISSUE_TEMPLATE_PATHS: Tuple[str, ...] = (
    ".github/ISSUE_TEMPLATE",  # directory — listed contents
    ".github/ISSUE_TEMPLATE.md",
)
_PR_TEMPLATE_PATHS: Tuple[str, ...] = (
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/PULL_REQUEST_TEMPLATE",
    ".github/pull_request_template.md",
    "PULL_REQUEST_TEMPLATE.md",
)
_CONTRIBUTING_PATHS: Tuple[str, ...] = (
    "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
)
_README_PATHS: Tuple[str, ...] = (
    "README.md",
    "README.MD",
    "Readme.md",
    "README",
)
_CODEOWNERS_PATHS: Tuple[str, ...] = (
    "CODEOWNERS",
    ".github/CODEOWNERS",
    "docs/CODEOWNERS",
)

# Tag / version policy hints — P0-S end-to-end.
# agent 가 release 정책을 추측하지 않고 repo 의 실제 신호를 기준으로 처리/
# 보류 결정. 없으면 "자동 tag/version 미적용" 으로 audit.
_CHANGELOG_PATHS: Tuple[str, ...] = (
    "CHANGELOG.md",
    "CHANGELOG",
    "CHANGES.md",
    "HISTORY.md",
    "docs/CHANGELOG.md",
)
_VERSION_FILES: Tuple[str, ...] = (
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "setup.cfg",
    "setup.py",
    "VERSION",
    "version.txt",
)
# Workflow 파일 이름에 "release" / "publish" / "tag" 가 들어가면 release
# automation 신호로 본다.
_RELEASE_WORKFLOW_TOKENS: Tuple[str, ...] = ("release", "publish", "tag")


@dataclass(frozen=True)
class RepoContract:
    """Discovered conventions for an external repo.

    All fields are optional except ``owner`` / ``repo``. ``fallback``
    indicates we found *nothing* and Yule defaults will be used.
    ``failure_mode`` captures why discovery degraded (no permission,
    no network, neither backend available).
    """

    owner: str
    repo: str
    primary_branch: Optional[str] = None
    issue_templates: Tuple[str, ...] = ()  # discovered paths (repo-relative)
    pr_templates: Tuple[str, ...] = ()
    contributing: Optional[str] = None
    readme: Optional[str] = None
    codeowners: Optional[str] = None
    workflows: Tuple[str, ...] = ()
    branch_protection_hint: Optional[str] = None
    branch_strategy: Optional[str] = None
    commit_convention: Optional[str] = None
    ssot_paths: Tuple[str, ...] = ()
    fallback: bool = False
    failure_mode: Optional[str] = None  # "no_backend" | "permission_denied" | "not_found" | etc.
    backend: Optional[str] = None  # "gh_cli" | "local_clone"
    # P0-S — tag/version policy 신호. 비어있으면 "정책 없음" 으로 audit.
    changelog: Optional[str] = None
    version_files: Tuple[str, ...] = ()
    release_workflows: Tuple[str, ...] = ()
    tag_policy: Optional[str] = None
    """One of: 'changelog_driven', 'workflow_driven', 'version_file_only',
    'none'. :func:`derive_tag_policy` decides."""

    @property
    def has_any_contract(self) -> bool:
        return bool(
            self.issue_templates
            or self.pr_templates
            or self.contributing
            or self.codeowners
            or self.workflows
        )

    @property
    def has_tag_policy(self) -> bool:
        return self.tag_policy not in (None, "", "none")

    def to_dict(self) -> Mapping[str, object]:
        return {
            "owner": self.owner,
            "repo": self.repo,
            "primary_branch": self.primary_branch,
            "issue_templates": list(self.issue_templates),
            "pr_templates": list(self.pr_templates),
            "contributing": self.contributing,
            "readme": self.readme,
            "codeowners": self.codeowners,
            "workflows": list(self.workflows),
            "branch_protection_hint": self.branch_protection_hint,
            "branch_strategy": self.branch_strategy,
            "commit_convention": self.commit_convention,
            "ssot_paths": list(self.ssot_paths),
            "fallback": self.fallback,
            "failure_mode": self.failure_mode,
            "backend": self.backend,
            "changelog": self.changelog,
            "version_files": list(self.version_files),
            "release_workflows": list(self.release_workflows),
            "tag_policy": self.tag_policy,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "RepoContract":
        return cls(
            owner=str(payload.get("owner") or ""),
            repo=str(payload.get("repo") or ""),
            primary_branch=_coerce_optional_str(payload.get("primary_branch")),
            issue_templates=tuple(_coerce_str_seq(payload.get("issue_templates"))),
            pr_templates=tuple(_coerce_str_seq(payload.get("pr_templates"))),
            contributing=_coerce_optional_str(payload.get("contributing")),
            readme=_coerce_optional_str(payload.get("readme")),
            codeowners=_coerce_optional_str(payload.get("codeowners")),
            workflows=tuple(_coerce_str_seq(payload.get("workflows"))),
            branch_protection_hint=_coerce_optional_str(payload.get("branch_protection_hint")),
            branch_strategy=_coerce_optional_str(payload.get("branch_strategy")),
            commit_convention=_coerce_optional_str(payload.get("commit_convention")),
            ssot_paths=tuple(_coerce_str_seq(payload.get("ssot_paths"))),
            fallback=bool(payload.get("fallback") or False),
            failure_mode=_coerce_optional_str(payload.get("failure_mode")),
            backend=_coerce_optional_str(payload.get("backend")),
            changelog=_coerce_optional_str(payload.get("changelog")),
            version_files=tuple(_coerce_str_seq(payload.get("version_files"))),
            release_workflows=tuple(_coerce_str_seq(payload.get("release_workflows"))),
            tag_policy=_coerce_optional_str(payload.get("tag_policy")),
        )

    def summary_line(self) -> str:
        """One-line human summary for status / PR body."""

        if self.fallback:
            reason = self.failure_mode or "no_backend"
            return f"⚠️ {self.owner}/{self.repo} — RepoContract fallback ({reason}) · Yule 기본 규칙 사용"
        parts: list[str] = []
        if self.issue_templates:
            parts.append(f"issue_templates={len(self.issue_templates)}")
        if self.pr_templates:
            parts.append(f"pr_templates={len(self.pr_templates)}")
        if self.contributing:
            parts.append("contributing")
        if self.codeowners:
            parts.append("codeowners")
        if self.workflows:
            parts.append(f"workflows={len(self.workflows)}")
        backend_tag = f" [{self.backend}]" if self.backend else ""
        detail = ", ".join(parts) if parts else "no convention files found"
        return f"✅ {self.owner}/{self.repo} — {detail}{backend_tag}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def discover_repo_contract(
    *,
    owner: str,
    repo: str,
    workspace_root: Optional[str] = None,
    gh_cli_runner=None,
) -> RepoContract:
    """Discover *owner/repo* conventions. Best-effort, never raises.

    Resolution order:

      1. If ``workspace_root/<owner>/<repo>`` exists locally → local-clone backend.
      2. Else try gh CLI (``gh api repos/<owner>/<repo>/...``).
      3. Else return a fallback contract with ``failure_mode='no_backend'``.

    *gh_cli_runner* is the subprocess seam (callable with same shape as
    ``subprocess.run``) so tests can inject without spawning a real
    process.
    """

    if not owner or not repo:
        return RepoContract(
            owner=owner or "",
            repo=repo or "",
            fallback=True,
            failure_mode="invalid_target",
        )

    # 1. local-clone backend
    if workspace_root:
        local = _try_local_clone(
            owner=owner, repo=repo, workspace_root=Path(workspace_root)
        )
        if local is not None:
            return local

    # 2. gh CLI backend
    runner = gh_cli_runner or _default_subprocess_run
    gh = _try_gh_cli(owner=owner, repo=repo, runner=runner)
    if gh is not None:
        return gh

    # 3. fallback
    return RepoContract(
        owner=owner,
        repo=repo,
        fallback=True,
        failure_mode="no_backend",
    )


# ---------------------------------------------------------------------------
# Backend — local clone
# ---------------------------------------------------------------------------


def _try_local_clone(
    *,
    owner: str,
    repo: str,
    workspace_root: Path,
) -> Optional[RepoContract]:
    repo_root = workspace_root / owner / repo
    if not repo_root.is_dir():
        return None
    return _scan_paths_under(repo_root, owner=owner, repo=repo, backend="local_clone")


def _scan_paths_under(
    root: Path, *, owner: str, repo: str, backend: str
) -> RepoContract:
    """Walk the well-known paths under *root*. Pure path inspection."""

    found_issue = _find_first_existing(root, _ISSUE_TEMPLATE_PATHS, allow_dir=True)
    issue_templates: Tuple[str, ...] = ()
    if found_issue is not None:
        full = root / found_issue
        if full.is_dir():
            issue_templates = tuple(
                f"{found_issue}/{entry.name}"
                for entry in sorted(full.iterdir())
                if entry.is_file()
            )
        else:
            issue_templates = (found_issue,)

    pr_templates: Tuple[str, ...] = ()
    for candidate in _PR_TEMPLATE_PATHS:
        if (root / candidate).is_file():
            pr_templates = (candidate,)
            break

    contributing = _find_first_existing(root, _CONTRIBUTING_PATHS)
    readme = _find_first_existing(root, _README_PATHS)
    codeowners = _find_first_existing(root, _CODEOWNERS_PATHS)

    workflows: Tuple[str, ...] = ()
    workflows_dir = root / ".github" / "workflows"
    if workflows_dir.is_dir():
        workflows = tuple(
            f".github/workflows/{entry.name}"
            for entry in sorted(workflows_dir.iterdir())
            if entry.is_file() and entry.suffix in (".yml", ".yaml")
        )

    changelog = _find_first_existing(root, _CHANGELOG_PATHS)
    version_files = tuple(
        path for path in _VERSION_FILES if (root / path).is_file()
    )
    release_workflows = tuple(
        wf for wf in workflows
        if any(token in Path(wf).name.lower() for token in _RELEASE_WORKFLOW_TOKENS)
    )
    tag_policy = derive_tag_policy(
        changelog=changelog,
        version_files=version_files,
        release_workflows=release_workflows,
    )

    ssot_paths = tuple(
        path
        for path in [
            *(issue_templates or ()),
            *(pr_templates or ()),
            contributing,
            readme,
            codeowners,
            *(workflows or ()),
            changelog,
            *(version_files or ()),
        ]
        if path
    )

    primary_branch = _read_primary_branch_from_workflows(root)
    branch_strategy = _detect_branch_strategy(root, contributing)
    commit_convention = _detect_commit_convention(root, contributing)

    contract = RepoContract(
        owner=owner,
        repo=repo,
        primary_branch=primary_branch,
        issue_templates=issue_templates,
        pr_templates=pr_templates,
        contributing=contributing,
        readme=readme,
        codeowners=codeowners,
        workflows=workflows,
        branch_strategy=branch_strategy,
        commit_convention=commit_convention,
        ssot_paths=ssot_paths,
        backend=backend,
        changelog=changelog,
        version_files=version_files,
        release_workflows=release_workflows,
        tag_policy=tag_policy,
    )
    return contract


# ---------------------------------------------------------------------------
# Backend — gh CLI
# ---------------------------------------------------------------------------


def _try_gh_cli(
    *,
    owner: str,
    repo: str,
    runner,
) -> Optional[RepoContract]:
    """Use ``gh api repos/<owner>/<repo>/git/trees/HEAD?recursive=1`` to list paths.

    Returns ``None`` when gh CLI is unavailable / unauthenticated /
    repo not accessible. Caller falls through to the fallback contract.
    """

    # Quick probe: list HEAD tree. If gh is missing / unauthenticated,
    # this returns a non-zero exit and we return None.
    try:
        result = runner(
            [
                "gh",
                "api",
                f"repos/{owner}/{repo}",
                "--jq",
                ".default_branch",
            ],
            timeout=10,
        )
    except FileNotFoundError:
        return None  # gh not installed
    except Exception:  # noqa: BLE001 - subprocess errors → fallback
        return None
    if result.returncode != 0:
        return None
    primary_branch = (result.stdout or "").strip().strip('"') or "main"

    try:
        tree = runner(
            [
                "gh",
                "api",
                f"repos/{owner}/{repo}/git/trees/{primary_branch}",
                "--jq",
                ".tree[] | .path",
            ],
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        return None
    if tree.returncode != 0:
        return None
    top_paths = tuple(line.strip() for line in (tree.stdout or "").splitlines() if line.strip())

    # Probe .github subtree separately because GitHub's trees endpoint
    # returns only the requested level's entries.
    try:
        gh_tree = runner(
            [
                "gh",
                "api",
                f"repos/{owner}/{repo}/contents/.github",
            ],
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        gh_tree = None
    github_dir_paths: Tuple[str, ...] = ()
    if gh_tree is not None and gh_tree.returncode == 0:
        try:
            entries = json.loads(gh_tree.stdout or "[]")
            github_dir_paths = tuple(
                f".github/{entry.get('name')}"
                for entry in entries
                if entry.get("name")
            )
        except json.JSONDecodeError:
            github_dir_paths = ()

    all_paths = tuple(top_paths) + tuple(github_dir_paths)

    # ISSUE_TEMPLATE / PR_TEMPLATE
    issue_templates = _select_present(all_paths, _ISSUE_TEMPLATE_PATHS, allow_prefix=True)
    pr_templates = _select_present(all_paths, _PR_TEMPLATE_PATHS, allow_prefix=False)
    contributing = _first_present(all_paths, _CONTRIBUTING_PATHS)
    readme = _first_present(all_paths, _README_PATHS)
    codeowners = _first_present(all_paths, _CODEOWNERS_PATHS)

    # Workflows directory
    workflows: Tuple[str, ...] = ()
    if ".github/workflows" in all_paths or any(
        p.startswith(".github/workflows/") for p in all_paths
    ):
        try:
            wf = runner(
                [
                    "gh",
                    "api",
                    f"repos/{owner}/{repo}/contents/.github/workflows",
                ],
                timeout=15,
            )
        except Exception:  # noqa: BLE001
            wf = None
        if wf is not None and wf.returncode == 0:
            try:
                entries = json.loads(wf.stdout or "[]")
                workflows = tuple(
                    f".github/workflows/{entry.get('name')}"
                    for entry in entries
                    if entry.get("name", "").endswith((".yml", ".yaml"))
                )
            except json.JSONDecodeError:
                workflows = ()

    changelog = _first_present(all_paths, _CHANGELOG_PATHS)
    version_files = tuple(
        path for path in _VERSION_FILES if path in all_paths
    )
    release_workflows = tuple(
        wf for wf in workflows
        if any(token in Path(wf).name.lower() for token in _RELEASE_WORKFLOW_TOKENS)
    )
    tag_policy = derive_tag_policy(
        changelog=changelog,
        version_files=version_files,
        release_workflows=release_workflows,
    )

    ssot_paths = tuple(
        path
        for path in [
            *(issue_templates or ()),
            *(pr_templates or ()),
            contributing,
            readme,
            codeowners,
            *(workflows or ()),
            changelog,
            *(version_files or ()),
        ]
        if path
    )

    return RepoContract(
        owner=owner,
        repo=repo,
        primary_branch=primary_branch,
        issue_templates=tuple(issue_templates),
        pr_templates=tuple(pr_templates),
        contributing=contributing,
        readme=readme,
        codeowners=codeowners,
        workflows=workflows,
        ssot_paths=ssot_paths,
        backend="gh_cli",
        changelog=changelog,
        version_files=version_files,
        release_workflows=release_workflows,
        tag_policy=tag_policy,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def derive_tag_policy(
    *,
    changelog: Optional[str],
    version_files: Sequence[str],
    release_workflows: Sequence[str],
) -> Optional[str]:
    """입력 신호 3 종에서 tag/version policy 분류.

    분류 우선순위 — 더 강한 신호가 우선:
      1. ``release_workflows`` 가 비어있지 않음 → ``"workflow_driven"``
         (GitHub Actions 가 release/publish/tag 작업을 가짐 — agent 는
         그 워크플로우 트리거 조건에 맞춰 plan)
      2. ``changelog`` 가 있음 → ``"changelog_driven"`` (release note 가
         CHANGELOG 에 누적 — agent 는 CHANGELOG entry 추가 + tag 명시
         계획)
      3. ``version_files`` 만 있음 → ``"version_file_only"`` (package.json
        / pyproject.toml 의 version field 만 있음. release automation
        없으므로 자동 tag 는 보류, version bump 만 계획)
      4. 아무 신호도 없음 → ``"none"`` (자동 tag/version 미적용).

    None 반환은 호출자가 신호 자체를 모를 때 (예: tests 가 명시적으로
    빈 input 을 전달) 의 명시적 noop 신호. 본 함수는 None 이 아닌 4 종
    문자열만 반환.
    """

    if release_workflows:
        return "workflow_driven"
    if changelog:
        return "changelog_driven"
    if version_files:
        return "version_file_only"
    return "none"


def _default_subprocess_run(cmd, *, timeout=None):
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=os.environ.copy(),
    )


def _find_first_existing(
    root: Path, candidates: Sequence[str], *, allow_dir: bool = False
) -> Optional[str]:
    for candidate in candidates:
        target = root / candidate
        if target.is_file():
            return candidate
        if allow_dir and target.is_dir():
            return candidate
    return None


def _first_present(paths: Iterable[str], candidates: Sequence[str]) -> Optional[str]:
    path_set = set(paths)
    for candidate in candidates:
        if candidate in path_set:
            return candidate
    return None


def _select_present(
    paths: Iterable[str], candidates: Sequence[str], *, allow_prefix: bool
) -> Tuple[str, ...]:
    path_set = set(paths)
    out: list[str] = []
    for candidate in candidates:
        if candidate in path_set:
            out.append(candidate)
            continue
        if allow_prefix:
            for path in path_set:
                if path.startswith(candidate + "/"):
                    out.append(path)
    return tuple(out)


def _read_primary_branch_from_workflows(root: Path) -> Optional[str]:
    """Best-effort: read a workflow's ``branches`` field. None when unclear."""

    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return None
    for entry in sorted(workflows_dir.iterdir()):
        if entry.suffix not in (".yml", ".yaml"):
            continue
        try:
            text = entry.read_text(encoding="utf-8")
        except OSError:
            continue
        if "branches:" not in text:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- main"):
                return "main"
            if stripped.startswith("- master"):
                return "master"
    return None


def _detect_branch_strategy(
    root: Path, contributing: Optional[str]
) -> Optional[str]:
    """Best-effort extraction from CONTRIBUTING / git-flow markers."""

    # .git-flow.cfg presence
    if (root / ".git-flow.cfg").is_file():
        return "git-flow"
    if contributing is None:
        return None
    try:
        text = (root / contributing).read_text(encoding="utf-8").lower()
    except OSError:
        return None
    if "git flow" in text or "git-flow" in text:
        return "git-flow"
    if "trunk" in text:
        return "trunk-based"
    if "github flow" in text:
        return "github-flow"
    return None


def _detect_commit_convention(
    root: Path, contributing: Optional[str]
) -> Optional[str]:
    """Best-effort: presence of commitlint config or known convention text."""

    for name in ("commitlint.config.js", "commitlint.config.cjs", ".commitlintrc"):
        if (root / name).is_file():
            return "commitlint"
    if (root / ".gitmessage").is_file():
        return "gitmessage"
    if contributing is None:
        return None
    try:
        text = (root / contributing).read_text(encoding="utf-8").lower()
    except OSError:
        return None
    if "conventional commits" in text:
        return "conventional-commits"
    return None


def _coerce_optional_str(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_str_seq(value) -> list:
    if not value:
        return []
    return [str(v) for v in value if v is not None and str(v).strip()]


__all__ = (
    "RepoContract",
    "discover_repo_contract",
)
