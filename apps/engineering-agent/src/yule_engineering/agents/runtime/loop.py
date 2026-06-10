"""Skeleton runtime loop.

``run_runtime_loop`` walks a :class:`RuntimeInput` through seven
stages — Observe → Understand → Recall → Research → Decide → Act →
Record — and returns a :class:`RuntimeResult`. Every stage is a
pluggable callable so:

- production wiring (Phase 4+) can pass real classifiers / lookups /
  IO actors,
- tests can pass mocks that record call ordering or short-circuit a
  stage,
- callers that don't care about a stage can pass ``None`` and the
  loop will use a safe deterministic default.

The default observers/decoders here are intentionally minimal — Phase
2 / 3 / 4 land the real logic. The loop must already accept enough
inputs for those phases without breaking the contract.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable, Optional

from .models import (
    ACTION_NOOP,
    ACTION_REPLY,
    INTENT_GENERAL_CHAT,
    RuntimeAction,
    RuntimeDecision,
    RuntimeInput,
    RuntimeIntent,
    RuntimeObservation,
    RuntimeRecallResult,
    RuntimeRecord,
    RuntimeResearchPlan,
    RuntimeResult,
)


ObserveFn = Callable[[RuntimeInput], RuntimeObservation]
UnderstandFn = Callable[[RuntimeObservation, RuntimeInput], RuntimeIntent]
RecallFn = Callable[[RuntimeObservation, RuntimeIntent, RuntimeInput], RuntimeRecallResult]
ResearchFn = Callable[
    [RuntimeObservation, RuntimeIntent, RuntimeRecallResult, RuntimeInput],
    RuntimeResearchPlan,
]
DecideFn = Callable[
    [
        RuntimeObservation,
        RuntimeIntent,
        RuntimeRecallResult,
        RuntimeResearchPlan,
        RuntimeInput,
    ],
    RuntimeDecision,
]
ActFn = Callable[[RuntimeDecision, RuntimeInput], "tuple[RuntimeAction, ...]"]
RecordFn = Callable[[RuntimeResult, RuntimeInput], "tuple[RuntimeRecord, ...]"]


_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _default_observe(input_: RuntimeInput) -> RuntimeObservation:
    """Pure normalisation: collapse whitespace, extract URLs.

    Stays free of IO so tests can inject a custom observe_fn that
    e.g. attaches Discord channel name; the default one is enough for
    Phase 1's contract tests.
    """

    raw = input_.message_text or ""
    normalized = _WHITESPACE_RE.sub(" ", raw).strip().lower()
    extracted = tuple(_URL_RE.findall(raw))
    return RuntimeObservation(
        role_id=input_.role_id,
        message_text=raw,
        normalized_text=normalized,
        channel_id=input_.channel_id,
        thread_id=input_.thread_id,
        author_id=input_.author_id,
        message_id=input_.message_id,
        extracted_urls=extracted,
        has_attachments=bool(input_.attachments),
        received_at=input_.received_at,
        last_proposed_prompt=input_.last_proposed_prompt,
    )


def _default_understand(_obs: RuntimeObservation, _input: RuntimeInput) -> RuntimeIntent:
    """Phase 1 default: everything is general chat.

    Phase 2 replaces this with a real deterministic classifier. The
    skeleton's contract is just "always produce a valid intent" so
    Decide / Act always have something to act on.
    """

    return RuntimeIntent(intent_id=INTENT_GENERAL_CHAT, confidence="low", reason="default")


def _default_recall(
    _obs: RuntimeObservation,
    _intent: RuntimeIntent,
    _input: RuntimeInput,
) -> RuntimeRecallResult:
    return RuntimeRecallResult(confidence="low", reason="default")


def _default_research(
    _obs: RuntimeObservation,
    _intent: RuntimeIntent,
    _recall: RuntimeRecallResult,
    _input: RuntimeInput,
) -> RuntimeResearchPlan:
    return RuntimeResearchPlan(run=False, reason="default")


def _default_decide(
    _obs: RuntimeObservation,
    intent: RuntimeIntent,
    _recall: RuntimeRecallResult,
    plan: RuntimeResearchPlan,
    _input: RuntimeInput,
) -> RuntimeDecision:
    return RuntimeDecision(
        intent=intent,
        research_plan=plan,
        actions=(RuntimeAction(action_id=ACTION_NOOP, reason="default"),),
        notes="default-decision",
    )


def _default_act(
    decision: RuntimeDecision,
    _input: RuntimeInput,
) -> "tuple[RuntimeAction, ...]":
    """Default Act echoes the decision's actions verbatim.

    Real wiring will translate the action_id into an actual Discord
    send / forum publish / state update; the default is a pure
    pass-through so contract tests can assert ordering without IO.
    """

    return tuple(decision.actions)


def _default_record(
    _result: RuntimeResult,
    _input: RuntimeInput,
) -> "tuple[RuntimeRecord, ...]":
    return ()


def run_runtime_loop(
    input_: RuntimeInput,
    *,
    observe_fn: Optional[ObserveFn] = None,
    understand_fn: Optional[UnderstandFn] = None,
    recall_fn: Optional[RecallFn] = None,
    research_fn: Optional[ResearchFn] = None,
    decide_fn: Optional[DecideFn] = None,
    act_fn: Optional[ActFn] = None,
    record_fn: Optional[RecordFn] = None,
) -> RuntimeResult:
    """Drive *input_* through the seven-stage loop and return a result.

    Stage ordering is fixed: Observe → Understand → Recall → Research
    → Decide → Act → Record. Each stage receives the prior stages'
    outputs so callers can build context cheaply. A stage callable
    that raises is caught and surfaced via :attr:`RuntimeResult.error`
    so a single misbehaving stage does not crash the whole bot.

    The result is always returned with the most-recent-known fields
    filled — even after an error — so the caller can show whichever
    stages already completed (e.g. intent succeeded but recall raised).
    """

    obs_fn = observe_fn or _default_observe
    und_fn = understand_fn or _default_understand
    rec_fn = recall_fn or _default_recall
    res_fn = research_fn or _default_research
    dec_fn = decide_fn or _default_decide
    act_fn_ = act_fn or _default_act
    rec_record_fn = record_fn or _default_record

    error: Optional[str] = None

    try:
        observation = obs_fn(input_)
    except Exception as exc:  # noqa: BLE001 - never crash the bot from a stage
        return RuntimeResult(
            role_id=input_.role_id,
            observation=RuntimeObservation(
                role_id=input_.role_id,
                message_text=input_.message_text,
            ),
            intent=RuntimeIntent(intent_id=INTENT_GENERAL_CHAT, reason="observe-failed"),
            recall=RuntimeRecallResult(),
            research_plan=RuntimeResearchPlan(),
            decision=RuntimeDecision(
                intent=RuntimeIntent(
                    intent_id=INTENT_GENERAL_CHAT, reason="observe-failed"
                ),
            ),
            error=f"observe: {exc}",
        )

    intent: RuntimeIntent
    try:
        intent = und_fn(observation, input_)
    except Exception as exc:  # noqa: BLE001
        intent = RuntimeIntent(
            intent_id=INTENT_GENERAL_CHAT,
            confidence="low",
            reason=f"understand-failed: {exc}",
        )
        error = _join_error(error, f"understand: {exc}")

    try:
        recall = rec_fn(observation, intent, input_)
    except Exception as exc:  # noqa: BLE001
        recall = RuntimeRecallResult(reason=f"recall-failed: {exc}")
        error = _join_error(error, f"recall: {exc}")

    try:
        plan = res_fn(observation, intent, recall, input_)
    except Exception as exc:  # noqa: BLE001
        plan = RuntimeResearchPlan(reason=f"research-failed: {exc}")
        error = _join_error(error, f"research: {exc}")

    try:
        decision = dec_fn(observation, intent, recall, plan, input_)
    except Exception as exc:  # noqa: BLE001
        decision = RuntimeDecision(
            intent=intent,
            research_plan=plan,
            actions=(
                RuntimeAction(
                    action_id=ACTION_REPLY,
                    payload={"text": "내부 처리 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요."},
                    reason="decide-failed",
                ),
            ),
            notes=f"decide-failed: {exc}",
        )
        error = _join_error(error, f"decide: {exc}")

    try:
        actions_taken = act_fn_(decision, input_)
    except Exception as exc:  # noqa: BLE001
        actions_taken = ()
        error = _join_error(error, f"act: {exc}")

    pre_record = RuntimeResult(
        role_id=input_.role_id,
        observation=observation,
        intent=intent,
        recall=recall,
        research_plan=plan,
        decision=decision,
        actions_taken=tuple(actions_taken),
        records=(),
        error=error,
    )

    try:
        records = rec_record_fn(pre_record, input_)
    except Exception as exc:  # noqa: BLE001
        records = ()
        error = _join_error(error, f"record: {exc}")

    return replace(pre_record, records=tuple(records), error=error)


def _join_error(prev: Optional[str], new: str) -> str:
    return f"{prev} · {new}" if prev else new


__all__ = (
    "run_runtime_loop",
    "ObserveFn",
    "UnderstandFn",
    "RecallFn",
    "ResearchFn",
    "DecideFn",
    "ActFn",
    "RecordFn",
)
