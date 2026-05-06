from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents import (
    ParticipantsPool,
    RegistryError,
    build_participants_pool,
)
from yule_orchestrator.agents.runners import (
    AgentRequest,
    AgentResponse,
    AgentRunner,
    RunnerCapability,
    RunnerHooks,
    RunnerStatus,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeRunner(AgentRunner):
    runner_id = "fake"
    provider = "fake"
    capabilities: Sequence[RunnerCapability] = (RunnerCapability.ADVISE,)

    def __init__(self, *, config: Mapping[str, Any] | None = None, hooks: RunnerHooks | None = None) -> None:
        super().__init__(config=config, hooks=hooks)
        self.submitted: list[AgentRequest] = []

    def is_available(self) -> bool:
        return True

    def submit(self, request: AgentRequest) -> AgentResponse:
        self.submitted.append(request)
        return AgentResponse(
            runner_id=self.runner_id,
            status=RunnerStatus.OK,
            text=f"echo:{request.prompt}",
        )


class _RecordingTracker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest, AgentResponse]] = []

    def record(self, runner_id: str, request: AgentRequest, response: AgentResponse) -> None:
        self.calls.append((runner_id, request, response))


class _StaticReferenceCollector:
    def __init__(self, payload: Sequence[Mapping[str, Any]]) -> None:
        self.payload = payload
        self.calls = 0

    def collect(self, request: AgentRequest) -> Sequence[Mapping[str, Any]]:
        self.calls += 1
        return self.payload


class RegistryLoadingTestCase(unittest.TestCase):
    def test_engineering_agent_pool_resolves_known_ids(self) -> None:
        pool = build_participants_pool(REPO_ROOT, "engineering-agent")

        self.assertIsInstance(pool, ParticipantsPool)
        self.assertEqual(pool.agent_id, "engineering-agent")
        self.assertEqual(
            set(pool.ids()),
            {"claude", "codex", "gemini", "ollama", "github-copilot"},
        )

    def test_unknown_id_is_skipped_with_warning(self) -> None:
        pool = build_participants_pool(
            REPO_ROOT,
            "engineering-agent",
            factories={
                "claude": lambda config, hooks: _FakeRunner(config=config, hooks=hooks),
            },
        )

        self.assertEqual(set(pool.ids()), {"claude"})
        skipped = [w for w in pool.warnings if "github-copilot" in w]
        self.assertTrue(skipped, "expected warning for unmapped participant")

    def test_unknown_agent_raises_registry_error(self) -> None:
        with self.assertRaises(RegistryError):
            build_participants_pool(REPO_ROOT, "no-such-agent")


class RunnerHookTestCase(unittest.TestCase):
    def test_dry_run_returns_status_dry_run_without_calling_submit(self) -> None:
        runner = _FakeRunner()
        request = AgentRequest(prompt="ignored", role="qa-engineer", task_id="t-1")

        response = runner.run(request, dry_run=True)

        self.assertEqual(response.status, RunnerStatus.DRY_RUN)
        self.assertEqual(runner.submitted, [])
        self.assertIn("elapsed_ms", response.metrics)

    def test_performance_tracker_receives_outcome(self) -> None:
        tracker = _RecordingTracker()
        runner = _FakeRunner(hooks=RunnerHooks(performance_tracker=tracker))
        request = AgentRequest(prompt="hello", role="backend-engineer", task_id="t-2")

        response = runner.run(request)

        self.assertEqual(response.status, RunnerStatus.OK)
        self.assertEqual(len(tracker.calls), 1)
        runner_id, recorded_request, recorded_response = tracker.calls[0]
        self.assertEqual(runner_id, "fake")
        self.assertEqual(recorded_request.task_id, "t-2")
        self.assertEqual(recorded_response, response)

    def test_reference_collector_fills_empty_references(self) -> None:
        collector = _StaticReferenceCollector(payload=({"src": "Mobbin", "url": "x"},))
        runner = _FakeRunner(hooks=RunnerHooks(reference_collector=collector))
        request = AgentRequest(prompt="design", role="product-designer", task_id="t-3")

        runner.run(request)

        self.assertEqual(collector.calls, 1)
        self.assertEqual(runner.submitted[0].references, ({"src": "Mobbin", "url": "x"},))

    def test_reference_collector_skipped_when_request_already_has_refs(self) -> None:
        collector = _StaticReferenceCollector(payload=({"src": "Mobbin"},))
        runner = _FakeRunner(hooks=RunnerHooks(reference_collector=collector))
        request = AgentRequest(
            prompt="design",
            role="product-designer",
            task_id="t-4",
            references=({"src": "Notefolio"},),
        )

        runner.run(request)

        self.assertEqual(collector.calls, 0)
        self.assertEqual(runner.submitted[0].references, ({"src": "Notefolio"},))


class RunnerAvailabilityTestCase(unittest.TestCase):
    def test_unavailable_runner_returns_unavailable_status(self) -> None:
        from yule_orchestrator.agents.runners.claude_code import ClaudeCodeRunner

        runner = ClaudeCodeRunner()
        request = AgentRequest(prompt="hi", role="tech-lead", task_id="t-5")
        with patch("yule_orchestrator.agents.runners.claude_code.shutil.which", return_value=None):
            response = runner.submit(request)

        self.assertEqual(response.status, RunnerStatus.UNAVAILABLE)
        self.assertIn("claude", response.detail or "")


if __name__ == "__main__":
    unittest.main()
