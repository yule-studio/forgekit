"""Vault auto-push hook (F8 / #99).

작업 완료 (status=done) 시점에 ``notes/vault-mirror/`` 의 변경분을
vault repo 의 auto 브랜치로 commit + push 하는 진입점이다. 본 모듈은
다음 hard rails 를 강제한다:

  1. 환경변수 ``YULE_VAULT_AUTOPUSH_ENABLED`` 가 ``true`` 가 아닌 경우
     아무 일도 하지 않는다 (default OFF).
  2. 푸시 대상 브랜치가 보호 브랜치 (``main`` / ``master``) 면 직접 push
     를 차단한다. auto 브랜치 ``YULE_VAULT_BRANCH`` (default
     ``auto/notes-sync``) 로 우회시킨다.
  3. PasteGuard (``guard_outbound(VAULT, …)``) 로 commit message 의
     secret 을 검열. PasteGuard 가 blocked 판정이면 push 를 중단한다.
  4. ``dry_run=True`` (default) 면 git 명령을 실행하지 않고 검증만 한다.

본 모듈은 ``record_completion`` 의 사이드카로 호출되도록 설계되었으며,
실제 git push 까지의 1-shot 흐름은 ``push_vault_if_ready`` 한 함수에
집약되어 있다.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from yule_security.paste_guard import OutboundChannel, guard_outbound

from ..governance.git_path_safety import (
    UnsafeGitPathError,
    assert_safe_git_repo_path,
    run_safe_git,
)


ENV_AUTOPUSH_ENABLED = "YULE_VAULT_AUTOPUSH_ENABLED"
ENV_VAULT_REPO_ROOT = "YULE_VAULT_REPO_ROOT"
ENV_VAULT_BRANCH = "YULE_VAULT_BRANCH"
ENV_VAULT_MIRROR_SUBPATH = "YULE_VAULT_MIRROR_SUBPATH"

DEFAULT_AUTO_BRANCH = "auto/notes-sync"
# Scoped staging target (relative to the vault repo) — never a broad `git add .`.
DEFAULT_MIRROR_SUBPATH = "notes/vault-mirror"
PROTECTED_BRANCHES: Sequence[str] = ("main", "master")


@dataclass(frozen=True)
class AutoPushVerdict:
    """auto-push 1회 호출 결과.

    * ``performed`` — 실제 push 가 수행됐는지 (dry_run / disabled 면 False).
    * ``branch`` — 사용된 auto 브랜치.
    * ``commit_hash`` — push 직전 만든 commit hash. dry-run / no-op 이면 빈 문자열.
    * ``skipped_reason`` — flag/status 가 OFF 이거나 변경분이 없어 건너뛴 경우의 사유.
    * ``blocked_reason`` — hard rail 차단 사유 (보호 브랜치 / PasteGuard / 환경 누락 등).
    """

    performed: bool
    branch: str
    commit_hash: str = ""
    skipped_reason: Optional[str] = None
    blocked_reason: Optional[str] = None


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _read_env(env: Optional[Mapping[str, str]], key: str) -> Optional[str]:
    if env is not None:
        return env.get(key)
    return os.environ.get(key)


def _resolve_branch(env: Optional[Mapping[str, str]]) -> str:
    raw = _read_env(env, ENV_VAULT_BRANCH)
    if raw is None or not raw.strip():
        return DEFAULT_AUTO_BRANCH
    return raw.strip()


def _completion_status(event: Any) -> str:
    status = getattr(event, "status", None)
    if isinstance(status, str):
        return status.strip().lower()
    return ""


def _mirror_subpath(env: Optional[Mapping[str, str]]) -> str:
    raw = _read_env(env, ENV_VAULT_MIRROR_SUBPATH)
    if raw is None or not raw.strip():
        return DEFAULT_MIRROR_SUBPATH
    return raw.strip()


def _has_staged_changes(repo_root: Path) -> bool:
    # git -C <repo> — validated path, never an ambiguous cwd.
    result = run_safe_git(repo_root, ["status", "--porcelain"], check=False)
    return bool((result.stdout or "").strip())


def _run_git(args: Sequence[str], *, repo_root: Path) -> subprocess.CompletedProcess:
    # All writes go through the safety guardrail: `git -C <validated repo> ...`,
    # HOME/ambiguous-path refused, broad `git add .` refused.
    return run_safe_git(repo_root, args, check=True)


def push_vault_if_ready(
    *,
    completion_event: Any,
    vault_repo_root: Optional[Path] = None,
    dry_run: bool = True,
    env: Optional[Mapping[str, str]] = None,
) -> AutoPushVerdict:
    """완료 이벤트 → vault auto-push 1-shot 디스패치.

    호출자는 본 함수의 반환값으로 audit / mistake ledger 를 채운다.
    실제 push 가 일어나는 유일한 경로는 ``dry_run=False`` + 모든 hard rail
    통과 + 환경 ON + 변경분 존재.
    """

    branch = _resolve_branch(env)

    if not _truthy(_read_env(env, ENV_AUTOPUSH_ENABLED)):
        return AutoPushVerdict(
            performed=False,
            branch=branch,
            skipped_reason=f"{ENV_AUTOPUSH_ENABLED} not set to true",
        )

    status = _completion_status(completion_event)
    if status != "done":
        return AutoPushVerdict(
            performed=False,
            branch=branch,
            skipped_reason=f"completion status={status!r}; only 'done' triggers push",
        )

    if branch in PROTECTED_BRANCHES:
        return AutoPushVerdict(
            performed=False,
            branch=branch,
            blocked_reason=f"branch {branch!r} is protected — auto-push refused",
        )

    repo_root = vault_repo_root
    if repo_root is None:
        raw = _read_env(env, ENV_VAULT_REPO_ROOT) or ""
        if not raw.strip():
            return AutoPushVerdict(
                performed=False,
                branch=branch,
                blocked_reason=f"{ENV_VAULT_REPO_ROOT} is not set",
            )
        repo_root = Path(raw).expanduser()

    # Light sanity (dry-run preview tolerates any absolute existing dir).
    if not repo_root.is_absolute() or not repo_root.exists():
        return AutoPushVerdict(
            performed=False,
            branch=branch,
            blocked_reason=f"vault repo root invalid: {repo_root}",
        )

    summary = getattr(completion_event, "reason", "") or "vault auto-sync"
    job_id = getattr(completion_event, "job_id", "")
    commit_message = f"chore(vault): auto-sync {job_id} — {summary}"

    guard = guard_outbound(channel=OutboundChannel.VAULT, payload=commit_message)
    if guard.blocked:
        return AutoPushVerdict(
            performed=False,
            branch=branch,
            blocked_reason="PasteGuard blocked vault commit message",
        )
    commit_message = guard.redacted or commit_message

    if dry_run:
        return AutoPushVerdict(
            performed=False,
            branch=branch,
            skipped_reason="dry_run=True — no git operations performed",
        )

    # Hard rail: refuse HOME / ambiguous / ancestor-of-HOME / non-repo paths
    # before ANY git write. This is the write-path guard (dry-run already
    # returned above), so the home-git-accident is unrepresentable here.
    try:
        repo_root = assert_safe_git_repo_path(repo_root)
    except UnsafeGitPathError as exc:
        return AutoPushVerdict(
            performed=False,
            branch=branch,
            blocked_reason=f"unsafe vault repo root: {exc}",
        )

    if not _has_staged_changes(repo_root):
        return AutoPushVerdict(
            performed=False,
            branch=branch,
            skipped_reason="vault working tree clean — nothing to push",
        )

    mirror_subpath = _mirror_subpath(env)
    try:
        _run_git(["checkout", "-B", branch], repo_root=repo_root)
        # Scoped staging — only the vault mirror subpath, never a broad `git add .`.
        _run_git(["add", "--", mirror_subpath], repo_root=repo_root)
        _run_git(["commit", "-m", commit_message, "--", mirror_subpath], repo_root=repo_root)
        hash_proc = _run_git(["rev-parse", "HEAD"], repo_root=repo_root)
        commit_hash = hash_proc.stdout.strip()
        _run_git(["push", "--set-upstream", "origin", branch], repo_root=repo_root)
    except subprocess.CalledProcessError as exc:
        return AutoPushVerdict(
            performed=False,
            branch=branch,
            blocked_reason=f"git command failed: {exc.cmd}",
        )

    return AutoPushVerdict(
        performed=True,
        branch=branch,
        commit_hash=commit_hash,
    )


__all__ = (
    "AutoPushVerdict",
    "DEFAULT_AUTO_BRANCH",
    "DEFAULT_MIRROR_SUBPATH",
    "ENV_AUTOPUSH_ENABLED",
    "ENV_VAULT_BRANCH",
    "ENV_VAULT_MIRROR_SUBPATH",
    "ENV_VAULT_REPO_ROOT",
    "PROTECTED_BRANCHES",
    "push_vault_if_ready",
)
