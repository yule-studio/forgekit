"""Regenerate provider-budget-evidence.txt — deterministic (fake transport, temp home, no net).

per-provider daily budget enforcement (honest fallback / throttle, no fake) + mode→slot
chat/non-chat separation. Run from repo root with every package src on PYTHONPATH; redirect
stdout into provider-budget-evidence.txt. Regression: tests/forgekit/test_provider_budget.py,
tests/forgekit/test_routing.py.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from forgekit_provider.usage import provider_budget as pb, ledger
from forgekit_provider.policy import provider_ops as ops, provider_config as pc, routing as rt
from forgekit_provider.chat.service import SubmitService


class FakeTransport:
    def __init__(self, reply="live reply"):
        self.reply = reply

    def openai_chat(self, *, endpoint, model, prompt, api_key=""):
        return self.reply

    def ollama_reachable(self, endpoint):
        return True

    def ollama_models(self, endpoint):
        return ("gemma3:latest",)


def banner(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main():
    print("ForgeKit per-provider budget + mode→slot 분리 — deterministic evidence (no fake)")
    print("재현: tests/forgekit/test_provider_budget.py · test_routing.py")

    banner("STEP 1 — per-provider 한도 영속 (set_provider_budget → persist → reload)")
    with tempfile.TemporaryDirectory() as home:
        env = {"FORGEKIT_HOME": home}
        base = ops.set_primary(ops.load_raw_config(env=env), "gemini")
        cfg = ops.set_provider_budget(base, "gemini", 100)
        ok, where = ops.persist_config(cfg, env=env)
        print(f"$ /provider budget gemini 100  → 저장 ok={ok}")
        reloaded = ops.load_raw_config(env=env)
        print(f"$ cat config.json  (재실행 후)\n  budget_policy = {json.dumps(reloaded.get('budget_policy'), ensure_ascii=False)}")
        print(f"  provider_limits(reload) = {pb.provider_limits(reloaded)}  (재실행-후-유지)")

        banner("STEP 2 — 한도 초과 → routing 정직 fallback (gemini ring-fenced → ollama)")
        full = {
            "primary_provider": "gemini", "linked_providers": ["gemini", "ollama"],
            "slot_routing": {"default_chat": "gemini"},
            "fallback_policy": {"slot_fallback_orders": {"default_chat": ["ollama"]}},
            "budget_policy": {"provider_daily_limits": {"gemini": 100}},
        }
        rows = [{"provider": "gemini", "total_tokens": 150, "throttled": False}]
        print(f"  오늘 gemini spent=150 / limit=100 → over={sorted(pb.over_budget_providers(full, rows))}")
        avail = pb.availability(full, rows)
        res = rt.resolve_routing(pc.load_provider_config(full), pc.SLOT_DEFAULT_CHAT, available=avail)
        print(f"  resolve default_chat → actual={res.actual_provider} (status={res.status}, "
              f"fallback_used={res.fallback_used})  ← gemini 건너뛰고 정직 fallback")

        banner("STEP 3 — live submit 경로도 동일 강제 (over-budget head skip → live fallback)")
        ledger.append_event(ledger.UsageEvent(ts=ledger.now_ts(env), provider="gemini",
                                              total_tokens=150, success=True), env=env)
        svc = SubmitService(transport=FakeTransport(), env=env, config=full)
        r = svc.submit("hello")
        print(f"  submit → ok={r.ok}, provider={r.provider_id}, fallback_used={r.fallback_used} "
              f"(gemini 예산초과 → ollama live)")

        # whole chain over budget → honest throttle, NO faked send.
        full2 = {**full, "budget_policy": {"provider_daily_limits": {"gemini": 100, "ollama": 100}}}
        ledger.append_event(ledger.UsageEvent(ts=ledger.now_ts(env), provider="ollama",
                                              total_tokens=150, success=True), env=env)
        r2 = SubmitService(transport=FakeTransport(), env=env, config=full2).submit("hello")
        print(f"  전 chain 초과 → ok={r2.ok}, category={r2.category}, throttled={r2.throttled} "
              f"(faked send 없음)")

    banner("STEP 4 — mode→slot: chat 은 항상 default_chat, 비-chat work 만 mode slot")
    cfg = pc.load_provider_config({
        "primary_provider": "gemini", "linked_providers": ["gemini", "ollama"],
        "slot_routing": {"default_chat": "ollama", "research": "gemini"},
    })
    chat = rt.resolve_submit(cfg, rt.rm.MODE_RESEARCH)                       # kind=chat (default)
    work = rt.resolve_submit(cfg, rt.rm.MODE_RESEARCH, kind=rt.WORK_NONCHAT)
    print(f"  research 모드 CHAT  → slot=default_chat → {chat.actual_provider} (chat 은 mode 로 안 끌려감)")
    print(f"  research 모드 WORK  → slot=research     → {work.actual_provider} (비-chat work 만 mode slot)")
    print(f"  slot_for(research, chat)={rt.slot_for('research', rt.WORK_CHAT)} · "
          f"slot_for(research, nonchat)={rt.slot_for('research', rt.WORK_NONCHAT)}")


if __name__ == "__main__":
    main()
