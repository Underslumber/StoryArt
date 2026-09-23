from __future__ import annotations

import json
import csv
import io
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from tools.reference_compatibility import load_character_identity, validate_reference_compatibility
from tools import body_reference_manager as body_manager
from tools import style_pack_manager
from PIL import Image


class ReferenceCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = Path(self.temp.name) / "CHARACTER_PROFILE.yaml"
        self.profile.write_text(
            'character_id: "CHAR_001"\n'
            'identity:\n  gender_identity: "MAN"\n  visible_presentation: "MASCULINE"\n'
            'anatomy_compatibility:\n  target_anatomy: "MALE_ANATOMY"\n  evidence_source: "approved character record"\n',
            encoding="utf-8",
        )
        self.target = load_character_identity(self.profile)

    def tearDown(self):
        self.temp.cleanup()

    def test_female_anatomy_reference_is_blocked_for_male_target(self):
        result = validate_reference_compatibility(
            self.target,
            {"anatomy_compatibility": "FEMALE_ANATOMY", "anatomy_evidence_source": "visual review"},
            ["BODY"],
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["compatible"])

    def test_aux_object_interaction_fails_closed_for_unknown_and_mismatched_anatomy(self):
        for reference, expected in (
            ({"framing": "FULL_BODY"}, "REVIEW_REQUIRED"),
            ({"subject_anatomy_compatibility": "FEMALE_ANATOMY", "subject_anatomy_evidence_source": "review", "framing": "FULL_BODY"}, "BLOCKED"),
        ):
            result = validate_reference_compatibility(self.target, reference, ["AUX_OBJECT_INTERACTION"])
            self.assertEqual(result["status"], expected)
            self.assertFalse(result["compatible"])

    def test_anatomy_visible_soft_role_fails_closed_when_target_is_unknown(self):
        unknown_target = {"character_id": "CHAR_CHANCE", "target_anatomy": "UNKNOWN", "anatomy_evidence_source": "UNKNOWN"}
        for reference in (
            {},
            {"subject_anatomy_compatibility": "FEMALE_ANATOMY", "subject_anatomy_evidence_source": "review", "framing": "FULL_BODY"},
        ):
            result = validate_reference_compatibility(unknown_target, reference, ["AUX_OBJECT_INTERACTION"])
            self.assertEqual(result["status"], "REVIEW_REQUIRED")
            self.assertFalse(result["compatible"])

    def test_target_anatomy_requires_evidence_even_when_reference_matches(self):
        target_without_evidence = {"character_id": "CHAR_CHANCE", "target_anatomy": "MALE_ANATOMY"}
        result = validate_reference_compatibility(
            target_without_evidence,
            {"subject_anatomy_compatibility": "MALE_ANATOMY", "subject_anatomy_evidence_source": "review", "framing": "FULL_BODY"},
            ["AUX_OBJECT_INTERACTION"],
        )
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertFalse(result["compatible"])

    def test_reviewed_male_reference_is_allowed_and_pose_only_cross_sex_remains_allowed(self):
        result = validate_reference_compatibility(
            self.target,
            {"subject_anatomy_compatibility": "MALE_ANATOMY", "subject_anatomy_evidence_source": "review", "framing": "FULL_BODY"},
            ["AUX_OBJECT_INTERACTION"],
        )
        self.assertEqual(result["status"], "ALLOWED")
        pose = validate_reference_compatibility(self.target, {"framing": "FULL_BODY"}, ["AUX_POSE"])
        self.assertEqual(pose["status"], "ALLOWED")

    def test_compatibility_wrapper_preserves_full_framing_for_soft_role_anatomy_gate(self):
        with self.assertRaisesRegex(style_pack_manager.StylePackError, "BLOCKED"):
            style_pack_manager.enforce_reference_compatibility(
                self.target,
                {
                    "path": "female-clothing-reference.png",
                    "subject_anatomy_compatibility": "FEMALE_ANATOMY",
                    "subject_anatomy_evidence_source": "review",
                    "framing": "FULL",
                },
                ["CLOTHES"],
            )

        pose = style_pack_manager.enforce_reference_compatibility(
            self.target, {"path": "pose-reference.png", "framing": "FULL"}, ["AUX_POSE"]
        )
        self.assertEqual(pose["status"], "ALLOWED")

    def test_soft_pose_style_and_background_can_cross_presentation(self):
        for role in ("POSE_SOFT", "STYLE", "BACKGROUND"):
            result = validate_reference_compatibility(
                self.target, {"visible_presentation": "FEMININE"}, [role]
            )
            self.assertEqual(result["status"], "ALLOWED")
            self.assertTrue(result["forbidden_transfers"])

    def test_missing_metadata_stays_unknown_and_does_not_replace_reference(self):
        missing = Path(self.temp.name) / "unknown.yaml"
        missing.write_text('name: "MaleHero"\n', encoding="utf-8")
        identity = load_character_identity(missing)
        self.assertEqual(identity["target_anatomy"], "UNKNOWN")
        result = validate_reference_compatibility(identity, {}, ["BODY"])
        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_canonical_face_must_match_same_character(self):
        result = validate_reference_compatibility(
            self.target, {"source_character_id": "CHAR_002"}, ["FACE"]
        )
        self.assertEqual(result["status"], "BLOCKED")

    def test_canonical_character_body_remains_allowed(self):
        result = validate_reference_compatibility(
            {"character_id": "CHAR_001", "target_anatomy": "UNKNOWN"},
            {"source_character_id": "CHAR_001"},
            ["BODY"],
        )
        self.assertEqual(result["status"], "ALLOWED")

    def test_mixed_roles_cannot_bypass_a_foreign_face_identity(self):
        result = validate_reference_compatibility(
            self.target,
            {"source_character_id": "CHAR_002", "anatomy_compatibility": "MALE_ANATOMY", "anatomy_evidence_source": "review"},
            ["BODY", "FACE"],
        )
        self.assertEqual(result["status"], "BLOCKED")

    def test_json_sidecar_shape_is_supported(self):
        sidecar = Path(self.temp.name) / "identity.json"
        sidecar.write_text(json.dumps({
            "character_id": "CHAR_002",
            "identity": {"gender_identity": "WOMAN", "visible_presentation": "FEMININE"},
            "anatomy_compatibility": {"target_anatomy": "FEMALE_ANATOMY", "evidence_source": "approved record"},
        }), encoding="utf-8")
        loaded = load_character_identity(sidecar)
        self.assertEqual(loaded["target_anatomy"], "FEMALE_ANATOMY")
        self.assertEqual(loaded["gender_identity"], "WOMAN")

    def test_body_ref_context_requires_concrete_target(self):
        workspace = Path(self.temp.name) / "workspace"
        manifest = workspace / style_pack_manager.BODY_LIBRARY_NAME / "BODY_REFERENCE_MANIFEST.csv"
        manifest.parent.mkdir(parents=True)
        with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["ref_id", "allowed_roles", "generator_safe"])
            writer.writeheader()
            writer.writerow({"ref_id": "BR_0001", "allowed_roles": "AUX_OBJECT_INTERACTION", "generator_safe": "YES"})
        args = Namespace(
            workspace=workspace, allowed_role=["AUX_OBJECT_INTERACTION"], style_name="", character_profile="",
            character_id="NONE", target_anatomy="UNKNOWN", target_anatomy_evidence="UNKNOWN",
            include_files=True, family="", pose="", view="", camera_angle="", body_build="",
            source_medium="", interaction="", generator_safe_only=False, json=True,
        )
        with self.assertRaisesRegex(style_pack_manager.StylePackError, "body-ref-context requires"):
            style_pack_manager.command_body_ref_context(args)
        args.allowed_role = []
        with self.assertRaisesRegex(style_pack_manager.StylePackError, "body-ref-context requires"):
            style_pack_manager.command_body_ref_context(args)
        args.allowed_role = ["AUX_OBJECT_INTERACTION"]
        args.target_anatomy = "MALE_ANATOMY"
        args.target_anatomy_evidence = "arbitrary evidence string"
        with self.assertRaisesRegex(style_pack_manager.StylePackError, "not evidence"):
            style_pack_manager.command_body_ref_context(args)

    def test_body_ref_context_returns_only_reviewed_anatomy_compatible_rows(self):
        workspace = Path(self.temp.name) / "workspace"
        manifest = workspace / style_pack_manager.BODY_LIBRARY_NAME / "BODY_REFERENCE_MANIFEST.csv"
        manifest.parent.mkdir(parents=True)
        fields = [
            "ref_id", "allowed_roles", "generator_safe", "framing", "anatomy_compatibility",
            "anatomy_evidence_source", "generator_path",
        ]
        with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows([
                {"ref_id": "BR_FEMALE", "allowed_roles": "AUX_OBJECT_INTERACTION", "generator_safe": "YES", "framing": "FULL_BODY", "anatomy_compatibility": "FEMALE_ANATOMY", "anatomy_evidence_source": "review", "generator_path": "female.png"},
                {"ref_id": "BR_UNKNOWN", "allowed_roles": "AUX_OBJECT_INTERACTION", "generator_safe": "YES", "framing": "FULL_BODY", "generator_path": "unknown.png"},
                {"ref_id": "BR_MALE", "allowed_roles": "AUX_OBJECT_INTERACTION", "generator_safe": "YES", "framing": "FULL_BODY", "anatomy_compatibility": "MALE_ANATOMY", "anatomy_evidence_source": "review", "generator_path": "male.png"},
            ])
        args = Namespace(
            workspace=workspace, allowed_role=["AUX_OBJECT_INTERACTION"], style_name="", character_profile="",
            character_id="NONE", target_anatomy="UNKNOWN", target_anatomy_evidence="UNKNOWN",
            target_prompt="Chance is a male character.", target_name="Chance",
            target_visible_presentation="UNKNOWN", include_files=True, family="", pose="", view="",
            camera_angle="", body_build="", source_medium="", interaction="", generator_safe_only=True, json=True,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            style_pack_manager.command_body_ref_context(args)
        result = json.loads(output.getvalue())
        self.assertEqual([row["ref_id"] for row in result["references"]], ["BR_MALE"])
        self.assertEqual(result["matching_references"], 1)
        self.assertEqual(result["compatibility_filtered_references"], 2)

    def test_explicit_current_prompt_can_supply_target_anatomy(self):
        args = Namespace(
            character_id="NONE", character_profile="", target_name="Шанс",
            target_prompt="Сделай мне арт Шанса он в своем костюме.",
            target_anatomy="UNKNOWN", target_anatomy_evidence="UNKNOWN",
        )
        identity = style_pack_manager.compatibility_target(None, args)
        self.assertEqual(identity["target_anatomy"], "MALE_ANATOMY")
        self.assertTrue(identity["anatomy_evidence_source"].startswith("CURRENT_PROMPT_EXPLICIT:"))

    def test_arbitrary_target_anatomy_and_evidence_are_not_provenance(self):
        args = Namespace(
            character_id="NONE", character_profile="", target_name="Chance", target_prompt="",
            target_anatomy="MALE_ANATOMY", target_anatomy_evidence="approved character record",
        )
        with self.assertRaisesRegex(style_pack_manager.StylePackError, "not evidence"):
            style_pack_manager.compatibility_target(None, args)

    def test_ambiguous_prompt_does_not_supply_target_anatomy(self):
        args = Namespace(
            character_id="NONE", character_profile="", target_name="Chance", target_prompt="Chance in his costume.",
            target_anatomy="UNKNOWN", target_anatomy_evidence="UNKNOWN",
        )
        identity = style_pack_manager.compatibility_target(None, args)
        self.assertEqual(identity["target_anatomy"], "UNKNOWN")
        self.assertEqual(identity["anatomy_evidence_source"], "UNKNOWN")

    def test_unapproved_profile_cannot_supply_target_anatomy(self):
        root = Path(self.temp.name)
        workspace = root / "workspace"
        generations = workspace / "GENERATIONS"
        profile = root / "unregistered.yaml"
        profile.write_text(
            'character_id: "CHAR_001"\nname: "Шанс"\nstatus: "APPROVED"\n'
            'anatomy_compatibility:\n  target_anatomy: "MALE_ANATOMY"\n  evidence_source: "reviewed source"\n',
            encoding="utf-8",
        )
        paths = style_pack_manager.StylePaths(workspace, "style", "STYLE", workspace / "STYLE", generations)
        args = Namespace(character_id="NEW", character_profile=str(profile), target_prompt="", target_anatomy="UNKNOWN", target_anatomy_evidence="UNKNOWN")
        with self.assertRaisesRegex(style_pack_manager.StylePackError, "APPROVED character-registry"):
            style_pack_manager.compatibility_target(paths, args)

    def test_approved_registered_profile_can_supply_target_anatomy(self):
        root = Path(self.temp.name)
        workspace = root / "workspace"
        generations = workspace / "GENERATIONS"
        folder = generations / "01_APPROVED_CHARACTERS" / "CHAR_001_Chance"
        folder.mkdir(parents=True)
        profile = folder / "CHARACTER_PROFILE.yaml"
        profile.write_text(
            'character_id: "CHAR_001"\nname: "Chance"\nstatus: "APPROVED"\n'
            'anatomy_compatibility:\n  target_anatomy: "MALE_ANATOMY"\n  evidence_source: "reviewed body record"\n',
            encoding="utf-8",
        )
        base = folder / "approved.png"
        base.write_bytes(b"approved base")
        paths = style_pack_manager.StylePaths(workspace, "style", "STYLE", workspace / "STYLE", generations)
        paths.character_registry.parent.mkdir(parents=True, exist_ok=True)
        with paths.character_registry.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["character_id", "name", "approved_base", "profile_path", "status"])
            writer.writeheader()
            writer.writerow({"character_id": "CHAR_001", "name": "Шанс", "approved_base": str(base), "profile_path": str(profile), "status": "APPROVED"})
        args = Namespace(character_id="CHAR_001", character_profile="", target_prompt="", target_anatomy="UNKNOWN", target_anatomy_evidence="UNKNOWN")
        identity = style_pack_manager.compatibility_target(paths, args)
        self.assertEqual(identity["target_anatomy"], "MALE_ANATOMY")
        self.assertEqual(identity["evidence_status"], "APPROVED_REVIEWED_PROFILE")
        profile.write_text('character_id: "CHAR_001"\nname: "Шанс"\nstatus: "APPROVED"\n', encoding="utf-8")
        args.target_prompt = "Сделай мне арт Шанса он в своем костюме."
        identity = style_pack_manager.compatibility_target(paths, args)
        self.assertEqual(identity["target_anatomy"], "MALE_ANATOMY")
        self.assertEqual(identity["evidence_status"], "CURRENT_PROMPT_EXPLICIT")

    def test_final_attachment_rechecks_current_anatomy_metadata(self):
        root = Path(self.temp.name)
        workspace = root / "workspace"
        pack = workspace / "style" / "STYLE"
        generations = workspace / "style" / "GENERATIONS"
        image = pack / "01_WORK" / "BODY_CROPS" / "female.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (32, 48), "gray").save(image)
        reference_manifest = pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "LOGS" / "PRELIMINARY_REFERENCE_MANIFEST.csv"
        reference_manifest.parent.mkdir(parents=True)
        with reference_manifest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["stored_relative_path", "status", "user_approved", "anatomy_compatibility", "anatomy_evidence_source", "framing"])
            writer.writeheader()
            writer.writerow({
                "stored_relative_path": "01_WORK/BODY_CROPS/female.png", "status": "APPROVED", "user_approved": "YES",
                "anatomy_compatibility": "FEMALE_ANATOMY", "anatomy_evidence_source": "review", "framing": "FULL",
            })
        paths = style_pack_manager.StylePaths(workspace, "style", "STYLE", pack, generations)
        metadata = style_pack_manager.style_reference_compatibility_metadata(paths, image)
        self.assertEqual(metadata["framing"], "FULL")
        plan = {
            "request_id": "REQ_TEST",
            "target_identity_compatibility": {"target": self.target},
            "selected_references": {"body": {"path": str(image)}},
            "generation_workflow": {
                "mode": "SINGLE_PASS",
                "attachment_limit": 1,
                "slots": [{"path": str(image), "sha256": style_pack_manager.sha256(image), "active_roles": ["CLOTHES"], "slot": 1}],
            },
        }
        with self.assertRaisesRegex(style_pack_manager.StylePackError, "BLOCKED"):
            style_pack_manager.resolve_call_slots(paths, root / "REFERENCE_PLAN.json", plan, "SINGLE_PASS")

    def test_final_resolve_call_uses_planned_semantic_role_for_primary_face(self):
        root = Path(self.temp.name)
        workspace = root / "workspace"
        pack = workspace / "style" / "STYLE"
        generations = workspace / "style" / "GENERATIONS"
        image = pack / "01_WORK" / "FACE_CROPS" / "primary.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (32, 32), "gray").save(image)
        reference_manifest = pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "LOGS" / "PRELIMINARY_REFERENCE_MANIFEST.csv"
        reference_manifest.parent.mkdir(parents=True)
        with reference_manifest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["stored_relative_path", "status", "user_approved"])
            writer.writeheader()
            writer.writerow({"stored_relative_path": "01_WORK/FACE_CROPS/primary.png", "status": "APPROVED", "user_approved": "YES"})
        paths = style_pack_manager.StylePaths(workspace, "style", "STYLE", pack, generations)
        plan = {
            "request_id": "REQ_TEST",
            "target_identity_compatibility": {"target": self.target},
            "selected_references": {"primary_face": {"path": str(image)}},
            "generation_workflow": {
                "mode": "SINGLE_PASS",
                "attachment_limit": 1,
                "slots": [{"path": str(image), "sha256": style_pack_manager.sha256(image), "active_roles": ["PRIMARY_FACE"], "slot": 1}],
            },
        }
        with patch("tools.style_pack_manager.canonical_source_character_id", return_value="CHAR_001"):
            slots = style_pack_manager.resolve_call_slots(paths, root / "REFERENCE_PLAN.json", plan, "SINGLE_PASS")
        self.assertEqual(slots[0]["active_roles"], ["PRIMARY_FACE"])

    def _body_library_call_fixture(self, *, role="AUX_OBJECT_INTERACTION", with_target=True, with_per_file=True):
        root = Path(self.temp.name)
        workspace = root / "body-workspace"
        library = workspace / style_pack_manager.BODY_LIBRARY_NAME
        image = library / "generator" / "reference.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (40, 60), "gray").save(image)
        manifest = library / "BODY_REFERENCE_MANIFEST.csv"
        row = {
            "ref_id": "BR_0001", "generator_path": "generator/reference.png", "generator_safe": "YES",
            "allowed_roles": role, "framing": "FULL_BODY", "anatomy_compatibility": "MALE_ANATOMY",
            "anatomy_evidence_source": "reviewed reference",
        }
        with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        paths = style_pack_manager.StylePaths(workspace, "style", "STYLE", workspace / "style" / "STYLE", workspace / "style" / "GENERATIONS")
        auxiliary = {
            "ref_id": "BR_0001", "path": str(image), "sha256": style_pack_manager.sha256(image),
            "manifest_sha256": style_pack_manager.sha256(manifest), "active_roles": [role],
        }
        plan = {
            "request_id": "REQ_TEST",
            "auxiliary_body_references": [auxiliary],
            "generation_workflow": {
                "mode": "SINGLE_PASS", "attachment_limit": 1,
                "slots": [{"path": str(image), "sha256": style_pack_manager.sha256(image), "active_roles": [role], "slot": 1}],
            },
        }
        if with_target:
            plan["target_identity_compatibility"] = {
                "target": self.target,
                "references": ([{"path": str(image), "roles": [role], "status": "ALLOWED"}] if with_per_file else []),
            }
        return root, image, paths, plan

    def test_resolve_call_blocks_aux_object_without_target_identity_record(self):
        root, _, paths, plan = self._body_library_call_fixture(with_target=False)
        with self.assertRaisesRegex(style_pack_manager.StylePackError, "evidence-backed target"):
            style_pack_manager.resolve_call_slots(paths, root / "REFERENCE_PLAN.json", plan, "SINGLE_PASS")

    def test_resolve_call_blocks_aux_object_without_per_file_compatibility_record(self):
        root, _, paths, plan = self._body_library_call_fixture(with_per_file=False)
        with self.assertRaisesRegex(style_pack_manager.StylePackError, "per-file compatibility record"):
            style_pack_manager.resolve_call_slots(paths, root / "REFERENCE_PLAN.json", plan, "SINGLE_PASS")

    def test_resolve_call_preserves_body_library_pose_only_cross_sex_attachment(self):
        root, image, paths, plan = self._body_library_call_fixture(role="AUX_POSE", with_target=False)
        slots = style_pack_manager.resolve_call_slots(paths, root / "REFERENCE_PLAN.json", plan, "SINGLE_PASS")
        self.assertEqual(slots[0]["path"], str(image.resolve()))

    def test_pose_only_body_reference_revalidates_manifest_safety_and_hashes(self):
        root, image, paths, plan = self._body_library_call_fixture(role="AUX_POSE", with_target=False)
        manifest = paths.workspace / style_pack_manager.BODY_LIBRARY_NAME / "BODY_REFERENCE_MANIFEST.csv"

        def read_row():
            with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))[0]

        def write_row(row):
            with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
                fields = next(csv.reader(handle))
            with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(row)

        baseline = read_row()
        for field, value in (("generator_safe", "NO"), ("allowed_roles", "CLOTHES")):
            with self.subTest(field=field):
                changed = dict(baseline, **{field: value})
                write_row(changed)
                plan["auxiliary_body_references"][0]["manifest_sha256"] = style_pack_manager.sha256(manifest)
                with self.assertRaisesRegex(style_pack_manager.StylePackError, "no longer generator-safe"):
                    style_pack_manager.resolve_call_slots(paths, root / "REFERENCE_PLAN.json", plan, "SINGLE_PASS")
                write_row(baseline)
                plan["auxiliary_body_references"][0]["manifest_sha256"] = style_pack_manager.sha256(manifest)

        plan["auxiliary_body_references"][0]["manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(style_pack_manager.StylePackError, "manifest hash"):
            style_pack_manager.resolve_call_slots(paths, root / "REFERENCE_PLAN.json", plan, "SINGLE_PASS")
        plan["auxiliary_body_references"][0]["manifest_sha256"] = style_pack_manager.sha256(manifest)
        image.write_bytes(b"changed image bytes")
        with self.assertRaisesRegex(style_pack_manager.StylePackError, "missing or changed"):
            style_pack_manager.resolve_call_slots(paths, root / "REFERENCE_PLAN.json", plan, "SINGLE_PASS")

    def test_execution_started_rechecks_current_body_library_manifest(self):
        from tools import task_execution_guard as guard

        def ready_fixture(*, role="AUX_POSE", anatomy="", mutate=None):
            root = Path(self.temp.name) / f"start-{role}-{anatomy or 'pose'}-{mutate or 'valid'}"
            workspace = root / "workspace"
            paths = style_pack_manager.make_paths(workspace, "STYLE")
            library = workspace / style_pack_manager.BODY_LIBRARY_NAME
            image = library / "generator" / "reference.png"
            image.parent.mkdir(parents=True)
            Image.new("RGB", (40, 60), "gray").save(image)
            manifest = library / "BODY_REFERENCE_MANIFEST.csv"
            row = {
                "ref_id": "BR_0001", "generator_path": "generator/reference.png", "generator_safe": "YES",
                "allowed_roles": role, "framing": "FULL_BODY", "anatomy_compatibility": anatomy,
                "anatomy_evidence_source": "reviewed reference" if anatomy else "",
            }
            def write_manifest():
                with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(row))
                    writer.writeheader()
                    writer.writerow(row)
            write_manifest()
            auxiliary = {
                "ref_id": "BR_0001", "path": str(image), "sha256": style_pack_manager.sha256(image),
                "manifest_sha256": style_pack_manager.sha256(manifest), "active_roles": [role],
            }
            compatibility = ([{"path": str(image), "roles": [role], "status": "ALLOWED"}] if anatomy else [])
            call = {
                "request_id": "REQ_TEST", "stage_id": "SINGLE_PASS",
                "slots": [{"path": str(image), "sha256": style_pack_manager.sha256(image), "active_roles": [role]}],
                "stage_output_bindings": [],
            }
            plan = {
                "style_name": "STYLE", "request_id": "REQ_TEST", "character_id": "NONE",
                "generation_workflow": {"mode": "SINGLE_PASS", "slots": call["slots"]},
                "auxiliary_body_references": [auxiliary], "execution_call": call,
            }
            if anatomy:
                plan["target_identity_compatibility"] = {"target": self.target, "references": compatibility}
            plan_path = paths.generations / "00_PENDING" / "REQ_TEST" / "REFERENCE_PLAN.json"
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            guard_path = plan_path.parent / "EXECUTION_GUARD.json"
            guard.create_guard(guard_path, request_id="REQ_TEST", goal="Use one reviewed body reference.",
                               deliverable="One image.", task_kind="IMAGE_GENERATION", now=guard.utc_now())
            state = guard.load_guard(guard_path)
            state["next_required_action"] = "EXECUTION_STARTED_OR_BLOCKER"
            state["ready_binding"] = {"stage": "SINGLE_PASS"}
            guard.atomic_write_json(guard_path, state)
            if mutate == "UNSAFE":
                row["generator_safe"] = "NO"
                write_manifest()
            elif mutate == "ROLE_REVOKED":
                row["allowed_roles"] = "CLOTHES"
                write_manifest()
            elif mutate == "ANATOMY_CHANGED":
                row["anatomy_compatibility"] = "FEMALE_ANATOMY"
                write_manifest()
            return guard_path, plan_path, call

        valid_guard, valid_plan, valid_call = ready_fixture()
        with patch("tools.task_execution_guard.validate_reference_plan", return_value={"stage": "SINGLE_PASS"}):
            started = guard.checkpoint(
                valid_guard, event="EXECUTION_STARTED", summary="Start valid pose-only call.",
                reference_plan=valid_plan, execution_call=valid_call, stage="SINGLE_PASS",
                output_contract="REQUESTED_DELIVERABLE",
            )
        self.assertEqual(started["active_attempt"]["status"], "ACTIVE")

        for mutate, role, anatomy, expected in (
            ("UNSAFE", "AUX_POSE", "", "manifest hash"),
            ("ROLE_REVOKED", "AUX_POSE", "", "manifest hash"),
            ("ANATOMY_CHANGED", "AUX_OBJECT_INTERACTION", "MALE_ANATOMY", "manifest hash"),
        ):
            with self.subTest(mutate=mutate):
                blocked_guard, blocked_plan, blocked_call = ready_fixture(role=role, anatomy=anatomy, mutate=mutate)
                with patch("tools.task_execution_guard.validate_reference_plan", return_value={"stage": "SINGLE_PASS"}):
                    with self.assertRaisesRegex(guard.GuardError, expected):
                        guard.checkpoint(
                            blocked_guard, event="EXECUTION_STARTED", summary="Start after manifest change.",
                            reference_plan=blocked_plan, execution_call=blocked_call, stage="SINGLE_PASS",
                            output_contract="REQUESTED_DELIVERABLE",
                        )

    def test_apply_update_appends_reviewed_fields_and_preserves_legacy_rows_and_ids(self):
        workspace = Path(self.temp.name) / "workspace"
        library = workspace / body_manager.LIBRARY_NAME
        batch = library / "02_LOCAL_ONLY" / "UPDATE_BATCHES" / "BATCH_TEST"
        batch.mkdir(parents=True)
        manifest = library / body_manager.MANIFEST_NAME
        legacy_fields = ["ref_id", "filename", "source_filename", "source_sha256", "notes"]
        with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=legacy_fields)
            writer.writeheader()
            writer.writerow({"ref_id": "BR_0001", "filename": "old.jpg", "source_filename": "old.jpg", "source_sha256": "old-hash", "notes": "preserve me"})
        incoming = workspace / "incoming.png"
        incoming.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 48), "gray").save(incoming)
        digest = body_manager.sha256(incoming)
        inventory = [{
            "source_id": "IN_001", "source_path": str(incoming), "source_filename": incoming.name,
            "source_sha256": digest, "proposed_ref_id": "BR_0002",
        }]
        with (batch / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(inventory[0]))
            writer.writeheader()
            writer.writerows(inventory)
        entry = {
            "source_id": "IN_001", "action": "ADD", "source_medium": "PHOTO",
            "primary_family": "BODY", "pose": "STANDING", "view": "FRONT",
            "camera_angle": "EYE", "framing": "FULL", "body_build": "AVERAGE",
            "proportion_tags": ["PROPORTION_REVIEWED"], "foreshortening": "LOW", "clothing_interaction": "NONE",
            "object_interaction": "NONE", "contact_points": "NONE", "anatomy_reliability": "HIGH",
            "allowed_roles": ["AUX_BODY_BUILD"], "not_for_roles": [], "safety_status": "SAFE",
            "visible_presentation": "MASCULINE", "anatomy_compatibility": ["MALE_ANATOMY"],
            "anatomy_evidence_source": "reviewed source", "notes": "reviewed",
        }
        (batch / "REVIEW_SPEC.json").write_text(json.dumps({
            "batch_id": "BATCH_TEST", "review_status": "VISUALLY_REVIEWED", "references": [entry],
        }), encoding="utf-8")
        (batch / "BATCH.json").write_text(json.dumps({
            "batch_id": "BATCH_TEST", "baseline_manifest_sha256": body_manager.sha256(manifest),
            "baseline_next_ref_number": 2,
        }), encoding="utf-8")

        body_manager.command_apply_update(Namespace(workspace=workspace, batch="BATCH_TEST", dry_run=False))

        fields, rows = body_manager.read_manifest(manifest)
        self.assertEqual([row["ref_id"] for row in rows], ["BR_0001", "BR_0002"])
        self.assertEqual(rows[0]["notes"], "preserve me")
        self.assertIn("anatomy_compatibility", fields)
        self.assertEqual(rows[1]["anatomy_compatibility"], "MALE_ANATOMY")
        self.assertEqual(rows[1]["visible_presentation"], "MASCULINE")
        self.assertEqual(rows[1]["anatomy_evidence_source"], "reviewed source")


if __name__ == "__main__":
    unittest.main()
