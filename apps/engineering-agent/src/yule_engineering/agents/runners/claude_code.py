from __future__ import annotations

import shutil
from typing import Sequence

from .base import (
    AgentRequest,
    AgentResponse,
    AgentRunner,
    RunnerCapability,
    RunnerStatus,
)


class ClaudeCodeRunner(AgentRunner):
    """Wraps the local ``claude`` CLI from Anthropic's Claude Code package.

    MVP body is intentionally a stub: ``submit`` defers to ``dry_run`` so the
    rest of the engineering-agent (registry, dispatcher, hooks) can be wired
    up before we shell out to the real CLI.
    """

    runner_id = "claude"
    provider = "anthropic"
    capabilities: Sequence[RunnerCapability] = (
        RunnerCapability.EXECUTE,
        RunnerCapability.ADVISE,
        RunnerCapability.REVIEW,
        RunnerCapability.PATCH_PROPOSE,
    )

    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def submit(self, request: AgentRequest) -> AgentResponse:
        if not self.is_available():
            return AgentResponse(
                runner_id=self.runner_id,
                status=RunnerStatus.UNAVAILABLE,
                text="",
                detail="claude CLI not found on PATH",
            )
        return self.dry_run(request)
