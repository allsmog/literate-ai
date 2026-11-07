"""Shell scripts are behavioral source: they inventory as shell and can be derived."""

import tempfile
import unittest
from pathlib import Path

from literate_ai.source_to_specification.inventory import (
    SourceFileClassification,
    inventory_source,
)
from literate_ai.source_to_specification.skills import (
    builtin_skill_set,
    load_builtin_skill_catalog,
)
from literate_ai.source_to_specification.static_workflow import derive_static_checkout

SCRIPT = """#!/bin/bash
set -euo pipefail
DEST=${1:?usage: build.sh DEST}
[ -e "$DEST" ] && { echo "ABORT $DEST exists" >&2; exit 2; }
mkdir -p "$DEST"
"""


class ShellSourceInventoryTests(unittest.TestCase):
    def test_sh_and_bash_files_are_shell_source(self):
        for name in ("build.sh", "setup.bash"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / name
                path.write_text(SCRIPT, encoding="utf-8")
                entry = inventory_source(path).entries[0]
                self.assertEqual(entry.classification, SourceFileClassification.SOURCE)
                self.assertEqual(entry.language, "shell")

    def test_credential_shaped_shell_stays_sensitive(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "deploy.sh"
            credential = "API_KEY=credential_literal_12345\n"
            path.write_text(SCRIPT + credential, encoding="utf-8")
            self.assertEqual(
                inventory_source(path).entries[0].classification,
                SourceFileClassification.SENSITIVE,
            )

    def test_shell_only_checkout_derives_without_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "build.sh").write_text(SCRIPT + "exit 99\n", encoding="utf-8")
            (root / "notes.txt").write_text("not behavioral\n", encoding="utf-8")
            inventory = inventory_source(root)
            catalog = load_builtin_skill_catalog()
            derivation = derive_static_checkout(
                source=root,
                inventory=inventory,
                origin_attestation_id="fixture-origin",
                skill_set=builtin_skill_set(catalog, languages=("shell",)),
                skill_catalog=catalog,
            )
            self.assertIsNotNone(derivation)


if __name__ == "__main__":
    unittest.main()
