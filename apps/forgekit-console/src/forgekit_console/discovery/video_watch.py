"""Video-watch ingest (WT3) — manual / low-cost, NOT a live video crawler.

This stage is honest about cost: it ingests an operator-provided transcript or notes
(free) and summarises + extracts ideas from them. A bare link with no transcript is
``reference_only`` — we do NOT fetch/transcribe video (YouTube/IG are planned source
seams, never fake-live). So the same mode works today without paid APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from . import models as M
from . import pipeline as P

STATUS_LIVE = "live"                 # transcript/notes ingested + summarised
STATUS_REFERENCE_ONLY = "reference_only"  # link only — no free transcript fetch (honest)


@dataclass(frozen=True)
class VideoIngest:
    link: str = ""
    transcript: str = ""
    notes: str = ""

    @property
    def has_text(self) -> bool:
        return bool(self.transcript.strip() or self.notes.strip())


@dataclass(frozen=True)
class VideoWatchResult:
    status: str
    summary: str = ""
    ideas: Tuple[M.IdeaBrief, ...] = ()
    reference: dict = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status, "summary": self.summary,
            "ideas": [i.to_dict() for i in self.ideas],
            "reference": self.reference, "note": self.note,
        }


def _sentences(text: str) -> Tuple[str, ...]:
    parts = []
    for chunk in text.replace("\n", ". ").split("."):
        c = chunk.strip()
        if len(c) >= 8:
            parts.append(c)
    return tuple(parts)


def summarize_ingest(ingest: VideoIngest) -> VideoWatchResult:
    """Summarise an ingest → ideas. Link-only → reference_only (no fake fetch)."""

    if not ingest.has_text:
        return VideoWatchResult(
            status=STATUS_REFERENCE_ONLY,
            reference={"link": ingest.link},
            note=("전사/노트가 없어 요약 불가 — 영상 live 크롤은 이번 단계 미연결(planned). "
                  "operator 가 transcript/notes 를 제공하면 live 요약됩니다."),
        )
    text = ingest.transcript or ingest.notes
    sents = _sentences(text)
    summary = " · ".join(sents[:3]) if sents else text[:160]
    # reuse the idea pipeline on the sentences (offline, deterministic)
    result = P.run_idea_discovery(list(sents), title="video-watch ingest")
    return VideoWatchResult(
        status=STATUS_LIVE, summary=summary, ideas=result.idea_briefs,
        reference={"link": ingest.link} if ingest.link else {},
        note="operator-provided transcript/notes 기반 저비용 요약 (live 크롤 아님)",
    )


__all__ = ("STATUS_LIVE", "STATUS_REFERENCE_ONLY", "VideoIngest", "VideoWatchResult",
           "summarize_ingest")
