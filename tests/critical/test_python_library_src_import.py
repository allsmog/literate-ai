"""Python library verification honors the required src layout and artifact custody."""

import base64
import json
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from literate_ai.adapters.standard_project import _STANDARD_LIBRARY_IMPORT_DRIVER


class PythonLibrarySrcImportTests(unittest.TestCase):
    def run_driver(self, export, module="fixture_lib", symbol="value"):
        surface = {
            "capabilities": [
                {"capability": "fixture", "module": module, "symbols": [symbol]}
            ]
        }
        encoded = base64.urlsafe_b64encode(
            zlib.compress(json.dumps(surface).encode())
        ).decode()
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                _STANDARD_LIBRARY_IMPORT_DRIVER,
                "python",
                json.dumps([sys.executable]),
                encoded,
                str(export),
                "",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_flat_and_src_packages_keep_standard_library_available(self):
        for layout in ("", "src"):
            with (
                self.subTest(layout=layout),
                tempfile.TemporaryDirectory() as temporary,
            ):
                export = Path(temporary).resolve()
                package = export / "source" / layout / "fixture_lib"
                package.mkdir(parents=True)
                (package / "__init__.py").write_text(
                    "from fractions import Fraction\nvalue = Fraction(1, 3)\n"
                )
                result = self.run_driver(export)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["capabilities"], ["fixture"])

    def test_ambient_standard_library_module_is_not_an_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            export = Path(temporary).resolve()
            (export / "source/src").mkdir(parents=True)
            result = self.run_driver(export, "json", "dumps")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("library import escaped the exact artifact", result.stderr)

    def test_src_symlink_cannot_extend_artifact_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            export = root / "export"
            (export / "source").mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (outside / "fixture_lib.py").write_text("value = 7\n")
            (export / "source/src").symlink_to(outside, target_is_directory=True)
            self.assertNotEqual(self.run_driver(export).returncode, 0)
