"""Curation gate — candidate → promote / raw / blocked (pure policy).

This is the lane's spine: the single deterministic decision about whether an
:class:`ExternalCandidate` is (a) BLOCKED (risk/shape/license/allowlist), (b) ready
to PROMOTE to an Armory candidate, or (c) kept as RAW intake (metadata too thin to
judge). No service layer, no state store — just a function and a result dataclass.
Actual Armory registration of a promoted candidate is a separate, approval-gated
step; this gate only decides eligibility (no auto-install).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from . import candidate as K
from .candidate import ExternalCandidate

# licenses that are acceptable for promotion if explicitly known ----------------
# (unknown is NOT acceptable for promote — it stays raw until vetted.)
_BLOCKED_LICENSES = {K.LICENSE_PROPRIETARY}


@dataclass(frozen=True)
class CurationVerdict:
    """The gate's decision for one candidate + the reasons behind it."""

    candidate: ExternalCandidate
    disposition: str = K.DISPOSITION_RAW
    reasons: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "candidate": self.candidate.to_dict(),
            "disposition": self.disposition,
            "reasons": list(self.reasons),
        }


def curate(
    cand: ExternalCandidate,
    *,
    shape_allowlist: Sequence[str] = K.DEFAULT_SHAPE_ALLOWLIST,
    source_allowlist: Sequence[str] = K.DEFAULT_SOURCE_ALLOWLIST,
    blocklist_fingerprints: Sequence[str] = (),
) -> CurationVerdict:
    """Decide a candidate's disposition. blocked > promote > raw (first match wins)."""

    shapes = set(shape_allowlist)
    sources = set(source_allowlist)
    blocked_fps = set(blocklist_fingerprints)

    # --- BLOCKED: hard rejections (never promotable) ---
    block_reasons: List[str] = []
    if cand.fingerprint in blocked_fps:
        block_reasons.append("operator blocklist 명시")
    if cand.install_shape not in shapes:
        block_reasons.append(f"install_shape '{cand.install_shape}' allowlist 밖 (backend 등)")
    if cand.source not in sources:
        block_reasons.append(f"source '{cand.source}' allowlist 밖")
    if cand.trust_risk == K.RISK_HIGH:
        block_reasons.append("trust_risk=high")
    if cand.maintenance_signal == K.MAINT_ARCHIVED:
        block_reasons.append("maintenance=archived")
    if cand.license in _BLOCKED_LICENSES:
        block_reasons.append(f"license '{cand.license}' 거부")
    if block_reasons:
        return CurationVerdict(cand, K.DISPOSITION_BLOCKED, tuple(block_reasons))

    # --- PROMOTE: meets the Armory-candidate bar ---
    promote_misses: List[str] = []
    if not cand.has_min_metadata:
        promote_misses.append("필수 메타(repo_url/name/capability/why) 부족")
    if cand.license == K.LICENSE_UNKNOWN:
        promote_misses.append("license 미상 — 확인 필요")
    if cand.trust_risk not in (K.RISK_LOW, K.RISK_MEDIUM):
        promote_misses.append("trust_risk 미평가")
    if cand.maintenance_signal != K.MAINT_ACTIVE:
        promote_misses.append("maintenance active 미확인")
    if not promote_misses:
        return CurationVerdict(
            cand, K.DISPOSITION_PROMOTE,
            (f"Armory candidate 자격 충족: {cand.install_shape}/{cand.capability_class} "
             f"({cand.provider_affinity}, {cand.license})",))

    # --- RAW: not blocked, not yet promotable ---
    return CurationVerdict(cand, K.DISPOSITION_RAW, tuple(promote_misses))


@dataclass
class IntakePacket:
    """The operator-facing result of an intake sweep — grouped by disposition.

    A plain result aggregate (like ``DiscoveryResult``), NOT a persistent store:
    evidence is the serialised snapshot, persistence is a planned seam.
    """

    verdicts: Tuple[CurationVerdict, ...] = ()
    source_status: Tuple[dict, ...] = ()   # which sources were live vs planned

    def _by(self, disposition: str) -> Tuple[CurationVerdict, ...]:
        return tuple(v for v in self.verdicts if v.disposition == disposition)

    @property
    def promoted(self) -> Tuple[CurationVerdict, ...]:
        return self._by(K.DISPOSITION_PROMOTE)

    @property
    def raw(self) -> Tuple[CurationVerdict, ...]:
        return self._by(K.DISPOSITION_RAW)

    @property
    def blocked(self) -> Tuple[CurationVerdict, ...]:
        return self._by(K.DISPOSITION_BLOCKED)

    @property
    def counts(self) -> Dict[str, int]:
        return {
            K.DISPOSITION_PROMOTE: len(self.promoted),
            K.DISPOSITION_RAW: len(self.raw),
            K.DISPOSITION_BLOCKED: len(self.blocked),
        }

    def to_dict(self) -> dict:
        return {
            "counts": self.counts,
            "promoted": [v.to_dict() for v in self.promoted],
            "raw": [v.to_dict() for v in self.raw],
            "blocked": [v.to_dict() for v in self.blocked],
            "source_status": list(self.source_status),
        }


def curate_all(
    cands: Sequence[ExternalCandidate],
    *,
    shape_allowlist: Sequence[str] = K.DEFAULT_SHAPE_ALLOWLIST,
    source_allowlist: Sequence[str] = K.DEFAULT_SOURCE_ALLOWLIST,
    blocklist_fingerprints: Sequence[str] = (),
    source_status: Sequence[dict] = (),
) -> IntakePacket:
    """Run the gate over every candidate → an :class:`IntakePacket` (sorted by score)."""

    verdicts = tuple(
        curate(c, shape_allowlist=shape_allowlist, source_allowlist=source_allowlist,
               blocklist_fingerprints=blocklist_fingerprints)
        for c in sorted(cands, key=lambda c: c.score, reverse=True)
    )
    return IntakePacket(verdicts=verdicts, source_status=tuple(source_status))


__all__ = ("CurationVerdict", "IntakePacket", "curate", "curate_all")
