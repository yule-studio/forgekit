"""`/provider` operator surface — projection + apply over the provider_ops engine.

Read surfaces (status / list / doctor) are pure over a config dict; ``set_primary`` is the
one mutation (validate → set_primary → persist). This is the surface that makes the merged
multi-provider control-plane usable: the operator sets the **primary provider** explicitly,
so ForgeKit never looks like it "just uses Ollama" — a reachable local Ollama is only one
available provider, and with no config the honest state is setup-required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from ..providers import builtins
from ..providers.contract import SUBMIT_OPENAI
from ..usage import provider_budget as _pb
from . import provider_config as pc
from . import provider_ops as ops
from . import routing as rt


def _live_word(pid: str) -> str:
    spec = builtins.builtin(pid)
    if spec is None:
        return "custom"
    return "live" if spec.submit_compat == SUBMIT_OPENAI else "unsupported_in_console"


# ── honest per-provider state taxonomy (single SSoT for the 5 operator-facing states) ──
# A provider is reported as exactly ONE state. `live` is asserted ONLY from a VERIFIED probe
# result (no fake-live): without a probe we never claim live, we fall back to the config role
# (configured/linked) or its console transport capability (unsupported for CLI brains).
STATE_SETUP_REQUIRED = "setup-required"   # not part of the configured brain (primary nor linked)
STATE_CONFIGURED = "configured"           # the primary brain, console-capable, not verified live yet
STATE_LINKED = "linked"                   # a linked participant, console-capable, not verified live yet
STATE_LIVE = "live"                       # verified live console transport right now
STATE_UNSUPPORTED = "unsupported"         # in brain but console live-submit unsupported (CLI claude/codex)

PROVIDER_STATES = (
    STATE_SETUP_REQUIRED, STATE_CONFIGURED, STATE_LINKED, STATE_LIVE, STATE_UNSUPPORTED,
)


def classify_provider_state(pid: str, parsed, *, live_capable: Optional[bool] = None) -> str:
    """Honest single-state verdict for *pid* given the parsed brain config.

    ``live_capable`` is the VERIFIED probe result (True/False) or ``None`` when not probed.
    Precedence (most decision-relevant first): verified-live → not-in-brain → CLI-unsupported →
    primary(configured) → linked. ``live`` is never inferred — only a True probe yields it."""

    in_brain = (pid == parsed.primary_provider) or (pid in parsed.linked_providers)
    if live_capable is True:
        return STATE_LIVE
    if not in_brain:
        return STATE_SETUP_REQUIRED
    spec = builtins.builtin(pid)
    if spec is not None and spec.submit_compat != SUBMIT_OPENAI:
        # in the brain but its console transport can never live-submit (CLI attach = routing only).
        return STATE_UNSUPPORTED
    if pid == parsed.primary_provider:
        return STATE_CONFIGURED
    return STATE_LINKED


def provider_state_map(cfg: Optional[Mapping], *,
                       live_map: Optional[Mapping[str, bool]] = None) -> Tuple[Tuple[str, str], ...]:
    """`(pid, state)` for every built-in provider, honest per :func:`classify_provider_state`.

    ``live_map`` maps pid→verified live_capable (from a connect probe); absent entries are
    treated as unprobed (``None``) so no provider is faked into ``live``."""

    parsed = pc.load_provider_config(cfg)
    lm = dict(live_map or {})
    return tuple(
        (pid, classify_provider_state(pid, parsed, live_capable=lm.get(pid)))
        for pid in builtins.BUILTIN_PROVIDERS
    )


def _route_word(res) -> str:
    """Honest one-word verdict for a slot resolution (declared → actual)."""

    from . import routing as rt
    if res.status == rt.RESOLVE_FALLBACK:
        return f"live, fallback {res.declared_provider}→{res.actual_provider}"
    if res.is_live_capable:
        return "live"
    if res.status == rt.RESOLVE_UNSUPPORTED:
        return "routing only / no console submit"
    if res.status == rt.RESOLVE_NO_CONFIG:
        return "setup-required"
    return res.status


def provider_status_lines(cfg: Optional[Mapping]) -> Tuple[str, ...]:
    """`/provider` — current brain: primary / linked / live-capable / setup verdict."""

    review = ops.setup_review(cfg)
    if not pc.has_brain_config(cfg):
        return (
            "provider: [setup-required] primary provider 미설정",
            "  ForgeKit 은 operator 가 정한 primary provider 를 씁니다 — 자동 Ollama 사용 안 함.",
            "  다음: `/provider set <id>` (claude/codex/gemini/ollama) 또는 `~/.forgekit/config.json` 의 primary_provider.",
            "  설정 후 `/doctor` 로 점검, `/mode` 로 routing 확인.",
        )
    parsed = pc.load_provider_config(cfg)
    bmap = ops.brain_map(parsed)
    from . import routing as rt
    dc = rt.resolve_routing(parsed, pc.SLOT_DEFAULT_CHAT)
    ex = rt.resolve_routing(parsed, pc.SLOT_EXECUTION)
    lines = [
        f"provider: [{review.verdict}]",
        f"  primary brain : {parsed.primary_provider} ({_live_word(parsed.primary_provider)})",
        f"  linked        : {', '.join(parsed.linked_providers) or '-'}",
        # the honest brain-vs-transport split: what each slot DECLARES vs the ACTUAL live
        # provider a free-text submit reaches (declared may be a CLI brain; actual is the
        # live console transport, with explicit fallback surfaced).
        f"  default_chat  : declared {dc.declared_provider} → actual {dc.actual_provider} ({_route_word(dc)})",
        f"  execution     : declared {ex.declared_provider} → actual {ex.actual_provider} ({_route_word(ex)})",
        f"  live          : {', '.join(bmap.live_capable) or '(없음)'}",
        f"  unsupported_in_console: {', '.join(bmap.unsupported) or '-'}",
        f"  implicit local fallback: {'on' if parsed.implicit_local_fallback else 'off (기본)'}",
    ]
    # per-provider daily budget (configured policy, cfg-pure — live spend is in `/usage`).
    lines.extend(_pb.limit_lines(cfg))
    if not review.ready:
        lines += [f"  ⚠ {i}" for i in review.issues]
    lines.append("  명령: /provider set <id> · /provider list · /provider doctor")
    return tuple(lines)


def provider_list_lines(cfg: Optional[Mapping]) -> Tuple[str, ...]:
    """`/provider list` — built-in providers + console-submit capability + configured state."""

    parsed = pc.load_provider_config(cfg)
    lines = ["provider list (built-in):"]
    for pid, spec in builtins.BUILTIN_PROVIDERS.items():
        marks = []
        if pid == parsed.primary_provider:
            marks.append("primary")
        elif pid in parsed.linked_providers:
            marks.append("linked")
        marks.append(_live_word(pid))
        if spec.auth_kind == "api_key":
            marks.append(f"needs {pid.upper()}_API_KEY")
        lines.append(f"  • {spec.label:<12} [{pid}] — {', '.join(marks)}")
    lines.append("  [dim]claude/codex 는 routable 이지만 콘솔 live-submit 미구현(unsupported_in_console).[/dim]")
    return tuple(lines)


def provider_doctor_lines(cfg: Optional[Mapping]) -> Tuple[str, ...]:
    """`/provider doctor` — provider-focused diagnosis (subset of /doctor)."""

    parsed = pc.load_provider_config(cfg)
    review = ops.setup_review(cfg)
    res = rt.resolve_submit(parsed, "interactive") if parsed.primary_provider else None
    lines = [f"provider doctor: {review.verdict}"]
    if not parsed.primary_provider:
        lines.append("  primary 미설정 → submit setup-required (mode gate). `/provider set <id>`.")
        return tuple(lines)
    lines.append(f"  primary {parsed.primary_provider} · live={_live_word(parsed.primary_provider)}")
    if res is not None:
        lines.append(f"  default_chat 해소: {res.actual_provider or '-'} ({res.status})"
                     + (f" · fallback {'→'.join(res.fallback_chain)}" if res.fallback_used else ""))
    for issue in review.issues:
        lines.append(f"  ⚠ {issue}")
    return tuple(lines)


def apply_set_primary(pid: str, *, env: Optional[Mapping[str, str]] = None,
                      path: Optional[Path] = None) -> Tuple[bool, str]:
    """`/provider set <id>` — set primary provider + persist. Honest success/failure."""

    pid = (pid or "").strip()
    if not pid:
        return False, "provider id 가 필요합니다 — `/provider set <id>`."
    if not builtins.is_builtin(pid):
        return False, (f"알 수 없는 provider: {pid} (built-in: "
                       f"{', '.join(builtins.BUILTIN_PROVIDERS)}). custom 은 config 로 추가하세요.")
    cur = ops.load_raw_config(env=env, path=path)
    new_cfg = ops.set_primary(cur, pid)
    ok, where = ops.persist_config(new_cfg, env=env, path=path)
    if not ok:
        return False, f"저장 실패: {where}"
    live = _live_word(pid)
    note = "" if live == "live" else f" — 단 {live}(콘솔 live-submit 미구현, routing/usage 는 동작)"
    return True, f"primary provider = {pid} 저장됨{note}. `/provider` 로 확인, `/mode` 로 routing."


def _persist(new_cfg, env, path):
    ok, where = ops.persist_config(new_cfg, env=env, path=path)
    return (True, "") if ok else (False, f"저장 실패: {where}")


def apply_preset(name: str, *, env: Optional[Mapping[str, str]] = None,
                 path: Optional[Path] = None) -> Tuple[bool, str]:
    """`/provider preset <name>` — apply a multi-provider brain template + persist.

    Writes a REAL config (primary + linked + slot_routing + fallback + model_overrides),
    not just a label. The success line is honest about brain-vs-transport: the primary
    brain may be claude while the actual free-text live lane is the default_chat slot
    (gemini), with claude/codex as routing/brain participants only."""

    name = (name or "").strip()
    if name not in ops.PRESETS:
        return False, f"알 수 없는 preset: {name or '(없음)'} (사용 가능: {', '.join(ops.PRESETS)})."
    cur = ops.load_raw_config(env=env, path=path)
    new_cfg = ops.PRESETS[name](cur)
    ok, msg = _persist(new_cfg, env, path)
    if not ok:
        return False, msg
    parsed = pc.load_provider_config(new_cfg)
    dc = parsed.slot_target(pc.SLOT_DEFAULT_CHAT)
    return True, (
        f"preset '{name}' 저장됨 — primary brain = {parsed.primary_provider}, "
        f"linked = {', '.join(parsed.linked_providers)}.\n"
        f"  free-text live lane: default_chat → {dc} ({_live_word(dc)}); execution → "
        f"{parsed.slot_target('execution')}. claude/codex 는 routing/brain participant "
        f"(콘솔 live-submit 미구현). `/provider` 로 확인, `/mode` 로 routing."
    )


def apply_link(pid: str, *, env=None, path=None) -> Tuple[bool, str]:
    """`/provider link <id>` — add to linked providers + persist."""

    pid = (pid or "").strip()
    if not builtins.is_builtin(pid):
        return False, f"알 수 없는 provider: {pid} (built-in: {', '.join(builtins.BUILTIN_PROVIDERS)})."
    cur = ops.load_raw_config(env=env, path=path)
    if pid in (cur.get("linked_providers") or []):
        return False, f"{pid} 는 이미 linked 입니다."
    ok, msg = _persist(ops.link_provider(cur, pid), env, path)
    return (True, f"{pid} linked ({_live_word(pid)}).") if ok else (False, msg)


def apply_unlink(pid: str, *, env=None, path=None) -> Tuple[bool, str]:
    """`/provider unlink <id>` — remove from linked (primary 는 안전하게 거부)."""

    pid = (pid or "").strip()
    cur = ops.load_raw_config(env=env, path=path)
    parsed = pc.load_provider_config(cur)
    if pid == parsed.primary_provider:
        return False, f"{pid} 는 primary 입니다 — `/provider set <other>` 로 primary 변경 후 unlink 하세요."
    if pid not in parsed.linked_providers:
        return False, f"{pid} 는 linked 가 아닙니다."
    ok, msg = _persist(ops.unlink_provider(cur, pid), env, path)
    return (True, f"{pid} unlinked (해당 slot route 도 정리).") if ok else (False, msg)


def apply_set_budget(pid: str, raw_limit: str, *, env: Optional[Mapping[str, str]] = None,
                     path: Optional[Path] = None) -> Tuple[bool, str]:
    """`/provider budget <id> <limit>` — set (or clear) a provider's daily token limit + persist.

    Honest: a non-positive limit clears the cap (→ unbounded, never invents one). The id must
    be a built-in provider; a non-integer limit is a hard error (no silent 0)."""

    pid = (pid or "").strip()
    if not pid:
        return False, "provider id 가 필요합니다 — `/provider budget <id> <limit>`."
    if not builtins.is_builtin(pid):
        return False, (f"알 수 없는 provider: {pid} (built-in: "
                       f"{', '.join(builtins.BUILTIN_PROVIDERS)}). custom 은 config 로 추가하세요.")
    raw_limit = (raw_limit or "").strip()
    if not raw_limit:
        return False, "limit(일일 토큰 한도, 정수)이 필요합니다 — `/provider budget <id> <limit>` (0 이하 = 해제)."
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return False, f"limit 는 정수여야 합니다 (받은 값: {raw_limit!r}). 예: `/provider budget {pid} 50000`."
    cur = ops.load_raw_config(env=env, path=path)
    new_cfg = ops.set_provider_budget(cur, pid, limit)
    ok, where = _persist(new_cfg, env, path)
    if not ok:
        return False, where
    if limit > 0:
        return True, f"per-provider budget {pid} = {limit}tok/day 저장됨 (초과 시 routing 이 정직하게 fallback/거부). `/provider budget show` 로 확인."
    return True, f"per-provider budget {pid} 해제됨 (unbounded — global budget 만 적용). `/provider budget show` 로 확인."


def budget_lines(cfg: Optional[Mapping], rows: Sequence[Mapping] = ()) -> Tuple[str, ...]:
    """`/provider budget show` — per-provider 한도 + 오늘 spent/over (정직).

    *rows* 는 오늘로 day-scope 된 usage ledger 행. 설정된 한도가 없으면 전 provider unbounded
    (global budget 만 적용)임을 솔직히 표시 — 가짜 숫자를 만들지 않는다."""

    states = _pb.provider_budget_states(cfg, rows)
    if not states:
        return (
            "per-provider budget: 미설정",
            "  설정된 provider 한도 없음 — 전 provider unbounded (global budget 만 적용).",
            "  설정: `/provider budget <id> <limit>` (예: gemini 50000). 해제: `/provider budget <id> 0`.",
        )
    lines = ["per-provider budget (오늘 기준):"]
    for st in states:
        flag = " ⚠ OVER" if st.over else ""
        pct = f" ({st.ratio * 100:.0f}%)" if st.limit > 0 else ""
        lines.append(f"  {st.provider:<10} {st.spent}/{st.limit}tok{pct}{flag}")
    lines.append("  초과 provider 는 routing 이 정직하게 fallback/거부합니다 (가짜 전송 없음).")
    lines.append("  한도 없는 provider 는 표시되지 않음 = unbounded (global budget 만 적용).")
    return tuple(lines)


def _slot_route_line(parsed, slot: str) -> str:
    """One honest `declared → actual` line for *slot*, resolving the EXPLICIT fallback.

    The point of this surface: a non-chat work slot often DECLARES a CLI brain
    (codex/claude = routing-only) yet has an explicit fallback to a live transport
    (gemini/ollama). Showing only the declared target made those slots look broken
    (`execution → codex (unsupported_in_console)`); resolving them shows the ACTUAL
    live provider the fallback reaches — or an honest "no live path" when it can't."""

    res = rt.resolve_routing(parsed, slot)
    # default_chat is the ONE slot the live submit path actually drives today; mark it ●.
    is_chat = slot == pc.SLOT_DEFAULT_CHAT
    if res.status == rt.RESOLVE_FALLBACK:
        glyph = "●" if is_chat else "◐"
        return (f"  {glyph} {slot:<14} {res.declared_provider} → {res.actual_provider}"
                f"   [dim]declared routing-only → fallback live[/dim]")
    if res.is_live_capable:                      # declared provider is itself a live transport
        glyph = "●" if is_chat else "◐"
        return f"  {glyph} {slot:<14} {res.actual_provider}   [dim]live[/dim]"
    if res.status == rt.RESOLVE_UNSUPPORTED:      # declared + every fallback are routing-only
        return (f"  ○ {slot:<14} {res.declared_provider} → (live 경로 없음)"
                f"   [dim]routing-only, fallback 도 live 불가 — `/provider route set {slot} <gemini|ollama>`[/dim]")
    # no primary at all → setup-required (caller already guards, but stay honest per-line).
    return f"  ○ {slot:<14} (미설정)   [dim]{res.reason}[/dim]"


def route_show_lines(cfg: Optional[Mapping]) -> Tuple[str, ...]:
    """`/provider route show` — slot routing resolved to the ACTUAL live provider per slot.

    Each slot is resolved through :func:`routing.resolve_routing` (declared + explicit
    fallback), so the operator sees, per slot, the real live transport — not a bare
    `unsupported_in_console` on every CLI-declared work slot. Chat (``default_chat``) is the
    one slot the live submit path drives today; the rest are routing DECLARATIONS for
    autonomous non-chat work, resolved with the same honest fallback."""

    parsed = pc.load_provider_config(cfg)
    if not parsed.primary_provider:
        return (
            "slot routing: [setup-required] primary provider 미설정",
            "  `/provider set <id>` 또는 `/provider preset four-brain` 후 다시 보세요.",
        )
    lines = ["slot routing — declared brain → actual live provider (explicit fallback 반영):",
             "  [dim]default_chat(●) = 실제 live submit 경로 · 그 외(◐/○) = 자율 non-chat work routing 선언[/dim]"]
    for slot in pc.ROUTING_SLOTS:
        lines.append(_slot_route_line(parsed, slot))
    lines.append(f"  fallback: implicit_local={'on' if parsed.implicit_local_fallback else 'off (기본)'}")
    lines.append("  [dim]범례: ● live submit · ◐ routing 선언(live 도달) · ○ live 경로 없음.[/dim]")
    lines.append("  [dim]claude/codex 는 routing-only(brain participant) — 실제 전송은 fallback 의 "
                 "live transport(gemini/ollama)가 담당.[/dim]")
    return tuple(lines)


def apply_route_set(slot: str, pid: str, *, env=None, path=None) -> Tuple[bool, str]:
    """`/provider route set <slot> <provider>` — slot 을 linked provider 로 라우팅 + persist."""

    slot, pid = (slot or "").strip(), (pid or "").strip()
    if slot not in pc.ROUTING_SLOTS:
        return False, f"알 수 없는 slot: {slot} (허용: {', '.join(pc.ROUTING_SLOTS)})."
    cur = ops.load_raw_config(env=env, path=path)
    if pid not in pc.load_provider_config(cur).linked_providers:
        return False, f"{pid} 는 linked 가 아닙니다 — 먼저 `/provider link {pid}`."
    ok, msg = _persist(ops.route_slot(cur, slot, pid), env, path)
    return (True, f"route {slot} → {pid} 저장됨.") if ok else (False, msg)


def apply_route_clear(slot: str, *, env=None, path=None) -> Tuple[bool, str]:
    """`/provider route clear <slot>` — slot route 제거(→ primary 로 복귀)."""

    slot = (slot or "").strip()
    if slot not in pc.ROUTING_SLOTS:
        return False, f"알 수 없는 slot: {slot}."
    cur = ops.load_raw_config(env=env, path=path)
    routes = {s: t for s, t in (cur.get("slot_routing") or {}).items() if s != slot}
    cur = {**cur, "slot_routing": routes}
    ok, msg = _persist(cur, env, path)
    return (True, f"route {slot} 제거됨 (→ primary).") if ok else (False, msg)


__all__ = (
    "provider_status_lines", "provider_list_lines", "provider_doctor_lines", "apply_set_primary",
    "apply_preset", "apply_link", "apply_unlink", "route_show_lines", "apply_route_set",
    "apply_route_clear", "apply_set_budget", "budget_lines",
    "STATE_SETUP_REQUIRED", "STATE_CONFIGURED", "STATE_LINKED", "STATE_LIVE", "STATE_UNSUPPORTED",
    "PROVIDER_STATES", "classify_provider_state", "provider_state_map",
)
