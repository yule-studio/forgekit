"""Regenerate setup-bootstrap-evidence.txt — deterministic (fake probe, no real IO).

Run from the repo root with every package src on PYTHONPATH (see README). Prints to stdout;
redirect into ``setup-bootstrap-evidence.txt``. The companion regression test is
``tests/forgekit/test_bootstrap.py``.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from forgekit_console import bootstrap as b
from forgekit_provider.policy import provider_ops as ops
from forgekit_provider_connect import wizard
from forgekit_runtime.runtime import heartbeat as HB
from forgekit_runtime.runtime.daemon import BoundedDaemon, TickOutcome
from forgekit_runtime.runtime import surface as daemon_surface
from hephaistos import nexus_ops as nops


class FakeProbe:
    """Deterministic probe (no real IO) so this evidence is reproducible in CI."""

    def __init__(self, *, claude=None, codex=None, gemini_key=False, ollama_up=False, models=()):
        self._c, self._x, self._g, self._o, self._m = claude, codex, gemini_key, ollama_up, models

    def cli_authenticated(self, pid):
        return {"claude": self._c, "codex": self._x}.get(pid)

    def api_key(self, pid, env=None):
        return "key" if (pid == "gemini" and self._g) else ""

    def daemon_reachable(self, ep):
        return self._o

    def installed_models(self, ep):
        return self._m


def banner(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main() -> None:
    print("ForgeKit 컨트롤플레인 부트스트랩 — 한 화면 정직 집계 (deterministic fake probe)")
    print("provider · knowledge(nexus/vault) · toolchain → 단일 canonical ~/.forgekit/config.json")
    print("재현: tests/forgekit/test_bootstrap.py / 이 스크립트(fake probe, no real IO)")

    with tempfile.TemporaryDirectory() as home, \
            tempfile.TemporaryDirectory() as vault, \
            tempfile.TemporaryDirectory() as repo:
        env = {"FORGEKIT_HOME": home}
        # repo with a real repo-local manifest (no guess) so the toolchain lane is honest.
        (Path(repo) / ".tool-versions").write_text("python 3.13.1\nnodejs 20.11.0\n", encoding="utf-8")
        # before any connection: only claude/codex CLI attached (routing-only) → NO live lane.
        pre = FakeProbe(claude=True, codex=True, gemini_key=False, ollama_up=False)

        banner("STEP 1 — `/setup` (fresh: no provider live lane, no nexus, manifest present)")
        for ln in b.bootstrap_lines(env=env, probe=pre, repo_root=Path(repo)):
            print(ln)

        banner("STEP 2 — `/setup apply` (persist recommended four-brain preset) + `/nexus set <vault>`")
        # now the operator also has a gemini key + ollama model (live lane appears) — honest, verified.
        live = FakeProbe(claude=True, codex=True, gemini_key=True, ollama_up=True, models=("llama3",))
        ok, msg, _ = wizard.apply_recommended(env=env, probe=live)
        print(f"$ /setup apply\n{msg}")
        okv, msgv = nops.apply_set_root(vault, env=env)
        print(f"\n$ /nexus set {vault}\n  {msgv}")

        banner("STEP 3 — RESTART SIMULATION: re-read canonical config from disk (no in-memory state)")
        reloaded = ops.load_raw_config(env=env)
        print(f"$ cat {home}/config.json")
        print(json.dumps(reloaded, ensure_ascii=False, indent=2))

        banner("STEP 4 — `/setup` after restart (settings persisted → verdict ready)")
        for ln in b.bootstrap_lines(env=env, probe=live, repo_root=Path(repo)):
            print(ln)

        bs = b.assess_bootstrap(env=env, probe=live, repo_root=Path(repo))
        banner("HONEST AGGREGATE (machine-readable, incl. 5-state provider taxonomy)")
        print(json.dumps(bs.to_dict(), ensure_ascii=False, indent=2))

        banner("STEP 5 — always-on daemon RESUME (restart continues tick numbering, not cold-start)")
        # a prior run left off at tick 5; a restart (e.g. launchd KeepAlive) resumes continuity.
        HB.write_heartbeat(HB.Heartbeat(status=HB.STATUS_STOPPED, tick=5, ts="(prev run)", pid=111,
                                        note="prev run stopped"), env=env)
        d = BoundedDaemon(max_ticks=2, env=env, sleep_fn=lambda s: None, pid=222, resume=True)
        r = d.serve(lambda t: TickOutcome(summary=f"tick {t}"))
        print(f"$ forgekit runtime serve --max-ticks 2   (prior heartbeat tick=5)")
        print(f"  resumed_from = {r.resumed_from} · 이 run tick = {r.resumed_from+1}..{r.ticks} "
              f"(this-run {r.ticks - r.resumed_from}, heartbeat tick 연속)")
        print("$ /daemon  (status surface)")
        for ln in daemon_surface.daemon_status_lines(env=env):
            print(f"  {ln}")


if __name__ == "__main__":
    main()
