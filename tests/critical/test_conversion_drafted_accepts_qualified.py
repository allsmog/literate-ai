"""The drafted conversion stage accepts Components that already qualified regeneratively."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from literate_ai.adapters import conversion_authority as subject
from literate_ai.adapters.conversion_authority import ConversionAuthorityError
from literate_ai.contracts.authority import ComponentAuthorityState as State
from literate_ai.contracts.operator_adoption import ConversionAuthorityStage as Stage

DERIVED = State.DERIVED_SOURCE_RETAINED
QUALIFIED = State.REGENERATIVELY_QUALIFIED_FUNGIBLE


class _Store:
    def __init__(self, rows):
        self.rows = rows

    def coordinates(self):
        return tuple(self.rows)

    def current(self, coordinate):
        state, digest = self.rows[coordinate]
        return SimpleNamespace(state=state, identity=SimpleNamespace(uri="sha256:" + digest))


class DraftedAcceptsQualifiedTests(unittest.TestCase):
    def evidence(self, stage, root_rows, child_rows=None):
        project = SimpleNamespace(root=Path("/project"))
        stores = {Path("/project"): _Store(root_rows)}
        children = []
        if child_rows is not None:
            children.append(Path("/project/child"))
            stores[Path("/project/child")] = _Store(child_rows)
        verified = []
        with (
            patch.object(subject, "FileAuthorityProjectionStore", side_effect=lambda root: stores[Path(root)]),
            patch.object(subject, "_native_project_roots", return_value=tuple(children)),
            patch.object(subject, "verify_source_promotion_evidence", side_effect=lambda root, item: verified.append(item)),
        ):
            result = subject.evidence_for_conversion_stage(project, stage)
        return result, verified

    def test_drafted_accepts_mixed_derived_and_qualified_components(self):
        result, verified = self.evidence(
            Stage.DRAFTED, {"component://a/x": (DERIVED, "b" * 64)}, {"component://a/y": (QUALIFIED, "a" * 64)}
        )
        self.assertEqual([item.uri for item in result], ["sha256:" + "a" * 64, "sha256:" + "b" * 64])
        self.assertEqual(verified, [])

    def test_drafted_accepts_all_qualified_components(self):
        result, _ = self.evidence(Stage.DRAFTED, {"component://a/x": (QUALIFIED, "c" * 64)})
        self.assertEqual(len(result), 1)

    def test_drafted_still_refuses_undrafted_components(self):
        for state in (State.SOURCE_AUTHORITATIVE, State.SPEC_ASSISTED):
            with self.subTest(state=state), self.assertRaises(ConversionAuthorityError) as caught:
                self.evidence(Stage.DRAFTED, {"component://a/x": (QUALIFIED, "c" * 64), "component://a/z": (state, "d" * 64)})
            self.assertEqual(caught.exception.code, "conversion_authority.component_stage_incomplete")

    def test_qualified_stage_stays_strict(self):
        with self.assertRaises(ConversionAuthorityError) as caught:
            self.evidence(Stage.QUALIFIED, {"component://a/x": (QUALIFIED, "c" * 64)}, {"component://a/y": (DERIVED, "e" * 64)})
        self.assertEqual(caught.exception.code, "conversion_authority.component_stage_incomplete")
        result, verified = self.evidence(Stage.QUALIFIED, {"component://a/x": (QUALIFIED, "c" * 64)})
        self.assertEqual(len(result), 1)
        self.assertEqual(len(verified), 1)

    def test_coordinate_collision_is_still_refused(self):
        with self.assertRaises(ConversionAuthorityError) as caught:
            self.evidence(Stage.DRAFTED, {"component://a/x": (DERIVED, "b" * 64)}, {"component://a/x": (QUALIFIED, "a" * 64)})
        self.assertEqual(caught.exception.code, "conversion_authority.component_coordinate_collision")


if __name__ == "__main__":
    unittest.main()
