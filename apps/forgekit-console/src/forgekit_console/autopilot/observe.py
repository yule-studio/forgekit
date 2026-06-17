"""Autopilot observe/classify (repo-autopilot WT3) — discomfort → improvement packets.

Gathers signals from multiple cheap sources and frames each as a user-discomfort
improvement packet (reusing :mod:`selfimprove`), so the autopilot chain has real
findings to route. Sources: repo-local gaps (TODO/large files), discovery self-improve
signals, and UI discomfort. UI discomfort consults a design reference ONLY if a Figma/
reference connection exists; otherwise the packet is honestly flagged
``reference_missing`` (no fake design read).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .artifacts import RepoFinding

# UI reference connection states (honest) ------------------------------------
REF_CONNECTED = "connected"
REF_NOT_CONNECTED = "figma_not_connected"
REF_MISSING = "reference_missing"


@dataclass(frozen=True)
class UIReferenceState:
    """Whether a design reference is available for UI discomfort comparison."""

    state: str = REF_NOT_CONNECTED
    detail: str = "Figma/MCP 미연결 — UI 개선은 reference 없이 진행, 연결 시 비교 강화"

    @property
    def connected(self) -> bool:
        return self.state == REF_CONNECTED


def default_ui_reference() -> UIReferenceState:
    """No live Figma/MCP wired in this stage → honest not-connected (never fake-read)."""

    return UIReferenceState(REF_NOT_CONNECTED)


def observe_repo(repo: str, repo_root, *, discovery_signals: Sequence = (),
                 ui_discomfort: Sequence[str] = (),
                 ui_reference: Optional[UIReferenceState] = None,
                 limit: int = 12) -> List[RepoFinding]:
    """Observe signals → RepoFindings (with discomfort framing) for the autopilot chain."""

    ref = ui_reference or default_ui_reference()
    findings: List[RepoFinding] = []

    # 1) repo-local gaps (offline)
    try:
        from ..sources import RepoLocalCollector

        for it in RepoLocalCollector(repo_root).collect(limit=limit):
            kind = "docs" if "TODO" in it.title else "gap"
            findings.append(RepoFinding(repo, it.title, kind=kind,
                                        evidence=it.summary or "repo-local scan"))
    except Exception:  # noqa: BLE001
        pass

    # 2) discovery self-improve signals
    for s in discovery_signals:
        text = getattr(s, "text", str(s))
        findings.append(RepoFinding(repo, text, kind="discomfort",
                                    evidence="idea-discovery self-improve signal"))

    # 3) UI discomfort — compared to a reference IF connected, else flagged honestly
    for d in ui_discomfort:
        ev = ("design reference 비교" if ref.connected
              else f"{ref.state} — reference 없이 기록 ({ref.detail})")
        findings.append(RepoFinding(repo, d, kind="discomfort", evidence=ev))

    return findings[:limit]


def to_improvement_packets(findings: Sequence[RepoFinding], *, risk_of=lambda f: ""):
    """Frame findings as RepoImprovementPackets (reuse selfimprove) — user-value framing."""

    from ..selfimprove import make_packet

    out = []
    for f in findings:
        out.append(make_packet(
            f.finding, why="autopilot 관측 — 유지보수/UX/신뢰 영향",
            area=f.kind, change="내부 승인 후 safe-class 적용 또는 제안",
            owner="tech-lead", origin=f"autopilot:{f.repo}",
            discomfort=f.evidence or "관측된 마찰"))
    return out


__all__ = (
    "REF_CONNECTED", "REF_NOT_CONNECTED", "REF_MISSING",
    "UIReferenceState", "default_ui_reference", "observe_repo", "to_improvement_packets",
)
