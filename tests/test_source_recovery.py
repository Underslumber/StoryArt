"""Approved source reuse when older records lack manager QA receipts."""

import argparse
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import style_pack_manager as manager


class SourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(os.environ.get("STORYART_TEST_TMP", str(ROOT)))
        self.workspace = temp_root / f"source-recovery-{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
        self.paths = manager.make_paths(self.workspace, "RIOT LOL SPLASH")
        self.folder = self.paths.generations / "01_APPROVED_CHARACTERS" / "CHAR_998_Test"
        self.base = self.folder / "01_APPROVED_BASE" / "assembly.png"
        self.face = self.folder / "03_FACE_REFERENCES" / "face.png"
        self.bodies = [self.folder / "04_BODY_REFERENCES" / f"{view}.png" for view in ("front", "side", "back")]
        self.wardrobe = self.folder / "03_CHARACTER_REFERENCES" / "03_WARDROBE" / "suit.png"
        for index, image in enumerate((self.base, self.face, *self.bodies, self.wardrobe)):
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), (30 + index, 60, 90)).save(image)
        self.profile = self.folder / "CHARACTER_PROFILE.yaml"
        self.profile.write_text(
            "character_id: CHAR_998\nname: Test\nstatus: APPROVED\n"
            "identity:\n  gender_identity: man\n  visible_presentation: masculine\n"
            "anatomy_compatibility:\n  target_anatomy: MALE_ANATOMY\n  evidence_source: APPROVED_PROFILE\n"
            'character_face_references:\n  - "03_FACE_REFERENCES/face.png"\n'
            'character_body_references:\n'
            + "".join(f'  - "04_BODY_REFERENCES/{p.name}"\n' for p in self.bodies)
            + "wardrobe_references: []\naccessory_references: []\n",
            encoding="utf-8",
        )
        manager.write_csv(self.paths.character_registry, manager.CHARACTER_FIELDS, [{
            "character_id": "CHAR_998", "name": "Test", "approved_base": str(self.base),
            "profile_path": str(self.profile), "face_references": str(self.face),
            "body_references": ";".join(map(str, self.bodies)), "status": "APPROVED",
        }])
        self.record(self.base, "APPROVED_CHARACTER_BASE")

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def record(self, image, status, *, hash_bound=False):
        return manager.append_generation(
            self.paths, request_id=f"fixture-{uuid.uuid4().hex}", character_id="CHAR_998", status=status,
            fidelity=90, risk_level="D1", description="fixture", source_image=image,
            archive_file=image, style_file=image,
            qa_output_sha256=manager.sha256(image) if hash_bound else "",
        )

    def defaults(self, *, explicit="", overrides=frozenset()):
        confirmed = manager.effective_character_profile(self.paths, "CHAR_998")
        args = argparse.Namespace(
            clothes_reference=explicit, accessory_reference=[], face_variant_reference=[], body_variant_reference=[],
            suppress_profile_default=[], character_assembly="", character_reference_mode="ASSEMBLY_ONLY",
            shot_complexity="NORMAL", selected_body_view="ASSEMBLY", primary_face="", body_reference="",
        )
        applied = manager.apply_confirmed_defaults(self.paths, args, "CHAR_998", confirmed, set(overrides))
        return args, applied

    def test_legacy_approved_wardrobe_is_attachable_with_honest_qa_status(self):
        self.record(self.wardrobe, "APPROVED_WARDROBE", hash_bound=True)
        assets, index = manager.approved_character_role_assets(self.paths, "CHAR_998", self.profile)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["qa_status"], "NOT_CHECKED")
        self.assertEqual(index["qa_diagnostics"][0]["qa_status"], "NOT_CHECKED")
        self.assertFalse(manager.generation_has_passed_qa(manager.newest_matching_generation(self.paths, self.wardrobe)))
        self.assertIsNotNone(manager.registered_approved_generation(self.paths, self.wardrobe))
        self.assertEqual(manager.registered_approved_character_asset(self.paths, self.wardrobe)["asset_role"], "WARDROBE")
        reference = manager.validate_plan_reference(self.paths, str(self.wardrobe), "CLOTHES")
        self.assertEqual(reference["sha256"], manager.sha256(self.wardrobe))
        self.assertEqual(reference["status"], "APPROVED_WARDROBE")
        attachment = manager.build_attachment_plan({"clothes": reference}, [], 5)
        self.assertEqual(attachment[0]["active_roles"], ["CLOTHES"])
        self.assertEqual(attachment[0]["path"], str(self.wardrobe.resolve()))
        args, applied = self.defaults()
        self.assertEqual(args.clothes_reference, str(self.wardrobe.resolve()))
        self.assertEqual(applied["wardrobe"][0]["path"], str(self.wardrobe.resolve()))

    def test_absent_wardrobe_does_not_invent_clothing(self):
        args, applied = self.defaults()
        self.assertEqual(args.clothes_reference, "")
        self.assertEqual(applied["wardrobe"], [])

    def test_multiple_wardrobes_require_selection_and_explicit_choice_wins(self):
        second = self.wardrobe.with_name("second.png")
        Image.new("RGB", (16, 16), (211, 45, 91)).save(second)
        self.record(self.wardrobe, "APPROVED_WARDROBE")
        self.record(second, "APPROVED_WARDROBE")
        with self.assertRaisesRegex(manager.StylePackError, "Multiple approved wardrobes"):
            self.defaults()
        args, applied = self.defaults(explicit=str(second))
        self.assertEqual(args.clothes_reference, str(second))
        self.assertEqual(applied["wardrobe"], [])
        args, applied = self.defaults(overrides={"CLOTHES"})
        self.assertEqual(args.clothes_reference, "")
        self.assertEqual(applied["wardrobe"], [])

    def test_revocation_and_changed_recorded_hash_still_block(self):
        self.record(self.wardrobe, "APPROVED_WARDROBE", hash_bound=True)
        self.record(self.wardrobe, "REJECTED", hash_bound=True)
        self.assertIsNone(manager.registered_approved_generation(self.paths, self.wardrobe))
        self.assertIsNone(manager.registered_approved_character_asset(self.paths, self.wardrobe))
        self.record(self.wardrobe, "APPROVED_WARDROBE", hash_bound=True)
        Image.new("RGB", (16, 16), (255, 0, 0)).save(self.wardrobe)
        self.assertIsNone(manager.registered_approved_generation(self.paths, self.wardrobe))
        assets, index = manager.approved_character_role_assets(self.paths, "CHAR_998", self.profile)
        self.assertEqual(assets, [])
        self.assertTrue(any("hash" in row["reason"].lower() for row in index["invalid_approved_records"]))

    def test_historical_approved_illustrations_are_not_ordinary_new_sources(self):
        prior = (
            (self.folder / "01_VARIATIONS" / "old-variation.png", "APPROVED_VARIATION"),
            (self.folder / "02_SCENES" / "old-scene.png", "APPROVED_SCENE"),
            (self.paths.generations / "02_APPROVED_STANDALONE" / "old-standalone.png", "APPROVED_STANDALONE"),
        )
        for index, (image, status) in enumerate(prior):
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), (140 + index, 50, 80)).save(image)
            self.record(image, status, hash_bound=True)
            self.assertIsNone(manager.registered_approved_generation(self.paths, image))
            with self.assertRaises(manager.StylePackError):
                manager.validate_plan_reference(self.paths, str(image), "STYLE")
        self.record(self.wardrobe, "APPROVED_WARDROBE", hash_bound=True)
        self.assertEqual(manager.validate_plan_reference(self.paths, str(self.wardrobe), "CLOTHES")["status"], "APPROVED_WARDROBE")


if __name__ == "__main__":
    unittest.main()
