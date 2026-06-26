"""External candidate schema — the pre-Armory intake record (vendor-neutral).

An :class:`ExternalCandidate` is ONE external skill/plugin/tool/MCP found in the
wild (GitHub / HN / Reddit / RSS) that *might* be worth adding to the Armory. It
captures the metadata a curation gate needs BEFORE anything is installed: where it
came from, what it is, who it's for, and whether it's trustworthy / maintained /
licensed. It is deliberately NOT an Armory ``WeaponSpec``/``SkillSpec`` (those are
*registered* catalog entries with a selection contract) — candidate is the intake
that *precedes* registration. Pure dataclass → serialisable + testable.

Vocab is aligned to ``docs/plugin-taxonomy.md`` (tool/skill/plugin/MCP/backend).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

# provider affinity — who the candidate is for (taxonomy: harness vs backend) ----
AFFINITY_CLAUDE = "claude"
AFFINITY_CODEX = "codex"
AFFINITY_GEMINI = "gemini"
AFFINITY_NEUTRAL = "neutral"

# install shape — how it would be installed / projected (taxonomy §1) ------------
SHAPE_SKILL = "skill"        # reusable agent procedure (skills/<id>.md → harness)
SHAPE_PLUGIN = "plugin"      # runtime-plugin OR harness-plugin bundle
SHAPE_MCP = "mcp"            # external tool server (Model Context Protocol)
SHAPE_HOOK = "hook"          # lifecycle intervention point
SHAPE_CLI = "cli"            # standalone command-line tool
SHAPE_LIB = "lib"            # library / SDK imported into code
SHAPE_BACKEND = "backend"    # LLM engine (Ollama etc.) — NOT a harness projection

# capability class — what it does (vendor-neutral; NOT a provider name) ----------
CAP_RETRIEVAL = "retrieval"
CAP_CODE_REVIEW = "code-review"
CAP_ORCHESTRATION = "orchestration"
CAP_MEMORY = "memory"
CAP_SECURITY = "security"
CAP_INFRA = "infra"
CAP_UI = "ui"
CAP_DATA = "data"
CAP_UNKNOWN = "unknown"

# trust / risk -------------------------------------------------------------------
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_UNKNOWN = "unknown"

# maintenance signal -------------------------------------------------------------
MAINT_ACTIVE = "active"
MAINT_STALE = "stale"
MAINT_ARCHIVED = "archived"
MAINT_UNKNOWN = "unknown"

# license buckets ----------------------------------------------------------------
LICENSE_UNKNOWN = "unknown"
LICENSE_PROPRIETARY = "proprietary"
# permissive/known OSI licenses pass as-is (e.g. "MIT", "Apache-2.0", "BSD-3-Clause").

# curation disposition (the gate's verdict) -------------------------------------
DISPOSITION_PROMOTE = "promote"   # eligible to become an Armory candidate
DISPOSITION_RAW = "raw"           # kept as raw intake (metadata too thin)
DISPOSITION_BLOCKED = "blocked"   # rejected (risk / shape / license / allowlist)


def normalize_url(url: str) -> str:
    """Normalize a URL for fingerprinting — strip scheme / trailing slash / case."""

    u = (url or "").strip().lower()
    for prefix in ("https://", "http://", "git+https://", "git://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
            break
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


@dataclass(frozen=True)
class ExternalCandidate:
    """One external tool/skill/plugin/MCP candidate — pre-Armory intake record."""

    name: str
    source: str = ""                          # source_type it was found in
    repo_url: str = ""                         # repo / homepage URL
    provider_affinity: str = AFFINITY_NEUTRAL
    capability_class: str = CAP_UNKNOWN
    install_shape: str = SHAPE_LIB
    trust_risk: str = RISK_UNKNOWN
    maintenance_signal: str = MAINT_UNKNOWN
    license: str = LICENSE_UNKNOWN
    why_it_matters: str = ""
    score: float = 0.0                         # upstream popularity (stars/ups/points)

    @property
    def fingerprint(self) -> str:
        """Dedupe key: normalized URL, else ``source_type:name`` (honest fallback)."""

        nu = normalize_url(self.repo_url)
        if nu:
            return nu
        return f"{self.source}:{(self.name or '').strip().lower()}"

    @property
    def has_min_metadata(self) -> bool:
        """The floor for promotion eligibility (gate adds the policy checks)."""

        return bool(
            self.repo_url
            and self.name
            and self.capability_class != CAP_UNKNOWN
            and self.why_it_matters
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source": self.source,
            "repo_url": self.repo_url,
            "provider_affinity": self.provider_affinity,
            "capability_class": self.capability_class,
            "install_shape": self.install_shape,
            "trust_risk": self.trust_risk,
            "maintenance_signal": self.maintenance_signal,
            "license": self.license,
            "why_it_matters": self.why_it_matters,
            "score": self.score,
            "fingerprint": self.fingerprint,
        }


# allowlist defaults (the curation gate reads these) ----------------------------
DEFAULT_SHAPE_ALLOWLIST: Tuple[str, ...] = (
    SHAPE_SKILL, SHAPE_PLUGIN, SHAPE_MCP, SHAPE_HOOK, SHAPE_CLI, SHAPE_LIB,
)  # SHAPE_BACKEND deliberately excluded — backends are not harness projections.
DEFAULT_SOURCE_ALLOWLIST: Tuple[str, ...] = (
    "github", "hackernews", "reddit", "rss", "repo-local",
)


__all__ = (
    "AFFINITY_CLAUDE", "AFFINITY_CODEX", "AFFINITY_GEMINI", "AFFINITY_NEUTRAL",
    "SHAPE_SKILL", "SHAPE_PLUGIN", "SHAPE_MCP", "SHAPE_HOOK", "SHAPE_CLI",
    "SHAPE_LIB", "SHAPE_BACKEND",
    "CAP_RETRIEVAL", "CAP_CODE_REVIEW", "CAP_ORCHESTRATION", "CAP_MEMORY",
    "CAP_SECURITY", "CAP_INFRA", "CAP_UI", "CAP_DATA", "CAP_UNKNOWN",
    "RISK_LOW", "RISK_MEDIUM", "RISK_HIGH", "RISK_UNKNOWN",
    "MAINT_ACTIVE", "MAINT_STALE", "MAINT_ARCHIVED", "MAINT_UNKNOWN",
    "LICENSE_UNKNOWN", "LICENSE_PROPRIETARY",
    "DISPOSITION_PROMOTE", "DISPOSITION_RAW", "DISPOSITION_BLOCKED",
    "normalize_url", "ExternalCandidate",
    "DEFAULT_SHAPE_ALLOWLIST", "DEFAULT_SOURCE_ALLOWLIST",
)
