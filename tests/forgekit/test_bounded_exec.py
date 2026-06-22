"""GW4-B physical execution — REAL bounded write + git commit + vault evidence.

Proves the opt-in apply path (``apply_approved_packet`` / ``execute_approved_packet(...,
apply=True, mutator=...)``) actually performs a bounded, verified file change in a TEMP
git repo and lands a real commit carrying the ``Forgekit-Agent`` trailer — WITHOUT ever
faking execution and WITHOUT touching the real repo:

- safe + authorized + mutator → real file change + verified + real commit (valid trailer)
  + goal execution/verification evidence + vault note when a vault_root is configured;
- vault_root unset → evidence to the goal store, vault write skipped honestly (no error);
- risky / destructive packet → no write, no commit, honest outcome;
- caps exceeded → rollback, no commit;
- verify fail → rollback, no commit;
- default ``execute_approved_packet(goal, env=env)`` (no apply/mutator) → UNCHANGED
  authorize+evidence only, NO repo mutation (regression guard for console-approve / #348).

Hermetic: a tempfile git repo is initialised per test; ``$FORGEKIT_HOME`` is a tempdir so
goal-store writes stay isolated. The real repository is NEVER used.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from forgekit_goal import Goal, GoalStatus, GoalStore, transitions
from forgekit_runtime.autopilot.runner import BoundedMutator
from forgekit_runtime.selfimprove import (
    OUTCOME_BLOCKED,
    OUTCOME_EXECUTED,
    apply_approved_packet,
    execute_approved_packet,
    goal_tick,
)


class _Signal:
    def __init__(self, text: str) -> None:
        self.text = text


def _clock():
    n = {"i": 0}

    def now() -> str:
        n["i"] += 1
        return f"2026-06-22T05:00:{n['i']:02d}+00:00"

    return now


def _git(repo: str, *args: str):
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=False)


def _init_repo(path: str) -> None:
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "seed@forgekit.local")
    _git(path, "config", "user.name", "seed")
    (Path(path) / "README.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "seed")


class BoundedExecTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._repo = tempfile.TemporaryDirectory()  # the autopilot scan repo (for tick)
        self._gitrepo = tempfile.TemporaryDirectory()  # the TEMP git repo we mutate
        _init_repo(self._gitrepo.name)
        self.env = {"FORGEKIT_HOME": self._home.name}

    def tearDown(self) -> None:
        self._home.cleanup()
        self._repo.cleanup()
        self._gitrepo.cleanup()

    def _goal_with_packet(self, signal_text: str) -> Goal:
        now = _clock()
        g = Goal.create("self-manage ForgeKit", mode="auto", now=now)
        g = transitions.apply(g, GoalStatus.ACTIVE, now=now)
        res = goal_tick.tick_goal(g, self._repo.name, signals=[_Signal(signal_text)], now=now)
        GoalStore(env=self.env).save(res.goal)
        return res.goal

    def _mutator(self, **kw) -> BoundedMutator:
        return BoundedMutator(repo_root=Path(self._gitrepo.name), **kw)

    # --- safe + authorized → real write + verified + real commit + vault note ----
    def test_safe_apply_real_write_commit_and_vault(self) -> None:
        vault = tempfile.TemporaryDirectory()
        self.addCleanup(vault.cleanup)
        env = dict(self.env)
        env["FORGEKIT_NEXUS_ROOT"] = vault.name

        goal = self._goal_with_packet("콘솔 도움말 문구 개선")
        out = apply_approved_packet(goal, self._mutator(), self._gitrepo.name, env=env)

        self.assertEqual(out.outcome, OUTCOME_EXECUTED)
        self.assertTrue(out.executed)
        self.assertTrue(out.applied)
        self.assertEqual(out.action_class, "safe")
        self.assertTrue(out.commit_sha)
        self.assertTrue(out.changed_path)

        # a REAL file change landed under runs/
        changed = Path(self._gitrepo.name) / out.changed_path
        self.assertTrue(changed.is_file())
        self.assertTrue(out.changed_path.startswith("runs/"))

        # a REAL commit exists with that sha and the changed file
        head = _git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(head, out.commit_sha)
        log = _git(self._gitrepo.name, "show", "--format=%an <%ae>%n%B", "--no-patch", head).stdout
        self.assertIn("Forgekit-Agent:", log)
        self.assertIn(f"Forgekit-Agent: {out.executor_id}", log)
        # committed by the executor identity (forgekit author), not the seed author
        self.assertNotIn("seed <seed@forgekit.local>", log)
        # the changed file is actually in the committed tree (quotepath=false → raw utf-8)
        tree = _git(self._gitrepo.name, "-c", "core.quotepath=false",
                    "ls-tree", "-r", "--name-only", head).stdout.splitlines()
        self.assertIn(out.changed_path, tree)

        # goal evidence recorded execution + verification with the sha
        reloaded = GoalStore(env=env).get(goal.id)
        kinds = [e.kind for e in reloaded.evidence]
        self.assertIn("execution", kinds)
        self.assertIn("verification", kinds)
        exec_ev = next(e for e in reloaded.evidence if e.kind == "execution")
        self.assertIn(out.commit_sha, exec_ev.summary)

        # vault note actually written (vault_root configured)
        self.assertTrue(out.vault_note)
        self.assertTrue(Path(out.vault_note).is_file())
        note_text = Path(out.vault_note).read_text(encoding="utf-8")
        self.assertIn(out.commit_sha, note_text)

        # never marked done
        self.assertNotEqual(reloaded.status, GoalStatus.DONE)

    # --- vault_root unset → evidence to goal, vault skipped honestly --------------
    def test_safe_apply_vault_unset_skips_honestly(self) -> None:
        goal = self._goal_with_packet("콘솔 도움말 문구 개선")
        out = apply_approved_packet(goal, self._mutator(), self._gitrepo.name, env=self.env)

        self.assertTrue(out.applied)
        self.assertTrue(out.commit_sha)
        self.assertEqual(out.vault_note, "")  # honest skip — no fake path

        reloaded = GoalStore(env=self.env).get(goal.id)
        self.assertIn("execution", [e.kind for e in reloaded.evidence])

    # --- risky / destructive → no write, no commit, honest outcome ---------------
    def test_risky_apply_no_write_no_commit(self) -> None:
        before = _git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip()
        goal = self._goal_with_packet("auth 권한 흐름 대규모 변경")
        out = apply_approved_packet(goal, self._mutator(), self._gitrepo.name, env=self.env)

        self.assertEqual(out.outcome, OUTCOME_BLOCKED)
        self.assertFalse(out.executed)
        self.assertFalse(out.applied)
        self.assertEqual(out.commit_sha, "")
        # HEAD unchanged → no commit happened
        self.assertEqual(_git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip(), before)
        # no execution evidence
        reloaded = GoalStore(env=self.env).get(goal.id)
        self.assertNotIn("execution", [e.kind for e in reloaded.evidence])

    def test_destructive_apply_no_write_no_commit(self) -> None:
        before = _git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip()
        goal = self._goal_with_packet("deploy 시크릿 회전")
        out = apply_approved_packet(goal, self._mutator(), self._gitrepo.name, env=self.env)
        self.assertEqual(out.outcome, OUTCOME_BLOCKED)
        self.assertFalse(out.applied)
        self.assertEqual(_git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip(), before)

    # --- caps exceeded → rollback, no commit ------------------------------------
    def test_caps_exceeded_rollback_no_commit(self) -> None:
        before = _git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip()
        goal = self._goal_with_packet("콘솔 도움말 문구 개선")
        # diff cap of 1 line forces the note (multi-line) over the cap → refuse + rollback
        out = apply_approved_packet(
            goal, self._mutator(max_diff_lines=1), self._gitrepo.name, env=self.env)

        self.assertEqual(out.outcome, OUTCOME_BLOCKED)
        self.assertFalse(out.applied)
        self.assertEqual(out.commit_sha, "")
        # no new commit, and no stray uncommitted file left behind (rollback removed it)
        self.assertEqual(_git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip(), before)
        status = _git(self._gitrepo.name, "status", "--porcelain").stdout.strip()
        self.assertEqual(status, "", f"working tree not clean after rollback: {status!r}")

    # --- verify fail → rollback, no commit --------------------------------------
    def test_verify_fail_rollback_no_commit(self) -> None:
        before = _git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip()
        goal = self._goal_with_packet("콘솔 도움말 문구 개선")

        # a mutator whose execute() reports an unverified write → bridge must rollback + refuse
        real = self._mutator()

        class _FailVerifyMutator:
            def execute(self, task):
                # perform the real write (so a file exists to roll back) but report unverified
                oc = real.execute(task)
                from forgekit_runtime.autopilot.runner import ExecOutcome
                return ExecOutcome(
                    executed=True, action=oc.action, path=oc.path,
                    lines_changed=oc.lines_changed, verified=False,
                    refused_reason="verify 실패(주입)")

        out = apply_approved_packet(
            goal, _FailVerifyMutator(), self._gitrepo.name, env=self.env)

        self.assertEqual(out.outcome, OUTCOME_BLOCKED)
        self.assertFalse(out.applied)
        self.assertEqual(out.commit_sha, "")
        self.assertEqual(_git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip(), before)
        status = _git(self._gitrepo.name, "status", "--porcelain").stdout.strip()
        self.assertEqual(status, "", f"working tree not clean after rollback: {status!r}")

    # --- DEFAULT path unchanged (regression guard for console-approve / #348) -----
    def test_default_execute_no_apply_unchanged_no_mutation(self) -> None:
        before = _git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip()
        goal = self._goal_with_packet("콘솔 도움말 문구 개선")

        # EXACT surface call shape — no apply, no mutator
        out = execute_approved_packet(goal, env=self.env)

        self.assertEqual(out.outcome, OUTCOME_EXECUTED)
        self.assertTrue(out.executed)
        self.assertFalse(out.applied)          # authorize-only — no physical apply
        self.assertEqual(out.commit_sha, "")
        self.assertEqual(out.changed_path, "")
        # NO repo mutation: HEAD unchanged + working tree clean
        self.assertEqual(_git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip(), before)
        self.assertEqual(_git(self._gitrepo.name, "status", "--porcelain").stdout.strip(), "")

        # default path still writes authorize+evidence to the goal (unchanged behaviour)
        reloaded = GoalStore(env=self.env).get(goal.id)
        self.assertIn("execution", [e.kind for e in reloaded.evidence])

    def test_default_execute_even_with_mutator_but_apply_false_no_mutation(self) -> None:
        """A mutator present but apply=False (default) must NOT mutate — opt-in only."""

        before = _git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip()
        goal = self._goal_with_packet("콘솔 도움말 문구 개선")
        out = execute_approved_packet(goal, repo_root=self._gitrepo.name,
                                      env=self.env, mutator=self._mutator())  # apply default False
        self.assertTrue(out.executed)
        self.assertFalse(out.applied)
        self.assertEqual(_git(self._gitrepo.name, "rev-parse", "HEAD").stdout.strip(), before)


if __name__ == "__main__":
    unittest.main()
