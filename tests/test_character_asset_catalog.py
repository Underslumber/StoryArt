import json
import hashlib
import argparse
import shutil
import unittest
import os
import uuid
from contextlib import redirect_stdout
import io
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tools import style_pack_manager as manager


def _qa_fields(image: Path, request_id: str) -> dict[str, str]:
    """Create a manager-recognized QA receipt for synthetic approved rows."""
    prompt = "Fixture output."
    plan = image.with_suffix(image.suffix + ".fixture-plan.json")
    plan.write_text(json.dumps({"request_id": request_id, "execution_call": {"prompt": {"text": prompt}}}), encoding="utf-8")
    evidence = manager.write_generation_qa_evidence(
        image, plan, stage_id="FINAL",
        qa_required=["ANATOMY_REVIEW", "PROMPT_ADHERENCE", "STYLE", "VISIBLE_DEFECTS"],
        qa_results={"ANATOMY_REVIEW": "PASS", "PROMPT_ADHERENCE": "PASS", "STYLE": "PASS", "VISIBLE_DEFECTS": "PASS"},
        status="TEST",
        visual_review={
            "schema_version": 1, "request_id": request_id, "attempt_id": f"{request_id}-attempt",
            "task_revision": 1, "output_sha256": manager.sha256(image),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "executed_plan_sha256": manager.sha256(plan),
            "author": "synthetic test fixture",
            "anatomy_review": {"status": "PASS", "checked_scope": "Full image at native resolution.", "findings": []},
            "visible_defect_review": {"status": "PASS", "checked_scope": "Full image at native resolution.", "findings": []},
            "prompt_adherence": {"status": "PASS", "checked_scope": "FULL_EXECUTED_PROMPT", "all_explicit_constraints_assessed": True,
                                 "constraints": [{"constraint": prompt, "status": "PASS", "evidence": "Fixture receipt."}]},
        },
    )
    contract = image.with_suffix(image.suffix + ".qa-contract.json")
    plan_snapshot = image.with_suffix(image.suffix + ".qa-plan.json")
    return {"qa_evidence": str(evidence), "qa_output_sha256": manager.sha256(image),
            "qa_receipt_sha256": manager.sha256(evidence), "qa_contract_sha256": manager.sha256(contract),
            "qa_plan_sha256": manager.sha256(plan_snapshot)}


class CharacterAssetCatalogTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(os.environ.get("STORYART_TEST_TMP", os.environ.get("TEMP", ".")))
        self.workspace = temp_root / f"storyart-asset-catalog-{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
        self.paths = manager.make_paths(self.workspace, "RIOT LOL SPLASH")
        self.folder = self.paths.generations / "01_APPROVED_CHARACTERS" / "CHAR_001_Chance"
        self.base = self.folder / "01_APPROVED_BASE" / "assembly.png"
        self.face = self.folder / "03_FACE_REFERENCES" / "face.png"
        self.bodies = [self.folder / "04_BODY_REFERENCES" / f"body-{view}.png" for view in ("front", "side", "back")]
        self.wardrobe = self.folder / "03_CHARACTER_REFERENCES" / "03_WARDROBE" / "suit.png"
        self.accessory = self.folder / "03_CHARACTER_REFERENCES" / "04_ACCESSORIES" / "glasses.png"
        self.face_variant = self.folder / "03_CHARACTER_REFERENCES" / "05_FACE_VARIANTS" / "scar.png"
        self.body_variant = self.folder / "03_CHARACTER_REFERENCES" / "06_BODY_VARIANTS" / "build.png"
        for index, path in enumerate([self.base, self.face, *self.bodies, self.wardrobe, self.accessory, self.face_variant, self.body_variant]):
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), (70 + index, 80, 90)).save(path)
        self.profile = self.folder / "CHARACTER_PROFILE.yaml"
        self.profile.write_text(
            "character_id: CHAR_001\nname: Chance\nstatus: APPROVED\n"
            "identity:\n  gender_identity: man\n  visible_presentation: masculine\n"
            "anatomy_compatibility:\n  target_anatomy: MALE_ANATOMY\n  evidence_source: APPROVED_PROFILE\n"
            'character_face_references:\n  - "03_FACE_REFERENCES/face.png"\n'
            'character_body_references:\n' + "".join(f'  - "04_BODY_REFERENCES/{path.name}"\n' for path in self.bodies)
            + "wardrobe_references: []\naccessory_references: []\n",
            encoding="utf-8",
        )
        manager.write_csv(self.paths.character_registry, manager.CHARACTER_FIELDS, [{
            "character_id": "CHAR_001", "name": "Chance", "approved_base": str(self.base),
            "profile_path": str(self.profile), "face_references": str(self.face),
            "body_references": ";".join(map(str, self.bodies)), "status": "APPROVED",
        }])
        self._generation("base", self.base, "APPROVED_CHARACTER_BASE")

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _generation(self, request_id, image, status, character_id="CHAR_001", with_qa=True):
        qa = _qa_fields(image, request_id) if with_qa else {}
        return manager.append_generation(
            self.paths, request_id=request_id, character_id=character_id, status=status,
            fidelity=90, risk_level="D1", description=request_id, source_image=image,
            archive_file=image, style_file=image, **qa,
        )

    def test_resolver_merges_exact_approved_role_assets_and_reports_stale_profile(self):
        self._generation("wardrobe", self.wardrobe, "APPROVED_WARDROBE")
        self._generation("accessory", self.accessory, "APPROVED_ACCESSORY")
        self._generation("face-variant", self.face_variant, "APPROVED_FACE_VARIANT")
        self._generation("body-variant", self.body_variant, "APPROVED_BODY_VARIANT")
        unclassified = self.folder / "01_VARIATIONS" / "variation.png"
        unclassified.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (201, 30, 40)).save(unclassified)
        self._generation("unclassified", unclassified, "APPROVED_VARIATION")

        with patch.object(manager, "generation_has_passed_qa", return_value=True):
            assets, index = manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)
            roles = {item["role"] for item in assets}
            self.assertEqual(roles, {"WARDROBE", "ACCESSORY", "FACE_VARIANT", "BODY_VARIANT"})
            self.assertIn(str(self.wardrobe.resolve()), index["approved_records_missing_from_profile"]["WARDROBE"])
            self.assertIn("APPROVED_VARIATION", index["unsupported_approved_statuses"])
            self.assertEqual(manager.registered_approved_character_asset(self.paths, self.wardrobe)["asset_role"], "WARDROBE")
            self.assertEqual(manager.registered_approved_character_asset(self.paths, self.accessory)["asset_role"], "ACCESSORY")

            output = io.StringIO()
            with redirect_stdout(output):
                manager.command_resolve_character(type("Args", (), {"workspace": self.workspace, "name": "Chance", "json": True})())
            resolved = json.loads(output.getvalue())
            self.assertEqual(resolved["status"], "FOUND")
            self.assertEqual({row["role"] for row in json.loads(resolved["matches"][0]["role_assets"])}, roles)
            self.assertIn("approved_records_missing_from_profile", json.loads(resolved["matches"][0]["asset_index"]))
            wardrobe = next(row for row in json.loads(resolved["matches"][0]["role_assets"]) if row["role"] == "WARDROBE")
            self.assertEqual(wardrobe["description"], "wardrobe [D1]")

    def test_base_profile_wardrobe_and_accessory_resolve_only_from_exact_profile_paths(self):
        stray = self.folder / "03_CHARACTER_REFERENCES" / "03_WARDROBE" / "unlisted.png"
        Image.new("RGB", (16, 16), (200, 40, 40)).save(stray)
        self.profile.write_text(
            self.profile.read_text(encoding="utf-8")
            .replace("wardrobe_references: []", 'wardrobe_references:\n  - "03_CHARACTER_REFERENCES/03_WARDROBE/suit.png"')
            .replace("accessory_references: []", 'accessory_references:\n  - "03_CHARACTER_REFERENCES/04_ACCESSORIES/glasses.png"'),
            encoding="utf-8",
        )
        with patch.object(manager, "generation_has_passed_qa", return_value=True):
            assets, _ = manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)
            by_role = {item["role"]: item for item in assets}
            self.assertEqual(by_role["WARDROBE"]["generation_id"], manager.read_csv(self.paths.generation_manifest)[0]["generation_id"])
            self.assertEqual(by_role["ACCESSORY"]["status"], "APPROVED_CHARACTER_BASE")
            self.assertEqual(by_role["WARDROBE"]["profile_indexed"], "true")
            self.assertNotIn(str(stray.resolve()), {item["path"] for item in assets})
            self.assertEqual(manager.registered_approved_character_asset(self.paths, self.wardrobe)["asset_role"], "WARDROBE")
            registry_rows = manager.read_csv(self.paths.character_registry)
            registry_rows[0]["profile_path"] = str(self.profile.relative_to(self.paths.generations))
            manager.write_csv(self.paths.character_registry, manager.CHARACTER_FIELDS, registry_rows)
            self.assertEqual(manager.registered_approved_character_asset(self.paths, self.wardrobe)["asset_role"], "WARDROBE")

    def test_registration_excludes_unapproved_wrong_path_and_wrong_role_records(self):
        pending = self.folder / "03_CHARACTER_REFERENCES" / "03_WARDROBE" / "pending.png"
        wrong_path = self.folder / "03_CHARACTER_REFERENCES" / "04_ACCESSORIES" / "wrong.png"
        for path in (pending, wrong_path):
            Image.new("RGB", (16, 16)).save(path)
        self._generation("pending", pending, "PENDING")
        self._generation("wrong-role", wrong_path, "APPROVED_WARDROBE")
        self.assertIsNone(manager.registered_approved_character_asset(self.paths, pending))
        self.assertIsNone(manager.registered_approved_character_asset(self.paths, wrong_path))
        self.assertIsNone(manager.registered_approved_character_asset(self.paths, self.folder / "unregistered.png"))

    def test_profile_role_sync_is_idempotent_and_failures_are_explicit(self):
        manager.sync_character_profile_asset(self.profile, "wardrobe_references", self.wardrobe)
        first = self.profile.read_text(encoding="utf-8")
        manager.sync_character_profile_asset(self.profile, "wardrobe_references", self.wardrobe)
        second = self.profile.read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertEqual(first.count("03_WARDROBE/suit.png"), 1)
        self.assertIn(self.wardrobe.resolve(), manager._character_profile_paths(self.profile, "wardrobe_references"))
        with self.assertRaises(manager.StylePackError):
            manager.sync_character_profile_asset(self.folder / "missing.yaml", "wardrobe_references", self.wardrobe)

    def test_profile_role_sync_converts_inline_yaml_list_without_nested_key(self):
        content = self.profile.read_text(encoding="utf-8").replace(
            "wardrobe_references: []", 'wardrobe_references: ["existing.png"] # keep comment'
        )
        self.profile.write_text(content, encoding="utf-8")
        manager.sync_character_profile_asset(self.profile, "wardrobe_references", self.wardrobe)
        first = self.profile.read_text(encoding="utf-8")
        manager.sync_character_profile_asset(self.profile, "wardrobe_references", self.wardrobe)
        second = self.profile.read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertEqual(first.count("wardrobe_references:"), 1)
        self.assertIn("# keep comment", first)
        self.assertIn('  - "existing.png"', first)
        self.assertEqual(first.count("03_WARDROBE/suit.png"), 1)

    def test_wardrobe_and_accessory_are_distinct_exact_attachment_roles(self):
        self._generation("wardrobe", self.wardrobe, "APPROVED_WARDROBE")
        self._generation("accessory", self.accessory, "APPROVED_ACCESSORY")
        self._generation("face-variant", self.face_variant, "APPROVED_FACE_VARIANT")
        self._generation("body-variant", self.body_variant, "APPROVED_BODY_VARIANT")
        with patch.object(manager, "generation_has_passed_qa", return_value=True):
            clothes = manager.validate_plan_reference(self.paths, str(self.wardrobe), "CLOTHES")
            accessory = manager.validate_plan_reference(self.paths, str(self.accessory), "ACCESSORY")
            face_variant = manager.validate_plan_reference(self.paths, str(self.face_variant), "FACE")
            body_variant = manager.validate_plan_reference(self.paths, str(self.body_variant), "BODY")
        plan = manager.build_attachment_plan({
            "clothes": clothes, "accessory": [accessory],
            "face_variant": [face_variant], "body_variant": [body_variant],
        }, [], 5)
        roles_by_path = {row["path"]: row["active_roles"] for row in plan}
        self.assertEqual(roles_by_path[str(self.wardrobe.resolve())], ["CLOTHES"])
        self.assertEqual(roles_by_path[str(self.accessory.resolve())], ["ACCESSORY"])
        self.assertEqual(roles_by_path[str(self.face_variant.resolve())], ["FACE_VARIANT"])
        self.assertEqual(roles_by_path[str(self.body_variant.resolve())], ["BODY_VARIANT"])
        self.assertNotEqual(clothes["path"], accessory["path"])

    def test_test_and_pending_assets_never_enter_approved_catalog(self):
        test_asset = self.folder / "03_CHARACTER_REFERENCES" / "03_WARDROBE" / "test.png"
        pending_asset = self.folder / "03_CHARACTER_REFERENCES" / "04_ACCESSORIES" / "pending.png"
        for path in (test_asset, pending_asset):
            Image.new("RGB", (16, 16)).save(path)
        self._generation("test", test_asset, "TEST")
        self._generation("pending", pending_asset, "PENDING")
        assets, _ = manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)
        approved_paths = {item["path"] for item in assets}
        self.assertNotIn(str(test_asset.resolve()), approved_paths)
        self.assertNotIn(str(pending_asset.resolve()), approved_paths)

    def test_missing_file_for_approved_manifest_row_is_reported_and_never_registered(self):
        self._generation("missing-wardrobe", self.wardrobe, "APPROVED_WARDROBE")
        self.wardrobe.unlink()
        assets, index = manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)
        self.assertNotIn(str(self.wardrobe.resolve()), {item["path"] for item in assets})
        diagnostic = next(item for item in index["invalid_approved_records"] if item["path"] == str(self.wardrobe.resolve()))
        self.assertIn("missing", diagnostic["reason"].lower())
        self.assertIsNone(manager.registered_approved_character_asset(self.paths, self.wardrobe))

    def test_role_rows_without_valid_qa_receipt_are_diagnosed_and_excluded(self):
        self._generation("qa-less", self.wardrobe, "APPROVED_WARDROBE", with_qa=False)
        self._generation("bad-receipt", self.accessory, "APPROVED_ACCESSORY")
        rows = manager.read_csv(self.paths.generation_manifest)
        accessory_row = next(row for row in rows if row["request_id"] == "bad-receipt")
        accessory_row["qa_evidence"] = str(self.accessory.with_suffix(".png.qa-evidence.json"))
        accessory_row["qa_output_sha256"] = manager.sha256(self.accessory)
        accessory_row["qa_receipt_sha256"] = "0" * 64
        manager.write_csv(self.paths.generation_manifest, manager.GENERATION_FIELDS, rows)
        assets, index = manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)
        self.assertEqual(assets, [])
        reasons = {Path(item["path"]).name: item["reason"] for item in index["invalid_approved_records"]}
        self.assertIn("QA receipt", reasons["suit.png"])
        self.assertIn("QA receipt", reasons["glasses.png"])

    def test_role_row_output_hash_mismatch_after_registration_is_diagnostic(self):
        self._generation("mutated-after-approval", self.wardrobe, "APPROVED_WARDROBE")
        rows = manager.read_csv(self.paths.generation_manifest)
        row = next(item for item in rows if item["request_id"] == "mutated-after-approval")
        row["qa_output_sha256"] = manager.sha256(self.wardrobe)
        manager.write_csv(self.paths.generation_manifest, manager.GENERATION_FIELDS, rows)
        Image.new("RGB", (16, 16), (251, 17, 80)).save(self.wardrobe)
        with patch.object(manager, "generation_has_passed_qa", return_value=True):
            assets, index = manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)
        self.assertNotIn(str(self.wardrobe.resolve()), {item["path"] for item in assets})
        diagnostic = next(item for item in index["invalid_approved_records"] if item["path"] == str(self.wardrobe.resolve()))
        self.assertIn("hash", diagnostic["reason"].lower())

    def test_later_rejected_per_file_record_revokes_base_profile_fallback(self):
        self.profile.write_text(
            self.profile.read_text(encoding="utf-8")
            .replace("wardrobe_references: []", 'wardrobe_references:\n  - "03_CHARACTER_REFERENCES/03_WARDROBE/suit.png"')
            .replace("accessory_references: []", 'accessory_references:\n  - "03_CHARACTER_REFERENCES/04_ACCESSORIES/glasses.png"'),
            encoding="utf-8",
        )
        self._generation("later-rejected-wardrobe", self.wardrobe, "REJECTED")
        with patch.object(manager, "generation_has_passed_qa", return_value=True):
            assets, index = manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)
        by_role = {item["role"]: item for item in assets}
        self.assertNotIn("WARDROBE", by_role)
        self.assertEqual(by_role["ACCESSORY"]["status"], "APPROVED_CHARACTER_BASE")
        self.assertTrue(any("revokes" in item["reason"] for item in index["invalid_approved_records"]))

    def test_accessory_uses_clothes_visual_review_category_not_face(self):
        record = {"path": str(self.accessory.resolve()), "sha256": manager.sha256(self.accessory)}
        selected = {"accessory": [record]}
        roles = manager.active_roles_by_selection_key(existing_scene=True, character_free_scene=False)
        self.assertEqual(roles["accessory"], ["CLOTHES"])
        def attestation(role):
            return json.dumps({
                "role": role, "slot_role": "ACCESSORY", "path": record["path"],
                "view": "FULL_RESOLUTION", "outcome": "PASS", "applicability": "scene accessory",
                "findings": "clear accessory shape", "limitations": "none",
            })
        reviewed = manager.parse_selected_source_reviews([attestation("CLOTHES")], selected, roles, [])
        self.assertEqual(reviewed[0]["role"], "CLOTHES")
        with self.assertRaises(manager.StylePackError):
            manager.parse_selected_source_reviews([attestation("FACE")], selected, roles, [])

    def test_multistage_plan_propagates_variants_and_accessories_with_conditions(self):
        selected = {
            "style": [{"path": str(self.base), "sha256": manager.sha256(self.base)}],
            "primary_face": {"path": str(self.face), "sha256": manager.sha256(self.face)},
            "body": {"path": str(self.bodies[0]), "sha256": manager.sha256(self.bodies[0])},
            "clothes": {"path": str(self.wardrobe), "sha256": manager.sha256(self.wardrobe)},
            "accessory": [{"path": str(self.accessory), "sha256": manager.sha256(self.accessory)}],
            "face_variant": [{"path": str(self.face_variant), "sha256": manager.sha256(self.face_variant)}],
            "body_variant": [{"path": str(self.body_variant), "sha256": manager.sha256(self.body_variant)}],
        }
        stages = manager.build_multistage_attachment_plan(selected, [], 5)
        by_id = {stage["stage_id"]: stage for stage in stages}
        def roles(stage_id):
            return {role for slot in by_id[stage_id]["slots"] for role in slot["active_roles"]}
        self.assertIn("FACE_VARIANT", roles("01_FACE_IDENTITY"))
        self.assertIn("BODY_VARIANT", roles("02_BODY_POSE"))
        self.assertIn("ACCESSORY", roles("03_CLOTHING"))
        self.assertIn("CLOTHES", roles("03_CLOTHING"))
        for stage_id in ("01_FACE_IDENTITY", "04_CHARACTER_COMPOSITE", "05_FINAL_SCENE"):
            self.assertIn("FACE_VARIANT", by_id[stage_id]["purpose"])
        for stage_id in ("02_BODY_POSE", "03_CLOTHING", "04_CHARACTER_COMPOSITE", "05_FINAL_SCENE"):
            self.assertIn("BODY_VARIANT", by_id[stage_id]["purpose"])
        for stage_id in ("03_CLOTHING", "04_CHARACTER_COMPOSITE", "05_FINAL_SCENE"):
            self.assertIn("ACCESSORY", by_id[stage_id]["purpose"])

    def test_optional_variants_cannot_fill_canonical_face_or_body_slots(self):
        self._generation("face-variant", self.face_variant, "APPROVED_FACE_VARIANT")
        self._generation("body-variant", self.body_variant, "APPROVED_BODY_VARIANT")
        with patch.object(manager, "generation_has_passed_qa", return_value=True):
            face_variant = manager.validate_plan_reference(self.paths, str(self.face_variant), "FACE")
            body_variant = manager.validate_plan_reference(self.paths, str(self.body_variant), "BODY")
            with self.assertRaises(manager.StylePackError):
                manager.require_approved_character_asset_role(self.paths, Path(face_variant["path"]), "CHAR_001", "FACE", "CHARACTER_FACE")
            with self.assertRaises(manager.StylePackError):
                manager.require_approved_character_asset_role(self.paths, Path(body_variant["path"]), "CHAR_001", "BODY", "NEAREST_CHARACTER_BODY")

    def test_approve_variation_registers_then_syncs_each_supported_profile_role(self):
        source_image = self.workspace / "qa-passed-source.png"
        Image.new("RGB", (16, 16), (9, 88, 170)).save(source_image)
        source_plan = self.workspace / "source-plan.json"
        source_plan.write_text('{"character_id":"CHAR_001"}', encoding="utf-8")
        events = []
        append_generation = manager.append_generation
        field_by_kind = {
            "wardrobe": "wardrobe_references", "accessory": "accessory_references",
            "face": "face_variant_references", "body": "body_variant_references",
        }
        for kind, field in field_by_kind.items():
            args = argparse.Namespace(
                workspace=self.workspace, style_name="RIOT LOL SPLASH", user_approved=True,
                fidelity=90, image=str(source_image), character_id="CHAR_001",
                request_id=f"approve-{kind}", risk_level="D1", description=f"Approved {kind}",
                parent_generation="", notes="confirmed", kind=kind,
            )
            events.clear()
            def register(*positional, **kwargs):
                qa = _qa_fields(Path(kwargs["style_file"]), kwargs["request_id"])
                generation_id = append_generation(*positional, **{**kwargs, **qa})
                events.append("registered")
                return generation_id
            with patch.object(manager, "ensure_generation_library"), patch.object(manager, "require_qa_passed_generation_for_approval", return_value={
                "character_id": "CHAR_001", "reference_plan": str(source_plan), "generation_id": "SOURCE_GEN",
            }), patch.object(manager, "ensure_generation_archived", return_value=self.workspace / "archive.png"), \
                 patch.object(manager, "approval_provenance", return_value=("SOURCE_GEN", str(source_plan), "qa.json", {}, "notes")), \
                 patch.object(manager, "append_generation", side_effect=register), \
                 patch.object(manager, "sync_character_profile_asset", side_effect=lambda profile, role_field, asset: events.append(("synced", role_field, asset))):
                with redirect_stdout(io.StringIO()):
                    manager.command_approve_variation(args)
            self.assertEqual(events[0], "registered")
            self.assertEqual(events[1][0:2], ("synced", field))


if __name__ == "__main__":
    unittest.main()
