"""Setup gate — is forgekit ready to run, or does it need an operator to finish setup?

The product rule (docs/forgekit-provider-policy.md): forgekit needs **at least one
main provider** before it can do real work. This module is the single honest answer
to "are we set up?", composed from facts we can actually check (the on-disk config)
— never a fake "ready".

It is deliberately conservative: the provider gate is a HARD check (no provider →
``setup-required`` / blocked). The remaining setup items (auth/endpoint, budget,
approval, brain/vault path) are reported as a checklist whose state is *derived*
from the provider profile once a provider exists; items we cannot verify here are
marked ``derived``/``unknown`` rather than ticked green. Pure given a config dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

from ..providers import builtins
from .main_profile import MainProviderProfile, profile_for
from . import provider_config as pc
from . import routing as rt

STATE_READY = "ready"
STATE_SETUP_REQUIRED = "setup-required"
STATE_NO_LIVE = "configured-no-live"   # primary set, but no live-submit-capable linked provider
STATE_DEGRADED = "degraded"

CHECK_OK = "ok"
CHECK_MISSING = "missing"
CHECK_DERIVED = "derived"   # satisfied by a derived default (not independently verified)
CHECK_UNKNOWN = "unknown"


@dataclass(frozen=True)
class SetupCheck:
    name: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (CHECK_OK, CHECK_DERIVED)


@dataclass(frozen=True)
class SetupState:
    """The honest setup verdict + per-item checklist + operator next actions."""

    state: str
    primary_provider: str = ""
    profile: Optional[MainProviderProfile] = None
    checks: Tuple[SetupCheck, ...] = field(default_factory=tuple)
    next_actions: Tuple[str, ...] = field(default_factory=tuple)
    live_capable: bool = False

    # back-compat alias (legacy readers); canonical is primary_provider.
    @property
    def main_provider(self) -> str:
        return self.primary_provider

    @property
    def blocked(self) -> bool:
        # ONLY no-primary is a hard block. "configured-no-live" is configured (not blocked) —
        # it is an honest capability state, not "설정 안 됨".
        return self.state == STATE_SETUP_REQUIRED

    @property
    def ready(self) -> bool:
        return self.state == STATE_READY


def _has_live_capable(cfg: pc.ProviderConfig) -> bool:
    """True when at least one linked provider can do console live-submit (openai-compat).

    claude/codex (CLI) are routable but NOT live in the console; a brain of only those
    is `configured-no-live`, not ready. Custom providers can't be verified here → treated
    as potentially live (their submit is checked at call time)."""

    for pid in (cfg.primary_provider, *cfg.linked_providers):
        spec = builtins.builtin(pid)
        if spec is None:
            return True   # custom/unknown built-in → can't disprove; honest at submit time
        if rt.submit_supported(spec):
            return True
    return False


def resolve_setup_state(config: Optional[Mapping] = None) -> SetupState:
    """Resolve the setup verdict from *config* (the on-disk ``~/.forgekit/config.json``).

    Reads the CANONICAL ``primary_provider`` (via :func:`provider_config.load_provider_config`,
    which also migrates a legacy ``main_provider``/``id``). No primary → ``setup-required``
    (blocked). Primary set but NO live-submit-capable linked provider → ``configured-no-live``
    (configured, NOT blocked — honest). Primary + a live path → ``ready``.
    """

    config = config or {}
    brain = pc.load_provider_config(config)
    primary = brain.primary_provider
    if not primary:
        return SetupState(
            state=STATE_SETUP_REQUIRED,
            checks=(
                SetupCheck("primary provider", CHECK_MISSING, "설정된 provider 가 없습니다"),
                SetupCheck("auth/endpoint", CHECK_UNKNOWN, "provider 설정 후 확인"),
                SetupCheck("budget/usage policy", CHECK_UNKNOWN, "provider 후 derive"),
                SetupCheck("approval policy", CHECK_UNKNOWN, "provider 후 derive"),
                SetupCheck("brain/vault path", CHECK_UNKNOWN, "provider 후 확인"),
            ),
            next_actions=(
                "primary provider 를 정하세요 — 콘솔에서 `/provider set <id>` "
                "(claude/codex/gemini/ollama) 또는 `/provider preset <claude|codex|gemini|ollama>-brain`. "
                "ForgeKit 은 자동으로 ollama 를 쓰지 않습니다(operator 가 primary_provider 를 정합니다).",
                "설정 후 `/provider` / `/doctor` 로 점검, `/mode` 로 routing 확인.",
            ),
        )

    main = primary
    spec = builtins.builtin(main)
    profile = profile_for(main, spec)
    live = _has_live_capable(brain)
    # auth/endpoint: we know the provider's auth KIND from the spec; whether the
    # secret is actually present is checked at submit time (chat.service), so we
    # report the requirement honestly rather than claiming it's satisfied.
    if spec is not None:
        auth_detail = f"auth_kind={spec.auth_kind}" + (f" · endpoint={spec.endpoint}" if spec.endpoint else "")
        auth_check = SetupCheck("auth/endpoint", CHECK_DERIVED, auth_detail)
    else:
        auth_check = SetupCheck("auth/endpoint", CHECK_DERIVED, "custom provider — config 의 auth/endpoint 사용")
    live_check = (SetupCheck("live submit", CHECK_OK, "live-capable linked provider 있음") if live
                  else SetupCheck("live submit", CHECK_MISSING,
                                  "linked provider 가 전부 unsupported_in_console (claude/codex) — "
                                  "console live-submit 불가"))
    checks = (
        SetupCheck("primary provider", CHECK_OK, main),
        auth_check,
        live_check,
        SetupCheck("budget/usage policy", CHECK_DERIVED, f"profile usage={profile.default_usage_mode}"),
        SetupCheck("approval policy", CHECK_DERIVED, f"policy={profile.default_policy_mode}"),
        SetupCheck("brain/vault path", CHECK_DERIVED, "runtime_paths 기본 경로"),
    )
    next_actions: Tuple[str, ...] = tuple(profile.warnings or ())
    if not live:
        next_actions = (
            "primary 는 설정됐지만 console live-submit 가능한 provider 가 없습니다 "
            "(claude/codex 는 routable 이나 미구현). `/provider link gemini` 또는 "
            "`/provider link ollama` 로 live 경로를 추가하세요.",
            *next_actions,
        )
    return SetupState(
        state=STATE_READY if live else STATE_NO_LIVE,
        primary_provider=main,
        profile=profile,
        checks=checks,
        next_actions=next_actions,
        live_capable=live,
    )


__all__ = (
    "STATE_READY", "STATE_SETUP_REQUIRED", "STATE_NO_LIVE", "STATE_DEGRADED",
    "CHECK_OK", "CHECK_MISSING", "CHECK_DERIVED", "CHECK_UNKNOWN",
    "SetupCheck", "SetupState", "resolve_setup_state",
)
