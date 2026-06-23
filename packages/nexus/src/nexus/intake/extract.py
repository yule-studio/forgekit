"""Extract — map raw ``SourceItem`` signals → ``ExternalCandidate`` (pure).

Heuristic, deterministic, offline: it classifies a collected signal's install
shape / provider affinity / capability from its title+summary+url, then dedupes by
fingerprint (keeping the freshest/highest-scored) and applies the source allowlist.
No network here — collection lives in :mod:`nexus.intake.collect`, so this stays a
pure transform that CI exercises without IO.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from . import candidate as K
from .candidate import ExternalCandidate

# keyword cues — install shape (order matters: most specific first) --------------
_MCP_CUES = ("mcp", "model context protocol", "mcp-server", "mcp server")
_SKILL_CUES = ("skill", "/skill", "skills", "claude skill", "agent skill", "recipe")
_PLUGIN_CUES = ("plugin", "extension", "addon", "add-on")
_HOOK_CUES = ("hook", "pre-commit", "lifecycle hook")
_CLI_CUES = ("cli", "command-line", "command line", "terminal tool", "tui")
_BACKEND_CUES = ("ollama", "local llm", "inference engine", "llama.cpp", "vllm", "backend runner")

# keyword cues — provider affinity ----------------------------------------------
_CLAUDE_CUES = ("claude", "anthropic", "claude code", "claude-code")
_CODEX_CUES = ("codex", "openai", "gpt-", "chatgpt")
_GEMINI_CUES = ("gemini", "google ai", "vertex ai", "bard")

# keyword cues — capability class -----------------------------------------------
_CAP_CUES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (K.CAP_RETRIEVAL, ("retriev", "rag", "search", "embedding", "vector", "index")),
    (K.CAP_CODE_REVIEW, ("review", "lint", "static analysis", "refactor", "code quality", "simplif")),
    (K.CAP_MEMORY, ("memory", "remember", "recall", "knowledge base", "note")),
    (K.CAP_SECURITY, ("security", "vuln", "secret", "audit", "sast", "pentest")),
    (K.CAP_INFRA, ("deploy", "infra", "kubernetes", "k8s", "docker", "terraform", "ci/cd")),
    (K.CAP_UI, ("ui", "frontend", "component", "design system", "figma")),
    (K.CAP_DATA, ("dataset", "etl", "pipeline", "analytics", "sql", "dataframe")),
    (K.CAP_ORCHESTRATION, ("agent", "orchestrat", "workflow", "multi-agent", "router", "scheduler")),
)


def _blob(item) -> str:
    parts = [
        getattr(item, "title", "") or "",
        getattr(item, "summary", "") or "",
        getattr(item, "url", "") or "",
    ]
    return " ".join(parts).lower()


def classify_install_shape(blob: str) -> str:
    if any(c in blob for c in _BACKEND_CUES):
        return K.SHAPE_BACKEND
    if any(c in blob for c in _MCP_CUES):
        return K.SHAPE_MCP
    if any(c in blob for c in _SKILL_CUES):
        return K.SHAPE_SKILL
    if any(c in blob for c in _HOOK_CUES):
        return K.SHAPE_HOOK
    if any(c in blob for c in _PLUGIN_CUES):
        return K.SHAPE_PLUGIN
    if any(c in blob for c in _CLI_CUES):
        return K.SHAPE_CLI
    return K.SHAPE_LIB


def classify_provider_affinity(blob: str) -> str:
    if any(c in blob for c in _CLAUDE_CUES):
        return K.AFFINITY_CLAUDE
    if any(c in blob for c in _CODEX_CUES):
        return K.AFFINITY_CODEX
    if any(c in blob for c in _GEMINI_CUES):
        return K.AFFINITY_GEMINI
    return K.AFFINITY_NEUTRAL


def classify_capability(blob: str) -> str:
    for cap, cues in _CAP_CUES:
        if any(c in blob for c in cues):
            return cap
    return K.CAP_UNKNOWN


def candidate_from_item(item) -> ExternalCandidate:
    """Map one ``SourceItem`` → a candidate (heuristic; license/maint stay unknown).

    We do NOT fabricate license / maintenance / trust we cannot read from a bare
    signal — they default to ``unknown`` and the curation gate keeps such candidates
    as ``raw`` until enriched. That honesty is the point: an un-vetted repo never
    auto-promotes.
    """

    blob = _blob(item)
    return ExternalCandidate(
        name=getattr(item, "title", "") or "",
        source=getattr(item, "source_id", "") or "",
        repo_url=getattr(item, "url", "") or "",
        provider_affinity=classify_provider_affinity(blob),
        capability_class=classify_capability(blob),
        install_shape=classify_install_shape(blob),
        trust_risk=K.RISK_UNKNOWN,
        maintenance_signal=K.MAINT_UNKNOWN,
        license=K.LICENSE_UNKNOWN,
        why_it_matters="",
        score=float(getattr(item, "score", 0.0) or 0.0),
    )


def _rank(c: ExternalCandidate) -> tuple:
    """Dedupe winner key: a hand-vetted (richer) record beats a thin one, then score."""

    return (1 if c.has_min_metadata else 0, c.score)


def dedupe(cands: Sequence[ExternalCandidate]) -> Tuple[ExternalCandidate, ...]:
    """Collapse same-fingerprint candidates, keeping the richest/freshest one.

    Tie-break: a candidate with full metadata (operator-enriched) wins over a thin
    auto-extracted one even at equal score, so curation can lift a signal into a
    promotable candidate by merging on fingerprint.
    """

    best: Dict[str, ExternalCandidate] = {}
    for c in cands:
        fp = c.fingerprint
        cur = best.get(fp)
        if cur is None or _rank(c) > _rank(cur):
            best[fp] = c
    return tuple(best.values())


def apply_source_allowlist(
    cands: Sequence[ExternalCandidate],
    allowlist: Sequence[str] = K.DEFAULT_SOURCE_ALLOWLIST,
) -> Tuple[ExternalCandidate, ...]:
    allowed = set(allowlist)
    return tuple(c for c in cands if c.source in allowed)


def extract_candidates(
    items: Sequence,
    *,
    source_allowlist: Sequence[str] = K.DEFAULT_SOURCE_ALLOWLIST,
    enrich: Sequence[ExternalCandidate] = (),
) -> Tuple[ExternalCandidate, ...]:
    """Full extract: items → candidates → (+operator enrich) → dedupe → allowlist.

    ``enrich`` lets an operator/curator supply hand-vetted candidates (with real
    license/trust/why_it_matters) that merge by fingerprint with the auto-extracted
    ones — the hand-vetted record wins ties via a small score bump applied by the
    caller, so curation can lift a raw signal into a promotable candidate.
    """

    auto = [candidate_from_item(it) for it in items]
    merged: List[ExternalCandidate] = list(auto) + list(enrich)
    deduped = dedupe(merged)
    return apply_source_allowlist(deduped, source_allowlist)


__all__ = (
    "classify_install_shape", "classify_provider_affinity", "classify_capability",
    "candidate_from_item", "dedupe", "apply_source_allowlist", "extract_candidates",
)
