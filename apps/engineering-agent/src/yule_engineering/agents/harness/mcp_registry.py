"""MCP server registry — vendor-neutral SSoT for external tool servers.

MCP (Model Context Protocol) servers are *external tool channels a backend
connects to* — NOT plugins/hooks/skills (see docs/plugin-taxonomy.md §6). This
module is the single source of truth for which MCP servers Yule knows about,
kept vendor-neutral so each provider (Claude `.mcp`, Codex `mcp_servers`,
Gemini MCP) can project the same server.

SSoT files: ``integrations/mcp/<id>.json``. This module loads + validates them.
It is pure-Python and deterministic; **secret values are never stored** — only
the env-var *key name* that carries the credential.

Hard rails encoded here:
  * ``transport`` ∈ {http, stdio}; http requires ``url``, stdio requires ``command``.
  * ``auth.env`` is a key name, never a value (secret hard rail).
  * ``supports_providers`` ⊆ MCP-capable harnesses {claude, codex, gemini}.
    **Ollama is excluded** — it is a local inference backend, not an MCP host
    (consistent with provider-capability-matrix.md §4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

# MCP-capable projection targets. Ollama is intentionally absent — backend, not host.
MCP_CAPABLE_PROVIDERS: Tuple[str, ...] = ("claude", "codex", "gemini")
TRANSPORTS: Tuple[str, ...] = ("http", "stdio")


class McpValidationError(ValueError):
    """Raised when an MCP server spec is malformed or violates a hard rail."""


@dataclass(frozen=True)
class McpAuth:
    type: str  # e.g. "bearer", "none"
    env: str = ""  # env var KEY name carrying the secret — never the value


@dataclass(frozen=True)
class McpServer:
    id: str
    name: str
    description: str
    transport: str
    url: str = ""
    command: str = ""
    auth: Optional[McpAuth] = None
    tools: Tuple[str, ...] = field(default_factory=tuple)
    supports_providers: Tuple[str, ...] = field(default_factory=tuple)
    autonomy_level: str = "supervised"
    risk_class: str = "MEDIUM"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "transport": self.transport,
            "url": self.url,
            "command": self.command,
            "auth": {"type": self.auth.type, "env": self.auth.env} if self.auth else None,
            "tools": list(self.tools),
            "supports_providers": list(self.supports_providers),
            "autonomy_level": self.autonomy_level,
            "risk_class": self.risk_class,
        }


def default_mcp_dir() -> Path:
    """Repo-default location of the MCP SSoT files."""

    repo_root = Path(__file__).resolve().parents[6]
    return repo_root / "integrations" / "mcp"


def load_mcp_server_from_dict(payload: Mapping[str, object]) -> McpServer:
    if not isinstance(payload, Mapping):
        raise McpValidationError("MCP server payload must be a mapping")
    auth_raw = payload.get("auth")
    auth = None
    if isinstance(auth_raw, Mapping):
        auth = McpAuth(type=str(auth_raw.get("type", "none")), env=str(auth_raw.get("env", "")))
    server = McpServer(
        id=str(payload.get("id", "")),
        name=str(payload.get("name", "")),
        description=str(payload.get("description", "")),
        transport=str(payload.get("transport", "")),
        url=str(payload.get("url", "") or ""),
        command=str(payload.get("command", "") or ""),
        auth=auth,
        tools=tuple(str(t) for t in (payload.get("tools") or ())),
        supports_providers=tuple(str(p) for p in (payload.get("supports_providers") or ())),
        autonomy_level=str(payload.get("autonomy_level", "supervised")),
        risk_class=str(payload.get("risk_class", "MEDIUM")),
    )
    validate_mcp_server(server)
    return server


def validate_mcp_server(server: McpServer) -> None:
    if not server.id:
        raise McpValidationError("MCP server missing id")
    if server.transport not in TRANSPORTS:
        raise McpValidationError(
            f"MCP server {server.id!r} transport {server.transport!r} must be one of {TRANSPORTS}"
        )
    if server.transport == "http" and not server.url:
        raise McpValidationError(f"MCP server {server.id!r} (http) requires a url")
    if server.transport == "stdio" and not server.command:
        raise McpValidationError(f"MCP server {server.id!r} (stdio) requires a command")
    for provider in server.supports_providers:
        if provider not in MCP_CAPABLE_PROVIDERS:
            raise McpValidationError(
                f"MCP server {server.id!r} supports_providers {provider!r} not MCP-capable "
                f"(must be one of {MCP_CAPABLE_PROVIDERS}; Ollama is a backend, not an MCP host)"
            )
    # Secret hard rail: auth must carry an env KEY name, never a literal secret.
    if server.auth and server.auth.type not in ("none", ""):
        if not server.auth.env:
            raise McpValidationError(
                f"MCP server {server.id!r} auth.type={server.auth.type!r} requires auth.env (key name)"
            )
        if _looks_like_secret_value(server.auth.env):
            raise McpValidationError(
                f"MCP server {server.id!r} auth.env looks like a secret VALUE, not an env key name"
            )


def _looks_like_secret_value(env_key: str) -> bool:
    # env keys are UPPER_SNAKE identifiers; long/random or url-ish strings are values.
    return (
        len(env_key) > 64
        or "://" in env_key
        or env_key.strip() != env_key
        or any(ch in env_key for ch in " \t/\\")
    )


def load_mcp_servers(mcp_dir: Optional[Path] = None) -> List[McpServer]:
    """Load + validate every ``<id>.json`` under *mcp_dir* (sorted by id)."""

    path = mcp_dir or default_mcp_dir()
    servers: List[McpServer] = []
    if not path.exists():
        return servers
    for f in sorted(path.glob("*.json")):
        servers.append(load_mcp_server_from_dict(json.loads(f.read_text(encoding="utf-8"))))
    return servers


__all__ = (
    "MCP_CAPABLE_PROVIDERS",
    "TRANSPORTS",
    "McpValidationError",
    "McpAuth",
    "McpServer",
    "default_mcp_dir",
    "load_mcp_server_from_dict",
    "validate_mcp_server",
    "load_mcp_servers",
)
