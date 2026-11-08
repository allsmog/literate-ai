"""A reviewed Component graph node accepts up to MAXIMUM_COMPONENT_ASSETS pinned asset selectors and no more."""

from __future__ import annotations

import hashlib
import unittest

from literate_ai.contracts.component_locking import ComponentAssetSelector
from literate_ai.contracts.identity import ContentIdentity
from literate_ai.source_to_specification import (
    ComponentCapabilityContractDraft,
    ComponentGraphNodeDraft,
    SourceToSpecificationError,
)
from literate_ai.source_to_specification.contracts import MAXIMUM_COMPONENT_ASSETS


def selectors(count):
    return tuple(
        ComponentAssetSelector(
            f"a{index:04d}",
            f"assets/data/{index:04d}.bin",
            f"source/pkg/data/{index:04d}.bin",
            "data",
            pin=ContentIdentity.parse_uri(
                "sha256:" + hashlib.sha256(str(index).encode()).hexdigest()
            ),
        )
        for index in range(count)
    )


def node(assets):
    return ComponentGraphNodeDraft(
        coordinate="fixture/library",
        title="Fixture library",
        kind="library",
        profiles=("library", "portable"),
        provided_capabilities=("fixture.value",),
        capability_contracts=(
            ComponentCapabilityContractDraft(
                "fixture.value", "Return the documented value.", ("evidence:1",)
            ),
        ),
        entrypoints=(),
        build_needs=("implementation.language-ecosystem",),
        source_paths=("app.py",),
        observation_ids=("observation:1",),
        evidence_ids=("evidence:1",),
        assets=assets,
    )


class ComponentAssetBoundTests(unittest.TestCase):
    def test_bound_is_explicit(self):
        self.assertEqual(MAXIMUM_COMPONENT_ASSETS, 1024)

    def test_packaged_tree_of_several_hundred_assets_is_accepted(self):
        self.assertEqual(len(node(selectors(590)).assets), 590)
        self.assertEqual(
            len(node(selectors(MAXIMUM_COMPONENT_ASSETS)).assets),
            MAXIMUM_COMPONENT_ASSETS,
        )

    def test_assets_above_the_bound_are_refused(self):
        with self.assertRaises(SourceToSpecificationError) as raised:
            node(selectors(MAXIMUM_COMPONENT_ASSETS + 1))
        self.assertEqual(raised.exception.code, "component_graph.assets_invalid")


if __name__ == "__main__":
    unittest.main()
