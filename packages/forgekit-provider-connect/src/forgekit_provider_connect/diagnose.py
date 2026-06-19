"""Diagnosis — turn probe facts into an honest :class:`status.ConnectionStatus`.

Routes by provider *type* (the spec's ``submit_compat`` / ``auth_kind``), so the three
real connection shapes are separated, never conflated:

* **CLI** (claude / codex) — detect an existing CLI login and *attach*. ForgeKit does NOT
  mint a new vendor OAuth. A connected CLI brain is a routing/brain participant but
  ``unsupported_in_console`` for live-submit (honest: ``live_capable=False``).
* **API key** (gemini) — the key is the connection; present ⇒ live, absent ⇒ ``missing_key``.
* **local daemon** (ollama) — daemon up + a model installed (and the selected override, if
  any, present) ⇒ live; else ``daemon_down`` / ``model_missing``.

Pure given a probe. No IO here (the probe owns IO), so it is fully unit-testable.
"""

from __future__ import annotations

from typing import Mapping, Optional

from forgekit_provider.policy import provider_config as pc
from forgekit_provider.providers import builtins
from forgekit_provider.providers.contract import AUTH_API_KEY, AUTH_NONE, SUBMIT_CLI, SUBMIT_OPENAI

from . import status as st
from .probe import ConnectionProbe, DefaultProbe


def diagnose_provider(
    provider_id: str,
    config: Optional[Mapping] = None,
    *,
    probe: Optional[ConnectionProbe] = None,
    env: Optional[Mapping[str, str]] = None,
) -> st.ConnectionStatus:
    """The honest connection verdict for *provider_id* (given the on-disk config)."""

    pid = (provider_id or "").strip()
    probe = probe or DefaultProbe()
    spec = builtins.builtin(pid)
    if spec is None:
        return st.ConnectionStatus(
            pid, st.STATE_UNKNOWN, st.TRANSPORT_NONE,
            detail="알 수 없는 provider (custom 은 config 의 auth/endpoint 로 검사)",
            next_action=f"built-in: {', '.join(builtins.BUILTIN_PROVIDERS)}",
        )

    # --- CLI providers (claude / codex): attach an existing login, never mint OAuth ---
    if spec.submit_compat == SUBMIT_CLI:
        authed = probe.cli_authenticated(pid)
        if authed is True:
            return st.ConnectionStatus(
                pid, st.STATE_CONNECTED, st.TRANSPORT_CLI, live_capable=False,
                detail=f"{spec.label} CLI 세션 attach(heuristic) — routing/brain participant",
                next_action="콘솔 live-submit 은 미구현 — free-text 의 live lane 은 gemini/ollama",
            )
        if authed is False:
            return st.ConnectionStatus(
                pid, st.STATE_MISSING_CLI_AUTH, st.TRANSPORT_CLI, live_capable=False,
                detail=f"{spec.label} CLI 는 설치됐으나 로그인 세션 미감지",
                next_action=f"`{pid} login` (또는 해당 CLI 로그인) 후 `/provider connect {pid}`",
            )
        return st.ConnectionStatus(  # None → undetectable (CLI 부재 등)
            pid, st.STATE_MISSING_CLI_AUTH, st.TRANSPORT_CLI, live_capable=False,
            detail=f"{spec.label} CLI/세션을 감지하지 못함(미설치 가능)",
            next_action=f"{spec.label} CLI 설치+로그인 후 다시 연결 — routing participant 로만 동작",
        )

    # --- API-key openai-compat (gemini): the key IS the connection ---
    if spec.auth_kind == AUTH_API_KEY and spec.submit_compat == SUBMIT_OPENAI:
        if probe.api_key(pid, env):
            return st.ConnectionStatus(
                pid, st.STATE_CONNECTED, st.TRANSPORT_OPENAI, live_capable=True,
                detail=f"{spec.label} API 키 감지 — console live-submit 가능",
                next_action="",
            )
        return st.ConnectionStatus(
            pid, st.STATE_MISSING_KEY, st.TRANSPORT_OPENAI, live_capable=False,
            detail=f"{spec.label} API 키 미설정 ({pid.upper()}_API_KEY)",
            next_action=f"환경변수 {pid.upper()}_API_KEY 설정 후 `/provider connect {pid}`",
        )

    # --- local daemon, no auth (ollama): daemon up + model installed ---
    if spec.auth_kind == AUTH_NONE and spec.submit_compat == SUBMIT_OPENAI:
        if not probe.daemon_reachable(spec.endpoint):
            return st.ConnectionStatus(
                pid, st.STATE_DAEMON_DOWN, st.TRANSPORT_OPENAI, live_capable=False,
                detail=f"{spec.label} 데몬 미응답 ({spec.endpoint})",
                next_action="`ollama serve` 로 데몬 기동 후 `/provider connect ollama`",
            )
        models = probe.installed_models(spec.endpoint)
        if not models:
            return st.ConnectionStatus(
                pid, st.STATE_MODEL_MISSING, st.TRANSPORT_OPENAI, live_capable=False,
                detail=f"{spec.label} 데몬은 떴으나 설치된 모델 없음",
                next_action="`ollama pull <model>` 후 다시 연결",
            )
        # if the operator pinned a model_overrides[ollama], it must actually be installed.
        sel = pc.load_provider_config(config or {}).model_for(pid).strip()
        if sel and sel not in models:
            return st.ConnectionStatus(
                pid, st.STATE_MODEL_MISSING, st.TRANSPORT_OPENAI, live_capable=False,
                detail=f"선택 모델 '{sel}' 가 설치 목록에 없음 (설치: {', '.join(models[:4])})",
                next_action=f"`ollama pull {sel}` 또는 `/provider` model override 조정",
            )
        return st.ConnectionStatus(
            pid, st.STATE_CONNECTED, st.TRANSPORT_OPENAI, live_capable=True,
            detail=f"{spec.label} 데몬+모델 OK ({(sel or models[0])})",
            next_action="",
        )

    # --- anything else (enterprise/custom transports) — honest unknown ---
    return st.ConnectionStatus(
        pid, st.STATE_UNKNOWN, st.TRANSPORT_NONE,
        detail=f"{spec.label} ({spec.submit_compat}) 연결 검사 미구현",
        next_action="openai-compatible/ollama 또는 CLI provider 를 사용하세요",
    )


def diagnose_all(config: Optional[Mapping] = None, *, probe: Optional[ConnectionProbe] = None,
                 env: Optional[Mapping[str, str]] = None):
    """Diagnose every built-in provider → tuple of :class:`status.ConnectionStatus`."""

    probe = probe or DefaultProbe()
    return tuple(
        diagnose_provider(pid, config, probe=probe, env=env)
        for pid in builtins.BUILTIN_PROVIDERS
    )


__all__ = ("diagnose_provider", "diagnose_all")
