from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence

from ...core.context_loader import ContextError, load_agent_context
from ..runners.base import AgentRunner, RunnerHooks
from ..runners.claude_code import ClaudeCodeRunner
from ..runners.codex import CodexRunner
from ..runners.gemini import GeminiRunner
from ..runners.github_copilot import GitHubCopilotRunner
from ..runners.ollama import OllamaRunner


class RegistryError(RuntimeError):
    """Raised when the participants pool cannot be assembled."""


RunnerFactory = Callable[[Mapping[str, Any], RunnerHooks], AgentRunner]


def _factory(cls: type[AgentRunner]) -> RunnerFactory:
    def build(config: Mapping[str, Any], hooks: RunnerHooks) -> AgentRunner:
        return cls(config=config, hooks=hooks)

    return build


DEFAULT_RUNNER_FACTORIES: Dict[str, RunnerFactory] = {
    "claude": _factory(ClaudeCodeRunner),
    "codex": _factory(CodexRunner),
    "gemini": _factory(GeminiRunner),
    "ollama": _factory(OllamaRunner),
    "github-copilot": _factory(GitHubCopilotRunner),
}


@dataclass(frozen=True)
class ParticipantsPool:
    """Department-level pool of runners loaded from a single manifest.json.

    Members (tech-lead, backend-engineer, ...) do *not* own runners. They
    request work from the gateway, which selects from this shared pool based
    on role-weights-v0.md and ranking signals.
    """

    agent_id: str
    runners: Mapping[str, AgentRunner]
    warnings: Sequence[str]

    def get(self, runner_id: str) -> AgentRunner:
        try:
            return self.runners[runner_id]
        except KeyError as exc:
            raise RegistryError(f"runner not in pool: {runner_id}") from exc

    def available(self) -> Sequence[AgentRunner]:
        return tuple(runner for runner in self.runners.values() if runner.is_available())

    def ids(self) -> Sequence[str]:
        return tuple(self.runners.keys())


def build_participants_pool(
    repo_root: Path,
    agent_id: str = "engineering-agent",
    *,
    hooks: Optional[RunnerHooks] = None,
    factories: Optional[Mapping[str, RunnerFactory]] = None,
) -> ParticipantsPool:
    """Load *agent_id*'s manifest and instantiate one runner per pool entry.

    *factories* lets tests inject fakes without touching the default mapping.
    Unknown ids are skipped with a warning instead of raising — that way a
    new participant can be referenced in manifest.json before its wrapper lands.
    """

    try:
        loaded = load_agent_context(repo_root=repo_root, agent_id=agent_id)
    except ContextError as exc:
        raise RegistryError(str(exc)) from exc

    manifest = loaded.manifest
    factory_map = dict(factories) if factories is not None else dict(DEFAULT_RUNNER_FACTORIES)
    hooks = hooks or RunnerHooks()
    runners: Dict[str, AgentRunner] = {}
    warnings: list[str] = list(loaded.warnings)

    for entry in _iter_pool_entries(manifest):
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            warnings.append("Skipping participants/integrations entry without id")
            continue

        factory = factory_map.get(entry_id)
        if factory is None:
            warnings.append(f"No runner factory registered for '{entry_id}', skipped")
            continue

        if entry_id in runners:
            warnings.append(f"Duplicate participant id '{entry_id}', keeping first")
            continue

        runners[entry_id] = factory(entry, hooks)

    return ParticipantsPool(agent_id=agent_id, runners=runners, warnings=tuple(warnings))


def _iter_pool_entries(manifest: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for key in ("participants", "integrations"):
        entries = manifest.get(key, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                yield entry
