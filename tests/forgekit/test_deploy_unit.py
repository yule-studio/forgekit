"""launchd unit auto-install (final-completion lane B) — render + place, honest.

Replaces the manual sed flow. Verified WITHOUT touching the real ~/Library/LaunchAgents
or running launchctl: rendering/resolution are pure, the file write targets a tmp dest,
and the launchctl run is an injected fake. Asserts no fake success (non-macOS / missing
template / leftover placeholder / launchctl failure all return ok=False) and that the
written plist is fully substituted (no __PLACEHOLDER__ leftovers).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import deploy_unit as du

_REPO = Path(__file__).resolve().parents[2]   # the real checkout (has deploy/launchd/)


class RenderResolveTests(unittest.TestCase):
    def test_resolve_values_uses_env_and_repo(self) -> None:
        vals = du.resolve_values(
            repo_root="/tmp/checkout",
            env={"HOME": "/Users/me", "FORGEKIT_HOME": "/Users/me/.fk"},
            bin_path="/venv/bin/forgekit", user_home="/Users/me")
        self.assertEqual(vals["__FORGEKIT_BIN__"], "/venv/bin/forgekit")
        self.assertEqual(vals["__FORGEKIT_HOME__"], "/Users/me/.fk")
        self.assertEqual(vals["__USER_HOME__"], "/Users/me")
        self.assertTrue(vals["__REPO_ROOT__"].endswith("checkout"))

    def test_render_substitutes_all_placeholders(self) -> None:
        tpl = du.template_path(_REPO).read_text(encoding="utf-8")
        vals = du.resolve_values(repo_root=str(_REPO), env={"HOME": "/Users/me"},
                                 bin_path="/venv/bin/forgekit", user_home="/Users/me")
        rendered, missing = du.render(tpl, vals)
        self.assertEqual(missing, ())                       # nothing left unsubstituted
        self.assertNotIn("__", rendered.replace("<!--", ""))  # no leftover placeholder token
        self.assertIn("/venv/bin/forgekit", rendered)

    def test_render_reports_leftover_placeholder(self) -> None:
        rendered, missing = du.render("X __FORGEKIT_BIN__ __REPO_ROOT__", {"__FORGEKIT_BIN__": "b"})
        self.assertIn("__REPO_ROOT__", missing)


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self.dest = str(Path(self.home) / "LaunchAgents" / "com.forgekit.runtime.plist")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_file_only_writes_rendered_plist_and_prints_load_cmd(self) -> None:
        ok, lines = du.install(
            repo_root=str(_REPO), env={"HOME": self.home}, user_home=self.home,
            bin_path="/venv/bin/forgekit", dest_path=self.dest, platform="darwin", load=False)
        self.assertTrue(ok)
        blob = "\n".join(lines)
        self.assertIn("launchctl bootstrap", blob)          # exact load command emitted
        self.assertIn("자동 load 안 함", blob)               # default does NOT start the daemon
        self.assertIn("lid-close", blob)                    # honest macOS limit restated
        written = Path(self.dest).read_text(encoding="utf-8")
        self.assertIn("/venv/bin/forgekit", written)
        self.assertNotIn("__FORGEKIT_BIN__", written)       # fully rendered, no fake

    def test_non_macos_is_honest_no_write(self) -> None:
        ok, lines = du.install(repo_root=str(_REPO), env={"HOME": self.home},
                               user_home=self.home, dest_path=self.dest, platform="linux")
        self.assertFalse(ok)
        self.assertIn("systemd", "\n".join(lines))
        self.assertFalse(Path(self.dest).exists())          # nothing written on the wrong OS

    def test_missing_template_is_honest_failure(self) -> None:
        ok, lines = du.install(repo_root=self._tmp.name, env={"HOME": self.home},
                               user_home=self.home, dest_path=self.dest, platform="darwin")
        self.assertFalse(ok)
        self.assertIn("템플릿 없음", lines[0])

    def test_load_runs_launchctl_via_injected_runner(self) -> None:
        calls = []

        def fake_runner(argv):
            calls.append(argv)
            return True, ""                                  # bootstrap + print both succeed

        ok, lines = du.install(
            repo_root=str(_REPO), env={"HOME": self.home}, user_home=self.home,
            bin_path="/venv/bin/forgekit", dest_path=self.dest, platform="darwin",
            load=True, runner=fake_runner)
        self.assertTrue(ok)
        self.assertTrue(any("bootstrap" in a for a in calls[0]))   # real launchctl invoked
        self.assertIn("bootstrap 완료", "\n".join(lines))

    def test_load_failure_is_honest_no_fake_success(self) -> None:
        def fail_runner(argv):
            return False, "Bootstrap failed: 5: Input/output error"

        ok, lines = du.install(
            repo_root=str(_REPO), env={"HOME": self.home}, user_home=self.home,
            bin_path="/venv/bin/forgekit", dest_path=self.dest, platform="darwin",
            load=True, runner=fail_runner)
        self.assertFalse(ok)                                 # NOT a fake success
        self.assertIn("실패", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
