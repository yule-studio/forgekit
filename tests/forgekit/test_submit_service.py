"""Free-text live-submit service — provider resolution + the 4 honest states.

Pure + stdlib (a fake transport, no network), so these run in the bare CI install.
The ONE live path (openai-compatible / ollama) is exercised with the fake; the
other states (no provider / auth missing / unsupported / transport error) are each
asserted distinct.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.chat import models as m
from forgekit_console.chat.service import SubmitService


class FakeTransport:
    def __init__(self, *, reachable=True, models=("gemma3:latest",), reply="hello there", raise_exc=None):
        self.reachable = reachable
        self.models = tuple(models)
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = []

    def openai_chat(self, *, endpoint, model, prompt, api_key=""):
        self.calls.append((endpoint, model, prompt, api_key))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.reply

    def ollama_reachable(self, endpoint):
        return self.reachable

    def ollama_models(self, endpoint):
        return self.models


class ResolveTests(unittest.TestCase):
    def test_zero_config_local_ollama_when_reachable(self) -> None:
        svc = SubmitService(transport=FakeTransport(reachable=True), env={}, config={})
        spec, source = svc.resolve()
        self.assertIsNotNone(spec)
        self.assertEqual(spec.id, "ollama")
        self.assertEqual(source, m.SOURCE_LOCAL_DEFAULT)

    def test_none_when_unconfigured_and_unreachable(self) -> None:
        svc = SubmitService(transport=FakeTransport(reachable=False), env={}, config={})
        spec, source = svc.resolve()
        self.assertIsNone(spec)
        self.assertEqual(source, m.SOURCE_NONE)

    def test_configured_builtin(self) -> None:
        svc = SubmitService(transport=FakeTransport(), env={}, config={"main_provider": "claude"})
        spec, source = svc.resolve()
        self.assertEqual(spec.id, "claude")
        self.assertEqual(source, m.SOURCE_CONFIGURED)


class SubmitTests(unittest.TestCase):
    def test_live_ollama_success(self) -> None:
        t = FakeTransport(reachable=True, reply="forgekit live ok")
        out = SubmitService(transport=t, env={}, config={}).submit("hi")
        self.assertTrue(out.ok)
        self.assertTrue(out.is_live)
        self.assertEqual(out.mode, m.MODE_LIVE)
        self.assertEqual(out.category, m.CAT_OK)
        self.assertEqual(out.text, "forgekit live ok")
        self.assertEqual(out.provider_id, "ollama")
        self.assertEqual(out.model, "gemma3:latest")
        self.assertEqual(t.calls[0][2], "hi")  # the prompt reached the transport
        lines = out.to_lines()
        self.assertIn("forgekit live ok", lines[0])
        self.assertIn("live", "\n".join(lines))  # receipt says live

    def test_no_provider_configured_is_setup(self) -> None:
        out = SubmitService(transport=FakeTransport(reachable=False), env={}, config={}).submit("hi")
        self.assertFalse(out.ok)
        self.assertEqual(out.mode, m.MODE_SETUP)
        self.assertEqual(out.category, m.CAT_NO_PROVIDER)
        self.assertTrue(out.next_action)
        self.assertIn("ollama", out.next_action.lower())

    def test_auth_missing_for_api_key_provider(self) -> None:
        # gemini is openai-compatible but needs an API key — none in env → auth_missing
        out = SubmitService(
            transport=FakeTransport(), env={}, config={"main_provider": "gemini"}
        ).submit("hi")
        self.assertFalse(out.ok)
        self.assertEqual(out.category, m.CAT_AUTH_MISSING)
        self.assertIn("GEMINI_API_KEY", out.next_action)

    def test_unsupported_cli_provider(self) -> None:
        out = SubmitService(
            transport=FakeTransport(), env={}, config={"main_provider": "claude"}
        ).submit("hi")
        self.assertFalse(out.ok)
        self.assertEqual(out.category, m.CAT_UNSUPPORTED)
        self.assertIn("ollama", out.next_action.lower())

    def test_transport_error_is_unreachable(self) -> None:
        t = FakeTransport(reachable=True, raise_exc=ConnectionError("refused"))
        out = SubmitService(transport=t, env={}, config={}).submit("hi")
        self.assertFalse(out.ok)
        self.assertEqual(out.category, m.CAT_UNREACHABLE)
        self.assertIn("ConnectionError", out.text)

    def test_four_states_are_distinct(self) -> None:
        cats = {
            SubmitService(transport=FakeTransport(reply="x"), env={}, config={}).submit("hi").category,
            SubmitService(transport=FakeTransport(reachable=False), env={}, config={}).submit("hi").category,
            SubmitService(transport=FakeTransport(), env={}, config={"main_provider": "gemini"}).submit("hi").category,
            SubmitService(transport=FakeTransport(), env={}, config={"main_provider": "claude"}).submit("hi").category,
        }
        self.assertEqual(
            cats, {m.CAT_OK, m.CAT_NO_PROVIDER, m.CAT_AUTH_MISSING, m.CAT_UNSUPPORTED}
        )


if __name__ == "__main__":
    unittest.main()
