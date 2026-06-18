"""Vendor-native usage parsing (WT1 #239) — pure parser, no network.

Covers the four states the submit path depends on: a usable usage block (→ live),
a missing block, a malformed block, and a partial block (only one of prompt/
completion). All-zero degrades to None so the caller falls back to honest estimate.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.chat import usage_parse as up
from forgekit_console.providers.contract import SUBMIT_CLI, SUBMIT_OPENAI


class ParseOpenAIUsageTests(unittest.TestCase):
    def test_full_block_is_usable(self) -> None:
        u = up.parse_openai_usage(
            {"usage": {"prompt_tokens": 26, "completion_tokens": 298, "total_tokens": 324}}
        )
        self.assertIsNotNone(u)
        self.assertTrue(u.usable)
        self.assertEqual((u.input_tokens, u.output_tokens, u.total_tokens), (26, 298, 324))
        self.assertIn("324", u.raw_json)

    def test_missing_usage_block_is_none(self) -> None:
        self.assertIsNone(up.parse_openai_usage({"choices": [{"message": {"content": "hi"}}]}))

    def test_malformed_usage_is_none(self) -> None:
        self.assertIsNone(up.parse_openai_usage({"usage": "not-a-dict"}))
        self.assertIsNone(up.parse_openai_usage("not-a-mapping"))
        self.assertIsNone(up.parse_openai_usage({"usage": {"total_tokens": "many"}}))

    def test_partial_block_derives_total(self) -> None:
        u = up.parse_openai_usage({"usage": {"prompt_tokens": 10}})
        self.assertIsNotNone(u)
        self.assertEqual((u.input_tokens, u.output_tokens, u.total_tokens), (10, 0, 10))

    def test_all_zero_degrades_to_none(self) -> None:
        # zero total → nothing measurable → estimate fallback (None), never faked live
        self.assertIsNone(up.parse_openai_usage(
            {"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
        ))

    def test_bool_is_rejected(self) -> None:
        # bools are ints in python — must NOT be accepted as token counts
        self.assertIsNone(up.parse_openai_usage({"usage": {"total_tokens": True}}))


class DispatchTests(unittest.TestCase):
    def test_dispatch_only_for_wired_compat(self) -> None:
        block = {"usage": {"total_tokens": 5}}
        self.assertIsNotNone(up.parse_usage(SUBMIT_OPENAI, block))
        self.assertIsNone(up.parse_usage(SUBMIT_CLI, block))
        self.assertIsNone(up.parse_usage("", block))

    def test_live_capability_surface(self) -> None:
        self.assertTrue(up.live_usage_supported(SUBMIT_OPENAI))
        self.assertFalse(up.live_usage_supported(SUBMIT_CLI))
        self.assertFalse(up.live_usage_supported(""))


if __name__ == "__main__":
    unittest.main()
