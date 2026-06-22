"""Console surface — pure line builders for the onboarding commands.

The console (`apps/forgekit-console`) renders these; all logic lives here / in the connect
core, so the UI stays a thin surface. Every line is honest about brain-vs-transport and
never green-washes a missing connection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Tuple

from forgekit_provider.policy import provider_config as pc
from forgekit_provider.policy import provider_ops as ops

from . import diagnose, status as st, wizard
from .probe import ConnectionProbe


def _status_row(s: st.ConnectionStatus) -> str:
    mark = "●" if s.connected and s.live_capable else ("◐" if s.connected else "○")
    line = f"  {mark} {s.provider_id:<7} {s.ok_word}"
    if s.detail:
        line += f"  [dim]— {s.detail}[/dim]"
    return line


def setup_status_lines(config: Optional[Mapping] = None, *, probe: Optional[ConnectionProbe] = None,
                       env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """`/setup` — the onboarding bootstrap surface: per-provider connection + recommendation."""

    b = wizard.assess(config, probe=probe, env=env)
    lines = [f"[b]/setup[/b] — provider onboarding  ·  verdict: [b]{b.verdict}[/b]"]
    if b.primary_provider:
        lines.append(f"  primary brain: {b.primary_provider}")
    lines.append("  연결 상태 (brain participant vs live transport 분리):")
    for s in b.statuses:
        lines.append(_status_row(s))
    live = ", ".join(b.live_lane) or "(없음)"
    lines.append(f"  live lane(검증됨): {live}   ·   추천 preset: [b]{b.recommended_preset}[/b]")
    if b.ready:
        lines.append("  다음: `/setup apply` 로 추천 4-provider 브레인 저장 + 검증 (또는 `/provider preset four-brain`).")
    else:
        lines.append("  다음: gemini API 키 또는 ollama 데몬을 연결한 뒤 `/setup apply` — claude/codex 만으로는 live 불가.")
    lines.append("  명령: `/setup apply` · `/provider connect <id>` · `/provider test <id>` · `/provider recommended`")
    return tuple(lines)


def recommended_lines(config: Optional[Mapping] = None, *, probe: Optional[ConnectionProbe] = None,
                      env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """`/provider recommended` — the recommended preset + per-provider readiness."""

    b = wizard.assess(config, probe=probe, env=env)
    lines = [
        f"추천 brain: [b]{b.recommended_preset}[/b]  (primary=claude · live lane=gemini/ollama · execution=codex)",
        "  역할: claude=safety/synthesis(participant) · codex=execution(participant) · "
        "gemini=default_chat/research(live) · ollama=compression/classification(live)",
        "  현재 연결:",
    ]
    for s in b.statuses:
        lines.append(_status_row(s))
    lines.append("  적용: `/setup apply` 또는 `/provider preset four-brain` (실제 config writer, persist).")
    return tuple(lines)


def test_lines(provider_id: str, config: Optional[Mapping] = None, *,
               probe: Optional[ConnectionProbe] = None,
               env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """`/provider test <id>` — diagnose one provider's connection (honest, no change)."""

    s = diagnose.diagnose_provider(provider_id, config, probe=probe, env=env)
    lines = [
        f"provider test: [b]{s.provider_id}[/b] → [b]{s.ok_word}[/b]  (transport={s.transport})",
        f"  live-capable(console live-submit): {'yes' if s.live_capable else 'no'}",
    ]
    if s.detail:
        lines.append(f"  {s.detail}")
    if s.next_action:
        lines.append(f"  다음: {s.next_action}")
    return tuple(lines)


def apply_connect(provider_id: str, *, env: Optional[Mapping[str, str]] = None,
                  path: Optional[Path] = None, probe: Optional[ConnectionProbe] = None) -> Tuple[bool, str]:
    """`/provider connect <id>` — diagnose, then link the provider + persist. Honest about
    whether it became a LIVE lane or stays a routing participant."""

    s = diagnose.diagnose_provider(provider_id, ops.load_raw_config(env=env, path=path),
                                   probe=probe, env=env)
    if s.state == st.STATE_UNKNOWN:
        return False, f"{s.provider_id}: {s.detail}. {s.next_action}"
    cur = ops.load_raw_config(env=env, path=path)
    new_cfg = ops.link_provider(cur, s.provider_id)
    ok, where = ops.persist_config(new_cfg, env=env, path=path)
    if not ok:
        return False, f"저장 실패: {where}"
    if s.connected and s.live_capable:
        return True, f"{s.provider_id} 연결됨 (live lane). linked + 저장. `/setup` 으로 확인."
    if s.connected:  # CLI attach — participant only
        return True, (f"{s.provider_id} attach 됨 (routing/brain participant, console live-submit "
                      f"미구현). linked + 저장 — live lane 은 gemini/ollama.")
    # not connected → linked 하되 정직하게 미연결 표면
    return True, (f"{s.provider_id} linked + 저장. 단 아직 미연결: {s.detail}. "
                  f"다음: {s.next_action}")


def apply_disconnect(provider_id: str, *, env: Optional[Mapping[str, str]] = None,
                     path: Optional[Path] = None) -> Tuple[bool, str]:
    """`/provider disconnect <id>` — unlink + persist (primary 는 안전하게 거부)."""

    pid = (provider_id or "").strip()
    cur = ops.load_raw_config(env=env, path=path)
    parsed = pc.load_provider_config(cur)
    if pid == parsed.primary_provider:
        return False, f"{pid} 는 primary brain 입니다 — `/provider set <other>` 후 disconnect 하세요."
    if pid not in parsed.linked_providers:
        return False, f"{pid} 는 linked 가 아닙니다."
    ok, where = ops.persist_config(ops.unlink_provider(cur, pid), env=env, path=path)
    return (True, f"{pid} disconnect (linked 에서 제거, slot route 정리) + 저장.") if ok else (False, f"저장 실패: {where}")


def apply_setup(preset: str = wizard.RECOMMENDED_PRESET, *, env: Optional[Mapping[str, str]] = None,
                path: Optional[Path] = None, probe: Optional[ConnectionProbe] = None) -> Tuple[bool, str]:
    """`/setup apply` — write the recommended preset (save) + re-diagnose (verify)."""

    ok, msg, _ = wizard.apply_recommended(env=env, path=path, probe=probe, preset=preset)
    return ok, msg


__all__ = (
    "setup_status_lines", "recommended_lines", "test_lines",
    "apply_connect", "apply_disconnect", "apply_setup",
)
