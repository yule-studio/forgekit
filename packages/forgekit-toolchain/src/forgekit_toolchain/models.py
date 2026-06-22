"""Toolchain version-switching models — pure dataclasses + vocabulary. stdlib only.

A *requirement* is "this repo wants node 20.11" (read from a repo-local manifest or a
loadout's environment assumption). A *profile* is the desired set of tool versions for
a piece of work. A *status* is required-vs-active for one tool. A *switch action* is one
concrete manager command with a scope (local / global / install / destructive) that
drives approval gating. None of this runs anything — the manager seam does the IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

# ── requirement source kinds (repo-local manifests + loadout) ────────────────
SRC_TOOL_VERSIONS = "tool-versions"     # .tool-versions (asdf/mise)
SRC_MISE = "mise.toml"                   # .mise.toml / mise.toml / .config/mise
SRC_NVMRC = ".nvmrc"
SRC_PYTHON_VERSION = ".python-version"
SRC_RUBY_VERSION = ".ruby-version"
SRC_GO = "go"                            # .go-version / go.mod
SRC_JAVA_VERSION = ".java-version"
SRC_PACKAGE_JSON = "package.json"        # engines.node
SRC_PYPROJECT = "pyproject.toml"         # requires-python
SRC_LOADOUT = "loadout"                  # derived from a Hephaistos loadout

# ── per-tool status states ───────────────────────────────────────────────────
STATE_MATCH = "match"               # active version satisfies the requirement
STATE_MISMATCH = "mismatch"         # active present but wrong version
STATE_MISSING = "missing"           # tool not installed / not resolvable
STATE_UNPINNED = "unpinned"         # active present, repo declares no version
STATE_MANAGER_MISSING = "manager_missing"   # cannot verify — no mise/asdf present
STATE_UNKNOWN = "unknown"

# ── switch action scopes (drive approval gating) ─────────────────────────────
SCOPE_LOCAL = "local"           # writes repo-local pin (mise use → ./.mise.toml) — reversible
SCOPE_INSTALL = "install"       # downloads/installs a runtime version (network, disk) — gated
SCOPE_GLOBAL = "global"         # writes the user-global pin (mise use -g) — gated
SCOPE_DESTRUCTIVE = "destructive"   # uninstall / prune — gated

_GATED_SCOPES = frozenset({SCOPE_INSTALL, SCOPE_GLOBAL, SCOPE_DESTRUCTIVE})


@dataclass(frozen=True)
class ToolRequirement:
    """One declared want: ``tool`` at ``version`` (may be a range/major), from ``source``."""

    tool: str
    version: str = ""           # "" = declared without a version (presence only)
    source: str = ""            # one of SRC_*
    source_file: str = ""       # the manifest path it came from (evidence)
    raw: str = ""               # the raw token, for honest display

    @property
    def pinned(self) -> bool:
        return bool(self.version.strip())

    def to_dict(self) -> dict:
        return {"tool": self.tool, "version": self.version, "source": self.source,
                "source_file": self.source_file, "raw": self.raw}


@dataclass(frozen=True)
class ToolchainProfile:
    """A desired toolchain — the set of tool versions for a body of work."""

    name: str
    origin: str = ""            # "loadout:backend-java-local" / "detected:<root>"
    tools: Tuple[ToolRequirement, ...] = ()
    notes: str = ""

    def tool(self, name: str) -> "ToolRequirement | None":
        for t in self.tools:
            if t.tool == name:
                return t
        return None

    def to_dict(self) -> dict:
        return {"name": self.name, "origin": self.origin,
                "tools": [t.to_dict() for t in self.tools], "notes": self.notes}


@dataclass(frozen=True)
class ToolStatus:
    """required-vs-active for one tool — the verify/drift unit."""

    tool: str
    required: str
    active: str
    state: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state in (STATE_MATCH, STATE_UNPINNED)

    def to_dict(self) -> dict:
        return {"tool": self.tool, "required": self.required, "active": self.active,
                "state": self.state, "detail": self.detail}


@dataclass(frozen=True)
class SwitchAction:
    """One concrete manager command to reach the profile, with its approval posture."""

    tool: str
    version: str
    command: Tuple[str, ...]        # argv actually handed to the manager (no shell)
    scope: str                      # one of SCOPE_*
    reason: str = ""

    @property
    def requires_approval(self) -> bool:
        return self.scope in _GATED_SCOPES

    def to_dict(self) -> dict:
        return {"tool": self.tool, "version": self.version, "command": list(self.command),
                "scope": self.scope, "reason": self.reason,
                "requires_approval": self.requires_approval}


@dataclass(frozen=True)
class SwitchPlan:
    """The ordered actions to move the env to a profile + the gating verdict."""

    profile: str
    actions: Tuple[SwitchAction, ...] = ()
    manager: str = ""               # "mise" / "" when no manager
    manager_available: bool = False

    @property
    def gated(self) -> Tuple[SwitchAction, ...]:
        return tuple(a for a in self.actions if a.requires_approval)

    @property
    def local(self) -> Tuple[SwitchAction, ...]:
        return tuple(a for a in self.actions if not a.requires_approval)

    @property
    def needs_approval(self) -> bool:
        return bool(self.gated)

    def to_dict(self) -> dict:
        return {"profile": self.profile, "manager": self.manager,
                "manager_available": self.manager_available,
                "needs_approval": self.needs_approval,
                "actions": [a.to_dict() for a in self.actions]}


@dataclass(frozen=True)
class ToolchainReport:
    """verify / drift outcome over a profile."""

    profile: str
    statuses: Tuple[ToolStatus, ...] = ()
    manager_available: bool = False

    @property
    def drifted(self) -> Tuple[ToolStatus, ...]:
        return tuple(s for s in self.statuses if not s.ok)

    @property
    def in_sync(self) -> bool:
        return self.manager_available and not self.drifted

    @property
    def verdict(self) -> str:
        if not self.manager_available:
            return "manager-missing"
        return "in-sync" if self.in_sync else "drift"

    def to_dict(self) -> dict:
        return {"profile": self.profile, "verdict": self.verdict,
                "manager_available": self.manager_available,
                "statuses": [s.to_dict() for s in self.statuses]}


__all__ = (
    "SRC_TOOL_VERSIONS", "SRC_MISE", "SRC_NVMRC", "SRC_PYTHON_VERSION", "SRC_RUBY_VERSION",
    "SRC_GO", "SRC_JAVA_VERSION", "SRC_PACKAGE_JSON", "SRC_PYPROJECT", "SRC_LOADOUT",
    "STATE_MATCH", "STATE_MISMATCH", "STATE_MISSING", "STATE_UNPINNED",
    "STATE_MANAGER_MISSING", "STATE_UNKNOWN",
    "SCOPE_LOCAL", "SCOPE_INSTALL", "SCOPE_GLOBAL", "SCOPE_DESTRUCTIVE",
    "ToolRequirement", "ToolchainProfile", "ToolStatus", "SwitchAction",
    "SwitchPlan", "ToolchainReport",
)
