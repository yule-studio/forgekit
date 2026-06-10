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


class GeminiRunner(AgentRunner):
    """Wraps Google's ``gemini`` CLI for long-context analysis and planning.

    The role-weights policy favours Gemini for product-designer and long-file
    review tasks; this wrapper exposes those capabilities so the dispatcher
    can pick it up. Body is a stub for the MVP.
    """

    runner_id = "gemini"
    provider = "google"
    capabilities: Sequence[RunnerCapability] = (
        RunnerCapability.ADVISE,
        RunnerCapability.LONG_CONTEXT,
    )

    def is_available(self) -> bool:
        return shutil.which("gemini") is not None

    def submit(self, request: AgentRequest) -> AgentResponse:
        if not self.is_available():
            return AgentResponse(
                runner_id=self.runner_id,
                status=RunnerStatus.UNAVAILABLE,
                text="",
                detail="gemini CLI not found on PATH",
            )
        return self.dry_run(request)
