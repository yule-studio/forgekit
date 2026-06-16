"""Product intake pre-step seam — the PM gate the discussion turn consults.

이 모듈은 **PM intake gate ↔ tech-lead discussion turn 경계** 를 코드 측에서
얇게 잇는 어댑터다. ``build_discussion_turn_response`` 가 engineering 분류/합성
을 시작하기 *전에* 이 seam 을 한 번 부른다.

핵심 원칙 (additive, PM ≠ engineering):

- **제품/기능 요청만** 가로챈다 (``gate.should_intercept``). 일정·잡담·단순
  질문 등 비-제품 요청은 ``intercepted=False`` 로 그대로 통과 — 기존 engineering
  clarification / research / status flow 는 무변경.
- ``clarification_needed`` → PM 결정 질문(번호+옵션+추천)을 **PM clarification**
  으로 surface 한다. 이것은 engineering 의 *기술* clarification 과 명확히
  구분된 라벨/operator status 를 가진다.
- ``spec_ready`` / ``implementation_candidate`` → product packet 요약
  (acceptance / user decisions / implied features / non-goals)을 engineering
  으로 carry 해서, tech-lead 가 raw request 가 아니라 **packet** 위에서 움직이게
  한다.

본 seam 은 외부 I/O 를 하지 않는다 — 순수 PM 코어
(:mod:`yule_engineering.agents.product_intake`)를 reuse 만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from yule_engineering.agents.product_intake.gate import (
    ProductIntakeOutcome,
    run_product_gate,
)
from yule_engineering.agents.product_intake.models import (
    READINESS_CLARIFICATION,
    READINESS_IMPLEMENTATION_CANDIDATE,
    READINESS_RESEARCH_ONLY,
    READINESS_SPEC_READY,
)
from yule_engineering.agents.product_intake.presenter import (
    clarification_lines,
    handoff_summary,
    operator_status_line,
)


# ---------------------------------------------------------------------------
# Operator-facing PM states — engineering 의 OPERATOR_STATE_* 와 의도적으로
# 다른 prefix(``pm_``)를 써서 운영자 surface 가 "PM 단계" 와 "engineering
# 단계" 를 한눈에 구분하게 한다. PM clarification ≠ engineering clarification.
# ---------------------------------------------------------------------------

PM_STATE_CLARIFICATION: str = "pm_clarification_needed"
PM_STATE_HANDOFF_READY: str = "pm_handoff_ready"
PM_STATE_RESEARCH_ONLY: str = "pm_research_only"

# 렌더 본문 라벨 — 사용자가 "지금은 PM 이 기획을 보강하는 단계" 임을 알 수
# 있도록 engineering 의 토의/clarification 헤더와 다른 라벨을 쓴다.
_PM_CLARIFICATION_HEADER: str = "**PM clarification — 기획 결정 질문**"
_PM_CLARIFICATION_INTRO: str = (
    "_제품 요청으로 보입니다. engineering 으로 넘기기 전에 PM 이 먼저 "
    "빠지면 안 되는 결정만 확인합니다 (engineering 의 기술 clarification 과는 "
    "별개입니다)._"
)
_PM_HANDOFF_HEADER: str = "**PM product packet — engineering handoff**"
_PM_HANDOFF_INTRO: str = (
    "_PM 이 요청을 product packet 으로 정리했습니다. tech-lead 는 raw 요청이 "
    "아니라 아래 packet 위에서 움직입니다._"
)


@dataclass(frozen=True)
class ProductIntakeResult:
    """discussion turn 이 소비하는 "product intake 결과".

    ``intercepted=False`` 면 discussion turn 은 이 결과를 무시하고 기존
    engineering flow 를 그대로 진행한다 (비-제품 요청 byte-for-byte 무변경).

    필드:

    - ``intercepted`` — PM gate 가 제품 요청으로 보고 가로챘는가.
    - ``short_circuit`` — discussion turn 이 engineering 분류/합성을 건너뛰고
      이 PM 결과를 그대로 응답으로 써야 하는가. ``clarification_needed`` 일 때만
      True (사용자 결정 대기). handoff-ready 면 False — packet 을 carry 한 뒤
      engineering 으로 계속 진행한다.
    - ``rendered_text`` — short_circuit 일 때 사용자에게 그대로 게시할 PM
      clarification 본문.
    - ``handoff_context`` — handoff-ready 일 때 engineering 합성 본문 앞에 끼울
      product packet 요약 블록 (없으면 빈 문자열).
    - ``operator_status`` — 운영자 surface 가 그대로 라우팅할 수 있는 PM 상태
      dict. ``state`` 는 ``pm_*`` prefix 로 engineering state 와 구분된다.
    - ``outcome`` — 원본 :class:`ProductIntakeOutcome` (디버그/재사용).
    """

    intercepted: bool
    short_circuit: bool = False
    rendered_text: str = ""
    handoff_context: str = ""
    operator_status: Mapping[str, Any] = field(default_factory=dict)
    outcome: Optional[ProductIntakeOutcome] = None


def run_product_intake(
    message_text: str,
    *,
    metadata: Optional[Mapping[str, object]] = None,
) -> ProductIntakeResult:
    """PM intake pre-step. 비-제품 요청이면 ``intercepted=False`` 로 통과.

    discussion turn 은 이 함수를 호출한 뒤:

    - ``intercepted=False`` → 결과 무시, 기존 engineering flow 그대로.
    - ``short_circuit=True`` → ``rendered_text`` 를 게시하고 turn 종료
      (PM 결정 질문 대기).
    - 그 외(handoff-ready) → ``handoff_context`` 를 engineering 본문에 carry
      하고 engineering flow 계속.
    """

    outcome = run_product_gate(message_text, metadata=metadata)
    if not outcome.intercepted:
        return ProductIntakeResult(intercepted=False, outcome=outcome)

    state = outcome.state
    if state == READINESS_CLARIFICATION:
        return _clarification_result(outcome)
    if state in (READINESS_SPEC_READY, READINESS_IMPLEMENTATION_CANDIDATE):
        return _handoff_result(outcome)
    if state == READINESS_RESEARCH_ONLY:
        return _research_result(outcome)

    # blocked / unknown — 가로채긴 했지만 short-circuit 하지 않고 engineering
    # 으로 흘려보낸다. operator 는 PM 상태만 본다.
    return ProductIntakeResult(
        intercepted=True,
        short_circuit=False,
        operator_status=_pm_operator_status(
            state=f"pm_{state}",
            primary_actor="operator",
            outcome=outcome,
        ),
        outcome=outcome,
    )


def _clarification_result(outcome: ProductIntakeOutcome) -> ProductIntakeResult:
    """``clarification_needed`` → PM 결정 질문을 short-circuit 응답으로."""

    questions = list(outcome.clarification_questions) or list(
        clarification_lines(outcome.packet) if outcome.packet else ()
    )
    parts = [_PM_CLARIFICATION_HEADER, _PM_CLARIFICATION_INTRO, ""]
    parts.extend(questions)
    rendered = "\n".join(p for p in parts if p)
    status = _pm_operator_status(
        state=PM_STATE_CLARIFICATION,
        primary_actor="user",
        outcome=outcome,
        remediation=(
            "사용자가 번호로 결정을 답하면 PM 이 packet 을 완성해 engineering "
            "으로 넘깁니다. (이 단계는 engineering 기술 clarification 이 아닙니다.)"
        ),
    )
    return ProductIntakeResult(
        intercepted=True,
        short_circuit=True,
        rendered_text=rendered,
        operator_status=status,
        outcome=outcome,
    )


def _handoff_result(outcome: ProductIntakeOutcome) -> ProductIntakeResult:
    """``spec_ready`` / ``implementation_candidate`` → packet 을 carry."""

    summary_lines = list(handoff_summary(outcome.packet)) if outcome.packet else []
    context_parts = [_PM_HANDOFF_HEADER, _PM_HANDOFF_INTRO, ""]
    context_parts.extend(summary_lines)
    handoff_context = "\n".join(p for p in context_parts if p)
    status = _pm_operator_status(
        state=PM_STATE_HANDOFF_READY,
        primary_actor="tech-lead",
        outcome=outcome,
        remediation=(
            "tech-lead 가 product packet(acceptance / decisions / implied / "
            "non-goals)을 받아 분해합니다. engineering 은 packet 기준으로 움직입니다."
        ),
    )
    return ProductIntakeResult(
        intercepted=True,
        short_circuit=False,
        handoff_context=handoff_context,
        operator_status=status,
        outcome=outcome,
    )


def _research_result(outcome: ProductIntakeOutcome) -> ProductIntakeResult:
    """``research_only`` → 가로채되 engineering research flow 로 흘려보낸다."""

    status = _pm_operator_status(
        state=PM_STATE_RESEARCH_ONLY,
        primary_actor="tech-lead",
        outcome=outcome,
        remediation="조사/분석 요청 — engineering research flow 로 진행합니다.",
    )
    return ProductIntakeResult(
        intercepted=True,
        short_circuit=False,
        operator_status=status,
        outcome=outcome,
    )


def _pm_operator_status(
    *,
    state: str,
    primary_actor: str,
    outcome: ProductIntakeOutcome,
    remediation: str = "",
) -> Mapping[str, Any]:
    """운영자 surface 용 PM 상태 dict.

    ``headline`` 은 PM presenter 의 ``operator_status_line`` 을 그대로 써서
    "PM clarification" vs "engineering handoff ready" 를 한 줄로 구분한다.
    ``layer="product"`` 로 engineering operator_status(layer 없음)와 명시적
    으로 구분된다 — PM clarification ≠ engineering clarification.
    """

    headline = (
        operator_status_line(outcome.packet)
        if outcome.packet is not None
        else f"product intake: {outcome.state}"
    )
    return {
        "layer": "product",
        "state": state,
        "primary_actor": primary_actor,
        "headline": headline,
        "remediation": remediation,
        "product_state": outcome.state,
        "handoff_ready": outcome.handoff_ready,
    }


__all__ = (
    "ProductIntakeResult",
    "run_product_intake",
    "PM_STATE_CLARIFICATION",
    "PM_STATE_HANDOFF_READY",
    "PM_STATE_RESEARCH_ONLY",
)
