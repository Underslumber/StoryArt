import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from PIL import Image

from tools import character_profile_state as profile_state
from tools import style_pack_manager as manager
from tests import test_character_asset_catalog as catalog_fixtures

_qa_fields = catalog_fixtures._qa_fields


class CharacterSketchTests(unittest.TestCase):
    setUp = catalog_fixtures.CharacterAssetCatalogTests.setUp
    tearDown = catalog_fixtures.CharacterAssetCatalogTests.tearDown
    _generation = catalog_fixtures.CharacterAssetCatalogTests._generation

    def sketch(self, status="APPROVED_SKETCH", with_qa=True):
        image = self.folder / "03_CHARACTER_REFERENCES/07_USEFUL_SKETCHES/silhouette.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (100, 80, 90)).save(image)
        self._generation("Study of the coat silhouette", image, status, with_qa=with_qa)
        return image

    def test_catalog_resolver_and_identity_boundaries(self):
        image = self.sketch()
        assets, index = manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["role"], "SKETCH")
        self.assertEqual(assets[0]["usage"], "STYLE_SUPPORT_ONLY")
        self.assertNotIn("SKETCH", index["approved_records_missing_from_profile"])
        output = io.StringIO()
        with redirect_stdout(output):
            manager.command_resolve_character(type("Args", (), {"workspace": self.workspace, "name": "Chance", "json": True})())
        result = json.loads(output.getvalue())["matches"][0]
        self.assertEqual(json.loads(result["useful_sketches"])[0]["path"], str(image.resolve()))
        style_support = manager.validate_plan_reference(self.paths, str(image), "STYLE")
        self.assertEqual(style_support["inferred_roles"], ["STYLE", "SKETCH"])
        self.assertEqual(style_support["usage"], "STYLE_SUPPORT_ONLY")
        self.assertEqual(style_support["asset_role"], "SKETCH")
        self.assertEqual(style_support["character_id"], "CHAR_001")
        self.assertEqual(style_support["reference_scope"], "CURRENT_REQUEST_ONLY")
        self.assertFalse(style_support["permanent_anchor"])
        self.assertTrue(style_support["canonical_identity_priority"])
        for role in ("FACE", "BODY", "CLOTHES", "ASSEMBLY", "POSE", "ACCESSORY"):
            with self.subTest(role=role), self.assertRaises(manager.StylePackError):
                manager.validate_plan_reference(self.paths, str(image), role)
        evidence = manager.approved_profile_asset_evidence(self.paths, "CHAR_001", self.profile)
        self.assertNotIn("sketch", evidence)

    def test_catalog_revocation_tamper_wrong_directory_and_missing_qa(self):
        image = self.sketch()
        self._generation("revoked", image, "REJECTED")
        self.assertEqual(manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)[0], [])
        image = self.sketch()
        image.write_bytes(b"changed")
        self.assertEqual(manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)[0], [])
        image = self.sketch(with_qa=False)
        self.assertEqual(manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)[0], [])
        self._generation("wrong path", self.wardrobe, "APPROVED_SKETCH")
        self.assertEqual(manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)[0], [])

    def args(self, image, description="Useful coat silhouette", quote="Save this coat silhouette study"):
        return manager.build_parser().parse_args([
            "approve-variation", "--workspace", str(self.workspace), "--style-name", "RIOT LOL SPLASH",
            "--image", str(image), "--character-id", "CHAR_001", "--request-id", "approve-sketch",
            "--kind", "sketch", "--description", description, "--risk-level", "D1",
            "--approval-quote", quote, "--user-approved",
        ])

    def test_sketch_approval_requires_specific_input_before_source_lookup(self):
        for description, quote in ((" ", "Save it"), ("Useful silhouette", " ")):
            with patch.object(manager, "ensure_generation_library"), patch.object(manager, "require_qa_passed_generation_for_approval") as lookup:
                with self.assertRaises(manager.StylePackError):
                    manager.command_approve_variation(self.args(self.base, description, quote))
                lookup.assert_not_called()

    def test_approval_uses_existing_pipeline_without_profile_publication(self):
        source = self.workspace / "source.png"
        Image.new("RGB", (16, 16)).save(source)
        plan = self.workspace / "source-plan.json"
        plan.write_text('{"character_id":"CHAR_001"}', encoding="utf-8")
        append = manager.append_generation
        def register(*args, **kwargs):
            return append(*args, **{**kwargs, **_qa_fields(kwargs["style_file"], kwargs["request_id"])})
        with patch.object(manager, "ensure_generation_library"), \
             patch.object(manager, "require_qa_passed_generation_for_approval", return_value={"character_id": "CHAR_001", "reference_plan": str(plan)}) as qa, \
             patch.object(manager, "ensure_generation_archived", return_value=source), \
             patch.object(manager, "approval_provenance", return_value=("SOURCE", str(plan), "qa.json", {}, "notes")), \
             patch.object(manager, "append_generation", side_effect=register), \
             patch.object(manager, "confirm_profile_state") as confirm, \
             patch.object(manager, "sync_character_profile_asset") as sync, redirect_stdout(io.StringIO()):
            manager.command_approve_variation(self.args(source))
            qa.assert_called_once()
            confirm.assert_not_called()
            sync.assert_not_called()
        assets, _ = manager.approved_character_role_assets(self.paths, "CHAR_001", self.profile)
        self.assertEqual(assets[0]["status"], "APPROVED_SKETCH")
        self.assertIn("sketch_approval_quote=Save this coat silhouette study", assets[0]["provenance"])
        self.assertFalse((self.folder / "CONFIRMED_PROFILE/ACTIVE.json").exists())

    def test_catalog_metadata_is_not_automatic_prompt_facts(self):
        self.assertEqual(profile_state.text_facts({"useful_sketches": {"purpose": "silhouette"}, "identity": {"name": "Chance"}}), {"identity": {"name": "Chance"}})


if __name__ == "__main__":
    unittest.main()
