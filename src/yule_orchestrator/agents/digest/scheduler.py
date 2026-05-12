"""F13 — Digest scheduler. interval 기반 background runner.

env (`.env.local`):
  YULE_DIGEST_SCHEDULER_ENABLED — true 일 때만 작동 (default false)
  YULE_DIGEST_SCHEDULER_INTERVAL_HOURS — default 12 (12h 마다 1회)

사용자 design (2026-05-12):
> "크롤러가 특정 사이트에서 글을 수집합니다."
> "수집 결과를 부서별로 분류해서 각 부서 채널에 1차 게시합니다."
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Optional, Sequence

from .crawler import CrawlOutcome, HttpPoster, crawl_role
from .dedup_ledger import DigestDedupLedger
from .dispatcher import DispatchPlan, build_dispatch_plan
from .source_catalog import ROLE_SOURCE_CATALOG


logger = logging.getLogger(__name__)


ENV_ENABLED = "YULE_DIGEST_SCHEDULER_ENABLED"
ENV_INTERVAL_HOURS = "YULE_DIGEST_SCHEDULER_INTERVAL_HOURS"
ENV_RETENTION_DAYS = "YULE_DIGEST_DEDUP_RETENTION_DAYS"


def _is_truthy(value: Optional[str]) -> bool:
    if not value:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SchedulerConfig:
    enabled: bool
    interval_hours: int
    retention_days: int

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "SchedulerConfig":
        e = env if env is not None else os.environ
        return cls(
            enabled=_is_truthy(e.get(ENV_ENABLED)),
            interval_hours=max(1, _safe_int(e.get(ENV_INTERVAL_HOURS), 12)),
            retention_days=max(1, _safe_int(e.get(ENV_RETENTION_DAYS), 14)),
        )


@dataclass(frozen=True)
class DigestCycleReport:
    """1회 사이클 결과 요약."""

    roles_processed: int
    sources_attempted: int
    cards_total: int
    skipped_duplicates: int
    blocked_sources: int
    dispatch: DispatchPlan


async def run_one_cycle(
    *,
    roles: Sequence[str],
    ledger: DigestDedupLedger,
    http_poster: Optional[HttpPoster] = None,
    env: Optional[Mapping[str, str]] = None,
    post_fn: Optional[Callable[[DispatchPlan], Awaitable[None]]] = None,
) -> DigestCycleReport:
    """주어진 roles 의 카탈로그 전부 크롤 → dedup → DispatchPlan 빌드 → (옵션) 실 게시.

    ``post_fn`` 미주입 시 plan 만 반환 (dry-run / test).
    """

    all_cards: list = []
    blocked = 0
    skipped = 0
    sources_attempted = 0

    for role in roles:
        outcomes = crawl_role(role, ledger=ledger, http_poster=http_poster)
        for outcome in outcomes:
            sources_attempted += 1
            skipped += outcome.skipped_duplicates
            if outcome.blocker_reason:
                blocked += 1
                logger.warning(
                    "digest source blocked role=%s host=%s reason=%s",
                    role, outcome.source_host, outcome.blocker_reason,
                )
                continue
            all_cards.extend(outcome.cards)

    plan = build_dispatch_plan(all_cards, env=env)

    if post_fn is not None:
        try:
            await post_fn(plan)
        except Exception:  # noqa: BLE001 — caller 가 ack 처리. 본 cycle 은 계속.
            logger.exception("digest post_fn raised")

    # 카드 게시 성공 가정 시 ledger 기록
    for target in plan.targets:
        if target.target_kind != "dept_feed":
            continue  # research_forum_thread 는 dedup 별도 (thread 자체가 dedup unit)
        ledger.record_posted(
            url=target.card.url,
            title=target.card.title,
            host=target.card.source_host,
            dept=target.card.dept_primary,
        )

    return DigestCycleReport(
        roles_processed=len(roles),
        sources_attempted=sources_attempted,
        cards_total=len(all_cards),
        skipped_duplicates=skipped,
        blocked_sources=blocked,
        dispatch=plan,
    )


async def run_scheduler(
    *,
    roles: Optional[Sequence[str]] = None,
    env: Optional[Mapping[str, str]] = None,
    ledger: Optional[DigestDedupLedger] = None,
    http_poster: Optional[HttpPoster] = None,
    post_fn: Optional[Callable[[DispatchPlan], Awaitable[None]]] = None,
    shutdown_event: Optional[asyncio.Event] = None,
    sleep_fn: Optional[Callable[[float], Awaitable[None]]] = None,
) -> int:
    """Long-running scheduler loop. service supervisor 가 spawn 한다.

    Returns: 0 정상 종료, ``78`` env disabled (systemd prevent-restart).
    """

    cfg = SchedulerConfig.from_env(env)
    if not cfg.enabled:
        logger.info("digest scheduler disabled (env %s not true) — exiting", ENV_ENABLED)
        return 78  # systemd RestartPreventExitStatus

    target_roles = tuple(roles or ROLE_SOURCE_CATALOG.keys())
    led = ledger or DigestDedupLedger(retention_days=cfg.retention_days)
    sleep = sleep_fn or asyncio.sleep
    shutdown = shutdown_event or asyncio.Event()
    interval_seconds = cfg.interval_hours * 3600

    logger.info(
        "digest scheduler starting — roles=%s interval=%dh retention=%dd",
        target_roles, cfg.interval_hours, cfg.retention_days,
    )

    while not shutdown.is_set():
        report = await run_one_cycle(
            roles=target_roles,
            ledger=led,
            http_poster=http_poster,
            env=env,
            post_fn=post_fn,
        )
        logger.info(
            "digest cycle — roles=%d sources=%d cards=%d skipped=%d blocked=%d targets=%d",
            report.roles_processed, report.sources_attempted,
            report.cards_total, report.skipped_duplicates,
            report.blocked_sources, len(report.dispatch.targets),
        )
        led.prune_expired()
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue  # 다음 사이클

    logger.info("digest scheduler shutdown")
    return 0


__all__ = (
    "DigestCycleReport",
    "ENV_ENABLED",
    "ENV_INTERVAL_HOURS",
    "ENV_RETENTION_DAYS",
    "SchedulerConfig",
    "run_one_cycle",
    "run_scheduler",
)
