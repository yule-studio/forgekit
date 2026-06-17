"""Operator notifications (WT4) — payload, cross-platform command, inbox tracking.

Verified WITHOUT real toast popups: the native command BUILDERS are pure (assert the
macOS osascript / Windows PowerShell command per platform), the service records the
event to the inbox AND attempts the desktop channel, and the payload is action-
oriented (what/why/now). Pure stdlib → bare CI.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import notify as N
from forgekit_console.notify import desktop, events
from forgekit_console.notify.service import NotificationService


class EventPayloadTests(unittest.TestCase):
    def test_request_type_mapping_covers_all_events(self) -> None:
        for ev in events.ALL_EVENTS:
            self.assertIn(events.request_type_for(ev),
                          (events.REQ_APPROVAL, events.REQ_DECISION, events.REQ_INFO,
                           events.REQ_ACCESS, events.REQ_SECRET))

    def test_body_is_action_oriented(self) -> None:
        ev = N.NotificationEvent(
            N.EVENT_APPROVAL_REQUIRED, "배포 승인 필요",
            why="프로덕션 배포는 자율 범위 밖", action="`#승인-대기` 카드에서 승인",
            options=("승인", "거부", "보류"), source="always-on",
        )
        body = ev.desktop_body()
        self.assertIn("프로덕션", body)
        self.assertIn("지금", body)  # tells the operator what to do NOW
        entry = ev.to_inbox_entry()
        self.assertEqual(entry["request_type"], events.REQ_APPROVAL)
        self.assertTrue(entry["needs_operator"])
        self.assertEqual(entry["options"], ["승인", "거부", "보류"])


class CrossPlatformCommandTests(unittest.TestCase):
    def test_macos_builds_osascript(self) -> None:
        cmd = desktop.build_command("forgekit", "승인 필요", platform=desktop.PLATFORM_MACOS)
        self.assertEqual(cmd[0], "osascript")
        self.assertIn("display notification", " ".join(cmd))

    def test_windows_builds_powershell_toast(self) -> None:
        cmd = desktop.build_command("forgekit", "승인 필요", platform=desktop.PLATFORM_WINDOWS)
        self.assertEqual(cmd[0], "powershell")
        joined = " ".join(cmd)
        self.assertIn("BurntToast", joined)  # toast path
        self.assertIn("forgekit", joined)

    def test_other_platform_has_no_desktop_channel(self) -> None:
        self.assertIsNone(desktop.build_command("t", "b", platform=desktop.PLATFORM_OTHER))
        delivered, channel = desktop.dispatch("t", "b", platform=desktop.PLATFORM_OTHER)
        self.assertFalse(delivered)
        self.assertEqual(channel, desktop.CHANNEL_NONE)

    def test_dispatch_uses_injected_runner(self) -> None:
        seen = {}

        def runner(cmd):
            seen["cmd"] = cmd
            return True

        delivered, channel = desktop.dispatch(
            "forgekit", "blocked", platform=desktop.PLATFORM_MACOS, runner=runner
        )
        self.assertTrue(delivered)
        self.assertEqual(channel, desktop.PLATFORM_MACOS)
        self.assertEqual(seen["cmd"][0], "osascript")


class ServiceTests(unittest.TestCase):
    def _svc(self, dispatcher):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        return NotificationService(inbox_path=tmp / "inbox.json", dispatcher=dispatcher), tmp

    def test_notify_records_inbox_and_attempts_desktop(self) -> None:
        calls = []

        def fake_dispatch(title, body):
            calls.append((title, body))
            return True, desktop.PLATFORM_MACOS

        svc, tmp = self._svc(fake_dispatch)
        ev = N.NotificationEvent(
            N.EVENT_RUNTIME_BLOCKED, "always-on 정지",
            why="권한 없는 배포에서 멈춤", action="runbook 확인 후 승인", source="runtime",
        )
        out = svc.notify(ev)
        # inbox + desktop both track the SAME event
        self.assertTrue(out.inbox_written)
        self.assertTrue(out.desktop_delivered)
        self.assertEqual(out.channel, desktop.PLATFORM_MACOS)
        self.assertEqual(len(calls), 1)
        records = json.loads((tmp / "inbox.json").read_text(encoding="utf-8"))
        self.assertEqual(records[-1]["event_type"], N.EVENT_RUNTIME_BLOCKED)
        self.assertEqual(records[-1]["request_type"], events.REQ_DECISION)

    def test_headless_still_records_inbox(self) -> None:
        # desktop unavailable (other platform) → inbox still durable, no fake success
        svc, tmp = self._svc(lambda t, b: (False, desktop.CHANNEL_NONE))
        out = svc.notify(N.NotificationEvent(
            N.EVENT_SECRET_REQUIRED, "secret 필요", why="키 없음", action="키 설정"))
        self.assertTrue(out.inbox_written)
        self.assertFalse(out.desktop_delivered)


if __name__ == "__main__":
    unittest.main()
