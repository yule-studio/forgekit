"""Setup wizard — the provider-onboarding control-plane flow (gh-auth-like).

The wizard is a *staged* flow expressed as a command surface (not interactive prompts):
``assess`` diagnoses every candidate provider (connect checks), proposes the recommended
preset, and reports an honest readiness verdict; ``apply`` writes the recommended preset to
the canonical ``~/.forgekit/config.json`` and re-diagnoses (verify). It NEVER fakes a
connection — a provider only counts as a live lane if its probe actually verified it.

This is the onboarding layer on top of the provider policy/routing core
(``forgekit-provider``); it persists through ``provider_ops`` (the single config writer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

from forgekit_provider.policy import provider_config as pc
from forgekit_provider.policy import provider_ops as ops

from . import diagnose, status as st
from .probe import ConnectionProbe, DefaultProbe

# The recommended onboarding default — a real preset writer in provider_ops, not prose.
RECOMMENDED_PRESET = "four-brain"


@dataclass(frozen=True)
class BootstrapStatus:
    """The honest onboarding verdict: per-provider connection + recommended next step."""

    statuses: Tuple[st.ConnectionStatus, ...]
    primary_provider: str = ""
    recommended_preset: str = RECOMMENDED_PRESET
    live_lane: Tuple[str, ...] = field(default_factory=tuple)   # providers verified live now

    @property
    def ready(self) -> bool:
        """Ready = at least one provider can actually carry a console live-submit."""
        return bool(self.live_lane)

    @property
    def verdict(self) -> str:
        return "ready" if self.ready else "setup-required"

    def status_for(self, pid: str) -> Optional[st.ConnectionStatus]:
        return next((s for s in self.statuses if s.provider_id == pid), None)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict, "primary_provider": self.primary_provider,
            "recommended_preset": self.recommended_preset, "live_lane": list(self.live_lane),
            "statuses": [s.to_dict() for s in self.statuses],
        }


def assess(config: Optional[Mapping] = None, *, probe: Optional[ConnectionProbe] = None,
           env: Optional[Mapping[str, str]] = None) -> BootstrapStatus:
    """Diagnose every candidate provider and produce the onboarding verdict."""

    probe = probe or DefaultProbe()
    statuses = diagnose.diagnose_all(config, probe=probe, env=env)
    parsed = pc.load_provider_config(config or {})
    live = tuple(s.provider_id for s in statuses if s.live_capable)
    return BootstrapStatus(
        statuses=statuses,
        primary_provider=parsed.primary_provider,
        recommended_preset=RECOMMENDED_PRESET,
        live_lane=live,
    )


def apply_recommended(*, env: Optional[Mapping[str, str]] = None, path: Optional[Path] = None,
                      probe: Optional[ConnectionProbe] = None,
                      preset: str = RECOMMENDED_PRESET) -> Tuple[bool, str, Optional[BootstrapStatus]]:
    """Apply the recommended preset to the canonical config (save), then re-diagnose (verify).

    Returns ``(ok, message, post_status)``. Honest: the message reports the ACTUAL live lane
    after writing — it does not claim claude/codex became live.
    """

    name = (preset or RECOMMENDED_PRESET).strip()
    builder = ops.PRESETS.get(name)
    if builder is None:
        return False, f"알 수 없는 preset: {name} (사용 가능: {', '.join(ops.PRESETS)}).", None
    cur = ops.load_raw_config(env=env, path=path)
    new_cfg = builder(cur)
    ok, where = ops.persist_config(new_cfg, env=env, path=path)
    if not ok:
        return False, f"저장 실패: {where}", None
    post = assess(new_cfg, probe=probe, env=env)   # VERIFY against the just-written config
    live = ", ".join(post.live_lane) or "(없음 — gemini API 키/ollama 데몬 확인 필요)"
    parsed = pc.load_provider_config(new_cfg)
    msg = (
        f"setup 적용 — primary brain = {parsed.primary_provider}, "
        f"default_chat actual live = {parsed.slot_target(pc.SLOT_DEFAULT_CHAT)}.\n"
        f"  실제 live lane(검증됨): {live}. claude/codex 는 routing/brain participant.\n"
        f"  verdict: {post.verdict}. `/provider` 로 declared→actual, `/setup` 로 연결 재점검."
    )
    return True, msg, post


__all__ = ("RECOMMENDED_PRESET", "BootstrapStatus", "assess", "apply_recommended")
