"""Public adoption plans derive Node stages and packaging from package.json."""

import json
import tempfile
import unittest
from pathlib import Path

from literate_ai.adapters.harness_test_discovery import observe_test_collection
from literate_ai.adapters.project_initialization import (
    detect_repo_flavors,
    plan_convert,
)


def _package(root: Path, scripts: dict[str, str]) -> None:
    (root / "package.json").write_text(
        json.dumps({"name": "app", "version": "1.0.0", "scripts": scripts}),
        encoding="utf-8",
    )


class JavaScriptAdoptionTests(unittest.TestCase):
    def test_only_declared_scripts_become_wrapper_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _package(root, {"dev": "next dev", "build": "next build", "lint": "x"})
            report = plan_convert(root, default_branch="main")
            commands = {
                stage["id"]: stage["command"]
                for stage in report["proposed_wrapper_stages"]
            }
            self.assertEqual(
                commands,
                {"build": "npm install && npm run build", "package": "npm pack"},
            )

    def test_lockfile_stages_install_with_npm_ci(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _package(root, {"build": "tsc", "test": "node --test"})
            (root / "package-lock.json").write_text("{}", encoding="utf-8")
            report = plan_convert(root, default_branch="main")
            commands = {
                stage["id"]: stage["command"]
                for stage in report["proposed_wrapper_stages"]
            }
            self.assertEqual(commands["build"], "npm ci && npm run build")
            self.assertEqual(commands["test"], "npm ci && npm test")

    def test_npm_init_placeholder_test_is_not_a_test_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _package(
                root,
                {
                    "start": "node index.js",
                    "test": 'echo "Error: no test specified" && exit 1',
                },
            )
            report = plan_convert(root, default_branch="main")
            stage_ids = {stage["id"] for stage in report["proposed_wrapper_stages"]}
            self.assertEqual(stage_ids, {"run", "package"})

    def test_declared_test_script_becomes_npm_test(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _package(root, {"test": "node --test"})
            report = plan_convert(root, default_branch="main")
            self.assertIn(
                {"id": "test", "command": "npm install && npm test"},
                [
                    {"id": stage["id"], "command": stage["command"]}
                    for stage in report["proposed_wrapper_stages"]
                ],
            )

    def test_node_manifest_selects_npm_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _package(root, {"build": "tsc"})
            selectors = detect_repo_flavors(root)
            self.assertIn("flavor://literate-ai/package-npm", selectors)
            self.assertNotIn("flavor://literate-ai/package-pip", selectors)

    def test_node_test_summaries_report_exact_counts(self):
        tap = b"TAP version 13\n1..2\n# tests 7\n# pass 5\n# fail 1\n# cancelled 0\n"
        tap += b"# skipped 1\n# todo 0\n# duration_ms 12.5\n"
        observed = observe_test_collection("test", tap, b"")
        self.assertEqual(
            (observed["state"], observed["total"], observed["passed"]),
            ("nonempty", 7, 5),
        )
        self.assertEqual((observed["failed"], observed["skipped"]), (1, 1))

        spec = "ℹ tests 3\nℹ pass 3\nℹ fail 0\n".encode()
        self.assertEqual(observe_test_collection("test", spec, b"")["passed"], 3)
        empty = observe_test_collection("test", b"# tests 0\n# pass 0\n", b"")
        self.assertEqual(empty["state"], "empty")

    def test_python_tree_keeps_pip_packaging_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
            selectors = detect_repo_flavors(root)
            self.assertIn("flavor://literate-ai/package-pip", selectors)


if __name__ == "__main__":
    unittest.main()
