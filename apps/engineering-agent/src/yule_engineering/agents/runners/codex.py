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


class CodexRunner(AgentRunner):
    """Wraps the OpenAI ``codex`` CLI as an advisor / patch-proposer.

    MVP scope: availability check + dry-run pass-through. Real subprocess
    invocation lives in a follow-up milestone.
    """

    runner_id = "codex"
    provider = "openai"
    capabilities: Sequence[RunnerCapability] = (
        RunnerCapability.ADVISE,
        RunnerCapability.REVIEW,
        RunnerCapability.PATCH_PROPOSE,
    )

    def is_available(self) -> bool:
        return shutil.which("codex") is not None

    def submit(self, request: AgentRequest) -> AgentResponse:
        if not self.is_available():
            return AgentResponse(
                runner_id=self.runner_id,
                status=RunnerStatus.UNAVAILABLE,
                text="",
                detail="codex CLI not found on PATH",
            )
        return self.dry_run(request)
