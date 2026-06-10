"""subprocess supervisor stdout/stderr prefix — A-M6.1a.

Pin that ``_forward_with_prefix`` decorates each child line with
``[<service_id>] `` so a busy ``runtime up`` produces operator-
readable logs instead of interleaved raw lines.
"""

from __future__ import annotations

import asyncio
import io
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.runtime.subprocess_supervisor import (
    _forward_with_prefix,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeStream:
    """asyncio.StreamReader-shaped stub — feeds prebuilt lines."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class ForwardWithPrefixTests(unittest.TestCase):
    def test_each_line_gets_service_prefix(self) -> None:
        sink = io.StringIO()
        stream = _FakeStream([
            b"queue picked job=abc\n",
            b"heartbeat ok\n",
            b"shutting down\n",
        ])
        _run(
            _forward_with_prefix(
                stream=stream,
                sink=sink,
                prefix="eng-research-worker",
            )
        )
        out = sink.getvalue()
        self.assertEqual(
            out,
            "[eng-research-worker] queue picked job=abc\n"
            "[eng-research-worker] heartbeat ok\n"
            "[eng-research-worker] shutting down\n",
        )

    def test_partial_line_without_trailing_newline(self) -> None:
        sink = io.StringIO()
        stream = _FakeStream([
            b"final line no newline",
        ])
        _run(
            _forward_with_prefix(
                stream=stream,
                sink=sink,
                prefix="eng-role-qa-engineer",
            )
        )
        # Forwarder appends the missing newline so the prefix lands
        # on its own line — important when the child crashes
        # mid-write and leaves a dangling fragment.
        self.assertTrue(
            sink.getvalue().endswith("final line no newline\n")
        )
        self.assertIn(
            "[eng-role-qa-engineer] final line no newline\n",
            sink.getvalue(),
        )

    def test_non_utf8_bytes_decode_with_replace(self) -> None:
        sink = io.StringIO()
        stream = _FakeStream([
            b"ok line\n",
            b"\xff\xfe broken bytes\n",
        ])
        _run(
            _forward_with_prefix(
                stream=stream,
                sink=sink,
                prefix="eng-supervisor-watch",
            )
        )
        # Both lines must land — the broken-bytes line decodes via
        # errors='replace' so the forwarder doesn't kill the loop
        # on a malformed UTF-8 chunk.
        out = sink.getvalue()
        self.assertIn("[eng-supervisor-watch] ok line", out)
        self.assertIn("[eng-supervisor-watch]", out.split("\n")[1])

    def test_none_stream_is_safe_noop(self) -> None:
        # When child is spawned with stdout/stderr inherited
        # (``stdout=None``), the supervisor still constructs the
        # forwarder; it must immediately return without crashing.
        sink = io.StringIO()
        _run(
            _forward_with_prefix(
                stream=None,
                sink=sink,
                prefix="eng-approval-worker",
            )
        )
        self.assertEqual(sink.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
