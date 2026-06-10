from __future__ import annotations

import shutil
import subprocess
from typing import Sequence

from .base import (
    AgentRequest,
    AgentResponse,
    AgentRunner,
    RunnerCapability,
    RunnerStatus,
)


class GitHubCopilotRunner(AgentRunner):
    """GitHub-native executor (Copilot workspace / `gh copilot`).

    Modeled as an *integration* in manifest.json rather than a standalone CLI:
    this runner is the entry point for issue → branch → draft-PR flows that
    happen entirely on GitHub. Body is a stub for the MVP.
    """

    runner_id = "github-copilot"
    provider = "github"
    capabilities: Sequence[RunnerCapability] = (
        RunnerCapability.GITHUB_NATIVE,
        RunnerCapability.PATCH_PROPOSE,
    )

    def is_available(self) -> bool:
        if shutil.which("gh") is None:
            return False
        result = subprocess.run(
            ["gh", "extension", "list"],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        return "copilot" in result.stdout.lower()

    def submit(self, request: AgentRequest) -> AgentResponse:
        if not self.is_available():
            return AgentResponse(
                runner_id=self.runner_id,
                status=RunnerStatus.UNAVAILABLE,
                text="",
                detail="gh copilot extension not installed",
            )
        return self.dry_run(request)
