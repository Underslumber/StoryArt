import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from PIL import Image

from tools import style_pack_manager as manager
from tests import test_character_asset_catalog as catalog_fixtures


class AuthorAcceptedSceneTests(unittest.TestCase):
    setUp = catalog_fixtures.CharacterAssetCatalogTests.setUp
    tearDown = catalog_fixtures.CharacterAssetCatalogTests.tearDown
    _generation = catalog_fixtures.CharacterAssetCatalogTests._generation

    def scene(self):
        image = self.folder / "candidate-scene.png"
        Image.new("RGB", (24, 32), (35, 80, 125)).save(image)
        return image

    def args(self, image, **overrides):
        values = {
            "workspace": self.workspace, "style_name": "RIOT LOL SPLASH",
            "image": str(image), "character_id": "CHAR_001", "request_id": "author-scene-r21",
            "kind": "scene", "description": "Pelun camp scene R21", "risk_level": "D1",
            "notes": "Visual QA: signature mismatch and soot detail needs review.",
            "approval_quote": "Save this exact scene despite the noted visual issues.",
            "user_approved": True, "author_accepted_scene": True,
            "accepted_image_sha256": manager.sha256(image), "fidelity": 90,
            "parent_generation": "", "alternative_only": False,
        }
        values.update(overrides)
        return type("Args", (), values)()

    def test_author_acceptance_stores_scene_without_claiming_technical_qa(self):
        image = self.scene()
        with patch.object(manager, "ensure_generation_library"), \
             patch.object(manager, "ensure_generation_archived") as archive, redirect_stdout(io.StringIO()):
            manager.command_approve_variation(self.args(image))
        archive.assert_not_called()
        rows = manager.read_csv(self.paths.generation_manifest)
        self.assertEqual(len(rows), 2)
        row = next(row for row in rows if row["status"] == "APPROVED_SCENE")
        approved = self.folder / "02_SCENES" / image.name
        self.assertEqual(row["status"], "APPROVED_SCENE")
        self.assertEqual(row["style_file"], str(approved))
        self.assertEqual(row["qa_output_sha256"], manager.sha256(image))
        self.assertEqual(row["reference_plan"], "")
        self.assertEqual(row["qa_evidence"], "")
        self.assertIn("Visual QA: signature mismatch", row["notes"])
        self.assertIn("Save this exact scene despite the noted visual issues.", row["notes"])
        self.assertEqual(approved.read_bytes(), image.read_bytes())
        self.assertFalse(manager.generation_has_passed_qa(row))

        first_id = row["generation_id"]
        with patch.object(manager, "ensure_generation_library"), redirect_stdout(io.StringIO()):
            manager.command_approve_variation(self.args(
                approved, description="Reworded retry description", notes="Different later QA wording.",
                approval_quote="Same accepted image.",
            ))
        retried_rows = manager.read_csv(self.paths.generation_manifest)
        self.assertEqual(len(retried_rows), 2)
        retry_row = next(row for row in retried_rows if row["status"] == "APPROVED_SCENE")
        self.assertEqual(retry_row["generation_id"], first_id)
        self.assertEqual(retry_row["description"], row["description"])
        self.assertEqual(retry_row["notes"], row["notes"])
        self.assertEqual(approved.read_bytes(), image.read_bytes())

    def test_rejects_invalid_author_acceptance_before_any_write(self):
        image = self.scene()
        initial_rows = manager.read_csv(self.paths.generation_manifest)
        bad_cases = (
            {"accepted_image_sha256": "0" * 64},
            {"kind": "variation"},
            {"character_id": "CHAR_999"},
            {"user_approved": False},
            {"approval_quote": " "},
            {"notes": " "},
            {"fidelity": 80},
            {"risk_level": "D11"},
            {"description": " "},
            {"description": "Pelun camp scene [D2]", "risk_level": "D1"},
        )
        for overrides in bad_cases:
            with self.subTest(overrides=overrides), \
                 patch.object(manager, "ensure_generation_library") as ensure, \
                 self.assertRaises(manager.StylePackError):
                manager.command_approve_variation(self.args(image, **overrides))
                ensure.assert_not_called()
            self.assertEqual(manager.read_csv(self.paths.generation_manifest), initial_rows)
            self.assertFalse((self.folder / "02_SCENES").exists())

    def test_rejects_acceptance_flag_for_other_approval_kind(self):
        image = self.scene()
        args = self.args(image, author_accepted_scene=False, accepted_image_sha256=manager.sha256(image))
        with patch.object(manager, "ensure_generation_library") as ensure, self.assertRaises(manager.StylePackError):
            manager.command_approve_variation(args)
        ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
