"""Tests for `agents/governance/code_audit.py` — orchestrator audit SSoT."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

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
# detect_mixed_responsibilities
# ---------------------------------------------------------------------------


def test_detect_mixed_responsibilities_returns_signals_present_in_text() -> None:
    text = """
    def route_engineering_message(msg):
        return None

    def render_runtime_status(s):
        save_session(s)
    """

    signals = detect_mixed_responsibilities(text=text)

    assert "routing" in signals
    assert "formatting" in signals
    assert "state_persistence" in signals
    # responsibilities 는 sorted-dedup
    assert list(signals) == sorted(set(signals))


def test_detect_mixed_responsibilities_returns_empty_for_neutral_text() -> None:
    text = "def add(a, b):\n    return a + b\n"
    assert detect_mixed_responsibilities(text=text) == ()


# ---------------------------------------------------------------------------
# audit_orchestrator_file_sizes — synthetic repo
# ---------------------------------------------------------------------------


def _write(path: Path, *, lines: int, extra: str = "") -> None:
    body = "\n".join(["pass"] * lines)
    path.write_text(body + ("\n" + extra if extra else "") + "\n", encoding="utf-8")


def test_audit_splits_files_into_correct_verdict_buckets(tmp_path: Path) -> None:
    base = tmp_path / "src" / "yule_orchestrator"
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
    _write(base / "huge_multi.py", lines=SPLIT_LOC + 200, extra=multi_resp_extra)

    audit = audit_orchestrator_file_sizes(
        repo_root=tmp_path, package_root="src/yule_orchestrator"
    )

    by_path = {row.path: row for row in audit.rows}
    assert by_path["src/yule_orchestrator/tiny.py"].verdict == VERDICT_SAFE
    assert by_path["src/yule_orchestrator/warn.py"].verdict == VERDICT_WARN
    assert (
        by_path["src/yule_orchestrator/split_soon.py"].verdict == VERDICT_SPLIT_SOON
    )
    huge = by_path["src/yule_orchestrator/huge_multi.py"]
    assert huge.verdict == VERDICT_SPLIT_NOW
    assert len(huge.responsibilities) >= 2

    assert audit.is_blocking() is True
    assert huge in audit.violations


def test_audit_honors_allowlist_with_explicit_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "src" / "yule_orchestrator"
    base.mkdir(parents=True)
    _write(base / "_legacy.py", lines=SPLIT_LOC + 500)

    monkeypatch.setattr(
        "yule_orchestrator.agents.governance.code_audit.FILE_SIZE_ALLOWLIST",
        {"src/yule_orchestrator/_legacy.py": "test allowlist reason"},
    )

    audit = audit_orchestrator_file_sizes(
        repo_root=tmp_path, package_root="src/yule_orchestrator"
    )

    [row] = audit.allowed_exceptions
    assert row.verdict == VERDICT_EXCEPTION
    assert row.reason == "test allowlist reason"
    assert audit.is_blocking() is False


def test_audit_returns_empty_for_missing_package_root(tmp_path: Path) -> None:
    audit = audit_orchestrator_file_sizes(
        repo_root=tmp_path, package_root="src/yule_orchestrator"
    )
    assert audit.rows == ()
    assert audit.is_blocking() is False


# ---------------------------------------------------------------------------
# detect_missing_worker_wiring
# ---------------------------------------------------------------------------


def test_missing_wiring_flags_declared_job_type_without_consumer() -> None:
    report = detect_missing_worker_wiring(
        declared_job_types=["coding_execute", "approval_post", "github_work_order"],
        kind_to_job_type={
            "CODING_EXECUTOR": "coding_execute",
            "APPROVAL_WORKER": "approval_post",
            # github_work_order 누락 → wiring miss
            "SUPERVISOR": None,
        },
    )

    assert report.unmapped_job_types == ("github_work_order",)
    assert report.is_blocking() is True


def test_missing_wiring_passes_when_all_job_types_mapped() -> None:
    report = detect_missing_worker_wiring(
        declared_job_types=["coding_execute", "approval_post"],
        kind_to_job_type={
            "CODING_EXECUTOR": "coding_execute",
            "APPROVAL_WORKER": "approval_post",
            "SUPERVISOR": None,
        },
    )
    assert report.unmapped_job_types == ()
    assert report.is_blocking() is False
    assert "coding_execute" in report.mapped_job_types
    assert "approval_post" in report.mapped_job_types


# ---------------------------------------------------------------------------
# detect_retryable_without_recovery
# ---------------------------------------------------------------------------


def test_recovery_gap_detects_uncovered_retryable_reason() -> None:
    report = detect_retryable_without_recovery(
        declared_retryable_reasons=[
            "work_order_no_repo",
            "github_app_token_expired",
        ],
        registered_recovery_reasons=["work_order_no_repo"],
    )

    assert report.uncovered_reasons == ("github_app_token_expired",)
    assert report.is_blocking() is True
    assert report.covered_reasons == ("work_order_no_repo",)


def test_recovery_gap_excludes_known_transient_reasons() -> None:
    report = detect_retryable_without_recovery(
        declared_retryable_reasons=["discord_rate_limited"],
        registered_recovery_reasons=[],
        known_transient=["discord_rate_limited"],
    )
    assert report.uncovered_reasons == ()
    assert report.is_blocking() is False


# ---------------------------------------------------------------------------
# render_audit_summary
# ---------------------------------------------------------------------------


def test_render_audit_summary_shows_violations_then_warnings_then_exceptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "src" / "yule_orchestrator"
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

    monkeypatch.setattr(
        "yule_orchestrator.agents.governance.code_audit.FILE_SIZE_ALLOWLIST",
        {"src/yule_orchestrator/_legacy.py": "in-flight"},
    )

    audit = audit_orchestrator_file_sizes(
        repo_root=tmp_path, package_root="src/yule_orchestrator"
    )
    summary = render_audit_summary(audit)

    assert "split_now 위반" in summary
    assert "huge_multi.py" in summary
    assert "warn.py" in summary
    assert "_legacy.py" in summary


def test_render_audit_summary_handles_empty_audit() -> None:
    audit = audit_orchestrator_file_sizes(
        repo_root=Path("/nonexistent-root-for-audit"),
        package_root="src/yule_orchestrator",
    )
    summary = render_audit_summary(audit)
    assert "통과" in summary


# ---------------------------------------------------------------------------
# Live SSoT regression — real repo audit must surface known monoliths.
# 본 테스트가 깨지면 실제 repo 가 정책을 어긴 것 (회귀) 이므로 fail 의도.
# ---------------------------------------------------------------------------


def test_live_repo_audit_known_monoliths_classified() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    audit = audit_orchestrator_file_sizes(repo_root=repo_root)
    by_path = {row.path: row for row in audit.rows}

    legacy_bot = by_path.get("src/yule_orchestrator/discord/bot/_legacy.py")
    assert legacy_bot is not None, "discord/bot/_legacy.py 가 audit 결과에 없음"
    assert legacy_bot.verdict == VERDICT_EXCEPTION


def test_live_repo_audit_has_no_unsanctioned_violations() -> None:
    """현재 repo 에서 split_now 위반이 0 이어야 한다.

    위반이 잡히면 (a) 본 PR 에서 분리하거나 (b) SPLIT_NOW_PENDING 에
    deadline 명시 후 추가. 그것이 없으면 즉시 fail.
    """

    repo_root = Path(__file__).resolve().parents[2]
    audit = audit_orchestrator_file_sizes(repo_root=repo_root)
    assert audit.violations == (), render_audit_summary(audit)


def test_allowlist_keys_exist_in_repo() -> None:
    """Allowlist 의 path 가 실존 파일이어야 함 — rename 시 stale exception 회귀."""
    repo_root = Path(__file__).resolve().parents[2]
    for rel in FILE_SIZE_ALLOWLIST:
        assert (repo_root / rel).is_file(), f"allowlist stale: {rel}"


# ---------------------------------------------------------------------------
# SPLIT_NOW_PENDING — deadline 까지 split_pending bucket, 지나면 violation.
# ---------------------------------------------------------------------------


def test_split_now_pending_with_future_deadline_moves_to_pending_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "src" / "yule_orchestrator"
    base.mkdir(parents=True)
    rel = "src/yule_orchestrator/big_pending.py"
    _write(
        base / "big_pending.py",
        lines=SPLIT_LOC + 100,
        extra=(
            "def route_engineering_message(m):\n    return None\n"
            "def render_runtime_status(s):\n    save_session(s)\n"
        ),
    )
    monkeypatch.setattr(
        "yule_orchestrator.agents.governance.code_audit.SPLIT_NOW_PENDING",
        {
            rel: {
                "deadline": "2099-12-31",
                "owner": "codwithyc",
                "axes": "axis_a, axis_b",
            }
        },
    )

    audit = audit_orchestrator_file_sizes(
        repo_root=tmp_path,
        package_root="src/yule_orchestrator",
        today=date(2026, 5, 17),
    )

    [row] = audit.split_pending
    assert row.path == rel
    assert row.verdict == VERDICT_SPLIT_PENDING
    assert "2099-12-31" in row.reason
    assert audit.is_blocking() is False


def test_split_now_pending_past_deadline_escalates_to_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "src" / "yule_orchestrator"
    base.mkdir(parents=True)
    rel = "src/yule_orchestrator/big_overdue.py"
    _write(
        base / "big_overdue.py",
        lines=SPLIT_LOC + 100,
        extra=(
            "def route_engineering_message(m):\n    return None\n"
            "def render_runtime_status(s):\n    save_session(s)\n"
        ),
    )
    monkeypatch.setattr(
        "yule_orchestrator.agents.governance.code_audit.SPLIT_NOW_PENDING",
        {
            rel: {
                "deadline": "2024-01-01",  # already past
                "owner": "codwithyc",
                "axes": "axis_a, axis_b",
            }
        },
    )

    audit = audit_orchestrator_file_sizes(
        repo_root=tmp_path,
        package_root="src/yule_orchestrator",
        today=date(2026, 5, 17),
    )

    assert audit.split_pending == ()
    [row] = audit.violations
    assert row.path == rel
    assert audit.is_blocking() is True


def test_split_now_pending_entries_all_have_required_fields() -> None:
    for path, meta in SPLIT_NOW_PENDING.items():
        assert "deadline" in meta and meta["deadline"], path
        assert "owner" in meta and meta["owner"], path
        assert "axes" in meta and meta["axes"], path
        # deadline 은 ISO 형식 / valid 날짜.
        date.fromisoformat(meta["deadline"])


# ---------------------------------------------------------------------------
# Live SSoT — 실제 runtime registry 와 wiring 회귀 차단.
# ---------------------------------------------------------------------------


def test_live_kind_to_job_type_covers_all_declared_job_type_constants() -> None:
    """`JOB_TYPE_*` 상수 ↔ `_KIND_TO_JOB_TYPE` mapping 누락 회귀 차단.

    queue 에는 enqueue 되는데 ServiceKind 매핑이 없으면 consumer 가 없는
    것이므로 hard fail.
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
    assert report.unmapped_job_types == (), (
        "JOB_TYPE 상수 ↔ ServiceKind 매핑 누락 — "
        f"{report.unmapped_job_types} consumer 가 없음"
    )
