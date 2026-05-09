"""Real classifier wiring — Round 2 of #73.

Phase 3 (foundation) wired the deterministic fast-path + Classifier
Protocol with a fake. This module adds the env-detected real
classifiers:

  * :class:`OllamaClassifier` — uses an Ollama HTTP endpoint
    (the same env contract the planning module already declares —
    ``OLLAMA_ENDPOINT`` / ``OLLAMA_MODEL`` / ``OLLAMA_TIMEOUT_SECONDS``).
  * :class:`AnthropicClassifier` — adapter contract only; live
    wiring waits for explicit operator authorization (D-73-10).
  * :class:`OpenAIClassifier` — adapter contract only; same.

The :func:`build_classifier_from_env` factory inspects the live
environment and returns the most specific classifier that has
the credentials it needs:

  1. ``CLAUDE_API_KEY`` / ``ANTHROPIC_API_KEY`` → AnthropicClassifier
     (currently disabled — adapter raises ``BlockedClassifierError``
     until the operator explicitly opts in).
  2. ``OPENAI_API_KEY`` → OpenAIClassifier (same).
  3. ``OLLAMA_ENDPOINT`` *and* ``YULE_DECISION_OLLAMA_ENABLED=true``
     → OllamaClassifier (live).
  4. None of the above → ``None`` (caller falls back to
     fast-path + clarification_needed default).

Hard rails:

  * No classifier is ever auto-enabled without an explicit env flag
    (``YULE_DECISION_<provider>_ENABLED=true``). Discovering an
    API key is *not* the same as authorising the spend.
  * Network requests carry a 20-second default timeout; failure
    falls back to ``clarification_needed`` rather than blocking
    the gateway.
  * Authorization headers / API keys never appear in logs or
    DecisionResult fields.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .router import (
    Classifier,
    DecisionRequest,
    DecisionResult,
    MODE_CLARIFICATION_NEEDED,
    MODE_DISCUSSION,
    MODE_IMPLEMENTATION_CANDIDATE,
    MODE_RESEARCH_ONLY,
    MODES,
    SOURCE_CLASSIFIER,
)


# ---------------------------------------------------------------------------
# Env contract
# ---------------------------------------------------------------------------


ENV_OLLAMA_ENABLED: str = "YULE_DECISION_OLLAMA_ENABLED"
ENV_OLLAMA_ENDPOINT: str = "OLLAMA_ENDPOINT"
ENV_OLLAMA_MODEL: str = "YULE_DECISION_OLLAMA_MODEL"
ENV_OLLAMA_TIMEOUT: str = "YULE_DECISION_OLLAMA_TIMEOUT"

ENV_ANTHROPIC_ENABLED: str = "YULE_DECISION_ANTHROPIC_ENABLED"
ENV_ANTHROPIC_API_KEY: str = "ANTHROPIC_API_KEY"
ENV_ANTHROPIC_API_KEY_ALT: str = "CLAUDE_API_KEY"

ENV_OPENAI_ENABLED: str = "YULE_DECISION_OPENAI_ENABLED"
ENV_OPENAI_API_KEY: str = "OPENAI_API_KEY"


_DEFAULT_OLLAMA_MODEL: str = "gemma3:latest"
_DEFAULT_OLLAMA_TIMEOUT: int = 20


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BlockedClassifierError(RuntimeError):
    """Raised when a classifier is wired but its provider is blocked.

    Carries ``reason`` so the gateway / audit can surface the exact
    reason a real LLM call wasn't issued.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# HTTP injection seam (so tests don't need a network)
# ---------------------------------------------------------------------------


class _DefaultHttpPoster:
    """Lazy-imports `urllib.request` to keep this module light when
    the classifier is never invoked.
    """

    def post_json(
        self,
        *,
        url: str,
        body: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: int,
    ) -> Mapping[str, Any]:
        import urllib.error
        import urllib.request

        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url=url, data=data, method="POST"
        )
        request.add_header("Content-Type", "application/json")
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise BlockedClassifierError(
                f"http_error: status={exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise BlockedClassifierError(
                f"url_error: {type(exc.reason).__name__}"
            ) from exc
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {"_raw": raw}


# ---------------------------------------------------------------------------
# Classifiers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OllamaClassifierConfig:
    endpoint: str
    model: str
    timeout_seconds: int


class OllamaClassifier:
    """LLM classifier backed by an Ollama-compatible chat endpoint.

    Returns a :class:`DecisionResult` with mode + confidence + reason
    + matched_keywords (empty for LLM source) + source = ``classifier``.
    """

    def __init__(
        self,
        config: OllamaClassifierConfig,
        *,
        http_poster: Optional[Any] = None,
    ) -> None:
        self._config = config
        self._http = http_poster or _DefaultHttpPoster()

    def classify(
        self,
        *,
        request: DecisionRequest,
        context_pack_id: Optional[str] = None,
    ) -> DecisionResult:
        prompt = _build_classification_prompt(request)
        body = {
            "model": self._config.model,
            "prompt": prompt,
            "stream": False,
        }
        try:
            response = self._http.post_json(
                url=f"{self._config.endpoint.rstrip('/')}/api/generate",
                body=body,
                headers={},
                timeout=self._config.timeout_seconds,
            )
        except BlockedClassifierError as exc:
            return DecisionResult(
                mode=MODE_CLARIFICATION_NEEDED,
                confidence=0.4,
                reason=f"ollama classifier failed ({exc.reason}); fallback to clarification",
                source=SOURCE_CLASSIFIER,
                matched_keywords=(),
                context_pack_id=context_pack_id,
                routed_at=_iso_now(),
            )
        text = str(response.get("response") or response.get("_raw") or "")
        return _parse_classifier_response(
            text=text,
            context_pack_id=context_pack_id,
            provider="ollama",
            model=self._config.model,
        )


class _BlockedAdapter:
    """Adapter that fails loudly with a structured ``blocked`` reason.

    Used for Anthropic / OpenAI until operator explicitly opts in
    via the matching ``YULE_DECISION_<provider>_ENABLED`` env flag.
    Returning ``clarification_needed`` (rather than raising) keeps
    the gateway responsive while still surfacing the blocker on
    every routed message.
    """

    def __init__(self, *, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason

    def classify(
        self,
        *,
        request: DecisionRequest,
        context_pack_id: Optional[str] = None,
    ) -> DecisionResult:
        return DecisionResult(
            mode=MODE_CLARIFICATION_NEEDED,
            confidence=0.3,
            reason=f"{self.provider} classifier blocked: {self.reason}",
            source=SOURCE_CLASSIFIER,
            matched_keywords=(),
            context_pack_id=context_pack_id,
            routed_at=_iso_now(),
        )


def AnthropicClassifier(
    *,
    api_key: str,
    model: str = "claude-sonnet-4-6",
) -> Classifier:
    """Adapter contract — currently routes to a blocked stub.

    Live wiring needs operator authorization + cost-budget review.
    Set ``YULE_DECISION_ANTHROPIC_ENABLED=true`` *and* a follow-up PR
    must replace this stub with the real ``anthropic.Anthropic`` call.
    """

    return _BlockedAdapter(
        provider="anthropic",
        reason=(
            "live anthropic classifier not yet wired — operator authorization + "
            "cost-budget review required (see ecc-foundation §4 / governance §6)"
        ),
    )


def OpenAIClassifier(
    *,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> Classifier:
    """Adapter contract — same posture as :func:`AnthropicClassifier`."""

    return _BlockedAdapter(
        provider="openai",
        reason=(
            "live openai classifier not yet wired — operator authorization + "
            "cost-budget review required"
        ),
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassifierResolution:
    """Result of :func:`build_classifier_from_env` — what wired or why not."""

    classifier: Optional[Classifier]
    provider: str  # "ollama" | "anthropic" | "openai" | "none"
    blocked_reason: str = ""
    detected_keys: tuple = ()


def build_classifier_from_env(
    env: Optional[Mapping[str, str]] = None,
    *,
    http_poster: Optional[Any] = None,
) -> ClassifierResolution:
    """Detect available classifier from env + return the resolution.

    Order of preference: anthropic → openai → ollama → none.
    Each requires both the key/endpoint AND the matching
    ``YULE_DECISION_<provider>_ENABLED=true`` flag — finding a key
    alone is *not* authorization.
    """

    env_map: Mapping[str, str] = env if env is not None else os.environ
    detected: list = []

    anthropic_key = (
        env_map.get(ENV_ANTHROPIC_API_KEY) or env_map.get(ENV_ANTHROPIC_API_KEY_ALT)
    )
    if anthropic_key:
        detected.append("anthropic")
        if _is_truthy(env_map.get(ENV_ANTHROPIC_ENABLED)):
            return ClassifierResolution(
                classifier=AnthropicClassifier(api_key=str(anthropic_key)),
                provider="anthropic",
                detected_keys=tuple(detected),
            )

    openai_key = env_map.get(ENV_OPENAI_API_KEY)
    if openai_key:
        detected.append("openai")
        if _is_truthy(env_map.get(ENV_OPENAI_ENABLED)):
            return ClassifierResolution(
                classifier=OpenAIClassifier(api_key=str(openai_key)),
                provider="openai",
                detected_keys=tuple(detected),
            )

    ollama_endpoint = env_map.get(ENV_OLLAMA_ENDPOINT)
    if ollama_endpoint and _is_truthy(env_map.get(ENV_OLLAMA_ENABLED)):
        detected.append("ollama")
        config = OllamaClassifierConfig(
            endpoint=str(ollama_endpoint),
            model=str(env_map.get(ENV_OLLAMA_MODEL) or _DEFAULT_OLLAMA_MODEL),
            timeout_seconds=_safe_int(
                env_map.get(ENV_OLLAMA_TIMEOUT), _DEFAULT_OLLAMA_TIMEOUT
            ),
        )
        return ClassifierResolution(
            classifier=OllamaClassifier(config=config, http_poster=http_poster),
            provider="ollama",
            detected_keys=tuple(detected),
        )

    blocked = ""
    if "anthropic" in detected and not _is_truthy(env_map.get(ENV_ANTHROPIC_ENABLED)):
        blocked = (
            f"Anthropic key detected but {ENV_ANTHROPIC_ENABLED}=true 미설정 — "
            "운영자 명시 opt-in 필요"
        )
    elif "openai" in detected and not _is_truthy(env_map.get(ENV_OPENAI_ENABLED)):
        blocked = (
            f"OpenAI key detected but {ENV_OPENAI_ENABLED}=true 미설정 — "
            "운영자 명시 opt-in 필요"
        )
    elif ollama_endpoint:
        blocked = (
            f"Ollama endpoint detected but {ENV_OLLAMA_ENABLED}=true 미설정 — "
            "운영자 명시 opt-in 필요"
        )
    else:
        blocked = "no classifier env detected (anthropic / openai / ollama)"

    return ClassifierResolution(
        classifier=None,
        provider="none",
        blocked_reason=blocked,
        detected_keys=tuple(detected),
    )


# ---------------------------------------------------------------------------
# Prompt + parser
# ---------------------------------------------------------------------------


_PROMPT_TEMPLATE = """You are a Yule Studio engineering-agent decision router.

Classify the following user request into EXACTLY one of these modes:
- discussion           : open question, no specific role/task assigned yet
- research_only        : gather references, do not implement
- implementation_candidate : suspected coding work, run authorization gate
- clarification_needed : too ambiguous, ask the user

Respond with a JSON object on a SINGLE line, no commentary, no markdown fences:
{{"mode": "<mode>", "confidence": <float 0~1>, "reason": "<short reason>"}}

User request:
{prompt}
"""


def _build_classification_prompt(request: DecisionRequest) -> str:
    return _PROMPT_TEMPLATE.format(prompt=(request.prompt or "").strip())


def _parse_classifier_response(
    *,
    text: str,
    context_pack_id: Optional[str],
    provider: str,
    model: str,
) -> DecisionResult:
    """Best-effort parser — extracts JSON object, falls back gracefully."""

    candidate = _extract_json(text)
    if not candidate:
        return DecisionResult(
            mode=MODE_CLARIFICATION_NEEDED,
            confidence=0.4,
            reason=(
                f"{provider} classifier returned non-JSON; defaulting to clarification "
                f"(model={model})"
            ),
            source=SOURCE_CLASSIFIER,
            matched_keywords=(),
            context_pack_id=context_pack_id,
            routed_at=_iso_now(),
        )
    mode = str(candidate.get("mode") or "").strip().lower()
    if mode not in MODES:
        return DecisionResult(
            mode=MODE_CLARIFICATION_NEEDED,
            confidence=0.4,
            reason=(
                f"{provider} classifier returned unknown mode {mode!r}; "
                "defaulting to clarification"
            ),
            source=SOURCE_CLASSIFIER,
            matched_keywords=(),
            context_pack_id=context_pack_id,
            routed_at=_iso_now(),
        )
    try:
        confidence = float(candidate.get("confidence") or 0.7)
    except (TypeError, ValueError):
        confidence = 0.7
    confidence = max(0.0, min(1.0, confidence))
    reason = (
        str(candidate.get("reason") or "")[:280]
        or f"{provider} classifier verdict ({model})"
    )
    return DecisionResult(
        mode=mode,
        confidence=confidence,
        reason=reason,
        source=SOURCE_CLASSIFIER,
        matched_keywords=(),
        context_pack_id=context_pack_id,
        routed_at=_iso_now(),
    )


_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}")


def _extract_json(text: str) -> Optional[Mapping[str, Any]]:
    if not text:
        return None
    # Try direct parse first.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, Mapping):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    # Then scan for the first {...} candidate.
    for match in _JSON_OBJECT_RE.findall(text):
        try:
            parsed = json.loads(match)
            if isinstance(parsed, Mapping):
                return parsed
        except (json.JSONDecodeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _is_truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


__all__ = (
    "AnthropicClassifier",
    "BlockedClassifierError",
    "ClassifierResolution",
    "ENV_ANTHROPIC_ENABLED",
    "ENV_ANTHROPIC_API_KEY",
    "ENV_ANTHROPIC_API_KEY_ALT",
    "ENV_OLLAMA_ENABLED",
    "ENV_OLLAMA_ENDPOINT",
    "ENV_OLLAMA_MODEL",
    "ENV_OLLAMA_TIMEOUT",
    "ENV_OPENAI_API_KEY",
    "ENV_OPENAI_ENABLED",
    "OllamaClassifier",
    "OllamaClassifierConfig",
    "OpenAIClassifier",
    "build_classifier_from_env",
)
