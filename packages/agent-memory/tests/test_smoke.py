import unittest
import yule_agent_memory
from yule_agent_memory import long_term_memory


class yule_agent_memorySmokeTests(unittest.TestCase):
    def test_legacy_identity(self) -> None:
        from yule_engineering.agents.agent-memory import long_term_memory as legacy
        self.assertIs(legacy, yule_agent_memory.long_term_memory)


if __name__ == "__main__":
    unittest.main()
