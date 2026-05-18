"""P1-M E — Session recovery 일반화 (특정 session 한정 패치 금지).

특정 사고 session (canonical: ``fe5eedc65196``, ``11917bf1e75d``) 만 살리는
hard-coded 해킹 대신, **모든 session 에 동일하게 동작하는** recovery
함수를 제공.

회복 항목:
  1. ``work_mode`` / ``topology`` / ``scope`` / ``mode_decided_*``
     — session.prompt 에서 explicit 토큰 재파싱.
  2. ``coding_backlog`` — full-stack-single-repo + 검색 류 의도면
     deterministic 8-slice seeding (idempotent).
  3. ``pr_merge_*`` — operator 가 PR 메타 (repo / number / head_sha) 를
     hint 로 주면 stamp. (안 주면 그대로 두고 audit 만 남김.)

CLI (operator 가 한 줄로 실행):

  python3 -m yule_orchestrator.cli.recover_session <session_id> \\
       [--mode autonomous_merge|approval_required] \\
       [--pr-number N --pr-url URL --head-sha SHA --repo owner/name]

코드 SSoT — 본 모듈. recover_session_full 이 main entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional

from ..coding.coding_backlog_seed import (
    EXTRA_CODING_BACKLOG,
    seed_coding_backlog,
)
from .session_mode import (
    EXTRA_DECIDED_AT,
    EXTRA_DECIDED_BY,
    EXTRA_SCOPE,
    EXTRA_TOPOLOGY,
    EXTRA_WORK_MODE,
    SCOPE_DEFAULT,
    SCOPE_FULL_STACK,
    TOPOLOGY_DEFAULT,
    TOPOLOGY_SINGLE,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
    WORK_MODE_DEFAULT,
    DECIDED_BY_USER,
    parse_mode_hints,
)


@dataclass(frozen=True)
class RecoveryReport:
    """recover_session_full 의 결과 — operator 가 한눈에 본다."""

    session_id: str
    found: bool = False
    mode_persisted: bool = False
    backlog_seeded_count: int = 0
    pr_merge_stamped: bool = False
    notes: tuple = field(default_factory=tuple)
    work_mode: Optional[str] = None
    topology: Optional[str] = None
    scope: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def recover_session_full(
    *,
    session_id: str,
    explicit_work_mode: Optional[str] = None,
    explicit_topology: Optional[str] = None,
    explicit_scope: Optional[str] = None,
    pr_number: Optional[int] = None,
    pr_url: Optional[str] = None,
    head_sha: Optional[str] = None,
    repo_full_name: Optional[str] = None,
    base_branch: str = "main",
) -> RecoveryReport:
    """session.extra 를 새 wiring 호환 상태로 회복.

    parse_mode_hints 를 session.prompt 에 실행 → explicit 토큰 추출.
    explicit_* 인자 가 주어지면 그것이 우선. backlog 는
    ``seed_coding_backlog`` (idempotent). PR 메타가 인자로 주어지면
    pr_merge_stage = pr_merge_pending 으로 stamp.
    """

    try:
        from dataclasses import replace as _replace
        from ..workflow_state import load_session, update_session
    except Exception:  # noqa: BLE001 - partial install
        return RecoveryReport(
            session_id=session_id, notes=("workflow_state import 실패",)
        )

    try:
        session = load_session(session_id)
    except Exception:  # noqa: BLE001
        return RecoveryReport(
            session_id=session_id, notes=("load_session raised",)
        )
    if session is None:
        return RecoveryReport(
            session_id=session_id, notes=("session not found",)
        )

    notes: List[str] = []
    extra = dict(getattr(session, "extra", None) or {})
    prompt = str(getattr(session, "prompt", "") or "")

    # 1. work_mode / topology / scope 영속화 — explicit 인자 > prompt 파싱 > 기존값
    hints = parse_mode_hints(prompt)

    final_work_mode = (
        explicit_work_mode
        or hints.get("work_mode")
        or extra.get(EXTRA_WORK_MODE)
    )
    final_topology = (
        explicit_topology
        or hints.get("topology")
        or extra.get(EXTRA_TOPOLOGY)
    )
    final_scope = (
        explicit_scope
        or hints.get("scope")
        or extra.get(EXTRA_SCOPE)
    )

    # explicit 또는 prompt 가 뭔가 알려줬으면 기록 (자동 default 로 빠지지
    # 않게).  prompt 도 비어있으면 기존 값 보존 (덮어쓰기 금지).
    new_extra = dict(extra)
    mode_changed = False
    if final_work_mode and final_work_mode != extra.get(EXTRA_WORK_MODE):
        new_extra[EXTRA_WORK_MODE] = final_work_mode
        mode_changed = True
    if final_topology and final_topology != extra.get(EXTRA_TOPOLOGY):
        new_extra[EXTRA_TOPOLOGY] = final_topology
        mode_changed = True
    if final_scope and final_scope != extra.get(EXTRA_SCOPE):
        new_extra[EXTRA_SCOPE] = final_scope
        mode_changed = True
    if mode_changed:
        new_extra[EXTRA_DECIDED_BY] = DECIDED_BY_USER
        new_extra[EXTRA_DECIDED_AT] = _now_iso()
        notes.append(
            f"mode persist 보정 → work_mode={final_work_mode} "
            f"topology={final_topology} scope={final_scope}"
        )
    else:
        notes.append("mode 키 변경 없음 (이미 영속됨 또는 단서 부족)")

    # 2. PR 메타 stamp — operator 가 hint 로 줬을 때만
    pr_merge_stamped = False
    if pr_number and pr_url and repo_full_name:
        from ..job_queue.pr_merge_continuation import (
            EXTRA_PR_MERGE_BASE_BRANCH,
            EXTRA_PR_MERGE_DECIDED_AT,
            EXTRA_PR_MERGE_HEAD_SHA,
            EXTRA_PR_MERGE_PR_NUMBER,
            EXTRA_PR_MERGE_PR_URL,
            EXTRA_PR_MERGE_REASON,
            EXTRA_PR_MERGE_REPO,
            EXTRA_PR_MERGE_STAGE,
            STAGE_PR_MERGE_PENDING,
        )

        new_extra[EXTRA_PR_MERGE_STAGE] = STAGE_PR_MERGE_PENDING
        new_extra[EXTRA_PR_MERGE_PR_NUMBER] = int(pr_number)
        new_extra[EXTRA_PR_MERGE_PR_URL] = str(pr_url)
        new_extra[EXTRA_PR_MERGE_REPO] = str(repo_full_name)
        new_extra[EXTRA_PR_MERGE_HEAD_SHA] = str(head_sha or "")
        new_extra[EXTRA_PR_MERGE_BASE_BRANCH] = str(base_branch or "main")
        new_extra[EXTRA_PR_MERGE_DECIDED_AT] = _now_iso()
        new_extra[EXTRA_PR_MERGE_REASON] = (
            f"recovered_for_{final_work_mode or WORK_MODE_DEFAULT}"
        )
        pr_merge_stamped = True
        notes.append(f"pr_merge 메타 stamp 완료 (PR #{int(pr_number)})")

    # 3. workflow_state persist (mode + pr_merge meta)
    try:
        updated = _replace(session, extra=new_extra)
        update_session(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        notes.append("update_session 저장 실패")
        return RecoveryReport(
            session_id=session_id,
            found=True,
            mode_persisted=False,
            pr_merge_stamped=False,
            notes=tuple(notes),
        )

    # 4. coding_backlog seed (idempotent)
    seeded = seed_coding_backlog(
        session_id=session_id, seeded_by="recovery_cli"
    )
    seeded_count = len(seeded) if seeded else 0
    if seeded_count > 0:
        notes.append(f"coding_backlog {seeded_count} slice seed 완료")
    else:
        existing = new_extra.get(EXTRA_CODING_BACKLOG) or ()
        if existing:
            notes.append(
                f"coding_backlog 이미 {len(existing)} slice — 보존"
            )
        else:
            notes.append(
                "coding_backlog seeding 안 함 (의도 detect 실패 — "
                "operator 가 직접 채우거나 prompt 보강 필요)"
            )

    return RecoveryReport(
        session_id=session_id,
        found=True,
        mode_persisted=mode_changed
        or any(
            new_extra.get(key)
            for key in (EXTRA_WORK_MODE, EXTRA_TOPOLOGY, EXTRA_SCOPE)
        ),
        backlog_seeded_count=seeded_count,
        pr_merge_stamped=pr_merge_stamped,
        notes=tuple(notes),
        work_mode=new_extra.get(EXTRA_WORK_MODE),
        topology=new_extra.get(EXTRA_TOPOLOGY),
        scope=new_extra.get(EXTRA_SCOPE),
    )


__all__ = (
    "RecoveryReport",
    "recover_session_full",
)
