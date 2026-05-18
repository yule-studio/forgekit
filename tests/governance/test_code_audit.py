"""Tests for `agents/governance/code_audit.py` — orchestrator audit SSoT.

본 모듈은 **stdlib only** 로 작성한다 — CI baseline 이
``python3 -m unittest discover`` 라서 third-party pytest 없이 import +
실행 가능해야 한다. 같은 회귀를 막기 위해 governance 테스트는
:mod:`unittest.TestCase` + :mod:`unittest.mock.patch` 만 사용.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.governance.code_audit import (
    FILE_SIZE_ALLOWLIST,
    SPLIT_LOC,
    SPLIT_NOW_PENDING,
    WARN_LOC,
    VERDICT_EXCEPTION,
    VERDICT_SAFE,
    VERDICT_SPLIT_NOW,
    VERDICT_SPLIT_PENDING,
    VERDICT_SPLIT_SOON,
    VERDICT_WARN,
    audit_orchestrator_file_sizes,
    detect_missing_worker_wiring,
    detect_mixed_responsibilities,
    detect_retryable_without_recovery,
    render_audit_summary,
)


# ---------------------------------------------------------------------------
# Local helpers — stdlib-only counterparts to pytest fixtures.
# ---------------------------------------------------------------------------


def _write(path: Path, *, lines: int, extra: str = "") -> None:
    body = "\n".join(["pass"] * lines)
    path.write_text(body + ("\n" + extra if extra else "") + "\n", encoding="utf-8")


def _patch_allowlist(mapping):
    """Module-level constant override — `patch.object` keeps cleanup
    automatic when the context exits / test method returns.
    """

    import yule_orchestrator.agents.governance.code_audit as code_audit_mod

    return patch.object(code_audit_mod, "FILE_SIZE_ALLOWLIST", mapping)


def _patch_pending(mapping):
    import yule_orchestrator.agents.governance.code_audit as code_audit_mod

    return patch.object(code_audit_mod, "SPLIT_NOW_PENDING", mapping)


# ---------------------------------------------------------------------------
# detect_mixed_responsibilities
# ---------------------------------------------------------------------------


class DetectMixedResponsibilitiesTests(unittest.TestCase):
    def test_returns_signals_present_in_text(self) -> None:
        text = """
        def route_engineering_message(msg):
            return None

        def render_runtime_status(s):
            save_session(s)
        """

        signals = detect_mixed_responsibilities(text=text)

        self.assertIn("routing", signals)
        self.assertIn("formatting", signals)
        self.assertIn("state_persistence", signals)
        # responsibilities 는 sorted-dedup
        self.assertEqual(list(signals), sorted(set(signals)))

    def test_returns_empty_for_neutral_text(self) -> None:
        text = "def add(a, b):\n    return a + b\n"
        self.assertEqual(detect_mixed_responsibilities(text=text), ())


# ---------------------------------------------------------------------------
# audit_orchestrator_file_sizes — synthetic repo
# ---------------------------------------------------------------------------


class AuditOrchestratorFileSizesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def test_splits_files_into_correct_verdict_buckets(self) -> None:
        base = self.tmp_path / "src" / "yule_orchestrator"
        base.mkdir(parents=True)
        _write(base / "tiny.py", lines=100)
        _write(base / "warn.py", lines=WARN_LOC + 5)
        # split_soon — 1000+ LOC 이지만 책임 signal 0 / 1 개
        _write(base / "split_soon.py", lines=SPLIT_LOC + 50)
        # split_now — 1000+ LOC + 책임 ≥ 2
        multi_resp_extra = (
            "def route_engineering_message(msg):\n    return None\n"
            "def render_runtime_status(s):\n    save_session(s)\n"
            "from yule_orchestrator.discord.bot import build_engineering_gateway_bot\n"
        )
        _write(
            base / "huge_multi.py",
            lines=SPLIT_LOC + 200,
            extra=multi_resp_extra,
        )

        audit = audit_orchestrator_file_sizes(
            repo_root=self.tmp_path, package_root="src/yule_orchestrator"
        )

        by_path = {row.path: row for row in audit.rows}
        self.assertEqual(
            by_path["src/yule_orchestrator/tiny.py"].verdict, VERDICT_SAFE
        )
        self.assertEqual(
            by_path["src/yule_orchestrator/warn.py"].verdict, VERDICT_WARN
        )
        self.assertEqual(
            by_path["src/yule_orchestrator/split_soon.py"].verdict,
            VERDICT_SPLIT_SOON,
        )
        huge = by_path["src/yule_orchestrator/huge_multi.py"]
        self.assertEqual(huge.verdict, VERDICT_SPLIT_NOW)
        self.assertGreaterEqual(len(huge.responsibilities), 2)

        self.assertTrue(audit.is_blocking())
        self.assertIn(huge, audit.violations)

    def test_honors_allowlist_with_explicit_reason(self) -> None:
        base = self.tmp_path / "src" / "yule_orchestrator"
        base.mkdir(parents=True)
        _write(base / "_legacy.py", lines=SPLIT_LOC + 500)

        with _patch_allowlist(
            {"src/yule_orchestrator/_legacy.py": "test allowlist reason"}
        ):
            audit = audit_orchestrator_file_sizes(
                repo_root=self.tmp_path, package_root="src/yule_orchestrator"
            )

        self.assertEqual(len(audit.allowed_exceptions), 1)
        row = audit.allowed_exceptions[0]
        self.assertEqual(row.verdict, VERDICT_EXCEPTION)
        self.assertEqual(row.reason, "test allowlist reason")
        self.assertFalse(audit.is_blocking())

    def test_returns_empty_for_missing_package_root(self) -> None:
        audit = audit_orchestrator_file_sizes(
            repo_root=self.tmp_path, package_root="src/yule_orchestrator"
        )
        self.assertEqual(audit.rows, ())
        self.assertFalse(audit.is_blocking())


# ---------------------------------------------------------------------------
# detect_missing_worker_wiring
# ---------------------------------------------------------------------------


class DetectMissingWorkerWiringTests(unittest.TestCase):
    def test_flags_declared_job_type_without_consumer(self) -> None:
        report = detect_missing_worker_wiring(
            declared_job_types=[
                "coding_execute",
                "approval_post",
                "github_work_order",
            ],
            kind_to_job_type={
                "CODING_EXECUTOR": "coding_execute",
                "APPROVAL_WORKER": "approval_post",
                # github_work_order 누락 → wiring miss
                "SUPERVISOR": None,
            },
        )

        self.assertEqual(report.unmapped_job_types, ("github_work_order",))
        self.assertTrue(report.is_blocking())

    def test_passes_when_all_job_types_mapped(self) -> None:
        report = detect_missing_worker_wiring(
            declared_job_types=["coding_execute", "approval_post"],
            kind_to_job_type={
                "CODING_EXECUTOR": "coding_execute",
                "APPROVAL_WORKER": "approval_post",
                "SUPERVISOR": None,
            },
        )
        self.assertEqual(report.unmapped_job_types, ())
        self.assertFalse(report.is_blocking())
        self.assertIn("coding_execute", report.mapped_job_types)
        self.assertIn("approval_post", report.mapped_job_types)


# ---------------------------------------------------------------------------
# detect_retryable_without_recovery
# ---------------------------------------------------------------------------


class DetectRetryableWithoutRecoveryTests(unittest.TestCase):
    def test_detects_uncovered_retryable_reason(self) -> None:
        report = detect_retryable_without_recovery(
            declared_retryable_reasons=[
                "work_order_no_repo",
                "github_app_token_expired",
            ],
            registered_recovery_reasons=["work_order_no_repo"],
        )

        self.assertEqual(report.uncovered_reasons, ("github_app_token_expired",))
        self.assertTrue(report.is_blocking())
        self.assertEqual(report.covered_reasons, ("work_order_no_repo",))

    def test_excludes_known_transient_reasons(self) -> None:
        report = detect_retryable_without_recovery(
            declared_retryable_reasons=["discord_rate_limited"],
            registered_recovery_reasons=[],
            known_transient=["discord_rate_limited"],
        )
        self.assertEqual(report.uncovered_reasons, ())
        self.assertFalse(report.is_blocking())


# ---------------------------------------------------------------------------
# render_audit_summary
# ---------------------------------------------------------------------------


class RenderAuditSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def test_shows_violations_then_warnings_then_exceptions(self) -> None:
        base = self.tmp_path / "src" / "yule_orchestrator"
        base.mkdir(parents=True)
        _write(
            base / "huge_multi.py",
            lines=SPLIT_LOC + 100,
            extra=(
                "def route_engineering_message(m):\n    return None\n"
                "def render_runtime_status(s):\n    save_session(s)\n"
            ),
        )
        _write(base / "warn.py", lines=WARN_LOC + 1)
        _write(base / "_legacy.py", lines=SPLIT_LOC + 500)

        with _patch_allowlist(
            {"src/yule_orchestrator/_legacy.py": "in-flight"}
        ):
            audit = audit_orchestrator_file_sizes(
                repo_root=self.tmp_path, package_root="src/yule_orchestrator"
            )
        summary = render_audit_summary(audit)

        self.assertIn("split_now 위반", summary)
        self.assertIn("huge_multi.py", summary)
        self.assertIn("warn.py", summary)
        self.assertIn("_legacy.py", summary)

    def test_handles_empty_audit(self) -> None:
        audit = audit_orchestrator_file_sizes(
            repo_root=Path("/nonexistent-root-for-audit"),
            package_root="src/yule_orchestrator",
        )
        summary = render_audit_summary(audit)
        self.assertIn("통과", summary)


# ---------------------------------------------------------------------------
# SPLIT_NOW_PENDING — deadline 까지 split_pending bucket, 지나면 violation.
# ---------------------------------------------------------------------------


class SplitNowPendingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _make_multi_resp_file(self, rel: str) -> None:
        base = self.tmp_path / "src" / "yule_orchestrator"
        base.mkdir(parents=True, exist_ok=True)
        _write(
            base / Path(rel).name,
            lines=SPLIT_LOC + 100,
            extra=(
                "def route_engineering_message(m):\n    return None\n"
                "def render_runtime_status(s):\n    save_session(s)\n"
            ),
        )

    def test_future_deadline_moves_to_pending_bucket(self) -> None:
        rel = "src/yule_orchestrator/big_pending.py"
        self._make_multi_resp_file(rel)

        with _patch_pending(
            {
                rel: {
                    "deadline": "2099-12-31",
                    "owner": "codwithyc",
                    "axes": "axis_a, axis_b",
                }
            }
        ):
            audit = audit_orchestrator_file_sizes(
                repo_root=self.tmp_path,
                package_root="src/yule_orchestrator",
                today=date(2026, 5, 17),
            )

        self.assertEqual(len(audit.split_pending), 1)
        row = audit.split_pending[0]
        self.assertEqual(row.path, rel)
        self.assertEqual(row.verdict, VERDICT_SPLIT_PENDING)
        self.assertIn("2099-12-31", row.reason)
        self.assertFalse(audit.is_blocking())

    def test_past_deadline_escalates_to_violation(self) -> None:
        rel = "src/yule_orchestrator/big_overdue.py"
        self._make_multi_resp_file(rel)

        with _patch_pending(
            {
                rel: {
                    "deadline": "2024-01-01",  # already past
                    "owner": "codwithyc",
                    "axes": "axis_a, axis_b",
                }
            }
        ):
            audit = audit_orchestrator_file_sizes(
                repo_root=self.tmp_path,
                package_root="src/yule_orchestrator",
                today=date(2026, 5, 17),
            )

        self.assertEqual(audit.split_pending, ())
        self.assertEqual(len(audit.violations), 1)
        self.assertEqual(audit.violations[0].path, rel)
        self.assertTrue(audit.is_blocking())

    def test_entries_all_have_required_fields(self) -> None:
        for path, meta in SPLIT_NOW_PENDING.items():
            with self.subTest(path=path):
                self.assertTrue(meta.get("deadline"))
                self.assertTrue(meta.get("owner"))
                self.assertTrue(meta.get("axes"))
                # deadline 은 ISO 형식 / valid 날짜.
                date.fromisoformat(meta["deadline"])


# ---------------------------------------------------------------------------
# Live SSoT — 실제 repo audit + runtime registry 와 wiring 회귀 차단.
# ---------------------------------------------------------------------------


class LiveRepoAuditTests(unittest.TestCase):
    def test_known_monoliths_classified_as_exception(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        audit = audit_orchestrator_file_sizes(repo_root=repo_root)
        by_path = {row.path: row for row in audit.rows}

        legacy_bot = by_path.get("src/yule_orchestrator/discord/bot/_legacy.py")
        self.assertIsNotNone(
            legacy_bot, "discord/bot/_legacy.py 가 audit 결과에 없음"
        )
        self.assertEqual(legacy_bot.verdict, VERDICT_EXCEPTION)

    def test_has_no_unsanctioned_violations(self) -> None:
        """현재 repo 에서 split_now 위반이 0 이어야 한다.

        위반이 잡히면 (a) 본 PR 에서 분리하거나 (b) SPLIT_NOW_PENDING 에
        deadline 명시 후 추가. 그것이 없으면 즉시 fail.
        """

        repo_root = Path(__file__).resolve().parents[2]
        audit = audit_orchestrator_file_sizes(repo_root=repo_root)
        self.assertEqual(audit.violations, (), render_audit_summary(audit))

    def test_allowlist_keys_exist_in_repo(self) -> None:
        """Allowlist 의 path 가 실존 파일이어야 함 — rename 시 stale
        exception 회귀 차단.
        """

        repo_root = Path(__file__).resolve().parents[2]
        for rel in FILE_SIZE_ALLOWLIST:
            with self.subTest(path=rel):
                self.assertTrue(
                    (repo_root / rel).is_file(), f"allowlist stale: {rel}"
                )


class LiveKindToJobTypeWiringTests(unittest.TestCase):
    def test_covers_all_declared_job_type_constants(self) -> None:
        """`JOB_TYPE_*` 상수 ↔ `_KIND_TO_JOB_TYPE` mapping 누락 회귀 차단.

        queue 에는 enqueue 되는데 ServiceKind 매핑이 없으면 consumer 가
        없는 것이므로 hard fail.
        """

        from yule_orchestrator.agents.job_queue.approval_worker import (
            JOB_TYPE_APPROVAL_POST,
        )
        from yule_orchestrator.agents.job_queue.coding_executor_worker import (
            JOB_TYPE_CODING_EXECUTE,
        )
        from yule_orchestrator.agents.job_queue.github_work_order import (
            JOB_TYPE_GITHUB_WORK_ORDER,
        )
        from yule_orchestrator.agents.job_queue.obsidian_writer_worker import (
            JOB_TYPE_OBSIDIAN_WRITE,
        )
        from yule_orchestrator.agents.job_queue.research_worker import (
            JOB_TYPE_RESEARCH_COLLECT,
        )
        from yule_orchestrator.agents.job_queue.role_take_worker import (
            JOB_TYPE_ROLE_TAKE,
        )
        from yule_orchestrator.runtime.status import _KIND_TO_JOB_TYPE

        declared = [
            JOB_TYPE_RESEARCH_COLLECT,
            JOB_TYPE_ROLE_TAKE,
            JOB_TYPE_APPROVAL_POST,
            JOB_TYPE_OBSIDIAN_WRITE,
            JOB_TYPE_CODING_EXECUTE,
            JOB_TYPE_GITHUB_WORK_ORDER,
        ]
        report = detect_missing_worker_wiring(
            declared_job_types=declared,
            kind_to_job_type=_KIND_TO_JOB_TYPE,
        )
        self.assertEqual(
            report.unmapped_job_types,
            (),
            "JOB_TYPE 상수 ↔ ServiceKind 매핑 누락 — "
            f"{report.unmapped_job_types} consumer 가 없음",
        )


if __name__ == "__main__":
    unittest.main()
