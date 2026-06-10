"""Smoke test for yule_agent_runtime."""
import unittest

import yule_agent_runtime
from yule_agent_runtime import loop, decide


class AgentRuntimeSmokeTests(unittest.TestCase):
    def test_importable(self) -> None:
        self.assertTrue(hasattr(loop, "__name__"))
        self.assertTrue(hasattr(decide, "__name__"))


if __name__ == "__main__":
    unittest.main()
