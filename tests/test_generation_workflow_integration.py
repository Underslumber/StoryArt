"""Public CLI workflow integration using synthetic images and no generator calls."""

from __future__ import annotations

import hashlib
import json
import csv
from argparse import Namespace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import shutil

from PIL import Image
import yaml


ROOT = Path(__file__).resolve().parents[1]


def review_declaration(role: str, slot_role: str, path: Path | str, *, outcome: str = "PASS") -> str:
    return json.dumps({
        "role": role,
        "slot_role": slot_role,
        "path": str(path),
        "view": "FULL_RESOLUTION",
        "outcome": outcome,
        "applicability": f"{role.lower()} evidence for the selected {slot_role} slot",
        "findings": "The selected source visibly supports its declared role.",
        "limitations": "Only the declared role was reviewed; other roles remain unassessed.",
    })


class GenerationWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        if self._testMethodName == "test_assembly_plus_face_uses_assembly_as_the_only_body_reference":
            self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        else:
            self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.style_name = "INTEGRATION"
        self.style_pack = self.workspace / f"{self.style_name}_PROJECT_PACK"
        self.generations = self.workspace / f"{self.style_name}_GENERATIONS"
        self._cli("style_pack_manager.py", "init", "--workspace", self.workspace, "--style-name", self.style_name)
        metadata_path = self.style_pack / ".style-pack.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["status"] = "FINALIZED_APPROVED"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self.style_reference = self._classify("style.png", "ANCHOR_STYLE", (24, 28, 36))
        self.primary_face = self._classify("primary-face.png", "CHARACTER_FACE", (175, 115, 105))
        self.supporting_face = self._classify("supporting-face.png", "CHARACTER_FACE", (165, 108, 98))

    def _run(self, script: str, *args: object, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools" / script), *(str(arg) for arg in args)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def _cli(self, script: str, *args: object, succeeds: bool = True):
        return self._run(script, *args, succeeds=succeeds)

    def _classify(self, filename: str, role: str, color: tuple[int, int, int], *, subject_label: str = "", copy_from: Path | None = None) -> Path:
        source = self.workspace / filename
        if copy_from:
            shutil.copyfile(copy_from, source)
        else:
            Image.new("RGB", (96, 128), color).save(source)
        result = self._cli(
            "style_pack_manager.py", "classify", "--workspace", self.workspace,
            "--style-name", self.style_name, "--file", source, "--role", role,
            "--status", "ANCHOR" if role == "ANCHOR_STYLE" else "TEST",
            *( ("--user-approved",) if role == "ANCHOR_STYLE" else () ),
            *( ("--subject-label", subject_label, "--subject-label-evidence", "Synthetic integration metadata fixture.") if subject_label else () ),
        )
        stored = next(line.removeprefix("FILE=") for line in result.stdout.splitlines() if line.startswith("FILE="))
        # These fixtures are plain color fields with no depicted subjects.
        manifest = self.style_pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "LOGS" / "PRELIMINARY_REFERENCE_MANIFEST.csv"
        with manifest.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = list(reader.fieldnames)
            rows = list(reader)
        labels = {
            "subject_label": subject_label or "NO_SUBJECT",
            "subject_label_evidence": "SYNTHETIC_SUBJECT_LABEL_FIXTURE" if subject_label else "SYNTHETIC_PLAIN_COLOR_FIELD",
            "subject_label_sha256": hashlib.sha256(Path(stored).read_bytes()).hexdigest(),
            "subject_label_status": "REVIEWED",
        }
        fields.extend(key for key in labels if key not in fields)
        for row in rows:
            if row.get("stored_relative_path") == Path(stored).relative_to(self.style_pack).as_posix():
                row.update(labels)
        with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return Path(stored)

    def _guard_path(self, request_id: str) -> Path:
        return self.generations / "00_PENDING" / request_id / "EXECUTION_GUARD.json"

    def _user_selections_file(self, request_id: str, plan_path: Path) -> Path:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        character_id = str(plan.get("character_id", "NONE")).upper()
        selections = {
            "style": {
                "choice": f"PROJECT_STYLE:{self.style_name}",
                "user_quote": f"Use project style {self.style_name} for this synthetic test.",
            },
            "reference_policy": {
                "choice": "APPROVED_CHARACTER_REFERENCES" if character_id.startswith("CHAR_") else "USER_ATTACHED_REFERENCES" if character_id == "NEW" else "PROJECT_STYLE_ONLY",
                "user_quote": "Use the selected synthetic test references.",
            },
            "character": {
                "choice": character_id,
                "user_quote": f"Selected character identity for the synthetic test: {character_id}.",
            },
        }
        path = self.workspace / f"{request_id}-user-selections.json"
        path.write_text(json.dumps(selections, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _user_reference_evidence_file(self, message_id: str) -> Path:
        rows = []
        for stored in (self.primary_face, self.supporting_face):
            upload = self.workspace / "user-attachments" / stored.name
            upload.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stored, upload)
            rows.append({
                "path": str(stored), "sha256": hashlib.sha256(stored.read_bytes()).hexdigest(),
                "source_path": str(upload), "chat_id": "integration-chat",
                "message_id": message_id,
            })
        path = self.workspace / f"user-reference-evidence-{message_id}.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        return path

    def test_approved_canonical_character_assets_are_positive_references(self):
        from tools import style_pack_manager as manager

        paths = manager.make_paths(self.workspace, self.style_name)
        folder = paths.generations / "01_APPROVED_CHARACTERS" / "CHAR_001_Chance"
        base_dir = folder / "01_APPROVED_BASE"
        face_dir = folder / "03_FACE_REFERENCES"
        body_dir = folder / "04_BODY_REFERENCES"
        for directory in (base_dir, face_dir, body_dir):
            directory.mkdir(parents=True)
        base, face, body = (base_dir / "CHARACTER_ASSEMBLY_USER_SELECTED.png", face_dir / "face.png", body_dir / "body.png")
        unlisted = folder / "unlisted.png"
        for index, image in enumerate((base, face, body, unlisted)):
            Image.new("RGB", (32, 48), (80 + index, 100, 120)).save(image)
        profile = folder / "CHARACTER_PROFILE.yaml"
        profile.write_text(
            "character_id: CHAR_001\nname: Chance\nstatus: APPROVED\n"
            "identity:\n  gender_identity: man\n  visible_presentation: masculine\n"
            "anatomy_compatibility:\n  target_anatomy: MALE_ANATOMY\n  evidence_source: APPROVED_PROFILE\n"
            'character_face_references:\n  - "03_FACE_REFERENCES/face.png"\n'
            'character_body_references:\n  - "04_BODY_REFERENCES/body.png"\n',
            encoding="utf-8",
        )
        manager.write_csv(paths.character_registry, manager.CHARACTER_FIELDS, [{
            "character_id": "CHAR_001", "name": "Chance", "approved_base": str(base),
            "profile_path": str(profile), "face_references": str(face), "body_references": str(body),
            "status": "APPROVED",
        }])
        manager.append_generation(
            paths, request_id="canonical-character", character_id="CHAR_001",
            status="APPROVED_CHARACTER_BASE", fidelity=70, risk_level="D1",
            description="Approved canonical character base", source_image=base,
            archive_file=base, style_file=base,
        )

        self.assertEqual(manager.validate_plan_reference(paths, str(base), "FACE")["status"], "APPROVED_CHARACTER_ASSET")
        self.assertEqual(manager.validate_plan_reference(paths, str(face), "FACE")["status"], "APPROVED_CHARACTER_ASSET")
        self.assertEqual(manager.validate_plan_reference(paths, str(body), "BODY")["status"], "APPROVED_CHARACTER_ASSET")
        target = manager.compatibility_target(paths, type("Args", (), {"character_id": "CHAR_001"})())
        self.assertEqual(target["target_anatomy"], "MALE_ANATOMY")
        face_metadata = manager.style_reference_compatibility_metadata(paths, face)
        body_metadata = manager.style_reference_compatibility_metadata(paths, body)
        assembly_metadata = manager.style_reference_compatibility_metadata(paths, base)
        self.assertTrue(manager.enforce_reference_compatibility(target, face_metadata, ["FACE"])["compatible"])
        self.assertTrue(manager.enforce_reference_compatibility(target, body_metadata, ["BODY_BUILD"])["compatible"])
        self.assertTrue(manager.enforce_reference_compatibility(target, assembly_metadata, ["BODY_BUILD"])["compatible"])

        def rejected(candidate: Path):
            with self.assertRaises(manager.StylePackError):
                manager.validate_plan_reference(paths, str(candidate), "FACE")

        rejected(unlisted)
        rejected(self.generations / "ordinary-unregistered.png")

        original_registry = manager.read_csv(paths.character_registry)[0]
        row = dict(original_registry, status="REVIEW")
        manager.write_csv(paths.character_registry, manager.CHARACTER_FIELDS, [row])
        rejected(base)
        manager.write_csv(paths.character_registry, manager.CHARACTER_FIELDS, [original_registry])

        profile.write_text(profile.read_text(encoding="utf-8").replace("status: APPROVED", "status: REVIEW"), encoding="utf-8")
        rejected(base)
        profile.write_text(profile.read_text(encoding="utf-8").replace("status: REVIEW", "status: APPROVED"), encoding="utf-8")

        manifest_rows = manager.read_csv(paths.generation_manifest)
        manifest_rows[-1]["style_file"] = str(face)
        manager.write_csv(paths.generation_manifest, manager.GENERATION_FIELDS, manifest_rows)
        rejected(base)

    def test_typed_item_approval_flows_through_scene_prepare_and_guard_validation(self):
        from tools import style_pack_manager as manager

        request_id = "typed-item-lifecycle"
        guard = self._start_request(request_id)
        paths = manager.make_paths(self.workspace, self.style_name)
        character_folder = paths.generations / "01_APPROVED_CHARACTERS" / "CHAR_007_Forge"
        base_dir = character_folder / "01_APPROVED_BASE"
        base_dir.mkdir(parents=True)
        assembly = base_dir / "CHARACTER_ASSEMBLY_USER_SELECTED.png"
        Image.new("RGB", (48, 72), (110, 90, 70)).save(assembly)
        profile = character_folder / "CHARACTER_PROFILE.yaml"
        profile.write_text(
            "character_id: CHAR_007\nname: Forge\nstatus: APPROVED\n"
            "identity:\n  gender_identity: man\n  visible_presentation: masculine\n"
            "anatomy_compatibility:\n  target_anatomy: MALE_ANATOMY\n  evidence_source: APPROVED_PROFILE\n",
            encoding="utf-8",
        )
        manager.write_csv(paths.character_registry, manager.CHARACTER_FIELDS, [{
            "character_id": "CHAR_007", "name": "Forge", "approved_base": str(assembly),
            "profile_path": str(profile), "status": "APPROVED",
        }])
        manager.append_generation(
            paths, request_id="typed-item-character", character_id="CHAR_007",
            status="APPROVED_CHARACTER_BASE", fidelity=70, risk_level="D1",
            description="Synthetic approved character assembly", source_image=assembly,
            archive_file=assembly, style_file=assembly,
        )

        request_dir = guard.parent
        plan_path = request_dir / "TYPED_ITEM_PLAN.json"
        self._cli(
            "item_reference_manager.py", "create-plan", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", request_id, "--output", plan_path,
            "--character-id", "CHAR_007", "--item-id", "ITEM_FORGE_SWORD", "--item-kind", "CLOTHING",
            "--name", "Forge sword", "--version", "v1", "--design-spec", "single-edged steel sword with a plain crossguard",
            "--dimensions", "blade 1.0 m; grip 0.2 m", "--material", "steel and leather",
            "--construction", "forged blade with wrapped grip", "--attachment-constraints", "may rest beside character",
        )
        sheet = request_dir / "item-sheet.png"
        design = request_dir / "item-design.png"
        scale = request_dir / "item-scale.png"
        for image, color in ((sheet, (20, 20, 20)), (design, (180, 180, 180)), (scale, (100, 120, 140))):
            Image.new("RGB", (40, 40), color).save(image)
        digest = lambda image: hashlib.sha256(image.read_bytes()).hexdigest()
        qa = {
            "checks": {name: {"status": "PASS", "evidence": f"synthetic review of {name}"} for name in (
                "ITEM_DESIGN", "ITEM_VERSION", "ITEM_DIMENSIONS", "ITEM_MATERIAL_CONSTRUCTION", "ITEM_CHARACTER_SCALE",
                "CLOTHING_FIT", "LAYER_ORDER", "CONTACT_CONSTRAINTS",
            )},
            "item_design_source": {
                "sha256": digest(design), "subject_label": "NO_SUBJECT",
                "subject_label_evidence": "Reviewed isolated item crop.",
                "source_sheet_path": str(sheet), "source_sheet_sha256": digest(sheet),
            },
            "item_scale_fit_source": {
                "sha256": digest(scale), "character_id": "CHAR_007",
                "character_assembly_sha256": digest(assembly), "subject_label": "MALE",
                "subject_label_sha256": digest(scale), "subject_label_evidence": "Reviewed male canonical character in scale view.",
                "anatomy_compatibility": "MALE_ANATOMY", "anatomy_evidence_source": "APPROVED_PROFILE",
                "source_sheet_path": str(sheet), "source_sheet_sha256": digest(sheet),
            },
        }
        qa_path = request_dir / "item-qa.json"
        qa_path.write_text(json.dumps(qa), encoding="utf-8")
        approved_text = self._cli(
            "item_reference_manager.py", "approve", "--workspace", self.workspace,
            "--item-plan", plan_path, "--design-image", design, "--scale-fit-image", scale,
            "--qa-json", qa_path, "--approval-quote", "Approve Forge sword v1 for integration.", "--user-approved",
        ).stdout
        manifest = Path(json.loads(approved_text)["manifest_path"])

        _guard, reference_plan = self._prepare_scene(
            request_id, start_guard=False, character_id="CHAR_007", item_reference=(manifest,),
        )
        call = self._resolve_and_prepare_call(
            request_id, guard, reference_plan, "Create a synthetic scene showing Forge with the approved sword nearby."
        )
        item_roles = {role for slot in call["slots"] for role in slot["active_roles"]
                      if role in {"ITEM_DESIGN", "ITEM_SCALE_FIT"}}
        self.assertEqual(item_roles, {"ITEM_DESIGN", "ITEM_SCALE_FIT"})
        attempt_id = self._start_call(guard, reference_plan, call)
        output = self.workspace / "typed-item-single-pass.png"
        Image.new("RGB", (120, 180), (30, 40, 50)).save(output)
        review_args = self._visual_review_args(guard, attempt_id, output)
        semantic_qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-face", "PASS", "--qa-body-silhouette", "PASS", "--qa-body-proportions", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-clothing", "PASS",
            "--qa-pose-contacts", "PASS", "--qa-objects-action", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS",
        )
        record_base = (
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", output, "--request-id", request_id,
            "--description", "Synthetic typed clothing scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "CHAR_007", "--attempt-id", attempt_id, "--reference-plan", reference_plan,
        )
        missing_item_qa = self._cli(*record_base, *review_args, *semantic_qa, succeeds=False)
        self.assertIn("Required post-generation QA was not performed", missing_item_qa.stderr)
        self.assertIn("CLOTHING_FIT", missing_item_qa.stderr)
        item_qa = (
            "--qa-item-design", "PASS", "--qa-item-version", "PASS", "--qa-item-dimensions", "PASS",
            "--qa-item-material-construction", "PASS", "--qa-item-character-scale", "PASS",
            "--qa-clothing-fit", "PASS", "--qa-layer-order", "PASS", "--qa-contact-constraints", "PASS",
        )
        recorded = self._cli(*record_base, *review_args, *semantic_qa, *item_qa)
        self.assertIn("STATUS=TEST", recorded.stdout)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as stream:
            row = next(row for row in csv.DictReader(stream) if row["request_id"] == request_id)
        receipt = manager.generation_qa_evidence(row)
        self.assertIsNotNone(receipt)
        qa_contract = json.loads(Path(receipt["qa_contract"]).read_text(encoding="utf-8"))
        for layer in ("ITEM_DESIGN", "ITEM_VERSION", "ITEM_DIMENSIONS", "ITEM_MATERIAL_CONSTRUCTION",
                      "ITEM_CHARACTER_SCALE", "CLOTHING_FIT", "LAYER_ORDER", "CONTACT_CONSTRAINTS"):
            self.assertEqual(qa_contract["qa_results"][layer], "PASS")

        multi_request = "typed-item-multistage-final"
        multi_guard = self._start_request(multi_request, required_stages=("05_FINAL_SCENE",))
        _guard, multi_plan_path = self._prepare_scene(
            multi_request, start_guard=False, character_id="CHAR_007", item_reference=(manifest,),
            reference_workflow="MULTI_STAGE",
        )
        multi_plan = json.loads(multi_plan_path.read_text(encoding="utf-8"))
        single_pass_plan = json.loads(reference_plan.read_text(encoding="utf-8"))
        final_stage = next(stage for stage in multi_plan["generation_workflow"]["stages"]
                           if stage["stage_id"] == "05_FINAL_SCENE")
        final_stage["slots"] = single_pass_plan["generation_workflow"]["slots"]
        final_stage["attachments_used"] = len(final_stage["slots"])
        multi_plan["generation_workflow"]["stages"] = [final_stage]
        multi_plan_path.write_text(json.dumps(multi_plan, indent=2), encoding="utf-8")
        multi_prompt = "Create a synthetic final scene showing Forge with the approved sword nearby."
        final_call = self._resolve_and_prepare_call(
            multi_request, multi_guard, multi_plan_path, multi_prompt, "05_FINAL_SCENE",
        )
        final_attempt = self._start_call(multi_guard, multi_plan_path, final_call)
        final_output = self.workspace / "typed-item-multistage-final.png"
        Image.new("RGB", (120, 180), (35, 45, 55)).save(final_output)
        final_review = self._visual_review_args(multi_guard, final_attempt, final_output)
        final_semantic_qa = (*semantic_qa, "--qa-lighting", "PASS")
        final_record_base = (
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", final_output, "--request-id", multi_request,
            "--description", "Synthetic typed multi-stage final scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "CHAR_007", "--attempt-id", final_attempt,
            "--reference-plan", multi_plan_path, "--stage-id", "05_FINAL_SCENE",
        )
        final_missing = self._cli(
            *final_record_base, *final_review, *final_semantic_qa, succeeds=False,
        )
        self.assertIn("Required post-generation QA was not performed", final_missing.stderr)
        self.assertIn("CLOTHING_FIT", final_missing.stderr)
        final_recorded = self._cli(
            *final_record_base, *final_review, *final_semantic_qa, *item_qa,
        )
        self.assertIn("STATUS=TEST", final_recorded.stdout)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as stream:
            final_row = [row for row in csv.DictReader(stream) if row["request_id"] == multi_request][-1]
        final_receipt = manager.generation_qa_evidence(final_row)
        self.assertIsNotNone(final_receipt)
        final_contract = json.loads(Path(final_receipt["qa_contract"]).read_text(encoding="utf-8"))
        self.assertTrue({"ITEM_DESIGN", "ITEM_VERSION", "ITEM_DIMENSIONS", "ITEM_MATERIAL_CONSTRUCTION",
                         "ITEM_CHARACTER_SCALE", "CLOTHING_FIT", "LAYER_ORDER", "CONTACT_CONSTRAINTS"}
                        .issubset(final_contract["expected_qa_layers"]))
        self.assertTrue(all(final_contract["qa_results"][layer] == "PASS"
                            for layer in ("ITEM_DESIGN", "ITEM_VERSION", "ITEM_DIMENSIONS",
                                          "ITEM_MATERIAL_CONSTRUCTION", "ITEM_CHARACTER_SCALE",
                                          "CLOTHING_FIT", "LAYER_ORDER", "CONTACT_CONSTRAINTS")))

        fail_request = "typed-item-multistage-final-fail"
        fail_guard = self._start_request(fail_request, required_stages=("05_FINAL_SCENE",))
        _guard, fail_plan_path = self._prepare_scene(
            fail_request, start_guard=False, character_id="CHAR_007", item_reference=(manifest,),
            reference_workflow="MULTI_STAGE",
        )
        fail_plan = json.loads(fail_plan_path.read_text(encoding="utf-8"))
        fail_stage = next(stage for stage in fail_plan["generation_workflow"]["stages"]
                          if stage["stage_id"] == "05_FINAL_SCENE")
        fail_stage["slots"] = single_pass_plan["generation_workflow"]["slots"]
        fail_stage["attachments_used"] = len(fail_stage["slots"])
        fail_plan["generation_workflow"]["stages"] = [fail_stage]
        fail_plan_path.write_text(json.dumps(fail_plan, indent=2), encoding="utf-8")
        fail_call = self._resolve_and_prepare_call(
            fail_request, fail_guard, fail_plan_path,
            "Create a synthetic final scene showing Forge with the approved sword nearby.", "05_FINAL_SCENE",
        )
        fail_attempt = self._start_call(fail_guard, fail_plan_path, fail_call)
        fail_output = self.workspace / "typed-item-multistage-final-fail.png"
        Image.new("RGB", (120, 180), (45, 40, 35)).save(fail_output)
        fail_recorded = self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", fail_output, "--request-id", fail_request,
            "--description", "Synthetic typed multi-stage final scene with failed fit QA",
            "--fidelity", "70", "--status", "TEST", "--character-id", "CHAR_007",
            "--attempt-id", fail_attempt, "--reference-plan", fail_plan_path,
            "--stage-id", "05_FINAL_SCENE",
            *self._visual_review_args(fail_guard, fail_attempt, fail_output), *final_semantic_qa,
            "--qa-item-design", "PASS", "--qa-item-version", "PASS", "--qa-item-dimensions", "PASS",
            "--qa-item-material-construction", "PASS", "--qa-item-character-scale", "PASS",
            "--qa-clothing-fit", "FAIL", "--qa-layer-order", "PASS", "--qa-contact-constraints", "PASS",
        )
        self.assertIn("STATUS=REJECTED", fail_recorded.stdout)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as stream:
            fail_row = [row for row in csv.DictReader(stream) if row["request_id"] == fail_request][-1]
        fail_receipt = manager.generation_qa_evidence(fail_row)
        self.assertIsNotNone(fail_receipt)
        fail_contract = json.loads(Path(fail_receipt["qa_contract"]).read_text(encoding="utf-8"))
        self.assertEqual(fail_contract["qa_results"]["CLOTHING_FIT"], "FAIL")

        plan = json.loads(reference_plan.read_text(encoding="utf-8"))
        plan["selected_references"]["item_scale_fit"] = []
        with self.assertRaisesRegex(manager.StylePackError, "no selected approved-manifest binding|complete ITEM_DESIGN"):
            manager.resolve_call_slots(paths, reference_plan, plan, "SINGLE_PASS")
        design_path = Path(json.loads(manifest.read_text(encoding="utf-8"))["item_design"]["path"])
        design_path.write_bytes(b"tampered approved item source")
        self._cli("item_reference_manager.py", "validate", "--workspace", self.workspace,
                  "--manifest", manifest, succeeds=False)

    def _register_synthetic_item_character(self, request_id: str) -> tuple[object, Path]:
        from tools import style_pack_manager as manager

        paths = manager.make_paths(self.workspace, self.style_name)
        folder = paths.generations / "01_APPROVED_CHARACTERS" / "CHAR_007_Forge"
        base = folder / "01_APPROVED_BASE"
        base.mkdir(parents=True)
        assembly = base / "CHARACTER_ASSEMBLY_USER_SELECTED.png"
        Image.new("RGB", (48, 72), (110, 90, 70)).save(assembly)
        profile = folder / "CHARACTER_PROFILE.yaml"
        profile.write_text(
            "character_id: CHAR_007\nname: Forge\nstatus: APPROVED\n"
            "identity:\n  gender_identity: man\n  visible_presentation: masculine\n"
            "anatomy_compatibility:\n  target_anatomy: MALE_ANATOMY\n  evidence_source: APPROVED_PROFILE\n",
            encoding="utf-8",
        )
        manager.write_csv(paths.character_registry, manager.CHARACTER_FIELDS, [{
            "character_id": "CHAR_007", "name": "Forge", "approved_base": str(assembly),
            "profile_path": str(profile), "status": "APPROVED",
        }])
        manager.append_generation(
            paths, request_id=f"{request_id}-character", character_id="CHAR_007",
            status="APPROVED_CHARACTER_BASE", fidelity=70, risk_level="D1",
            description="Synthetic approved character assembly", source_image=assembly,
            archive_file=assembly, style_file=assembly,
        )
        return paths, assembly

    def _approve_synthetic_typed_item(self, request_id: str, assembly: Path, kind: str) -> Path:
        plan_path = self._guard_path(request_id).parent / "ITEM_REFERENCE_PLAN.json"
        self._cli(
            "item_reference_manager.py", "create-plan", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", request_id, "--output", plan_path,
            "--character-id", "CHAR_007", "--item-id", f"ITEM_FORGE_{kind}", "--item-kind", kind,
            "--name", f"Forge {kind.lower()}", "--version", "v1",
            "--design-spec", "synthetic item with exact approved construction and silhouette",
            "--dimensions", "height 0.8 m; width 0.3 m", "--material", "steel and cloth",
            "--construction", "layered synthetic construction", "--attachment-constraints", "may rest beside character",
        )
        sheet = self._guard_path(request_id).parent / f"{kind}-sheet.png"
        design = self._guard_path(request_id).parent / f"{kind}-design.png"
        scale = self._guard_path(request_id).parent / f"{kind}-scale.png"
        for image, color in ((sheet, (20, 20, 20)), (design, (180, 180, 180)), (scale, (100, 120, 140))):
            Image.new("RGB", (40, 40), color).save(image)
        digest = lambda image: hashlib.sha256(image.read_bytes()).hexdigest()
        qa_names = ("ITEM_DESIGN", "ITEM_VERSION", "ITEM_DIMENSIONS", "ITEM_MATERIAL_CONSTRUCTION", "ITEM_CHARACTER_SCALE")
        if kind == "CLOTHING":
            qa_names += ("CLOTHING_FIT", "LAYER_ORDER", "CONTACT_CONSTRAINTS")
        qa = {
            "checks": {name: {"status": "PASS", "evidence": f"synthetic review of {name}"} for name in qa_names},
            "item_design_source": {
                "sha256": digest(design), "subject_label": "NO_SUBJECT",
                "subject_label_evidence": "Reviewed isolated item crop.",
                "source_sheet_path": str(sheet), "source_sheet_sha256": digest(sheet),
            },
            "item_scale_fit_source": {
                "sha256": digest(scale), "character_id": "CHAR_007",
                "character_assembly_sha256": digest(assembly), "subject_label": "MALE",
                "subject_label_sha256": digest(scale), "subject_label_evidence": "Reviewed exact male character scale view.",
                "anatomy_compatibility": "MALE_ANATOMY", "anatomy_evidence_source": "APPROVED_PROFILE",
                "source_sheet_path": str(sheet), "source_sheet_sha256": digest(sheet),
            },
        }
        qa_path = self._guard_path(request_id).parent / "item-qa.json"
        qa_path.write_text(json.dumps(qa), encoding="utf-8")
        result = self._cli(
            "item_reference_manager.py", "approve", "--workspace", self.workspace,
            "--item-plan", plan_path, "--design-image", design, "--scale-fit-image", scale,
            "--qa-json", qa_path, "--approval-quote", f"Approve Forge {kind.lower()} v1 for integration.", "--user-approved",
        )
        return Path(json.loads(result.stdout)["manifest_path"])

    def test_typed_clothing_and_weapon_complete_full_multistage_lifecycles(self):
        from tools import style_pack_manager as manager

        character_request = "typed-multistage-character"
        _paths, assembly = self._register_synthetic_item_character(character_request)
        stage_ids = ("01_FACE_IDENTITY", "02_BODY_POSE", "03_CLOTHING", "04_CHARACTER_COMPOSITE", "05_FINAL_SCENE")
        qa_flags = {
            "FACE_GEOMETRY": "face", "EXPRESSION": "expression", "BODY_SILHOUETTE": "body-silhouette",
            "BODY_PROPORTIONS": "body-proportions", "LIMB_PROPORTIONS": "limb-proportions",
            "POSE_CONTACTS": "pose-contacts", "CAMERA": "camera", "STYLE": "style",
            "CLOTHING": "clothing", "LIGHTING": "lighting", "BACKGROUND": "background",
            "COMPOSITION": "composition", "MULTIVIEW_CONSISTENCY": "multiview-consistency",
            "NEUTRAL_BACKDROP": "neutral-backdrop", "SAFE_COVERAGE": "safe-coverage",
            "CLOTHING_TOPOLOGY": "clothing-topology", "BODY_RENDERING_STYLE": "body-style",
            "ITEM_DESIGN": "item-design", "ITEM_VERSION": "item-version", "ITEM_DIMENSIONS": "item-dimensions",
            "ITEM_MATERIAL_CONSTRUCTION": "item-material-construction", "ITEM_CHARACTER_SCALE": "item-character-scale",
            "CLOTHING_FIT": "clothing-fit", "LAYER_ORDER": "layer-order", "CONTACT_CONSTRAINTS": "contact-constraints",
            "SUBJECT_ACCURACY": "subject-accuracy", "OBJECTS_AND_ACTION": "objects-action",
            "NO_UNREQUESTED_CHARACTERS": "no-unrequested-characters", "FOCAL_HIERARCHY": "focal-hierarchy",
            "DISTANCE_READABILITY": "distance-readability", "DESKTOP_USABILITY": "desktop-usability",
            "POSTER_READABILITY": "poster-readability", "COPY_SAFE_AREA": "copy-safe-area",
            "DEPTH_AND_SCALE": "depth-and-scale", "PHENOMENON_CAUSALITY": "phenomenon-causality",
            "ARTIFACT_INTEGRITY": "artifact-integrity",
        }
        max_slots_by_kind = {}
        resolved_slot_evidence = {}
        plans_by_kind = {}
        plan_paths_by_kind = {}
        for kind in ("CLOTHING", "WEAPON"):
            request_id = f"typed-full-{kind.lower()}"
            guard = self._start_request(request_id, required_stages=stage_ids)
            item_manifest = self._approve_synthetic_typed_item(request_id, assembly, kind)
            _guard, plan_path = self._prepare_scene(
                request_id, start_guard=False, character_id="CHAR_007", item_reference=(item_manifest,),
                reference_workflow="MULTI_STAGE",
            )
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plans_by_kind[kind] = plan
            plan_paths_by_kind[kind] = plan_path
            stages = plan["generation_workflow"]["stages"]
            self.assertEqual([stage["stage_id"] for stage in stages], list(stage_ids))
            self.assertEqual(plan["selected_references"]["item_design"][0]["item_kind"], kind)
            self.assertEqual(plan["selected_references"]["item_design"][0]["version"], "v1")
            max_slots = 0
            prior_outputs = {}
            kind_slot_evidence = []
            for index, stage in enumerate(stages):
                stage_id = stage["stage_id"]
                call = self._resolve_and_prepare_call(
                    request_id, guard, plan_path,
                    f"Create stage {stage_id} for the exact approved character and the approved {kind.lower()} item.",
                    stage_id,
                )
                self.assertEqual(call["stage_id"], stage_id)
                max_slots = max(max_slots, len(call["slots"]))
                self.assertLessEqual(len(call["slots"]), 5)
                if stage_id in {"03_CLOTHING", "04_CHARACTER_COMPOSITE", "05_FINAL_SCENE"}:
                    assembly_hash = hashlib.sha256(assembly.read_bytes()).hexdigest()
                    self.assertTrue(any(
                        "CHARACTER_ASSEMBLY" in slot["active_roles"] and slot["sha256"] == assembly_hash
                        for slot in call["slots"]
                    ), f"{stage_id} must physically attach this character's approved assembly")
                kind_slot_evidence.append({
                    "stage_id": stage_id,
                    "slots": [
                        {
                            "path_name": Path(slot["path"]).name,
                            "sha256": slot["sha256"],
                            "active_roles": slot["active_roles"],
                        }
                        for slot in call["slots"]
                    ],
                    "stage_output_bindings": [
                        {"stage_id": binding["stage_id"], "path_name": Path(binding["path"]).name,
                         "sha256": binding["sha256"]}
                        for binding in call.get("stage_output_bindings", [])
                    ],
                })
                if stage_id in {"03_CLOTHING", "05_FINAL_SCENE"}:
                    roles = {role for slot in call["slots"] for role in slot["active_roles"]}
                    self.assertEqual(roles & {"ITEM_DESIGN", "ITEM_SCALE_FIT"}, {"ITEM_DESIGN", "ITEM_SCALE_FIT"})
                expected_bindings = [binding["stage_id"] for binding in call.get("stage_output_bindings", [])]
                self.assertTrue(set(expected_bindings).issubset(prior_outputs))
                attempt = self._start_call(guard, plan_path, call)
                output = self.workspace / f"{request_id}-{index}-{stage_id}.png"
                kind_offset = 0 if kind == "CLOTHING" else 19
                color = (35 + index * 23 + kind_offset, 45 + index * 17 + kind_offset,
                         55 + index * 11 + kind_offset)
                Image.new("RGB", (120 + index, 180), color).save(output)
                qa_args: list[str] = ["--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS"]
                for name in dict.fromkeys([*stage["required_qa"], *plan.get("scene_semantic_requirements", [])]):
                    suffix = qa_flags.get(name)
                    if suffix:
                        qa_args.extend((f"--qa-{suffix}", "PASS"))
                if stage_id in {"03_CLOTHING", "05_FINAL_SCENE"}:
                    for name in ("ITEM_DESIGN", "ITEM_VERSION", "ITEM_DIMENSIONS", "ITEM_MATERIAL_CONSTRUCTION", "ITEM_CHARACTER_SCALE"):
                        qa_args.extend((f"--qa-{qa_flags[name]}", "PASS"))
                    if kind == "CLOTHING":
                        for name in ("CLOTHING_FIT", "LAYER_ORDER", "CONTACT_CONSTRAINTS"):
                            qa_args.extend((f"--qa-{qa_flags[name]}", "PASS"))
                self._cli(
                    "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
                    "--style-name", self.style_name, "--image", output, "--request-id", request_id,
                    "--description", f"Synthetic {kind} {stage_id} stage", "--fidelity", "70",
                    "--status", "TEST" if stage_id == "05_FINAL_SCENE" else "STAGING",
                    "--character-id", "CHAR_007", "--attempt-id", attempt,
                    "--reference-plan", plan_path, "--stage-id", stage_id,
                    *self._visual_review_args(guard, attempt, output), *qa_args,
                )
                with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as stream:
                    row = [row for row in csv.DictReader(stream) if row["request_id"] == request_id][-1]
                copied = Path(row["style_file"])
                prior_outputs[stage_id] = copied
                self._cli(
                    "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "STAGE_COMPLETED",
                    "--summary", f"Synthetic {stage_id} output passed stage QA.", "--stage", stage_id, "--evidence", copied,
                )
                if stage_id in {"03_CLOTHING", "05_FINAL_SCENE"}:
                    receipt = manager.generation_qa_evidence(row)
                    self.assertIsNotNone(receipt)
                    contract = json.loads(Path(receipt["qa_contract"]).read_text(encoding="utf-8"))
                    self.assertTrue({"ITEM_DESIGN", "ITEM_VERSION", "ITEM_DIMENSIONS", "ITEM_MATERIAL_CONSTRUCTION", "ITEM_CHARACTER_SCALE"}
                                    .issubset(contract["expected_qa_layers"]))
            max_slots_by_kind[kind] = max_slots
            resolved_slot_evidence[kind] = {"peak_slots": max_slots, "stages": kind_slot_evidence}
            final_attempt = attempt
            final_output = copied
            self._cli(
                "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "RESULT_DELIVERED",
                "--summary", f"Synthetic {kind} multistage result delivery receipt.", "--attempt-id", final_attempt,
                "--delivery-evidence", str(final_output),
            )
            self._cli(
                "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "COMPLETE",
                "--summary", f"All {kind} scene stages passed and the result was delivered.",
            )
            final_state = json.loads(guard.read_text(encoding="utf-8"))
            self.assertEqual(final_state["status"], "COMPLETE")
            self.assertEqual([stage["id"] for stage in final_state["required_stages"]], list(stage_ids))
            self.assertEqual([stage["status"] for stage in final_state["required_stages"]], ["COMPLETED"] * len(stage_ids))
        self.assertEqual(max_slots_by_kind, {"CLOTHING": 5, "WEAPON": 5})
        clothing_outputs = {
            binding["stage_id"]: binding["sha256"]
            for stage in resolved_slot_evidence["CLOTHING"]["stages"]
            for binding in stage["stage_output_bindings"]
        }
        weapon_outputs = {
            binding["stage_id"]: binding["sha256"]
            for stage in resolved_slot_evidence["WEAPON"]["stages"]
            for binding in stage["stage_output_bindings"]
        }
        self.assertTrue(set(clothing_outputs) & set(weapon_outputs))
        self.assertTrue(all(clothing_outputs[stage] != weapon_outputs[stage]
                            for stage in set(clothing_outputs) & set(weapon_outputs)),
                        "a stage output from the other item's run must not share the same synthetic bytes")
        clothing_stage_outputs = manager.validated_stage_outputs(
            _paths, plan_paths_by_kind["CLOTHING"], plans_by_kind["CLOTHING"],
        )
        with self.assertRaisesRegex(manager.StylePackError, "different request"):
            manager.resolve_call_slots(
                _paths, plan_paths_by_kind["WEAPON"], plans_by_kind["WEAPON"],
                "03_CLOTHING", clothing_stage_outputs,
            )
        evidence_dir = Path(__file__).resolve().parents[1] / ".task_guard" / "character-base-multistage-20261016"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "resolved-slot-evidence.json").write_text(
            json.dumps(resolved_slot_evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )

    def test_assembly_plus_face_uses_assembly_as_the_only_body_reference(self):
        from tools import style_pack_manager as manager

        original_thread_id = os.environ.get("CODEX_THREAD_ID")
        self.addCleanup(
            lambda: os.environ.__setitem__("CODEX_THREAD_ID", original_thread_id)
            if original_thread_id is not None else os.environ.pop("CODEX_THREAD_ID", None)
        )
        paths = manager.make_paths(self.workspace, self.style_name)
        folder = paths.generations / "01_APPROVED_CHARACTERS" / "CHAR_001_Chance"
        base_dir = folder / "01_APPROVED_BASE"
        face_dir = folder / "03_FACE_REFERENCES"
        body_dir = folder / "04_BODY_REFERENCES"
        for directory in (base_dir, face_dir, body_dir):
            directory.mkdir(parents=True)
        assembly = base_dir / "CHARACTER_ASSEMBLY_USER_SELECTED.png"
        face = face_dir / "face.png"
        body = body_dir / "front.png"
        side = body_dir / "side.png"
        back = body_dir / "back.png"
        unapproved_face = self.workspace / "unapproved-face.png"
        for index, image in enumerate((assembly, face, body, side, back, unapproved_face)):
            Image.new("RGB", (32, 48), (80 + index, 100, 120)).save(image)
        profile = folder / "CHARACTER_PROFILE.yaml"
        profile.write_text(
            "character_id: CHAR_001\nname: Chance\nstatus: APPROVED\n"
            "identity:\n  gender_identity: man\n  visible_presentation: masculine\n"
            "anatomy_compatibility:\n  target_anatomy: MALE_ANATOMY\n  evidence_source: APPROVED_PROFILE\n"
            'character_face_references:\n  - "03_FACE_REFERENCES/face.png"\n'
            'character_body_references:\n  - "04_BODY_REFERENCES/front.png"\n  - "04_BODY_REFERENCES/side.png"\n  - "04_BODY_REFERENCES/back.png"\n'
            'canonical_views:\n  face: "03_FACE_REFERENCES/face.png"\n',
            encoding="utf-8",
        )
        manager.write_csv(paths.character_registry, manager.CHARACTER_FIELDS, [{
            "character_id": "CHAR_001", "name": "Chance", "approved_base": str(assembly),
            "profile_path": str(profile), "face_references": str(face),
            "body_references": ";".join(str(path) for path in (body, side, back)),
            "status": "APPROVED",
        }])
        manager.append_generation(
            paths, request_id="assembly-plus-face-base", character_id="CHAR_001",
            status="APPROVED_CHARACTER_BASE", fidelity=70, risk_level="D1",
            description="Approved canonical character base", source_image=assembly,
            archive_file=assembly, style_file=assembly,
        )
        resolved_result = self._cli(
            "style_pack_manager.py", "resolve-character", "--workspace", self.workspace,
            "--name", "Chance", "--json",
        )
        resolved = json.loads(resolved_result.stdout)
        self.assertEqual(resolved["status"], "FOUND", json.dumps(resolved, ensure_ascii=False))
        self.assertTrue(any(row["character_id"] == "CHAR_001" for row in resolved["matches"]))

        def prepare(mode: str, request_id: str, *, selected_face: Path | None = face,
                    body_reference: Path | None = None, selected_view: str = "ASSEMBLY",
                    use_profile_face_default: bool = False,
                    succeeds: bool = True) -> dict | None:
            os.environ["CODEX_THREAD_ID"] = f"integration-{self.workspace.name}-{request_id}"
            guard = self._guard_path(request_id)
            self._cli(
                "task_execution_guard.py", "start", "--state", guard,
                "--request-id", request_id, "--goal", "Check approved character scene references.",
                "--deliverable", "One synthetic preparation plan.",
            )
            args: list[object] = [
                "prepare-generation", "--workspace", self.workspace, "--style-name", self.style_name,
                "--request-id", request_id, "--fidelity", "70",
                "--startup-selection-mode", "DIRECT_CONFIRMATION", "--aux-body-decision", "DECLINED",
                "--confirmed-parameters-user-quote", "Use 70% fidelity and decline BODY_REFERENCE_LIBRARY.",
                "--confirmed-chat-id", f"integration-chat-{request_id}", "--confirmed-message-id", request_id,
                "--generation-purpose", "SCENE", "--character-id", "CHAR_001",
                "--scene-kind", "ARTIFACT", "--scene-output-use", "GENERAL_ART",
                "--orientation", "PORTRAIT", "--framing", "FULL_BODY",
                "--target-pose-family", "STANDING", "--dominant-body-source", "CHARACTER_BODY",
                "--body-source-coverage", "FULL_BODY", "--body-source-pose-family", "STANDING",
                "--body-silhouette-notes", "Preserve approved character silhouette.",
                "--style-reference", self.style_reference, "--reviewed", "STYLE=1",
                "--character-assembly", assembly, "--character-reference-mode", mode,
                "--shot-complexity", "COMPLEX", "--selected-body-view", selected_view,
                "--character-reference-evidence", "The assembly covers body identity; use face detail when needed.",
                "--override", "POSE", "--override", "CLOTHES", "--override", "LIGHTING",
                "--override", "BACKGROUND", "--override", "COMPOSITION",
            ]
            if selected_face and not use_profile_face_default:
                args.extend(("--primary-face", selected_face))
            if body_reference:
                args.extend(("--body-reference", body_reference))
            selected_body = body_reference or assembly
            for role, slot, path in (
                ("STYLE", "STYLE", self.style_reference),
                ("FACE", "PRIMARY_FACE", selected_face or face),
                ("FACE", "CHARACTER_ASSEMBLY", assembly),
                ("BODY", "BODY", selected_body),
                ("BODY", "CHARACTER_ASSEMBLY", assembly),
            ):
                args.extend(("--reviewed-source", review_declaration(role, slot, path)))
            original_profile = profile.read_text(encoding="utf-8")
            if selected_face is None:
                profile_data = yaml.safe_load(original_profile)
                profile_data["canonical_views"] = {}
                profile.write_text(yaml.safe_dump(profile_data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            try:
                result = self._cli("style_pack_manager.py", *args, succeeds=succeeds)
            finally:
                profile.write_text(original_profile, encoding="utf-8")
            if not succeeds:
                return None
            plan = json.loads((guard.parent / "REFERENCE_PLAN.json").read_text(encoding="utf-8"))
            return plan

        plan = prepare("ASSEMBLY_PLUS_FACE", "assembly-plus-face-success", use_profile_face_default=True)
        selection = plan["character_reference_selection"]
        self.assertEqual(selection["mode"], "ASSEMBLY_PLUS_FACE")
        self.assertEqual(selection["selected_body_view"], "ASSEMBLY")
        selected = plan["selected_references"]
        self.assertEqual(Path(selected["body"]["path"]).resolve(), assembly.resolve())
        self.assertEqual(Path(selected["character_assembly"]["path"]).resolve(), assembly.resolve())
        self.assertEqual(Path(selected["primary_face"]["path"]).resolve(), face.resolve())
        self.assertEqual(len({Path(row["path"]).resolve() for row in plan["selected_source_reviews"]}), 3)

        prepare("ASSEMBLY_PLUS_FACE", "assembly-plus-face-missing", selected_face=None, succeeds=False)
        prepare("ASSEMBLY_PLUS_FACE", "assembly-plus-face-unapproved", selected_face=unapproved_face, succeeds=False)
        prepare("ASSEMBLY_PLUS_FACE", "assembly-plus-face-extra-body", body_reference=body, succeeds=False)
        strict_plan = prepare("IDENTITY_STRICT", "assembly-plus-face-strict", body_reference=body, selected_view="FRONT")
        strict_selection = strict_plan["character_reference_selection"]
        self.assertEqual(strict_selection["mode"], "IDENTITY_STRICT")
        self.assertEqual(Path(strict_plan["selected_references"]["body"]["path"]).resolve(), body.resolve())
        auto_plan = prepare("AUTO", "assembly-plus-face-auto-regression", body_reference=body, selected_view="FRONT")
        self.assertEqual(auto_plan["character_reference_selection"]["mode"], "IDENTITY_STRICT")
        self.assertEqual(Path(auto_plan["selected_references"]["body"]["path"]).resolve(), body.resolve())

    def test_new_character_face_sources_can_be_explicit_identity_intent(self):
        request_id = "new-face-identity-intent"
        self.primary_face = self._classify(
            "identity-primary.png", "CHARACTER_FACE", (175, 115, 105), subject_label="MALE",
        )
        self.supporting_face = self._classify(
            "identity-supporting.png", "CHARACTER_FACE", (165, 108, 98), subject_label="MALE",
        )
        duplicate_bytes_face = self._classify(
            "identity-duplicate-bytes.png", "CHARACTER_FACE", (0, 0, 0),
            subject_label="MALE", copy_from=self.primary_face,
        )
        guard = self._start_request(request_id)
        args: list[object] = [
            "prepare-generation", "--workspace", self.workspace, "--style-name", self.style_name,
            "--request-id", request_id, "--fidelity", "50", "--confirmed-chat-id", "integration-chat",
            "--startup-selection-mode", "DIRECT_CONFIRMATION", "--aux-body-decision", "DECLINED",
            "--confirmed-message-id", "identity-intent-message",
            "--confirmed-parameters-user-quote", "Use 50% fidelity and decline BODY_REFERENCE_LIBRARY.",
            "--generation-purpose", "CHARACTER_BASE", "--character-id", "NEW", "--character-name", "Intent Test",
            "--adult-character", "--target-prompt", "Intent Test is an adult male character.",
            "--orientation", "PORTRAIT", "--framing", "FULL_BODY", "--target-pose-family", "STANDING",
            "--dominant-body-source", "PROMPT_BODY_SPEC", "--body-source-coverage", "FULL_BODY",
            "--body-source-pose-family", "STANDING", "--body-height-heads", "7.2-7.5",
            "--body-silhouette-notes", "Synthetic stable proportions for test fixture.", "--prompt-only-physique",
            "--style-reference", self.style_reference, "--primary-face", self.primary_face,
            "--supporting-face", self.supporting_face, "--new-face-reference-purpose", "USER_IDENTITY_INTENT",
            "--new-face-reference-purpose-evidence", "Current author message requests these portraits as identity intent.",
            "--reviewed", "STYLE=1", "--reviewed-source", review_declaration("STYLE", "STYLE", self.style_reference),
            "--reviewed-source", review_declaration("FACE", "PRIMARY_FACE", self.primary_face),
            "--reviewed-source", review_declaration("FACE", "SUPPORTING_FACE", self.supporting_face),
            "--reviewed", "FACE=2",
            "--override", "CLOTHES", "--override", "LIGHTING", "--override", "BACKGROUND", "--override", "COMPOSITION",
            "--user-reference-evidence-json", self._user_reference_evidence_file("identity-intent-message"),
        ]
        self._cli("style_pack_manager.py", *args)
        plan_path = guard.parent / "REFERENCE_PLAN.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        binding = plan["new_face_reference_purpose"]
        self.assertEqual(binding["mode"], "USER_IDENTITY_INTENT")
        self.assertEqual(binding["scope"], "NEW_CHARACTER_BASE")
        self.assertEqual([row["semantic_role"] for row in binding["sources"]], ["FACE", "FACE"])
        self.assertEqual({row["selection_key"] for row in binding["sources"]}, {"primary_face", "supporting_face"})
        self.assertEqual({row["sha256"] for row in binding["sources"]}, {
            hashlib.sha256(self.primary_face.read_bytes()).hexdigest(),
            hashlib.sha256(self.supporting_face.read_bytes()).hexdigest(),
        })
        selected_reviews = plan["selected_source_reviews"]
        face_reviews = [row for row in selected_reviews if row.get("role") == "FACE"]
        self.assertEqual({slot for row in face_reviews for slot in row["active_slot_roles"]}, {"PRIMARY_FACE", "SUPPORTING_FACE"})
        self.assertEqual([row["role"] for row in selected_reviews if "STYLE" in row.get("active_slot_roles", [])], ["STYLE"])

        call = self._resolve_and_prepare_call(
            request_id, guard, plan_path, "Create the synthetic identity face stage.", "01_FACE_IDENTITY",
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(call["new_face_reference_purpose"], binding)
        self.assertEqual(call["risk_assessment"]["new_face_reference_purpose"], binding)
        self.assertEqual(plan["risk_assessment"]["new_face_reference_purpose"], binding)
        ready_bytes = plan_path.read_bytes()
        changed_purpose = json.loads(ready_bytes)
        changed_purpose["new_face_reference_purpose"]["evidence"] = "altered after call preparation"
        plan_path.write_text(json.dumps(changed_purpose), encoding="utf-8")
        guard_module = __import__("tools.task_execution_guard", fromlist=["validate_reference_plan"])
        with self.assertRaisesRegex(guard_module.GuardError, "face purpose differs"):
            guard_module.validate_reference_plan(plan_path)
        plan_path.write_bytes(ready_bytes)
        for duplicate_mode in ("same_path", "same_hash_distinct_path"):
            altered = json.loads(ready_bytes)
            binding_to_tamper = altered["new_face_reference_purpose"]
            first_source, second_source = binding_to_tamper["sources"]
            if duplicate_mode == "same_path":
                duplicate_path = first_source["path"]
                duplicate_hash = first_source["sha256"]
                duplicate_selected = altered["selected_references"]["primary_face"]
                expected_error = "distinct absolute FACE source paths"
            else:
                duplicate_path = str(duplicate_bytes_face.resolve())
                duplicate_hash = hashlib.sha256(duplicate_bytes_face.read_bytes()).hexdigest()
                duplicate_selected = dict(altered["selected_references"]["primary_face"])
                duplicate_selected["path"] = duplicate_path
                duplicate_selected["sha256"] = duplicate_hash
                expected_error = "distinct FACE source SHA-256 hashes"
            altered["selected_references"]["supporting_face"] = duplicate_selected
            second_source["path"] = duplicate_path
            second_source["sha256"] = duplicate_hash
            second_source["subject_label_sha256"] = duplicate_hash
            for carrier in (
                altered["execution_call"],
                altered["risk_assessment"],
                altered["execution_call"]["risk_assessment"],
            ):
                carrier["new_face_reference_purpose"] = binding_to_tamper
            plan_path.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(guard_module.GuardError, expected_error):
                guard_module.validate_reference_plan(plan_path)
            plan_path.write_bytes(ready_bytes)
        self._start_call(guard, plan_path, call)
        state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(state["active_attempt"]["execution_snapshot"]["execution_call"]["new_face_reference_purpose"], binding)

        from tools import style_pack_manager as manager
        default_binding = manager.new_face_reference_purpose_binding(
            Namespace(new_face_reference_purpose="STYLE_SEED", new_face_reference_purpose_evidence="",
                     workspace=str(self.workspace), style_name=self.style_name),
            "CHARACTER_BASE", "NEW", {}, {},
        )
        self.assertEqual(default_binding, {"mode": "STYLE_SEED", "scope": "DEFAULT_EXISTING_BEHAVIOR", "sources": []})
        with self.assertRaisesRegex(manager.StylePackError, "only for a NEW CHARACTER_BASE"):
            manager.new_face_reference_purpose_binding(
                Namespace(new_face_reference_purpose="USER_IDENTITY_INTENT", new_face_reference_purpose_evidence="current author request",
                         workspace=str(self.workspace), style_name=self.style_name),
                "SCENE", "NONE", {}, {},
            )
        selected = plan["selected_references"]
        identity = plan["target_identity_compatibility"]["target"]
        args_namespace = Namespace(
            new_face_reference_purpose="USER_IDENTITY_INTENT",
            new_face_reference_purpose_evidence="current author request",
            workspace=self.workspace, style_name=self.style_name,
        )
        with self.assertRaisesRegex(manager.StylePackError, "both distinct"):
            manager.new_face_reference_purpose_binding(
                args_namespace, "CHARACTER_BASE", "NEW", {key: value for key, value in selected.items() if key != "supporting_face"}, identity,
            )
        with self.assertRaisesRegex(manager.StylePackError, "reviewed FEMALE subject label"):
            manager.new_face_reference_purpose_binding(
                args_namespace, "CHARACTER_BASE", "NEW", selected,
                {**identity, "target_anatomy": "FEMALE_ANATOMY"},
            )
        paths = manager.make_paths(self.workspace, self.style_name)
        duplicate_record = manager.validate_plan_reference(paths, str(duplicate_bytes_face), "FACE")
        same_path_selected = {**selected, "supporting_face": selected["primary_face"]}
        with self.assertRaisesRegex(manager.StylePackError, "distinct absolute FACE source paths"):
            manager.new_face_reference_purpose_binding(args_namespace, "CHARACTER_BASE", "NEW", same_path_selected, identity)
        same_hash_selected = {**selected, "supporting_face": duplicate_record}
        with self.assertRaisesRegex(manager.StylePackError, "distinct FACE source SHA-256 hashes"):
            manager.new_face_reference_purpose_binding(args_namespace, "CHARACTER_BASE", "NEW", same_hash_selected, identity)

        def rejected_preparation(variant_id: str, supporting_path: Path, expected: str) -> None:
            self._start_request(variant_id)
            variant = list(args)
            variant[variant.index("--request-id") + 1] = variant_id
            variant[variant.index("--supporting-face") + 1] = supporting_path
            for index, value in enumerate(variant):
                if not isinstance(value, str) or not value.startswith("{"):
                    continue
                try:
                    declaration = json.loads(value)
                except json.JSONDecodeError:
                    continue
                if declaration.get("role") == "FACE" and declaration.get("slot_role") == "SUPPORTING_FACE":
                    variant[index] = review_declaration("FACE", "SUPPORTING_FACE", supporting_path)
            result = self._cli("style_pack_manager.py", *variant, succeeds=False)
            self.assertIn(expected, result.stdout + result.stderr)

        rejected_preparation(
            "new-face-same-path-fidelity50", self.primary_face, "distinct absolute FACE source paths",
        )
        rejected_preparation(
            "new-face-same-hash-fidelity50", duplicate_bytes_face,
            "distinct FACE source SHA-256 hashes",
        )
        stale_selected = json.loads(json.dumps(selected))
        stale_selected["primary_face"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(manager.StylePackError, "missing or changed"):
            manager.new_face_reference_purpose_binding(args_namespace, "CHARACTER_BASE", "NEW", stale_selected, identity)
        style_overlap = dict(selected)
        style_overlap["style"] = selected["primary_face"]
        with self.assertRaisesRegex(manager.StylePackError, "independent STYLE authority"):
            manager.new_face_reference_purpose_binding(args_namespace, "CHARACTER_BASE", "NEW", style_overlap, identity)
        malformed = json.loads(plan_path.read_text(encoding="utf-8"))
        malformed["execution_call"]["new_face_reference_purpose"] = {"mode": "STYLE_SEED"}
        plan_path.write_text(json.dumps(malformed), encoding="utf-8")
        with self.assertRaisesRegex(guard_module.GuardError, "purpose"):
            guard_module.validate_reference_plan(plan_path)

    def _start_request(self, request_id: str, *, required_stages: tuple[str, ...] = ()) -> Path:
        guard_path = self._guard_path(request_id)
        args: list[object] = [
            "start", "--state", guard_path, "--request-id", request_id,
            "--goal", "Run a synthetic StoryArt workflow integration fixture.",
            "--deliverable", "One synthetic image result.",
        ]
        for stage in required_stages:
            args.extend(("--required-stage", stage))
        self._cli("task_execution_guard.py", *args)
        return guard_path

    def _prepare_scene(
        self,
        request_id: str,
        fidelity: int = 70,
        *,
        start_guard: bool = True,
        startup_mode: str = "DIRECT_CONFIRMATION",
        startup_quote: str | None = None,
        body_decision: str = "DECLINED",
        succeeds: bool = True,
        startup_choice: str = "",
        startup_choice_quote: str = "",
        startup_options: tuple[str, str, str] | None = None,
        custom_profile_quote: str = "",
        menu_surface: str = "TEXT_NUMBERED_MENU",
        reuse_from: Path | None = None,
        reuse_chat_id: str = "",
        reuse_message_id: str = "",
        reviewed_style: int = 1,
        review_selected_source: bool = True,
        character_id: str = "NONE",
        item_reference: tuple[Path, ...] = (),
        reference_workflow: str = "SINGLE_PASS",
    ) -> tuple[Path, Path]:
        guard = self._start_request(request_id) if start_guard else self._guard_path(request_id)
        args: list[object] = [
            "prepare-generation",
            "--workspace", self.workspace, "--style-name", self.style_name,
            "--request-id", request_id, "--fidelity", str(fidelity),
            "--startup-selection-mode", startup_mode, "--aux-body-decision", body_decision,
            "--confirmed-chat-id", "integration-chat", "--confirmed-message-id", "integration-message",
        ]
        if startup_mode == "DIRECT_CONFIRMATION":
            args.extend(("--confirmed-parameters-user-quote", startup_quote or f"Keep the confirmed {fidelity}% fidelity and decline BODY_REFERENCE_LIBRARY."))
        elif startup_mode in {"USER_CONFIRMATION", "NEW"}:
            args.extend(("--startup-menu-surface", menu_surface, "--startup-choice", startup_choice, "--startup-choice-user-quote", startup_choice_quote))
            for option in startup_options or ():
                if "; style=" not in option:
                    option += f"; style=PROJECT_STYLE:{self.style_name}; reference_policy=PROJECT_STYLE_ONLY; character=NONE"
                args.extend(("--startup-option", option))
            if custom_profile_quote:
                args.extend(("--custom-parameters-user-quote", custom_profile_quote))
        elif startup_mode == "REUSE":
            args.extend(("--reuse-startup-from", reuse_from, "--reuse-chat-id", reuse_chat_id, "--reuse-message-id", reuse_message_id))
        args.extend((
            "--generation-purpose", "SCENE", "--character-id", character_id,
            "--reference-workflow", reference_workflow,
            "--scene-kind", "ARTIFACT", "--scene-output-use", "GENERAL_ART",
            "--scene-subject-from-prompt", "--style-reference", self.style_reference,
            "--reviewed", f"STYLE={reviewed_style}",
            "--override", "FACE", "--override", "BODY", "--override", "POSE",
            "--override", "CLOTHES", "--override", "LIGHTING", "--override", "BACKGROUND",
            "--override", "COMPOSITION",
        ))
        if review_selected_source:
            args.extend(("--reviewed-source", review_declaration("STYLE", "STYLE", self.style_reference)))
        if character_id != "NONE":
            from tools import style_pack_manager as manager
            row = next(item for item in manager.read_csv(manager.make_paths(self.workspace, self.style_name).character_registry)
                       if item.get("character_id") == character_id)
            args.extend((
                "--character-assembly", row["approved_base"],
                "--character-reference-evidence", "The approved assembly is the requested exact character identity source.",
                "--framing", "THREE_QUARTER", "--target-pose-family", "STANDING",
                "--dominant-body-source", "CHARACTER_BODY", "--body-source-coverage", "THREE_QUARTER",
                "--body-source-pose-family", "STANDING",
            ))
            assembly = Path(row["approved_base"])
            for role, slot_role in (("FACE", "CHARACTER_ASSEMBLY"), ("BODY", "CHARACTER_ASSEMBLY"),
                                    ("FACE", "PRIMARY_FACE"), ("BODY", "BODY")):
                args.extend(("--reviewed-source", review_declaration(role, slot_role, assembly)))
        for manifest in item_reference:
            args.extend(("--item-reference", manifest))
            item = json.loads(manifest.read_text(encoding="utf-8"))
            for role, slot_role, key in (("ITEM_DESIGN", "ITEM_DESIGN", "item_design"),
                                         ("ITEM_SCALE_FIT", "ITEM_SCALE_FIT", "item_scale_fit")):
                args.extend(("--reviewed-source", review_declaration(role, slot_role, Path(item[key]["path"]))))
        if body_decision == "SELECTED":
            args.extend(("--aux-body-selection-note", "No reviewed reusable body reference is relevant to this artifact scene."))
        self._cli("style_pack_manager.py", *args, succeeds=succeeds)
        plan_path = guard.parent / "REFERENCE_PLAN.json"
        return guard, plan_path

    def test_prepare_generation_requires_exact_selected_source_review(self):
        matrix_pool = self.style_pack / "01_WORK" / "STYLE_CROPS"
        matrix_pool.mkdir(parents=True, exist_ok=True)
        for index in range(25):
            Image.new("RGB", (24, 32), (index, 90, 140)).save(matrix_pool / f"matrix-{index:02}.png")
        _, plan_path = self._prepare_scene(
            "selected-source-review-missing", review_selected_source=False, succeeds=False,
        )
        self.assertFalse(plan_path.exists())
        guard, plan_path = self._prepare_scene("selected-source-review-bound")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        reviews = plan["selected_source_reviews"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(Path(reviews[0]["path"]).resolve(), self.style_reference.resolve())
        self.assertEqual(reviews[0]["sha256"], hashlib.sha256(self.style_reference.read_bytes()).hexdigest())
        self.assertEqual(reviews[0]["role"], "STYLE")
        self.assertEqual(plan["local_context"]["inventory_status"], "NOT_SCANNED_DURING_PREPARATION")
        self.assertIsNone(plan["local_context"]["local_files_total"])
        self.assertEqual(plan["local_context"]["review_pool_counts"], {})

    def test_approved_scene_reviews_every_attached_slot_including_auxiliary_body(self):
        from tools import style_pack_manager as manager

        assembly = self.workspace / "approved-assembly.png"
        auxiliary = self.workspace / "aux-body.png"
        Image.new("RGB", (64, 96), (40, 50, 60)).save(assembly)
        Image.new("RGB", (64, 96), (70, 80, 90)).save(auxiliary)
        selected = {
            "character_assembly": {"path": str(assembly), "sha256": manager.sha256(assembly)},
            "primary_face": {"path": str(assembly), "sha256": manager.sha256(assembly)},
        }
        active_roles = {"character_assembly": ["FACE", "BODY"], "primary_face": ["FACE"]}
        attached_auxiliary = [{
            "ref_id": "BR_0007", "path": str(auxiliary), "sha256": manager.sha256(auxiliary),
            "active_roles": ["AUX_BODY_BUILD", "AUX_POSE", "AUX_CAMERA", "AUX_CLOTHING_BEHAVIOR", "AUX_OBJECT_INTERACTION"],
        }]
        declarations = [
            review_declaration("FACE", "CHARACTER_ASSEMBLY", assembly),
            review_declaration("BODY", "CHARACTER_ASSEMBLY", assembly),
            review_declaration("FACE", "PRIMARY_FACE", assembly),
            review_declaration("BODY", "BR_0007:AUX_BODY_BUILD", auxiliary),
            review_declaration("POSE", "BR_0007:AUX_POSE", auxiliary),
            review_declaration("COMPOSITION", "BR_0007:AUX_CAMERA", auxiliary),
            review_declaration("CLOTHES", "BR_0007:AUX_CLOTHING_BEHAVIOR", auxiliary),
            review_declaration("BODY", "BR_0007:AUX_OBJECT_INTERACTION", auxiliary),
            review_declaration("COMPOSITION", "BR_0007:AUX_OBJECT_INTERACTION", auxiliary),
        ]
        reviews = manager.parse_selected_source_reviews(declarations, selected, active_roles, attached_auxiliary)
        self.assertEqual({(row["role"], tuple(row["active_slot_roles"])) for row in reviews}, {
            ("FACE", ("CHARACTER_ASSEMBLY",)), ("BODY", ("CHARACTER_ASSEMBLY",)),
            ("FACE", ("PRIMARY_FACE",)), ("BODY", ("BR_0007:AUX_BODY_BUILD",)),
            ("POSE", ("BR_0007:AUX_POSE",)), ("COMPOSITION", ("BR_0007:AUX_CAMERA",)),
            ("CLOTHES", ("BR_0007:AUX_CLOTHING_BEHAVIOR",)),
            ("BODY", ("BR_0007:AUX_OBJECT_INTERACTION",)),
            ("COMPOSITION", ("BR_0007:AUX_OBJECT_INTERACTION",)),
        })
        self.assertTrue(all(row["view"] == "FULL_RESOLUTION" and row["review_outcome"] == "PASS" for row in reviews))
        self.assertTrue(all(row["review_basis"] == "CALLER_DECLARED_CURRENT_REQUEST_VISUAL_REVIEW" for row in reviews))
        self.assertTrue(all(row["limitations"] and row["review_findings"] for row in reviews))
        with self.assertRaisesRegex(manager.StylePackError, "BR_0007:AUX_BODY_BUILD"):
            manager.parse_selected_source_reviews(declarations[:3] + declarations[4:], selected, active_roles, attached_auxiliary)
        with self.assertRaisesRegex(manager.StylePackError, "attestation"):
            manager.parse_selected_source_reviews([f"FACE={assembly}"], selected, active_roles, attached_auxiliary)
        incomplete = json.dumps({"role": "FACE", "slot_role": "CHARACTER_ASSEMBLY", "path": str(assembly)})
        with self.assertRaisesRegex(manager.StylePackError, "missing required fields"):
            manager.parse_selected_source_reviews([incomplete, *declarations[1:]], selected, active_roles, attached_auxiliary)
        with self.assertRaisesRegex(manager.StylePackError, "only PASS"):
            manager.parse_selected_source_reviews([
                review_declaration("FACE", "CHARACTER_ASSEMBLY", assembly, outcome="FAIL"), *declarations[1:],
            ], selected, active_roles, attached_auxiliary)

    def test_style_only_yes_cannot_prepare_or_write_reference_plan(self):
        _, plan_path = self._prepare_scene(
            "style-only-yes", startup_mode="DIRECT_CONFIRMATION", body_decision="SELECTED",
            startup_quote="да", succeeds=False,
        )
        self.assertFalse(plan_path.exists())

    def test_unformed_style_accepts_confirmed_request_local_master_candidate_without_promoting_anchor(self):
        metadata_path = self.style_pack / ".style-pack.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["status"] = "REVIEW_REQUIRED"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        candidate = self.style_pack / "01_WORK" / "STYLE_CROPS" / "MASTER_STYLE_fixture.png"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (96, 128), (40, 50, 60)).save(candidate)
        self.style_reference = candidate
        reference_manifest = self.style_pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "LOGS" / "PRELIMINARY_REFERENCE_MANIFEST.csv"
        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            manifest_rows = list(reader)
        candidate_row = {field: "" for field in fieldnames}
        candidate_row.update({
            "stored_relative_path": "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png",
            "primary_role": "MASTER_STYLE",
            "status": "TEST",
            "generator_safe": "YES",
            "subject_label": "NO_SUBJECT", "subject_label_evidence": "SYNTHETIC_PLAIN_COLOR_FIELD",
            "subject_label_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(), "subject_label_status": "REVIEWED",
        })
        manifest_rows.append(candidate_row)
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(manifest_rows)
        options = (
            "OPTION_1=Recommended 90% fidelity; select BODY_REFERENCE_LIBRARY",
            "OPTION_2=Contextual 90% fidelity; decline BODY_REFERENCE_LIBRARY",
            "OPTION_3=Contextual 70% fidelity; decline BODY_REFERENCE_LIBRARY",
        )
        guard, plan_path = self._prepare_scene(
            "unformed-request-local-style", 90, startup_mode="USER_CONFIRMATION",
            body_decision="SELECTED", startup_choice="OPTION_1", startup_choice_quote="1",
            startup_options=options, reviewed_style=2,
        )
        call = self._resolve_and_prepare_call(
            "unformed-request-local-style", guard, plan_path,
            "Create one synthetic fixture using only the confirmed local style candidate.",
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        selected_style = plan["selected_references"]["style"][0]
        self.assertEqual(selected_style["status"], "REQUEST_LOCAL_STYLE_CANDIDATE")
        self.assertEqual(selected_style["reference_scope"], "CURRENT_REQUEST_ONLY")
        self.assertFalse(selected_style["permanent_anchor"])
        self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8"))["status"], "REVIEW_REQUIRED")

        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            rows = list(reader)
        for row in rows:
            if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                row["status"] = "REJECTED"
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        prompt = "Create one synthetic fixture using only the confirmed local style candidate."
        self._cli(
            "style_pack_manager.py", "prepare-call", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", "unformed-request-local-style",
            "--prompt-text", prompt, "--risk-assessment", call["risk_assessment"]["path"],
            "--user-selections-json", self._user_selections_file("unformed-request-local-style", plan_path),
            succeeds=False,
        )

        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            rows = list(reader)
        for row in rows:
            if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                row["status"], row["generator_safe"] = "TEST", "NO"
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["status"] = "FINALIZED_APPROVED"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self._cli(
            "style_pack_manager.py", "prepare-call", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", "unformed-request-local-style",
            "--prompt-text", prompt, "--risk-assessment", call["risk_assessment"]["path"],
            "--user-selections-json", self._user_selections_file("unformed-request-local-style", plan_path),
            succeeds=False,
        )

        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            rows = list(reader)
        for row in rows:
            if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                row["status"], row["generator_safe"] = "TEST", "NO"
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        from tools import style_pack_manager as manager
        from tools.task_execution_guard import GuardError, validate_reference_plan
        with self.assertRaisesRegex(GuardError, "no longer positive and generator-safe"):
            validate_reference_plan(plan_path)

        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            rows = list(reader)
        for row in rows:
            if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                row["status"], row["generator_safe"] = "", "YES"
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        self.assertFalse(manager.has_positive_master_style_manifest_entry(
            manager.make_paths(self.workspace, self.style_name), candidate.relative_to(self.style_pack),
        ))
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            valid_row = {field: "" for field in fieldnames}
            valid_row.update({
                "stored_relative_path": "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png",
                "primary_role": "MASTER_STYLE", "status": "TEST", "generator_safe": "YES",
                "user_approved": "NO",
            })
            writer.writerow(valid_row)
            writer.writerow(valid_row)
        self.assertFalse(manager.has_positive_master_style_manifest_entry(
            manager.make_paths(self.workspace, self.style_name), candidate.relative_to(self.style_pack),
        ))

    def test_request_local_style_candidate_rechecks_manifest_at_prepare_call(self):
        metadata_path = self.style_pack / ".style-pack.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["status"] = "REVIEW_REQUIRED"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        candidate = self.style_pack / "01_WORK" / "STYLE_CROPS" / "MASTER_STYLE_fixture.png"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (96, 128), (40, 50, 60)).save(candidate)
        self.style_reference = candidate
        reference_manifest = self.style_pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "LOGS" / "PRELIMINARY_REFERENCE_MANIFEST.csv"
        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            manifest_rows = list(reader)
        candidate_row = {field: "" for field in fieldnames}
        candidate_row.update({
            "stored_relative_path": "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png",
            "primary_role": "MASTER_STYLE", "status": "TEST", "generator_safe": "YES",
            "subject_label": "NO_SUBJECT", "subject_label_evidence": "SYNTHETIC_PLAIN_COLOR_FIELD",
            "subject_label_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(), "subject_label_status": "REVIEWED",
            "user_approved": "NO",
        })
        manifest_rows.append(candidate_row)
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(manifest_rows)

        options = (
            "OPTION_1=Recommended 90% fidelity; select BODY_REFERENCE_LIBRARY",
            "OPTION_2=Contextual 90% fidelity; decline BODY_REFERENCE_LIBRARY",
            "OPTION_3=Contextual 70% fidelity; decline BODY_REFERENCE_LIBRARY",
        )
        for request_id, status, safe in (
            ("request-local-rejected-after-plan", "REJECTED", "YES"),
            ("request-local-unsafe-after-plan", "TEST", "NO"),
        ):
            with self.subTest(status=status, generator_safe=safe):
                with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
                    reader = csv.DictReader(stream)
                    fieldnames = reader.fieldnames
                    rows = list(reader)
                for row in rows:
                    if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                        row["status"], row["generator_safe"] = "TEST", "YES"
                with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)

                guard, plan_path = self._prepare_scene(
                    request_id, 90, startup_mode="USER_CONFIRMATION", body_decision="SELECTED",
                    startup_choice="OPTION_1", startup_choice_quote="1", startup_options=options, reviewed_style=2,
                )
                self._cli(
                    "style_pack_manager.py", "resolve-call", "--workspace", self.workspace,
                    "--style-name", self.style_name, "--request-id", request_id,
                )
                resolved_path = guard.parent / "TECHNICAL_REFERENCES" / "RESOLVED_CALL_SINGLE_PASS.json"
                resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
                prompt = f"Create one synthetic fixture for {request_id}."
                risk_args: list[object] = [
                    "--text", prompt, "--output", self.workspace / f"{request_id}-risk.json",
                ]
                for slot in resolved["slots"]:
                    role_list = ",".join(slot["active_roles"])
                    risk_args.extend(("--reference", f"{slot['path']}::D2::0D::Synthetic reviewed fixture::{role_list}"))
                risk_path = self.workspace / f"{request_id}-risk.json"
                self._cli("generation_risk_assessor.py", *risk_args)

                with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
                    reader = csv.DictReader(stream)
                    fieldnames = reader.fieldnames
                    rows = list(reader)
                for row in rows:
                    if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                        row["status"], row["generator_safe"] = status, safe
                with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)

                self._cli(
                    "style_pack_manager.py", "prepare-call", "--workspace", self.workspace,
                    "--style-name", self.style_name, "--request-id", request_id,
                    "--prompt-text", prompt, "--risk-assessment", risk_path,
                    "--user-selections-json", self._user_selections_file(request_id, plan_path),
                    succeeds=False,
                )
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                self.assertEqual(plan["gate_status"], "PREPARED_AWAITING_EXECUTABLE_CALL")
                self.assertIsNone(plan["execution_call"])
                guard_state = json.loads(guard.read_text(encoding="utf-8"))
                self.assertNotIn("ready_binding", guard_state)
    def test_numbered_native_custom_and_reused_profiles_reach_ready_call(self):
        options = (
            "OPTION_1=Recommended 90% fidelity; select BODY_REFERENCE_LIBRARY",
            "OPTION_2=Contextual 90% fidelity; decline BODY_REFERENCE_LIBRARY",
            "OPTION_3=Contextual 70% fidelity; decline BODY_REFERENCE_LIBRARY",
        )
        cases = (
            ("menu-option-1", 90, "SELECTED", "OPTION_1", "1", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", ""),
            ("menu-option-2", 90, "DECLINED", "OPTION_2", "2", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", ""),
            ("menu-option-3", 70, "DECLINED", "OPTION_3", "3", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", ""),
            ("menu-custom", 50, "SELECTED", "CUSTOM", "4", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", "Use 50% fidelity and select BODY_REFERENCE_LIBRARY."),
            ("menu-custom-natural-en", 50, "SELECTED", "CUSTOM", "Use 50% fidelity and select BODY_REFERENCE_LIBRARY.", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", ""),
            ("menu-custom-natural-ru", 70, "DECLINED", "CUSTOM", "Хочу 70% стиля без BODY_REFERENCE_LIBRARY.", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", ""),
            ("menu-native-new", 90, "SELECTED", "OPTION_1", "OPTION_1", "NEW", "NATIVE_CONTEXT_MENU", ""),
        )
        source_plan: Path | None = None
        for request_id, fidelity, decision, choice, reply, mode, surface, custom_quote in cases:
            with self.subTest(request_id=request_id):
                guard, plan_path = self._prepare_scene(
                    request_id, fidelity, startup_mode=mode, body_decision=decision,
                    startup_choice=choice, startup_choice_quote=reply,
                    startup_options=tuple(value + f"; style=PROJECT_STYLE:{self.style_name}; reference_policy=PROJECT_STYLE_ONLY; character=NONE" for value in options) if request_id == "menu-native-new" else options,
                    custom_profile_quote=custom_quote, menu_surface=surface,
                )
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                startup = plan["startup_parameter_selection"]
                self.assertEqual(startup["user_choice_quote"], reply)
                self.assertEqual(startup["resolved_parameters"]["fidelity"], fidelity)
                self.assertEqual(startup["resolved_parameters"]["aux_body_decision"], decision)
                if choice == "CUSTOM":
                    self.assertEqual(startup["custom_parameters_user_quote"], custom_quote or reply)
                prompt = f"Create one synthetic observatory fixture for {request_id}."
                call = self._resolve_and_prepare_call(request_id, guard, plan_path, prompt)
                if request_id == "menu-native-new":
                    self.assertEqual(call["user_selections"]["style"], {"choice": f"PROJECT_STYLE:{self.style_name}", "user_quote": reply})
                if request_id == "menu-option-2":
                    self.assertEqual(startup["resolved_user_selections"], {
                        "style": f"PROJECT_STYLE:{self.style_name}",
                        "reference_policy": "PROJECT_STYLE_ONLY",
                        "character": "NONE",
                    })
                    self.assertEqual(call["user_selections"], {
                        key: {"choice": value, "user_quote": "2"}
                        for key, value in startup["resolved_user_selections"].items()
                    })
                if request_id == "menu-option-1":
                    source_plan = plan_path

        assert source_plan is not None
        source = json.loads(source_plan.read_text(encoding="utf-8"))["startup_parameter_selection"]
        profile = source["resolved_parameters"]
        for request_id, reused_from in (("menu-reuse-once", source_plan),):
            guard, plan_path = self._prepare_scene(
                request_id, profile["fidelity"], startup_mode="REUSE", body_decision=profile["aux_body_decision"],
                reuse_from=reused_from, reuse_chat_id=source["confirmed_provenance"]["chat_id"],
                reuse_message_id=source["confirmed_provenance"]["message_id"],
            )
            call = self._resolve_and_prepare_call(request_id, guard, plan_path, f"Create one synthetic observatory fixture for {request_id}.")
            self.assertEqual(json.loads(plan_path.read_text(encoding="utf-8"))["startup_parameter_selection"]["selected"], "OPTION_1")
            source_plan = plan_path

        assert source_plan is not None
        repeated = json.loads(source_plan.read_text(encoding="utf-8"))["startup_parameter_selection"]
        guard, plan_path = self._prepare_scene(
            "menu-reuse-twice", repeated["resolved_parameters"]["fidelity"], startup_mode="REUSE",
            body_decision=repeated["resolved_parameters"]["aux_body_decision"],
            reuse_from=source_plan, reuse_chat_id=repeated["confirmed_provenance"]["chat_id"],
            reuse_message_id=repeated["confirmed_provenance"]["message_id"],
        )
        self._resolve_and_prepare_call("menu-reuse-twice", guard, plan_path, "Create one synthetic observatory fixture for repeated reuse.")

    def _resolve_and_prepare_call(self, request_id: str, guard: Path, plan_path: Path, prompt: str, stage_id: str = "") -> dict:
        resolve_args: list[object] = [
            "resolve-call", "--workspace", self.workspace, "--style-name", self.style_name,
            "--request-id", request_id,
        ]
        if stage_id:
            resolve_args.extend(("--stage-id", stage_id))
        self._cli("style_pack_manager.py", *resolve_args)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        from tools.style_pack_manager import render_confirmed_prompt
        prompt = render_confirmed_prompt(plan, prompt)
        selected_stage = stage_id or "SINGLE_PASS"
        resolved_manifest = guard.parent / "TECHNICAL_REFERENCES" / f"RESOLVED_CALL_{selected_stage}.json"
        resolved = json.loads(resolved_manifest.read_text(encoding="utf-8"))
        self.assertEqual(resolved["request_id"], request_id)
        self.assertTrue(resolved["slots"])
        risk_args: list[object] = ["--text", prompt, "--output", self.workspace / f"{request_id}-{selected_stage}-risk.json"]
        for slot in resolved["slots"]:
            role_list = ",".join(slot["active_roles"])
            risk_args.extend(("--reference", f"{slot['path']}::D2::0D::Synthetic reviewed fixture::{role_list}"))
        risk_path = Path(risk_args[risk_args.index("--output") + 1])
        self._cli("generation_risk_assessor.py", *risk_args)
        call_args: list[object] = [
            "prepare-call", "--workspace", self.workspace, "--style-name", self.style_name,
            "--request-id", request_id, "--prompt-text", prompt, "--risk-assessment", risk_path,
            "--user-selections-json", self._user_selections_file(request_id, plan_path),
        ]
        if plan.get("startup_parameter_selection", {}).get("resolved_user_selections"):
            call_args = call_args[:-2]
        if plan.get("character_id") == "NEW":
            stage_id = stage_id or "SINGLE_PASS"
            resolved_path = guard.parent / "TECHNICAL_REFERENCES" / f"RESOLVED_CALL_{stage_id}.json"
            resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
            from tools import style_pack_manager as manager
            stage_evidence = manager.expected_stage_user_reference_evidence(plan, resolved)
            evidence_path = self.workspace / f"stage-user-reference-evidence-{stage_id}.json"
            evidence_path.write_text(json.dumps(stage_evidence), encoding="utf-8")
            call_args.extend(("--user-reference-evidence-json", evidence_path))
        if stage_id:
            call_args.extend(("--stage-id", stage_id))
        self._cli("style_pack_manager.py", *call_args)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["gate_status"], "READY_FOR_GENERATION")
        call = plan["execution_call"]
        self.assertTrue(call["slots"])
        self.assertEqual(call["risk_assessment"]["generation_risk"], plan["risk_assessment"]["generation_risk"])
        return call

    def _start_call(self, guard: Path, plan_path: Path, call: dict) -> str:
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "CALL_VALIDATED",
            "--summary", "Synthetic integration call was validated.",
        )
        args: list[object] = [
            "checkpoint", "--state", guard, "--event", "EXECUTION_STARTED",
            "--summary", "Synthetic integration call started.", "--reference-plan", plan_path,
            "--stage", call["stage_id"], "--output-contract", "REQUESTED_DELIVERABLE",
        ]
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        child_base = isinstance(plan.get("character_base_contract"), dict) and plan["character_base_contract"].get("age_group") == "CHILD"
        if not child_base and call["stage_id"] in {"02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK"}:
            args.extend(("--swimwear-rung", "TWO_PIECE"))
        self._cli("task_execution_guard.py", *args)
        state = json.loads(guard.read_text(encoding="utf-8"))
        return state["active_attempt"]["attempt_id"]

    def _risk_level(self, plan_path: Path) -> str:
        return json.loads(plan_path.read_text(encoding="utf-8"))["risk_assessment"]["generation_risk"]

    def test_exact_call_user_evidence_is_stage_scoped_and_rejects_invalid_bindings(self):
        from tools import generation_request, style_pack_manager as manager

        evidence_path = self._user_reference_evidence_file("integration-message")
        evidence_rows = json.loads(evidence_path.read_text(encoding="utf-8"))
        plan = {
            "user_reference_evidence": evidence_rows,
            "startup_parameter_selection": {"confirmed_provenance": {"chat_id": "integration-chat"}},
            "selected_source_reviews": [
                {"role": "FACE", "active_slot_roles": [role], "path": row["path"], "sha256": row["sha256"]}
                for row, role in zip(evidence_rows, ("PRIMARY_FACE", "SUPPORTING_FACE"))
            ],
        }
        first_stage = {"slots": [{
            "path": evidence_rows[0]["path"], "sha256": evidence_rows[0]["sha256"],
            "active_roles": ["PRIMARY_FACE"],
        }]}
        expected = manager.expected_stage_user_reference_evidence(plan, first_stage)
        self.assertEqual(expected, [evidence_rows[0]])
        generation_request._validate_call_evidence({"user_reference_evidence": expected}, plan, first_stage)
        with self.assertRaisesRegex(manager.StylePackError, "stage's reviewed physical slots"):
            generation_request._validate_call_evidence({"user_reference_evidence": []}, plan, first_stage)
        with self.assertRaisesRegex(manager.StylePackError, "stage's reviewed physical slots"):
            generation_request._validate_call_evidence({"user_reference_evidence": evidence_rows}, plan, first_stage)

        stale = json.loads(json.dumps(plan))
        stale["user_reference_evidence"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(manager.StylePackError, "hash"):
            manager.expected_stage_user_reference_evidence(stale, first_stage)
        wrong_chat = json.loads(json.dumps(plan))
        wrong_chat["user_reference_evidence"][0]["chat_id"] = "another-chat"
        with self.assertRaisesRegex(manager.StylePackError, "different chat"):
            manager.expected_stage_user_reference_evidence(wrong_chat, first_stage)
        wrong_role = {"slots": [{**first_stage["slots"][0], "active_roles": ["SUPPORTING_FACE"]}]}
        with self.assertRaisesRegex(manager.StylePackError, "reviewed role"):
            manager.expected_stage_user_reference_evidence(plan, wrong_role)

    def _visual_review_args(self, guard: Path, attempt_id: str, output: Path) -> tuple[str, Path]:
        """Create a fixture review bound to the exact attempt snapshot and output."""
        from tools.style_pack_manager import prompt_review_clauses

        state = json.loads(guard.read_text(encoding="utf-8"))
        attempt = next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)
        snapshot = attempt["execution_snapshot"]
        execution_call = snapshot["execution_call"]
        prompt = execution_call["prompt"]["text"]
        visible_character = str(execution_call.get("character_id", "NONE")).upper() not in {"NONE", "NEW", ""}
        anatomy_status = "PASS" if visible_character or snapshot["execution_stage"] in {
            "01_FACE_IDENTITY", "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY"
        } else "NOT_APPLICABLE"
        report = {
            "schema_version": 1,
            "request_id": state["request_id"],
            "attempt_id": attempt_id,
            "task_revision": attempt["task_revision"],
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "prompt_sha256": snapshot["prompt_sha256"],
            "executed_plan_sha256": snapshot["reference_plan_snapshot_sha256"],
            "author": "integration fixture visual reviewer",
            "anatomy_review": {
                "status": anatomy_status,
                "no_visible_anatomy": anatomy_status == "NOT_APPLICABLE",
                "reason": "Synthetic fixture scene contains no visible human or animal anatomy." if anatomy_status == "NOT_APPLICABLE" else "Synthetic full-body stage is explicitly reviewed for anatomy.",
                "checked_scope": "Visible anatomy across the full image at original resolution.",
                "findings": [],
            },
            "visible_defect_review": {"status": "PASS", "checked_scope": "Entire synthetic output at full resolution.", "findings": []},
            "prompt_adherence": {
                "status": "PASS", "checked_scope": "FULL_EXECUTED_PROMPT", "all_explicit_constraints_assessed": True,
                "constraints": [
                    {"constraint": clause, "status": "PASS", "evidence": f"Synthetic fixture reviewed prompt clause: {clause}"}
                    for clause in prompt_review_clauses(prompt)
                ],
            },
        }
        path = self.workspace / f"{attempt_id}-{output.stem}-visual-review.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return "--visual-review-json", path

    def test_qa_stage_contract_must_match_manifest_stage_marker(self):
        from unittest.mock import patch
        from tools.style_pack_manager import make_paths, validated_stage_outputs

        paths = make_paths(self.workspace, self.style_name)
        request_id = "qa-stage-provenance"
        plan_path = paths.generations / "00_PENDING" / request_id / "REFERENCE_PLAN.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan = {"request_id": request_id, "character_id": "NEW", "target_identity_compatibility": {
            "target": {"target_anatomy": "MALE_ANATOMY", "anatomy_evidence_source": "current explicit male prompt"}}}
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        output = self.workspace / "qa-passed-face.png"
        Image.new("RGB", (32, 32), (10, 20, 30)).save(output)
        snapshot = self.workspace / "qa-plan-snapshot.json"
        snapshot.write_text(json.dumps(plan), encoding="utf-8")
        contract = self.workspace / "qa-contract.json"
        contract.write_text(json.dumps({"stage_id": "02_PHYSIQUE_FRONT", "plan_snapshot": str(snapshot)}), encoding="utf-8")
        with paths.generation_manifest.open("w", encoding="utf-8-sig", newline="") as manifest_file:
            fields = ("request_id", "character_id", "status", "style_file", "reference_plan", "notes", "qa_evidence")
            writer = csv.DictWriter(manifest_file, fieldnames=fields)
            writer.writeheader()
            writer.writerow({
                "request_id": request_id, "character_id": "NEW", "status": "STAGING",
                "style_file": str(output), "reference_plan": str(plan_path.resolve()),
                "notes": "[STAGE_ID=01_FACE_IDENTITY]", "qa_evidence": "mocked-valid-receipt",
            })
        def receipt(_row):
            return {"record_status": "STAGING", "qa_failed": [], "qa_contract": str(contract)}
        with patch("tools.style_pack_manager.generation_qa_evidence", side_effect=receipt):
            outputs = validated_stage_outputs(paths, plan_path, plan)
        self.assertNotIn("01_FACE_IDENTITY", outputs)

    def test_newest_rejected_stage_row_blocks_older_qa_passed_output(self):
        from unittest.mock import patch
        from tools.style_pack_manager import make_paths, validated_stage_outputs

        paths = make_paths(self.workspace, self.style_name)
        request_id = "latest-stage-authority"
        plan_path = self.workspace / "REFERENCE_PLAN.json"
        plan_path.write_text(json.dumps({"request_id": request_id, "character_id": "NEW"}), encoding="utf-8")
        older = self.workspace / "older-stage.png"
        newer = self.workspace / "newer-rejected-stage.png"
        Image.new("RGB", (32, 32), (10, 20, 30)).save(older)
        Image.new("RGB", (32, 32), (30, 20, 10)).save(newer)
        with paths.generation_manifest.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=("request_id", "status", "style_file", "reference_plan", "notes", "qa_evidence"))
            writer.writeheader()
            writer.writerow({"request_id": request_id, "status": "STAGING", "style_file": str(older), "reference_plan": str(plan_path.resolve()), "notes": "[STAGE_ID=01_FACE_IDENTITY]", "qa_evidence": "valid"})
            writer.writerow({"request_id": request_id, "status": "REJECTED", "style_file": str(newer), "reference_plan": str(plan_path.resolve()), "notes": "[STAGE_ID=01_FACE_IDENTITY]", "qa_evidence": "corrupt"})

        def receipt(row):
            return {"record_status": "STAGING", "qa_failed": []} if row.get("qa_evidence") == "valid" else None

        with patch("tools.style_pack_manager.generation_qa_evidence", side_effect=receipt):
            outputs = validated_stage_outputs(paths, plan_path, {"request_id": request_id, "character_id": "NEW"})
        self.assertNotIn("01_FACE_IDENTITY", outputs)

    def test_scene_cli_stays_prepared_until_exact_call_and_result_waits_for_delivery(self):
        request_id = "scene-happy"
        guard, plan_path = self._prepare_scene(request_id)
        prepared = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(prepared["gate_status"], "PREPARED_AWAITING_EXECUTABLE_CALL")
        self.assertIsNone(prepared["execution_call"])
        self.assertEqual(prepared["startup_parameter_selection"]["parameter_source"], "DIRECT_USER_CONFIRMED_CURRENT_CHAT")
        self.assertEqual(prepared["generation_workflow"]["mode"], "SINGLE_PASS")
        self.assertNotIn("stages", prepared["generation_workflow"], "SCENE + NONE must not create helper art stages.")

        prompt = "Create one synthetic stone observatory artifact on a plain dark backdrop."
        call = self._resolve_and_prepare_call(request_id, guard, plan_path, prompt)
        ready_plan_bytes = plan_path.read_bytes()
        ready_binding = json.loads(guard.read_text(encoding="utf-8"))["ready_binding"]
        risk_path = call["risk_assessment"]["path"]
        same_call_retry = self._cli(
            "style_pack_manager.py", "prepare-call", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", request_id,
            "--prompt-text", prompt, "--risk-assessment", risk_path,
            "--user-selections-json", self._user_selections_file(request_id, plan_path),
        )
        self.assertIn("unchanged retry", same_call_retry.stdout)
        self.assertEqual(plan_path.read_bytes(), ready_plan_bytes)
        self.assertEqual(json.loads(guard.read_text(encoding="utf-8"))["ready_binding"], ready_binding)
        changed_call = self._cli(
            "style_pack_manager.py", "prepare-call", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", request_id,
            "--prompt-text", prompt + " Change the camera angle.", "--risk-assessment", risk_path,
            "--user-selections-json", self._user_selections_file(request_id, plan_path),
            succeeds=False,
        )
        self.assertTrue(changed_call.stderr.strip())
        self.assertEqual(plan_path.read_bytes(), ready_plan_bytes)
        self.assertEqual(json.loads(guard.read_text(encoding="utf-8"))["ready_binding"], ready_binding)
        attempt_id = self._start_call(guard, plan_path, call)
        # Reproduce the legacy mismatch: the guard stored the purpose label as
        # its stage while the executable call is the canonical SINGLE_PASS stage.
        state = json.loads(guard.read_text(encoding="utf-8"))
        legacy_attempt = state["attempts"][-1]
        legacy_attempt["stage"] = "SCENE"
        legacy_attempt["reference_binding"]["stage"] = "SCENE"
        legacy_attempt["execution_snapshot"]["stage"] = "SCENE"
        legacy_attempt["execution_snapshot"]["guard_stage"] = "SCENE"
        legacy_attempt["execution_snapshot"]["reference_binding"]["stage"] = "SCENE"
        state["active_attempt"] = legacy_attempt
        guard.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = self.workspace / "synthetic-scene.png"
        Image.new("RGB", (720, 1280), (65, 72, 84)).save(result)
        qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-no-unrequested-characters", "PASS",
            "--qa-focal-hierarchy", "PASS", "--qa-lighting", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS", "--qa-depth-and-scale", "PASS", "--qa-artifact-integrity", "PASS",
        )
        recorded = self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", result, "--request-id", request_id,
            "--description", "Synthetic integration scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path,
            "--stage-id", "SCENE", *self._visual_review_args(guard, attempt_id, result), *qa,
        )
        self.assertIn("STATUS=TEST", recorded.stdout)
        state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "ACTIVE")
        self.assertEqual(state["phase"], "RESULT_AVAILABLE")
        snapshot = state["attempts"][-1]["execution_snapshot"]
        self.assertEqual(snapshot["request_id"], request_id)
        self.assertEqual(snapshot["attempt_id"], attempt_id)
        self.assertEqual(snapshot["stage"], "SCENE")
        self.assertEqual(snapshot["guard_stage"], "SCENE")
        self.assertEqual(snapshot["request_purpose"], "SCENE")
        self.assertEqual(snapshot["execution_stage"], "SINGLE_PASS")
        self.assertEqual(snapshot["provider_artifact"]["sha256"], hashlib.sha256(result.read_bytes()).hexdigest())
        self.assertTrue(snapshot["execution_call_sha256"])
        self.assertIsNone(state["delivered_result_at"])
        self.assertNotEqual(state["status"], "COMPLETE")

        retry = self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", result, "--request-id", request_id,
            "--description", "Synthetic integration scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path,
            "--stage-id", "SCENE", *self._visual_review_args(guard, attempt_id, result), *qa,
        )
        self.assertIn("UNCHANGED_RETRY=true", retry.stdout)
        state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(len(state["available_results"]), 1)
        state["attempts"][-1]["execution_snapshot"]["task_revision"] = 99
        guard.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tampered = self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", result, "--request-id", request_id,
            "--description", "Synthetic integration scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path,
            "--stage-id", "SCENE", *self._visual_review_args(guard, attempt_id, result), *qa, succeeds=False,
        )
        self.assertIn("executed-call snapshot", tampered.stderr)

    def test_record_generation_keeps_original_artifact_when_visual_qa_is_missing(self):
        from tools import style_pack_manager as manager

        request_id = "scene-artifact-before-qa"
        guard, plan_path = self._prepare_scene(request_id)
        call = self._resolve_and_prepare_call(request_id, guard, plan_path, "Create one synthetic observatory scene.")
        attempt_id = self._start_call(guard, plan_path, call)
        output = self.workspace / "unreviewed-original.png"
        Image.new("RGB", (720, 1280), (45, 70, 95)).save(output)
        original_hash = hashlib.sha256(output.read_bytes()).hexdigest()
        failed = self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", output, "--request-id", request_id,
            "--description", "Synthetic integration scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path,
            "--visual-review-json", self.workspace / "missing-visual-review.json",
            succeeds=False,
        )
        self.assertIn("visual", failed.stderr.lower())
        state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertIn("provider_artifact", state["active_attempt"], failed.stderr + "\n" + json.dumps(state["events"][-3:]))
        artifact = state["active_attempt"]["provider_artifact"]
        self.assertEqual(artifact["path"], str(output.resolve()))
        self.assertEqual(artifact["sha256"], original_hash)
        self.assertEqual(artifact["result_status"], "NOT_CHECKED")
        self.assertEqual(state["next_required_action"], "REVIEW_AND_REGISTER_EXISTING_ARTIFACT")
        self.assertEqual(state["status"], "ACTIVE")
        self.assertFalse(any(row["request_id"] == request_id for row in manager.read_csv(self.generations / "GENERATION_MANIFEST.csv")))

    def test_record_generation_recovers_checkpoint_failure_without_duplicate_record(self):
        from unittest.mock import patch
        from tools import style_pack_manager as manager

        request_id = "scene-checkpoint-retry"
        guard, plan_path = self._prepare_scene(request_id)
        call = self._resolve_and_prepare_call(request_id, guard, plan_path, "Create one synthetic observatory scene.")
        attempt_id = self._start_call(guard, plan_path, call)
        output = self.workspace / "checkpoint-original.png"
        Image.new("RGB", (720, 1280), (41, 61, 81)).save(output)
        qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-no-unrequested-characters", "PASS",
            "--qa-focal-hierarchy", "PASS", "--qa-lighting", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS", "--qa-depth-and-scale", "PASS", "--qa-artifact-integrity", "PASS",
        )
        args = manager.build_parser().parse_args([
            "record-generation", "--workspace", str(self.workspace), "--style-name", self.style_name,
            "--image", str(output), "--request-id", request_id, "--description", "Synthetic checkpoint retry",
            "--fidelity", "70", "--status", "TEST", "--character-id", "NONE",
            "--attempt-id", attempt_id, "--reference-plan", str(plan_path),
            *(str(part) for part in self._visual_review_args(guard, attempt_id, output)), *qa,
        ])
        checkpoint = manager.execution_checkpoint
        failed_once = False

        def fail_visible_once(*call_args, **kwargs):
            nonlocal failed_once
            if kwargs.get("event") == "VISIBLE_RESULT" and not failed_once:
                failed_once = True
                raise manager.ExecutionGuardError("injected one-time checkpoint failure")
            return checkpoint(*call_args, **kwargs)

        with patch.object(manager, "execution_checkpoint", side_effect=fail_visible_once):
            with self.assertRaisesRegex(manager.StylePackError, "visible-result checkpoint failed"):
                manager.command_record_generation(args)
        rows = [row for row in manager.read_csv(self.generations / "GENERATION_MANIFEST.csv") if row["request_id"] == request_id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(guard.read_text(encoding="utf-8"))["active_attempt"]["status"], "ACTIVE")
        manager.command_record_generation(args)
        after = [row for row in manager.read_csv(self.generations / "GENERATION_MANIFEST.csv") if row["request_id"] == request_id]
        self.assertEqual(after, rows)
        state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "RESULT_AVAILABLE")
        self.assertEqual(state["available_results"][-1]["evidence"], [rows[0]["archive_file"]])

    def test_visible_old_revision_registers_after_correction_without_satisfying_current_revision(self):
        request_id = "scene-corrected-after-visible"
        guard, plan_path = self._prepare_scene(request_id)
        call = self._resolve_and_prepare_call(request_id, guard, plan_path, "Create one synthetic observatory scene.")
        attempt_id = self._start_call(guard, plan_path, call)
        result = self.workspace / "old-revision-scene.png"
        Image.new("RGB", (720, 1280), (65, 72, 84)).save(result)
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "VISIBLE_RESULT",
            "--summary", "The provider returned the original scene.", "--attempt-id", attempt_id,
            "--result-status", "TEST", "--evidence", result,
        )
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "USER_CORRECTION",
            "--summary", "Revise the scene genre after reviewing the visible result.",
            "--correction-impact", "PRESERVE",
        )
        (self.workspace / f"{request_id}-SINGLE_PASS-risk.json").unlink()
        corrected_call = self._resolve_and_prepare_call(
            request_id, guard, plan_path, "Revise the observatory scene as a winter folklore genre composition.",
        )
        self.assertEqual(corrected_call["prompt"]["text"], "Revise the observatory scene as a winter folklore genre composition.")
        corrected_state = json.loads(guard.read_text(encoding="utf-8"))
        executed_snapshot = corrected_state["attempts"][-1]["execution_snapshot"]
        self.assertNotEqual(hashlib.sha256(plan_path.read_bytes()).hexdigest(), executed_snapshot["reference_binding"]["sha256"])
        immutable_plan = Path(executed_snapshot["reference_plan_snapshot_path"])
        self.assertEqual(hashlib.sha256(immutable_plan.read_bytes()).hexdigest(), executed_snapshot["reference_plan_snapshot_sha256"])
        qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-no-unrequested-characters", "PASS",
            "--qa-focal-hierarchy", "PASS", "--qa-lighting", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS", "--qa-depth-and-scale", "PASS", "--qa-artifact-integrity", "PASS",
        )
        common = (
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", result, "--request-id", request_id,
            "--description", "Original visible scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path,
            *self._visual_review_args(guard, attempt_id, result), *qa,
        )
        recorded = self._cli(*common)
        self.assertIn("STATUS=TEST", recorded.stdout)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            old_row = next(row for row in csv.DictReader(manifest_file) if row["request_id"] == request_id)
        self.assertIn("[TASK_REVISION=0]", old_row["notes"])
        results_dir = self.workspace / "GENERATION_RESULTS"
        results_before_conflict = set(results_dir.iterdir())
        conflicting_retry = list(common)
        conflicting_retry[conflicting_retry.index("--status") + 1] = "REJECTED"
        conflict = self._cli(*conflicting_retry, succeeds=False)
        self.assertIn("different or unverifiable", conflict.stderr)
        self.assertEqual(set(results_dir.iterdir()), results_before_conflict)
        old_revision_delivery = self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "RESULT_DELIVERED",
            "--summary", "Attempt to deliver old revision.", "--attempt-id", attempt_id,
            "--delivery-evidence", str(result), succeeds=False,
        )
        self.assertIn("earlier task revision", old_revision_delivery.stderr)
        wrong_artifact = self.workspace / "wrong-old-scene.png"
        Image.new("RGB", (720, 1280), (7, 8, 9)).save(wrong_artifact)
        wrong_image_args = list(common)
        wrong_image_args[wrong_image_args.index("--image") + 1] = wrong_artifact
        rejected_image = self._cli(*wrong_image_args, succeeds=False)
        self.assertIn("exact path recorded", rejected_image.stderr)
        wrong_request_args = list(common)
        wrong_request_args[wrong_request_args.index("--request-id") + 1] = "another-request"
        rejected_request = self._cli(*wrong_request_args, succeeds=False)
        self.assertIn("execution guard", rejected_request.stderr)
        state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(state["task_revision"], 1)
        self.assertEqual(state["status"], "READY")
        self.assertEqual(state["phase"], "READY_FOR_EXECUTION")
        self.assertEqual(state["next_required_action"], "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER")
        self.assertIsNone(state["delivered_result_at"])
        self.assertIsNone(state["active_attempt"])

    def test_reconciled_available_result_records_once_and_rejects_stale_evidence(self):
        request_id = "reconciled-available"
        guard, plan_path = self._prepare_scene(request_id)
        call = self._resolve_and_prepare_call(request_id, guard, plan_path, "Create one reconciled synthetic artifact.")
        attempt_id = self._start_call(guard, plan_path, call)
        result = self.workspace / "reconciled-artifact.png"
        Image.new("RGB", (720, 1280), (65, 72, 84)).save(result)
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "ATTEMPT_UNKNOWN",
            "--summary", "Synthetic provider timeout.", "--attempt-id", attempt_id,
        )
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "ATTEMPT_RECONCILED",
            "--summary", "Synthetic provider receipt confirms the output exists.", "--attempt-id", attempt_id,
            "--outcome", "AVAILABLE", "--result-status", "TEST", "--evidence", result,
            "--reconciliation-evidence", "synthetic-provider-receipt-001",
        )
        fixed_evidence_hash = hashlib.sha256(result.read_bytes()).hexdigest()
        reconciled_state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(reconciled_state["available_results"][-1]["evidence_sha256"], [fixed_evidence_hash])
        original_result = result.read_bytes()
        Image.new("RGB", (720, 1280), (9, 8, 7)).save(result)
        qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-no-unrequested-characters", "PASS",
            "--qa-focal-hierarchy", "PASS", "--qa-lighting", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS", "--qa-depth-and-scale", "PASS", "--qa-artifact-integrity", "PASS",
        )
        stale = self.workspace / "stale-reconciled-artifact.png"
        Image.new("RGB", (720, 1280), (12, 14, 18)).save(stale)
        rejected = self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", stale, "--request-id", request_id,
            "--description", "Synthetic stale evidence", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path,
            *self._visual_review_args(guard, attempt_id, result), *qa,
            succeeds=False,
        )
        self.assertIn("exact path recorded", rejected.stderr)
        changed_same_path = self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", result, "--request-id", request_id,
            "--description", "Synthetic changed reconciled scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path,
            *self._visual_review_args(guard, attempt_id, result), *qa,
            succeeds=False,
        )
        self.assertIn("exact path recorded", changed_same_path.stderr)
        result.write_bytes(original_result)
        common = (
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", result, "--request-id", request_id,
            "--description", "Synthetic reconciled scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path,
            *self._visual_review_args(guard, attempt_id, result), *qa,
        )
        first = self._cli(*common)
        retry = self._cli(*common)
        self.assertIn("STATUS=TEST", first.stdout)
        self.assertIn("UNCHANGED_RETRY=true", retry.stdout)
        state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "ATTEMPT_AVAILABLE")
        self.assertEqual(len(state["available_results"]), 1)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = [row for row in csv.DictReader(stream) if row["request_id"] == request_id]
        self.assertEqual(len(rows), 1)

    def test_wrong_request_attempt_stage_character_and_low_fidelity_missing_qa_are_rejected(self):
        prompt = "Create one synthetic stone observatory artifact on a plain dark backdrop."
        semantic_qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-no-unrequested-characters", "PASS",
            "--qa-focal-hierarchy", "PASS", "--qa-lighting", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS", "--qa-depth-and-scale", "PASS", "--qa-artifact-integrity", "PASS",
        )
        def start_negative(request_id: str):
            guard, plan_path = self._prepare_scene(request_id, fidelity=30)
            call = self._resolve_and_prepare_call(request_id, guard, plan_path, prompt)
            attempt_id = self._start_call(guard, plan_path, call)
            output = self.workspace / f"{request_id}-output.png"
            Image.new("RGB", (120, 180), (20, 24, 28)).save(output)
            common: list[object] = [
                "record-generation", "--workspace", self.workspace, "--style-name", self.style_name,
                "--image", output, "--description", "Synthetic negative fixture", "--fidelity", "30",
                "--risk-level", self._risk_level(plan_path), "--character-id", "NONE",
                "--attempt-id", attempt_id, "--reference-plan", plan_path,
                *self._visual_review_args(guard, attempt_id, output),
            ]
            return guard, common

        # Each failing result can archive its attempt, so exercise each contract
        # against a fresh request rather than chaining state-mutating failures.
        guard, common = start_negative("negative-request")
        results_dir = self.workspace / "GENERATION_RESULTS"
        results_before_failure = set(results_dir.iterdir()) if results_dir.exists() else set()
        pending_before_risk_failure = set((self.generations / "00_PENDING" / "negative-request").iterdir())
        self._cli("style_pack_manager.py", *common, "--request-id", "wrong-request", succeeds=False)
        self.assertEqual(set(results_dir.iterdir()) if results_dir.exists() else set(), results_before_failure)
        conflicting_risk_description = list(common)
        description_index = conflicting_risk_description.index("--description") + 1
        conflicting_risk_description[description_index] = "Synthetic negative fixture [D10]"
        risk_marker = self._cli(
            "style_pack_manager.py", *conflicting_risk_description,
            "--request-id", "negative-request", *semantic_qa, succeeds=False,
        )
        self.assertIn("Description risk marker conflicts", risk_marker.stderr)
        self.assertEqual(set(results_dir.iterdir()) if results_dir.exists() else set(), results_before_failure)
        self.assertEqual(set((self.generations / "00_PENDING" / "negative-request").iterdir()), pending_before_risk_failure)
        mismatch = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-request", "--character-id", "CHAR_001",
            succeeds=False,
        )
        self.assertIn("does not match the prepared plan", mismatch.stderr)
        mismatch_state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertIsNone(mismatch_state["delivered_result_at"])
        self.assertIsNotNone(mismatch_state["active_attempt"], "A metadata correction must not consume the active attempt.")
        corrected = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-request", *semantic_qa,
        )
        self.assertIn("STATUS=TEST", corrected.stdout)
        corrected_state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(corrected_state["attempts"][-1]["attempt_id"], common[common.index("--attempt-id") + 1])
        self.assertEqual(corrected_state["phase"], "RESULT_AVAILABLE")

        _, common = start_negative("negative-attempt")
        wrong_attempt = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-attempt", "--attempt-id", "wrong-attempt",
            succeeds=False,
        )
        self.assertTrue(wrong_attempt.stderr.strip())

        _, common = start_negative("negative-stage")
        wrong_stage = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-stage", "--stage-id", "WRONG_STAGE",
            succeeds=False,
        )
        self.assertTrue(wrong_stage.stderr.strip())

        guard, common = start_negative("negative-low-fidelity-qa")
        results_before_failure = set(results_dir.iterdir()) if results_dir.exists() else set()
        low_fidelity = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-low-fidelity-qa", succeeds=False,
        )
        self.assertIn("Required post-generation QA was not performed", low_fidelity.stderr)
        self.assertEqual(set(results_dir.iterdir()) if results_dir.exists() else set(), results_before_failure)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            rows = list(csv.DictReader(manifest_file))
        self.assertFalse(any(row["request_id"] == "negative-low-fidelity-qa" for row in rows))
        self.assertIsNotNone(json.loads(guard.read_text(encoding="utf-8"))["active_attempt"])
        retried = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-low-fidelity-qa", *semantic_qa,
        )
        self.assertIn("STATUS=TEST", retried.stdout)
        final_state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(final_state["attempts"][-1]["attempt_id"], common[common.index("--attempt-id") + 1])
        self.assertEqual(final_state["phase"], "RESULT_AVAILABLE")

    def test_visual_review_is_required_hash_bound_and_failure_is_preserved(self):
        from tools import style_pack_manager as manager

        request_id = "visual-review-required"
        prompt = "Create one synthetic stone observatory artifact on a plain dark backdrop."
        guard, plan_path = self._prepare_scene(request_id, fidelity=30)
        call = self._resolve_and_prepare_call(request_id, guard, plan_path, prompt)
        attempt_id = self._start_call(guard, plan_path, call)
        output = self.workspace / "visual-review-result.png"
        Image.new("RGB", (120, 180), (20, 24, 28)).save(output)
        review_args = self._visual_review_args(guard, attempt_id, output)
        semantic_qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-no-unrequested-characters", "PASS",
            "--qa-focal-hierarchy", "PASS", "--qa-lighting", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS", "--qa-depth-and-scale", "PASS", "--qa-artifact-integrity", "PASS",
        )
        base = (
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", output, "--request-id", request_id,
            "--description", "Synthetic visual review test", "--fidelity", "30", "--status", "TEST",
            "--risk-level", self._risk_level(plan_path), "--character-id", "NONE",
            "--attempt-id", attempt_id, "--reference-plan", plan_path,
        )
        missing = self._cli(*base, *semantic_qa, succeeds=False)
        self.assertIn("visual-review-json", missing.stderr)
        review_path = review_args[1]
        report = json.loads(review_path.read_text(encoding="utf-8"))
        report["prompt_sha256"] = "0" * 64
        review_path.write_text(json.dumps(report), encoding="utf-8")
        tampered = self._cli(*base, *review_args, *semantic_qa, succeeds=False)
        self.assertIn("prompt_sha256 does not match", tampered.stderr)
        report["prompt_sha256"] = json.loads(self._visual_review_args(guard, attempt_id, output)[1].read_text(encoding="utf-8"))["prompt_sha256"]
        report["visible_defect_review"] = {
            "status": "FAIL", "checked_scope": "Entire synthetic output at full resolution.",
            "findings": [{"location": "center", "issue": "fixture artifact"}],
        }
        review_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        rejected = self._cli(*base, *review_args, *semantic_qa)
        self.assertIn("STATUS=REJECTED", rejected.stdout)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as stream:
            row = next(row for row in csv.DictReader(stream) if row["request_id"] == request_id)
        receipt = manager.generation_qa_evidence(row)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["record_status"], "REJECTED")
        self.assertEqual(receipt["visual_review"]["visible_defect_review"]["findings"], report["visible_defect_review"]["findings"])
        self.assertIn("VISIBLE_DEFECTS", receipt["qa_failed"])

    def test_visual_review_anatomy_not_applicable_requires_absence_and_rejects_body_plans(self):
        from tools.style_pack_manager import StylePackError, prompt_review_clauses, validate_visual_review

        image = self.workspace / "visual-na-fixture.png"
        Image.new("RGB", (10, 10), (0, 0, 0)).save(image)
        prompt_text = "Draw a stone observatory without people, with a red roof and blue windows."
        plan_sha = "b" * 64
        snapshot = {
            "execution_call": {"prompt": {"text": prompt_text}},
            "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
            "reference_plan_snapshot_sha256": plan_sha,
        }
        report = {
            "schema_version": 1, "request_id": "na-fixture", "attempt_id": "attempt-na",
            "task_revision": 0, "output_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "prompt_sha256": snapshot["prompt_sha256"], "executed_plan_sha256": plan_sha,
            "author": "fixture reviewer",
            "anatomy_review": {"status": "NOT_APPLICABLE", "no_visible_anatomy": True, "reason": "No human or animal anatomy is visible anywhere in this image."},
            "visible_defect_review": {"status": "PASS", "checked_scope": "Entire image at full resolution.", "findings": []},
            "prompt_adherence": {"status": "PASS", "checked_scope": "FULL_EXECUTED_PROMPT", "all_explicit_constraints_assessed": True, "constraints": [{"constraint": prompt_text, "status": "PASS", "evidence": "One broad checklist item is intentionally insufficient."}]},
        }
        path = self.workspace / "visual-na-fixture.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        common = {
            "review_path": path, "request_id": "na-fixture", "attempt_id": "attempt-na",
            "task_revision": 0, "output": image, "execution_snapshot": snapshot,
            "stage_id": "SINGLE_PASS",
        }
        with self.assertRaisesRegex(StylePackError, "every explicit constraint"):
            validate_visual_review(plan={}, **common)
        report["prompt_adherence"]["constraints"] = [
            {"constraint": clause, "status": "PASS", "evidence": f"Reviewed clause: {clause}"}
            for clause in prompt_review_clauses(prompt_text)
        ]
        path.write_text(json.dumps(report), encoding="utf-8")
        self.assertEqual(validate_visual_review(plan={}, **common)["anatomy_review"]["status"], "NOT_APPLICABLE")
        report["anatomy_review"]["no_visible_anatomy"] = False
        path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(StylePackError, "no_visible_anatomy"):
            validate_visual_review(plan={}, **common)
        report["anatomy_review"]["no_visible_anatomy"] = True
        path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(StylePackError, "full-body"):
            validate_visual_review(plan={"canvas_contract": {"full_figure": True}}, **common)
        with self.assertRaisesRegex(StylePackError, "visible character or face"):
            validate_visual_review(plan={"scene_contract": {"has_character": True}}, **common)
        with self.assertRaisesRegex(StylePackError, "visible character or face"):
            validate_visual_review(plan={"face_review": {"face_visible": True}}, **common)

    def test_record_generation_recovers_exact_already_visible_output(self):
        request_id = "visible-before-record"
        guard, plan_path = self._prepare_scene(request_id, fidelity=30)
        prompt = "Create one synthetic stone observatory artifact on a plain dark backdrop."
        call = self._resolve_and_prepare_call(request_id, guard, plan_path, prompt)
        attempt_id = self._start_call(guard, plan_path, call)
        provider_output = self.workspace / "provider-output.png"
        Image.new("RGB", (240, 320), (65, 72, 84)).save(provider_output)

        from tools import style_pack_manager as manager

        paths = manager.make_paths(self.workspace, self.style_name)
        archive = manager.archive_generation(paths, provider_output, "already visible fixture")
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "VISIBLE_RESULT",
            "--summary", "The exact provider image is already visible.", "--attempt-id", attempt_id,
            "--result-status", "TEST", "--evidence", provider_output,
        )
        state_before = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(state_before["attempts"][-1]["status"], "RESULT_AVAILABLE")
        self.assertEqual(state_before["attempts"][-1]["result_evidence"], [str(provider_output.resolve())])

        wrong_archive = self.workspace / "GENERATION_RESULTS" / "different-content.png"
        wrong_archive.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (240, 320), (12, 34, 56)).save(wrong_archive)

        qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-no-unrequested-characters", "PASS",
            "--qa-focal-hierarchy", "PASS", "--qa-lighting", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS", "--qa-depth-and-scale", "PASS", "--qa-artifact-integrity", "PASS",
        )
        record_args = (
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", request_id,
            "--description", "Synthetic visible-result recovery", "--fidelity", "30", "--status", "TEST",
            "--risk-level", self._risk_level(plan_path), "--character-id", "NONE",
            "--attempt-id", attempt_id, "--reference-plan", plan_path,
            *self._visual_review_args(guard, attempt_id, provider_output), *qa,
        )
        rejected = self._cli(
            *record_args, "--image", wrong_archive, succeeds=False,
        )
        self.assertTrue(rejected.stderr.strip())
        self.assertEqual(len(list((self.workspace / "GENERATION_RESULTS").iterdir())), 2)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            self.assertFalse(any(row["request_id"] == request_id for row in csv.DictReader(manifest_file)))

        recorded = self._cli(*record_args, "--image", archive)
        self.assertIn("STATUS=TEST", recorded.stdout)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            rows = [row for row in csv.DictReader(manifest_file) if row["request_id"] == request_id]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(Path(row["archive_file"]).resolve(), archive.resolve())
        self.assertTrue(Path(row["style_file"]).is_file())
        self.assertTrue(row["qa_evidence"])
        self.assertIsNotNone(manager.generation_qa_evidence(row))
        self.assertEqual(len(list((self.workspace / "GENERATION_RESULTS").iterdir())), 2)
        state_after = json.loads(guard.read_text(encoding="utf-8"))
        visible_events = [event for event in state_after["events"] if event.get("event") == "VISIBLE_RESULT"]
        self.assertEqual(len(visible_events), 1)

    def test_all_five_canonical_stages_resolve_packs_pass_qa_and_complete(self):
        self._run_multistage_character_base(child=False)

    def test_child_boy_five_stage_cli_approval_profile_and_resolver(self):
        self._run_multistage_character_base(child=True, anatomy="MALE_ANATOMY")

    def test_child_girl_five_stage_cli_approval_profile_and_resolver(self):
        self._run_multistage_character_base(child=True, anatomy="FEMALE_ANATOMY")

    def _run_multistage_character_base(self, *, child: bool, anatomy: str = ""):
        request_id = "multistage-child" if child else "multistage-continuation"
        subject = "boy" if anatomy == "MALE_ANATOMY" else "girl"
        required_garment = (
            "opaque age-appropriate T-shirt and shorts" if anatomy == "MALE_ANATOMY"
            else "opaque age-appropriate T-shirt and skirt" if anatomy == "FEMALE_ANATOMY" else ""
        )
        if child:
            self.primary_face = self._classify(
                f"child-{subject}-primary.png", "CHARACTER_FACE", (175, 115, 105),
                subject_label="MALE" if anatomy == "MALE_ANATOMY" else "FEMALE",
            )
            self.supporting_face = self._classify(
                f"child-{subject}-support.png", "CHARACTER_FACE", (165, 108, 98),
                subject_label="MALE" if anatomy == "MALE_ANATOMY" else "FEMALE",
            )
        stages = (
            "01_FACE_IDENTITY", "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE",
            "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY",
        )
        guard = self._start_request(request_id, required_stages=stages if child else ())
        prep_args: list[object] = [
            "prepare-generation", "--workspace", self.workspace, "--style-name", self.style_name,
            "--request-id", request_id, "--fidelity", "70", "--confirmed-chat-id", "integration-chat",
            "--startup-selection-mode", "DIRECT_CONFIRMATION", "--aux-body-decision", "DECLINED",
            "--confirmed-message-id", "integration-message",
            "--confirmed-parameters-user-quote", "Keep 70% fidelity and decline BODY_REFERENCE_LIBRARY.",
            "--generation-purpose", "CHARACTER_BASE", "--character-id", "NEW", "--character-name", f"Synthetic {subject.title()}" if child else "Synthetic",
            "--character-age-group", "CHILD" if child else "ADULT",
            *(() if child else ("--adult-character",)),
            "--target-prompt", f"Synthetic {subject.title()} is a child {subject}." if child else "Synthetic is an adult male character.",
            "--orientation", "PORTRAIT", "--framing", "FULL_BODY", "--target-pose-family", "STANDING",
            "--dominant-body-source", "PROMPT_BODY_SPEC", "--body-source-coverage", "FULL_BODY",
            "--body-source-pose-family", "STANDING", "--body-height-heads", "CHILD_AGE_APPROPRIATE" if child else "7.2-7.5",
            "--body-silhouette-notes", "Synthetic stable proportions for test fixture.", "--prompt-only-physique",
            "--style-reference", self.style_reference, "--primary-face", self.primary_face,
            "--supporting-face", self.supporting_face, "--reviewed", "STYLE=1",
            "--reviewed-source", review_declaration("STYLE", "STYLE", self.style_reference),
            "--reviewed-source", review_declaration("STYLE", "PRIMARY_FACE", self.primary_face),
            "--reviewed-source", review_declaration("STYLE", "SUPPORTING_FACE", self.supporting_face),
            "--reviewed", "FACE=2",
            "--override", "CLOTHES", "--override", "LIGHTING", "--override", "BACKGROUND", "--override", "COMPOSITION",
            "--user-reference-evidence-json", self._user_reference_evidence_file("integration-message"),
        ]
        self._cli("style_pack_manager.py", *prep_args)
        plan_path = guard.parent / "REFERENCE_PLAN.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["generation_workflow"]["mode"], "MULTI_STAGE")
        self.assertEqual(len(plan["generation_workflow"]["stages"]), 5)
        if child:
            contract = plan["character_base_contract"]
            self.assertEqual(contract["age_group"], "CHILD")
            self.assertEqual(contract["subject_anatomy"], anatomy)
            self.assertEqual(contract["required_garment"], required_garment)
            height = plan["body_proportion_contract"]["height_in_heads"]
            self.assertEqual(height["mode"], "CHILD_AGE_APPROPRIATE")
            self.assertIsNone(height["numeric_head_count"])
            self.assertFalse(height["adult_anthropometric_bounds"])
            self.assertNotIn("bust_volume", plan["body_proportion_contract"]["locked_measurements"])
            from tools import style_pack_manager as manager
            with self.assertRaisesRegex(manager.StylePackError, "must explicitly require"):
                manager.validate_character_base_executable_prompt(
                    f"A child {subject} in the wrong swimsuit.", contract,
                )
            incomplete_qa = Namespace(
                stage_id="02_PHYSIQUE_FRONT", qa_attachments="PASS", qa_canvas="PASS", qa_stage_layer="PASS",
                qa_face="PASS", qa_body_silhouette="PASS", qa_body_proportions="PASS", qa_limb_proportions="PASS",
                limb_qa_evidence="age_group=CHILD;view=FRONT;proportions_reviewed=PASS", visual_anatomy_status="PASS",
                visual_defects_status="PASS", visual_prompt_status="PASS", qa_view="PASS", qa_safe_coverage="NOT_CHECKED",
                qa_clothing_topology="PASS", qa_clothing="PASS", qa_style="PASS", qa_body_style="PASS",
            )
            with self.assertRaisesRegex(manager.StylePackError, "SAFE_COVERAGE"):
                manager.evaluate_generation_qa(plan, incomplete_qa)

        face_prompt = (
            f"Resolve the synthetic child {subject} face in the selected drawing style; identity kit garment is {required_garment}."
            if child else "Resolve the synthetic adult character face in the selected drawing style."
        )
        face_call = self._resolve_and_prepare_call(request_id, guard, plan_path, face_prompt, "01_FACE_IDENTITY")
        ready_state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual([row["id"] for row in ready_state["required_stages"]], list(stages))
        face_attempt = self._start_call(guard, plan_path, face_call)
        face_output = self.workspace / "face-stage.png"
        Image.new("RGB", (512, 768), (185, 125, 112)).save(face_output)
        self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", face_output, "--request-id", request_id,
            "--description", "Synthetic face stage", "--fidelity", "70", "--status", "STAGING",
            "--character-id", "NEW", "--attempt-id", face_attempt, "--reference-plan", plan_path,
            *self._visual_review_args(guard, face_attempt, face_output),
            "--stage-id", "01_FACE_IDENTITY", "--qa-attachments", "PASS", "--qa-canvas", "PASS",
            "--qa-stage-layer", "PASS", "--qa-face", "PASS", "--qa-expression", "PASS",
            "--qa-style", "PASS", "--qa-neutral-backdrop", "PASS",
        )
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            recorded_rows = list(csv.DictReader(manifest_file))
        self.assertEqual(recorded_rows[-1]["status"], "STAGING")
        self.assertTrue(recorded_rows[-1]["qa_evidence"])
        copied_face = Path(recorded_rows[-1]["style_file"])
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "STAGE_COMPLETED",
            "--summary", "Synthetic first stage has a durable QA-passed record.",
            "--stage", "01_FACE_IDENTITY", "--evidence", copied_face,
        )

        front_prompt = (
            f"Project the verified face into the canonical full-body front view wearing {required_garment}."
            if child else "Project the verified face into the canonical full-body front view."
        )
        front_call = self._resolve_and_prepare_call(request_id, guard, plan_path, front_prompt, "02_PHYSIQUE_FRONT")
        face_binding = next(binding for binding in front_call["stage_output_bindings"] if binding["stage_id"] == "01_FACE_IDENTITY")
        self.assertEqual(face_binding["sha256"], hashlib.sha256(copied_face.read_bytes()).hexdigest())
        manifest_path = self.generations / "GENERATION_MANIFEST.csv"
        original_manifest = manifest_path.read_bytes()
        with manifest_path.open(encoding="utf-8-sig", newline="") as manifest_file:
            rows = list(csv.DictReader(manifest_file))
            fields = list(rows[0].keys())
        stale_row = dict(rows[-1])
        stale_row["status"] = "REJECTED"
        stale_row["qa_evidence"] = "corrupt receipt"
        stale_row["style_file"] = str(self.workspace / "newer-rejected-face.png")
        Image.new("RGB", (32, 32), (1, 2, 3)).save(stale_row["style_file"])
        with manifest_path.open("w", encoding="utf-8-sig", newline="") as manifest_file:
            writer = csv.DictWriter(manifest_file, fieldnames=fields)
            writer.writeheader()
            writer.writerows([*rows, stale_row])
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "EXECUTION_STARTED",
            "--summary", "Synthetic multi-stage call started.", "--reference-plan", plan_path,
            "--stage", front_call["stage_id"], "--output-contract", "REQUESTED_DELIVERABLE",
            "--swimwear-rung", "TWO_PIECE", succeeds=False,
        )
        manifest_path.write_bytes(original_manifest)
        front_attempt = self._start_call(guard, plan_path, front_call)
        front_output = self.workspace / "front-stage.png"
        Image.new("RGB", (720, 1280), (145, 118, 104)).save(front_output)
        limb_evidence = (
            f"age_group=CHILD;view=FRONT;proportions_reviewed=PASS"
            if child else
            "source=PROMPT_BODY_SPEC;view=FRONT;head_units=7.3;pubic_height_fraction=0.5;hip_knee=1.0;"
            "knee_ankle=1.0;ankle_width=0.2;foot_length=1.0;foot_pose=FLAT;foot_length_mode=MEASURED;"
            "hip_landmark=FEMORAL_HEAD_CENTER;knee_landmark=KNEE_JOINT_CENTER;ankle_landmark=TALOCRURAL_JOINT_CENTER;"
            "landmark_confidence=0.95;crown_landmark=CRANIAL_VERTEX;crown_confidence=0.95;"
            "heel_endpoint=VISIBLE;toe_endpoint=VISIBLE;neck_head_ratio=0.3;neck_jaw_ratio=0.7"
        )
        self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", front_output, "--request-id", request_id,
            "--description", "Synthetic front physique stage", "--fidelity", "70", "--status", "STAGING",
            "--character-id", "NEW", "--attempt-id", front_attempt, "--reference-plan", plan_path,
            *self._visual_review_args(guard, front_attempt, front_output),
            "--stage-id", "02_PHYSIQUE_FRONT", "--qa-attachments", "PASS", "--qa-canvas", "PASS",
            "--qa-stage-layer", "PASS", "--qa-face", "PASS", "--qa-body-silhouette", "PASS",
            "--qa-body-proportions", "PASS", "--qa-limb-proportions", "PASS",
            "--limb-qa-evidence", limb_evidence,
            "--qa-view", "PASS", "--qa-safe-coverage", "PASS", "--qa-clothing-topology", "PASS",
            *( ("--qa-clothing", "PASS") if child else () ),
            "--qa-style", "PASS", "--qa-body-style", "PASS",
        )
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            front_rows = list(csv.DictReader(manifest_file))
        self.assertEqual(front_rows[-1]["status"], "STAGING")
        copied_front = Path(front_rows[-1]["style_file"])
        front_completion: list[object] = [
            "checkpoint", "--state", guard, "--event", "STAGE_COMPLETED",
            "--summary", "Synthetic front stage has a durable QA-passed record.",
            "--stage", "02_PHYSIQUE_FRONT", "--evidence", copied_front,
        ]
        if not child:
            front_completion.extend(("--swimwear-rung", "TWO_PIECE", "--observed-topology", "TWO_PIECE"))
        self._cli("task_execution_guard.py", *front_completion)

        side_prompt = (
            f"Create the canonical side view wearing {required_garment}; preserve the verified face and child proportions."
            if child else "Create the canonical side view while preserving the verified face and front proportions."
        )
        side_call = self._resolve_and_prepare_call(request_id, guard, plan_path, side_prompt, "03_PHYSIQUE_SIDE")
        self.assertEqual(len(side_call["targeted_pack_bindings"]), 1)
        pack_binding = side_call["targeted_pack_bindings"][0]
        manifest = json.loads(Path(pack_binding["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(pack_binding["request_id"], request_id)
        self.assertEqual([source["sha256"] for source in manifest["sources"]], [
            hashlib.sha256(copied_face.read_bytes()).hexdigest(),
            hashlib.sha256(copied_front.read_bytes()).hexdigest(),
        ])
        self.assertTrue(Path(pack_binding["path"]).is_file())
        self.assertTrue(all(Path(source["path"]).is_file() for source in manifest["sources"]))
        self.assertEqual(json.loads(guard.read_text(encoding="utf-8"))["status"], "READY")

        side_attempt = self._start_call(guard, plan_path, side_call)
        side_output = self.workspace / "side-stage.png"
        Image.new("RGB", (720, 1280), (144, 116, 102)).save(side_output)
        side_limb_evidence = limb_evidence.replace("view=FRONT;", "view=SIDE;")
        self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", side_output, "--request-id", request_id,
            "--description", "Synthetic side physique stage", "--fidelity", "70", "--status", "STAGING",
            "--character-id", "NEW", "--attempt-id", side_attempt, "--reference-plan", plan_path,
            *self._visual_review_args(guard, side_attempt, side_output),
            "--stage-id", "03_PHYSIQUE_SIDE", "--qa-attachments", "PASS", "--qa-canvas", "PASS",
            "--qa-stage-layer", "PASS", "--qa-face", "PASS", "--qa-body-silhouette", "PASS",
            "--qa-body-proportions", "PASS", "--qa-limb-proportions", "PASS", "--limb-qa-evidence", side_limb_evidence,
            "--qa-view", "PASS", "--qa-safe-coverage", "PASS", "--qa-clothing-topology", "PASS",
            *( ("--qa-clothing", "PASS") if child else () ),
            "--qa-multiview-consistency", "PASS", "--qa-style", "PASS", "--qa-body-style", "PASS",
        )
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            side_row = list(csv.DictReader(manifest_file))[-1]
        copied_side = Path(side_row["style_file"])
        side_completion: list[object] = [
            "checkpoint", "--state", guard, "--event", "STAGE_COMPLETED",
            "--summary", "Synthetic side stage passed required QA.", "--stage", "03_PHYSIQUE_SIDE", "--evidence", copied_side,
        ]
        if not child:
            side_completion.extend(("--swimwear-rung", "TWO_PIECE", "--observed-topology", "TWO_PIECE"))
        self._cli("task_execution_guard.py", *side_completion)

        back_prompt = (
            f"Create the canonical back view wearing {required_garment}; use the verified face and prior views."
            if child else "Create the canonical back view using the verified face, front, and side outputs."
        )
        back_call = self._resolve_and_prepare_call(request_id, guard, plan_path, back_prompt, "04_PHYSIQUE_BACK")
        back_pack = next(binding for binding in back_call["targeted_pack_bindings"])
        back_manifest = json.loads(Path(back_pack["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual([source["sha256"] for source in back_manifest["sources"]], [
            hashlib.sha256(copied_face.read_bytes()).hexdigest(),
            hashlib.sha256(copied_front.read_bytes()).hexdigest(),
            hashlib.sha256(copied_side.read_bytes()).hexdigest(),
        ])
        back_attempt = self._start_call(guard, plan_path, back_call)
        back_output = self.workspace / "back-stage.png"
        Image.new("RGB", (720, 1280), (143, 115, 101)).save(back_output)
        back_limb_evidence = limb_evidence.replace("view=FRONT;", "view=BACK;")
        self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", back_output, "--request-id", request_id,
            "--description", "Synthetic back physique stage", "--fidelity", "70", "--status", "STAGING",
            "--character-id", "NEW", "--attempt-id", back_attempt, "--reference-plan", plan_path,
            *self._visual_review_args(guard, back_attempt, back_output),
            "--stage-id", "04_PHYSIQUE_BACK", "--qa-attachments", "PASS", "--qa-canvas", "PASS",
            "--qa-stage-layer", "PASS", "--qa-face", "PASS", "--qa-body-silhouette", "PASS",
            "--qa-body-proportions", "PASS", "--qa-limb-proportions", "PASS", "--limb-qa-evidence", back_limb_evidence,
            "--qa-view", "PASS", "--qa-safe-coverage", "PASS", "--qa-clothing-topology", "PASS",
            *( ("--qa-clothing", "PASS") if child else () ),
            "--qa-multiview-consistency", "PASS", "--qa-style", "PASS", "--qa-body-style", "PASS",
        )
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            back_row = list(csv.DictReader(manifest_file))[-1]
        copied_back = Path(back_row["style_file"])
        back_completion: list[object] = [
            "checkpoint", "--state", guard, "--event", "STAGE_COMPLETED",
            "--summary", "Synthetic back stage passed required QA.", "--stage", "04_PHYSIQUE_BACK", "--evidence", copied_back,
        ]
        if not child:
            back_completion.extend(("--swimwear-rung", "TWO_PIECE", "--observed-topology", "TWO_PIECE"))
        self._cli("task_execution_guard.py", *back_completion)

        assembly_prompt = (
            f"Assemble the verified face and all three child physique views in {required_garment} into a neutral 3/4 image."
            if child else "Assemble the verified face and all three physique views into a neutral 3/4 character image."
        )
        assembly_call = self._resolve_and_prepare_call(
            request_id, guard, plan_path, assembly_prompt, "05_CHARACTER_ASSEMBLY",
        )
        self.assertEqual(len(assembly_call["stage_output_bindings"]), 4)
        assembly_attempt = self._start_call(guard, plan_path, assembly_call)
        assembly_output = self.workspace / "assembly-stage.png"
        Image.new("RGB", (720, 1280), (142, 114, 100)).save(assembly_output)
        self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", assembly_output, "--request-id", request_id,
            "--description", "Synthetic canonical character assembly", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NEW", "--attempt-id", assembly_attempt, "--reference-plan", plan_path,
            *self._visual_review_args(guard, assembly_attempt, assembly_output),
            "--stage-id", "05_CHARACTER_ASSEMBLY", "--qa-attachments", "PASS", "--qa-canvas", "PASS",
            "--qa-stage-layer", "PASS", "--qa-face", "PASS", "--qa-body-silhouette", "PASS",
            "--qa-body-proportions", "PASS", "--qa-limb-proportions", "PASS",
            "--limb-qa-evidence", limb_evidence.replace("view=FRONT", "view=ASSEMBLY") if child else limb_evidence,
            "--qa-multiview-consistency", "PASS", "--qa-neutral-backdrop", "PASS", "--qa-style", "PASS",
            "--qa-body-style", "PASS",
            *( ("--qa-safe-coverage", "PASS", "--qa-clothing-topology", "PASS", "--qa-clothing", "PASS") if child else () ),
        )
        assembly_row = None
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            assembly_row = list(csv.DictReader(manifest_file))[-1]
        copied_assembly = Path(assembly_row["style_file"])
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "STAGE_COMPLETED",
            "--summary", "Synthetic canonical assembly passed required QA.",
            "--stage", "05_CHARACTER_ASSEMBLY", "--evidence", copied_assembly,
        )
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "RESULT_DELIVERED",
            "--summary", "Synthetic assembly result delivery receipt.", "--attempt-id", assembly_attempt,
            "--delivery-evidence", str(copied_assembly),
        )
        if child:
            approved = self._cli(
                "style_pack_manager.py", "approve-character", "--workspace", self.workspace,
                "--style-name", self.style_name, "--request-id", request_id, "--image", copied_assembly,
                "--name", f"Synthetic {subject.title()}", "--risk-level", self._risk_level(plan_path),
                "--face-reference", copied_face, "--body-reference", copied_front,
                "--body-reference", copied_side, "--body-reference", copied_back,
                "--approval-quote", f"Approve synthetic child {subject} profile for integration testing.", "--user-approved",
            )
            registration = dict(line.split("=", 1) for line in approved.stdout.splitlines() if "=" in line)
            self.assertEqual(registration.get("STATUS"), "APPROVED_CHARACTER_REGISTERED")
            from tools import character_profile_state, style_pack_manager as manager
            profile_path = Path(registration["CHARACTER_PROFILE"])
            effective_profile = character_profile_state.load_effective(profile_path)["profile"]
            self.assertEqual(effective_profile["character_age_group"], "CHILD")
            self.assertEqual(effective_profile["character_base_contract"]["required_garment"], required_garment)
            self.assertEqual(effective_profile["anatomy_compatibility"]["target_anatomy"], anatomy)
            self.assertTrue((profile_path.parent / "CONFIRMED_PROFILE" / "ACTIVE.json").is_file())
            profile_yaml = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
            self.assertEqual(len(profile_yaml["character_face_references"]), 1)
            self.assertEqual(len(profile_yaml["character_body_references"]), 3)
            self.assertTrue(all((profile_path.parent / relative).is_file() for relative in [
                *profile_yaml["character_face_references"], *profile_yaml["character_body_references"],
                profile_yaml["approved_base"],
            ]))
            resolved_raw = self._cli(
                "style_pack_manager.py", "resolve-character", "--workspace", self.workspace,
                "--name", registration["CHARACTER_ID"], "--json",
            )
            resolved = json.loads(resolved_raw.stdout)
            self.assertEqual(resolved["status"], "FOUND")
            match = next(row for row in resolved["matches"] if row["character_id"] == registration["CHARACTER_ID"])
            confirmed = json.loads(match["confirmed_profile"])
            resolved_assets = json.loads(match["identity_assets"])
            self.assertEqual(set(resolved_assets), {
                "CHARACTER_ASSEMBLY", "FACE_IDENTITY", "BODY_IDENTITY_1", "BODY_IDENTITY_2", "BODY_IDENTITY_3",
            })
            self.assertTrue(confirmed["identity_bindings"])
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "COMPLETE",
            "--summary", "All five canonical stages passed and the assembly was delivered.",
        )
        final_state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(final_state["status"], "COMPLETE")
        self.assertEqual([row["id"] for row in final_state["required_stages"]], list(stages))
        self.assertEqual([row["status"] for row in final_state["required_stages"]], ["COMPLETED"] * 5)


if __name__ == "__main__":
    unittest.main()
