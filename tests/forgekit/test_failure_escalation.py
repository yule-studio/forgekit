"""Repeated-failure escalation — threshold, signatures, surfaces, alternatives.

Pure + stdlib only (json/tempdir), so these run in the bare CI install (no textual/
rich/PIL). All file IO is pointed at a tempdir; the notifier is a fake.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.lifecycle import failure_escalation as fe


def _esc(tmp: Path, *, threshold=3, env=None, notifier=None):
    return fe.FailureEscalator(
        env=env or {},
        threshold=threshold,
        ledger_path=tmp / "led.json",
        inbox_path=tmp / "inbox.json",
        notifier=notifier or (lambda t, b: True),
        bridge_troubleshooting=False,  # don't touch the heavy ledger in tests
    )


class ThresholdPolicyTests(unittest.TestCase):
    def test_default_threshold_is_three(self) -> None:
        self.assertEqual(fe.resolve_threshold({}), 3)

    def test_env_override_clamped_one_to_five(self) -> None:
        self.assertEqual(fe.resolve_threshold({"FORGEKIT_BLOCKED_THRESHOLD": "5"}), 5)
        self.assertEqual(fe.resolve_threshold({"FORGEKIT_BLOCKED_THRESHOLD": "9"}), 5)  # cap
        self.assertEqual(fe.resolve_threshold({"FORGEKIT_BLOCKED_THRESHOLD": "0"}), 1)  # floor
        self.assertEqual(fe.resolve_threshold({"FORGEKIT_BLOCKED_THRESHOLD": "x"}), 3)  # bad → default

    def test_notify_flag(self) -> None:
        self.assertFalse(fe.notify_enabled({}))
        for on in ("1", "true", "on", "yes"):
            self.assertTrue(fe.notify_enabled({"FORGEKIT_NOTIFY": on}), on)


class SignatureTests(unittest.TestCase):
    def test_key_combines_kind_reason_scope(self) -> None:
        sig = fe.FailureSignature(fe.KIND_RENDERER, "not true raster", "vscode")
        self.assertEqual(sig.key(), "renderer:not-true-raster:vscode")

    def test_different_signatures_do_not_merge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            esc = _esc(Path(d))
            a = esc.record_failure(fe.FailureSignature(fe.KIND_RENDERER, "halfcell"))
            b = esc.record_failure(fe.FailureSignature(fe.KIND_IMPORT, "no module"))
            self.assertEqual(a.count, 1)
            self.assertEqual(b.count, 1)  # independent counters


class EscalationFlowTests(unittest.TestCase):
    def test_advisory_below_threshold_then_escalate(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            esc = _esc(Path(d), threshold=3)
            sig = fe.FailureSignature(fe.KIND_COMMAND, "boom", "status")
            o1 = esc.record_failure(sig, symptom="failed")
            o2 = esc.record_failure(sig)
            o3 = esc.record_failure(sig)
            self.assertFalse(o1.escalated)
            self.assertFalse(o2.escalated)
            self.assertIn("2/3", o2.advisory)
            self.assertTrue(o3.escalated)
            self.assertEqual(o3.count, 3)

    def test_escalation_writes_minimum_two_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            esc = _esc(tmp, threshold=2)
            sig = fe.FailureSignature(fe.KIND_RENDERER, "halfcell", "vscode")
            esc.record_failure(sig)
            out = esc.record_failure(sig)
            self.assertTrue(out.escalated)
            self.assertTrue(out.meets_minimum_surfaces(minimum=2))
            self.assertIn(fe.SURFACE_LEDGER, out.surfaces)
            self.assertIn(fe.SURFACE_OPERATOR_INBOX, out.surfaces)
            # both durable surfaces exist on disk
            self.assertTrue((tmp / "led.json").is_file())
            self.assertTrue((tmp / "inbox.json").is_file())

    def test_report_includes_rca_and_alternatives(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            esc = _esc(Path(d), threshold=1)
            out = esc.record_failure(
                fe.FailureSignature(fe.KIND_RENDERER, "halfcell", "vscode"),
                symptom="avatar fallback",
                evidence="lib=ok:halfcell",
                attempted_fix="prime_image_backend",
                alternatives=("caller-supplied alt",),
            )
            r = out.report
            self.assertIsNotNone(r)
            self.assertEqual(r.symptom, "avatar fallback")
            self.assertIn("lib=ok:halfcell", r.evidence)
            self.assertIn("prime_image_backend", r.attempted_fixes)
            self.assertTrue(r.why_failing)
            self.assertTrue(r.next_step)
            # caller alt first, then KB remedies (deduped, order preserved)
            self.assertEqual(r.alternatives[0], "caller-supplied alt")
            self.assertGreaterEqual(len(r.alternatives), 3)
            joined = "\n".join(r.to_lines())
            self.assertIn("대안", joined)
            self.assertIn("다음 단계", joined)

    def test_status_surface_kind_needs_operator(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            esc = _esc(Path(d), threshold=1)
            out = esc.record_failure(fe.FailureSignature(fe.KIND_STATUS_SURFACE, "runtime unavailable"))
            self.assertTrue(out.report.needs_operator)
            alert = esc.as_alert(out)
            self.assertEqual(alert.level, "error")  # needs-operator → error level


class NotificationTests(unittest.TestCase):
    def test_notify_dispatched_only_when_flag_set(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            calls = []
            esc = _esc(Path(d), threshold=1, env={"FORGEKIT_NOTIFY": "1"},
                       notifier=lambda t, b: calls.append((t, b)) or True)
            out = esc.record_failure(fe.FailureSignature(fe.KIND_IMPORT, "no module"))
            self.assertEqual(len(calls), 1)
            self.assertIn(fe.SURFACE_NOTIFICATION, out.surfaces)

    def test_core_escalation_works_without_notify(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            calls = []
            esc = _esc(Path(d), threshold=1, env={},  # no FORGEKIT_NOTIFY
                       notifier=lambda t, b: calls.append(1) or True)
            out = esc.record_failure(fe.FailureSignature(fe.KIND_IMPORT, "no module"))
            self.assertEqual(len(calls), 0)  # notifier NOT called
            self.assertTrue(out.escalated)   # but core escalation still fired
            self.assertNotIn(fe.SURFACE_NOTIFICATION, out.surfaces)
            self.assertTrue(out.meets_minimum_surfaces())  # still ≥2 surfaces


class ReaderTests(unittest.TestCase):
    def test_read_and_summarize_open_escalations(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            esc = _esc(tmp, threshold=1)
            esc.record_failure(fe.FailureSignature(fe.KIND_RENDERER, "halfcell", "vscode"))
            records = fe.read_escalations(tmp / "led.json")
            self.assertEqual(len(records), 1)
            lines = fe.open_escalation_lines({"FORGEKIT_HOME": str(tmp.parent)})
            # (uses default ledger path under FORGEKIT_HOME — may be empty, but never errors)
            self.assertIsInstance(lines, tuple)

    def test_open_lines_empty_when_no_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lines = fe.open_escalation_lines({"FORGEKIT_HOME": d})
            self.assertTrue(any("없음" in ln for ln in lines))

    def test_open_lines_lists_escalations(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            # point FORGEKIT_HOME so the default ledger path lands in the tempdir
            esc = fe.FailureEscalator(env={"FORGEKIT_HOME": str(tmp)}, threshold=1,
                                      notifier=lambda t, b: True, bridge_troubleshooting=False)
            esc.record_failure(fe.FailureSignature(fe.KIND_RENDERER, "halfcell", "vscode"),
                               symptom="avatar fallback")
            lines = fe.open_escalation_lines({"FORGEKIT_HOME": str(tmp)})
            joined = "\n".join(lines)
            self.assertIn("renderer:halfcell:vscode", joined)
            self.assertIn("blocked", joined)


class BlockedCommandTests(unittest.TestCase):
    def test_blocked_command_registered(self) -> None:
        from forgekit_console.commands.registry import find_command, load_commands

        self.assertIsNotNone(find_command("blocked", load_commands()))

    def test_slash_blocked_routes_to_info(self) -> None:
        from pathlib import Path

        from forgekit_console.commands.parser import parse_input
        from forgekit_console.commands.router import build_default_context, route

        res = route(parse_input("/blocked"), build_default_context(Path("/tmp")))
        self.assertEqual(res.kind, "info")
        self.assertEqual(res.title, "blocked")
        self.assertTrue(res.lines)


if __name__ == "__main__":
    unittest.main()
