from argparse import Namespace
import json
import unittest

from pathlib import Path
import tempfile

from tools.style_pack_manager import (
    GENERATION_FIELDS, StylePackError, StylePaths, command_approve_character,
    command_approve_standalone, command_init, command_validate,
    read_csv, registered_approved_generation, require_qa_passed_generation_for_approval,
    sha256,
    parse_aux_body_references,
    validate_reference_compatibility,
    style_readiness_proposal, validate_plan_reference, validate_prior_stages,
    validate_prompt_only_body_library_review, write_csv,
)


def make_args(*, prompt_only: bool, decision: str, reviewed: int, total: int) -> Namespace:
    return Namespace(
        prompt_only_physique=prompt_only,
        aux_body_decision=decision,
        body_library_candidates_reviewed=reviewed,
        body_library_relevant_candidates_total=total,
    )


def write_qa_evidence(
    image: Path, plan: Path, *, output_hash: str | None = None,
    stage_id: str = "FINAL", record_status: str = "TEST",
) -> str:
    """Fixture equivalent of record-generation's non-CLI QA receipt."""
    plan_snapshot = image.with_suffix(image.suffix + ".qa-plan.json")
    contract = image.with_suffix(image.suffix + ".qa-contract.json")
    evidence = image.with_suffix(image.suffix + ".qa-evidence.json")
    plan_snapshot.write_bytes(plan.read_bytes())
    contract.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "contract_version": 1,
                "plan_snapshot": str(plan_snapshot),
                "plan_content_sha256": sha256(plan_snapshot),
                "stage_id": stage_id,
                "expected_qa_layers": ["STYLE"],
                "qa_results": {"STYLE": "PASS"},
            }
        ),
        encoding="utf-8",
    )
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "record_status": record_status,
                "output_sha256": output_hash or sha256(image),
                "qa_contract": str(contract),
                "qa_contract_sha256": sha256(contract),
            }
        ),
        encoding="utf-8",
    )
    return str(evidence)


def qa_manifest_fields(image: Path) -> dict[str, str]:
    evidence = image.with_suffix(image.suffix + ".qa-evidence.json")
    receipt = json.loads(evidence.read_text(encoding="utf-8"))
    contract = Path(receipt["qa_contract"])
    plan_snapshot = Path(json.loads(contract.read_text(encoding="utf-8"))["plan_snapshot"])
    return {
        "qa_output_sha256": sha256(image),
        "qa_receipt_sha256": sha256(evidence),
        "qa_contract_sha256": sha256(contract),
        "qa_plan_sha256": sha256(plan_snapshot),
    }


class BodyLibraryReviewTests(unittest.TestCase):
    def test_unselected_body_library_is_neutral_not_user_declined(self) -> None:
        self.assertEqual(
            parse_aux_body_references(Path("."), [], "NOT_SELECTED", "", False, False),
            [],
        )

    def test_external_anatomy_requires_reviewed_compatible_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            library = workspace / "BODY_REFERENCE_LIBRARY"
            (library / "refs").mkdir(parents=True)
            image = library / "refs" / "body.png"
            image.write_bytes(b"body")
            write_csv(library / "BODY_REFERENCE_MANIFEST.csv", [
                "ref_id", "generator_safe", "generator_path", "allowed_roles", "source_character_id",
                "anatomy_compatibility", "anatomy_evidence_source",
            ], [{
                "ref_id": "BR_0001", "generator_safe": "YES", "generator_path": "refs/body.png",
                "allowed_roles": "AUX_BODY_BUILD", "source_character_id": "UNKNOWN",
                "anatomy_compatibility": "FEMALE_ANATOMY", "anatomy_evidence_source": "reviewed source metadata",
            }])
            with self.assertRaisesRegex(StylePackError, "BLOCKED"):
                parse_aux_body_references(
                    workspace, ["BR_0001=BODY_BUILD_TARGET"], "SELECTED", "", True, False,
                    {"character_id": "NEW", "target_anatomy": "MALE_ANATOMY", "anatomy_evidence_source": "explicit user request"},
                )

    def test_soft_body_reference_role_crosses_visible_presentation(self) -> None:
        decision = validate_reference_compatibility(
            {"character_id": "CHAR_009", "target_anatomy": "UNKNOWN"},
            {"source_character_id": "CHAR_004", "anatomy_compatibility": "UNKNOWN"},
            ["AUX_POSE"],
        )
        self.assertTrue(decision["compatible"])
        self.assertEqual(decision["status"], "ALLOWED")

    def test_selected_library_blocks_prompt_only_before_complete_relevant_review(self) -> None:
        with self.assertRaisesRegex(StylePackError, "reviewed 2 of 5"):
            validate_prompt_only_body_library_review(
                make_args(prompt_only=True, decision="SELECTED", reviewed=2, total=5)
            )

    def test_selected_library_allows_prompt_only_after_complete_relevant_review(self) -> None:
        validate_prompt_only_body_library_review(
            make_args(prompt_only=True, decision="SELECTED", reviewed=5, total=5)
        )

    def test_visual_body_route_does_not_require_prompt_only_review_counts(self) -> None:
        validate_prompt_only_body_library_review(
            make_args(prompt_only=False, decision="SELECTED", reviewed=0, total=0)
        )

    def test_generation_reference_rejects_unregistered_and_rejected_pending_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            image = paths.generations / "00_PENDING" / "req" / "REJECTED" / "bad.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"bad")
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [])
            with self.assertRaisesRegex(StylePackError, "unregistered"):
                validate_plan_reference(paths, str(image), "STYLE")

    def test_newest_rejected_record_overrides_older_test_and_approved_records(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            paths.generations.mkdir()
            image = workspace / "result.png"
            image.write_bytes(b"result")
            plan = workspace / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            base = {field: "" for field in GENERATION_FIELDS}
            qa_evidence = write_qa_evidence(image, plan)
            test_row = {**base, "status": "TEST", "style_file": str(image), "reference_plan": str(plan), "qa_evidence": qa_evidence, **qa_manifest_fields(image)}
            rejected_row = {**base, "status": "REJECTED", "style_file": str(image), "reference_plan": str(plan), "qa_evidence": qa_evidence, **qa_manifest_fields(image)}
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [test_row, rejected_row])
            with self.assertRaisesRegex(StylePackError, "REJECTED"):
                require_qa_passed_generation_for_approval(paths, image)
            approved_row = {**base, "status": "APPROVED_STANDALONE", "style_file": str(image), "reference_plan": str(plan), "qa_evidence": qa_evidence, **qa_manifest_fields(image)}
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [approved_row, rejected_row])
            self.assertIsNone(registered_approved_generation(paths, image))

    def test_distinct_copy_rejection_overrides_original_test_by_hash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            paths.generations.mkdir()
            original = workspace / "original.png"
            rejected_copy = workspace / "rejected-copy.png"
            original.write_bytes(b"same-content")
            rejected_copy.write_bytes(b"same-content")
            plan = workspace / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            base = {field: "" for field in GENERATION_FIELDS}
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [
                {**base, "status": "TEST", "style_file": str(original), "reference_plan": str(plan), "qa_evidence": write_qa_evidence(original, plan), **qa_manifest_fields(original)},
                {**base, "status": "REJECTED", "style_file": str(rejected_copy), "reference_plan": str(plan), "qa_evidence": write_qa_evidence(rejected_copy, plan), **qa_manifest_fields(rejected_copy)},
            ])
            with self.assertRaisesRegex(StylePackError, "REJECTED"):
                require_qa_passed_generation_for_approval(paths, original)

    def test_notes_and_plan_without_bound_qa_hash_do_not_pass_generation_qa(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            paths.generations.mkdir()
            image = workspace / "result.png"
            image.write_bytes(b"result")
            plan = workspace / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            row = {field: "" for field in GENERATION_FIELDS}
            row.update({"status": "TEST", "style_file": str(image), "reference_plan": str(plan), "notes": "[QA_REQUIRED=STYLE]"})
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [row])
            with self.assertRaisesRegex(StylePackError, "QA-incomplete"):
                require_qa_passed_generation_for_approval(paths, image)
            row["notes"] = "[QA_REQUIRED=STYLE] [QA_OUTPUT_SHA256=" + sha256(image) + "]"
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [row])
            with self.assertRaisesRegex(StylePackError, "QA-incomplete"):
                require_qa_passed_generation_for_approval(paths, image)
            row["qa_evidence"] = write_qa_evidence(image, plan, output_hash="0" * 64)
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [row])
            with self.assertRaisesRegex(StylePackError, "QA-incomplete"):
                require_qa_passed_generation_for_approval(paths, image)
            row["qa_evidence"] = write_qa_evidence(image, plan)
            row.update(qa_manifest_fields(image))
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [row])
            self.assertEqual(require_qa_passed_generation_for_approval(paths, image)["status"], "TEST")

    def test_manifest_binding_requires_exact_style_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            paths.generations.mkdir()
            style_file = workspace / "style.png"
            source_copy = workspace / "source.png"
            archive_copy = workspace / "archive.png"
            style_file.write_bytes(b"authoritative-output")
            source_copy.write_bytes(style_file.read_bytes())
            archive_copy.write_bytes(style_file.read_bytes())
            plan = workspace / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            row = {field: "" for field in GENERATION_FIELDS}
            row.update({
                "status": "TEST", "style_file": str(style_file), "source_image": str(source_copy),
                "archive_file": str(archive_copy), "reference_plan": str(plan),
                "qa_evidence": write_qa_evidence(style_file, plan), **qa_manifest_fields(style_file),
            })
            style_file.write_bytes(b"mutated-output")
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [row])
            with self.assertRaisesRegex(StylePackError, "QA-incomplete"):
                require_qa_passed_generation_for_approval(paths, style_file)

    def test_validate_scans_qa_bearing_staging_but_allows_pending_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            command_init(Namespace(workspace=workspace, style_name="TEST", source=None, non_recursive=False))
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            image = workspace / "staging.png"
            image.write_bytes(b"staging")
            plan = workspace / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            staging = {field: "" for field in GENERATION_FIELDS}
            staging.update({
                "generation_id": "GEN_STAGING", "status": "STAGING", "style_file": str(image),
                "reference_plan": str(plan), "qa_evidence": write_qa_evidence(image, plan),
                **qa_manifest_fields(image),
            })
            pending = {field: "" for field in GENERATION_FIELDS}
            pending.update({"generation_id": "GEN_PENDING", "status": "STAGING"})
            rejected = {field: "" for field in GENERATION_FIELDS}
            rejected.update({"generation_id": "GEN_REJECTED", "status": "REJECTED"})
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [staging, pending, rejected])
            command_validate(Namespace(workspace=workspace, style_name="TEST", strict=False))
            Path(staging["qa_evidence"]).write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(StylePackError, "Validation failed"):
                command_validate(Namespace(workspace=workspace, style_name="TEST", strict=False))

    def test_prior_stage_requires_newest_bound_qa_passed_staging(self) -> None:
        plan = {
            "generation_workflow": {
                "mode": "MULTI_STAGE",
                "stages": [
                    {"stage_id": "FIRST", "required_qa": ["STYLE"]},
                    {"stage_id": "SECOND", "required_qa": ["STYLE"]},
                ],
            }
        }
        cases = {
            "valid_staging": ("STAGING", True, False, False, False),
            "rejected": ("REJECTED", False, False, False, True),
            "corrupt_receipt": ("STAGING", True, True, False, True),
            "qa_less_staging": ("STAGING", False, False, False, True),
            "newer_invalid_overrides_old_success": ("STAGING", True, False, True, True),
        }
        for name, (status, include_receipt, corrupt, newer_invalid, blocked) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                workspace = Path(folder)
                paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
                paths.generations.mkdir()
                source = workspace / "first.png"
                source.write_bytes(b"first")
                source_plan = workspace / "plan.json"
                source_plan.write_text("{}", encoding="utf-8")
                rows: list[dict[str, str]] = []
                if newer_invalid:
                    old = {field: "" for field in GENERATION_FIELDS}
                    old.update({
                        "request_id": "request", "status": "STAGING", "style_file": str(source),
                        "reference_plan": str(source_plan), "notes": "[STAGE_ID=FIRST]",
                        "qa_evidence": write_qa_evidence(source, source_plan, stage_id="FIRST", record_status="STAGING"),
                        **qa_manifest_fields(source),
                    })
                    rows.append(old)
                row = {field: "" for field in GENERATION_FIELDS}
                row.update({"request_id": "request", "status": status, "style_file": str(source), "notes": "[STAGE_ID=FIRST]"})
                if include_receipt:
                    row.update({
                        "reference_plan": str(source_plan),
                        "qa_evidence": write_qa_evidence(source, source_plan, stage_id="FIRST", record_status="STAGING"),
                        **qa_manifest_fields(source),
                    })
                if corrupt:
                    Path(row["qa_evidence"]).write_text("{}", encoding="utf-8")
                rows.append(row)
                if newer_invalid:
                    row["status"] = "REJECTED"
                write_csv(paths.generation_manifest, GENERATION_FIELDS, rows)
                if blocked:
                    with self.assertRaisesRegex(StylePackError, "FIRST"):
                        validate_prior_stages(paths, plan, "request", "SECOND")
                else:
                    validate_prior_stages(paths, plan, "request", "SECOND")

    def test_unformed_pack_source_cannot_be_explicit_planning_reference(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            source = paths.pack / "00_SOURCE_ORIGINALS" / "source.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"source")
            with self.assertRaisesRegex(StylePackError, "unformed style-pack source"):
                validate_plan_reference(paths, str(source), "STYLE")

    def test_approved_copy_preserves_qa_provenance_for_positive_reference(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            paths.pack.mkdir()
            paths.metadata.write_text('{"schema_version": 5, "style_name": "TEST", "style_slug": "TEST"}', encoding="utf-8")
            source = paths.generations / "00_PENDING" / "request" / "source.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"source")
            plan = workspace / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            row = {field: "" for field in GENERATION_FIELDS}
            row.update({"generation_id": "GEN_PARENT", "status": "TEST", "style_file": str(source), "reference_plan": str(plan), "qa_evidence": write_qa_evidence(source, plan), **qa_manifest_fields(source)})
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [row])
            command_approve_standalone(Namespace(workspace=workspace, style_name="TEST", user_approved=True, image=str(source), request_id="approved", description="Approved output", fidelity=90, risk_level="D1", reference_plan="", approval_quote="", notes=""))
            approved_row = read_csv(paths.generation_manifest)[-1]
            self.assertEqual(approved_row["parent_generation"], "GEN_PARENT")
            self.assertEqual(approved_row["reference_plan"], str(plan))
            self.assertTrue(approved_row["qa_evidence"])
            plan.write_text('{"mutated": true}', encoding="utf-8")
            source.with_suffix(source.suffix + ".qa-evidence.json").unlink()
            source.with_suffix(source.suffix + ".qa-contract.json").unlink()
            source.with_suffix(source.suffix + ".qa-plan.json").unlink()
            selected = validate_plan_reference(paths, approved_row["style_file"], "STYLE")
            self.assertEqual(selected["status"], "APPROVED_STANDALONE")

    def test_qa_snapshot_rejects_corrupted_layers_or_results(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            paths.generations.mkdir()
            image = workspace / "result.png"
            image.write_bytes(b"result")
            plan = workspace / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            for mutation in (
                lambda contract: contract.update({"expected_qa_layers": ["STYLE", "STYLE"]}),
                lambda contract: contract.update({"expected_qa_layers": ["UNKNOWN_LAYER"], "qa_results": {"UNKNOWN_LAYER": "PASS"}}),
                lambda contract: contract.update({"qa_results": {"STYLE": "FAIL"}}),
                lambda contract: contract.update({"qa_results": {}}),
            ):
                row = {field: "" for field in GENERATION_FIELDS}
                evidence_path = Path(write_qa_evidence(image, plan))
                contract_path = image.with_suffix(image.suffix + ".qa-contract.json")
                contract = json.loads(contract_path.read_text(encoding="utf-8"))
                mutation(contract)
                contract_path.write_text(json.dumps(contract), encoding="utf-8")
                receipt = json.loads(evidence_path.read_text(encoding="utf-8"))
                receipt["qa_contract_sha256"] = sha256(contract_path)
                evidence_path.write_text(json.dumps(receipt), encoding="utf-8")
                row.update({"status": "TEST", "style_file": str(image), "reference_plan": str(plan), "qa_evidence": str(evidence_path)})
                write_csv(paths.generation_manifest, GENERATION_FIELDS, [row])
                with self.assertRaisesRegex(StylePackError, "QA-incomplete"):
                    require_qa_passed_generation_for_approval(paths, image)

    def test_approve_character_rejects_unregistered_or_rejected_base(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            paths.pack.mkdir()
            paths.metadata.write_text('{"schema_version": 5, "style_name": "TEST", "style_slug": "TEST"}', encoding="utf-8")
            pending = paths.generations / "00_PENDING" / "request"
            pending.mkdir(parents=True)
            image = pending / "base.png"
            image.write_bytes(b"base")
            args = Namespace(workspace=workspace, style_name="TEST", user_approved=True, request_id="request", image=str(image), name="Name", fidelity=90, risk_level="D1", face_reference=[], body_reference=[], wardrobe_reference=[], accessory_reference=[], notes="")
            with self.assertRaisesRegex(StylePackError, "unregistered"):
                command_approve_character(args)
            plan = workspace / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            row = {field: "" for field in GENERATION_FIELDS}
            row.update({"status": "REJECTED", "style_file": str(image), "reference_plan": str(plan), "qa_evidence": write_qa_evidence(image, plan), **qa_manifest_fields(image)})
            write_csv(paths.generation_manifest, GENERATION_FIELDS, [row])
            with self.assertRaisesRegex(StylePackError, "REJECTED"):
                command_approve_character(args)

    def test_style_readiness_proposes_only_after_five_unique_qa_passed_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            paths.pack.mkdir()
            paths.generations.mkdir()
            plan = workspace / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            rows = []
            for index in range(5):
                image = workspace / f"result-{index}.png"
                image.write_bytes(f"result-{index}".encode())
                rows.append({field: "" for field in GENERATION_FIELDS})
                rows[-1].update({"status": "TEST", "reference_plan": str(plan), "qa_evidence": write_qa_evidence(image, plan), "style_file": str(image), **qa_manifest_fields(image)})
            write_csv(paths.generation_manifest, GENERATION_FIELDS, rows)
            proposal = style_readiness_proposal(paths)
            self.assertEqual(proposal["style_formation"], "UNKNOWN")
            self.assertEqual(proposal["proposal"], "CONSENT_REQUIRED_STYLE_CALIBRATION")
            self.assertFalse(proposal["calibration_started"])

    def test_style_readiness_excludes_hash_whose_newest_record_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            paths.pack.mkdir()
            paths.generations.mkdir()
            plan = workspace / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            rows = []
            for index in range(5):
                image = workspace / f"result-{index}.png"
                image.write_bytes(f"result-{index}".encode())
                row = {field: "" for field in GENERATION_FIELDS}
                row.update({"status": "TEST", "reference_plan": str(plan), "qa_evidence": write_qa_evidence(image, plan), "style_file": str(image), **qa_manifest_fields(image)})
                rows.append(row)
                if index == 0:
                    rejected_copy = workspace / "later-rejected-copy.png"
                    rejected_copy.write_bytes(image.read_bytes())
                    rejected = {field: "" for field in GENERATION_FIELDS}
                    rejected.update({"status": "REJECTED", "reference_plan": str(plan), "qa_evidence": write_qa_evidence(rejected_copy, plan), "style_file": str(rejected_copy), **qa_manifest_fields(rejected_copy)})
                    rows.append(rejected)
            write_csv(paths.generation_manifest, GENERATION_FIELDS, rows)
            proposal = style_readiness_proposal(paths)
            self.assertEqual(proposal["qa_passed_unique_generations"], 4)
            self.assertEqual(proposal["proposal"], "NO_AUTOMATIC_ACTION")

    def test_style_readiness_deduplicates_re_registration_by_evidence_bound_output(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = StylePaths(workspace, "TEST", "TEST", workspace / "TEST_PROJECT_PACK", workspace / "TEST_GENERATIONS")
            paths.pack.mkdir()
            paths.generations.mkdir()
            plan = workspace / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            original = workspace / "original.png"
            re_registered = workspace / "re-registered.png"
            original.write_bytes(b"one-qa-passed-output")
            re_registered.write_bytes(original.read_bytes())
            rows = []
            for image in (original, re_registered):
                row = {field: "" for field in GENERATION_FIELDS}
                row.update(
                    {
                        "status": "TEST",
                        "reference_plan": str(plan),
                        "qa_evidence": write_qa_evidence(image, plan),
                        "style_file": str(image),
                        **qa_manifest_fields(image),
                    }
                )
                rows.append(row)
            write_csv(paths.generation_manifest, GENERATION_FIELDS, rows)
            proposal = style_readiness_proposal(paths)
            self.assertEqual(proposal["qa_passed_unique_generations"], 1)
            self.assertEqual(proposal["proposal"], "NO_AUTOMATIC_ACTION")
