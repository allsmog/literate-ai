"""Explicit source selection follows stable custody without accepting semantic drift."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from literate_ai.adapters.cache import (
    FileSystemSourceCache,
    FilesystemStandardSourceRestorer,
    SourceCacheMaterializer,
    SourceCacheResolver,
)
from literate_ai.adapters.cache.standard import StandardSourceCacheRestorationError
from literate_ai.contracts import SourceCacheMode
from tests.support.fixtures_test_source_cache import (
    _cache_key,
    _configuration,
    _identity,
    _target,
)


class ExplicitSourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        cache = FileSystemSourceCache("local", self.root / "cache")
        self.resolver = SourceCacheResolver(
            _configuration(SourceCacheMode.READ_ONLY, _target()), {"local": cache}
        )
        self.previous = replace(
            _cache_key(), source_semantics_identity=_identity("source-semantics")
        )
        self.current = replace(self.previous, request_identity=_identity("new-request"))
        self.entry = _identity("selected-entry")

    def restorer(self, key):
        return FilesystemStandardSourceRestorer(
            resolver=self.resolver,
            materializer=SourceCacheMaterializer(),
            cache_key=lambda _node: key,
            entry_selections=(
                (self.previous.accepted_source_lookup_identity, self.entry),
            ),
        )

    def test_changed_request_custody_keeps_exact_source_selection(self):
        self.assertNotEqual(self.previous.identity, self.current.identity)
        self.restorer(self.current).require_applicable_selections((object(),))

    def test_semantic_recipe_and_model_drift_are_rejected(self):
        changed = (
            replace(self.current, source_semantics_identity=_identity("other-source")),
            replace(self.current, recipe_identity=_identity("other-recipe")),
            replace(
                self.current,
                model_binding=replace(
                    self.current.model_binding, model_selector="other-model"
                ),
            ),
        )
        for key in changed:
            with self.subTest(key=key.identity.uri):
                with self.assertRaises(StandardSourceCacheRestorationError):
                    self.restorer(key).require_applicable_selections((object(),))

    def test_restore_forwards_the_exact_entry_to_the_strict_resolver(self):
        with patch.object(self.resolver, "resolve", return_value=()) as resolve:
            self.assertIsNone(
                self.restorer(self.current).restore(
                    self.current,
                    self.root / "restored",
                    component_lock_identity=_identity("lock"),
                )
            )
        self.assertEqual(resolve.call_args.kwargs["entry_identity"], self.entry)
