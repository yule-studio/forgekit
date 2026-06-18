"""Auto orchestration — classify a situation → recommend / safe-switch / escalate.

``auto`` does NOT seize control. It splits into three explicit behaviours:

* **auto-recommend** — classify the ask/context → suggest a mode + the *reason*. No
  switch happens; the operator decides.
* **auto-switch-safe** — switch ONLY when it is safe: never over an explicit operator
  pin, and never *into* a gated/dangerous mode (red-blue, approval-wait) on its own.
* **auto-escalate** — when work is blocked / repeatedly failing, recommend escalation
  (notify the operator) rather than silently retrying.

Pure classification (keyword signals → mode) so it's deterministic + testable. The
reason is always surfaced so the operator sees WHY a mode was recommended/switched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from . import runtime_mode as rm

# the situations auto can classify (each maps to a runtime mode) ----------------
_SIGNALS = (
    # (mode, reason, keyword signals)
    (rm.MODE_RED_BLUE, "보안 드릴/공격-방어 신호",
     ("red team", "blue team", "보안 드릴", "침투", "pentest", "취약", "hardening", "red/blue", "red-blue")),
    (rm.MODE_IDEA_DISCOVERY, "아이디어/경쟁 조사 신호",
     ("아이디어", "idea", "경쟁", "competitor", "시장", "트렌드", "discovery", "saas 아이디어")),
    (rm.MODE_VIDEO_WATCH, "영상/전사/노트 요약 신호",
     ("영상", "video", "youtube", "transcript", "전사", "강의 요약")),
    (rm.MODE_SELF_IMPROVEMENT, "레포/자체 개선 신호",
     ("개선", "리팩토링", "refactor", "tech debt", "self-improve", "repo improvement", "governance gap")),
    (rm.MODE_DELIVERY, "구현/완성/배포 신호",
     ("구현", "완성", "만들어", "deliver", "ship", "기능 추가")),
    (rm.MODE_WATCH, "관측/모니터 신호",
     ("관측", "모니터", "watch", "감시", "상태 확인")),
    (rm.MODE_COST_SAVE, "비용 절감 신호",
     ("비용", "절감", "cost", "cheap", "예산")),
)

# auto NEVER auto-switches INTO these (gated / dangerous) — recommend only.
_NO_AUTO_SWITCH_INTO = frozenset({rm.MODE_RED_BLUE, rm.MODE_APPROVAL_WAIT})

DECISION_RECOMMEND = "recommend"
DECISION_SWITCH_SAFE = "switch-safe"
DECISION_ESCALATE = "escalate"
DECISION_HOLD = "hold"  # explicit operator pin → auto does nothing


@dataclass(frozen=True)
class AutoDecision:
    decision: str
    recommended_mode: str
    reason: str
    switched: bool = False
    requires_operator: bool = False

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "recommended_mode": self.recommended_mode,
            "reason": self.reason,
            "switched": self.switched,
            "requires_operator": self.requires_operator,
        }


def classify(text: str) -> Tuple[str, str]:
    """Classify *text* → ``(mode, reason)``. Defaults to interactive."""

    low = (text or "").lower()
    for mode, reason, signals in _SIGNALS:
        if any(sig in low for sig in signals):
            return mode, reason
    return rm.MODE_INTERACTIVE, "특정 신호 없음 — 기본 interactive"


def auto_recommend(text: str) -> AutoDecision:
    """Suggest a mode + reason. Never switches — the operator decides."""

    mode, reason = classify(text)
    return AutoDecision(DECISION_RECOMMEND, mode, reason, switched=False,
                        requires_operator=mode in _NO_AUTO_SWITCH_INTO)


def auto_switch_safe(text: str, *, current_mode: str, operator_pinned: bool) -> AutoDecision:
    """Switch ONLY when safe: not over an operator pin, not into a gated mode."""

    mode, reason = classify(text)
    if operator_pinned:
        return AutoDecision(DECISION_HOLD, current_mode,
                            "operator 가 모드를 고정함 — auto 가 덮어쓰지 않음", switched=False)
    if mode in _NO_AUTO_SWITCH_INTO:
        return AutoDecision(DECISION_RECOMMEND, mode,
                            f"{reason} — gated 모드라 자동 전환 안 함, 추천만 (operator 승인 필요)",
                            switched=False, requires_operator=True)
    if mode == current_mode:
        return AutoDecision(DECISION_HOLD, mode, f"{reason} — 이미 해당 모드", switched=False)
    return AutoDecision(DECISION_SWITCH_SAFE, mode, reason, switched=True)


def auto_escalate(*, blocked: bool, repeated_failures: int, threshold: int = 3) -> Optional[AutoDecision]:
    """When blocked / repeatedly failing → recommend escalation (not silent retry)."""

    if blocked or repeated_failures >= max(1, threshold):
        return AutoDecision(
            DECISION_ESCALATE, rm.MODE_APPROVAL_WAIT,
            f"blocked={blocked} · 반복 실패 {repeated_failures}회 → operator 에스컬레이션",
            switched=False, requires_operator=True,
        )
    return None


__all__ = (
    "DECISION_RECOMMEND", "DECISION_SWITCH_SAFE", "DECISION_ESCALATE", "DECISION_HOLD",
    "AutoDecision", "classify", "auto_recommend", "auto_switch_safe", "auto_escalate",
)
