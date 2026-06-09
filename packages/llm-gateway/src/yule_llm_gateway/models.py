"""Core request/response/usage dataclasses for the LLM gateway.

These are the *minimal* wire shapes a caller hands to (and gets back from)
:class:`yule_llm_gateway.client.LLMGateway`. They are deliberately small,
trackable, and JSON-friendly — standard library only — so any side of the
platform can construct them without dragging in app internals.

The shape is kept *compatible in spirit* with the existing provider call sites
(``agents.runners.*`` use ``model`` + ``temperature``; ``planning.ollama`` uses
``model`` + ``temperature`` + ``timeout``), but it does NOT replace them. See the
package README for the migration TODO list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class Message:
    """A single chat message in a multi-turn prompt.

    ``role`` is an opaque string (``"system"`` / ``"user"`` / ``"assistant"``)
    so the gateway does not couple to any provider's exact vocabulary.
    """

    role: str
    content: str

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Message":
        return cls(role=str(data["role"]), content=str(data["content"]))


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting for a single LLM call.

    ``total`` is derived from input + output when not supplied explicitly so a
    caller can pass partial data and still get a consistent number.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total: int = 0

    def __post_init__(self) -> None:
        if self.total == 0 and (self.input_tokens or self.output_tokens):
            object.__setattr__(self, "total", self.input_tokens + self.output_tokens)

    def to_dict(self) -> Dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total": self.total,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TokenUsage":
        return cls(
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            total=int(data.get("total", 0)),
        )


@dataclass(frozen=True)
class LLMRequest:
    """A single unit of work handed to the gateway.

    Either ``prompt`` (single-string) or ``messages`` (multi-turn) may be set;
    callers typically use one or the other. ``metadata`` carries opaque,
    provider-agnostic hints (task id, role, repo, ...) so the gateway contract
    does not depend on Discord / planning / GitHub schemas.
    """

    provider: str
    model: str
    prompt: Optional[str] = None
    messages: Sequence[Message] = field(default_factory=tuple)
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt": self.prompt,
            "messages": [m.to_dict() for m in self.messages],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LLMRequest":
        raw_messages = data.get("messages") or ()
        messages: List[Message] = [Message.from_dict(m) for m in raw_messages]
        return cls(
            provider=str(data["provider"]),
            model=str(data["model"]),
            prompt=data.get("prompt"),
            messages=tuple(messages),
            max_tokens=data.get("max_tokens"),
            temperature=data.get("temperature"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class LLMResponse:
    """Structured result returned by the gateway.

    ``usage`` is always present (defaults to a zeroed :class:`TokenUsage`) so a
    budget tracker can record every call without a None check. ``raw`` carries
    provider-specific metadata the caller may want to inspect (cache hit, model
    revision, finish reason, ...) without it being part of the typed contract.
    """

    text: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "model": self.model,
            "usage": self.usage.to_dict(),
            "raw": dict(self.raw),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LLMResponse":
        return cls(
            text=str(data.get("text", "")),
            model=str(data.get("model", "")),
            usage=TokenUsage.from_dict(data.get("usage") or {}),
            raw=dict(data.get("raw") or {}),
        )


__all__ = (
    "Message",
    "TokenUsage",
    "LLMRequest",
    "LLMResponse",
)
