"""P1-Z6 D — dev-only file watch + selective service restart.

배경
----
사용자 요구: ``yule runtime up`` 후 코드 수정할 때마다 전체 supervisor
재기동은 디버깅 루프를 길게 만든다.  hot-reload / selective restart 가
없어서 매번 down/up 반복.

본 모듈
========
* opt-in 만 — production / systemd path 는 절대 건드리지 않음.
* polling 기반 (외부 dependency 없음).  ``watchfiles`` / ``watchdog``
  같은 패키지 도입 X.
* 파일 → 영향 서비스 매핑 (``FILE_SERVICE_AFFECTED_MAP``).  매핑이
  없으면 conservative fallback (관련 engineering services 집합 restart).
* dev runtime 의 supervisor 가 본 helper 를 import 해 사용.

API
====
* :func:`compute_affected_services(changed_files)` — pure (no I/O) 파일 →
  영향 서비스 집합 결정.
* :class:`FileWatcher` — polling-based mtime watcher.  외부 dep 없음.
* :func:`run_dev_watch_loop(...)` — supervisor 가 호출하는 메인 루프.
  파일 변경 감지 → 영향 서비스 restart fn 호출.

production safety
=================
* :class:`FileWatcher` 는 dev 전용 — env (``YULE_RUNTIME_DEV_WATCH=1``)
  또는 explicit CLI ``--watch`` 가 켜져야만 활성.
* systemd unit / production launcher 는 본 모듈 import 안 함.
* watcher 실패는 supervisor 본체 안 깸 (best-effort).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, FrozenSet, Iterable, Mapping, Optional, Set, Tuple


logger = logging.getLogger(__name__)


SERVICE_CODING_EXECUTOR: str = "eng-coding-executor"
SERVICE_GITHUB_WORK_ORDER_EXECUTOR: str = "eng-github-work-order-executor"
SERVICE_DISCORD_GATEWAY: str = "eng-discord-gateway"
SERVICE_APPROVAL_WORKER: str = "eng-approval-worker"
SERVICE_OBSIDIAN_WRITER: str = "eng-obsidian-writer"


# Conservative engineering set — shared module 변경 시 restart 대상.
ENGINEERING_SERVICES_SET: FrozenSet[str] = frozenset(
    {
        SERVICE_CODING_EXECUTOR,
        SERVICE_GITHUB_WORK_ORDER_EXECUTOR,
        SERVICE_DISCORD_GATEWAY,
        SERVICE_APPROVAL_WORKER,
        SERVICE_OBSIDIAN_WRITER,
    }
)


# P1-Z6 D — 파일 path prefix → 영향 받는 service 집합 매핑.
# 가장 좁은 prefix 매칭 우선 — caller (FileWatcher) 가 정렬 후 first-match.
FILE_SERVICE_AFFECTED_MAP: Mapping[str, FrozenSet[str]] = {
    # coding executor pipeline
    "src/yule_orchestrator/runtime/coding_executor_runner.py": frozenset(
        {SERVICE_CODING_EXECUTOR}
    ),
    "src/yule_orchestrator/agents/job_queue/coding_executor_worker.py": frozenset(
        {SERVICE_CODING_EXECUTOR}
    ),
    "src/yule_orchestrator/agents/job_queue/coding_executor_live.py": frozenset(
        {SERVICE_CODING_EXECUTOR}
    ),
    "src/yule_orchestrator/agents/job_queue/coding_execute_dispatcher.py": frozenset(
        {SERVICE_CODING_EXECUTOR}
    ),
    "src/yule_orchestrator/agents/job_queue/coding_execute_recovery.py": frozenset(
        {SERVICE_CODING_EXECUTOR}
    ),
    "src/yule_orchestrator/agents/job_queue/coding_execute_terminal_skip.py": frozenset(
        {SERVICE_CODING_EXECUTOR}
    ),
    "src/yule_orchestrator/agents/job_queue/coding_write_scope_resolution.py": frozenset(
        {SERVICE_CODING_EXECUTOR}
    ),
    # github work order executor
    "src/yule_orchestrator/agents/job_queue/github_work_order_executor.py": frozenset(
        {SERVICE_GITHUB_WORK_ORDER_EXECUTOR}
    ),
    "src/yule_orchestrator/agents/job_queue/github_work_order.py": frozenset(
        {SERVICE_GITHUB_WORK_ORDER_EXECUTOR}
    ),
    "src/yule_orchestrator/agents/job_queue/github_work_order_recovery.py": frozenset(
        {SERVICE_GITHUB_WORK_ORDER_EXECUTOR}
    ),
    "src/yule_orchestrator/agents/job_queue/work_order_coding_continuation.py": frozenset(
        {SERVICE_GITHUB_WORK_ORDER_EXECUTOR, SERVICE_CODING_EXECUTOR}
    ),
    "src/yule_orchestrator/agents/job_queue/post_approval_dispatch.py": frozenset(
        {SERVICE_GITHUB_WORK_ORDER_EXECUTOR}
    ),
    # Discord gateway
    "src/yule_orchestrator/discord/commands/__init__.py": frozenset(
        {SERVICE_DISCORD_GATEWAY}
    ),
    "src/yule_orchestrator/discord/integrations/github_workos_adapter.py": frozenset(
        {SERVICE_DISCORD_GATEWAY}
    ),
    "src/yule_orchestrator/discord/integrations/intake_approval_eligibility.py": frozenset(
        {SERVICE_DISCORD_GATEWAY}
    ),
    "src/yule_orchestrator/discord/engineering_channel_router/": frozenset(
        {SERVICE_DISCORD_GATEWAY}
    ),
    "src/yule_orchestrator/discord/bot/": frozenset({SERVICE_DISCORD_GATEWAY}),
    "src/yule_orchestrator/discord/forum/": frozenset({SERVICE_DISCORD_GATEWAY}),
    # Approval worker
    "src/yule_orchestrator/agents/job_queue/approval_reply.py": frozenset(
        {SERVICE_APPROVAL_WORKER}
    ),
    "src/yule_orchestrator/agents/job_queue/approval_discord_poster.py": frozenset(
        {SERVICE_APPROVAL_WORKER}
    ),
    # Obsidian writer
    "src/yule_orchestrator/agents/job_queue/obsidian_writer_worker.py": frozenset(
        {SERVICE_OBSIDIAN_WRITER}
    ),
}


# Strategy / coding planning shared — affects coding_executor + work_order
SHARED_CODING_PATHS: Tuple[str, ...] = (
    "src/yule_orchestrator/agents/coding/",
)


@dataclass(frozen=True)
class AffectedDecision:
    """파일 변경 → 영향 서비스 결정 결과."""

    services: FrozenSet[str]
    reason: str
    matched_prefix: Optional[str] = None
    conservative_fallback: bool = False


def compute_affected_services(
    changed_files: Iterable[str],
) -> AffectedDecision:
    """변경된 파일 경로 집합 → restart 대상 service 집합 결정.

    Pure — no I/O.  same input → same output.  caller (FileWatcher) 가
    호출하고, 결과의 ``services`` 만 restart fn 으로 전달.
    """

    affected: Set[str] = set()
    matched_prefixes: list[str] = []
    fallback_triggered = False

    # 정렬: 긴 prefix 가 먼저 매칭 (좁은 매칭 우선)
    sorted_mapping = sorted(
        FILE_SERVICE_AFFECTED_MAP.items(),
        key=lambda kv: -len(kv[0]),
    )

    files = sorted(set(str(f).replace("\\", "/") for f in changed_files if f))
    for rel_path in files:
        path_matched = False
        for prefix, services in sorted_mapping:
            if rel_path.startswith(prefix):
                affected.update(services)
                matched_prefixes.append(prefix)
                path_matched = True
                break
        if path_matched:
            continue
        # shared coding planning paths
        for shared in SHARED_CODING_PATHS:
            if rel_path.startswith(shared):
                affected.update(
                    {SERVICE_CODING_EXECUTOR, SERVICE_GITHUB_WORK_ORDER_EXECUTOR}
                )
                matched_prefixes.append(shared)
                path_matched = True
                break
        if path_matched:
            continue
        # conservative fallback — 매핑 없는 src/yule_orchestrator 변경은
        # 전체 engineering set 재시작.  매핑 외 변경 (tests/ / docs/) 은
        # 무시.
        if rel_path.startswith("src/yule_orchestrator/"):
            affected.update(ENGINEERING_SERVICES_SET)
            fallback_triggered = True
            matched_prefixes.append("<conservative-fallback>")

    return AffectedDecision(
        services=frozenset(affected),
        reason=(
            "conservative_fallback"
            if fallback_triggered
            else (
                "explicit_mapping"
                if matched_prefixes
                else "no_match"
            )
        ),
        matched_prefix=", ".join(sorted(set(matched_prefixes))) if matched_prefixes else None,
        conservative_fallback=fallback_triggered,
    )


class FileWatcher:
    """Polling-based mtime watcher.  외부 dep 없음.

    *roots* 는 watch 할 디렉터리 집합 (예: ``src/yule_orchestrator``).
    *extensions* 는 추적할 확장자 (기본 ``.py``).  *interval* 은 폴링
    주기 (초).
    """

    def __init__(
        self,
        *,
        roots: Iterable[str],
        extensions: Tuple[str, ...] = (".py",),
        interval_seconds: float = 1.5,
    ) -> None:
        self._roots: Tuple[Path, ...] = tuple(Path(r) for r in roots)
        self._extensions: Tuple[str, ...] = tuple(extensions)
        self._interval = max(0.5, float(interval_seconds))
        self._mtimes: dict[str, float] = {}

    def snapshot(self) -> Mapping[str, float]:
        """현재 mtime snapshot 수집.  내부 상태 안 갱신."""

        result: dict[str, float] = {}
        for root in self._roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if self._extensions and not str(path).endswith(self._extensions):
                    continue
                try:
                    result[str(path)] = path.stat().st_mtime
                except OSError:
                    continue
        return result

    def detect_changes(self) -> Tuple[str, ...]:
        """이전 snapshot 과 비교해 변경된 파일 경로 (repo-relative).

        첫 호출 시 baseline 만 수집 → 빈 튜플.  이후 호출부터 변경 감지.
        """

        current = self.snapshot()
        if not self._mtimes:
            self._mtimes = dict(current)
            return ()
        changed: list[str] = []
        for path, mtime in current.items():
            previous = self._mtimes.get(path)
            if previous is None or mtime > previous:
                changed.append(path)
        # mtime baseline 갱신
        self._mtimes = dict(current)
        return tuple(_to_repo_relative(p) for p in changed)

    @property
    def interval(self) -> float:
        return self._interval


def _to_repo_relative(absolute_path: str) -> str:
    """``/abs/path/src/yule_orchestrator/...`` → ``src/yule_orchestrator/...``."""

    p = Path(absolute_path)
    parts = p.parts
    try:
        idx = parts.index("src")
        return "/".join(parts[idx:])
    except ValueError:
        return absolute_path


def dev_watch_enabled() -> bool:
    """env / CLI 명시 opt-in 만 활성화."""

    value = os.environ.get("YULE_RUNTIME_DEV_WATCH", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def run_dev_watch_iteration(
    *,
    watcher: FileWatcher,
    restart_service_fn: Callable[[str], None],
    log_fn: Optional[Callable[[str], None]] = None,
) -> AffectedDecision:
    """한 사이클 — 변경 감지 + 영향 서비스 restart 호출.

    storage / network I/O 직접 안 함.  caller 가 ``restart_service_fn``
    inject (실제 supervisor 의 restart 핸들러).  변경 0건이면
    no-restart, AffectedDecision.services 비어있음.

    반환: AffectedDecision — 호출자 audit 용.
    """

    changed = watcher.detect_changes()
    decision = compute_affected_services(changed)
    if not changed:
        return decision
    if log_fn:
        try:
            log_fn(
                f"dev watch: {len(changed)} file(s) changed → "
                f"restart {sorted(decision.services) or '(no-op)'} "
                f"(reason={decision.reason}, matched={decision.matched_prefix})"
            )
        except Exception:  # noqa: BLE001
            pass
    for service in sorted(decision.services):
        try:
            restart_service_fn(service)
        except Exception:  # noqa: BLE001
            logger.warning(
                "dev_watch: restart_service_fn raised for %s",
                service,
                exc_info=True,
            )
    return decision


def run_dev_watch_loop(
    *,
    watcher: FileWatcher,
    restart_service_fn: Callable[[str], None],
    stop_predicate: Callable[[], bool] = lambda: False,
    log_fn: Optional[Callable[[str], None]] = None,
) -> None:
    """``stop_predicate`` 가 True 반환할 때까지 watch 루프 실행.

    blocking — supervisor 가 별도 thread / asyncio task 에서 호출.
    """

    while not stop_predicate():
        try:
            run_dev_watch_iteration(
                watcher=watcher,
                restart_service_fn=restart_service_fn,
                log_fn=log_fn,
            )
        except Exception:  # noqa: BLE001
            logger.warning("dev_watch loop iteration raised", exc_info=True)
        time.sleep(watcher.interval)


__all__ = (
    "AffectedDecision",
    "ENGINEERING_SERVICES_SET",
    "FILE_SERVICE_AFFECTED_MAP",
    "FileWatcher",
    "SERVICE_APPROVAL_WORKER",
    "SERVICE_CODING_EXECUTOR",
    "SERVICE_DISCORD_GATEWAY",
    "SERVICE_GITHUB_WORK_ORDER_EXECUTOR",
    "SERVICE_OBSIDIAN_WRITER",
    "SHARED_CODING_PATHS",
    "compute_affected_services",
    "dev_watch_enabled",
    "run_dev_watch_iteration",
    "run_dev_watch_loop",
)
