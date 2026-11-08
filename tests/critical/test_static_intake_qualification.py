from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace
from pathlib import Path

from literate_ai.application.source_promotion import qualify_locked_source_promotion
from literate_ai.contracts import canonical_identity
from literate_ai.source_to_specification.model_workflow import (
    SOURCE_TRANSLATION_RUN_SCHEMA,
)
from literate_ai.source_to_specification.promotion_materialization import (
    REVIEWED_SOURCE_PROMOTION_PROVENANCE_SCHEMA,
    SourcePromotionError,
    verify_source_promotion_evidence,
)
from literate_ai.source_to_specification.static_intake import (
    StaticIntakeFile,
    StaticIntakePlan,
    StaticIntakeValidation,
    sign_static_intake_review,
)
from tests.support import fixtures_test_locked_source_promotion as fixtures
from tests.support.fixtures_test_schema_catalog import SchemaCatalog


class StaticIntakeQualificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LockedSourcePromotionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.review = sign_static_intake_review(
            StaticIntakePlan(
                self.fixture.retained.source_snapshot_identity.uri,
                fixtures._identity("origin").uri,
                (
                    StaticIntakeFile(
                        "example.ts",
                        "sha256:" + hashlib.sha256(b"owned").hexdigest(),
                        5,
                        "typescript",
                        "sensitive",
                        "Owned fixture",
                    ),
                ),
            ),
            actor="owned-reviewer",
            authority="human",
            reason="Owned fixture contract",
            key=b"owned-fixture-intake-qualification-key-32-bytes",
        )

    def validation(self, projection=None):
        projection = projection or self.fixture.retained
        return StaticIntakeValidation(
            self.review.source_snapshot_id,
            projection.provenance_reference_identity.uri,
            self.review.identity,
            self.fixture.qualification.identity.uri,
            self.review.key_id,
        )

    def test_measured_qualification_requires_every_exact_review_binding(self):
        fixture = self.fixture
        with self.assertRaises(SourcePromotionError):
            replace(fixture.evidence, static_intake_review=self.review)
        validation = self.validation()
        evidence = replace(
            fixture.evidence,
            static_intake_review=self.review,
            static_intake_validation=validation,
        )
        self.assertIn(
            validation.identity,
            [item.uri for item in evidence.qualification_evidence_identities],
        )
        projection = qualify_locked_source_promotion(fixture.authority, evidence)
        self.assertIn(
            validation.identity, [item.uri for item in projection.evidence_identities]
        )
        for name in (
            "source_snapshot_id",
            "promotion_reference_id",
            "review_identity",
            "qualification_result_id",
            "trust_key_id",
        ):
            with self.subTest(binding=name), self.assertRaises(SourcePromotionError):
                replace(
                    evidence,
                    static_intake_validation=replace(
                        validation, **{name: fixtures._identity("other").uri}
                    ),
                )
        with self.assertRaises(SourcePromotionError):
            replace(evidence, static_intake_review=None)

    def test_qualified_reader_requires_projection_bound_validation_without_private_key(
        self,
    ):
        fixture = self.fixture
        root = Path(fixture.temporary.name).resolve() / "promoted"
        root.mkdir()
        (root / "component.md").write_bytes(
            (fixture.component / "component.md").read_bytes()
        )
        audit = fixture.evidence.promotion_input_audits[0]
        audit_name = f"generation-input-audits/{audit.identity.split(':')[1]}.json"
        translation = {
            "schema": SOURCE_TRANSLATION_RUN_SCHEMA,
            "mode": "deterministic-static",
            "intelligence": None,
            "journals": [],
        }
        translation_identity = canonical_identity(translation)
        record = {
            "schema": REVIEWED_SOURCE_PROMOTION_PROVENANCE_SCHEMA,
            "source_snapshot_identity": self.review.source_snapshot_id,
            "specification_set_identity": (
                fixture.retained.specification_set_identity.uri
            ),
            "review_identity": fixtures._identity("intent-review").uri,
            "component_graph_identity": fixtures._identity("graph").uri,
            "translation_identity": translation_identity.uri,
            "inverse_evidence_reference": None,
            "authored_asset_bindings": [],
            "static_intake_review_reference": {
                "kind": "static-intake-review",
                "identity": self.review.identity,
                "path": "static-intake-review.json",
            },
            "generation_input_audit_references": [
                {
                    "kind": "generation-input-audit",
                    "identity": audit.identity,
                    "path": audit_name,
                }
            ],
        }
        promotion_identity = canonical_identity(record)
        retained = replace(
            fixture.retained, provenance_reference_identity=promotion_identity
        )
        validation = self.validation(retained)
        evidence = replace(
            fixture.evidence,
            authority_projection=retained,
            static_intake_review=self.review,
            static_intake_validation=validation,
            translation=translation,
            translation_identity=translation_identity,
        )
        projection = qualify_locked_source_promotion(fixture.authority, evidence)
        provenance = root / "provenance/source-promotion" / promotion_identity.digest
        (provenance / "generation-input-audits").mkdir(parents=True)
        for name, value in [
            ("reference.json", record),
            (audit_name, audit.to_dict()),
            ("static-intake-review.json", self.review.to_dict()),
            ("source-translation.json", translation),
        ]:
            (provenance / name).write_text(json.dumps(value))
        qualification_root = root / "provenance/qualification"
        qualification_root.mkdir()
        (
            qualification_root / f"{fixture.qualification.identity.digest}.json"
        ).write_text(json.dumps(fixture.qualification.to_dict()))
        with self.assertRaises(SourcePromotionError) as raised:
            verify_source_promotion_evidence(root, projection)
        self.assertEqual(
            raised.exception.code, "promotion.static_intake_validation_required"
        )
        validation_root = provenance / "static-intake-validations"
        validation_root.mkdir()
        validation_path = validation_root / f"{validation.identity.split(':')[1]}.json"
        original = json.dumps(validation.to_dict()).encode()
        validation_path.write_bytes(original)
        reopened = verify_source_promotion_evidence(root, projection)
        self.assertEqual(reopened.static_intake_validation, validation)
        self.assertEqual(reopened.static_intake_review, self.review)
        self.assertEqual(reopened.qualification_lifecycle_result, fixture.qualification)
        schema = SchemaCatalog(Path(__file__).resolve().parents[2] / "schemas/v2")
        schema.validate(record["schema"], record)
        schema.validate(
            "urn:literate-ai:schema:v1:source-static-intake-review",
            self.review.to_dict(),
        )
        schema.validate(
            "urn:literate-ai:schema:v1:source-static-intake-validation",
            validation.to_dict(),
        )
        for content in [
            json.dumps(
                {
                    **validation.to_dict(),
                    "review_identity": fixtures._identity("other").uri,
                }
            ).encode(),
            original[:-1] + b',"purpose":"local-static-intake"}',
            original.decode().encode("utf-16"),
        ]:
            with self.subTest(content_length=len(content)):
                validation_path.write_bytes(content)
                with self.assertRaises(SourcePromotionError):
                    verify_source_promotion_evidence(root, projection)
        validation_path.write_bytes(original)
        self.assertEqual(
            verify_source_promotion_evidence(root, projection).static_intake_validation,
            validation,
        )
