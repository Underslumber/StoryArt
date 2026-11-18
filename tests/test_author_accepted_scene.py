import io
import copy
import os
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

    def test_anonymous_scene_requires_registered_latest_attempt_and_is_idempotent(self):
        # This temporary fixture is independent of the live chat's request registry.
        environment = patch.dict(os.environ, {"CODEX_THREAD_ID": ""})
        environment.start()
        self.addCleanup(environment.stop)
        request_id = "anonymous-scene"
        root = self.paths.generations / "00_PENDING" / request_id
        root.mkdir(parents=True)
        image = root / "scene.png"
        Image.new("RGB", (24, 32), (35, 80, 125)).save(image)
        source = {"generation_id": "SOURCE", "request_id": request_id, "character_id": "NONE",
                  "status": "REJECTED", "qa_output_sha256": manager.sha256(image),
                  "qa_plan_sha256": "plan-hash", "reference_plan": "immutable-plan"}
        manager.write_csv(self.paths.generation_manifest, manager.GENERATION_FIELDS, [source])
        state = {"request_id": request_id, "task_revision": 2, "attempts": [{
            "attempt_id": "latest", "status": "RESULT_AVAILABLE", "task_revision": 2,
            "provider_artifact": {"sha256": manager.sha256(image)},
            "execution_snapshot": {"request_purpose": "SCENE", "execution_call": {"character_id": "NONE"},
                                   "reference_plan_snapshot_sha256": "plan-hash"}}]}
        qa = {"visual_review": {"attempt_id": "latest", "task_revision": 2}}
        args = self.args(image, request_id=request_id, character_id="NONE")
        with patch.object(manager, "load_execution_guard", return_value=state), \
             patch.object(manager, "generation_qa_evidence", return_value=qa), \
             patch.object(manager, "ensure_generation_library"), redirect_stdout(io.StringIO()):
            manager.command_approve_variation(args)
            manager.command_approve_variation(args)
        rows = manager.read_csv(self.paths.generation_manifest)
        self.assertEqual(len(rows), 2)
        approved = next(row for row in rows if row["status"] == "APPROVED_SCENE")
        self.assertEqual(approved["parent_generation"], "SOURCE")
        self.assertEqual(approved["character_id"], "NONE")
        self.assertEqual(approved["qa_evidence"], "")
        self.assertEqual(approved["reference_plan"], "immutable-plan")
        saved = self.paths.generations / "02_APPROVED_STANDALONE/02_SCENES/scene.png"
        self.assertEqual(saved.read_bytes(), image.read_bytes())
        self.assertEqual(rows[0]["status"], "REJECTED")
        completed = copy.deepcopy(state)
        completed["attempts"][0].update(status="DELIVERED", result_status="APPROVED_SCENE")
        binding = {"attempt_id": "latest", "task_revision": 2, "output_sha256": manager.sha256(image),
                   "generation_id": approved["generation_id"], "receipt_path": str(root / "AUTHOR_SCENE_COMPLETION.json")}
        completed["author_scene_storage"] = binding
        with patch.object(manager, "load_execution_guard", return_value=completed), \
             patch.object(manager, "generation_qa_evidence", return_value=qa), \
             patch("tools.author_scene_completion.validate", return_value=binding), \
             patch.object(manager, "ensure_generation_library"), redirect_stdout(io.StringIO()):
            manager.command_approve_variation(args)
        self.assertEqual(manager.read_csv(self.paths.generation_manifest), rows)
        for invalid_binding in ({}, {**binding, "attempt_id": "foreign"}, {**binding, "output_sha256": "0" * 64}):
            completed["author_scene_storage"] = invalid_binding
            with patch.object(manager, "load_execution_guard", return_value=completed), self.assertRaises(manager.StylePackError):
                manager.command_approve_variation(args)
        self.assertEqual(manager.read_csv(self.paths.generation_manifest), rows)
        for change in ("foreign_request", "stale_revision", "different_output", "wrong_attempt_qa"):
            with self.subTest(change=change):
                bad = copy.deepcopy(state)
                bad_qa = copy.deepcopy(qa)
                if change == "foreign_request": bad["request_id"] = "foreign"
                if change == "stale_revision": bad["task_revision"] = 3
                if change == "different_output": bad["attempts"][0]["provider_artifact"]["sha256"] = "0" * 64
                if change == "wrong_attempt_qa": bad_qa["visual_review"]["attempt_id"] = "older"
                with patch.object(manager, "load_execution_guard", return_value=bad), \
                     patch.object(manager, "generation_qa_evidence", return_value=bad_qa), \
                     self.assertRaises(manager.StylePackError):
                    manager.command_approve_variation(args)
                self.assertEqual(manager.read_csv(self.paths.generation_manifest), rows)


if __name__ == "__main__":
    unittest.main()
