"""Reviewed asset custody through graph transport, promotion, locks and assembly."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from literate_ai.adapters.component_markdown import parse_component_markdown
from literate_ai.adapters.locked_generation_authority import (
    FilesystemLockedGenerationAuthorityReader,
)
from literate_ai.adapters.standard_project import admit_locked_authored_assets
from literate_ai.application.artifact_graph import (
    ArtifactAssemblyError,
    assemble_source_tree,
)
from literate_ai.cli._wire import component_graph_from_wire
from literate_ai.cli.errors import CliFailure
from literate_ai.cli.source_to_specification import _locked_promotion_asset_pins
from literate_ai.contracts.component_locking import ComponentAssetSelector
from literate_ai.contracts.executable_components.artifacts import (
    GeneratedTextFile,
    GeneratedTextTree,
)
from literate_ai.contracts.identity import ContentIdentity
from literate_ai.source_to_specification import (
    ComponentCapabilityContractDraft,
    ComponentGraphDraft,
    ComponentGraphNodeDraft,
    SourceToSpecificationError,
    canonical_value,
    inventory_source,
)
from literate_ai.source_to_specification.model_workflow import _merge_component_graphs
from literate_ai.source_to_specification.promotion_materialization import (
    PromotionInputKind,
    SourcePromotionError,
    SourcePromotionInput,
    SourcePromotionMaterializer,
    assess_source_exclusion,
)
from literate_ai.storage import FileSystemCAS
from tests.support.fixtures_test_cli_arbitrary_source import (
    KEY,
    invoke,
    reviewed_static_graph,
)

DATA = b"\x00\xff\x80reviewed immutable input\x00\n"


def pin(data):
    return ContentIdentity.parse_uri("sha256:" + hashlib.sha256(data).hexdigest())


def selector(**changes):
    return replace(
        ComponentAssetSelector(
            "payload",
            "assets/payload.bin",
            "source/data/payload.bin",
            "data",
            pin=pin(DATA),
        ),
        **changes,
    )


def graph(assets=()):
    return ComponentGraphDraft(
        "sha256:" + "1" * 64,
        "fixture/library",
        (
            ComponentGraphNodeDraft(
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
            ),
        ),
        (),
    )


class PromotedAssetContractsTests(unittest.TestCase):
    def test_unrelated_dependency_assets_and_ambiguous_targets(self):
        # Existing dependency assets may use URI sources. They cannot authorize
        # an audited local asset or invalidate a legacy asset-free promotion.
        remote = SimpleNamespace(
            revision=SimpleNamespace(
                coordinate=SimpleNamespace(name="dependency"),
                assets=(
                    SimpleNamespace(
                        selector=selector(source="project:///data/input.bin"),
                        blob=SimpleNamespace(identity=pin(DATA).uri),
                    ),
                ),
            )
        )
        local = SimpleNamespace(
            revision=SimpleNamespace(
                coordinate=SimpleNamespace(name="library"),
                assets=(
                    SimpleNamespace(
                        selector=selector(),
                        blob=SimpleNamespace(identity=pin(DATA).uri),
                    ),
                ),
            )
        )
        audit = SimpleNamespace(entries=())
        lock = SimpleNamespace(nodes=(remote, local))
        self.assertEqual(_locked_promotion_asset_pins(audit, lock), {})
        path = "components/library/assets/payload.bin"
        audit = SimpleNamespace(
            entries=(
                SimpleNamespace(
                    kind=PromotionInputKind.IMMUTABLE_ASSET, target_path=path
                ),
            )
        )
        self.assertEqual(
            _locked_promotion_asset_pins(audit, lock), {path: pin(DATA).uri}
        )
        with self.assertRaises(CliFailure):
            _locked_promotion_asset_pins(audit, SimpleNamespace(nodes=(local, local)))

    def test_published_graph_schema_requires_exact_local_pins_and_authoring_bound(self):
        registry = Registry()
        root = Path(__file__).resolve().parents[2]
        for directory in (root / "schemas/v1", root / "schemas/v2"):
            for path in sorted(directory.glob("*.schema.json")):
                document = json.loads(path.read_text(encoding="utf-8"))
                registry = registry.with_resource(
                    document["$id"], Resource.from_contents(document)
                )
        schema = "urn:literate-ai:schema:v3:component-graph-draft"
        validator = Draft202012Validator({"$ref": schema}, registry=registry.crawl())
        wire = {"schema": schema, **canonical_value(graph((selector(),)))}
        validator.validate(wire)
        for field, value in (
            ("pin", None),
            ("source", "project:///assets/payload.bin"),
        ):
            changed = json.loads(json.dumps(wire))
            changed["nodes"][0]["assets"][0][field] = value
            with self.subTest(field=field):
                self.assertFalse(validator.is_valid(changed))
        missing = json.loads(json.dumps(wire))
        del missing["nodes"][0]["assets"][0]["pin"]
        self.assertFalse(validator.is_valid(missing))
        excessive = json.loads(json.dumps(wire))
        excessive["nodes"][0]["assets"] *= 257
        self.assertFalse(validator.is_valid(excessive))
        with self.assertRaises(SourceToSpecificationError):
            graph(tuple(selector() for _ in range(257)))

    def test_graph_wire_preserves_pins_and_legacy_empty_identity(self):
        original = graph()
        legacy = canonical_value(original)
        self.assertNotIn("assets", legacy["nodes"][0])
        self.assertEqual(component_graph_from_wire(legacy), original)
        current = graph((selector(),))
        wire = canonical_value(current)
        self.assertEqual(wire["nodes"][0]["assets"], [selector().to_dict()])
        self.assertEqual(component_graph_from_wire(wire), current)
        self.assertNotEqual(current.identity, original.identity)
        changed = graph((selector(pin=pin(b"changed input")),))
        self.assertNotEqual(current.identity, changed.identity)
        self.assertEqual(
            _merge_component_graphs((current, current)).nodes[0].assets,
            current.nodes[0].assets,
        )
        with self.assertRaises(SourceToSpecificationError):
            _merge_component_graphs((current, changed))

    def test_unpinned_remote_and_aliasing_assets_refuse(self):
        for item in (
            selector(pin=None),
            selector(source="https://example.invalid/input.bin"),
            selector(source="project:///assets/payload.bin"),
            selector(source="inputs/payload.bin"),
        ):
            with (
                self.subTest(source=item.source),
                self.assertRaises(SourceToSpecificationError),
            ):
                graph((item,))
        for items in (
            (
                selector(asset_id="a"),
                selector(
                    asset_id="b", source="assets/PAYLOAD.bin", path="source/other.bin"
                ),
            ),
            (
                selector(asset_id="a"),
                selector(
                    asset_id="b",
                    source="assets/other.bin",
                    path="source/data/payload.bin/child",
                ),
            ),
        ):
            with self.assertRaises(SourceToSpecificationError):
                graph(items)

    def test_asset_kind_cannot_materialize_into_generated_source(self):
        for target in (
            "source/main.py",
            "component.md",
            "components/library/source/main.py",
        ):
            with self.subTest(target=target), self.assertRaises(SourcePromotionError):
                SourcePromotionInput(
                    PromotionInputKind.IMMUTABLE_ASSET,
                    Path("."),
                    "reviewed-assets",
                    "assets/payload.bin",
                    target,
                    pin(DATA).uri,
                )

    def test_binary_materialization_requires_locked_pin_and_keeps_raw_source_guard(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "assets").mkdir(parents=True)
            (source / "assets/payload.bin").write_bytes(DATA)
            (source / "app.py").write_text("SOURCE_CANARY = 7\n")
            materializer = SourcePromotionMaterializer()
            item = SourcePromotionInput(
                PromotionInputKind.IMMUTABLE_ASSET,
                source,
                "reviewed-assets",
                "assets/payload.bin",
                "assets/payload.bin",
                pin(DATA).uri,
            )
            result = materializer.materialize((item,), root / "component")
            self.assertEqual((result.output_root / item.target_path).read_bytes(), DATA)
            result.audit.verify(result.output_root)
            inventory = inventory_source(source)
            for pins in (
                None,
                {"assets/payload.bin": pin(b"wrong").uri},
                {"assets/other.bin": pin(DATA).uri},
            ):
                with self.subTest(pins=pins), self.assertRaises(SourcePromotionError):
                    assess_source_exclusion(
                        result.audit, inventory, immutable_asset_pins=pins
                    )
            assessment = assess_source_exclusion(
                result.audit,
                inventory,
                immutable_asset_pins={"assets/payload.bin": pin(DATA).uri},
            )
            self.assertTrue(assessment.source_excluded)
            self.assertEqual(
                assessment.immutable_assets, (("assets/payload.bin", pin(DATA).uri),)
            )
            # An allowed asset digest cannot conceal another copy admitted as a
            # specification. Authorization applies to the exact typed entry.
            other = root / "accepted"
            other.mkdir()
            (other / "spec.md").write_bytes(DATA)
            raw = SourcePromotionInput(
                PromotionInputKind.SPECIFICATION,
                other,
                "accepted-specifications",
                "spec.md",
                "spec.md",
                pin(DATA).uri,
            )
            mixed = materializer.audit((item, raw))
            assessment = assess_source_exclusion(
                mixed,
                inventory,
                immutable_asset_pins={"assets/payload.bin": pin(DATA).uri},
            )
            self.assertFalse(assessment.source_excluded)
            self.assertEqual(
                assessment.overlapping_content_identities, (pin(DATA).uri,)
            )

    def test_changed_missing_and_symlinked_inputs_never_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "assets").mkdir(parents=True)
            path = source / "assets/payload.bin"
            item = SourcePromotionInput(
                PromotionInputKind.IMMUTABLE_ASSET,
                source,
                "reviewed-assets",
                "assets/payload.bin",
                "assets/payload.bin",
                pin(DATA).uri,
            )
            for label, data in (("missing", None), ("changed", b"changed input")):
                if data is not None:
                    path.write_bytes(data)
                output = root / label
                with self.assertRaises(SourcePromotionError):
                    SourcePromotionMaterializer().materialize((item,), output)
                self.assertFalse(output.exists())
            path.unlink()
            outside = root / "outside.bin"
            outside.write_bytes(DATA)
            try:
                path.symlink_to(outside)
            except OSError as error:
                self.skipTest(f"symlink creation unavailable: {error}")
            with self.assertRaises(SourcePromotionError):
                SourcePromotionMaterializer().materialize((item,), root / "linked")
            self.assertFalse((root / "linked").exists())


class PromotedAssetCliTests(unittest.TestCase):
    def success(self, *arguments):
        status, output, errors = invoke(*arguments)
        self.assertEqual((status, errors), (0, ""), output or errors)
        return json.loads(output)["result"]

    def prepare(self, root, *, asset_pin=None):
        source = root / "source"
        (source / "assets").mkdir(parents=True)
        (source / "app.py").write_text("def value():\n    return 7\n")
        (source / "assets/payload.bin").write_bytes(DATA)
        key = root / "trust.key"
        key.write_bytes(KEY)
        attestation = self.success(
            "spec",
            "attest",
            str(source),
            "--signer",
            "reviewer",
            "--machine",
            "fixture",
            "--key",
            str(key),
        )
        (root / "attestation.json").write_text(json.dumps(attestation))
        bundle = self.success(
            "spec",
            "derive",
            str(source),
            "--attestation",
            str(root / "attestation.json"),
            "--trust-key",
            str(key),
        )
        (root / "bundle.json").write_text(json.dumps(bundle))
        reviewed = reviewed_static_graph(bundle)
        reviewed["nodes"][0]["assets"] = [
            selector(pin=asset_pin or pin(DATA)).to_dict()
        ]
        (root / "graph.json").write_text(json.dumps(reviewed))
        arguments = [
            "spec",
            "review",
            str(root / "bundle.json"),
            "--actor",
            "reviewer",
            "--key",
            str(key),
            "--component-graph",
            str(root / "graph.json"),
        ]
        for item in bundle["review_gate"]["blocking_uncertainty_ids"]:
            arguments += ["--resolve", item]
        review = self.success(*arguments)
        (root / "review.json").write_text(json.dumps(review))
        return source, reviewed, review

    def acceptance(self, root, source, *, review="review.json", suffix=""):
        return (
            "spec",
            "accept",
            str(source),
            str(root / "bundle.json"),
            "--review",
            str(root / review),
            "--trust-key",
            str(root / "trust.key"),
            "--target",
            str(root / ("accepted" + suffix)),
            "--project-target",
            str(root / ("promoted" + suffix)),
        )

    def test_signed_cli_promotes_asset_into_lock_cas_and_immutable_overlay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, reviewed, review = self.prepare(root)
            tampered = json.loads(json.dumps(review))
            tampered["review"]["component_graph_draft"]["nodes"][0]["assets"][0][
                "role"
            ] = "unreviewed"
            (root / "tampered.json").write_text(json.dumps(tampered))
            status, output, errors = invoke(
                *self.acceptance(
                    root, source, review="tampered.json", suffix="-tampered"
                )
            )
            self.assertEqual((status, output), (2, ""))
            self.assertEqual(
                json.loads(errors)["error"]["code"], "review.attestation_invalid"
            )
            result = self.success(*self.acceptance(root, source))["promoted_project"]
            project = root / "promoted"
            component_path = project / result["component"]
            component = component_path.parent
            authoring = parse_component_markdown(
                component_path, component_path.read_text(), project_root=project
            )
            self.assertEqual(authoring.assets, (selector(),))
            self.assertEqual((component / "assets/payload.bin").read_bytes(), DATA)
            self.assertFalse((component / "app.py").exists())
            snapshot = FilesystemLockedGenerationAuthorityReader().read(
                component, target_name="host"
            )
            lock = snapshot.authority.lock
            node = next(
                item
                for item in lock.nodes
                if item.revision.identity == lock.root_revision
            )
            self.assertEqual(node.revision.assets[0].blob.digest, pin(DATA).digest)
            cas = FileSystemCAS(root / "cas")
            assets = admit_locked_authored_assets(snapshot, cas=cas)
            self.assertEqual(len(assets), 1)
            self.assertFalse(assets[0].model_writable)
            self.assertEqual(cas.get_bytes(assets[0].blob), DATA)
            text = cas.put_bytes(b"print(7)\n", media_type="text/x-python")
            generated = GeneratedTextTree(
                assets[0].component_revision,
                assets[0].target_identity,
                assets[0].authorization_identity,
                (GeneratedTextFile("source/main.py", text),),
            )
            manifest = assemble_source_tree(generated, assets, read_blob=cas.get_bytes)
            entry = next(
                item for item in manifest.entries if item.path == selector().path
            )
            self.assertEqual(entry.origin.value, "authored-binary")
            self.assertEqual(entry.blob.digest, pin(DATA).digest)
            overwrite = replace(
                generated, files=(GeneratedTextFile(selector().path, text),)
            )
            with self.assertRaises(ArtifactAssemblyError):
                assemble_source_tree(overwrite, assets, read_blob=cas.get_bytes)
            # The current lock's exact asset entries are the only allowed
            # source-byte matches during qualification.
            audits = [
                json.loads(p.read_text())
                for p in (project / "provenance/source-promotion").rglob(
                    "generation-input-audits/*.json"
                )
            ]
            entries = [
                entry
                for audit in audits
                for entry in audit["entries"]
                if entry["kind"] == "immutable-asset"
            ]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["content_identity"], pin(DATA).uri)
            self.assertEqual((source / "assets/payload.bin").read_bytes(), DATA)

    def test_wrong_reviewed_pin_refuses_atomic_project_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, _reviewed, _review = self.prepare(root, asset_pin=pin(b"wrong pin"))
            status, output, errors = invoke(*self.acceptance(root, source))
            self.assertEqual((status, output), (2, ""))
            self.assertEqual(
                json.loads(errors)["error"]["code"], "promotion.input_identity_mismatch"
            )
            self.assertFalse((root / "promoted").exists())
            self.assertFalse(list(root.glob(".promoted.*")))
            self.assertEqual((source / "assets/payload.bin").read_bytes(), DATA)

    def test_qualification_binds_assets_from_the_actual_current_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, _reviewed, _review = self.prepare(root)
            profile = root / "profile.json"
            profile.write_text(
                json.dumps(
                    {
                        "schema": (
                            "urn:literate-ai:schema:v2:"
                            "local-regenerative-qualification-profile"
                        ),
                        "profile_id": "immutable-asset-boundary@1",
                        "build_command": ["python3", "build.py"],
                        "test_commands": [["python3", "tests.py"]],
                        "source_command": ["python3", "app.py"],
                        "generated_command": ["python3", "app.py"],
                        "cases": [
                            {"case_id": "value", "arguments": [], "expected_result": 7}
                        ],
                        "covered_surface_ids": ["surface.portable-app"],
                        "generated_root": "source",
                        "minimum_clean_runs": 2,
                        "timeout_seconds": 30,
                        "maximum_output_bytes": 1048576,
                    }
                )
            )
            promoted = self.success(
                *self.acceptance(root, source),
                "--qualification-profile",
                str(profile),
            )["promoted_project"]
            component = (root / "promoted" / promoted["component"]).parent
            # Stop after actual audited source exclusion and before planning or
            # generation. No declared build, source or test command may run.
            with (
                mock.patch(
                    "literate_ai.application.source_promotion.assess_source_exclusion",
                    wraps=assess_source_exclusion,
                ) as assessment,
                mock.patch(
                    "literate_ai.cli.generation.plan_from_args",
                    side_effect=CliFailure("fixture.after_asset_binding", "stop"),
                ),
            ):
                status, output, errors = invoke(
                    "spec",
                    "qualify",
                    str(component),
                    "--source",
                    str(source),
                    "--profile",
                    str(component / "qualification/profile.json"),
                    "--output",
                    str(root / "qualification.json"),
                    "--key",
                    str(root / "trust.key"),
                    "--signer",
                    "reviewer",
                    "--allow-host-execution",
                )
            self.assertEqual((status, output), (2, ""))
            self.assertEqual(
                json.loads(errors)["error"]["code"], "fixture.after_asset_binding"
            )
            assessment.assert_called_once()
            asset_path = (
                f"{component.relative_to(root / 'promoted')}/assets/payload.bin"
            )
            self.assertEqual(
                assessment.call_args.kwargs["immutable_asset_pins"],
                {asset_path: pin(DATA).uri},
            )
            self.assertFalse((root / "qualification.json").exists())
