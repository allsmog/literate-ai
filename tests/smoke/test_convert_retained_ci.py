"""Conversion keeps root-anchored CI declarations where forges read them."""

import tempfile
import unittest
from pathlib import Path

from literate_ai.adapters.harness_inventory import inspect_harness
from literate_ai.adapters.project_initialization import (
    _quarantine_existing_tree,
    _rollback_converted_tree,
    plan_convert,
)

_WORKFLOW = "jobs:\n  check:\n    runs-on: ubuntu-latest\n"


def _legacy_tree(root: Path) -> None:
    (root / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('legacy')\n", encoding="utf-8")
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(_WORKFLOW, encoding="utf-8")


class RetainedCiTests(unittest.TestCase):
    def test_plan_reports_ci_retained_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _legacy_tree(root)
            plan = plan_convert(root, default_branch="main")
            self.assertEqual(plan["quarantine"]["retained_in_place"], [".github"])
            self.assertNotIn(".github", plan["quarantine"]["entries"])
            self.assertEqual(plan["ci"]["class"], "remote-configured")

    def test_quarantine_and_rollback_leave_ci_at_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _legacy_tree(root)
            quarantine = _quarantine_existing_tree(root)
            assert quarantine is not None
            self.assertEqual(quarantine["retained_in_place"], [".github"])
            self.assertTrue((root / ".github" / "workflows" / "ci.yml").is_file())
            self.assertFalse((root / "Makefile").exists())
            legacy = root / str(quarantine["directory"])
            self.assertFalse((legacy / ".github").exists())

            inventory = inspect_harness(legacy, ci_root=root)
            self.assertIn(
                "ci.os-matrix",
                {item["detector_id"] for item in inventory["findings"]},
            )

            (root / "literate.project.json").write_text("{}", encoding="utf-8")
            _rollback_converted_tree(root, quarantine)
            self.assertEqual(
                sorted(item.name for item in root.iterdir()),
                [".github", "Makefile", "src"],
            )
            self.assertEqual(
                (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"),
                _WORKFLOW,
            )


if __name__ == "__main__":
    unittest.main()
