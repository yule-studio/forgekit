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
from ..providers.registry import no_provider_configured
from .main_profile import MainProviderProfile, profile_for

STATE_READY = "ready"
STATE_SETUP_REQUIRED = "setup-required"
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
    main_provider: str = ""
    profile: Optional[MainProviderProfile] = None
    checks: Tuple[SetupCheck, ...] = field(default_factory=tuple)
    next_actions: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        return self.state == STATE_SETUP_REQUIRED

    @property
    def ready(self) -> bool:
        return self.state == STATE_READY


def _main_provider_id(config: Mapping) -> str:
    return str((config or {}).get("main_provider", "") or (config or {}).get("id", "")).strip()


def resolve_setup_state(config: Optional[Mapping] = None) -> SetupState:
    """Resolve the setup verdict from *config* (the on-disk ``~/.forgekit/config.json``).

    No provider → ``setup-required`` (blocked) with a clear next action. A provider
    present → ``ready`` with the derived profile, plus a checklist that is honest
    about which items are verified vs derived.
    """

    config = config or {}
    if no_provider_configured(config):
        return SetupState(
            state=STATE_SETUP_REQUIRED,
            checks=(
                SetupCheck("main provider", CHECK_MISSING, "설정된 provider 가 없습니다"),
                SetupCheck("auth/endpoint", CHECK_UNKNOWN, "provider 설정 후 확인"),
                SetupCheck("budget/usage policy", CHECK_UNKNOWN, "provider 후 derive"),
                SetupCheck("approval policy", CHECK_UNKNOWN, "provider 후 derive"),
                SetupCheck("brain/vault path", CHECK_UNKNOWN, "provider 후 확인"),
            ),
            next_actions=(
                "primary provider 를 정하세요 — 콘솔에서 `/provider set <id>` "
                "(claude/codex/gemini/ollama) 또는 `~/.forgekit/config.json` 의 `primary_provider`. "
                "ForgeKit 은 자동으로 ollama 를 쓰지 않습니다(operator 주도).",
                "설정 후 `/provider` / `/doctor` 로 점검, `/mode` 로 routing 확인.",
            ),
        )

    main = _main_provider_id(config)
    spec = builtins.builtin(main)
    profile = profile_for(main, spec)
    # auth/endpoint: we know the provider's auth KIND from the spec; whether the
    # secret is actually present is checked at submit time (chat.service), so we
    # report the requirement honestly rather than claiming it's satisfied.
    if spec is not None:
        auth_detail = f"auth_kind={spec.auth_kind}" + (f" · endpoint={spec.endpoint}" if spec.endpoint else "")
        auth_check = SetupCheck("auth/endpoint", CHECK_DERIVED, auth_detail)
    else:
        auth_check = SetupCheck("auth/endpoint", CHECK_DERIVED, "custom provider — config 의 auth/endpoint 사용")
    checks = (
        SetupCheck("main provider", CHECK_OK, main),
        auth_check,
        SetupCheck("budget/usage policy", CHECK_DERIVED, f"profile usage={profile.default_usage_mode}"),
        SetupCheck("approval policy", CHECK_DERIVED, f"policy={profile.default_policy_mode}"),
        SetupCheck("brain/vault path", CHECK_DERIVED, "runtime_paths 기본 경로"),
    )
    next_actions: Tuple[str, ...] = ()
    if profile.warnings:
        next_actions = profile.warnings
    return SetupState(
        state=STATE_READY,
        main_provider=main,
        profile=profile,
        checks=checks,
        next_actions=next_actions,
    )


__all__ = (
    "STATE_READY", "STATE_SETUP_REQUIRED", "STATE_DEGRADED",
    "CHECK_OK", "CHECK_MISSING", "CHECK_DERIVED", "CHECK_UNKNOWN",
    "SetupCheck", "SetupState", "resolve_setup_state",
)
