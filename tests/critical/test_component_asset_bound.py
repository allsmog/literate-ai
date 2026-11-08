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
from literate_ai.contracts.component_locking.authoring import MAXIMUM_COMPONENT_ASSETS


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


class AuthoringAndMarkdownAssetBoundTests(unittest.TestCase):
    def test_authoring_assets_use_the_shared_bound(self):
        from literate_ai.contracts.component_locking import authoring

        many = selectors(590)
        self.assertEqual(
            authoring._canonical(
                many,
                path="ComponentAuthoring.assets",
                expected_type=ComponentAssetSelector,
                key=lambda item: item.asset_id,
                label="asset IDs",
                maximum=MAXIMUM_COMPONENT_ASSETS,
            ),
            many,
        )
        with self.assertRaises(ValueError):
            authoring._canonical(
                many,
                path="ComponentAuthoring.provider_resolutions",
                expected_type=ComponentAssetSelector,
                key=lambda item: item.asset_id,
                label="asset IDs",
            )

    def test_markdown_assets_list_is_the_only_list_above_256(self):
        from literate_ai.adapters import component_markdown

        items = list(range(590))
        self.assertEqual(
            component_markdown._list(items, "assets", maximum=MAXIMUM_COMPONENT_ASSETS),
            items,
        )
        with self.assertRaises(component_markdown.ComponentMarkdownError):
            component_markdown._list(items, "provides")
        with self.assertRaises(component_markdown.ComponentMarkdownError):
            component_markdown._list(
                list(range(MAXIMUM_COMPONENT_ASSETS + 1)),
                "assets",
                maximum=MAXIMUM_COMPONENT_ASSETS,
            )


class GenerationDocumentCountTests(unittest.TestCase):
    def test_asset_metadata_does_not_consume_the_document_budget(self):
        from types import SimpleNamespace

        from literate_ai.application.component_generation_context import _document_count
        from literate_ai.contracts.executable_components.context import (
            ContextAuthorityKind,
        )

        segments = (
            SimpleNamespace(authority_kind=ContextAuthorityKind.LOCAL_SPECIFICATION),
            SimpleNamespace(authority_kind=ContextAuthorityKind.LOCAL_FLAVOR),
            *(
                SimpleNamespace(
                    authority_kind=ContextAuthorityKind.LOCAL_ASSET_METADATA
                )
                for _ in range(590)
            ),
        )
        self.assertEqual(_document_count(segments), 2)
