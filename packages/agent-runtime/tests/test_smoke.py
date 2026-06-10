import unittest
import yule_agent_runtime
from yule_agent_runtime import loop


class yule_agent_runtimeSmokeTests(unittest.TestCase):
    def test_legacy_identity(self) -> None:
        from yule_engineering.agents.agent-runtime import loop as legacy
        self.assertIs(legacy, yule_agent_runtime.loop)


if __name__ == "__main__":
    unittest.main()
