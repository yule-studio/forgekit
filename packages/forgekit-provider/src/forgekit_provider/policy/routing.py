"""Submit routing resolution (forgekit brain teeth).

Turns the operator's :class:`ProviderConfig` + the active runtime mode into ONE honest
answer to "which provider actually handles this submit, and is that a live path?".

The resolution chain (no magic, no implicit local fallback):
  1. the active **mode** picks a brain **slot** (research mode → research slot, etc.).
  2. the config's **slot_routing** gives the *declared* provider for that slot.
  3. candidates = [declared] + the slot's **explicit** fallback order
     (+ ollama ONLY if ``implicit_local_fallback`` is explicitly enabled AND linked).
  4. the first candidate whose transport supports console live-submit wins (``actual``).
  5. if none support live-submit → ``unsupported_in_console`` (honest — never faked live).

So ``declared`` vs ``actual`` are always distinguishable, fallback is always explicit
and surfaced, and a no-config state resolves to ``no_config`` (setup required) — forgekit
never silently routes to a reachable local ollama.

Pure / stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

from ..providers import builtins
from ..providers.contract import SUBMIT_OPENAI, ProviderSpec
from . import provider_config as pc
from . import runtime_mode as rm

# resolution status
RESOLVE_OK = "resolved"                       # actual provider supports console live-submit
RESOLVE_FALLBACK = "fallback"                 # declared unusable → explicit fallback used
RESOLVE_UNSUPPORTED = "unsupported_in_console"  # selected provider has no console transport
RESOLVE_NO_CONFIG = "no_config"               # nothing configured → setup required

# work kind — a submit is either an operator CHAT turn or an autonomous NON-CHAT work item.
WORK_CHAT = "chat"          # operator free-text conversation → always the default_chat slot
WORK_NONCHAT = "nonchat"    # autonomous work (execution/research/…) → the mode's WORK slot

# mode → the brain slot the mode's autonomous NON-CHAT work uses (mode steers work routing).
# Chat is deliberately ABSENT here: an operator chat turn is never silently routed to a work
# slot by the mode (that conflated chat with work) — chat is always default_chat.
_MODE_WORK_SLOT = {
    rm.MODE_RESEARCH: pc.SLOT_RESEARCH,
    rm.MODE_IDEA_DISCOVERY: pc.SLOT_RESEARCH,
    rm.MODE_DELIVERY: pc.SLOT_EXECUTION,
    rm.MODE_REPO_AUTOPILOT: pc.SLOT_EXECUTION,
    rm.MODE_SELF_IMPROVEMENT: pc.SLOT_EXECUTION,
    rm.MODE_COST_SAVE: pc.SLOT_COMPRESSION,    # cheapest slot
    rm.MODE_WATCH: pc.SLOT_CLASSIFICATION,
    rm.MODE_RED_BLUE: pc.SLOT_SAFETY,
    # modes with no distinct work slot fall back to default_chat.
}


def mode_work_slot(mode_id: str) -> str:
    """The brain slot the active mode's autonomous NON-CHAT work routes to."""

    return _MODE_WORK_SLOT.get(mode_id, pc.SLOT_DEFAULT_CHAT)


def slot_for(mode_id: str, kind: str = WORK_CHAT) -> str:
    """The brain slot for a submit of *kind* under *mode_id*.

    ``chat`` → ``default_chat`` ALWAYS (an operator chat turn is chat, never re-routed to a
    work slot by the mode — matches the live submit path, which uses ``default_chat``).
    ``nonchat`` → the mode's work slot (:func:`mode_work_slot`)."""

    return pc.SLOT_DEFAULT_CHAT if kind == WORK_CHAT else mode_work_slot(mode_id)


def mode_submit_slot(mode_id: str) -> str:
    """Back-compat: the mode's NON-CHAT work slot (== :func:`mode_work_slot`).

    Retained for existing callers; new code should call :func:`slot_for` with an explicit
    ``kind`` so chat (default_chat) and non-chat work (mode slot) stay separated."""

    return mode_work_slot(mode_id)


def submit_supported(spec: Optional[ProviderSpec]) -> bool:
    """True if this provider has a wired console live-submit transport.

    Today that is exactly the openai-compatible transport (ollama / openai / gemini
    compat). CLI providers (claude / codex) are routable but NOT live in the console
    — reported honestly as unsupported_in_console, never faked."""

    return spec is not None and spec.submit_compat == SUBMIT_OPENAI


def _spec_of(provider_id: str) -> Optional[ProviderSpec]:
    return builtins.builtin(provider_id)


@dataclass(frozen=True)
class RoutingResolution:
    """The honest declared-vs-actual routing answer for one submit."""

    slot: str
    declared_provider: str
    actual_provider: str
    status: str
    fallback_used: bool = False
    fallback_chain: Tuple[str, ...] = field(default_factory=tuple)
    submit_supported: bool = False
    reason: str = ""

    @property
    def is_live_capable(self) -> bool:
        return self.status in (RESOLVE_OK, RESOLVE_FALLBACK) and self.submit_supported

    def to_dict(self) -> dict:
        return {
            "slot": self.slot, "declared_provider": self.declared_provider,
            "actual_provider": self.actual_provider, "status": self.status,
            "fallback_used": self.fallback_used, "fallback_chain": list(self.fallback_chain),
            "submit_supported": self.submit_supported, "reason": self.reason,
        }


def resolve_routing(
    cfg: pc.ProviderConfig,
    slot: str,
    *,
    supported: Callable[[str], bool] = None,
    available: Callable[[str], bool] = None,
) -> RoutingResolution:
    """Resolve *slot* → an actual provider via declared + explicit fallback only.

    ``supported`` overrides the live-submit capability check (defaults to transport);
    ``available`` optionally gates by runtime reachability/auth (defaults to always)."""

    if not cfg.primary_provider:
        return RoutingResolution(slot, "", "", RESOLVE_NO_CONFIG,
                                 reason="provider 미설정 — `/setup` 으로 브레인을 구성하세요")

    sup = supported or (lambda pid: submit_supported(_spec_of(pid)))
    avail = available or (lambda pid: True)

    declared = cfg.slot_target(slot)
    # candidate order: declared, then EXPLICIT fallback order for the slot.
    chain = [declared, *[p for p in cfg.fallback_order(slot) if p != declared]]
    # implicit local fallback ONLY when explicitly enabled AND ollama is linked.
    if cfg.implicit_local_fallback and "ollama" in cfg.linked_providers and "ollama" not in chain:
        chain.append("ollama")

    tried = []
    for i, pid in enumerate(chain):
        if pid not in cfg.linked_providers:
            continue
        tried.append(pid)
        if not avail(pid):
            continue
        if sup(pid):
            is_fb = i > 0
            return RoutingResolution(
                slot=slot, declared_provider=declared, actual_provider=pid,
                status=RESOLVE_FALLBACK if is_fb else RESOLVE_OK,
                fallback_used=is_fb, fallback_chain=tuple(tried),
                submit_supported=True,
                reason=(f"declared '{declared}' 불가 → fallback '{pid}'" if is_fb
                        else f"slot '{slot}' → {pid} (declared)"),
            )
    # nothing in the chain supports console live-submit → honest unsupported.
    return RoutingResolution(
        slot=slot, declared_provider=declared, actual_provider=declared,
        status=RESOLVE_UNSUPPORTED, fallback_chain=tuple(tried), submit_supported=False,
        reason=(f"'{declared}' 는 콘솔 live-submit 미구현(routable). "
                "explicit fallback 도 live 불가 — 설정 또는 fallback 정책을 조정하세요"),
    )


def resolve_submit(cfg: pc.ProviderConfig, mode_id: str, *,
                   kind: str = WORK_CHAT, **kw) -> RoutingResolution:
    """Top-level: (mode, kind) → slot → resolved routing.

    ``kind=chat`` (default) resolves the ``default_chat`` slot — the honest answer for an
    operator chat turn (and what the live submit path actually uses). ``kind=nonchat``
    resolves the mode's WORK slot, so autonomous work routes by the mode, not by chat."""

    return resolve_routing(cfg, slot_for(mode_id, kind), **kw)


def submit_chain(cfg: pc.ProviderConfig, slot: str, *, prefer: str = "") -> Tuple[str, ...]:
    """The ORDERED provider ids the submit service should ATTEMPT for *slot*.

    head = ``prefer`` (the gate's routing target) or the slot's declared provider;
    then the slot's EXPLICIT fallback order; then ollama ONLY when implicit local
    fallback is explicitly enabled AND ollama is linked. Deduped, empties dropped.

    This is the pure builder the live submit path iterates — so ``slot_fallback_orders``
    and ``slot_routing`` actually steer which provider is called (and in what order),
    not just the display. No implicit ollama unless the operator opted in.
    """

    head = (prefer or cfg.slot_target(slot) or "").strip()
    ordered = [head, *cfg.fallback_order(slot)]
    if cfg.implicit_local_fallback and "ollama" in cfg.linked_providers:
        ordered.append("ollama")
    seen = set()
    chain = []
    for pid in ordered:
        pid = (pid or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        chain.append(pid)
    return tuple(chain)


__all__ = (
    "RESOLVE_OK", "RESOLVE_FALLBACK", "RESOLVE_UNSUPPORTED", "RESOLVE_NO_CONFIG",
    "WORK_CHAT", "WORK_NONCHAT", "mode_work_slot", "slot_for", "mode_submit_slot",
    "submit_supported", "RoutingResolution",
    "resolve_routing", "resolve_submit", "submit_chain",
)
