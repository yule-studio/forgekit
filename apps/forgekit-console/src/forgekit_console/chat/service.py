"""Submit service — resolve a provider and submit free text. One live path: ollama.

The seam, top→bottom: TUI free-text → :meth:`SubmitService.submit` → provider
resolution (config or zero-config local ollama) → a submit adapter keyed by the
provider's ``submit_compat`` → :class:`SubmitResult`. Exactly ONE adapter is truly
live in this work tree — **openai-compatible HTTP**, which a local **ollama**
(``auth_kind=none``) satisfies with zero config. Every other provider/compat is
reported honestly (auth missing / unsupported in console / unreachable), never a
"works-like" stub.

Transport is injected (:class:`Transport`) so the resolution + branching logic is
unit-testable with a fake — and the real :class:`DefaultTransport` uses only stdlib
``urllib`` (no new dependency).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Protocol, Tuple

from ..providers import builtins
from ..providers.contract import (
    AUTH_API_KEY,
    AUTH_NONE,
    SUBMIT_OPENAI,
    ProviderSpec,
)
from ..providers.registry import build_provider
from ..runtime_paths import config_path
from . import models as m


class Transport(Protocol):
    """Pluggable IO — real (urllib) in production, fake in tests."""

    def openai_chat(self, *, endpoint: str, model: str, prompt: str, api_key: str = "") -> "m.ChatResult":
        """POST an openai-compatible chat completion; return a :class:`ChatResult`
        (assistant text + native ``usage`` when the provider reports it). Raises on
        failure. A transport MAY still return a bare ``str`` (legacy/fake) — the
        service normalises that to text-only (usage_basis=estimate)."""

    def ollama_reachable(self, endpoint: str) -> bool:
        """True if a local ollama (or compatible) server answers at *endpoint*."""

    def ollama_models(self, endpoint: str) -> Tuple[str, ...]:
        """Model names the server advertises (best-effort; empty on failure)."""


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Honest heuristic — usage_basis=estimate."""

    return max(1, len((text or "")) // 4)


def load_config(env: Optional[Mapping[str, str]] = None) -> dict:
    """Read ``~/.forgekit/config.json`` (or ``$FORGEKIT_HOME/config.json``). {} if absent."""

    try:
        raw = config_path(env).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@dataclass
class SubmitService:
    """Resolve a provider and submit free text → one honest :class:`SubmitResult`."""

    transport: Transport
    env: Optional[Mapping[str, str]] = None
    config: Optional[dict] = None

    def __post_init__(self) -> None:
        if self.config is None:
            self.config = load_config(self.env)

    # --- resolution ---------------------------------------------------------
    def resolve(self, *, prefer_provider: str = "") -> Tuple[Optional[ProviderSpec], str]:
        """Return ``(spec, source)``. Configured provider, else zero-config local ollama.

        ``prefer_provider`` (the runtime mode's routing target, WT-auto) selects that
        provider when it is a configured/builtin option — so a mode's routing actually
        steers the live submit, not just the display. Unknown preference → ignored.
        """

        from ..policy.provider_config import load_provider_config

        prefer = (prefer_provider or "").strip()
        if prefer and builtins.is_builtin(prefer):
            return builtins.BUILTIN_PROVIDERS[prefer], m.SOURCE_CONFIGURED
        brain = load_provider_config(self.config)
        main = brain.primary_provider
        if main:
            if builtins.is_builtin(main):
                return builtins.BUILTIN_PROVIDERS[main], m.SOURCE_CONFIGURED
            try:  # a custom/enterprise provider config
                spec = build_provider({**(self.config or {}), "id": main})
                return spec, m.SOURCE_CONFIGURED
            except Exception:  # noqa: BLE001 - invalid config → treat as unconfigured
                pass
        # NO implicit local fallback by default: a reachable ollama is NOT "configured".
        # forgekit uses it only when the operator EXPLICITLY opts in
        # (fallback_policy.implicit_local_fallback = true). Otherwise → setup required.
        if brain.implicit_local_fallback and self.transport.ollama_reachable(builtins.OLLAMA.endpoint):
            return builtins.OLLAMA, m.SOURCE_LOCAL_DEFAULT
        return None, m.SOURCE_NONE

    # --- submit -------------------------------------------------------------
    def submit(self, prompt: str, *, prefer_provider: str = "", context=None) -> m.SubmitResult:
        # WT1 runtime-teeth: enforce the EffectivePolicy BEFORE touching a provider.
        runtime_mode = getattr(context, "runtime_mode", "") if context else ""
        if context is not None:
            from .policy_gate import GATE_HOLD, GATE_THROTTLE, evaluate_gate

            gate = evaluate_gate(context)
            if gate.action == GATE_HOLD:
                return m.SubmitResult(
                    ok=False, mode=m.MODE_HELD, category=m.CAT_POLICY_HELD,
                    runtime_mode=runtime_mode, text=gate.reason, next_action=gate.next_action,
                )
            if gate.action == GATE_THROTTLE:
                return m.SubmitResult(
                    ok=False, mode=m.MODE_HELD, category=m.CAT_BUDGET_THROTTLED,
                    runtime_mode=runtime_mode, throttled=True,
                    text=gate.reason, next_action=gate.next_action,
                )
            # the mode's routing target steers the provider (gate wins over the arg).
            prefer_provider = gate.routing_target or prefer_provider

        spec, source = self.resolve(prefer_provider=prefer_provider)
        if spec is None:
            return m.SubmitResult(
                ok=False, mode=m.MODE_SETUP, category=m.CAT_NO_PROVIDER, source=source,
                runtime_mode=runtime_mode,
                text="primary provider 가 아직 설정되지 않았습니다 — free-text 를 보낼 대상이 없습니다.",
                next_action="콘솔에서 `/provider set <id>` (claude/codex/gemini/ollama) 로 primary provider 를 "
                            "정하세요. ForgeKit 은 자동으로 ollama 를 쓰지 않습니다 — operator 가 정합니다.",
            )
        if spec.submit_compat == SUBMIT_OPENAI:
            return self._submit_openai(prompt, spec, source, runtime_mode=runtime_mode)
        # CLI / native / custom-http → honestly not wired for console live-submit.
        return m.SubmitResult(
            ok=False, mode=m.MODE_ERROR, category=m.CAT_UNSUPPORTED, source=source,
            provider_id=spec.id, provider_label=spec.label, runtime_mode=runtime_mode,
            text=f"{spec.label} 는 콘솔 live-submit 이 아직 구현되지 않았습니다 "
                 f"(submit_compat={spec.submit_compat}).",
            next_action="로컬 ollama 또는 openai-compatible provider 를 설정하면 free-text 가 live 로 동작합니다.",
        )

    def _submit_openai(self, prompt: str, spec: ProviderSpec, source: str,
                       *, runtime_mode: str = "") -> m.SubmitResult:
        # auth is the first precondition the operator must satisfy — check it before
        # the endpoint so an api-key provider reports the actionable "auth_missing".
        api_key = ""
        if spec.auth_kind == AUTH_API_KEY:
            env = os.environ if self.env is None else self.env
            key_name = f"{spec.id.upper()}_API_KEY"
            api_key = str(env.get(key_name, "") or "").strip()
            if not api_key:
                return m.SubmitResult(
                    ok=False, mode=m.MODE_ERROR, category=m.CAT_AUTH_MISSING, source=source,
                    provider_id=spec.id, provider_label=spec.label,
                    text=f"{spec.label} 는 API 키가 필요합니다 ({key_name} 미설정).",
                    next_action=f"환경변수 {key_name} 를 설정한 뒤 다시 시도하세요.",
                )
        if not spec.endpoint:
            return m.SubmitResult(
                ok=False, mode=m.MODE_ERROR, category=m.CAT_TRANSPORT, source=source,
                provider_id=spec.id, provider_label=spec.label,
                text=f"{spec.label} endpoint 가 비어 있습니다.",
                next_action="provider config 의 endpoint 를 설정하세요.",
            )
        model = str((self.config or {}).get("model", "")).strip()
        if not model and spec.auth_kind == AUTH_NONE:  # ollama: pick an installed model
            models = self.transport.ollama_models(spec.endpoint)
            model = models[0] if models else ""
        if not model:
            model = spec.id  # last-ditch; the server will reject if unknown
        try:
            reply = self.transport.openai_chat(
                endpoint=spec.endpoint, model=model, prompt=prompt, api_key=api_key
            )
        except Exception as exc:  # noqa: BLE001 - any transport failure → honest error
            cat = m.CAT_UNREACHABLE if spec.auth_kind == AUTH_NONE else m.CAT_TRANSPORT
            return m.SubmitResult(
                ok=False, mode=m.MODE_ERROR, category=cat, source=source,
                provider_id=spec.id, provider_label=spec.label, model=model,
                runtime_mode=runtime_mode,
                text=f"{spec.label} 요청 실패: {type(exc).__name__}: {exc}",
                next_action="endpoint/health 를 `/render` 또는 `/doctor` 로 확인하세요.",
            )
        # normalise: a transport may return a ChatResult (text + native usage) or a
        # bare str (legacy/fake → text only, no native usage).
        if isinstance(reply, m.ChatResult):
            text, usage = reply.text, reply.usage
        else:
            text, usage = reply, None
        text = (text or "").strip() or "(빈 응답)"
        # WT1 #239: prefer the provider's NATIVE usage when present; otherwise degrade
        # to an honest length estimate. live and estimate are never mixed in one row.
        if usage is not None and usage.usable:
            return m.SubmitResult(
                ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, source=source,
                provider_id=spec.id, provider_label=spec.label, model=model,
                runtime_mode=runtime_mode, usage_basis=m.USAGE_LIVE,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens, text=text,
            )
        in_tok = _estimate_tokens(prompt)
        out_tok = _estimate_tokens(text)
        return m.SubmitResult(
            ok=True, mode=m.MODE_LIVE, category=m.CAT_OK, source=source,
            provider_id=spec.id, provider_label=spec.label, model=model,
            runtime_mode=runtime_mode, usage_basis=m.USAGE_ESTIMATE,
            input_tokens=in_tok, output_tokens=out_tok, total_tokens=in_tok + out_tok,
            text=text,
        )


@dataclass
class DefaultTransport:
    """Real transport — stdlib ``urllib`` only (no new dependency)."""

    timeout: float = 60.0
    probe_timeout: float = 2.0

    def openai_chat(self, *, endpoint: str, model: str, prompt: str, api_key: str = "") -> m.ChatResult:
        import urllib.request

        from .usage_parse import parse_openai_usage

        url = endpoint.rstrip("/") + "/v1/chat/completions"
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        # WT1 #239: ollama's /v1/chat/completions (and OpenAI/gemini compat) return a
        # native usage block on the SAME response — parse it so usage_basis=live.
        return m.ChatResult(text=text, usage=parse_openai_usage(data))

    def ollama_reachable(self, endpoint: str) -> bool:
        import urllib.request

        try:
            req = urllib.request.Request(endpoint.rstrip("/") + "/api/tags")
            with urllib.request.urlopen(req, timeout=self.probe_timeout) as resp:
                return 200 <= resp.status < 300
        except Exception:  # noqa: BLE001
            return False

    def ollama_models(self, endpoint: str) -> Tuple[str, ...]:
        import urllib.request

        try:
            req = urllib.request.Request(endpoint.rstrip("/") + "/api/tags")
            with urllib.request.urlopen(req, timeout=self.probe_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return tuple(str(mm.get("name", "")) for mm in data.get("models", []) if mm.get("name"))
        except Exception:  # noqa: BLE001
            return ()


def build_default_service(env: Optional[Mapping[str, str]] = None) -> SubmitService:
    """The production submit service (real transport + config from disk)."""

    return SubmitService(transport=DefaultTransport(), env=env)


__all__ = (
    "Transport",
    "SubmitService",
    "DefaultTransport",
    "load_config",
    "build_default_service",
)
