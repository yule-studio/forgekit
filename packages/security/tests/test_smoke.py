import unittest
import yule_security
from yule_security import paste_guard


class yule_securitySmokeTests(unittest.TestCase):
    def test_legacy_identity(self) -> None:
        from yule_engineering.agents.security import paste_guard as legacy
        self.assertIs(legacy, yule_security.paste_guard)


if __name__ == "__main__":
    unittest.main()
