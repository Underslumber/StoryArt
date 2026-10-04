import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.reference_compatibility import validate_reference_compatibility
from tools.body_reference_type_router import reference_context
from tools import style_pack_manager as manager


class StrictLabelTests(unittest.TestCase):
    def test_approved_legacy_body_fact_supplies_classification_and_rejects_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = manager.make_paths(Path(directory), "STYLE")
            folder = paths.generations / "01_APPROVED_CHARACTERS" / "CHAR_002_Beal"
            folder.mkdir(parents=True)
            base = folder / "base.png"
            base.write_bytes(b"synthetic approved asset")
            profile = folder / "CHARACTER_PROFILE.yaml"
            manager.write_csv(paths.character_registry, manager.CHARACTER_FIELDS, [{"character_id": "CHAR_002", "status": "APPROVED", "profile_path": str(profile), "approved_base": str(base)}])
            prefix = 'character_id: CHAR_002\nname: Beal\nstatus: APPROVED\nlocked_invariants:\n  body_proportions: '
            profile.write_text(prefix + '"Naturally lean functional male traveler build, about 7.0-7.25 heads tall."\n', encoding="utf-8")
            identity = manager._approved_profile_identity(paths, profile, "CHAR_002")
            self.assertEqual(identity["target_anatomy"], "MALE_ANATOMY")
            self.assertIn("locked_invariants.body_proportions:PROFILE_SHA256=", identity["anatomy_evidence_source"])
            profile.write_text(prefix + '"male and female build"\n', encoding="utf-8")
            with self.assertRaisesRegex(manager.StylePackError, "contradictory"):
                manager._approved_profile_identity(paths, profile, "CHAR_002")
    def test_style_context_filters_before_image_info_and_path_output(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            pack = workspace / "pack"
            pack.mkdir()
            for name in ("female.png", "unknown.png", "male.png"):
                (pack / name).write_bytes(b"fixture")
            paths = manager.StylePaths(workspace, "style", "STYLE", pack, workspace / "generations")
            discovery = SimpleNamespace(pack_path=str(pack), style_name="STYLE", management="LOCAL", local_readiness="READY", web_readiness="READY")
            def metadata(_, file):
                return {"subject_label": {"male.png": "MALE", "female.png": "FEMALE"}.get(file.name, "UNKNOWN"),
                        "subject_label_evidence": "review", "subject_label_sha256": manager.sha256(file)}
            with patch.object(manager, "matching_discovered_style", return_value=discovery), \
                 patch.object(manager, "style_reference_compatibility_metadata", side_effect=metadata), \
                 patch.object(manager, "manifest_role_files", return_value=["male.png", "female.png", "unknown.png"]), \
                 patch.object(manager, "image_info", return_value=("PNG", 1, 1)) as info:
                context = manager.build_style_context(paths, "ALL", True, True,
                    {"target_anatomy": "MALE_ANATOMY", "anatomy_evidence_source": "review"})
                summary = manager.build_style_context(paths, "ALL", True, False)
                minimal = manager._minimal_preparation_style_context(paths)
            self.assertEqual([Path(row["absolute_path"]).name for row in context["files"]], ["male.png"])
            self.assertEqual([call.args[0].name for call in info.call_args_list], ["male.png"])
            self.assertEqual(context["explicit_anchor_files"], ["male.png"])
            self.assertEqual(summary["explicit_anchor_files"], [])
            self.assertEqual(summary["manifest_master_style_files"], [])
            self.assertEqual(minimal["explicit_anchor_files"], [])

    def test_all_pose_aliases_cannot_attach_without_source_record(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            file = workspace / "ref.png"
            file.write_bytes(b"fixture")
            paths = manager.StylePaths(workspace, "style", "STYLE", workspace / "pack", workspace / "generations")
            for role in ("ART_POSE_SOFT", "POSE_STUDY", "STAGING_ONLY"):
                with self.assertRaisesRegex(manager.StylePackError, "source metadata record"):
                    manager.validate_resolved_slot_compatibility(paths, {}, {"active_roles": [role]}, file)

    def test_review_evidence_quarantine_and_hash_are_mandatory(self):
        target = {"target_anatomy": "MALE_ANATOMY", "anatomy_evidence_source": "review"}
        for extra in ({}, {"subject_label_evidence": "review", "subject_label_status": "QUARANTINED"},
                      {"subject_label_evidence": "review", "subject_label_status": "SOURCE_MISSING"},
                      {"subject_label_evidence": "review", "sha256": "current"},
                      {"subject_label_evidence": "review", "subject_label_sha256": "old", "sha256": "new"}):
            for role in ("STYLE", "CAMERA", "POSE_SOFT", "ART_POSE_SOFT", "POSE_STUDY", "STAGING_ONLY"):
                self.assertFalse(validate_reference_compatibility(target, {"subject_label": "MALE", **extra}, role)["compatible"])

    def test_reviewed_no_subject_is_nonpose_only(self):
        target = {"target_anatomy": "MALE_ANATOMY", "anatomy_evidence_source": "review"}
        source = {"subject_label": "NO_SUBJECT", "subject_label_evidence": "review"}
        self.assertTrue(validate_reference_compatibility(target, source, "STYLE")["compatible"])
        self.assertFalse(validate_reference_compatibility(target, source, "POSE")["compatible"])
        self.assertFalse(validate_reference_compatibility(target, source, "FACE")["compatible"])

    def test_unclassified_identity_id_cannot_spoof_approved_provenance(self):
        target = {"character_id": "CHAR_001", "target_anatomy": "MALE_ANATOMY", "anatomy_evidence_source": "review"}
        for role in ("STYLE", "BODY", "FACE"):
            source = {"source_character_id": "CHAR_001", "subject_label": "UNCLASSIFIED"}
            self.assertFalse(validate_reference_compatibility(target, source, role)["compatible"])

    def test_generic_context_cannot_accept_human_sources_without_target(self):
        for label in ("MALE", "FEMALE"):
            self.assertFalse(validate_reference_compatibility({}, {"subject_label": label, "subject_label_evidence": "review"}, "STYLE")["compatible"])

    def test_pose_matrix_has_no_soft_exception(self):
        for target_label in ("MALE", "FEMALE"):
            target = {"target_anatomy": target_label + "_ANATOMY", "anatomy_evidence_source": "approved record"}
            for role in ("POSE", "POSE_SOFT", "AUX_POSE", "ART_POSE_SOFT"):
                for label in ("MALE", "FEMALE", "MIXED", "NEUTRAL", "UNCLASSIFIED", "UNKNOWN"):
                    with self.subTest(target=target_label, role=role, label=label):
                        result = validate_reference_compatibility(target, {"subject_label": label, "subject_label_evidence": "review"}, role)
                        self.assertEqual(result["compatible"], label == target_label)

    def test_mixed_reviewed_compatibility_is_not_a_unique_label(self):
        result = validate_reference_compatibility(
            {"target_anatomy": "MALE_ANATOMY", "anatomy_evidence_source": "review"},
            {"anatomy_compatibility": "MALE_ANATOMY;FEMALE_ANATOMY", "anatomy_evidence_source": "review"}, "POSE")
        self.assertFalse(result["compatible"])

    def test_same_character_cannot_bypass_opposite_explicit_label(self):
        for role in ("BODY", "FACE", "STYLE", "POSE"):
            result = validate_reference_compatibility(
                {"character_id": "CHAR_001", "target_anatomy": "MALE_ANATOMY", "anatomy_evidence_source": "review"},
                {"source_character_id": "CHAR_001", "subject_label": "FEMALE"}, role)
            self.assertFalse(result["compatible"])

    def test_routed_context_never_exposes_ineligible_paths(self):
        for type_id in ("SKETCH", "OLCHAS"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                rows = [{"subject_label": label, "subject_label_evidence": "review", "final_path": label + ".png", "thumbnail_path": label + "-preview.png"}
                        for label in ("MALE", "FEMALE", "MIXED", "UNCLASSIFIED")]
                for row in rows:
                    (root / row["final_path"]).write_bytes(b"fixture")
                    row["subject_label_sha256"] = manager.sha256(root / row["final_path"])
                with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
                result = reference_context(type_id, {"target_anatomy": "MALE_ANATOMY", "anatomy_evidence_source": "review"},
                                           root, {"types": {type_id: {"manifest": "manifest.csv"}}})
                self.assertEqual(result["references"], rows[:1])
                self.assertEqual(result["filtered_count"], 3)
