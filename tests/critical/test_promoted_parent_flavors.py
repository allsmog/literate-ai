"""Selected readable Flavor custody and signed promotion without host execution."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from literate_ai.adapters.component_lock_planning import ComponentLockPlanningError
from literate_ai.adapters.conversion_authority import FilesystemConversionAuthorityStore
from literate_ai.adapters.promotion_parent_flavors import (
    PromotionParentFlavorError,
    capture_parent_flavor_inputs,
)
from literate_ai.contracts import canonical_identity
from literate_ai.contracts.authoring_markdown import (
    parse_authoring_markdown,
    render_authoring_markdown,
)
from literate_ai.contracts.operator_adoption import ConversionAuthorityStage
from literate_ai.projects import PinnedInputClosureError, discover_project
from tests.support.fixtures_test_cli_arbitrary_source import (
    KEY,
    invoke,
    reviewed_static_graph,
)

SELECTOR = "+flavor://fixture/shared-cpp"


class PromotedParentFlavorTests(unittest.TestCase):
    def success(self, *arguments):
        status, output, errors = invoke(*arguments)
        self.assertEqual((status, errors), (0, ""), output or errors)
        return json.loads(output)["result"]

    def parent(self, root):
        parent = root / "parent"
        self.success("init", str(parent), "--empty", "--flavor", "cpp")
        flavor = parent / "flavors/shared-cpp"
        shutil.copytree(parent / "flavors/lang-cpp", flavor)
        document, body = parse_authoring_markdown(
            (flavor / "flavor.md").read_bytes(), source="fixture Flavor"
        )
        document = dict(document)
        document.update(
            namespace="fixture",
            name="shared-cpp",
            applicable_capabilities=[],
            conflicts=["flavor://literate-ai/lang-cpp"],
        )
        (flavor / "flavor.md").write_bytes(render_authoring_markdown(document, body))
        profile = flavor / "standard-command-profile.json"
        value = json.loads(profile.read_text())
        value["cpp_library_kind"] = "shared"
        profile.write_text(json.dumps(value) + "\n")
        return discover_project(parent), flavor

    def bundle(self, root, *, cuda=False):
        source = root / "source"
        source.mkdir()
        (source / "main.py").write_text(
            ("# CUDA driver compatibility\n" if cuda else "")
            + "def value():\n    return 7\n"
        )
        key = root / "fixture.key"
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
        bundle_path = root / "bundle.json"
        bundle_path.write_text(json.dumps(bundle))
        graph = reviewed_static_graph(bundle)
        node = graph["nodes"][0]
        node["provided_capabilities"] = ["fixture.value"]
        node["capability_contracts"] = [
            {
                "name": "fixture.value",
                "contract": "Return integer seven.",
                "evidence_ids": node["evidence_ids"],
            }
        ]
        node["library_imports"] = [
            {
                "language": "cpp",
                "capability": "fixture.value",
                "package": "fixture_value",
                "module": "fixture_value/value.h",
                "symbols": ["value"],
            }
        ]
        (root / "graph.json").write_text(json.dumps(graph))
        argv = [
            "spec",
            "review",
            str(bundle_path),
            "--actor",
            "reviewer",
            "--key",
            str(key),
            "--component-graph",
            str(root / "graph.json"),
        ]
        for identifier in bundle["review_gate"]["blocking_uncertainty_ids"]:
            argv += ["--resolve", identifier]
        review = self.success(*argv)
        review_path = root / "review.json"
        review_path.write_text(json.dumps(review))
        return [
            "spec",
            "accept",
            str(source),
            str(bundle_path),
            "--review",
            str(review_path),
            "--trust-key",
            str(key),
            "--target",
            str(root / "accepted"),
        ]

    def test_selected_parent_profile_and_skill_closure_have_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent, flavor = self.parent(root)
            captured = capture_parent_flavor_inputs(parent, (SELECTOR,))
            child = root / "child"
            child.mkdir()
            paths = captured.materialize(child)
            self.assertEqual(
                (
                    child / "flavors/shared-cpp/standard-command-profile.json"
                ).read_bytes(),
                (flavor / "standard-command-profile.json").read_bytes(),
            )
            self.assertGreaterEqual(len(captured.skill_references), 3)
            self.assertIn(
                "skills/specification-to-source/cpp17-portable-json-application/SKILL.md",
                paths,
            )
            self.assertIn(
                "skills/specification-to-source/portable-application-implementation/SKILL.md",
                paths,
            )
            self.assertIn(
                "skills/specification-to-source/portable-specification-planning/SKILL.md",
                paths,
            )
            captured.require_unchanged()
            self.assertFalse((child / "flavors/lang-cpp").exists())

    def test_unselected_parent_defaults_and_removals_do_not_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent, _ = self.parent(Path(temporary).resolve())
            self.assertIsNone(capture_parent_flavor_inputs(parent, ()))
            self.assertIsNone(
                capture_parent_flavor_inputs(
                    parent, ("-flavor://fixture/shared-cpp", "+cpp")
                )
            )

    def test_changed_profile_and_parent_configuration_refuse_revalidation(self):
        for changed in ("profile", "configuration"):
            with (
                self.subTest(changed=changed),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary).resolve()
                parent, flavor = self.parent(root)
                captured = capture_parent_flavor_inputs(parent, (SELECTOR,))
                path = (
                    flavor / "standard-command-profile.json"
                    if changed == "profile"
                    else parent.root / "literate.project.json"
                )
                path.write_bytes(path.read_bytes() + b"\n")
                with self.assertRaises(PinnedInputClosureError):
                    captured.require_unchanged()

    def test_different_initialized_profile_and_symlink_target_refuse(self):
        for symlink in (False, True):
            with (
                self.subTest(symlink=symlink),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary).resolve()
                parent, _ = self.parent(root)
                captured = capture_parent_flavor_inputs(parent, (SELECTOR,))
                target = root / "child/flavors/shared-cpp/standard-command-profile.json"
                target.parent.mkdir(parents=True)
                other = root / "other.json"
                other.write_text('{"cpp_library_kind":"static"}\n')
                if symlink:
                    target.symlink_to(other)
                else:
                    target.write_bytes(other.read_bytes())
                with self.assertRaises(PromotionParentFlavorError) as error:
                    captured.materialize(root / "child")
                self.assertEqual(
                    error.exception.code, "promotion.parent_flavor_collision"
                )
                self.assertEqual(other.read_text(), '{"cpp_library_kind":"static"}\n')

    def test_symlinked_parent_profile_is_not_dereferenced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent, flavor = self.parent(root)
            path = flavor / "standard-command-profile.json"
            other = root / "profile.json"
            path.rename(other)
            path.symlink_to(other)
            with self.assertRaises(ComponentLockPlanningError) as error:
                capture_parent_flavor_inputs(parent, (SELECTOR,))
            self.assertEqual(error.exception.code, "component_lock.flavor_unsafe")

    def test_added_ambiguous_parent_membership_refuses_after_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent, flavor = self.parent(root)
            captured = capture_parent_flavor_inputs(parent, (SELECTOR,))
            shutil.copytree(flavor, parent.root / "flavors/duplicate")
            with self.assertRaises(ComponentLockPlanningError) as error:
                captured.require_unchanged()
            self.assertEqual(error.exception.code, "component_lock.flavor_duplicate")

    def test_project_local_input_without_a_readable_target_owner_refuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent, flavor = self.parent(root)
            profile = parent.root / "outside-profile.json"
            profile.write_bytes((flavor / "standard-command-profile.json").read_bytes())
            document, body = parse_authoring_markdown(
                (flavor / "flavor.md").read_bytes(), source="fixture Flavor"
            )
            document = dict(document)
            document["contributions"][0]["content"]["uri"] = (
                "../../outside-profile.json"
            )
            (flavor / "flavor.md").write_bytes(
                render_authoring_markdown(document, body)
            )
            with self.assertRaises(PromotionParentFlavorError) as error:
                capture_parent_flavor_inputs(parent, (SELECTOR,))
            self.assertEqual(
                error.exception.code, "promotion.parent_flavor_input_scope"
            )

    def test_explicit_catalog_promotes_independently_without_changing_its_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent, flavor = self.parent(root)
            before = (parent.root / "literate.project.json").read_bytes()
            original = (flavor / "standard-command-profile.json").read_bytes()
            self.success(
                *self.bundle(root),
                "--project-target",
                str(root / "child"),
                "--qualification-catalog",
                str(parent.root),
                "--flavor",
                SELECTOR,
                "--flavor",
                "+linux",
            )
            self.assertEqual(
                (parent.root / "literate.project.json").read_bytes(), before
            )
            self.assertEqual(
                (flavor / "standard-command-profile.json").read_bytes(), original
            )
            self.assertEqual(
                (
                    root / "child/flavors/shared-cpp/standard-command-profile.json"
                ).read_bytes(),
                original,
            )
            self.assertFalse((parent.root / ".literate/native-projects").exists())
            defaults = json.loads((root / "child/literate.project.json").read_text())[
                "default_flavor_selectors"
            ]
            self.assertIn(SELECTOR, defaults)
            self.assertNotIn("+flavor://literate-ai/lang-cpp", defaults)

    def test_catalog_cannot_override_integration_or_exist_without_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent, _ = self.parent(root)
            arguments = self.bundle(root)
            for additions, code in (
                ([], "promotion.qualification_catalog_requires_project"),
                (
                    ["--integrate-project", str(parent.root)],
                    "promotion.qualification_catalog_conflict",
                ),
                (
                    [
                        "--project-target",
                        str(root / "child"),
                        "--qualification-catalog",
                        str(root / "source"),
                    ],
                    "promotion.qualification_catalog_invalid",
                ),
            ):
                with self.subTest(code=code):
                    status, _, errors = invoke(
                        *arguments,
                        "--qualification-catalog",
                        str(parent.root),
                        *additions,
                    )
                    self.assertEqual(status, 2)
                    self.assertEqual(json.loads(errors)["error"]["code"], code)
                    self.assertFalse((root / "child").exists())
                    self.assertFalse((root / "accepted").exists())

    def test_changed_parent_profile_before_publication_never_exposes_a_child(self):
        from literate_ai.cli.project import validate_project_from_args

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent, flavor = self.parent(root)
            arguments = self.bundle(root)

            def changed(args):
                result = validate_project_from_args(args)
                profile = flavor / "standard-command-profile.json"
                profile.write_bytes(profile.read_bytes() + b"\n")
                return result

            with mock.patch(
                "literate_ai.cli.project.validate_project_from_args",
                side_effect=changed,
            ):
                status, _, errors = invoke(
                    *arguments,
                    "--project-target",
                    str(root / "child"),
                    "--qualification-catalog",
                    str(parent.root),
                    "--flavor",
                    SELECTOR,
                    "--flavor",
                    "+linux",
                )
            self.assertEqual(status, 2)
            self.assertEqual(
                json.loads(errors)["error"]["code"], "inputs.closure_changed"
            )
            self.assertFalse((root / "child").exists())

    def test_signed_integration_preserves_custom_shared_profile_in_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent, flavor = self.parent(root)
            original_profile = (flavor / "standard-command-profile.json").read_bytes()
            # This fixture declares a metadata stage to exercise the signed
            # admission path. It claims no retained lifecycle or native execution.
            store = FilesystemConversionAuthorityStore(parent.root)
            store.initialize(
                project_id=parent.definition.project_id,
                evidence_identity=canonical_identity({"fixture": "retained-metadata"}),
            )
            store.advance(
                ConversionAuthorityStage.RETAINED,
                evidence_identities=(canonical_identity({"fixture": "stage-only"}),),
            )
            result = self.success(
                *self.bundle(root),
                "--integrate-project",
                str(parent.root),
                "--flavor",
                SELECTOR,
                "--flavor",
                "+linux",
            )
            child = Path(result["promoted_project"]["project_target"])
            self.assertEqual(
                (
                    child / "flavors/shared-cpp/standard-command-profile.json"
                ).read_bytes(),
                original_profile,
            )
            self.assertEqual(
                (flavor / "standard-command-profile.json").read_bytes(),
                original_profile,
            )
            defaults = json.loads((child / "literate.project.json").read_text())[
                "default_flavor_selectors"
            ]
            self.assertIn(SELECTOR, defaults)
            self.assertNotIn("+flavor://literate-ai/lang-cpp", defaults)
            audits = [
                json.loads(path.read_text())
                for path in child.rglob("*generation-input-audits/*.json")
            ]
            entries = [entry for audit in audits for entry in audit["entries"]]
            self.assertTrue(
                any(
                    entry["target_path"]
                    == "flavors/shared-cpp/standard-command-profile.json"
                    for entry in entries
                )
            )
            self.assertFalse(list(child.rglob("*.so")))

    def test_canonical_accelerator_alias_keeps_target_and_contributions(self):
        from literate_ai.cli.project import init_project_from_args

        def inherited_accelerator(args):
            args.flavors = [*args.flavors, "flavor://literate-ai/accel-nvidia-cuda"]
            return init_project_from_args(args)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            arguments = self.bundle(root, cuda=True)
            with mock.patch(
                "literate_ai.cli.project.init_project_from_args",
                side_effect=inherited_accelerator,
            ):
                self.success(
                    *arguments,
                    "--project-target",
                    str(root / "child"),
                    "--flavor",
                    "+cpp",
                    "--flavor",
                    "+linux",
                )
            path = root / "child/flavors/accel-nvidia-cuda/flavor.md"
            document, _ = parse_authoring_markdown(
                path.read_bytes(), source="promoted accelerator"
            )
            self.assertEqual(document["target"], "nvidia-cuda")
            self.assertEqual(document["namespace"], "literate-ai")
            self.assertTrue(document["contributions"])

    def test_alias_does_not_authorize_a_different_initialized_target(self):
        from literate_ai.cli.project import init_project_from_args

        def changed(args):
            args.flavors = [*args.flavors, "flavor://literate-ai/accel-nvidia-cuda"]
            result = init_project_from_args(args)
            path = Path(args.path) / "flavors/accel-nvidia-cuda/flavor.md"
            document, body = parse_authoring_markdown(
                path.read_bytes(), source="altered fixture"
            )
            document = dict(document)
            document["target"] = "python"
            path.write_bytes(render_authoring_markdown(document, body))
            return result

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            arguments = self.bundle(root, cuda=True)
            with mock.patch(
                "literate_ai.cli.project.init_project_from_args", side_effect=changed
            ):
                status, _, errors = invoke(
                    *arguments,
                    "--project-target",
                    str(root / "child"),
                    "--flavor",
                    "+cpp",
                    "--flavor",
                    "+linux",
                )
            self.assertEqual(status, 2)
            self.assertEqual(
                json.loads(errors)["error"]["code"], "promotion.flavor_collision"
            )
            self.assertFalse((root / "child").exists())
