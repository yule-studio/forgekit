"""Lane B / axis 4 — `forgekit runtime install-unit` render + install guard.

Asserts:
- launchd render: plistlib parses, Label/serve/RunAtLoad/FORGEKIT_HOME correct,
  repo-root + interval substituted, NO placeholders left;
- systemd render: unit parses, ExecStart + substitutions correct;
- dry-run constructs the right install command strings WITHOUT executing — the
  injected runner is never called, no file written;
- a real (non-dry-run) install writes to the right target path and issues the
  planned commands through the injected runner (idempotency: bootout-then-
  bootstrap / enable --now repeatable).

Pure / CI-safe: no real launchctl/systemctl is ever invoked; the side-effecting
runner + writer are injected fakes.
"""

from __future__ import annotations

import configparser
import plistlib
import unittest
from pathlib import Path

from forgekit_console.cli import unit_install as U

_ROOT = Path(__file__).resolve().parents[2]
# Templates are read from the repo root, so render against the real checkout;
# the daemon operates on this same root (repo_root == template source in reality).
_REPO = _ROOT
_HOME = Path("/tmp/forgekit-home")
_BIN = "/opt/venv/bin/forgekit"


def _launchd_plan(interval: int = 123) -> U.InstallPlan:
    return U.build_plan(
        backend=U.LAUNCHD,
        repo_root=_REPO,
        forgekit_bin=_BIN,
        forgekit_home=_HOME / ".forgekit",
        user_home=_HOME,
        interval=interval,
    )


def _systemd_plan(interval: int = 456) -> U.InstallPlan:
    return U.build_plan(
        backend=U.SYSTEMD,
        repo_root=_REPO,
        forgekit_bin=_BIN,
        forgekit_home=_HOME / ".forgekit",
        user_home=_HOME,
        interval=interval,
    )


class LaunchdRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = _launchd_plan(interval=123)
        self.data = plistlib.loads(self.plan.rendered.encode("utf-8"))

    def test_parses_and_label(self) -> None:
        self.assertEqual(self.data["Label"], "com.forgekit.runtime")

    def test_program_invokes_serve_with_substitutions(self) -> None:
        args = self.data["ProgramArguments"]
        self.assertEqual(args[0], _BIN)
        self.assertIn("runtime", args)
        self.assertIn("serve", args)
        self.assertIn("--interval", args)
        self.assertIn("123", args)  # interval substituted
        self.assertIn(str(_REPO), args)  # repo-root substituted

    def test_runatload_and_env(self) -> None:
        self.assertIs(self.data["RunAtLoad"], True)
        self.assertEqual(
            self.data["EnvironmentVariables"]["FORGEKIT_HOME"],
            str(_HOME / ".forgekit"),
        )

    def test_no_placeholders_left(self) -> None:
        self.assertNotIn("__", self.plan.rendered)

    def test_target_path(self) -> None:
        self.assertEqual(
            self.plan.target,
            _HOME / "Library" / "LaunchAgents" / "com.forgekit.runtime.plist",
        )


class SystemdRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = _systemd_plan(interval=456)
        self.cp = configparser.ConfigParser()
        self.cp.read_string(self.plan.rendered)

    def test_parses_unit_sections(self) -> None:
        self.assertIn("Unit", self.cp)
        self.assertIn("Service", self.cp)
        self.assertIn("Install", self.cp)

    def test_execstart_substitutions(self) -> None:
        exec_start = self.cp["Service"]["ExecStart"]
        self.assertTrue(exec_start.startswith(_BIN))
        self.assertIn("runtime serve", exec_start)
        self.assertIn("--interval 456", exec_start)
        self.assertIn(f"--repo-root {_REPO}", exec_start)

    def test_environment_and_workingdir(self) -> None:
        self.assertEqual(
            self.cp["Service"]["Environment"],
            f"FORGEKIT_HOME={_HOME / '.forgekit'}",
        )
        self.assertEqual(self.cp["Service"]["WorkingDirectory"], str(_REPO))

    def test_no_placeholders_left(self) -> None:
        self.assertNotIn("__", self.plan.rendered)

    def test_target_path(self) -> None:
        self.assertEqual(
            self.plan.target,
            _HOME / ".config" / "systemd" / "user" / "forgekit-runtime.service",
        )


class DryRunTests(unittest.TestCase):
    def test_dry_run_executes_nothing(self) -> None:
        calls = []
        writes = []
        plan = _launchd_plan()
        rc = U.apply_plan(
            plan,
            dry_run=True,
            out=lambda *_: None,
            runner=lambda argv: calls.append(list(argv)) or 0,
            writer=lambda p, t: writes.append(p),
            mkdir=lambda p: writes.append(("mkdir", p)),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [], "dry-run must not run any command")
        self.assertEqual(writes, [], "dry-run must not write/mkdir anything")

    def test_dry_run_prints_install_command_strings(self) -> None:
        lines = []
        U.apply_plan(_launchd_plan(), dry_run=True, out=lines.append,
                     runner=lambda argv: 1 / 0)  # would raise if called
        joined = "\n".join(lines)
        self.assertIn("launchctl bootstrap", joined)
        self.assertIn("launchctl bootout", joined)
        self.assertIn("DRY-RUN", joined)
        self.assertIn("com.forgekit.runtime.plist", joined)

    def test_systemd_dry_run_command_strings(self) -> None:
        lines = []
        U.apply_plan(_systemd_plan(), dry_run=True, out=lines.append,
                     runner=lambda argv: 1 / 0)
        joined = "\n".join(lines)
        self.assertIn("systemctl --user daemon-reload", joined)
        self.assertIn("systemctl --user enable --now forgekit-runtime.service", joined)


class RealInstallTests(unittest.TestCase):
    def test_real_install_writes_target_and_runs_commands(self) -> None:
        calls = []
        writes = {}
        mkdirs = []
        plan = _launchd_plan()
        rc = U.apply_plan(
            plan,
            dry_run=False,
            out=lambda *_: None,
            runner=lambda argv: calls.append(list(argv)) or 0,
            writer=lambda p, t: writes.__setitem__(p, t),
            mkdir=lambda p: mkdirs.append(p),
        )
        self.assertEqual(rc, 0)
        # Wrote the rendered unit to the planned target.
        self.assertIn(plan.target, writes)
        self.assertEqual(writes[plan.target], plan.rendered)
        # Created the LaunchAgents + log dirs first.
        self.assertIn(plan.target.parent, mkdirs)
        # Ran exactly the planned install commands (idempotent bootout→bootstrap).
        self.assertEqual(calls, [list(c) for c in plan.install_commands])

    def test_install_aborts_on_command_failure(self) -> None:
        calls = []
        rc = U.apply_plan(
            _launchd_plan(),
            dry_run=False,
            out=lambda *_: None,
            runner=lambda argv: calls.append(list(argv)) or 9,  # first cmd fails
            writer=lambda p, t: None,
            mkdir=lambda p: None,
        )
        self.assertEqual(rc, 9)
        self.assertEqual(len(calls), 1, "must stop after the first failing command")


class BackendTests(unittest.TestCase):
    def test_unknown_backend_raises(self) -> None:
        with self.assertRaises(ValueError):
            U.build_plan(
                backend="upstart",
                repo_root=_REPO,
                forgekit_bin=_BIN,
                forgekit_home=_HOME,
                user_home=_HOME,
                interval=60,
            )

    def test_templates_render_against_real_repo_root(self) -> None:
        # Render against the actual checked-in templates to catch token drift.
        for backend in (U.LAUNCHD, U.SYSTEMD):
            plan = U.build_plan(
                backend=backend,
                repo_root=_ROOT,
                forgekit_bin=_BIN,
                forgekit_home=_HOME / ".forgekit",
                user_home=_HOME,
                interval=300,
            )
            self.assertNotIn("__", plan.rendered, f"{backend} left placeholders")


if __name__ == "__main__":
    unittest.main()
