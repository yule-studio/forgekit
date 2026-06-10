import unittest
import yule_learning
from yule_learning import mistake_ledger


class yule_learningSmokeTests(unittest.TestCase):
    def test_legacy_identity(self) -> None:
        from yule_engineering.agents.learning import mistake_ledger as legacy
        self.assertIs(legacy, yule_learning.mistake_ledger)


if __name__ == "__main__":
    unittest.main()
