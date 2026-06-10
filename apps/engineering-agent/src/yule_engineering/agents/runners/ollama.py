from __future__ import annotations

import urllib.error
import urllib.request
from typing import Sequence

from .base import (
    AgentRequest,
    AgentResponse,
    AgentRunner,
    RunnerCapability,
    RunnerStatus,
)


class OllamaRunner(AgentRunner):
    """Local Ollama HTTP wrapper for private, low-cost work.

    Uses the same endpoint shape as ``planning.ollama`` so we can later share
    a single client. MVP only does a tags-endpoint reachability check; real
    generate/chat calls come once the dispatcher is in place.
    """

    runner_id = "ollama"
    provider = "local"
    capabilities: Sequence[RunnerCapability] = (
        RunnerCapability.ADVISE,
        RunnerCapability.LOCAL_PRIVATE,
    )

    DEFAULT_ENDPOINT = "http://localhost:11434"

    @property
    def endpoint(self) -> str:
        endpoint = self.config.get("endpoint")
        if isinstance(endpoint, str) and endpoint:
            return endpoint
        return self.DEFAULT_ENDPOINT

    def is_available(self) -> bool:
        url = self.endpoint.rstrip("/") + "/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def submit(self, request: AgentRequest) -> AgentResponse:
        if not self.is_available():
            return AgentResponse(
                runner_id=self.runner_id,
                status=RunnerStatus.UNAVAILABLE,
                text="",
                detail=f"ollama endpoint {self.endpoint} unreachable",
            )
        return self.dry_run(request)
