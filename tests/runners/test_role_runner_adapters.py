"""RoleRunner ↔ AgentRunner adapter tests — A-M11.

The dispatcher-side tests use bespoke ``_ScriptedRunner`` stubs so the
contract is decoupled from the existing CLI-wrapping
:class:`AgentRunner`. The adapter tests here pin the *bridge* between
the two: when an existing :class:`AgentRunner` (Claude/Codex/Ollama) is
wrapped via :func:`claude_role_runner` & friends, the adapter must
correctly translate ``RunnerStatus`` → role-runner status.

Why this matters: the production wiring will hand the dispatcher
adapter-wrapped Claude/Codex/Ollama runners. If the adapter's status
mapping silently treats UNAVAILABLE as OK we'd never fall through the
priority chain.
"""

from __future__ import annotations

import unittest
from typing import Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.runners.base import (
    AgentRequest,
    AgentResponse,
    AgentRunner,
    RunnerCapability,
    RunnerStatus,
)
from yule_orchestrator.agents.runners.role_runner import (
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_OLLAMA,
    RoleRunnerInput,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    claude_role_runner,
    codex_role_runner,
    ollama_role_runner,
)


class _StubAgentRunner(AgentRunner):
    """An AgentRunner whose submit() returns a scripted response.

    Mirrors the existing Claude/Codex/Ollama wrapper shape but lets
    tests drive any RunnerStatus + text.
    """

    runner_id = "stub"
    provider = "stub"
    capabilities: Sequence[RunnerCapability] = (RunnerCapability.ADVISE,)

    def __init__(
        self,
        *,
        available: bool = True,
        status: RunnerStatus = RunnerStatus.OK,
        text: str = "",
        raises: bool = False,
        runner_id: str = "stub",
    ) -> None:
        super().__init__()
        self._available = available
        self._status = status
        self._text = text
        self._raises = raises
        self.runner_id = runner_id
        self.last_request: AgentRequest | None = None

    def is_available(self) -> bool:
        return self._available

    def submit(self, request: AgentRequest) -> AgentResponse:
        self.last_request = request
        if self._raises:
            raise RuntimeError("stub agent runner exploded")
        return AgentResponse(
            runner_id=self.runner_id,
            status=self._status,
            text=self._text,
            detail=f"stub runner status={self._status.value}",
        )


def _input() -> RoleRunnerInput:
    return RoleRunnerInput(
        role="ai-engineer",
        session_id="sess-adapter-1",
        prompt="Discord on_message 흐름 정리",
        role_profile={"short_name": "ai-engineer"},
    )


class AdapterStatusMappingTests(unittest.TestCase):
    def test_ok_response_with_text_maps_to_ok(self) -> None:
        agent = _StubAgentRunner(
            status=RunnerStatus.OK, text="Claude take body", runner_id="claude"
        )
        adapter = claude_role_runner(agent)
        out = adapter.generate(_input())
        self.assertEqual(out.status, STATUS_OK)
        self.assertEqual(out.provider, PROVIDER_CLAUDE)
        self.assertEqual(out.text, "Claude take body")

    def test_unavailable_response_maps_to_unavailable(self) -> None:
        agent = _StubAgentRunner(
            status=RunnerStatus.UNAVAILABLE, text="", runner_id="codex"
        )
        adapter = codex_role_runner(agent)
        out = adapter.generate(_input())
        self.assertEqual(out.status, STATUS_UNAVAILABLE)
        self.assertEqual(out.provider, PROVIDER_CODEX)
        self.assertEqual(out.text, "")

    def test_dry_run_response_maps_to_error_so_chain_walks_on(self) -> None:
        # The CLI wrappers currently default ``submit`` to ``dry_run``
        # while the real subprocess wiring is pending. The adapter
        # treats DRY_RUN as error so the dispatcher walks past it
        # rather than emitting placeholder text into the forum.
        agent = _StubAgentRunner(
            status=RunnerStatus.DRY_RUN,
            text="[dry-run] codex would have run",
            runner_id="codex",
        )
        adapter = codex_role_runner(agent)
        out = adapter.generate(_input())
        self.assertEqual(out.status, STATUS_ERROR)
        self.assertEqual(out.text, "")

    def test_ok_response_with_empty_text_maps_to_error(self) -> None:
        # Empty body from a runner is useless — adapter degrades to
        # error so the chain reaches the next candidate.
        agent = _StubAgentRunner(
            status=RunnerStatus.OK, text="", runner_id="ollama"
        )
        adapter = ollama_role_runner(agent)
        out = adapter.generate(_input())
        self.assertEqual(out.status, STATUS_ERROR)
        self.assertEqual(out.provider, PROVIDER_OLLAMA)

    def test_submit_raise_does_not_propagate(self) -> None:
        agent = _StubAgentRunner(raises=True, runner_id="claude")
        adapter = claude_role_runner(agent)
        out = adapter.generate(_input())
        # Any exception inside the wrapped agent maps to an error
        # role-runner output so the dispatcher can degrade cleanly.
        self.assertEqual(out.status, STATUS_ERROR)
        self.assertEqual(out.provider, PROVIDER_CLAUDE)
        self.assertIn("exploded", out.detail or "")


class AdapterRequestShapingTests(unittest.TestCase):
    """The adapter must hand the wrapped runner an AgentRequest whose
    ``context`` carries the role profile / topic memory / source
    context, plus the previous-decisions list as references. That's the
    bridge that lets a real Claude/Codex implementation pull the four
    spec-mandated context channels straight off the request without
    knowing about RoleRunnerInput.
    """

    def test_request_carries_role_profile_and_context(self) -> None:
        agent = _StubAgentRunner(status=RunnerStatus.OK, text="ok body")
        adapter = claude_role_runner(agent)
        adapter.generate(
            RoleRunnerInput(
                role="ai-engineer",
                session_id="sess-99",
                prompt="요청 요약",
                role_profile={"short_name": "ai-engineer", "memory_role_filter": "ai-engineer"},
                topic_memory={"topic_key": "rag-pipeline"},
                source_context={"title": "RAG basics"},
                previous_decisions=(
                    {"role": "tech-lead", "summary": "구조 분리"},
                ),
            )
        )

        req = agent.last_request
        self.assertIsNotNone(req)
        assert req is not None  # for type narrowing
        self.assertEqual(req.role, "ai-engineer")
        self.assertEqual(req.task_id, "sess-99")
        self.assertEqual(req.prompt, "요청 요약")
        # Four context channels survive the bridge.
        ctx = dict(req.context)
        self.assertEqual(ctx["role_profile"]["memory_role_filter"], "ai-engineer")
        self.assertEqual(ctx["topic_memory"]["topic_key"], "rag-pipeline")
        self.assertEqual(ctx["source_context"]["title"], "RAG basics")
        # Previous decisions ride on AgentRequest.references so
        # downstream runners can look them up without a custom field.
        self.assertEqual(len(req.references), 1)
        self.assertEqual(req.references[0]["role"], "tech-lead")


if __name__ == "__main__":
    unittest.main()
