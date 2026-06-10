"""F13 — 부서 채널 게시 dispatcher.

사용자 정책 (2026-05-12):
- 단일 부서 → 그 부서 채널만 게시
- 다중 부서 → ``#운영-리서치`` thread + 각 부서 채널 양쪽 게시
- 실행 필요 시 ``#업무-접수``, 승인 필요 시 ``#승인-대기`` 로 후속 라우팅 (본 PR 비범위)

Discord 게시는 caller (e.g. `discord/gateway`) 가 실 호출. 본 모듈은
구조화된 ``DispatchPlan`` 만 반환 — Discord 모듈과 분리해서 unit-testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Tuple

from .formatter import DigestCard


# .env.local 의 부서 채널 ID env
ENV_DEPT_CHANNELS: Mapping[str, str] = {
    "planning": "DISCORD_DEPT_PLANNING_CHANNEL_ID",
    "design": "DISCORD_DEPT_DESIGN_CHANNEL_ID",
    "engineering": "DISCORD_DEPT_DEV_CHANNEL_ID",
}

ENV_RESEARCH_FORUM = "DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_ID"


@dataclass(frozen=True)
class DispatchTarget:
    """한 카드의 한 게시 대상."""

    channel_id: str
    channel_name: str
    card: DigestCard
    target_kind: str  # "dept_feed" | "research_forum_thread"


@dataclass(frozen=True)
class DispatchPlan:
    """전체 디지스트 사이클의 게시 계획."""

    targets: Tuple[DispatchTarget, ...]
    skipped_no_channel: Tuple[str, ...] = ()
    research_forum_threads: Tuple[str, ...] = ()


def _resolve_channel_id(env: Mapping[str, str], dept: str) -> Optional[str]:
    env_key = ENV_DEPT_CHANNELS.get(dept)
    if not env_key:
        return None
    val = (env.get(env_key) or "").strip()
    return val or None


def build_dispatch_plan(
    cards: Sequence[DigestCard],
    *,
    env: Optional[Mapping[str, str]] = None,
) -> DispatchPlan:
    """카드 시퀀스 → 부서별 게시 계획.

    단일 부서 카드 → primary dept 채널만.
    multi-dept 카드 → primary + affected 모든 부서 채널 + research forum thread.
    """

    env_map = env if env is not None else os.environ

    targets: list = []
    skipped_no_channel: set = set()
    research_threads: list = []

    for card in cards:
        if card.meeting_trigger:
            # 다중 부서 — 영향 부서 모두 + research forum thread
            for dept in card.affected_depts:
                channel_id = _resolve_channel_id(env_map, dept)
                if not channel_id:
                    skipped_no_channel.add(dept)
                    continue
                targets.append(
                    DispatchTarget(
                        channel_id=channel_id,
                        channel_name=f"dept-{dept}",
                        card=card,
                        target_kind="dept_feed",
                    )
                )
            # research forum thread title
            forum_id = (env_map.get(ENV_RESEARCH_FORUM) or "").strip()
            if forum_id:
                research_threads.append(
                    f"[Research] {card.title[:80]} (영향: {', '.join(card.affected_depts)})"
                )
                # forum 게시도 target 으로
                targets.append(
                    DispatchTarget(
                        channel_id=forum_id,
                        channel_name="운영-리서치",
                        card=card,
                        target_kind="research_forum_thread",
                    )
                )
        else:
            # 단일 부서 — primary 만
            channel_id = _resolve_channel_id(env_map, card.dept_primary)
            if not channel_id:
                skipped_no_channel.add(card.dept_primary)
                continue
            targets.append(
                DispatchTarget(
                    channel_id=channel_id,
                    channel_name=f"dept-{card.dept_primary}",
                    card=card,
                    target_kind="dept_feed",
                )
            )

    return DispatchPlan(
        targets=tuple(targets),
        skipped_no_channel=tuple(sorted(skipped_no_channel)),
        research_forum_threads=tuple(research_threads),
    )


__all__ = (
    "DispatchPlan",
    "DispatchTarget",
    "ENV_DEPT_CHANNELS",
    "ENV_RESEARCH_FORUM",
    "build_dispatch_plan",
)
