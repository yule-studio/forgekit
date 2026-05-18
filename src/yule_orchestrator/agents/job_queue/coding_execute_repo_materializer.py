"""P1-F — target repo auto-materializer for coding executor.

이전 ``_default_repo_root_resolver`` 는 "이미 있는 local checkout 찾기"
까지만 했다. operator 가 직접 clone 하거나 env path 를 설정해야
canonical session ``11917bf1e75d`` 같은 cross-repo coding request 가
진행됐다. 운영 시 병목.

본 모듈은 그 다음 단계 — **운영 가능한 auto-clone/fetch** 를 governed
방식으로 제공한다:

  resolver (existing local checkout)
    → materializer (clone / fetch into a deterministic cache)
    → worktree provisioner

설계 원칙:

  * **opt-in**: ``YULE_CODING_EXECUTOR_REPO_AUTO_CLONE`` env 가 명시적으로
    on (``1`` / ``true`` / ``yes`` / ``on``) 일 때만 작동. default off.
  * **allowlist gate**: ``YULE_CODING_EXECUTOR_ALLOWED_REPO_OWNERS`` CSV
    에 owner 가 들어있어야 함. 아니면 ``MaterializationResult(action=
    "refused_owner")`` — clone 시도하지 않음.
  * **deterministic cache**: ``YULE_CODING_EXECUTOR_REPO_CACHE_ROOT``
    (default ``~/.cache/yule/repos``) / ``<owner>/<repo>``.
  * **idempotent**: cache 에 이미 있으면 ``git fetch --prune`` 만, 없으면
    ``git clone --filter=blob:none``.
  * **fail loud**: 모든 분기에 operator-readable reason. ``RuntimeError``
    raise 하지 않고 ``MaterializationResult.action="failed"`` +
    ``reason`` 으로 정보 전달 → caller 가 ``WorktreeProvisionError`` /
    ``TargetRepoUnavailableError`` 로 surface.

본 모듈은 subprocess 만 사용 — credential 추측 / GitHub App API 의존성
없음. operator 가 git config 로 credentials.helper / ssh / read-only HTTPS
중 하나를 미리 세팅했어야 한다.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Tuple


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env / token surface
# ---------------------------------------------------------------------------


ENV_AUTO_CLONE: str = "YULE_CODING_EXECUTOR_REPO_AUTO_CLONE"
ENV_CACHE_ROOT: str = "YULE_CODING_EXECUTOR_REPO_CACHE_ROOT"
ENV_ALLOWED_OWNERS: str = "YULE_CODING_EXECUTOR_ALLOWED_REPO_OWNERS"
ENV_CLONE_BASE_URL: str = "YULE_CODING_EXECUTOR_REPO_CLONE_BASE_URL"

DEFAULT_CACHE_ROOT_FALLBACK: str = "~/.cache/yule/repos"
DEFAULT_CLONE_BASE_URL: str = "https://github.com"


ACTION_REUSED: str = "reused"
ACTION_CLONED: str = "cloned"
ACTION_FETCHED: str = "fetched"
ACTION_REFUSED_DISABLED: str = "refused_disabled"
ACTION_REFUSED_OWNER: str = "refused_owner"
ACTION_REFUSED_INVALID_NAME: str = "refused_invalid_repo_name"
ACTION_FAILED: str = "failed"


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaterializationResult:
    """Outcome of :func:`materialize_repo` — caller (provisioner) uses
    ``path`` when ``action in {reused, cloned, fetched}``; otherwise
    surfaces ``reason`` via ``TargetRepoUnavailableError`` /
    ``WorktreeProvisionError(reason="repo_materialization_failed:…")``.
    """

    action: str
    path: Optional[str] = None
    reason: str = ""
    repo_full_name: str = ""

    @property
    def succeeded(self) -> bool:
        return self.action in (ACTION_REUSED, ACTION_CLONED, ACTION_FETCHED)

    def to_audit(self) -> dict:
        return {
            "action": self.action,
            "path": self.path,
            "reason": self.reason,
            "repo_full_name": self.repo_full_name,
        }


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def _truthy_env(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_cache_root(env: Optional[dict] = None) -> Path:
    src = env if env is not None else os.environ
    raw = (src.get(ENV_CACHE_ROOT) or "").strip() or DEFAULT_CACHE_ROOT_FALLBACK
    return Path(os.path.expanduser(raw)).resolve()


def _resolve_allowed_owners(env: Optional[dict] = None) -> Tuple[str, ...]:
    src = env if env is not None else os.environ
    raw = (src.get(ENV_ALLOWED_OWNERS) or "").strip()
    if not raw:
        return ()
    return tuple(
        token.strip()
        for token in raw.replace(",", " ").split()
        if token.strip()
    )


def _resolve_clone_base_url(env: Optional[dict] = None) -> str:
    src = env if env is not None else os.environ
    raw = (src.get(ENV_CLONE_BASE_URL) or "").strip()
    return raw or DEFAULT_CLONE_BASE_URL


def _is_auto_clone_enabled(env: Optional[dict] = None) -> bool:
    src = env if env is not None else os.environ
    return _truthy_env(src.get(ENV_AUTO_CLONE))


# ---------------------------------------------------------------------------
# Subprocess wrapper
# ---------------------------------------------------------------------------


_DEFAULT_TIMEOUT_SECONDS: int = 300


def _default_runner(
    cmd: Sequence[str],
    *,
    cwd: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT_SECONDS,
) -> Tuple[int, str, str]:
    try:
        result = subprocess.run(  # noqa: S603 - explicit list, no shell
            list(cmd),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return 124, "", f"timeout after {timeout}s: {exc}"
    return result.returncode, result.stdout or "", result.stderr or ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def materialize_repo(
    *,
    repo_full_name: str,
    auto_clone_enabled: Optional[bool] = None,
    cache_root: Optional[Path] = None,
    allowed_owners: Optional[Sequence[str]] = None,
    clone_base_url: Optional[str] = None,
    runner: Optional[Callable[..., Tuple[int, str, str]]] = None,
    env: Optional[dict] = None,
) -> MaterializationResult:
    """Materialize *repo_full_name* under the cache root.

    Governance order:
      1. If ``auto_clone_enabled`` is False (or env off) → refuse with
         ``refused_disabled``.
      2. If ``repo_full_name`` malformed → ``refused_invalid_repo_name``.
      3. If ``allowed_owners`` non-empty AND owner ∉ allowlist →
         ``refused_owner``.
      4. cache_root/<owner>/<repo> 가 이미 git working tree → ``git
         fetch --prune`` → ``fetched`` (or ``reused`` if fetch failed
         softly).
      5. 그 외 → ``git clone --filter=blob:none <clone_base_url>/<repo>.git
         <target>``. 실패 시 ``failed`` + 첫 stderr line 으로 reason 명시.
    """

    name = (repo_full_name or "").strip()
    if not name or "/" not in name:
        return MaterializationResult(
            action=ACTION_REFUSED_INVALID_NAME,
            reason=f"repo_full_name {name!r} 가 'owner/name' 형식이 아님",
            repo_full_name=name,
        )
    owner, _, repo_name = name.partition("/")
    owner = owner.strip()
    repo_name = repo_name.strip().rstrip(".git")
    if not owner or not repo_name:
        return MaterializationResult(
            action=ACTION_REFUSED_INVALID_NAME,
            reason=f"repo_full_name {name!r} parse 후 owner / name 비어있음",
            repo_full_name=name,
        )

    if auto_clone_enabled is None:
        auto_clone_enabled = _is_auto_clone_enabled(env)
    if not auto_clone_enabled:
        return MaterializationResult(
            action=ACTION_REFUSED_DISABLED,
            reason=(
                f"auto-clone disabled — set {ENV_AUTO_CLONE}=1 to enable"
            ),
            repo_full_name=name,
        )

    if allowed_owners is None:
        allowed_owners = _resolve_allowed_owners(env)
    if allowed_owners and owner not in allowed_owners:
        return MaterializationResult(
            action=ACTION_REFUSED_OWNER,
            reason=(
                f"owner {owner!r} not in allowlist "
                f"({', '.join(allowed_owners)}) — set "
                f"{ENV_ALLOWED_OWNERS} to include this owner"
            ),
            repo_full_name=name,
        )

    cache_root_resolved = (cache_root or _resolve_cache_root(env)).expanduser()
    target = cache_root_resolved / owner / repo_name
    cache_root_resolved.mkdir(parents=True, exist_ok=True)

    runner_fn = runner or _default_runner

    if (target / ".git").is_dir():
        # Existing checkout — fetch + prune, soft-fail to reuse so a
        # transient network blip doesn't block the pipeline.
        rc, _stdout, stderr = runner_fn(
            ["git", "-C", str(target), "fetch", "--prune", "--quiet"],
        )
        if rc == 0:
            logger.info(
                "repo materializer: fetched %s into %s", name, target
            )
            return MaterializationResult(
                action=ACTION_FETCHED,
                path=str(target),
                reason="git fetch --prune succeeded",
                repo_full_name=name,
            )
        logger.warning(
            "repo materializer: fetch failed for %s (rc=%s, stderr=%s) "
            "— reusing existing checkout",
            name,
            rc,
            (stderr or "").splitlines()[:1],
        )
        return MaterializationResult(
            action=ACTION_REUSED,
            path=str(target),
            reason=(
                f"git fetch failed (rc={rc}): "
                f"{(stderr or '').splitlines()[:1] or 'unknown'} — "
                "reusing existing checkout"
            ),
            repo_full_name=name,
        )

    base_url = (clone_base_url or _resolve_clone_base_url(env)).rstrip("/")
    clone_url = f"{base_url}/{owner}/{repo_name}.git"
    target.parent.mkdir(parents=True, exist_ok=True)
    rc, _stdout, stderr = runner_fn(
        [
            "git",
            "clone",
            "--filter=blob:none",
            clone_url,
            str(target),
        ],
        timeout=_DEFAULT_TIMEOUT_SECONDS,
    )
    if rc != 0:
        head = (stderr or "").splitlines()[:1] or ["unknown error"]
        logger.warning(
            "repo materializer: clone failed for %s (rc=%s, stderr=%s)",
            name,
            rc,
            head,
        )
        return MaterializationResult(
            action=ACTION_FAILED,
            path=None,
            reason=(
                f"git clone {clone_url} failed (rc={rc}): {head[0]}"
            ),
            repo_full_name=name,
        )
    logger.info("repo materializer: cloned %s into %s", name, target)
    return MaterializationResult(
        action=ACTION_CLONED,
        path=str(target),
        reason=f"git clone {clone_url} succeeded",
        repo_full_name=name,
    )


__all__ = (
    "ACTION_CLONED",
    "ACTION_FAILED",
    "ACTION_FETCHED",
    "ACTION_REFUSED_DISABLED",
    "ACTION_REFUSED_INVALID_NAME",
    "ACTION_REFUSED_OWNER",
    "ACTION_REUSED",
    "DEFAULT_CACHE_ROOT_FALLBACK",
    "DEFAULT_CLONE_BASE_URL",
    "ENV_ALLOWED_OWNERS",
    "ENV_AUTO_CLONE",
    "ENV_CACHE_ROOT",
    "ENV_CLONE_BASE_URL",
    "MaterializationResult",
    "materialize_repo",
)
