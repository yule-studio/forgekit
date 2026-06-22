"""Auto-recommend routing engine (forgekit brain advisor).

Given the operator's current brain config + the active mode (+ optional usage/budget
signals), forgekit *suggests* a better provider routing — with a stated reason for
every suggestion. It NEVER mutates config: this is suggestion-only; the operator (or
a future explicit apply step) decides.

Three tiers so the operator knows the weight of each suggestion:
  * ``safe``     — a clear improvement at no real cost (link a capable provider to its
    natural slot).
  * ``tradeoff`` — an improvement that changes something the operator may care about
    (cheaper but a different synthesis voice, etc.).
  * ``blocked``  — a real constraint to fix (a slot routed to a provider with no console
    live-submit), surfaced honestly rather than hidden.

Pure / stdlib-only → deterministic + unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from ..providers import builtins
from ..providers.contract import (
    CAP_CHEAP,
    CAP_EXECUTION,
    CAP_RESEARCH,
    CAP_SAFETY,
    CAP_SYNTHESIS,
)
from . import provider_config as pc
from . import routing as rt
from . import runtime_mode as rm

TIER_SAFE = "safe"
TIER_TRADEOFF = "tradeoff"
TIER_BLOCKED = "blocked"

# the capability that ideally fills each slot.
_SLOT_IDEAL_CAP = {
    pc.SLOT_RESEARCH: CAP_RESEARCH,
    pc.SLOT_EXECUTION: CAP_EXECUTION,
    pc.SLOT_SYNTHESIS: CAP_SYNTHESIS,
    pc.SLOT_SAFETY: CAP_SAFETY,
    pc.SLOT_COMPRESSION: CAP_CHEAP,
    pc.SLOT_CLASSIFICATION: CAP_CHEAP,
}


@dataclass(frozen=True)
class Recommendation:
    tier: str
    title: str
    reason: str
    action: str
    slot: str = ""
    provider: str = ""

    def to_dict(self) -> dict:
        return {"tier": self.tier, "title": self.title, "reason": self.reason,
                "action": self.action, "slot": self.slot, "provider": self.provider}


def _provider_with_cap(cap: str, linked: Tuple[str, ...]) -> str:
    for pid in linked:
        spec = builtins.builtin(pid)
        if spec is not None and spec.has_capability(cap):
            return pid
    return ""


def recommend(cfg: pc.ProviderConfig, mode_id: str, *,
              usage: Optional[Mapping] = None,
              budget_high_providers: Tuple[str, ...] = ()) -> Tuple[Recommendation, ...]:
    """Reasoned routing suggestions for the current config + mode (suggestion-only)."""

    if not cfg.primary_provider:
        return (Recommendation(
            TIER_BLOCKED, "브레인 미설정",
            "primary provider 가 없어 어떤 slot 도 라우팅할 수 없습니다",
            "`/setup` 으로 primary + linked provider 를 구성하세요"),)

    recs = []
    linked = cfg.linked_providers

    # 1) capability-aligned slot routing — link a capable provider to its natural slot.
    for slot, cap in _SLOT_IDEAL_CAP.items():
        target = cfg.slot_target(slot)
        target_spec = builtins.builtin(target)
        if target_spec is not None and target_spec.has_capability(cap):
            continue  # already well-routed
        better = _provider_with_cap(cap, linked)
        if better and better != target:
            recs.append(Recommendation(
                TIER_SAFE, f"{slot} → {better}",
                f"slot '{slot}' 는 현재 {target} 인데 {better} 가 {cap} capability 를 가집니다",
                f"`/provider route {slot} {better}`", slot=slot, provider=better))

    # 2) active-mode WORK slot must be live-capable — surface unsupported as blocked.
    #    (non-chat work routes by the mode's slot; chat always uses default_chat separately.)
    res = rt.resolve_submit(cfg, mode_id, kind=rt.WORK_NONCHAT)
    if res.status == rt.RESOLVE_UNSUPPORTED:
        live_alt = _provider_with_cap(CAP_CHEAP, linked) or \
            next((p for p in linked if rt.submit_supported(builtins.builtin(p))), "")
        if live_alt:
            recs.append(Recommendation(
                TIER_BLOCKED, f"{res.slot} live-submit 불가",
                f"'{res.declared_provider}' 는 routable 이지만 콘솔 live-submit 미구현입니다",
                f"`/provider route {res.slot} {live_alt}` 또는 fallback 정책 추가", slot=res.slot,
                provider=live_alt))
        else:
            recs.append(Recommendation(
                TIER_BLOCKED, f"{res.slot} live 불가",
                f"'{res.declared_provider}' live-submit 미구현, live 가능한 linked provider 없음",
                "openai-compatible provider(ollama/gemini)를 link 하세요", slot=res.slot))

    # 3) budget-aware tradeoff — primary spending high → move default_chat to a cheaper voice.
    for high in budget_high_providers:
        if cfg.slot_target(pc.SLOT_DEFAULT_CHAT) == high:
            cheap = _provider_with_cap(CAP_CHEAP, linked)
            if cheap and cheap != high:
                recs.append(Recommendation(
                    TIER_TRADEOFF, f"default_chat → {cheap}",
                    f"{high} 예산 임계 근접 — synthesis 는 {high} 유지, 기본 대화는 {cheap} 로 비용↓",
                    f"`/provider route default_chat {cheap}` (voice 변화 감수)",
                    slot=pc.SLOT_DEFAULT_CHAT, provider=cheap))

    # 4) cost-save mode but cheap fallback not enabled → offer the choice.
    if mode_id == rm.MODE_COST_SAVE and not cfg.implicit_local_fallback and "ollama" in linked:
        recs.append(Recommendation(
            TIER_TRADEOFF, "cost-save: ollama fallback",
            "cost-save mode 인데 implicit local fallback 이 꺼져 있습니다",
            "fallback_policy.implicit_local_fallback=true 로 저가 경로 허용(선택)"))

    return tuple(recs)


def render_lines(recs: Tuple[Recommendation, ...]) -> Tuple[str, ...]:
    """Operator-facing lines (grouped by tier; reason always shown)."""

    if not recs:
        return ("현재 연결/모드 기준 추천 변경 없음 — 구성이 적절합니다.",)
    icon = {TIER_SAFE: "✓", TIER_TRADEOFF: "≈", TIER_BLOCKED: "✗"}
    lines = ["라우팅 추천 (suggestion-only — 자동 변경하지 않음):"]
    for rec in recs:
        lines.append(f"  {icon.get(rec.tier, '-')} [{rec.tier}] {rec.title}")
        lines.append(f"      이유: {rec.reason}")
        lines.append(f"      제안: {rec.action}")
    return tuple(lines)


__all__ = (
    "TIER_SAFE", "TIER_TRADEOFF", "TIER_BLOCKED",
    "Recommendation", "recommend", "render_lines",
)
