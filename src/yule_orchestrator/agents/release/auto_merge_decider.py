"""Auto-merge decider — F7 / issue #98.

The decider answers one question: *is this PR safe enough for
the tech-lead agent to merge without operator approval?* It
does so by:

  1. Classifying the PR's :class:`RiskClass` from a static
     :class:`PrDiffSummary` + :class:`PrMetadata` snapshot
     (:func:`classify_risk`).
  2. Evaluating the conventions §5 8 auto-merge conditions
     deterministically and returning a :class:`AutoMergeVerdict`
     (:func:`evaluate_auto_merge`).

Vocabulary alignment with F12 (#103):

  * Both modules expose a ``RiskClass`` enum with the same four
    severity names (``LOW`` / ``MEDIUM`` / ``HIGH`` / ``CRITICAL``).
    F12 also has a ``SAFE`` level for read-only tool calls — F7
    deliberately starts at ``LOW`` because a PR by definition
    introduces at least one change to a tracked file. Operators
    reading either feed see one consistent ladder.

Hard rails (regression-pinned in
``tests/engineering/test_auto_merge_governance.py``):

  * ``HIGH`` and ``CRITICAL`` PRs *never* land with
    ``eligible=True`` — env, autonomy, or rule edits can't
    bypass.
  * ``cycle_authorized=False`` short-circuits every PR to
    ``eligible=False`` regardless of risk class. The cycle gate
    is the ops-side kill-switch.
  * Protected branch bases (``main`` / ``master`` / ``develop`` /
    ``release`` / ``prod`` / ``production``) escalate to at
    least ``HIGH`` and block auto-merge. The convention treats
    the base branch as the merge target — never the source.

Mistake ledger signatures live under the ``automerge.*``
namespace. ``record_automerge_signature`` is a thin helper that
forwards into :class:`MistakeLedger.record_mistake` so the
preflight hook can pick up repeat blocker hits.
"""

from __future__ import annotations

import enum
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Tuple

from ..learning.mistake_ledger import BlockerLevel, MistakeLedger


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------


#: Environment variable that opt-ins the auto-merge cycle. Empty /
#: unset means "no active cycle" and the decider refuses every
#: PR. The value is interpreted leniently — see
#: :func:`is_cycle_authorized_from_env`.
ENV_AUTOMERGE_CYCLE: str = "YULE_AUTOMERGE_CYCLE"


#: Protected branch names — direct auto-merge into these targets
#: is forbidden by §5 condition 5. Names compared lowercased.
PROTECTED_BRANCHES: frozenset[str] = frozenset(
    {
        "main",
        "master",
        "develop",
        "release",
        "prod",
        "production",
    }
)


_PROTECTED_BRANCH_PREFIXES: Tuple[str, ...] = ("release/", "hotfix/")


_SECRET_KEYWORDS: Tuple[str, ...] = (
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    "password",
    "passphrase",
    ".pem",
)


# Modules whose changes mean a PR touches *security / outbound /
# secret handling*. A diff that lands here climbs at least to
# HIGH per §5.2.
_HIGH_RISK_MODULE_PREFIXES: Tuple[str, ...] = (
    "src/yule_orchestrator/agents/security/",
    "src/yule_orchestrator/agents/safety/",
    "policies/runtime/agents/engineering-agent/issue-pr-conventions",
)


_LIVE_LLM_MODULE_PREFIXES: Tuple[str, ...] = (
    "src/yule_orchestrator/agents/job_queue/coding_executor_live",
    "src/yule_orchestrator/agents/job_queue/claude_subprocess_adapter",
)


_LARGE_DIFF_LINES_THRESHOLD: int = 600
_MEDIUM_DIFF_LINES_THRESHOLD: int = 150
_LARGE_FILES_THRESHOLD: int = 20
_MEDIUM_FILES_THRESHOLD: int = 6


# ---------------------------------------------------------------------------
# RiskClass
# ---------------------------------------------------------------------------


class RiskClass(str, enum.Enum):
    """PR-level severity ladder for auto-merge (F7 / #98).

    Vocabulary intentionally identical to the F12 (#103)
    tool-call ``RiskClass`` enum modulo the missing ``SAFE``
    level — a PR by definition changes at least one file so the
    floor here is ``LOW``.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


_CLASS_ORDER: Mapping[RiskClass, int] = {
    RiskClass.LOW: 0,
    RiskClass.MEDIUM: 1,
    RiskClass.HIGH: 2,
    RiskClass.CRITICAL: 3,
}


def _max_class(a: RiskClass, b: RiskClass) -> RiskClass:
    return a if _CLASS_ORDER[a] >= _CLASS_ORDER[b] else b


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrDiffSummary:
    """Static snapshot of a PR's diff shape.

    ``modules_touched`` is the (relative) set of top-level paths
    the diff touches — caller may pass file paths or coarser
    module names; the classifier just looks for prefix matches.

    ``has_secret_keywords`` is True when *anything* in the PR
    body / commit messages / diff hunks tripped a secret keyword
    scan (PasteGuard layer feeds this — the decider doesn't
    re-scan). ``touches_protected_branch_policy`` is True when
    the diff edits files under
    ``policies/.../protected-branch*`` or the conventions doc
    itself.
    """

    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    modules_touched: Tuple[str, ...] = ()
    has_secret_keywords: bool = False
    touches_protected_branch_policy: bool = False

    def total_lines(self) -> int:
        return max(0, int(self.lines_added)) + max(0, int(self.lines_removed))


@dataclass(frozen=True)
class PrMetadata:
    """GitHub-side metadata snapshot.

    Fields mirror the subset the §5 8 conditions actually care
    about. ``status_check_state`` is the rolled-up CI / required
    check status — one of ``SUCCESS`` / ``PENDING`` / ``FAILURE``
    / ``ERROR`` (case-insensitive). ``mergeable`` and
    ``merge_state`` follow GitHub's REST shape
    (``MERGEABLE`` / ``CLEAN`` etc.).
    """

    pr_number: int = 0
    base_branch: str = ""
    head_branch: str = ""
    is_draft: bool = False
    mergeable: str = ""
    merge_state: str = ""
    status_check_state: str = ""
    author_role: str = ""
    labels: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskSignal:
    """One piece of evidence behind a risk classification.

    ``weight`` is the :class:`RiskClass` this signal pushes the
    overall verdict toward. Signals are *additive but escalation-
    only* — they can only raise the verdict, never lower it.
    """

    name: str
    weight: RiskClass
    evidence: str


@dataclass(frozen=True)
class AutoMergeVerdict:
    """Final auto-merge decision for a PR.

    * ``eligible`` — True iff every condition listed in
      :data:`satisfied_conditions` is met AND no blocker remains.
    * ``risk_class`` — verdict from :func:`classify_risk`.
    * ``reason`` — short, single-line summary suitable for a PR
      comment or audit log.
    * ``blocker_signatures`` — mistake-ledger signatures keyed
      under the ``automerge.*`` namespace. Empty when eligible.
    * ``satisfied_conditions`` — names of the §5 conditions that
      passed. Always present (helpful even on failure).
    * ``failed_conditions`` — names of the §5 conditions that
      failed. Empty when eligible.
    """

    eligible: bool
    risk_class: RiskClass
    reason: str
    blocker_signatures: Tuple[str, ...]
    satisfied_conditions: Tuple[str, ...]
    failed_conditions: Tuple[str, ...]


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------


def classify_risk(
    diff: PrDiffSummary,
    meta: PrMetadata,
) -> Tuple[RiskClass, Tuple[RiskSignal, ...]]:
    """Return ``(RiskClass, signals)`` for the given PR snapshot.

    Pure function: no I/O, no clock, no env reads. The signal
    list is deterministic and ordered by the order rules fire.
    Multiple matching signals only raise the class, never lower
    it.
    """

    signals: list[RiskSignal] = []
    verdict = RiskClass.LOW

    # Baseline: every PR starts at LOW. Emit an explicit baseline
    # signal so the evidence trail always has at least one entry.
    signals.append(
        RiskSignal(
            name="baseline.pr",
            weight=RiskClass.LOW,
            evidence=f"pr=#{meta.pr_number or 0}",
        )
    )

    # Rule: protected base branch — always HIGH minimum.
    base_lower = (meta.base_branch or "").strip().lower()
    if _is_protected_branch(base_lower):
        signals.append(
            RiskSignal(
                name="rule.protected_branch.base",
                weight=RiskClass.HIGH,
                evidence=f"base_branch={meta.base_branch}",
            )
        )
        verdict = _max_class(verdict, RiskClass.HIGH)

    # Rule: secret keywords detected in PR (PasteGuard upstream)
    # → HIGH minimum. The decider does not re-scan; it trusts
    # the caller's hint and treats it as a hard escalation.
    if diff.has_secret_keywords:
        signals.append(
            RiskSignal(
                name="rule.secret.keyword",
                weight=RiskClass.HIGH,
                evidence="diff.has_secret_keywords=True",
            )
        )
        verdict = _max_class(verdict, RiskClass.HIGH)

    # Rule: protected branch policy edits → CRITICAL. Editing
    # the branch guard itself is the most sensitive surface.
    if diff.touches_protected_branch_policy:
        signals.append(
            RiskSignal(
                name="rule.protected_branch.policy_edit",
                weight=RiskClass.CRITICAL,
                evidence="diff.touches_protected_branch_policy=True",
            )
        )
        verdict = _max_class(verdict, RiskClass.CRITICAL)

    # Rule: modules under security/safety / live LLM live wiring
    # are HIGH at minimum.
    modules = tuple(str(m or "").strip() for m in diff.modules_touched or ())
    for prefix in _HIGH_RISK_MODULE_PREFIXES:
        if any(_module_matches(prefix, m) for m in modules):
            signals.append(
                RiskSignal(
                    name="rule.module.security_or_safety",
                    weight=RiskClass.HIGH,
                    evidence=f"module_prefix={prefix}",
                )
            )
            verdict = _max_class(verdict, RiskClass.HIGH)
            break

    for prefix in _LIVE_LLM_MODULE_PREFIXES:
        if any(_module_matches(prefix, m) for m in modules):
            signals.append(
                RiskSignal(
                    name="rule.module.live_llm",
                    weight=RiskClass.HIGH,
                    evidence=f"module_prefix={prefix}",
                )
            )
            verdict = _max_class(verdict, RiskClass.HIGH)
            break

    # Rule: diff size — large diffs are MEDIUM at minimum.
    total_lines = diff.total_lines()
    files_changed = max(0, int(diff.files_changed))
    if (
        total_lines >= _LARGE_DIFF_LINES_THRESHOLD
        or files_changed >= _LARGE_FILES_THRESHOLD
    ):
        signals.append(
            RiskSignal(
                name="rule.diff.large",
                weight=RiskClass.MEDIUM,
                evidence=f"files={files_changed}, lines={total_lines}",
            )
        )
        verdict = _max_class(verdict, RiskClass.MEDIUM)
    elif (
        total_lines >= _MEDIUM_DIFF_LINES_THRESHOLD
        or files_changed >= _MEDIUM_FILES_THRESHOLD
    ):
        signals.append(
            RiskSignal(
                name="rule.diff.medium",
                weight=RiskClass.MEDIUM,
                evidence=f"files={files_changed}, lines={total_lines}",
            )
        )
        verdict = _max_class(verdict, RiskClass.MEDIUM)

    # Rule: PR labels — explicit governance labels push higher.
    label_set = frozenset(
        _normalise_label(label) for label in (meta.labels or ()) if label
    )
    if "security" in label_set or "secret" in label_set:
        signals.append(
            RiskSignal(
                name="rule.label.security",
                weight=RiskClass.HIGH,
                evidence="labels contain security/secret",
            )
        )
        verdict = _max_class(verdict, RiskClass.HIGH)
    if "deploy" in label_set or "production" in label_set:
        signals.append(
            RiskSignal(
                name="rule.label.deploy",
                weight=RiskClass.CRITICAL,
                evidence="labels contain deploy/production",
            )
        )
        verdict = _max_class(verdict, RiskClass.CRITICAL)

    return verdict, tuple(signals)


# ---------------------------------------------------------------------------
# 8-condition evaluation
# ---------------------------------------------------------------------------


# Names match conventions §5.1 ordering so audit readers can
# cross-reference the doc and the verdict.
_CONDITION_NAMES: Tuple[str, ...] = (
    "ci_regression_ok",
    "governance_regression_ok",
    "mergeable_and_clean",
    "risk_class_low",
    "no_protected_branch_direct_push",
    "no_force_or_no_verify",
    "paste_guard_clean",
    "acceptance_criteria_reported",
)


def evaluate_auto_merge(
    diff: PrDiffSummary,
    meta: PrMetadata,
    *,
    cycle_authorized: bool,
    ci_ok: Optional[bool] = None,
    governance_ok: Optional[bool] = None,
    paste_guard_clean: Optional[bool] = None,
    acceptance_criteria_reported: Optional[bool] = None,
    force_push_detected: bool = False,
    no_verify_detected: bool = False,
    protected_branch_direct_push: bool = False,
) -> AutoMergeVerdict:
    """Return :class:`AutoMergeVerdict` for the supplied PR snapshot.

    The 8 §5.1 conditions are evaluated in order; the result
    accumulates pass/fail names and blocker signatures. The
    function never raises on missing optional inputs — it
    treats them as "unknown" and **fails closed** (the condition
    counts as failing). This keeps the decider safe to call
    early in the pipeline before every input is gathered.

    Hard rails:

      * ``cycle_authorized=False`` → ``eligible=False`` with
        a single signature ``automerge.cycle.not_authorized``.
      * Any ``risk_class`` >= ``HIGH`` → ``eligible=False`` with
        signature ``automerge.risk-class.high-without-approval``
        (matches the §7 ledger row).
      * Protected branch base → reported under
        ``no_protected_branch_direct_push`` failing and the
        signature ``automerge.protected_branch.base``.
    """

    risk_class, _ = classify_risk(diff, meta)

    satisfied: list[str] = []
    failed: list[str] = []
    blockers: list[str] = []

    def _record(name: str, ok: bool, *signatures: str) -> None:
        if ok:
            satisfied.append(name)
        else:
            failed.append(name)
            for signature in signatures:
                if signature and signature not in blockers:
                    blockers.append(signature)

    # ------------------------------------------------------------
    # Hard rail 1: cycle authorization is the kill-switch.
    # ------------------------------------------------------------
    if not cycle_authorized:
        # Mark every condition as failed for transparency, but
        # the dominant reason is the cycle gate.
        for name in _CONDITION_NAMES:
            failed.append(name)
        return AutoMergeVerdict(
            eligible=False,
            risk_class=risk_class,
            reason="cycle not authorized — YULE_AUTOMERGE_CYCLE empty/false",
            blocker_signatures=("automerge.cycle.not_authorized",),
            satisfied_conditions=(),
            failed_conditions=tuple(failed),
        )

    # ------------------------------------------------------------
    # Condition 1: CI / regression test OK.
    # ------------------------------------------------------------
    state = (meta.status_check_state or "").strip().lower()
    if ci_ok is None:
        ci_ok_value = state == "success"
    else:
        ci_ok_value = bool(ci_ok) and state in {"", "success"}
    _record("ci_regression_ok", ci_ok_value, "automerge.ci.regression_failed")

    # ------------------------------------------------------------
    # Condition 2: governance regression test OK.
    # ------------------------------------------------------------
    _record(
        "governance_regression_ok",
        bool(governance_ok) if governance_ok is not None else False,
        "automerge.governance.regression_failed",
    )

    # ------------------------------------------------------------
    # Condition 3: mergeable=MERGEABLE + mergeStateStatus=CLEAN.
    # ------------------------------------------------------------
    mergeable_ok = (meta.mergeable or "").strip().upper() == "MERGEABLE"
    state_clean = (meta.merge_state or "").strip().upper() == "CLEAN"
    not_draft = not bool(meta.is_draft)
    _record(
        "mergeable_and_clean",
        mergeable_ok and state_clean and not_draft,
        "automerge.merge_state.not_clean",
    )

    # ------------------------------------------------------------
    # Condition 4: risk class is LOW.
    # ------------------------------------------------------------
    low_only = risk_class is RiskClass.LOW
    if risk_class in (RiskClass.HIGH, RiskClass.CRITICAL):
        _record(
            "risk_class_low",
            False,
            "automerge.risk-class.high-without-approval",
        )
    else:
        _record(
            "risk_class_low",
            low_only,
            "automerge.risk-class.not_low",
        )

    # ------------------------------------------------------------
    # Condition 5: no protected branch direct push.
    # ------------------------------------------------------------
    base_protected = _is_protected_branch((meta.base_branch or "").strip().lower())
    _record(
        "no_protected_branch_direct_push",
        not protected_branch_direct_push and not base_protected,
        "automerge.protected_branch.base"
        if base_protected
        else "automerge.protected_branch.direct_push",
    )

    # ------------------------------------------------------------
    # Condition 6: no force push, no --no-verify.
    # ------------------------------------------------------------
    _record(
        "no_force_or_no_verify",
        not force_push_detected and not no_verify_detected,
        "automerge.force_push.detected"
        if force_push_detected
        else "automerge.no_verify.detected",
    )

    # ------------------------------------------------------------
    # Condition 7: PasteGuard clean (no surviving secret findings).
    # ------------------------------------------------------------
    _record(
        "paste_guard_clean",
        bool(paste_guard_clean) if paste_guard_clean is not None else False,
        "automerge.paste_guard.secret_detected",
    )

    # ------------------------------------------------------------
    # Condition 8: acceptance criteria self-report present.
    # ------------------------------------------------------------
    _record(
        "acceptance_criteria_reported",
        bool(acceptance_criteria_reported)
        if acceptance_criteria_reported is not None
        else False,
        "automerge.acceptance.missing_report",
    )

    eligible = not failed and risk_class is RiskClass.LOW

    reason = _build_reason(
        eligible=eligible,
        risk_class=risk_class,
        failed=failed,
    )

    return AutoMergeVerdict(
        eligible=eligible,
        risk_class=risk_class,
        reason=reason,
        blocker_signatures=tuple(blockers),
        satisfied_conditions=tuple(satisfied),
        failed_conditions=tuple(failed),
    )


# ---------------------------------------------------------------------------
# Mistake ledger integration
# ---------------------------------------------------------------------------


def record_automerge_signature(
    ledger: MistakeLedger,
    *,
    role: str,
    signature: str,
    pr_number: int,
    blocker_level: BlockerLevel = BlockerLevel.WARNING,
    postmortem_ref: Optional[str] = None,
) -> None:
    """Persist an ``automerge.*`` signature into the mistake ledger.

    Convenience wrapper so callers (the tech-lead agent's
    pre-merge hook, the GitHub adapter, governance tests) don't
    have to remember the ``pattern="automerge"`` convention.

    Signatures starting with ``automerge.risk-class.high`` are
    forced to :class:`BlockerLevel.BLOCK` regardless of the
    caller's input — that matches conventions §7 row for the
    HIGH-without-approval rail.
    """

    sig = str(signature or "").strip()
    if not sig:
        return
    if not sig.startswith("automerge."):
        sig = f"automerge.{sig}"
    level = blocker_level
    if sig.startswith("automerge.risk-class.high") or sig.startswith(
        "automerge.cycle"
    ):
        level = BlockerLevel.BLOCK
    ledger.record_mistake(
        role=role or "engineering-agent/tech-lead",
        pattern="automerge",
        signature=f"{sig} pr=#{pr_number or 0}",
        postmortem_ref=postmortem_ref,
        blocker_level=level,
    )


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def is_cycle_authorized_from_env(
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """True iff ``YULE_AUTOMERGE_CYCLE`` is set to an active value.

    Accepted truthy values (case-insensitive): non-empty cycle
    name (e.g. ``"F1-F8"``), ``"true"``, ``"yes"``, ``"on"``,
    ``"1"``. Empty / unset / ``"false"`` / ``"off"`` / ``"0"`` /
    ``"no"`` all mean "no active cycle".
    """

    raw = (env if env is not None else os.environ).get(ENV_AUTOMERGE_CYCLE, "")
    text = str(raw or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered in {"false", "off", "0", "no", "disabled"}:
        return False
    # Any other non-empty value (cycle name, "true", "yes", ...) is
    # treated as "operator has explicitly named a cycle".
    return True


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _is_protected_branch(name: str) -> bool:
    if not name:
        return False
    candidate = name.strip().lower()
    if candidate in PROTECTED_BRANCHES:
        return True
    if candidate.startswith("refs/heads/"):
        candidate = candidate.rsplit("/", 1)[-1]
        if candidate in PROTECTED_BRANCHES:
            return True
    for prefix in _PROTECTED_BRANCH_PREFIXES:
        if candidate.startswith(prefix):
            return True
    return False


def _module_matches(prefix: str, module: str) -> bool:
    normalised = (module or "").strip().lstrip("./")
    return normalised.startswith(prefix)


_LABEL_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalise_label(label: str) -> str:
    text = str(label or "").strip().lower()
    tokens = _LABEL_TOKEN_RE.findall(text)
    return tokens[-1] if tokens else text


def _build_reason(
    *,
    eligible: bool,
    risk_class: RiskClass,
    failed: Sequence[str],
) -> str:
    if eligible:
        return f"all 8 conditions satisfied; risk_class={risk_class.value}"
    if risk_class in (RiskClass.HIGH, RiskClass.CRITICAL):
        return (
            f"blocked — risk_class={risk_class.value} requires operator approval"
        )
    if failed:
        return f"blocked — failed_conditions={','.join(failed)}"
    return f"blocked — risk_class={risk_class.value}"
