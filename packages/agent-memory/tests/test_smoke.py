"""Smoke test for yule_agent_memory."""
import unittest

import yule_agent_memory
from yule_agent_memory import long_term_memory


class AgentMemorySmokeTests(unittest.TestCase):
    def test_importable(self) -> None:
        self.assertTrue(hasattr(long_term_memory, "__name__"))


if __name__ == "__main__":
    unittest.main()
