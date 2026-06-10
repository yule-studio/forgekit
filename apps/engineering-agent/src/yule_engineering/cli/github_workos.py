"""`yule github` subcommand handlers — G6 integration seam.

Wires the G1 doctor, G2 triage, G3 plan/writer, and the G6 live smoke
behind a single CLI surface so an operator drives the full pipeline
with three commands (``doctor`` / ``triage`` / ``plan-pr`` /
``smoke-pr``). Module is **pure-Python**: no Discord bridging, no
Obsidian write — those flows are exercised dry-run as part of the
smoke and stay out of the CLI itself.

Secret hygiene rules every helper here follows:

  * Never echo the installation token, the Authorization header, or
    the pem bytes. The doctor's redactor (``redact_secret_like``) is
    applied to every error string before it's printed.
  * Never write secret-shaped text into the smoke marker file —
    only metadata (issue link / audit id / timestamp).
  * Refuse to operate on a protected branch (``main``/``master``/
    etc.) regardless of input — the `is_protected_branch` guard from
    G3 is layered on top of the writer's policy gate.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ..agents.github_workos.audit import redact_secrets
from ..agents.github_workos.branching import (
    derive_branch_name,
    is_protected_branch,
)
from ..agents.github_workos.issue_context import (
    build_request_from_github_issue,
    redact_secret_like,
)
from ..agents.github_workos.models import RoleWorkOrder, TriagePlan
from ..agents.github_workos.pr_template import render_pr_body
from ..agents.github_workos.triage import senior_triage


# ---------------------------------------------------------------------------
# G2 ↔ G3 Protocol adapter
# ---------------------------------------------------------------------------
#
# G2's :class:`TriagePlan` and G3's modules (:mod:`branching`,
# :mod:`pr_template`, :mod:`actions`) ship slightly different field
# names — G2 calls the section ``scope`` / ``non_scope`` / ``decisions``
# / ``role_work_orders`` while G3's :class:`TriagePlanLike` Protocol
# spells them ``in_scope`` / ``out_of_scope`` / ``approvals_needed`` /
# ``work_orders``. Each side is internally consistent + tested in
# isolation; G6's job is to bridge them so a single ``yule github
# plan-pr`` / ``smoke-pr`` flow can drive both.
#
# Strategy: a small adapter that exposes both vocabularies (so the G3
# Protocol getters see what they expect) and copies through the few
# enrichments the CLI knows (issue number / repo / session id /
# rendered title + body / labels / trace links).
#
# Keeping the adapter inside the CLI module — not the public
# ``agents.github_workos`` API — avoids leaking the schema-bridge into
# the runtime where it could mask future model rename drift.


@dataclass(frozen=True)
class _G3PlanAdapter:
    """Adapter exposing G2 :class:`TriagePlan` with G3 field aliases.

    Construct via :func:`_adapt_plan_for_g3`. Consumers call
    ``getattr`` and read the aliases (``in_scope``, ``out_of_scope``,
    …) without knowing they came from a G2 plan.
    """

    g2: TriagePlan
    title: str
    body: str
    issue_number: Optional[int]
    session_id: Optional[str]
    repo: Optional[str]
    base_branch: str
    source: str

    # ----- G3 field aliases ------------------------------------------------
    @property
    def primary_role(self) -> str:
        return self.g2.primary_role

    @property
    def autonomy_level(self) -> str:
        # G3 reads ``autonomy_level`` as a plain string for ranking;
        # G2 stores a :class:`PermissionLevel` enum so we surface the
        # ``L1`` / ``L2`` / ``L3`` short label most G3 callers grep for.
        raw = getattr(self.g2, "autonomy_level", None)
        value = getattr(raw, "value", str(raw))
        # value comes through as e.g. ``L2_PLAN`` — keep the level
        # prefix so G3's ``_autonomy_rank`` can match.
        return str(value).split("_", 1)[0]

    @property
    def in_scope(self) -> Sequence[str]:
        return tuple(self.g2.scope)

    @property
    def out_of_scope(self) -> Sequence[str]:
        return tuple(self.g2.non_scope)

    @property
    def test_plan(self) -> Sequence[str]:
        return tuple(self.g2.test_plan)

    @property
    def risks(self) -> Sequence[str]:
        return tuple(self.g2.hidden_risks)

    @property
    def approvals_needed(self) -> Sequence[str]:
        return tuple(self.g2.approval_required_actions)

    @property
    def work_orders(self) -> Sequence[Mapping[str, str]]:
        # G3's pr_template expects each order as a mapping with
        # ``autonomy_level`` / ``action`` / ``target`` keys. G2's
        # role_work_orders carry ``role`` / ``mission`` / ``expected_output``;
        # we project the most informative pair so the rendered "agent
        # work orders" block is non-empty when G2 produced any.
        out: list[Mapping[str, str]] = []
        for order in self.g2.role_work_orders or ():
            if not isinstance(order, RoleWorkOrder):
                continue
            out.append(
                {
                    "autonomy_level": self.autonomy_level,
                    "action": f"{order.role}: {order.mission}",
                    "target": order.expected_output,
                }
            )
        return out

    @property
    def labels(self) -> Sequence[str]:
        # G2 has no labels field. Production callers may layer labels
        # via ``additional_labels`` on build_github_action_plan; the CLI
        # just exposes an empty tuple here so the action plan honours
        # whatever the caller injects.
        return ()

    @property
    def excluded_roles(self) -> Sequence[str]:
        return tuple(self.g2.excluded_roles)

    @property
    def support_roles(self) -> Sequence[str]:
        return tuple(self.g2.support_roles)

    @property
    def rationale_by_role(self) -> Mapping[str, str]:
        return dict(self.g2.rationale_by_role)

    @property
    def request_type(self) -> str:
        return self.g2.request_type

    @property
    def coding_required(self) -> bool:
        return bool(self.g2.coding_required)

    @property
    def approval_required_before_write(self) -> bool:
        return bool(self.g2.approval_required_before_write)

    @property
    def suggested_branch(self) -> str:
        return self.g2.suggested_branch


def _adapt_plan_for_g3(
    plan: TriagePlan,
    *,
    title: str,
    body: str,
    issue_number: Optional[int],
    session_id: Optional[str],
    repo: Optional[str],
    base_branch: str = "main",
    source: str = "github",
) -> _G3PlanAdapter:
    """Wrap a G2 :class:`TriagePlan` so G3 modules see their schema."""

    return _G3PlanAdapter(
        g2=plan,
        title=title,
        body=body,
        issue_number=issue_number,
        session_id=session_id,
        repo=repo,
        base_branch=base_branch,
        source=source,
    )
from ..github_app.config import GitHubAppConfig, GitHubAppConfigError
from ..github_app.doctor import (
    CHECK_STATUS_FAIL,
    CHECK_STATUS_OK,
    CHECK_STATUS_SKIP,
    CHECK_STATUS_WARN,
    DOCTOR_OVERALL_FAIL,
    DOCTOR_OVERALL_OK,
    DOCTOR_OVERALL_WARN,
    doctor as _run_doctor,
)


logger = logging.getLogger(__name__)


__all__ = (
    "run_github_doctor_command",
    "run_github_triage_command",
    "run_github_plan_pr_command",
    "run_github_smoke_pr_command",
)


# ---------------------------------------------------------------------------
# Helpers — env load / repo coordinates / safe printing
# ---------------------------------------------------------------------------


_PEM_PATTERN_KEY = "private_key_path"


def _print_err(message: str) -> None:
    """Write to stderr without a stack trace. Doctor / triage / smoke
    failures should leave the operator with one actionable line.
    """

    sys.stderr.write(message.rstrip() + "\n")
    sys.stderr.flush()


def _redact_for_console(value: Any) -> Any:
    """Apply the audit redactor to any payload before console output."""

    return redact_secrets(value)


def _config_or_die() -> Optional[GitHubAppConfig]:
    """Load + validate the env contract, printing a friendly error
    when the env is incomplete.
    """

    try:
        return GitHubAppConfig.from_env()
    except GitHubAppConfigError as exc:
        key = getattr(exc, "key", None)
        hint = (
            f"  → fix env key {key}" if isinstance(key, str) and key else ""
        )
        _print_err(f"github 환경변수 검증 실패: {exc}\n{hint}")
        return None


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def _format_doctor_human(result: Any) -> str:
    """Render :class:`DoctorResult` for the standard doctor output."""

    overall_label = {
        DOCTOR_OVERALL_OK: "✅ OK",
        DOCTOR_OVERALL_WARN: "⚠️ WARN",
        DOCTOR_OVERALL_FAIL: "❌ FAIL",
    }.get(result.overall, result.overall.upper())

    lines: list[str] = [
        f"github doctor — overall: {overall_label}"
        f" (live={'yes' if result.live else 'no'})",
    ]
    for check in result.checks:
        prefix = {
            CHECK_STATUS_OK: "✅",
            CHECK_STATUS_WARN: "⚠️ ",
            CHECK_STATUS_FAIL: "❌",
            CHECK_STATUS_SKIP: "⏭ ",
        }.get(check.status, check.status)
        message = redact_secret_like(str(check.message))
        lines.append(f"  {prefix} {check.name}: {message}")
        if check.detail:
            for key, value in sorted(check.detail.items()):
                lines.append(f"      · {key}: {redact_secret_like(str(value))}")
    return "\n".join(lines)


def run_github_doctor_command(
    *,
    json_output: bool = False,
    live: bool = False,
) -> int:
    """`yule github doctor [--json] [--live]` entry point.

    Exit codes:

      * 0 — overall OK or WARN (operator may still need to address).
      * 1 — overall FAIL (deal-breaker; live calls would fail too).
      * 2 — env contract incomplete (config missing).
    """

    try:
        result = _run_doctor(live=live)
    except GitHubAppConfigError as exc:
        _print_err(f"github 환경변수 검증 실패: {exc}")
        return 2

    if json_output:
        payload = _redact_for_console(result.to_payload())
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_doctor_human(result))

    if result.overall == DOCTOR_OVERALL_FAIL:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------


def _fetch_issue_payload(
    *, repo: str, issue_number: int
) -> Optional[Mapping[str, Any]]:
    """Best-effort issue fetch using the on-host gh CLI.

    The gh CLI is already authenticated (G0 dependency) and produces
    GitHub-shaped JSON, which means the dry-run paths can read real
    issue context without minting an installation token. When gh is
    not available the caller falls back to a synthetic stub (smoke
    flow uses a smoke marker title/body).
    """

    import subprocess

    try:
        completed = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue_number),
                "--repo",
                repo,
                "--json",
                "number,title,body,labels,author,url,state",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except FileNotFoundError:
        return None
    if completed.returncode != 0:
        _print_err(
            f"gh issue view failed (rc={completed.returncode}): "
            + redact_secret_like(completed.stderr.strip().splitlines()[0])
            if completed.stderr
            else f"gh issue view failed (rc={completed.returncode})"
        )
        return None
    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        return None
    if isinstance(payload, dict):
        # `gh issue view` returns GraphQL-shaped keys (`author`, `url`),
        # while the WorkOS issue boundary consumes REST/webhook-shaped keys
        # (`user`, `html_url`). Normalize here so the rest of the pipeline
        # stays independent from the CLI transport.
        author = payload.get("author")
        if "user" not in payload and isinstance(author, Mapping):
            payload["user"] = author
        url = payload.get("url")
        if "html_url" not in payload and isinstance(url, str):
            payload["html_url"] = url
    return payload


def _triage_plan_payload(
    plan: TriagePlan, *, issue_number: Optional[int] = None
) -> Mapping[str, Any]:
    """JSON-friendly snapshot of a G2 TriagePlan for `--json` output."""

    work_orders: list[Mapping[str, Any]] = []
    for order in plan.role_work_orders or ():
        if not isinstance(order, RoleWorkOrder):
            continue
        work_orders.append(
            {
                "role": order.role,
                "mission": order.mission,
                "expected_output": order.expected_output,
                "files_or_domains_to_inspect": list(
                    order.files_or_domains_to_inspect
                ),
                "done_criteria": list(order.done_criteria),
                "handoff_to_next_role": order.handoff_to_next_role,
            }
        )
    return {
        "issue_number": issue_number,
        "request_type": plan.request_type,
        "primary_role": plan.primary_role,
        "support_roles": list(plan.support_roles),
        "excluded_roles": list(plan.excluded_roles),
        "rationale_by_role": dict(plan.rationale_by_role),
        "scope": list(plan.scope),
        "non_scope": list(plan.non_scope),
        "hidden_risks": list(plan.hidden_risks),
        "assumptions": list(plan.assumptions),
        "implementation_steps": list(plan.implementation_steps),
        "test_plan": list(plan.test_plan),
        "approval_required_actions": list(plan.approval_required_actions),
        "suggested_branch": plan.suggested_branch,
        "coding_required": bool(plan.coding_required),
        "approval_required_before_write": bool(
            plan.approval_required_before_write
        ),
        "decisions": list(plan.decisions),
        "risk_level": plan.risk_level.value,
        "autonomy_level": plan.autonomy_level.value,
        "role_work_orders": work_orders,
    }


def _format_triage_human(
    plan: TriagePlan, *, issue_number: Optional[int] = None
) -> str:
    head = (
        f"issue #{issue_number}"
        if issue_number is not None
        else "(no issue ref)"
    )
    lines = [
        f"github triage — {head}",
        f"  request_type: {plan.request_type} "
        f"(coding_required={plan.coding_required})",
        f"  autonomy: {plan.autonomy_level.value} · "
        f"risk: {plan.risk_level.value}",
        f"  primary_role: {plan.primary_role}",
        f"  support_roles: {', '.join(plan.support_roles) or '(none)'}",
        f"  excluded_roles: {', '.join(plan.excluded_roles) or '(none)'}",
        f"  suggested_branch: {plan.suggested_branch}",
        f"  approval_required_before_write: {plan.approval_required_before_write}",
    ]
    if plan.scope:
        lines.append("  scope:")
        for item in plan.scope:
            lines.append(f"    - {item}")
    if plan.non_scope:
        lines.append("  non_scope:")
        for item in plan.non_scope:
            lines.append(f"    - {item}")
    if plan.hidden_risks:
        lines.append("  hidden_risks:")
        for item in plan.hidden_risks:
            lines.append(f"    - {item}")
    if plan.test_plan:
        lines.append("  test_plan:")
        for item in plan.test_plan:
            lines.append(f"    - {item}")
    if plan.decisions:
        lines.append("  decisions:")
        for item in plan.decisions:
            lines.append(f"    - {item}")
    return "\n".join(lines)


def run_github_triage_command(
    issue_number: int,
    *,
    dry_run: bool = True,
    json_output: bool = False,
    repo: Optional[str] = None,
) -> int:
    """`yule github triage <issue> --dry-run [--json]`."""

    if not dry_run:
        _print_err(
            "triage 는 현 단계에서 --dry-run 만 지원합니다 (정책 결정 + 미리보기). "
            "실제 issue 수정은 plan-pr → smoke-pr 또는 별도 승인 플로우를 통해 수행하세요."
        )
        return 2

    cfg = _config_or_die()
    if cfg is None:
        return 2
    repo_full = repo or cfg.repo_full_name

    payload = _fetch_issue_payload(repo=repo_full, issue_number=issue_number)
    if payload is None:
        _print_err(
            f"issue #{issue_number} 를 가져오지 못했습니다 (gh CLI 미설치 / 권한 부족 / 미공개 repo)."
        )
        return 1

    request = build_request_from_github_issue(payload)
    plan = senior_triage(request)

    if json_output:
        print(
            json.dumps(
                _redact_for_console(
                    _triage_plan_payload(plan, issue_number=issue_number)
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(_format_triage_human(plan, issue_number=issue_number))
    return 0


# ---------------------------------------------------------------------------
# plan-pr
# ---------------------------------------------------------------------------


def _format_plan_pr_human(
    *,
    plan: TriagePlan,
    branch_name: str,
    base_branch: str,
    pr_title: str,
    pr_body: str,
) -> str:
    pr_body_preview = pr_body
    if len(pr_body_preview) > 800:
        pr_body_preview = pr_body_preview[:800].rstrip() + "\n…(truncated)"
    return "\n".join(
        [
            "github plan-pr — preview",
            f"  branch: {branch_name} (from {base_branch})",
            f"  title : {pr_title}",
            f"  draft : True (G6 smoke 정책: 강제 draft, merge 금지)",
            f"  body  :",
            *(f"    {line}" for line in pr_body_preview.splitlines()),
        ]
    )


def run_github_plan_pr_command(
    issue_number: int,
    *,
    dry_run: bool = True,
    base_branch: Optional[str] = None,
    repo: Optional[str] = None,
    audit_id: str = "plan-pr-preview",
    json_output: bool = False,
) -> int:
    """`yule github plan-pr <issue> --dry-run`."""

    if not dry_run:
        _print_err(
            "plan-pr 는 현 단계에서 --dry-run 만 지원합니다. 실제 PR 생성은 "
            "yule github smoke-pr --live 를 사용하세요."
        )
        return 2

    cfg = _config_or_die()
    if cfg is None:
        return 2
    repo_full = repo or cfg.repo_full_name

    payload = _fetch_issue_payload(repo=repo_full, issue_number=issue_number)
    if payload is None:
        _print_err(
            f"issue #{issue_number} 를 가져오지 못했습니다 (plan-pr 는 issue context 가 필요합니다)."
        )
        return 1

    request = build_request_from_github_issue(payload)
    plan = senior_triage(request)
    issue_title = str((payload.get("title") or "")).strip()
    issue_body = str((payload.get("body") or "")).strip()
    adapter = _adapt_plan_for_g3(
        plan,
        title=issue_title or plan.suggested_branch,
        body=issue_body,
        issue_number=issue_number,
        session_id=None,
        repo=repo_full,
        base_branch=(base_branch or "main").strip() or "main",
    )
    branch_name = derive_branch_name(adapter, fallback_seed=audit_id)
    if is_protected_branch(branch_name):
        _print_err(
            f"branch_name 후보 {branch_name!r} 가 protected branch 로 분류됩니다. 거부합니다."
        )
        return 1
    base = adapter.base_branch
    pr_body = render_pr_body(adapter, audit_id=audit_id).render()
    pr_title = (adapter.title or branch_name).strip()

    if json_output:
        print(
            json.dumps(
                _redact_for_console(
                    {
                        "branch": branch_name,
                        "base": base,
                        "draft": True,
                        "title": pr_title,
                        "body": pr_body,
                        "merge_blocked": True,
                        "merge_blocked_reason": "G6 smoke policy — never merge",
                    }
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            _format_plan_pr_human(
                plan=plan,
                branch_name=branch_name,
                base_branch=base,
                pr_title=pr_title,
                pr_body=pr_body,
            )
        )
    return 0




# ---------------------------------------------------------------------------
# smoke-pr — live GitHub App branch + commit + draft PR
# ---------------------------------------------------------------------------


_SMOKE_BRANCH_PREFIX: str = "yule-workos-smoke"
_SMOKE_FILE_DIR: str = "runs/github-workos-smoke"


_MERGE_BLOCKED_NOTICE: str = (
    "## ⚠️ Merge 금지\n\n"
    "이 PR 은 GitHub App (G1~G6) 의 라이브 smoke 검증용으로 자동 생성되었습니다. "
    "**병합하지 마세요.** smoke 검증이 끝나면 `gh pr close` 로 닫고, "
    "필요한 경우 별도 운영자 승인 절차를 통해 새 PR 을 만드세요. "
    "본 PR 의 commit / branch 는 `runs/github-workos-smoke/` 아래 marker 파일 외 "
    "프로덕션 코드 변경을 포함하지 않습니다."
)


@dataclass(frozen=True)
class SmokePROutcome:
    branch: str
    commit_sha: str
    pr_number: int
    pr_url: str
    smoke_file_path: str
    audit_id: str


def _build_smoke_marker_body(
    *,
    audit_id: str,
    issue_number: Optional[int],
    repo_full: str,
    pr_url_placeholder: str,
    extras: Mapping[str, str],
) -> str:
    when = _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()
    lines = [
        f"# GitHub WorkOS smoke marker — {when}",
        "",
        "이 파일은 GitHub App (G1~G6) 라이브 smoke 검증으로 생성되었습니다. ",
        "**프로덕션 코드 변경 없음.** secret 도 포함하지 않습니다.",
        "",
        f"- audit_id: `{audit_id}`",
        f"- repo: `{repo_full}`",
    ]
    if issue_number is not None:
        lines.append(f"- issue: #{issue_number}")
    lines.append(f"- created_at: `{when}`")
    if extras:
        for key, value in extras.items():
            lines.append(f"- {key}: {redact_secret_like(str(value))}")
    lines.append("")
    lines.append("## 다음 단계")
    lines.append("")
    lines.append(
        "- `gh pr close <number>` 로 본 PR 을 닫는다 (병합 금지)."
    )
    lines.append(
        "- 필요시 `gh api repos/<owner>/<repo>/git/refs/heads/<branch> -X DELETE` 로 smoke branch 도 삭제."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _utc_timestamp_slug() -> str:
    when = _dt.datetime.now(tz=_dt.timezone.utc)
    return when.strftime("%Y%m%dT%H%M%SZ")


def _resolve_repo_root_for_template() -> Path:
    """Best-effort repo-root resolver for PR-template discovery.

    Walks up from the current working directory looking for a
    ``.github`` sibling. Falls back to the package install root so
    a smoke run from a non-repo cwd still picks up the bundled
    template.
    """

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".github").is_dir():
            return candidate
    # Fallback: the file's repo root (works for tests + dev installs).
    return Path(__file__).resolve().parents[5]


def _new_smoke_audit_id() -> str:
    import uuid

    return f"workos-smoke-{_utc_timestamp_slug()}-{uuid.uuid4().hex[:8]}"


def run_github_smoke_pr_command(
    *,
    live: bool = False,
    issue_number: Optional[int] = None,
    repo: Optional[str] = None,
    base_branch: Optional[str] = None,
    branch_name_override: Optional[str] = None,
    json_output: bool = False,
) -> int:
    """`yule github smoke-pr [--live]` — end-to-end GitHub App write.

    Without ``--live`` the command refuses (smoke would be a no-op).
    With ``--live`` the flow:

      1. `doctor --live` first; abort on FAIL.
      2. Build a fresh installation token via :class:`LiveGithubAppClient`.
      3. Optionally fetch *issue_number* via gh + run senior_triage.
      4. Resolve base branch + base commit sha through the App's
         git refs API.
      5. Create a smoke branch off the base sha.
      6. Create a single blob containing the smoke marker, plus a
         tree referencing it under ``runs/github-workos-smoke/``.
      7. Create a commit on the new branch and update the ref.
      8. Open a draft PR with the rendered triage body + the
         "merge 금지" notice.
    """

    if not live:
        _print_err(
            "smoke-pr 는 --live 플래그 없이 실행할 수 없습니다. "
            "GitHub App 으로 실제 branch/file/draft PR 을 만드는 검증입니다."
        )
        return 2

    cfg = _config_or_die()
    if cfg is None:
        return 2

    # Step 1 — doctor --live before issuing any write.
    doctor_result = _run_doctor(live=True)
    if doctor_result.overall == DOCTOR_OVERALL_FAIL:
        _print_err(
            "doctor --live 가 FAIL 입니다. 설정을 고친 뒤 다시 시도하세요."
        )
        print(_format_doctor_human(doctor_result), file=sys.stderr)
        return 1

    repo_full = repo or cfg.repo_full_name
    base = (base_branch or "main").strip() or "main"

    # Step 2 — live client.
    from ..github_app.live_client import (
        LiveGithubAppClient,
        LiveGithubAppHTTPError,
    )

    client = LiveGithubAppClient(config=cfg)

    # Step 3 — triage, when an issue id was supplied.
    plan: Optional[TriagePlan] = None
    plan_adapter: Optional[_G3PlanAdapter] = None
    issue_html_url: Optional[str] = None
    issue_title: str = ""
    issue_body: str = ""
    if issue_number is not None:
        payload = _fetch_issue_payload(
            repo=repo_full, issue_number=issue_number
        )
        if payload is not None:
            request = build_request_from_github_issue(payload)
            plan = senior_triage(request)
            extra = payload.get("html_url")
            if isinstance(extra, str) and extra.strip():
                issue_html_url = extra.strip()
            issue_title = str(payload.get("title") or "").strip()
            issue_body = str(payload.get("body") or "").strip()
        else:
            _print_err(
                f"issue #{issue_number} 가져오기 실패 — 스모크는 issue 컨텍스트 없이 진행합니다."
            )

    audit_id = _new_smoke_audit_id()
    if plan is not None:
        plan_adapter = _adapt_plan_for_g3(
            plan,
            title=issue_title or plan.suggested_branch,
            body=issue_body,
            issue_number=issue_number,
            session_id=None,
            repo=repo_full,
            base_branch=base,
        )
    if branch_name_override and branch_name_override.strip():
        branch_name = branch_name_override.strip()
    elif plan_adapter is not None:
        branch_name = derive_branch_name(plan_adapter, fallback_seed=audit_id)
    else:
        branch_name = f"{_SMOKE_BRANCH_PREFIX}/{_utc_timestamp_slug()}"
    if is_protected_branch(branch_name):
        _print_err(
            f"branch_name 후보 {branch_name!r} 가 protected branch — 작업을 중단합니다."
        )
        return 1

    # Step 4 — base sha.
    try:
        base_sha = client.get_branch_head_sha(repo=repo_full, branch=base)
        base_tree = client.get_commit_tree_sha(repo=repo_full, commit_sha=base_sha)
    except LiveGithubAppHTTPError as exc:
        _print_err(f"base branch 조회 실패: {exc}")
        return 1

    # Step 5 — branch ref.
    try:
        client.create_branch_ref(repo=repo_full, branch=branch_name, base_sha=base_sha)
    except LiveGithubAppHTTPError as exc:
        _print_err(f"branch 생성 실패 (이미 존재할 수 있음): {exc}")
        return 1

    # Step 6 — blob + tree.
    smoke_relpath = f"{_SMOKE_FILE_DIR}/{_utc_timestamp_slug()}.md"
    smoke_body = _build_smoke_marker_body(
        audit_id=audit_id,
        issue_number=issue_number,
        repo_full=repo_full,
        pr_url_placeholder="(filled-in after PR open)",
        extras={
            "branch": branch_name,
            "base_branch": base,
            "base_sha": base_sha,
            **({"issue_url": issue_html_url} if issue_html_url else {}),
        },
    )
    try:
        blob_sha = client.create_blob(repo=repo_full, content=smoke_body)
        tree = client.create_tree(
            repo=repo_full,
            base_tree=base_tree,
            entries=[
                {
                    "path": smoke_relpath,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            ],
        )
    except LiveGithubAppHTTPError as exc:
        _print_err(f"smoke blob/tree 생성 실패: {exc}")
        return 1

    # Step 7 — commit + ref update.
    commit_message = (
        f"chore(github-workos-smoke): live App write smoke {audit_id}"
        + (f" (issue #{issue_number})" if issue_number is not None else "")
    )
    try:
        commit = client.create_commit_via_data_api(
            repo=repo_full,
            branch=branch_name,
            message=commit_message,
            tree=tree,
            author={
                "name": "yule-studio engineering-agent",
                "email": "engineering-agent[bot]@users.noreply.github.com",
            },
            committer={
                "name": "yule-studio engineering-agent",
                "email": "engineering-agent[bot]@users.noreply.github.com",
            },
            parents=[base_sha],
        )
        commit_sha = str(commit.get("sha") or "")
    except LiveGithubAppHTTPError as exc:
        _print_err(f"commit 생성 실패: {exc}")
        return 1

    # Step 8 — draft PR.
    # Build a PrTemplateFillContext from the live smoke metadata +
    # (optional) triage adapter so the repo's PR template (.github/
    # PULL_REQUEST_TEMPLATE) can drive the body shape. When no
    # template is found we fall back to render_pr_body and stamp a
    # ``template_missing`` audit reason.
    from ..agents.github_workos.repository_pr_template import (
        PrTemplateFillContext,
        compose_pr_body,
    )

    smoke_change_summary = (
        f"smoke marker file `{smoke_relpath}` 추가 (production code 변경 없음)",
    )
    fill_context = PrTemplateFillContext(
        audit_id=audit_id,
        branch=branch_name,
        commit_sha=commit_sha,
        actor="yule-studio-engineering-agent[bot]",
        primary_role=(
            getattr(plan_adapter, "primary_role", "") if plan_adapter else ""
        ),
        autonomy_level=(
            getattr(plan_adapter, "autonomy_level", "") if plan_adapter else ""
        ),
        issue_number=issue_number,
        issue_url=issue_html_url or "",
        purpose=(
            (getattr(plan_adapter, "body", "") or "").strip()
            if plan_adapter
            else "GitHub WorkOS (G1~G6) live App smoke — production code 변경 없음."
        ),
        change_summary=smoke_change_summary,
        test_plan=(
            tuple(getattr(plan_adapter, "test_plan", ()) or ())
            if plan_adapter
            else ()
        ),
        risks=(
            tuple(getattr(plan_adapter, "risks", ()) or ())
            if plan_adapter
            else ()
        ),
        approvals_needed=(
            tuple(getattr(plan_adapter, "approvals_needed", ()) or ())
            if plan_adapter
            else ()
        ),
        work_orders=(
            tuple(getattr(plan_adapter, "work_orders", ()) or ())
            if plan_adapter
            else ()
        ),
        trace_links={
            **({"github": issue_html_url} if issue_html_url else {}),
        },
        smoke_mode=True,
        smoke_marker_path=smoke_relpath,
        base_branch=base,
        repo_full_name=repo_full,
    )
    composed = compose_pr_body(
        repo_root=str(_resolve_repo_root_for_template()),
        plan=plan_adapter,
        context=fill_context,
        fallback_renderer=render_pr_body,
    )
    pr_body = composed.rendered
    if composed.template_missing:
        # Surface the audit reason so the operator can grep
        # "template_missing" in the smoke output / agent_ops log.
        _print_err(
            "PR template 을 찾지 못했습니다 (template_missing). "
            "fallback render_pr_body 를 사용했습니다."
        )

    pr_title = (
        f"[smoke][do-not-merge] github-workos live App smoke ({audit_id})"
        if plan_adapter is None
        else f"[smoke][do-not-merge] {(plan_adapter.title or branch_name).strip()} ({audit_id})"
    )

    try:
        pr_response = client.create_draft_pull_request(
            repo=repo_full,
            head=branch_name,
            base=base,
            title=pr_title,
            body=pr_body,
            draft=True,
        )
    except LiveGithubAppHTTPError as exc:
        _print_err(f"draft PR 생성 실패: {exc}")
        return 1

    pr_number = int(pr_response.get("number") or 0)
    pr_url = str(pr_response.get("html_url") or "")
    outcome = SmokePROutcome(
        branch=branch_name,
        commit_sha=commit_sha,
        pr_number=pr_number,
        pr_url=pr_url,
        smoke_file_path=smoke_relpath,
        audit_id=audit_id,
    )

    if json_output:
        print(
            json.dumps(
                _redact_for_console(
                    {
                        "branch": outcome.branch,
                        "commit_sha": outcome.commit_sha,
                        "pr_number": outcome.pr_number,
                        "pr_url": outcome.pr_url,
                        "smoke_file_path": outcome.smoke_file_path,
                        "audit_id": outcome.audit_id,
                        "merge_blocked": True,
                    }
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            "github smoke-pr — done\n"
            f"  branch    : {outcome.branch}\n"
            f"  commit    : {outcome.commit_sha}\n"
            f"  pr_number : {outcome.pr_number}\n"
            f"  pr_url    : {outcome.pr_url}\n"
            f"  smoke file: {outcome.smoke_file_path}\n"
            f"  audit_id  : {outcome.audit_id}\n"
            f"  merge     : 금지 (G6 smoke policy)"
        )
    return 0
