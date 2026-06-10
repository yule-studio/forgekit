"""Smoke test for yule_learning."""
import unittest

import yule_learning
from yule_learning import mistake_ledger, preflight


class LearningSmokeTests(unittest.TestCase):
    def test_importable(self) -> None:
        self.assertTrue(hasattr(mistake_ledger, "__name__"))
        self.assertTrue(hasattr(preflight, "__name__"))


if __name__ == "__main__":
    unittest.main()
