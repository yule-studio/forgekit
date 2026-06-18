"""Self-improvement (WT4) — observe → classify → packetize → route → wait (bounded).

Scans the repo (offline, reuses the WT2 repo-local collector) + optional discovery
self-improve signals, turns gaps into risk-classified :class:`RepoImprovementPacket`s,
and ROUTES them — but never executes:

  * SAFE   → routed to tech-lead as ready (auto-OK *within* the approval chain).
  * RISKY  → approval-wait (operator decision).
  * BLOCKED→ runbook + operator (deploy/secret/infra — never auto).

This is the bounded posture: observe/classify/packetize/route is automatic; any
mutation is gated. Deterministic + offline → testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import packet as P


@dataclass
class SelfImprovementResult:
    packets: List[P.RepoImprovementPacket] = field(default_factory=list)

    @property
    def safe(self):
        return [p for p in self.packets if p.risk == P.RISK_SAFE]

    @property
    def risky(self):
        return [p for p in self.packets if p.risk == P.RISK_RISKY]

    @property
    def blocked(self):
        return [p for p in self.packets if p.risk == P.RISK_BLOCKED]

    def to_dict(self) -> dict:
        return {
            "packets": [p.to_dict() for p in self.packets],
            "safe": len(self.safe), "risky": len(self.risky), "blocked": len(self.blocked),
        }


def _from_repo_local(repo_root, limit: int) -> List[P.RepoImprovementPacket]:
    from ..sources import RepoLocalCollector

    out: List[P.RepoImprovementPacket] = []
    try:
        items = RepoLocalCollector(repo_root).collect(limit=limit)
    except Exception:  # noqa: BLE001
        items = []
    for it in items:
        title = it.title
        if "TODO/FIXME" in title:
            out.append(P.make_packet(
                title, why="누적 TODO/FIXME 는 미완 의도/부채 신호 — 정리 시 가독성·신뢰 향상",
                area="docs/code", change="TODO 정리 또는 이슈화 + 작은 리팩터",
                owner="be", discomfort="개발자가 코드 의도를 신뢰하기 어렵다"))
        elif "줄" in title or "분리" in title:
            out.append(P.make_packet(
                title, why="1000 줄 초과 파일은 책임 분리 가드레일 위반 — 변경 위험↑",
                area="refactor", change="책임 분리(별도 모듈)", owner="be",
                discomfort="파일이 커서 수정 시 사이드이펙트가 두렵다"))
    return out


def _from_self_improve_signals(signals: Sequence) -> List[P.RepoImprovementPacket]:
    out: List[P.RepoImprovementPacket] = []
    for s in signals:
        text = getattr(s, "text", str(s))
        out.append(P.make_packet(
            text, why="discovery 가 잡은 forgekit 자체 개선 신호", area="docs/ux",
            change="개선안 패킷화 후 tech-lead 검토", owner="tech-lead",
            origin="idea-discovery", discomfort="operator/사용자가 콘솔에서 불편을 느낌"))
    return out


def run_self_improvement(repo_root, *, signals: Sequence = (), limit: int = 10
                         ) -> SelfImprovementResult:
    """observe (scan) → classify → packetize → (route is the caller's). Never executes."""

    packets = _from_repo_local(repo_root, limit) + _from_self_improve_signals(signals)
    return SelfImprovementResult(packets=packets[:limit])


def route_packet(packet: P.RepoImprovementPacket) -> str:
    """The routing decision for a packet (no execution)."""

    if packet.risk == P.RISK_BLOCKED:
        return "blocked → runbook + operator 승인 (자동 실행 금지)"
    if packet.risk == P.RISK_RISKY:
        return "risky → approval-wait (operator 결정)"
    return "safe → tech-lead ready (승인 체계 내 자동 가능)"


__all__ = ("SelfImprovementResult", "run_self_improvement", "route_packet")
