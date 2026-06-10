"""Smoke test for yule_security."""
import unittest

import yule_security
from yule_security import paste_guard


class SecuritySmokeTests(unittest.TestCase):
    def test_importable(self) -> None:
        self.assertTrue(hasattr(paste_guard, "__name__"))


if __name__ == "__main__":
    unittest.main()
