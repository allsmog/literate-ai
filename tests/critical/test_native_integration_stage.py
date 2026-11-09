"""New native children integrate while a project is retained, drafted or qualified.

A qualified project first moves back to drafted, and a failed integration puts the
exact qualified state back.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from literate_ai.adapters import conversion_authority as authority
from literate_ai.cli.source_to_specification import native_integration_stage_allowed
from literate_ai.contracts import ContentIdentity
from literate_ai.contracts.operator_adoption import ConversionAuthorityStage as Stage
from literate_ai.contracts.operator_adoption import ConversionAuthorityState


def identity(character):
    return ContentIdentity.parse_uri("sha256:" + character * 64)


class NativeIntegrationStageTests(unittest.TestCase):
    def test_retained_drafted_and_qualified_projects_accept_native_children(self):
        for stage in (Stage.RETAINED, Stage.DRAFTED, Stage.QUALIFIED):
            self.assertTrue(native_integration_stage_allowed(stage), stage)

    def test_original_wrapped_and_unknown_stages_refuse(self):
        for stage in (Stage.ORIGINAL, Stage.WRAPPED):
            self.assertFalse(native_integration_stage_allowed(stage), stage)
        self.assertFalse(native_integration_stage_allowed(None))
        self.assertFalse(native_integration_stage_allowed("qualified"))


class ReopenForNativeChildTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.project = SimpleNamespace(root=Path(self.directory.name).resolve())
        self.store = authority.FilesystemConversionAuthorityStore(self.project.root)
        self.qualified = ConversionAuthorityState(
            project_id="example",
            stage=Stage.QUALIFIED,
            evidence_identities=(identity("a"), identity("b")),
            prior_state_identity=identity("c"),
        )
        self.store._replace(self.qualified, expected=None)
        self.evidence = patch.object(
            authority,
            "evidence_for_conversion_stage",
            return_value=(identity("e"), identity("d")),
        )
        self.evidence.start()

    def tearDown(self):
        self.evidence.stop()
        self.directory.cleanup()

    def test_qualified_project_reopens_to_drafted_with_history(self):
        before, reopened = authority.reopen_for_native_child(
            self.project, self.qualified
        )
        self.assertEqual(before, self.qualified)
        self.assertIs(reopened.stage, Stage.DRAFTED)
        self.assertEqual(reopened.prior_state_identity, self.qualified.identity)
        self.assertEqual(reopened.evidence_identities, (identity("d"), identity("e")))
        self.assertEqual(self.store.load_optional(), reopened)
        authority.evidence_for_conversion_stage.assert_called_once_with(
            self.project, Stage.DRAFTED
        )

    def test_failed_integration_restores_the_exact_qualified_state(self):
        before, reopened = authority.reopen_for_native_child(
            self.project, self.qualified
        )
        authority.restore_after_failed_native_child(self.project, before, reopened)
        self.assertEqual(self.store.load_optional(), self.qualified)

    def test_only_qualified_state_reopens(self):
        drafted = ConversionAuthorityState(
            project_id="example",
            stage=Stage.DRAFTED,
            evidence_identities=(identity("a"),),
            prior_state_identity=identity("c"),
        )
        with self.assertRaises(authority.ConversionAuthorityError) as caught:
            authority.reopen_for_native_child(self.project, drafted)
        self.assertEqual(caught.exception.code, "conversion_authority.transition_invalid")
        self.assertEqual(self.store.load_optional(), self.qualified)

    def test_changed_state_refuses_to_reopen(self):
        other = ConversionAuthorityState(
            project_id="example",
            stage=Stage.QUALIFIED,
            evidence_identities=(identity("f"),),
            prior_state_identity=identity("c"),
        )
        with self.assertRaises(authority.ConversionAuthorityError) as caught:
            authority.reopen_for_native_child(self.project, other)
        self.assertEqual(caught.exception.code, "conversion_authority.concurrent_change")
        self.assertEqual(self.store.load_optional(), self.qualified)

    def test_restore_refuses_after_a_concurrent_change(self):
        before, reopened = authority.reopen_for_native_child(
            self.project, self.qualified
        )
        later = ConversionAuthorityState(
            project_id="example",
            stage=Stage.QUALIFIED,
            evidence_identities=(identity("f"),),
            prior_state_identity=reopened.identity,
        )
        self.store._replace(later, expected=reopened.identity)
        with self.assertRaises(authority.ConversionAuthorityError):
            authority.restore_after_failed_native_child(self.project, before, reopened)
        self.assertEqual(self.store.load_optional(), later)


if __name__ == "__main__":
    unittest.main()
