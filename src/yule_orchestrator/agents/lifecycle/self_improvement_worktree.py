"""Worktree provisioning for self-improvement fixes.

self-improvement loop 가 ``runtime_code_change`` action 으로 가는 순간,
main 위에서 바로 고치지 않고 **분기 worktree 를 생성** 해 그 위에서
작업한다. 이렇게 하면:

* 같은 코드를 두 가지 가설로 동시에 고치는 시도가 격리된다.
* 사용자가 main 에서 평소처럼 작업하는 동안 self-improvement 코드가
  방해하지 않는다.
* worktree 별로 metadata (problem_signature / owner_role 등) 가
  남아 audit 가 가능하다.

본 모듈은 **계획 + metadata 책임만** 담당한다. 실제 git worktree 생성
명령은 :class:`WorktreeProvisioner` Protocol 을 통해 주입 — 테스트는
in-memory provisioner 를 쓰고, 프로덕션은 실제 ``git worktree add``
호출 provisioner 를 쓴다.

핵심 정책:

* branch / worktree 이름은 RepoContract 가 있으면 그것을 우선, 없으면
  ``codex/self-improve/<short-signature>`` 패턴.
* 같은 ``problem_signature`` 에 대해 이미 worktree 가 존재하면
  **재사용** — 같은 문제에 worktree 를 여러 개 만들지 않는다.
* stale cleanup 은 자동 destructive delete 가 아니라 *suggestion-only* —
  운영-리서치 카드로 보고하고 사람 승인이 있을 때만 정리한다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Tuple


logger = logging.getLogger(__name__)


DEFAULT_WORKTREE_ROOT: str = ".cache/yule/self-improve-worktrees"
DEFAULT_BRANCH_PREFIX: str = "codex/self-improve"


_BRANCH_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _short_signature(signature: str, *, max_len: int = 40) -> str:
    """Trim a problem signature into something git-safe."""

    text = (signature or "unknown").strip()
    cleaned = _BRANCH_SAFE_RE.sub("-", text)
    cleaned = cleaned.strip("-._")
    if not cleaned:
        cleaned = "unknown"
    return cleaned[:max_len]


@dataclass(frozen=True)
class WorktreeMetadata:
    """Self-improvement worktree 의 metadata anchor.

    Persisted alongside the worktree (sidecar JSON) and stamped onto
    :class:`ProblemObject.related_session_ids` so audit can correlate.
    """

    branch: str
    path: str
    problem_signature: str
    owner_role: str
    spawned_by: str
    parent_session_id: Optional[str]
    delegated_approval_state: str
    created_at: str
    cwd: str
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "branch": self.branch,
            "path": self.path,
            "problem_signature": self.problem_signature,
            "owner_role": self.owner_role,
            "spawned_by": self.spawned_by,
            "parent_session_id": self.parent_session_id,
            "delegated_approval_state": self.delegated_approval_state,
            "created_at": self.created_at,
            "cwd": self.cwd,
            "extra": dict(self.extra or {}),
        }


@dataclass(frozen=True)
class WorktreeProvisionOutcome:
    """Result of :func:`provision_worktree_for_problem`.

    ``reused`` is True when the registry already had a worktree for
    the same problem signature — no new git invocation happened.
    """

    metadata: WorktreeMetadata
    reused: bool


class WorktreeProvisioner(Protocol):
    """Seam for the actual git worktree creation.

    Production wires this to a shell-based provisioner that runs
    ``git worktree add -b <branch> <path>`` against the engineering
    repo. Tests inject an in-memory recorder.
    """

    def create(
        self,
        *,
        branch: str,
        path: str,
        base_branch: str,
        cwd: str,
    ) -> None:  # pragma: no cover - protocol
        ...

    def exists(self, *, branch: str, path: str) -> bool:  # pragma: no cover
        ...

    def remove(
        self, *, branch: str, path: str, force: bool = False
    ) -> None:  # pragma: no cover
        ...


@dataclass
class InMemoryWorktreeRegistry:
    """Tracks ``problem_signature → WorktreeMetadata`` mappings.

    Per-process registry — the supervisor is a single process so a
    dict is enough. Optional sidecar JSON file lets the registry
    survive restart; cleared on operator request.
    """

    by_signature: dict[str, WorktreeMetadata] = field(default_factory=dict)
    sidecar_path: Optional[Path] = None

    def __post_init__(self) -> None:
        if self.sidecar_path is not None and self.sidecar_path.exists():
            self._load()

    def get(self, signature: str) -> Optional[WorktreeMetadata]:
        return self.by_signature.get(signature)

    def register(self, metadata: WorktreeMetadata) -> None:
        self.by_signature[metadata.problem_signature] = metadata
        self._save()

    def drop(self, signature: str) -> Optional[WorktreeMetadata]:
        result = self.by_signature.pop(signature, None)
        self._save()
        return result

    def all(self) -> Tuple[WorktreeMetadata, ...]:
        return tuple(self.by_signature.values())

    # -- persistence ----------------------------------------------------

    def _load(self) -> None:
        import json

        if self.sidecar_path is None:
            return
        try:
            data = json.loads(self.sidecar_path.read_text(encoding="utf-8") or "{}")
        except Exception:  # noqa: BLE001
            logger.warning(
                "InMemoryWorktreeRegistry: failed to load sidecar %s",
                self.sidecar_path,
                exc_info=True,
            )
            return
        entries = data.get("worktrees") if isinstance(data, Mapping) else None
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            try:
                metadata = WorktreeMetadata(
                    branch=str(entry.get("branch") or ""),
                    path=str(entry.get("path") or ""),
                    problem_signature=str(entry.get("problem_signature") or ""),
                    owner_role=str(entry.get("owner_role") or ""),
                    spawned_by=str(entry.get("spawned_by") or ""),
                    parent_session_id=entry.get("parent_session_id"),
                    delegated_approval_state=str(
                        entry.get("delegated_approval_state") or ""
                    ),
                    created_at=str(entry.get("created_at") or ""),
                    cwd=str(entry.get("cwd") or ""),
                    extra=dict(entry.get("extra") or {}),
                )
            except Exception:  # noqa: BLE001
                continue
            if metadata.problem_signature:
                self.by_signature[metadata.problem_signature] = metadata

    def _save(self) -> None:
        import json

        if self.sidecar_path is None:
            return
        try:
            self.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "worktrees": [m.to_payload() for m in self.by_signature.values()],
                "saved_at": _utc_now_iso(),
            }
            self.sidecar_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "InMemoryWorktreeRegistry: failed to save sidecar",
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Provision API
# ---------------------------------------------------------------------------


def build_branch_name(
    *,
    problem_signature: str,
    prefix: str = DEFAULT_BRANCH_PREFIX,
) -> str:
    short = _short_signature(problem_signature)
    return f"{prefix}/{short}"


def build_worktree_path(
    *,
    branch: str,
    root: str = DEFAULT_WORKTREE_ROOT,
) -> str:
    safe = _BRANCH_SAFE_RE.sub("-", branch).strip("-._") or "worktree"
    return str(Path(root) / safe)


def provision_worktree_for_problem(
    *,
    problem_signature: str,
    owner_role: str,
    spawned_by: str,
    base_branch: str = "main",
    parent_session_id: Optional[str] = None,
    delegated_approval_state: str = "delegated_ok",
    provisioner: WorktreeProvisioner,
    registry: InMemoryWorktreeRegistry,
    cwd: str = ".",
    worktree_root: str = DEFAULT_WORKTREE_ROOT,
    branch_prefix: str = DEFAULT_BRANCH_PREFIX,
    extra: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
) -> WorktreeProvisionOutcome:
    """Provision (or reuse) a worktree for *problem_signature*.

    Returns the metadata + a ``reused`` flag. If a worktree already
    exists for this signature the provisioner is NOT called — same
    problem → same branch.

    The provisioner is responsible for the actual ``git worktree add``
    call; this function only computes the branch/path and records the
    metadata.
    """

    existing = registry.get(problem_signature)
    if existing is not None and provisioner.exists(
        branch=existing.branch, path=existing.path
    ):
        return WorktreeProvisionOutcome(metadata=existing, reused=True)

    branch = build_branch_name(
        problem_signature=problem_signature, prefix=branch_prefix
    )
    path = build_worktree_path(branch=branch, root=worktree_root)

    # If git already knows about this branch but the registry is stale,
    # short-circuit: reuse + register so we don't fight git.
    if provisioner.exists(branch=branch, path=path):
        when = _format_iso(now or _utc_now())
        metadata = WorktreeMetadata(
            branch=branch,
            path=path,
            problem_signature=problem_signature,
            owner_role=owner_role,
            spawned_by=spawned_by,
            parent_session_id=parent_session_id,
            delegated_approval_state=delegated_approval_state,
            created_at=when,
            cwd=cwd,
            extra=dict(extra or {}),
        )
        registry.register(metadata)
        return WorktreeProvisionOutcome(metadata=metadata, reused=True)

    provisioner.create(branch=branch, path=path, base_branch=base_branch, cwd=cwd)
    when = _format_iso(now or _utc_now())
    metadata = WorktreeMetadata(
        branch=branch,
        path=path,
        problem_signature=problem_signature,
        owner_role=owner_role,
        spawned_by=spawned_by,
        parent_session_id=parent_session_id,
        delegated_approval_state=delegated_approval_state,
        created_at=when,
        cwd=cwd,
        extra=dict(extra or {}),
    )
    registry.register(metadata)
    return WorktreeProvisionOutcome(metadata=metadata, reused=False)


# ---------------------------------------------------------------------------
# Stale cleanup — suggestion only
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StaleWorktreeReport:
    """One stale worktree the cleanup sweep flagged.

    Cleanup is **never automatic** — the caller (runtime loop) gets a
    report and decides whether to escalate as operator action.
    """

    metadata: WorktreeMetadata
    stale_seconds: float
    reason: str


def detect_stale_worktrees(
    *,
    registry: InMemoryWorktreeRegistry,
    closed_signatures: Iterable[str] = (),
    now: Optional[datetime] = None,
    stale_after_seconds: int = 7 * 24 * 3600,
) -> Tuple[StaleWorktreeReport, ...]:
    """Return worktrees that look abandoned.

    Heuristic:
      * older than ``stale_after_seconds`` AND
      * either the problem is closed (signature in ``closed_signatures``)
        OR ``created_at`` failed to parse.
    """

    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
    closed = set(closed_signatures or ())
    reports: list[StaleWorktreeReport] = []
    for metadata in registry.all():
        created = _parse_iso(metadata.created_at)
        if created is None:
            reports.append(
                StaleWorktreeReport(
                    metadata=metadata,
                    stale_seconds=float(stale_after_seconds),
                    reason="created_at_unparseable",
                )
            )
            continue
        age = (when - created).total_seconds()
        if age < stale_after_seconds:
            continue
        reason = "older_than_threshold"
        if metadata.problem_signature in closed:
            reason = "problem_closed"
        reports.append(
            StaleWorktreeReport(
                metadata=metadata,
                stale_seconds=age,
                reason=reason,
            )
        )
    return tuple(reports)


# ---------------------------------------------------------------------------
# Production provisioner — shells out to ``git worktree`` (best-effort)
# ---------------------------------------------------------------------------


@dataclass
class GitWorktreeProvisioner:
    """Production provisioner — shells out to ``git worktree add`` /
    ``git branch -a`` / ``git worktree list``.

    Failures are logged + re-raised so the caller can record an
    audit row. The supervisor's outer loop swallows the exception
    (never crashes on a worktree creation failure).
    """

    git_binary: str = "git"
    runner: Callable[..., Any] = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.runner is None:
            import subprocess

            def _run(args: list, cwd: str) -> Any:
                return subprocess.run(  # noqa: S603,S607
                    args, cwd=cwd, capture_output=True, text=True, check=False
                )

            self.runner = _run

    def create(
        self, *, branch: str, path: str, base_branch: str, cwd: str
    ) -> None:
        # ``git worktree add -B <branch> <path> <base>`` — -B re-creates
        # if exists so an orphaned branch row doesn't poison the second
        # attempt. We DO NOT pass --force so concurrent self-improve
        # runs can't clobber each other's checkout.
        result = self.runner(
            [
                self.git_binary,
                "worktree",
                "add",
                "-B",
                branch,
                path,
                base_branch,
            ],
            cwd=cwd,
        )
        rc = getattr(result, "returncode", 0)
        if rc != 0:
            stderr = getattr(result, "stderr", "") or ""
            raise RuntimeError(
                f"git worktree add failed (rc={rc}): {stderr.strip()}"
            )

    def exists(self, *, branch: str, path: str) -> bool:
        result = self.runner(
            [self.git_binary, "worktree", "list", "--porcelain"], cwd="."
        )
        if getattr(result, "returncode", 0) != 0:
            return False
        stdout = getattr(result, "stdout", "") or ""
        return path in stdout

    def remove(self, *, branch: str, path: str, force: bool = False) -> None:
        args = [self.git_binary, "worktree", "remove"]
        if force:
            args.append("--force")
        args.append(path)
        result = self.runner(args, cwd=".")
        rc = getattr(result, "returncode", 0)
        if rc != 0:
            stderr = getattr(result, "stderr", "") or ""
            raise RuntimeError(
                f"git worktree remove failed (rc={rc}): {stderr.strip()}"
            )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


from typing import Iterable  # noqa: E402  late import to keep type checker quiet


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


def _format_iso(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat()


def _utc_now_iso() -> str:
    return _format_iso(_utc_now())


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


__all__ = (
    "DEFAULT_BRANCH_PREFIX",
    "DEFAULT_WORKTREE_ROOT",
    "GitWorktreeProvisioner",
    "InMemoryWorktreeRegistry",
    "StaleWorktreeReport",
    "WorktreeMetadata",
    "WorktreeProvisionOutcome",
    "WorktreeProvisioner",
    "build_branch_name",
    "build_worktree_path",
    "detect_stale_worktrees",
    "provision_worktree_for_problem",
)
