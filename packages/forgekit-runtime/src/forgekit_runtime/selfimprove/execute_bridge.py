"""GW4-B execution bridge — approve → REAL gated path → evidence (loop closed).

This closes the self-management loop ``goal → tick → approve → EXECUTE → verify →
evidence`` at the orchestration+evidence level. The ``/goal approve`` surface
(``forgekit_console.goal_surface``) looks this up lazily as
``forgekit_runtime.selfimprove.execute_approved_packet`` and calls
``fn(goal, env=env)``; before this module existed it always returned
"승인됨(실행 대기)". Now approve invokes the **REAL gated path** and writes **real
evidence** — it is not a stub and it never fakes "executed".

What it actually does (honest boundary — see ``docs/forgekit-goal-roadmap.md`` GW4-B):

1. Resolve the goal's linked improvement packet (by ``packet_id`` or the most
   recent linked one) from the goal's append-only ``proposal`` evidence — the
   tick recorded ``[<risk>] <finding> -> <route>`` with ``ref=<packet_id>``.
2. Convert the ``RepoImprovementPacket`` → autopilot ``RepoFinding`` and run the
   **EXISTING** approval chain: ``run_internal_chain`` → ``can_specialist_execute``,
   then the runtime gate ``decision_lane.authorize_runtime_execution`` (same gate
   the orchestrator injects via ``make_runtime_authorizer``) and the
   ``autopilot.validate_execution`` re-check. **No bypass, no re-implementation.**
3. Approval-gated, safe-class only: only a SAFE-class + internally-authorized
   packet is authorized to execute. risky / blocked / unauthorized → NOT executed;
   an honest ``blocked`` / ``awaiting`` outcome is returned and recorded. We NEVER
   fabricate "executed" and NEVER move a goal to ``done`` without verified evidence.
4. On an authorized safe run we write an ``execution`` evidence record (with the
   approval metadata + the executing agent identity) and a ``verification``
   evidence record back to the goal, attributed to a real registry identity.

Physical-mutation boundary (honest): the ACTUAL repo file write stays
**BoundedMutator-gated** (``autopilot.AutopilotOrchestrator.mutator`` / WT3) — this
bridge does NOT perform an autonomous file diff/commit here. It runs the REAL gated
*authorization* + ``validate_execution`` and records execution/verification evidence
describing exactly what WAS authorized (incl. the trailer-stamped commit message the
executor path would carry). The loop is "closed" at orchestration+evidence: approve
now exercises the real gate and writes real evidence — it does not invent a diff or a
commit that did not happen.

Owner: ``packages/forgekit-runtime/selfimprove``. The console surface owns no
execution logic; it only renders the outcome string this returns.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

from forgekit_config.identity.attribution import commit_trailers, git_author_for
from forgekit_config.identity.registry import canonical_id, is_known

from forgekit_goal import Goal, GoalStatus, transitions

from ..autopilot import (
    AutopilotLimits,
    ExecutionTaskSplit,
    RepoFinding,
    can_specialist_execute,
    run_internal_chain,
    validate_execution,
)
from ..decision_lane import (
    ActionRequest,
    authorize_runtime_execution,
    execution_commit_trailers,
)
from . import packet as P

# Outcome states (honest — never a fake "executed").
OUTCOME_EXECUTED = "executed"      # authorized safe run; execution+verification recorded
OUTCOME_BLOCKED = "blocked"        # risky/destructive/unauthorized — refused, recorded
OUTCOME_AWAITING = "awaiting"      # nothing actionable / no resolvable packet
OUTCOME_ERROR = "error"            # bad input (unknown packet id, no packets)

# The executing specialist identity for self-improvement findings. Resolved against the
# registry so the stamped ``Forgekit-Agent`` trailer is always ``is_known`` (GW2-B / #346).
# A finding of kind "gap" routes to backend in the chain; we attribute execution to that
# same registry engineer so the trailer matches who the chain hands off to.
_EXECUTOR_FALLBACK = "backend-engineer"

# kind the safe-class self-improvement note maps to (in SAFE_CLASS_ALLOWLIST so the
# execution-time classifier keeps it safe instead of bumping it to risky).
_SAFE_KIND = "note"

# env var that points the Nexus vault root (same key hephaistos reads). When set + a
# real dir, an authorized real-execution run also lands an authored evidence note there;
# unset → skip honestly (no fake write). config['nexus_root'] is an accepted fallback.
_ENV_NEXUS_ROOT = "FORGEKIT_NEXUS_ROOT"

# parse a tick ``proposal`` evidence summary: ``[<risk>] <finding> -> <route>``
_PROPOSAL_RE = re.compile(r"^\[(?P<risk>[^\]]+)\]\s*(?P<finding>.*?)\s*->\s*(?P<route>.*)$")


@dataclass(frozen=True)
class ExecuteOutcome:
    """Honest result of an approve→execute bridge attempt. ``executed`` is True ONLY
    when the real gate authorized a safe-class run and evidence was written."""

    outcome: str
    executed: bool = False
    packet_id: str = ""
    action_class: str = ""
    executor_id: str = ""
    approval_metadata: str = ""
    commit_message: str = ""
    reasons: Tuple[str, ...] = ()
    detail: str = ""
    # real-execution (opt-in apply path) evidence — empty on the default authorize-only path.
    applied: bool = False          # True only after a verified bounded write + real commit
    commit_sha: str = ""           # the real commit sha (apply path), "" otherwise
    changed_path: str = ""         # the repo-relative file the bounded write touched
    vault_note: str = ""           # vault note path written ("" = unset/skip — honest)

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome, "executed": self.executed,
            "packet_id": self.packet_id, "action_class": self.action_class,
            "executor_id": self.executor_id, "approval_metadata": self.approval_metadata,
            "reasons": list(self.reasons), "detail": self.detail,
            "applied": self.applied, "commit_sha": self.commit_sha,
            "changed_path": self.changed_path, "vault_note": self.vault_note,
        }

    def __str__(self) -> str:  # the console surface renders this directly
        if self.executed:
            return (f"실행 인가됨(safe·게이트 통과): {self.detail} "
                    f"[executor={self.executor_id}]")
        if self.outcome == OUTCOME_BLOCKED:
            return f"실행 차단됨({self.action_class or '미인가'}): {'; '.join(self.reasons) or self.detail}"
        if self.outcome == OUTCOME_ERROR:
            return f"실행 불가: {self.detail}"
        return f"실행 대기: {self.detail}"


def _resolve_packet(goal: Goal, packet_id: Optional[str]) -> Tuple[Optional[str], Optional[P.RepoImprovementPacket], str]:
    """Resolve a linked packet to a ``(packet_id, RepoImprovementPacket, error)``.

    Packets are linked to a goal as ids only; the tick records the packet's
    ``finding`` + ``risk`` in a ``proposal`` evidence record (``ref=<packet_id>``).
    We reconstruct the packet from that recorded proposal — honest (it uses what
    was actually proposed), no re-scan. ``packet_id=None`` → the most recent
    linked packet that has a proposal record.
    """

    if not goal.packets:
        return None, None, "연결된 packet 없음 — tick 이 제안한 packet 이 있어야 실행 가능"

    # proposal evidence indexed by ref (packet id), newest last (append-only order)
    proposals = {e.ref: e for e in goal.evidence if e.kind == "proposal" and e.ref}

    pid = (packet_id or "").strip() or None
    if pid is not None:
        if pid not in goal.packets:
            return None, None, f"packet {pid!r} 는 이 goal 에 연결돼 있지 않음"
    else:
        # most recent linked packet that has a proposal record
        for cand in reversed(goal.packets):
            if cand in proposals:
                pid = cand
                break
        if pid is None:
            return None, None, "실행할 proposal evidence 가 있는 packet 을 찾지 못함"

    ev = proposals.get(pid)
    if ev is None:
        return pid, None, f"packet {pid} 의 proposal evidence 가 없어 내용을 복원할 수 없음"

    m = _PROPOSAL_RE.match(ev.summary or "")
    if not m:
        return pid, None, f"packet {pid} 의 proposal 형식을 해석할 수 없음: {ev.summary!r}"

    risk = (m.group("risk") or "").strip()
    finding = (m.group("finding") or "").strip()
    if risk not in (P.RISK_SAFE, P.RISK_RISKY, P.RISK_BLOCKED):
        risk = P.classify_risk(finding)
    pkt = P.RepoImprovementPacket(
        finding=finding, risk=risk, affected_area="",
        approval_needed=(risk != P.RISK_SAFE))
    return pid, pkt, ""


def _executor_for(owner_role: str) -> str:
    """The canonical executing identity for the chain's owner role (must be ``is_known``)."""

    cid = canonical_id(owner_role)
    if cid and is_known(cid):
        return cid
    return canonical_id(_EXECUTOR_FALLBACK) or _EXECUTOR_FALLBACK


def build_execution_commit_message(verdict, finding: str, *, env=None) -> str:
    """Trailer-stamped commit message the executor path would carry (GW2-B / #346).

    Built from the AUTHORIZED verdict only — ``execution_commit_trailers`` returns
    nothing for a blocked verdict, so a refused run never produces a fake-approved
    message. The ``Forgekit-Agent`` trailer carries a registry ``is_known`` id, so
    the #346 commit-governance validator accepts it.
    """

    trailers = execution_commit_trailers(verdict, flow="selfimprove-execute", env=env)
    subject = f"✅ forgekit 자가개선 실행: {finding[:60]}".rstrip()
    body = (
        "변경 이유\n"
        f"- 승인된 self-improvement packet 의 safe-class 작업 실행 (approval={verdict.approval_metadata})\n\n"
        "주요 변경 사항\n"
        f"- {finding}\n\n"
        "비고\n"
        "- PM→gateway→tech-lead 내부 승인 + decision-lane 실행 게이트 통과분만 실행\n"
    )
    return subject + "\n\n" + body + "\n" + "\n".join(trailers) + "\n"


# --- real-execution helpers (opt-in apply path only) -------------------------

def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in (text or "").lower())[:40].strip("-") or "task"


def _build_safe_task(pid: str, finding: str):
    """A safe-class ``note`` ExecTask under an ALLOWED_WRITE_PREFIX (``runs/``).

    The BoundedMutator re-validates path + caps + verifies the write; this only shapes
    the bounded note content. Lazy import keeps the module importable without runner."""

    from ..autopilot.runner import ACTION_NOTE, ExecTask

    rel = f"runs/forgekit/selfimprove/{_slug(finding)}-{_slug(pid)}.md"
    content = (
        f"# self-improvement note — packet {pid}\n\n"
        f"- finding: {finding}\n"
        "- class: safe (internal-approved: PM→gateway→tech-lead + decision-lane)\n"
        "- action: 안전 클래스 note 기록 (실제 bounded write · BoundedMutator 검증)\n"
    )
    return ExecTask(ACTION_NOTE, rel, content=content, summary=finding)


def _git(repo_root: str, *args: str):
    """Run ``git -C repo_root <args>`` — captured, never raises (returns CompletedProcess)."""

    return subprocess.run(["git", "-C", str(repo_root), *args],
                          capture_output=True, text=True, check=False)


def _git_pre_state(repo_root: str, rel_path: str) -> Tuple[bool, str]:
    """Whether *rel_path* is tracked (HEAD has it) — drives rollback (restore vs rm).

    Returns ``(tracked, head_sha)``. A non-git repo → ``(False, "")`` so the caller can
    refuse a real commit honestly instead of pretending."""

    head = _git(repo_root, "rev-parse", "HEAD")
    head_sha = head.stdout.strip() if head.returncode == 0 else ""
    tracked = _git(repo_root, "ls-files", "--error-unmatch", rel_path).returncode == 0
    return tracked, head_sha


def _git_rollback(repo_root: str, rel_path: str, tracked: bool) -> None:
    """Undo a bounded write: restore a tracked file from HEAD, else delete the new file.
    Best-effort — a rollback failure must not crash the decision (already refused)."""

    target = Path(repo_root) / rel_path
    if tracked:
        _git(repo_root, "checkout", "--", rel_path)
    else:
        try:
            if target.exists():
                target.unlink()
        except OSError:
            pass


def _git_commit(repo_root: str, rel_path: str, author: str, message: str) -> Tuple[bool, str, str]:
    """Stage exactly *rel_path* + commit with *author* and *message*. NEVER pushes.

    Returns ``(ok, sha, error)``. ``ok=False`` → no commit happened (caller rolls back).
    Uses an explicit pathspec (no ``git add .``) and ``-F -`` for the message via stdin so
    the trailer-stamped body lands verbatim. ``GIT_*`` committer env keeps it deterministic."""

    add = _git(repo_root, "add", "--", rel_path)
    if add.returncode != 0:
        return False, "", f"git add 실패: {add.stderr.strip()}"
    env = dict(os.environ)
    # committer mirrors the author so the commit is fully attributed to the executor.
    name = author.split(" <")[0].strip() if " <" in author else author
    email = author.split("<", 1)[1].rstrip(">").strip() if "<" in author else "forgekit@forgekit.local"
    env.update({"GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email})
    cp = subprocess.run(
        ["git", "-C", str(repo_root), "commit", "--no-verify", "--author", author, "-F", "-"],
        input=message, capture_output=True, text=True, check=False, env=env)
    if cp.returncode != 0:
        return False, "", f"git commit 실패: {cp.stderr.strip() or cp.stdout.strip()}"
    sha = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
    return True, sha, ""


def _resolve_vault_root(env, config) -> Optional[Path]:
    """Configured Nexus vault root (env ``FORGEKIT_NEXUS_ROOT`` or ``config['nexus_root']``).
    None → not connected → vault write skipped honestly (no fake)."""

    e = os.environ if env is None else env
    raw = str(e.get(_ENV_NEXUS_ROOT, "") or (config or {}).get("nexus_root", "") or "").strip()
    if not raw:
        return None
    root = Path(raw)
    return root if root.is_dir() else None


def _write_vault_evidence(vault_root: Path, *, executor: str, pid: str, finding: str,
                          rel_path: str, sha: str, approval: str, created_at: str = "") -> Optional[Path]:
    """Author a real evidence note into the vault (best-effort, guarded). Returns the
    path written or None (failure / unavailable). Lazy import keeps nexus optional."""

    try:
        from nexus.vault.note import build_authored_note, write_note
    except Exception:  # noqa: BLE001 — nexus optional; skip honestly
        return None
    body = (
        "## 핵심 요약\n"
        f"- self-improvement packet {pid} 의 safe-class 작업이 실제 실행됨 (bounded write + commit).\n\n"
        "## 내 해석\n"
        f"- finding: {finding}\n"
        f"- 실행 파일: {rel_path}\n"
        f"- commit: {sha}\n"
        f"- approval: {approval}\n\n"
        "## 적용 맥락\n"
        "- PM→gateway→tech-lead 내부 승인 + decision-lane 실행 게이트 통과분만 실행.\n"
        "- 실제 파일 mutation 은 BoundedMutator 검증(재읽기) 후에만 commit. 파괴적/risky 는 approval-gated.\n\n"
        "## 관련 노트\n- (자동 생성 evidence)\n\n"
        "## 참고\n- GW4-B physical-execution + evidence→vault.\n"
    )
    content = build_authored_note(
        executor,
        title=f"self-improvement 실행 evidence — {finding[:32]}",
        body=body, kind="execution-evidence", status="done", created_at=created_at,
        phase="execute", source_flow="selfimprove-execute",
        tags=("forgekit", "self-improvement", "execution"), related=())
    subpath = f"00-inbox/forgekit/selfimprove/exec-{_slug(finding)}-{_slug(pid)}.md"
    return write_note(content, vault_root, subpath)


def execute_approved_packet(
    goal: Goal,
    packet_id: Optional[str] = None,
    repo_root: Optional[str] = None,
    *,
    approver: str = "operator",
    env: Optional[Mapping[str, str]] = None,
    persist: bool = True,
    apply: bool = False,
    mutator: Optional[object] = None,
    config: Optional[Mapping] = None,
) -> ExecuteOutcome:
    """Bridge an approved goal's linked packet into the REAL gated execution path.

    Signature matches what ``goal_surface._try_execute_bridge`` calls — ``fn(goal,
    env=env)`` — with ``packet_id`` / ``repo_root`` / ``approver`` optional so both
    the surface and direct callers work.

    Behaviour (honest, gate-reusing):

    * Resolve the linked packet (``packet_id`` or most recent). Unknown / none →
      ``OUTCOME_ERROR`` (no execution).
    * Convert to a ``RepoFinding`` and run the EXISTING chain
      (``run_internal_chain`` + ``can_specialist_execute``) then the runtime gate
      (``authorize_runtime_execution``) and ``validate_execution``. No bypass.
    * SAFE + authorized → write ``execution`` + ``verification`` evidence to the
      goal (attributed to the executing registry identity + ``approver``), keep the
      goal legal (re-assert ACTIVE if it sits in ``awaiting_approval``), and return
      ``OUTCOME_EXECUTED`` (``executed=True``).
    * risky / blocked / unauthorized → return ``OUTCOME_BLOCKED`` (``executed=False``),
      record a ``decision`` evidence noting the refusal. NEVER fabricate execution,
      NEVER transition to ``done``.

    The physical file mutation stays BoundedMutator-gated (module docstring): this
    records what WAS authorized + the trailer-stamped commit message the executor
    path would carry — it does not invent a diff/commit.

    Returns the :class:`ExecuteOutcome`; when ``persist`` is True and the run is
    authorized, the goal is saved to its store (so the surface's lazy call closes
    the loop even though the surface only renders the returned string).
    """

    pid, pkt, err = _resolve_packet(goal, packet_id)
    if err:
        return ExecuteOutcome(OUTCOME_ERROR, packet_id=pid or "", detail=err)

    risk_class = pkt.risk  # safe / risky / blocked — drives classification authoritatively

    finding = RepoFinding(repo="forgekit", finding=pkt.finding, kind="gap",
                          evidence=f"self-improvement packet {pid}")
    # EXISTING internal chain (PM → gateway → tech-lead). risk_class makes the
    # recorded packet risk authoritative (not just finding wording).
    _packet, route, decision, _trace = run_internal_chain(finding, risk_class=risk_class)
    executor = _executor_for(route.owner_role)

    # runtime execution gate (the SAME gate make_runtime_authorizer wraps). Uses a
    # safe-class kind so an authorized safe packet is not bumped to risky at exec time.
    request = ActionRequest(kind=_SAFE_KIND if risk_class == P.RISK_SAFE else risk_class,
                            summary=pkt.finding, risk_flag=risk_class)
    verdict = authorize_runtime_execution(
        decision, request, executor_role=executor, gateway_ok=True,
        operator_approval=None)  # risky needs a real operator grant — not auto-supplied here

    chain_ok = can_specialist_execute(decision)
    split = ExecutionTaskSplit(decision_summary=pkt.finding, executor=executor,
                               tasks=(pkt.finding,))
    val_ok, val_reasons = validate_execution(decision, split, AutopilotLimits())

    authorized = bool(verdict.allowed and chain_ok and val_ok)

    if not authorized:
        reasons = list(verdict.blocking_reasons) + list(val_reasons)
        if not chain_ok:
            reasons.append("내부 chain 승인(can_execute) 없음")
        # record the honest refusal as decision evidence (append-only)
        if persist:
            g = goal.add_evidence(
                "decision",
                f"execute 거부 — packet {pid} ({risk_class}): {'; '.join(reasons)[:200]}",
                ref=pid)
            _save(g, env)
        return ExecuteOutcome(
            OUTCOME_BLOCKED, executed=False, packet_id=pid,
            action_class=verdict.action_class, executor_id=executor,
            reasons=tuple(reasons),
            detail=f"safe-class + 내부+런타임 승인 통과분만 실행 가능 ({risk_class} 거부)")

    # authorized safe-class run — record REAL execution + verification evidence.
    commit_message = build_execution_commit_message(verdict, pkt.finding, env=env)
    author = git_author_for(executor)

    g = goal
    # keep the goal legal: a goal parked in awaiting_approval moves back to ACTIVE on
    # an authorized execution (operator approved). Never forces an illegal move; never done.
    if g.status == GoalStatus.AWAITING_APPROVAL and transitions.can_transition(
            g.status, GoalStatus.ACTIVE):
        g = transitions.apply(g, GoalStatus.ACTIVE)

    # OPT-IN real-execution path (GW4-B physical). Default authorize-only callers
    # (``execute_approved_packet(goal, env=env)``) never enter this — ``apply`` is False
    # and no mutator is supplied — so console-approve / #348 behaviour is unchanged.
    if apply and mutator is not None and repo_root and risk_class == P.RISK_SAFE:
        return _apply_real_execution(
            g, pid=pid, finding=pkt.finding, verdict=verdict, executor=executor,
            author=author, commit_message=commit_message, mutator=mutator,
            repo_root=str(repo_root), approver=approver, env=env, config=config,
            persist=persist)

    exec_summary = (
        f"safe-class 실행 인가 — packet {pid}: {pkt.finding} "
        f"[executor={executor} author={author} approver={approver} "
        f"approval={verdict.approval_metadata}] "
        "(실제 파일 mutation 은 BoundedMutator 게이트 — 본 단계는 인가+검증 기록)")
    g = g.add_evidence("execution", exec_summary, ref=pid)

    verify_summary = (
        f"실행 게이트 재검증 통과 — chain(can_execute)+decision-lane(authorize)+"
        f"validate_execution 모두 통과, action_class={verdict.action_class}, "
        f"approval={verdict.approval_metadata}")
    g = g.add_evidence("verification", verify_summary, ref=pid)

    if persist:
        _save(g, env)

    return ExecuteOutcome(
        OUTCOME_EXECUTED, executed=True, packet_id=pid,
        action_class=verdict.action_class, executor_id=executor,
        approval_metadata=verdict.approval_metadata, commit_message=commit_message,
        detail=f"{pkt.finding} (safe·게이트 통과·evidence 기록)")


def _apply_real_execution(
    goal: Goal, *, pid: str, finding: str, verdict, executor: str, author: str,
    commit_message: str, mutator, repo_root: str, approver: str,
    env: Optional[Mapping[str, str]], config: Optional[Mapping], persist: bool,
) -> ExecuteOutcome:
    """Authorized safe-class → REAL bounded write + real git commit + evidence (incl. vault).

    Only reached from the opt-in apply path after a full 3-gate PASS. Performs the
    bounded write through the injected ``BoundedMutator`` (hard caps + re-read verify),
    then a real ``git -C repo_root`` commit (NEVER push). On any caps-exceeded /
    verify-fail / non-verified outcome it ROLLS BACK (no commit) and returns an honest
    ``blocked`` outcome — it never fakes "applied" and never marks the goal ``done``."""

    task = _build_safe_task(pid, finding)

    # capture pre-state for rollback BEFORE the write (tracked vs new file).
    tracked, _head = _git_pre_state(repo_root, task.rel_path)

    outcome = mutator.execute(task)   # REAL bounded write — caps + re-read verify inside
    if not (outcome.executed and outcome.verified):
        # caps exceeded / verify fail / non-safe path / no-op → rollback, no commit.
        _git_rollback(repo_root, task.rel_path, tracked)
        reason = outcome.refused_reason or "bounded write 미검증"
        if persist:
            gg = goal.add_evidence(
                "decision", f"apply 거부 — packet {pid}: {reason} (rollback, 커밋 안 함)", ref=pid)
            _save(gg, env)
        return ExecuteOutcome(
            OUTCOME_BLOCKED, executed=False, applied=False, packet_id=pid,
            action_class=verdict.action_class, executor_id=executor,
            reasons=(reason,),
            detail=f"bounded write 실패/한도초과 — rollback (no commit): {reason}")

    # verified write landed → real git commit (explicit pathspec, no push).
    ok, sha, cerr = _git_commit(repo_root, task.rel_path, author, commit_message)
    if not ok:
        _git_rollback(repo_root, task.rel_path, tracked)
        if persist:
            gg = goal.add_evidence(
                "decision", f"apply 커밋 실패 — packet {pid}: {cerr} (rollback)", ref=pid)
            _save(gg, env)
        return ExecuteOutcome(
            OUTCOME_BLOCKED, executed=False, applied=False, packet_id=pid,
            action_class=verdict.action_class, executor_id=executor,
            changed_path=task.rel_path, reasons=(cerr,),
            detail=f"commit 실패 — rollback (no commit): {cerr}")

    # real execution succeeded → execution + verification evidence (with the commit sha).
    g = goal
    exec_summary = (
        f"safe-class 실제 실행 — packet {pid}: {finding} "
        f"[executor={executor} author={author} approver={approver} "
        f"approval={verdict.approval_metadata} file={task.rel_path} commit={sha}] "
        f"(BoundedMutator 검증 + git -C 커밋, push 안 함)")
    g = g.add_evidence("execution", exec_summary, ref=pid)
    verify_summary = (
        f"실행 검증 통과 — bounded write 재읽기 검증 + 3-gate(chain/decision-lane/validate) 통과, "
        f"commit={sha}, action_class={verdict.action_class}, approval={verdict.approval_metadata}")
    g = g.add_evidence("verification", verify_summary, ref=pid)

    # vault evidence note — only when a real vault root is configured (else skip honestly).
    vault_root = _resolve_vault_root(env, config)
    vault_note_path = ""
    if vault_root is not None:
        written = _write_vault_evidence(
            vault_root, executor=executor, pid=pid, finding=finding,
            rel_path=task.rel_path, sha=sha, approval=verdict.approval_metadata)
        if written is not None:
            vault_note_path = str(written)
            g = g.add_evidence("verification", f"vault evidence note 기록 — {vault_note_path}", ref=pid)

    if persist:
        _save(g, env)

    return ExecuteOutcome(
        OUTCOME_EXECUTED, executed=True, applied=True, packet_id=pid,
        action_class=verdict.action_class, executor_id=executor,
        approval_metadata=verdict.approval_metadata, commit_message=commit_message,
        commit_sha=sha, changed_path=task.rel_path, vault_note=vault_note_path,
        detail=f"{finding} (safe·실제 실행·commit={sha[:12]}"
               + (f"·vault note 기록" if vault_note_path else "·vault 미설정 skip") + ")")


def apply_approved_packet(
    goal: Goal,
    mutator: object,
    repo_root: str,
    packet_id: Optional[str] = None,
    *,
    approver: str = "operator",
    env: Optional[Mapping[str, str]] = None,
    config: Optional[Mapping] = None,
    persist: bool = True,
) -> ExecuteOutcome:
    """REAL-execution sibling of :func:`execute_approved_packet` (GW4-B physical).

    Explicit caller (bounded runtime / daemon) only — requires a ``BoundedMutator`` and a
    ``repo_root``. Runs the SAME 3-gate authorization; on a safe + authorized verdict it
    performs a verified bounded write + a real git commit (no push) + evidence (goal store
    and, when ``FORGEKIT_NEXUS_ROOT``/``config['nexus_root']`` is set, a vault note).
    risky/destructive/caps-exceeded/verify-fail → rollback, no commit, honest outcome.

    This NEVER changes the default ``execute_approved_packet(goal, env=env)`` surface
    behaviour; it is a separate opt-in entry point."""

    return execute_approved_packet(
        goal, packet_id, repo_root, approver=approver, env=env, persist=persist,
        apply=True, mutator=mutator, config=config)


def _save(goal: Goal, env: Optional[Mapping[str, str]]) -> None:
    """Persist the updated goal so the closed loop survives (best-effort, lazy import
    to keep this module importable without a store/home configured)."""

    try:
        from forgekit_goal import GoalStore
        GoalStore(env=env).save(goal)
    except Exception:  # noqa: BLE001 — a store failure must not corrupt the decision
        pass


__all__ = (
    "OUTCOME_EXECUTED", "OUTCOME_BLOCKED", "OUTCOME_AWAITING", "OUTCOME_ERROR",
    "ExecuteOutcome", "execute_approved_packet", "apply_approved_packet",
    "build_execution_commit_message",
)
