"""/provider budget operator surface (next-wave P1 #3) — thin show/set over the
per-provider daily token budget that gw2 enforces (#343).

Console stays a *surface* (ownership §3.1): the policy lives in
``forgekit_provider.usage.provider_budget`` (which providers are over their own daily
limit → routing fallback) and the config writer in
``forgekit_provider.policy.provider_ops.set_provider_budget`` (persists
``budget_policy.provider_daily_limits`` in the canonical config). This module only:

* ``budget_lines`` — render each configured provider's LIVE state (today's spend from the
  usage ledger vs its limit, over-budget flagged honestly);
* ``apply_set_budget`` — validate operator input and apply via the package writer
  (``0`` clears the limit → unbounded; never invents a limit).

No fake numbers: spend comes from the real ledger; "no limit configured" is stated
plainly rather than shown as a fake cap.
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple


def budget_lines(env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """Per-provider daily budget + LIVE today's spend (honest; unconfigured = unbounded)."""

    from forgekit_provider.policy import provider_ops as ops
    from forgekit_provider.usage import provider_budget as pb, read_events, today

    cfg = ops.load_raw_config(env=env)
    try:
        rows = read_events(env=env, day=today(env))   # today's ledger rows → real spend
    except Exception:  # noqa: BLE001 - ledger read must never break the surface
        rows = []
    states = pb.provider_budget_states(cfg, rows)
    if not states:
        return (
            "per-provider budget: 미설정 — 전 provider unbounded (global `/usage` budget 만 적용).",
            "  설정: `/provider budget set <provider> <일일토큰>`  (0 = 해제/unbounded)",
        )
    out = ["per-provider 일일 token budget (live spend / limit):"]
    for s in states:
        flag = "  ⚠ 초과(routing fallback)" if s.over else ""
        out.append(f"  {s.provider:<10} {s.spent}/{s.limit}tok ({int(s.ratio * 100)}%){flag}")
    out.append("  set: `/provider budget set <provider> <일일토큰>`  (0 = 해제)")
    return tuple(out)


def apply_set_budget(pid: str, tokens: str, *, env: Optional[Mapping[str, str]] = None) -> Tuple[bool, str]:
    """Set (or clear with 0) a provider's per-provider daily token limit via the package
    writer, then persist. Validation is honest: non-int / negative is rejected, not coerced."""

    from forgekit_provider.policy import provider_ops as ops

    pid = (pid or "").strip()
    if not pid:
        return False, "provider id 필요 — `/provider budget set <provider> <일일토큰>`"
    try:
        limit = int(tokens)
    except (TypeError, ValueError):
        return False, f"토큰 한도는 정수여야 합니다 — {tokens!r}"
    if limit < 0:
        return False, "토큰 한도는 0 이상 (0 = 해제/unbounded)"
    new_cfg = ops.set_provider_budget(ops.load_raw_config(env=env), pid, limit)
    ok, msg = ops.persist_config(new_cfg, env=env)
    if not ok:
        return False, f"저장 실패: {msg}"
    if limit == 0:
        return True, f"{pid} per-provider budget 해제 (unbounded)"
    return True, f"{pid} per-provider budget = {limit}tok/day 설정"


def usage_lines() -> Tuple[str, ...]:
    return (
        "`/provider budget` — per-provider 일일 token budget (gw2 enforcement #343)",
        "  /provider budget [show]            provider 별 한도 + 오늘 사용량(live)",
        "  /provider budget set <id> <토큰>   한도 설정 (0 = 해제/unbounded)",
    )


__all__ = ("budget_lines", "apply_set_budget", "usage_lines")
