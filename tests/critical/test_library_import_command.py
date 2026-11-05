"""Large library checks retain every import and immutable authority binding."""

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib
from dataclasses import replace
from pathlib import Path

from literate_ai.adapters.standard_project import (
    _STANDARD_LIBRARY_IMPORT_DRIVER,
    StandardCommandProjectionError,
    _encoded_library_import_surface,
)
from literate_ai.contracts import (
    LibraryCapabilityImport,
    LibraryImportSurface,
    canonical_identity,
)


def _surface(size=51):
    imports = []
    for index in range(size):
        name = canonical_identity({"operation": index}).digest[:24]
        imports.append(
            LibraryCapabilityImport(
                "fixture.operation-" + name,
                canonical_identity({"interface": index}),
                "fixture_library/operation_" + name,
                ("operation_" + name,),
            )
        )
    return LibraryImportSurface(
        "javascript",
        "fixture_library",
        tuple(sorted(imports, key=lambda x: x.capability)),
    )


def _decode(encoded):
    return json.loads(zlib.decompress(base64.urlsafe_b64decode(encoded)))


class LibraryImportCommandTests(unittest.TestCase):
    def test_many_interfaces_fit_without_omitting_any_verifier_input(self):
        surface = _surface()
        old = base64.urlsafe_b64encode(
            zlib.compress(
                json.dumps(surface.to_dict(), separators=(",", ":")).encode(), 9
            )
        )
        self.assertGreater(len(old), 4096)
        encoded = _encoded_library_import_surface(surface)
        self.assertLessEqual(len(encoded), 4096)
        self.assertEqual(
            _decode(encoded),
            {
                "identity": surface.identity.to_dict(),
                "language": surface.language,
                "package": surface.package,
                "capabilities": [
                    {
                        "capability": item.capability,
                        "module": item.module,
                        "symbols": list(item.symbols),
                    }
                    for item in surface.capabilities
                ],
            },
        )

    def test_interface_only_change_still_invalidates_execute_command(self):
        surface = _surface()
        changed = replace(
            surface,
            capabilities=(
                replace(
                    surface.capabilities[0],
                    interface_identity=canonical_identity({"interface": "revised"}),
                ),
                *surface.capabilities[1:],
            ),
        )
        before = _encoded_library_import_surface(surface)
        after = _encoded_library_import_surface(changed)
        self.assertNotEqual(before, after)
        left, right = _decode(before), _decode(after)
        self.assertNotEqual(left.pop("identity"), right.pop("identity"))
        self.assertEqual(left, right)

    def test_oversized_import_mappings_still_fail_closed(self):
        with self.assertRaises(StandardCommandProjectionError) as error:
            _encoded_library_import_surface(_surface(400))
        self.assertEqual(
            error.exception.code, "standard_command.library_import_surface_oversized"
        )

    def test_large_package_checks_every_export_and_retains_path_checks(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable")
        surface = _surface()
        encoded = _encoded_library_import_surface(surface)
        with tempfile.TemporaryDirectory() as directory:
            export = Path(directory) / "artifact"
            package = export / "source" / surface.package
            package.mkdir(parents=True)
            manifest = {"name": surface.package, "type": "commonjs", "exports": {}}
            for item in surface.capabilities:
                relative = item.module.split("/", 1)[1]
                (package / (relative + ".js")).write_text(
                    "module.exports = {" + item.symbols[0] + ": 7};\n"
                )
                manifest["exports"]["./" + relative] = "./" + relative + ".js"
            metadata = package / "package.json"

            def run():
                metadata.write_text(json.dumps(manifest))
                return subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        _STANDARD_LIBRARY_IMPORT_DRIVER,
                        "javascript",
                        json.dumps([node]),
                        encoded,
                        str(export),
                        "-",
                        str(export.parent),
                    ],
                    capture_output=True,
                    text=True,
                )

            imported = run()
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(
                json.loads(imported.stdout)["capabilities"],
                [item.capability for item in surface.capabilities],
            )
            item = surface.capabilities[-1]
            module = package / (item.module.split("/", 1)[1] + ".js")
            original = module.read_text()
            module.write_text("module.exports = {};\n")
            rejected = run()
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("missing library symbol", rejected.stderr)
            module.write_text(original)
            manifest["exports"]["./undeclared"] = "./undeclared.js"
            rejected = run()
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("undeclared entries", rejected.stderr)
            del manifest["exports"]["./undeclared"]
            manifest["dependencies"] = {"unexpected": "1.0.0"}
            rejected = run()
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("dependency-free library", rejected.stderr)
            del manifest["dependencies"]
            outside = Path(directory) / "outside.js"
            outside.write_text(original)
            module.unlink()
            module.symlink_to(outside)
            rejected = run()
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("escaped exact artifact", rejected.stderr)
