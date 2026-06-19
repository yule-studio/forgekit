"""RWT3 guard — storage ↔ integrations cycle is broken (storage is a persistence leaf).

CI-run. Proves the decouple held:
- ``yule_storage`` production code imports NOTHING from ``yule_integrations`` (the old
  TYPE_CHECKING import of the calendar models is gone — storage declares a structural
  Protocol instead);
- the dependency is one-way ``integrations → storage`` (no cycle);
- integrations' concrete calendar dataclasses still structurally satisfy the storage
  persistence contract (so calendar-state sync keeps working).
"""

from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]


class StorageIsAPersistenceLeafTests(unittest.TestCase):
    def test_storage_src_does_not_import_integrations(self) -> None:
        storage_src = REPO / "packages" / "storage" / "src" / "yule_storage"
        pat = re.compile(r"^\s*(?:from|import)\s+yule_integrations\b")
        offenders = [
            py.name for py in storage_src.rglob("*.py")
            if any(pat.match(line) for line in py.read_text(encoding="utf-8").splitlines())
        ]
        self.assertEqual(
            offenders, [],
            f"yule_storage must not import yule_integrations (cycle): {offenders}. "
            f"Use the structural Protocol in yule_storage.calendar_contract instead.",
        )

    def test_dependency_is_one_way_integrations_to_storage(self) -> None:
        # integrations may depend on storage; storage may not depend on integrations.
        def imports(pkg_dir, target):
            src = REPO / "packages" / pkg_dir / "src"
            pat = re.compile(rf"^\s*(?:from|import)\s+{target}\b")
            return any(
                any(pat.match(l) for l in py.read_text(encoding="utf-8").splitlines())
                for py in src.rglob("*.py") if "__pycache__" not in str(py)
            )

        self.assertTrue(imports("integrations", "yule_storage"), "expected integrations → storage")
        self.assertFalse(imports("storage", "yule_integrations"), "storage → integrations must be gone")


class CalendarContractSatisfiedTests(unittest.TestCase):
    def test_storage_protocols_exist(self) -> None:
        from yule_storage.calendar_contract import (
            CalendarEventLike, CalendarTodoLike, CalendarQueryResultLike,  # noqa: F401
        )
        self.assertTrue(True)

    def test_integrations_models_satisfy_storage_contract(self) -> None:
        from yule_integrations.calendar.models import CalendarEvent, CalendarTodo, CalendarQueryResult

        # the concrete dataclasses carry every field the storage persistence layer reads
        event_fields = {"item_uid", "title", "description", "start", "end", "all_day",
                        "calendar_name", "category_color", "source", "last_modified"}
        todo_fields = {"item_uid", "title", "start", "due", "status", "completed",
                       "calendar_name", "source", "last_modified"}
        self.assertTrue(event_fields <= set(CalendarEvent.__dataclass_fields__))
        self.assertTrue(todo_fields <= set(CalendarTodo.__dataclass_fields__))
        self.assertTrue({"events", "todos"} <= set(CalendarQueryResult.__dataclass_fields__))


if __name__ == "__main__":
    unittest.main()
