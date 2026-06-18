"""Repeated-failure escalation — surface blocked loops instead of failing silently.

The rule (see ``docs/troubleshooting-mandatory.md``): if the SAME failure repeats
past a threshold (default 3, up to 5), it must NOT stay in the conversation only —
it auto-surfaces to **≥2 places** with a mini-RCA (symptom · evidence · attempted
fixes · why it's failing · alternatives · next step · whether a human is needed).

Design
------
* ``FailureSignature`` groups "the same failure" by ``kind:reason:scope`` — NOT a
  raw string, so a renderer-fallback and a status-surface-unavailable never merge.
* ``FailureEscalator.record_failure`` accumulates occurrences + evidence/attempts.
  Below the threshold it returns an ADVISORY (``2/3 — still retrying``); at/above it
  builds an ``EscalationReport`` and writes the surfaces.
* Surfaces on escalation (always ≥2): the JSON **ledger** + the operator **inbox**
  (+ the live **console alert** the caller renders). A macOS **notification** is an
  opt-in 3rd surface (``FORGEKIT_NOTIFY=1``). When the heavy ``yule_engineering``
  troubleshooting ledger is importable it is also bridged (best-effort).
* Alternatives come from a small remedy knowledge-base keyed by failure kind, merged
  with any caller-supplied alternatives — so the escalation always proposes a way
  forward, not just a count.

Pure-ish + stdlib only (json/os/subprocess), so forgekit-console stays importable
in a bare CI install. All IO is guarded; nothing here ever raises to the caller.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Sequence, Tuple

from ..models import LEVEL_ERROR, LEVEL_WARN, Alert
from ..runtime_paths import escalation_ledger_path, operator_inbox_path

# --- policy knobs -----------------------------------------------------------
DEFAULT_THRESHOLD = 3
MAX_THRESHOLD = 5
ENV_THRESHOLD = "FORGEKIT_BLOCKED_THRESHOLD"
ENV_NOTIFY = "FORGEKIT_NOTIFY"

# --- failure kinds (stable signature prefixes) ------------------------------
KIND_RENDERER = "renderer"
KIND_IMPORT = "import"
KIND_COMMAND = "command"
KIND_POLICY = "policy"
KIND_DEPENDENCY = "dependency"
KIND_STATUS_SURFACE = "status-surface"

# --- surfaces ---------------------------------------------------------------
SURFACE_LEDGER = "ledger"
SURFACE_OPERATOR_INBOX = "operator_inbox"
SURFACE_CONSOLE_ALERT = "console_alert"
SURFACE_NOTIFICATION = "notification"
SURFACE_TROUBLESHOOTING = "troubleshooting_ledger"


def resolve_threshold(env: Optional[Mapping[str, str]] = None) -> int:
    """The blocked threshold: ``FORGEKIT_BLOCKED_THRESHOLD`` clamped to [1, 5], else 3."""

    src = os.environ if env is None else env
    raw = (src.get(ENV_THRESHOLD) or "").strip()
    if raw.isdigit():
        return max(1, min(MAX_THRESHOLD, int(raw)))
    return DEFAULT_THRESHOLD


def notify_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """True when ``FORGEKIT_NOTIFY`` opts into local (macOS) notifications."""

    src = os.environ if env is None else env
    return (src.get(ENV_NOTIFY) or "").strip().lower() in ("1", "true", "on", "yes")


@dataclass(frozen=True)
class FailureSignature:
    """What makes two failures "the same" — kind + reason + optional scope anchor."""

    kind: str
    reason: str
    scope: str = ""

    def key(self) -> str:
        parts = [p.strip().lower() for p in (self.kind, self.reason, self.scope) if p and p.strip()]
        return ":".join("-".join(p.split()) for p in parts)


# remedy KB: kind → (why_failing, alternatives, next_step, needs_operator)
_REMEDIES: Mapping[str, Tuple[str, Tuple[str, ...], str, bool]] = {
    KIND_RENDERER: (
        "터미널이 true raster(tgp/sixel)를 지원하지 않아 fallback 으로 반복 렌더됨",
        ("iTerm2 / WezTerm / Kitty 에서 실행", "Python 3.10+ console env(.venv-console) 사용",
         "FORGEKIT_AVATAR=portrait|mark 로 고정"),
        "권장 터미널에서 `/render` 로 true-raster 여부 확인",
        False,
    ),
    KIND_IMPORT: (
        "필수 라이브러리 import 가 반복 실패(버전/설치 문제 가능)",
        ("`pip install -e '.[console]'` 재설치", "Python 3.10+ 인터프리터 사용", "의존성 버전 점검"),
        "import 대상/버전 확인 후 재설치하고 `/render` 로 재검증",
        True,
    ),
    KIND_DEPENDENCY: (
        "외부 의존성/권한이 반복적으로 충족되지 않음",
        ("자격/토큰/권한 재확인", "네트워크·엔드포인트 접근성 확인", "최소 권한으로 재시도"),
        "필요한 자격/권한을 operator 에게 요청",
        True,
    ),
    KIND_POLICY: (
        "정책 게이트가 같은 이유로 반복 차단",
        ("요청 범위를 정책 허용 범위로 축소", "필요한 승인/권한 카드 요청", "정책 근거 문서 재확인"),
        "승인 매트릭스에 따라 operator 승인/결정 요청",
        True,
    ),
    KIND_STATUS_SURFACE: (
        "상태 surface(runtime/doctor/operator)가 반복적으로 unavailable",
        ("runtime 기동 여부 확인", "repo-root/DB 경로 확인", "yule_engineering 설치 확인"),
        "`/doctor` 로 환경 점검 후 runtime 재기동",
        True,
    ),
    KIND_COMMAND: (
        "같은 명령이 같은 이유로 반복 실패",
        ("명령 인자/대상 재확인", "선행 상태(`/status`,`/doctor`) 점검", "대체 명령 경로 사용"),
        "실패 원인을 좁히고 operator 에게 공유",
        True,
    ),
}
_DEFAULT_REMEDY = (
    "같은 실패가 임계값 이상 반복됨",
    ("입력/환경 재확인", "선행 조건 점검", "대안 경로 시도"),
    "원인을 좁혀 operator 에게 보고",
    True,
)


@dataclass(frozen=True)
class EscalationReport:
    """A mini-RCA produced when a signature crosses the threshold."""

    signature_key: str
    kind: str
    count: int
    threshold: int
    symptom: str
    evidence: Tuple[str, ...]
    attempted_fixes: Tuple[str, ...]
    why_failing: str
    alternatives: Tuple[str, ...]
    next_step: str
    needs_operator: bool

    def to_payload(self) -> dict:
        return {
            "signature_key": self.signature_key,
            "kind": self.kind,
            "count": self.count,
            "threshold": self.threshold,
            "symptom": self.symptom,
            "evidence": list(self.evidence),
            "attempted_fixes": list(self.attempted_fixes),
            "why_failing": self.why_failing,
            "alternatives": list(self.alternatives),
            "next_step": self.next_step,
            "needs_operator": self.needs_operator,
        }

    def to_lines(self) -> Tuple[str, ...]:
        """Human-readable RCA block (Rich markup) for the transcript / `/blocked`."""

        lines = [
            f"[b]⚠ 반복 실패 에스컬레이션[/b] — {self.signature_key} "
            f"({self.count}/{self.threshold}회)",
            f"  • 증상: {self.symptom}",
        ]
        if self.evidence:
            lines.append(f"  • 증거: {self.evidence[-1]}")
        if self.attempted_fixes:
            lines.append(f"  • 시도한 것: {', '.join(dict.fromkeys(self.attempted_fixes))}")
        lines.append(f"  • 왜 안 되나: {self.why_failing}")
        lines.append("  • 대안:")
        lines.extend(f"      - {alt}" for alt in self.alternatives)
        lines.append(f"  • 다음 단계: {self.next_step}")
        if self.needs_operator:
            lines.append("  • [b]operator 답변/승인/결정 필요[/b] → `#승인-대기` / `/blocked`")
        return tuple(lines)


@dataclass(frozen=True)
class EscalationOutcome:
    """The result of recording one failure occurrence."""

    escalated: bool
    advisory: str
    count: int
    threshold: int
    report: Optional[EscalationReport] = None
    surfaces: Tuple[str, ...] = ()

    def meets_minimum_surfaces(self, *, minimum: int = 2) -> bool:
        return len(self.surfaces) >= max(1, int(minimum))


@dataclass
class _Occurrence:
    kind: str
    reason: str
    scope: str
    count: int = 0
    symptoms: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    attempts: List[str] = field(default_factory=list)
    alternatives: List[str] = field(default_factory=list)


def macos_notifier(title: str, body: str) -> bool:
    """Best-effort macOS notification via ``osascript``. Returns dispatched?

    Never raises; returns False when not on macOS / osascript missing / it errors.
    This is the opt-in 3rd surface — the core escalation never depends on it.
    """

    try:
        import shutil
        import subprocess  # noqa: WPS433 - guarded, opt-in only

        if not shutil.which("osascript"):
            return False
        safe_title = title.replace('"', "'")
        safe_body = body.replace('"', "'")
        subprocess.run(
            ["osascript", "-e", f'display notification "{safe_body}" with title "{safe_title}"'],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return True
    except Exception:  # noqa: BLE001 - notification must never break escalation
        return False


@dataclass
class FailureEscalator:
    """Tracks repeated failures and surfaces them past the threshold.

    Inject ``env`` (defaults to the live environment), explicit ``ledger_path`` /
    ``inbox_path`` (tests point these at a tempdir), and a ``notifier`` callable
    ``(title, body) -> dispatched`` (tests inject a fake to assert dispatch without
    touching the OS).
    """

    env: Optional[Mapping[str, str]] = None
    threshold: Optional[int] = None
    ledger_path: Optional[Path] = None
    inbox_path: Optional[Path] = None
    notifier: Optional[Callable[[str, str], bool]] = None
    bridge_troubleshooting: bool = True
    _occ: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.threshold is None:
            self.threshold = resolve_threshold(self.env)
        self.threshold = max(1, min(MAX_THRESHOLD, int(self.threshold)))
        if self.ledger_path is None:
            self.ledger_path = escalation_ledger_path(self.env)
        if self.inbox_path is None:
            self.inbox_path = operator_inbox_path(self.env)
        if self.notifier is None:
            self.notifier = macos_notifier

    # --- recording ----------------------------------------------------------
    def record_failure(
        self,
        signature: FailureSignature,
        *,
        symptom: str = "",
        evidence: str = "",
        attempted_fix: str = "",
        alternatives: Sequence[str] = (),
        timestamp: str = "",
    ) -> EscalationOutcome:
        """Record one occurrence. Below threshold → advisory; at/above → escalate."""

        key = signature.key()
        occ = self._occ.get(key)
        if occ is None:
            occ = _Occurrence(kind=signature.kind, reason=signature.reason, scope=signature.scope)
            self._occ[key] = occ
        occ.count += 1
        if symptom:
            occ.symptoms.append(symptom)
        if evidence:
            occ.evidence.append(evidence)
        if attempted_fix:
            occ.attempts.append(attempted_fix)
        occ.alternatives.extend(a for a in alternatives if a)

        if occ.count < self.threshold:
            advisory = (
                f"[dim]↻ {key} 반복 {occ.count}/{self.threshold} — 아직 시도 중 "
                f"(임계값 도달 시 자동 에스컬레이션)[/dim]"
            )
            return EscalationOutcome(
                escalated=False, advisory=advisory, count=occ.count, threshold=self.threshold
            )

        report = self._build_report(occ, key)
        surfaces = self._write_surfaces(report, timestamp)
        return EscalationOutcome(
            escalated=True,
            advisory=f"⚠ {key} {occ.count}/{self.threshold}회 — 에스컬레이션 ({', '.join(surfaces)})",
            count=occ.count,
            threshold=self.threshold,
            report=report,
            surfaces=surfaces,
        )

    def occurrences(self) -> int:
        return len(self._occ)

    # --- internals ----------------------------------------------------------
    def _build_report(self, occ: _Occurrence, key: str) -> EscalationReport:
        why, kb_alts, next_step, needs_op = _REMEDIES.get(occ.kind, _DEFAULT_REMEDY)
        merged = tuple(dict.fromkeys([*occ.alternatives, *kb_alts]))  # dedupe, keep order
        symptom = occ.symptoms[-1] if occ.symptoms else (occ.reason or key)
        return EscalationReport(
            signature_key=key,
            kind=occ.kind,
            count=occ.count,
            threshold=self.threshold,
            symptom=symptom,
            evidence=tuple(dict.fromkeys(occ.evidence)),
            attempted_fixes=tuple(dict.fromkeys(occ.attempts)),
            why_failing=why,
            alternatives=merged,
            next_step=next_step,
            needs_operator=needs_op,
        )

    def _write_surfaces(self, report: EscalationReport, timestamp: str) -> Tuple[str, ...]:
        surfaces: List[str] = []
        payload = {**report.to_payload(), "recorded_at": timestamp}
        # 1) escalation ledger (always)
        if _append_json(self.ledger_path, payload):
            surfaces.append(SURFACE_LEDGER)
        # 2) operator inbox (always — guarantees ≥2 durable surfaces)
        inbox_entry = {
            "request_type": "DECISION" if report.needs_operator else "INFO",
            "title": f"반복 실패: {report.signature_key}",
            "next_step": report.next_step,
            "needs_operator": report.needs_operator,
            **payload,
        }
        if _append_json(self.inbox_path, inbox_entry):
            surfaces.append(SURFACE_OPERATOR_INBOX)
        # 3) live console alert is always available to the caller (it renders it)
        surfaces.append(SURFACE_CONSOLE_ALERT)
        # 4) macOS notification (opt-in)
        if notify_enabled(self.env) and self.notifier is not None:
            title = f"forgekit: 반복 실패 {report.count}/{report.threshold}"
            body = f"{report.signature_key} — {report.next_step}"
            try:
                if self.notifier(title, body):
                    surfaces.append(SURFACE_NOTIFICATION)
            except Exception:  # noqa: BLE001
                pass
        # 5) bridge to the heavy troubleshooting ledger when available (best-effort)
        if self.bridge_troubleshooting and _bridge_troubleshooting(report):
            surfaces.append(SURFACE_TROUBLESHOOTING)
        return tuple(surfaces)

    # --- live console surface ----------------------------------------------
    def as_alert(self, outcome: EscalationOutcome) -> Optional[Alert]:
        """A forgekit :class:`Alert` for the live console (None below threshold)."""

        if not outcome.escalated or outcome.report is None:
            return None
        return console_alert_for(outcome.report)


def console_alert_for(report: EscalationReport) -> Alert:
    level = LEVEL_ERROR if report.needs_operator else LEVEL_WARN
    return Alert(
        level,
        f"반복 실패 {report.signature_key} {report.count}/{report.threshold}회 — {report.next_step}",
    )


# --- ledger / inbox readers (for the /blocked surface) ----------------------
def read_escalations(path: Path) -> Tuple[dict, ...]:
    """Read escalation/inbox records from *path* (oldest-first). Empty on miss."""

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ()
    try:
        data = json.loads(raw)
    except ValueError:
        return ()
    return tuple(data) if isinstance(data, list) else ()


def open_escalation_lines(env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """Operator-facing summary of escalated repeated failures (for ``/blocked``).

    Reads the persistent ledger so it works even in a fresh session / with the
    debug flag off. Dedupes by signature (latest wins). Pure given *env*.
    """

    records = read_escalations(escalation_ledger_path(env))
    if not records:
        return (
            "[dim]blocked: 없음 — 반복 실패가 임계값을 넘지 않았습니다.[/dim]",
            f"[dim](임계값 {resolve_threshold(env)}회 · FORGEKIT_BLOCKED_THRESHOLD 로 조정)[/dim]",
        )
    latest: dict = {}
    for rec in records:
        latest[rec.get("signature_key", "?")] = rec
    lines = [f"[b]blocked / 반복 실패[/b] — {len(latest)}건 (임계값 {resolve_threshold(env)}회)"]
    for key, rec in latest.items():
        flag = "  [b](operator 필요)[/b]" if rec.get("needs_operator") else ""
        lines.append(f"  • {key}  {rec.get('count')}/{rec.get('threshold')}회 — {rec.get('next_step', '')}{flag}")
        alts = rec.get("alternatives") or ()
        if alts:
            lines.append(f"      대안: {', '.join(alts[:3])}")
    return tuple(lines)


def _append_json(path: Optional[Path], entry: Mapping, *, max_entries: int = 200) -> bool:
    if path is None:
        return False
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if p.exists():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    existing = loaded
            except ValueError:
                existing = []
        existing.append(dict(entry))
        if len(existing) > max_entries:
            existing = existing[-max_entries:]
        p.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def _bridge_troubleshooting(report: EscalationReport) -> bool:
    """Best-effort: mirror into the heavy yule troubleshooting ledger if importable."""

    try:
        from yule_engineering.agents.lifecycle.troubleshooting_ledger import (  # noqa: WPS433
            TroubleshootingLedger,
            default_ledger_path,
        )
        from yule_engineering.agents.lifecycle.troubleshooting_record import CaptureReason
    except Exception:  # noqa: BLE001 - heavy package absent → skip the bridge
        return False
    try:
        ledger = TroubleshootingLedger(ledger_path=default_ledger_path())
        ledger.capture(
            title=f"forgekit 반복 실패: {report.signature_key}",
            capture_reason=CaptureReason.FALLBACK_TRIGGERED,
            detected_by="tooling/forgekit-console",
            owner_role="platform-runtime-engineer",
            scope=f"forgekit/{report.kind}",
            symptom=report.symptom,
            exact_evidence="\n".join(report.evidence),
            attempted_fix="; ".join(report.attempted_fixes),
            problem_signature=report.signature_key,
            followup_required=report.needs_operator,
        )
        return True
    except Exception:  # noqa: BLE001 - bridge is optional, never fatal
        return False


__all__ = (
    "DEFAULT_THRESHOLD",
    "MAX_THRESHOLD",
    "ENV_THRESHOLD",
    "ENV_NOTIFY",
    "KIND_RENDERER",
    "KIND_IMPORT",
    "KIND_COMMAND",
    "KIND_POLICY",
    "KIND_DEPENDENCY",
    "KIND_STATUS_SURFACE",
    "SURFACE_LEDGER",
    "SURFACE_OPERATOR_INBOX",
    "SURFACE_CONSOLE_ALERT",
    "SURFACE_NOTIFICATION",
    "SURFACE_TROUBLESHOOTING",
    "resolve_threshold",
    "notify_enabled",
    "FailureSignature",
    "EscalationReport",
    "EscalationOutcome",
    "FailureEscalator",
    "console_alert_for",
    "macos_notifier",
    "read_escalations",
    "open_escalation_lines",
)
