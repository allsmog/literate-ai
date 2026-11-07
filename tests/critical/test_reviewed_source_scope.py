"""Complete declared scope without fabricating observations or entrypoints."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from literate_ai.cli._wire import component_graph_from_wire
from literate_ai.source_to_specification import SourceToSpecificationError
from literate_ai.source_to_specification.workflow import (
    validate_component_graph_projection,
)
from tests.support.fixtures_test_cli_arbitrary_source import (
    KEY,
    invoke,
    reviewed_static_graph,
)

SNAPSHOT = "sha256:" + "a" * 64


def projection_fixture(*, split=False):
    paths = {"main": "main.py", "helper": "helper.py"}
    observations = tuple(
        SimpleNamespace(
            observation_id="observation:" + name,
            evidence=(SimpleNamespace(evidence_id="evidence:" + name, path=path),),
        )
        for name, path in paths.items()
    )
    result = SimpleNamespace(
        request=SimpleNamespace(
            source_snapshot_id=SNAPSHOT,
            included_paths=("build.sh", "helper.py", "main.py"),
            excluded_paths=(),
        ),
        observations=observations,
        component_definition_draft=SimpleNamespace(
            coordinate="fixture/root", title="Fixture root"
        ),
    )

    def node(name, members):
        ids = sorted("evidence:" + member for member in members)
        capability = "fixture." + name
        return {
            "coordinate": "fixture/" + name,
            "title": "Fixture " + name,
            "kind": "library",
            "profiles": ["library", "portable"],
            "provided_capabilities": [capability],
            "capability_contracts": [
                {
                    "name": capability,
                    "contract": "Provide the reviewed value.",
                    "evidence_ids": ids,
                }
            ],
            "entrypoints": [],
            "build_needs": ["implementation.language-ecosystem"],
            "source_paths": sorted(paths[member] for member in members),
            "observation_ids": sorted("observation:" + member for member in members),
            "evidence_ids": ids,
        }

    nodes = (
        [node("root", ("main",)), node("helper", ("helper",))]
        if split
        else [node("root", ("main", "helper"))]
    )
    edges = (
        [
            {
                "source_coordinate": "fixture/root",
                "target_coordinate": "fixture/helper",
                "requirement_id": "helper",
                "capability": "fixture.helper",
                "version_range": "^1.0.0",
                "dependency_kind": "build",
                "optional": False,
                "evidence_ids": ["evidence:main"],
            }
        ]
        if split
        else []
    )
    return result, {
        "schema": "urn:literate-ai:schema:v3:component-graph-draft",
        "source_snapshot_id": SNAPSHOT,
        "root_coordinate": "fixture/root",
        "nodes": nodes,
        "edges": edges,
    }


class ReviewedSourceScopeTests(unittest.TestCase):
    def validate(self, result, graph):
        validate_component_graph_projection(result, component_graph_from_wire(graph))

    def test_declared_unobserved_file_can_have_explicit_owner(self):
        result, graph = projection_fixture()
        graph["nodes"][0]["source_paths"].insert(0, "build.sh")
        self.validate(result, graph)
        self.assertEqual(len(result.observations), 2)

    def test_unknown_and_excluded_extra_paths_refuse(self):
        for path, excluded in (("unknown.sh", ()), ("build.sh", ("build.sh",))):
            result, graph = projection_fixture()
            result.request.excluded_paths = excluded
            graph["nodes"][0]["source_paths"] = sorted(["main.py", "helper.py", path])
            with (
                self.subTest(path=path),
                self.assertRaises(SourceToSpecificationError) as error,
            ):
                self.validate(result, graph)
            self.assertEqual(
                error.exception.code, "workflow.component_graph_path_unknown"
            )

    def test_missing_observed_path_refuses(self):
        result, graph = projection_fixture()
        graph["nodes"][0]["source_paths"] = ["build.sh", "main.py"]
        with self.assertRaises(SourceToSpecificationError) as error:
            self.validate(result, graph)
        self.assertEqual(
            error.exception.code, "workflow.component_graph_path_incomplete"
        )

    def test_another_nodes_observed_path_cannot_be_borrowed(self):
        result, graph = projection_fixture(split=True)
        graph["nodes"][0]["source_paths"] = ["helper.py", "main.py"]
        with self.assertRaises(SourceToSpecificationError) as error:
            self.validate(result, graph)
        self.assertEqual(
            error.exception.code, "workflow.component_graph_path_incomplete"
        )

    def test_unobserved_scope_cannot_have_two_owners(self):
        result, graph = projection_fixture(split=True)
        for node in graph["nodes"]:
            node["source_paths"] = sorted([*node["source_paths"], "build.sh"])
        with self.assertRaises(SourceToSpecificationError) as error:
            self.validate(result, graph)
        self.assertEqual(
            error.exception.code, "workflow.component_graph_scope_ambiguous"
        )

    def test_declared_presence_does_not_authorize_unobserved_entrypoint(self):
        result, graph = projection_fixture()
        node = graph["nodes"][0]
        node.update(
            kind="cli",
            profiles=["cli", "portable"],
            source_paths=["build.sh", "helper.py", "main.py"],
            entrypoints=[
                {
                    "name": "run",
                    "kind": "cli",
                    "path": "build.sh",
                    "evidence_ids": node["evidence_ids"],
                }
            ],
        )
        with self.assertRaises(SourceToSpecificationError) as error:
            self.validate(result, graph)
        self.assertEqual(
            error.exception.code, "workflow.component_graph_entrypoint_evidence_unknown"
        )
        node["entrypoints"][0]["path"] = "main.py"
        self.validate(result, graph)

    def test_legacy_empty_scope_keeps_exact_evidence_paths(self):
        result, graph = projection_fixture()
        result.request.included_paths = ()
        self.validate(result, graph)
        graph["nodes"][0]["source_paths"].insert(0, "build.sh")
        with self.assertRaises(SourceToSpecificationError):
            self.validate(result, graph)


class ReviewedSourceScopeCliTests(unittest.TestCase):
    def success(self, *arguments):
        status, output, errors = invoke(*arguments)
        self.assertEqual((status, errors), (0, ""), output or errors)
        return json.loads(output)["result"]

    def test_signed_review_and_accept_preserve_the_full_declared_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "source"
            source.mkdir()
            (source / "main.py").write_text("def value():\n    return 7\n")
            script = b"#!/bin/sh\nprintf '%s\\n' reviewed-build-behavior\n"
            # Shell is behavioral source; a build file type the deriver does not
            # observe keeps this test on the declared-but-unobserved path.
            (source / "build.cmd").write_bytes(script)
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
            self.assertIn("build.cmd", bundle["result"]["request"]["included_paths"])
            self.assertNotIn(
                "build.cmd",
                {
                    item["path"]
                    for observation in bundle["result"]["observations"]
                    for item in observation["evidence"]
                },
            )
            (root / "bundle.json").write_text(json.dumps(bundle))
            graph = reviewed_static_graph(bundle)
            graph["nodes"][0]["source_paths"] = sorted(
                bundle["result"]["request"]["included_paths"]
            )
            (root / "graph.json").write_text(json.dumps(graph))
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
            result = self.success(
                "spec",
                "accept",
                str(source),
                str(root / "bundle.json"),
                "--review",
                str(root / "review.json"),
                "--trust-key",
                str(key),
                "--target",
                str(root / "accepted"),
                "--project-target",
                str(root / "promoted"),
            )
            self.assertTrue(
                (root / "promoted" / result["promoted_project"]["component"]).is_file()
            )
            self.assertEqual((source / "build.cmd").read_bytes(), script)
            self.assertFalse(list((root / "promoted").rglob("build.cmd")))
            retained_review = json.dumps(review)
            self.assertIn("build.cmd", retained_review)
