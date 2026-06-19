"""`/provider` operator surface — projection + apply over the provider_ops engine.

Read surfaces (status / list / doctor) are pure over a config dict; ``set_primary`` is the
one mutation (validate → set_primary → persist). This is the surface that makes the merged
multi-provider control-plane usable: the operator sets the **primary provider** explicitly,
so ForgeKit never looks like it "just uses Ollama" — a reachable local Ollama is only one
available provider, and with no config the honest state is setup-required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Tuple

from ..providers import builtins
from ..providers.contract import SUBMIT_OPENAI
from . import provider_config as pc
from . import provider_ops as ops
from . import routing as rt


def _live_word(pid: str) -> str:
    spec = builtins.builtin(pid)
    if spec is None:
        return "custom"
    return "live" if spec.submit_compat == SUBMIT_OPENAI else "unsupported_in_console"


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
    lines = [
        f"provider: [{review.verdict}]",
        f"  primary : {parsed.primary_provider} ({_live_word(parsed.primary_provider)})",
        f"  linked  : {', '.join(parsed.linked_providers) or '-'}",
        f"  live    : {', '.join(bmap.live_capable) or '(없음)'}",
        f"  unsupported_in_console: {', '.join(bmap.unsupported) or '-'}",
        f"  implicit local fallback: {'on' if parsed.implicit_local_fallback else 'off (기본)'}",
    ]
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


def route_show_lines(cfg: Optional[Mapping]) -> Tuple[str, ...]:
    """`/provider route show` — slot routing + fallback policy 가시화."""

    parsed = pc.load_provider_config(cfg)
    lines = ["slot routing (declared → primary if unset):"]
    for slot in pc.ROUTING_SLOTS:
        tgt = parsed.slot_target(slot)
        lines.append(f"  {slot:<14} → {tgt} ({_live_word(tgt)})")
    lines.append(f"  fallback: implicit_local={'on' if parsed.implicit_local_fallback else 'off (기본)'}")
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
    "apply_link", "apply_unlink", "route_show_lines", "apply_route_set", "apply_route_clear",
)
