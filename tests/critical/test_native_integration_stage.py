"""New native children integrate while a project is retained or drafted."""

import unittest

from literate_ai.cli.source_to_specification import native_integration_stage_allowed
from literate_ai.contracts.operator_adoption import ConversionAuthorityStage as Stage


class NativeIntegrationStageTests(unittest.TestCase):
    def test_retained_and_drafted_projects_accept_native_children(self):
        self.assertTrue(native_integration_stage_allowed(Stage.RETAINED))
        self.assertTrue(native_integration_stage_allowed(Stage.DRAFTED))

    def test_qualified_and_unknown_stages_refuse(self):
        for stage in (Stage.ORIGINAL, Stage.WRAPPED, Stage.QUALIFIED):
            self.assertFalse(native_integration_stage_allowed(stage), stage)
        self.assertFalse(native_integration_stage_allowed(None))
        self.assertFalse(native_integration_stage_allowed("drafted"))


if __name__ == "__main__":
    unittest.main()
