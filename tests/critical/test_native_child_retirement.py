"""Superseded, never-qualified native children retire only after a verified archive."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from literate_ai.adapters import conversion_authority as subject
from literate_ai.adapters.conversion_authority import ConversionAuthorityError
from literate_ai.contracts import canonical_json_bytes
from literate_ai.contracts.authority import ComponentAuthorityState as State

DERIVED = State.DERIVED_SOURCE_RETAINED
QUALIFIED = State.REGENERATIVELY_QUALIFIED_FUNGIBLE


class NativeChildRetirementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name).resolve()
        self.root = base / "project"
        self.outside = base / "outside"
        self.outside.mkdir()
        native = self.root / ".literate" / "native-projects"
        self.entries = []
        for name in ("local-old", "local-new", "local-other"):
            child = native / name
            (child / "components" / "x").mkdir(parents=True)
            (child / "literate.project.json").write_text(
                json.dumps({"project_id": name})
            )
            (child / "components" / "x" / "component.md").write_text("# " + name + "\n")
            self.entries.append(
                {
                    "path": f".literate/native-projects/{name}",
                    "project_definition_identity": "sha256:" + name[-3:] * 8,
                    "project_id": name,
                }
            )
        self.registry = self.root / subject.NATIVE_PROJECTS_FILE
        self.registry.write_bytes(
            canonical_json_bytes(
                {"schema": subject.NATIVE_PROJECTS_SCHEMA, "projects": self.entries}
            )
            + b"\n"
        )
        self.project = SimpleNamespace(root=self.root)
        self.states = {
            "local-old": [DERIVED],
            "local-new": [QUALIFIED],
            "local-other": [QUALIFIED],
        }

    def retire(self, child="local-old", successor="local-new", archive=None):
        def registered(project):
            document = json.loads(self.registry.read_bytes())
            return tuple(self.root / item["path"] for item in document["projects"])

        with (
            patch.object(subject, "_native_project_roots", side_effect=registered),
            patch.object(
                subject,
                "_projection_states",
                side_effect=lambda root: self.states[root.name],
            ),
        ):
            return subject.retire_native_project(
                self.project,
                child,
                superseded_by=successor,
                archive=archive or self.outside / "archive",
            )

    def registered_ids(self):
        return [
            item["project_id"]
            for item in json.loads(self.registry.read_bytes())["projects"]
        ]

    def assert_refused(self, code, **kwargs):
        before = self.registry.read_bytes()
        with self.assertRaises(ConversionAuthorityError) as caught:
            self.retire(**kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.registry.read_bytes(), before)
        self.assertTrue((self.root / ".literate/native-projects/local-old").is_dir())

    def test_retires_after_verified_archive_and_keeps_other_entries(self):
        original = (
            self.root / ".literate/native-projects/local-old/components/x/component.md"
        ).read_bytes()
        result = self.retire()
        archive = self.outside / "archive"
        self.assertEqual(
            (archive / "child/components/x/component.md").read_bytes(), original
        )
        record = json.loads((archive / "RETIREMENT.json").read_text())
        self.assertEqual(record["retired"]["project_id"], "local-old")
        self.assertEqual(record["superseded_by"]["project_id"], "local-new")
        self.assertEqual(
            set(record["files"]), {"literate.project.json", "components/x/component.md"}
        )
        self.assertEqual(self.registered_ids(), ["local-new", "local-other"])
        self.assertFalse((self.root / ".literate/native-projects/local-old").exists())
        self.assertEqual(result["registered_projects"], 2)

    def test_refuses_qualified_child(self):
        self.states["local-old"] = [DERIVED, QUALIFIED]
        self.assert_refused("conversion_authority.native_retirement_qualified")

    def test_refuses_successor_that_is_not_fully_qualified(self):
        self.states["local-new"] = [QUALIFIED, DERIVED]
        self.assert_refused(
            "conversion_authority.native_retirement_successor_unqualified"
        )
        self.states["local-new"] = []
        self.assert_refused(
            "conversion_authority.native_retirement_successor_unqualified"
        )

    def test_refuses_unknown_or_identical_projects(self):
        self.assert_refused(
            "conversion_authority.native_retirement_unknown", child="local-missing"
        )
        self.assert_refused(
            "conversion_authority.native_retirement_unknown", successor="local-missing"
        )
        self.assert_refused(
            "conversion_authority.native_retirement_unknown", successor="local-old"
        )

    def test_refuses_existing_or_in_project_archive(self):
        (self.outside / "exists").mkdir()
        self.assert_refused(
            "conversion_authority.native_retirement_archive_exists",
            archive=self.outside / "exists",
        )
        self.assert_refused(
            "conversion_authority.native_retirement_archive_inside_project",
            archive=self.root / "archive",
        )


if __name__ == "__main__":
    unittest.main()
