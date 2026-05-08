"""runtime.status_summary — A-M7 markdown formatter unit tests.

Pin the rendered markdown shape so a future M7.x ``#봇-상태``
poster can rely on the exact section headers.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.runtime.circuit_breaker import (
    CircuitBreakerPolicy,
    CircuitBreakerRegistry,
)
from yule_orchestrator.runtime.fallback import (
    FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
    build_fallback_audit_record,
    summarise_role_results,
)
from yule_orchestrator.runtime.status import (
    HEALTH_ALIVE,
    HEALTH_STALE,
    HEALTH_UNKNOWN,
    FailedJobSummary,
    JobTypeSummary,
    RuntimeStatusReport,
    ServiceStatus,
)
from yule_orchestrator.runtime.status_summary import (
    render_status_summary_markdown,
)


_NOW: float = 1_731_000_000.0


def _service(
    service_id: str,
    health: str,
    *,
    age: float = None,  # type: ignore[assignment]
    implemented: bool = True,
) -> ServiceStatus:
    return ServiceStatus(
        service_id=service_id,
        kind="research_worker",
        role=None,
        description="test",
        implemented=implemented,
        health=health,
        heartbeat_age_seconds=age,
        heartbeat_last_beat=None,
        pid=None,
        metadata={},
        job_type="research_collect",
    )


def _empty_report(services=()) -> RuntimeStatusReport:
    return RuntimeStatusReport(
        profile="engineering",
        generated_at=_NOW,
        deadline_seconds=90.0,
        services=tuple(services),
        job_types=(),
        failed_recent=(),
        warnings=(),
    )


class AllClearTests(unittest.TestCase):
    def test_empty_report_renders_all_clear_line(self) -> None:
        report = _empty_report(
            services=(_service("eng-research-worker", HEALTH_ALIVE, age=5.0),)
        )
        text = render_status_summary_markdown(
            report=report, circuits={}, fallbacks=()
        )
        self.assertIn("runtime status", text)
        self.assertIn("모든 서비스 alive", text)
        # No section headers when nothing's wrong.
        self.assertNotIn("Stale services", text)
        self.assertNotIn("Circuit-open", text)


class StaleAndUnknownSectionTests(unittest.TestCase):
    def test_stale_and_unknown_services_listed(self) -> None:
        report = _empty_report(
            services=(
                _service("eng-research-worker", HEALTH_STALE, age=900.0),
                _service("eng-approval-worker", HEALTH_UNKNOWN),
                _service("eng-role-tech-lead", HEALTH_ALIVE, age=3.0),
            )
        )
        text = render_status_summary_markdown(
            report=report, circuits={}, fallbacks=()
        )
        self.assertIn("### Stale services", text)
        self.assertIn("eng-research-worker", text)
        self.assertIn("eng-approval-worker", text)
        # Distinguishes stale vs unknown.
        self.assertIn("stale", text)
        self.assertIn("unknown", text)
        # alive service must not show up under stale.
        self.assertNotIn("`eng-role-tech-lead` — stale", text)


class CircuitSectionTests(unittest.TestCase):
    def test_open_circuits_rendered_with_count_and_reason(self) -> None:
        registry = CircuitBreakerRegistry(
            policy=CircuitBreakerPolicy(window_seconds=60.0, max_restarts=1)
        )
        for i in range(3):
            registry.record_restart(
                "eng-approval-worker",
                now=_NOW - 10.0 + i,
                reason=f"exit_code={i}",
            )
        report = _empty_report()
        text = render_status_summary_markdown(
            report=report,
            circuits=registry.snapshot(now=_NOW),
            fallbacks=(),
        )
        self.assertIn("### Circuit-open services", text)
        self.assertIn("eng-approval-worker", text)
        self.assertIn("circuit OPEN", text)
        # Count-in-window appears so the operator sees how
        # aggressive the breaker fired.
        self.assertIn("3 restarts", text)

    def test_no_circuit_section_when_nothing_open(self) -> None:
        registry = CircuitBreakerRegistry()
        registry.record_restart("eng-x", now=_NOW)
        text = render_status_summary_markdown(
            report=_empty_report(),
            circuits=registry.snapshot(now=_NOW),
            fallbacks=(),
        )
        self.assertNotIn("Circuit-open", text)


class FailedTerminalSectionTests(unittest.TestCase):
    def test_failed_terminal_jobs_render_with_error(self) -> None:
        report = RuntimeStatusReport(
            profile="engineering",
            generated_at=_NOW,
            deadline_seconds=90.0,
            services=(),
            job_types=(),
            failed_recent=(
                FailedJobSummary(
                    job_id="job-1",
                    job_type="role_take",
                    role="qa-engineer",
                    state="failed_terminal",
                    attempt=3,
                    age_seconds=60.0,
                    error="ProviderError: capacity exceeded",
                ),
                # failed_retryable should NOT show in this section.
                FailedJobSummary(
                    job_id="job-2",
                    job_type="approval_post",
                    role=None,
                    state="failed_retryable",
                    attempt=1,
                    age_seconds=10.0,
                    error="TimeoutError: post",
                ),
            ),
            warnings=(),
        )
        text = render_status_summary_markdown(
            report=report, circuits={}, fallbacks=()
        )
        self.assertIn("### Failed-terminal jobs", text)
        self.assertIn("job-1", text)
        self.assertIn("ProviderError: capacity exceeded", text)
        self.assertNotIn("job-2", text)


class FallbackSectionTests(unittest.TestCase):
    def test_fallback_records_render_with_approval_warning(self) -> None:
        notice = summarise_role_results(
            expected_roles=("tech-lead", "qa-engineer"),
            failed_roles=("tech-lead", "qa-engineer"),
        )
        record = build_fallback_audit_record(
            session_id="sess-fb-1",
            notice=notice,
            authority=FALLBACK_AUTHORITY_DETERMINISTIC_TEMPLATE,
            now=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
        text = render_status_summary_markdown(
            report=_empty_report(),
            circuits={},
            fallbacks=(record,),
        )
        self.assertIn("### Fallback / degrade events", text)
        self.assertIn("sess-fb-1", text)
        self.assertIn("deterministic_template", text)
        self.assertIn("human approval required", text)
        # Failed-role list surfaces.
        self.assertIn("tech-lead", text)
        self.assertIn("qa-engineer", text)


if __name__ == "__main__":
    unittest.main()
