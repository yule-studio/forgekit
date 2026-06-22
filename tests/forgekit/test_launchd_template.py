"""GW7 — launchd control-plane unit template guard.

The Mac mini ForgeKit host runs `forgekit runtime serve` as a LaunchAgent. A
malformed plist fails silently at load time, so this guard parses the template
with the stdlib plist parser and asserts the structural invariants:

- valid plist, Label == com.forgekit.runtime;
- ProgramArguments invoke `<bin> runtime serve` with a repo-root;
- RunAtLoad + KeepAlive (bounded loop, restart on crash);
- FORGEKIT_HOME is set (reproducible config — same as the systemd path);
- logs go under ~/Library/Logs/forgekit, NEVER inside the repo (no runtime
  state in git);
- the README is HONEST about the macOS lid-close suspend limit (caffeinate /
  pmset) and cross-links the systemd 1급 path.

Pure / CI-safe: plistlib is stdlib; no daemon is launched.
"""

from __future__ import annotations

import plistlib
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PLIST = _ROOT / "deploy" / "launchd" / "com.forgekit.runtime.plist"
_README = _ROOT / "deploy" / "launchd" / "README.md"


class LaunchdTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_PLIST.exists(), f"missing {_PLIST}")
        self.data = plistlib.loads(_PLIST.read_bytes())

    def test_label_and_program(self) -> None:
        self.assertEqual(self.data["Label"], "com.forgekit.runtime")
        args = self.data["ProgramArguments"]
        self.assertEqual(args[0], "__FORGEKIT_BIN__")
        self.assertIn("runtime", args)
        self.assertIn("serve", args)
        self.assertIn("--repo-root", args)

    def test_runatload_and_keepalive(self) -> None:
        self.assertIs(self.data["RunAtLoad"], True)
        self.assertIn("KeepAlive", self.data)

    def test_reproducible_config_env(self) -> None:
        self.assertEqual(
            self.data["EnvironmentVariables"]["FORGEKIT_HOME"], "__FORGEKIT_HOME__"
        )

    def test_logs_outside_repo(self) -> None:
        for key in ("StandardOutPath", "StandardErrorPath"):
            p = self.data[key]
            self.assertIn("Library/Logs/forgekit", p)
            self.assertNotIn("__REPO_ROOT__", p)

    def test_readme_is_honest_about_lid_close(self) -> None:
        txt = _README.read_text(encoding="utf-8")
        self.assertIn("caffeinate", txt)
        self.assertIn("pmset", txt)
        self.assertTrue("lid" in txt.lower())
        self.assertIn("deploy/systemd", txt)  # cross-link the 1급 path


if __name__ == "__main__":
    unittest.main()
