"""Synthetic confirmed-profile lifecycle; no real character files are touched."""

import json
import hashlib
import shutil
import unittest
import uuid
import io
from contextlib import redirect_stdout
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from tools import character_profile_state as state
from tools.reference_compatibility import load_character_identity
from tools import style_pack_manager as manager


ROOT = Path(__file__).resolve().parents[1]


def _qa_fields(image: Path, request_id: str) -> dict[str, str]:
    """Create a real manager-recognized QA receipt for synthetic manifest rows."""
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


class ConfirmedProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = ROOT / ".test_profile_state" / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.profile = self.folder / "CHARACTER_PROFILE.yaml"
        self.profile.write_text(
            "character_id: CHAR_999\nname: Test\nstatus: APPROVED\n"
            "identity:\n  gender_identity: woman\n  visible_presentation: feminine\n"
            "locked_invariants:\n  hair: silver\nwardrobe_references: []\n"
            "accessory_references: []\nface_variant_references: []\nbody_variant_references: []\n",
            encoding="utf-8",
        )
        self.outfit_a = self.folder / "outfit-a.png"
        self.outfit_b = self.folder / "outfit-b.png"
        self.accessory = self.folder / "accessory.png"
        self.face = self.folder / "face.png"
        for image in (self.outfit_a, self.outfit_b, self.accessory, self.face):
            image.write_bytes(image.name.encode("utf-8"))

    def tearDown(self):
        self.assertTrue(self.folder.resolve().is_relative_to(ROOT.resolve()))
        shutil.rmtree(self.folder)

    def confirm(self, patch=None, changes=None, expected=0, operation="one", alternative=False):
        evidence = {
            role: [{"path": str(Path(value).resolve()), "sha256": manager.sha256(Path(value))} for value in values]
            for role, values in (changes or {}).items()
        }
        return state.confirm(self.profile, patch=patch or {}, active_changes=changes or {},
                             expected_revision=expected, operation_id=operation,
                             user_confirmation="User confirmed this synthetic change",
                             alternative_only=alternative, approved_assets=evidence)

    def test_nested_fact_survives_reload_and_raw_yaml_edit_stays_inactive(self):
        first = self.confirm({"appearance": {"eyes": {"iris": "violet", "freckles": "three"}}})
        self.assertEqual(first["revision"], 1)
        self.profile.write_text(self.profile.read_text(encoding="utf-8") + "appearance:\n  eyes:\n    iris: orange\n", encoding="utf-8")
        effective = state.load_effective(self.profile)
        self.assertEqual(effective["profile"]["appearance"]["eyes"]["iris"], "violet")
        self.assertIn('"freckles": "three"', state.facts_block(effective))
        self.assertEqual(load_character_identity(self.profile)["gender_identity"], "woman")

    def test_wardrobe_replacement_accessory_addition_and_alternative_only(self):
        one = self.confirm(changes={"wardrobe": [str(self.outfit_a)], "accessory": [str(self.accessory)]})
        two = self.confirm(changes={"wardrobe": [str(self.outfit_b)]}, expected=1, operation="two")
        three = self.confirm(changes={"face_variant": [str(self.face)]}, expected=2, operation="three", alternative=True)
        self.assertEqual(three["active_assets"]["wardrobe"], [str(self.outfit_b.resolve())])
        self.assertEqual(three["active_assets"]["accessory"], [str(self.accessory.resolve())])
        self.assertEqual(three["active_assets"]["face_variant"], [])
        self.assertEqual(three["alternatives"]["wardrobe"], [str(self.outfit_a.resolve()), str(self.outfit_b.resolve())])
        self.assertEqual(three["alternatives"]["face_variant"], [str(self.face.resolve())])
        self.assertEqual(three["profile"]["character_id"], "CHAR_999")
        self.assertEqual(one["revision"], 1)

    def test_idempotent_retry_stale_revision_and_protected_fields(self):
        first = self.confirm({"traits": {"temper": "calm"}})
        self.assertEqual(self.confirm({"traits": {"temper": "calm"}}), first)
        with self.assertRaisesRegex(state.ProfileStateError, "Stale"):
            self.confirm({"traits": {"temper": "alert"}}, operation="two")
        with self.assertRaisesRegex(state.ProfileStateError, "different content"):
            self.confirm({"traits": {"temper": "alert"}}, operation="one")
        with self.assertRaisesRegex(state.ProfileStateError, "Protected"):
            self.confirm({"character_id": "CHAR_001"}, expected=1, operation="bad")
        with self.assertRaisesRegex(state.ProfileStateError, "approved supported role"):
            self.confirm({"pet_image": "pet.png"}, expected=1, operation="unsupported")

    def test_interrupted_publish_leaves_previous_revision_active_and_tampering_fails(self):
        first = self.confirm({"traits": {"temper": "calm"}})
        real_write = state._atomic_json
        def interrupted(path, value):
            if path.name == "ACTIVE.json":
                raise OSError("simulated interrupted pointer update")
            return real_write(path, value)
        with patch.object(state, "_atomic_json", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.confirm({"traits": {"temper": "alert"}}, expected=1, operation="two")
        self.assertEqual(state.load_effective(self.profile), first)
        second = self.confirm({"traits": {"temper": "alert"}}, expected=1, operation="two")
        self.assertEqual(second["revision"], 2)
        active = self.folder / "CONFIRMED_PROFILE" / "ACTIVE.json"
        revision = active.parent / json.loads(active.read_text(encoding="utf-8"))["file"]
        revision.write_text(revision.read_text(encoding="utf-8").replace("alert", "changed"), encoding="utf-8")
        with self.assertRaisesRegex(state.ProfileStateError, "hash"):
            state.load_effective(self.profile)


class ManagerProfileFlowTests(unittest.TestCase):
    def setUp(self):
        self.workspace = ROOT / ".test_profile_flow" / uuid.uuid4().hex
        self.workspace.mkdir(parents=True)
        self.paths = manager.make_paths(self.workspace, "TEST")
        self.folder = self.paths.generations / "01_APPROVED_CHARACTERS" / "CHAR_999_Test"
        self.base = self.folder / "01_APPROVED_BASE" / "base.png"
        self.wardrobe = self.folder / "03_CHARACTER_REFERENCES" / "03_WARDROBE" / "suit.png"
        self.accessory = self.folder / "03_CHARACTER_REFERENCES" / "04_ACCESSORIES" / "glasses.png"
        for index, image in enumerate((self.base, self.wardrobe, self.accessory)):
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), (40 + index, 80, 120)).save(image)
        self.profile = self.folder / "CHARACTER_PROFILE.yaml"
        self.profile.write_text(
            "character_id: CHAR_999\nname: Test\nstatus: APPROVED\n"
            "identity:\n  gender_identity: woman\n  visible_presentation: feminine\n"
            "wardrobe_references: []\naccessory_references: []\n",
            encoding="utf-8",
        )
        manager.write_csv(self.paths.character_registry, manager.CHARACTER_FIELDS, [{
            "character_id": "CHAR_999", "name": "Test", "approved_base": str(self.base),
            "profile_path": str(self.profile), "face_references": "", "body_references": "", "status": "APPROVED",
        }])
        for image, role in ((self.base, "APPROVED_CHARACTER_BASE"), (self.wardrobe, "APPROVED_WARDROBE"), (self.accessory, "APPROVED_ACCESSORY")):
            qa = _qa_fields(image, image.stem)
            manager.append_generation(self.paths, request_id=image.stem, character_id="CHAR_999", status=role,
                                      fidelity=90, risk_level="D1", description=image.stem,
                                      source_image=image, archive_file=image, style_file=image, **qa)

    def tearDown(self):
        self.assertTrue(self.workspace.resolve().is_relative_to(ROOT.resolve()))
        shutil.rmtree(self.workspace)

    def test_confirmed_facts_and_default_assets_reach_exact_call_and_stale_plan_fails(self):
        with patch.object(manager, "generation_has_passed_qa", return_value=True):
            state.confirm(self.profile, patch={"appearance": {"jewelry": {"emblem": "silver crescent"}}},
                          active_changes={"wardrobe": [str(self.wardrobe)], "accessory": [str(self.accessory)]},
                          expected_revision=0, operation_id="approved", user_confirmation="User approved outfit, accessory and emblem",
                          approved_assets=manager.approved_profile_asset_evidence(self.paths, "CHAR_999", self.profile))
            resolved = manager.effective_character_profile(self.paths, "CHAR_999")
            args = Namespace(clothes_reference="", accessory_reference=[], face_variant_reference=[], body_variant_reference=[],
                             suppress_profile_default=[], character_assembly="", character_reference_mode="ASSEMBLY_ONLY",
                             shot_complexity="NORMAL", selected_body_view="ASSEMBLY", primary_face="", body_reference="")
            manager.apply_confirmed_defaults(self.paths, args, "CHAR_999", resolved, set())
            self.assertEqual(args.clothes_reference, str(self.wardrobe.resolve()))
            self.assertEqual(args.accessory_reference, [str(self.accessory.resolve())])
            self.assertEqual(args.character_assembly, str(self.base))
            plan = {"character_id": "CHAR_999", "generation_purpose": "SCENE", "request_id": "fixture", "generation_workflow": {"mode": "SINGLE_PASS"},
                    "confirmed_character_profile": resolved}
            prompt = manager.render_confirmed_prompt(plan, "Draw Test in a hall.")
            self.assertIn("silver crescent", prompt)
            self.assertEqual(prompt.count("CONFIRMED_CHARACTER_DATA ("), 1)
            with patch.object(manager, "resolve_call_slots", return_value=[{"path": str(self.wardrobe), "sha256": manager.sha256(self.wardrobe), "slot": "CLOTHES", "active_roles": ["CLOTHES"], "physically_attach": True},
                                                                           {"path": str(self.accessory), "sha256": manager.sha256(self.accessory), "slot": "ACCESSORY", "active_roles": ["ACCESSORY"], "physically_attach": True}]), \
                 patch.object(manager, "validated_stage_outputs", return_value={}), \
                 patch.object(manager, "load_and_validate_risk_assessment", return_value=(self.workspace / "risk.json", {"generation_risk": "D1"})):
                call, _, _ = manager.resolve_execution_call(self.paths, self.workspace / "REFERENCE_PLAN.json", plan, "SINGLE_PASS", prompt, "risk.json")
            self.assertEqual(call["prompt"]["text"], prompt)
            self.assertEqual(call["confirmed_character_profile"]["revision"], 1)
            self.assertEqual([slot["sha256"] for slot in call["slots"]], [manager.sha256(self.wardrobe), manager.sha256(self.accessory)])
            original_wardrobe = self.wardrobe.read_bytes()
            self.wardrobe.write_bytes(b"tampered")
            with self.assertRaisesRegex(manager.StylePackError, "changed since preparation"):
                manager.assert_profile_plan_current(self.paths, plan)
            self.wardrobe.write_bytes(original_wardrobe)
            altered_plan = {**plan, "confirmed_character_profile": {**resolved, "facts": {"appearance": "tampered"}}}
            with self.assertRaisesRegex(manager.StylePackError, "changed since preparation"):
                manager.assert_profile_plan_current(self.paths, altered_plan)
            with self.assertRaisesRegex(manager.StylePackError, "differ"):
                manager.render_confirmed_prompt(plan, "Draw Test.\nCONFIRMED_CHARACTER_DATA: forged")
            suppressed = Namespace(**{**vars(args), "clothes_reference": "", "accessory_reference": [], "suppress_profile_default": ["WARDROBE", "ACCESSORY"]})
            manager.apply_confirmed_defaults(self.paths, suppressed, "CHAR_999", resolved, set())
            self.assertEqual(suppressed.clothes_reference, "")
            self.assertEqual(suppressed.accessory_reference, [])
            scene_text_override = Namespace(**{**vars(args), "clothes_reference": "", "accessory_reference": [], "suppress_profile_default": []})
            manager.apply_confirmed_defaults(self.paths, scene_text_override, "CHAR_999", resolved, {"CLOTHES", "ACCESSORY", "FACE", "BODY"})
            self.assertEqual(scene_text_override.clothes_reference, "")
            self.assertEqual(scene_text_override.accessory_reference, [])
            self.assertEqual(manager.effective_character_profile(self.paths, "CHAR_999")["revision"], 1)
            state.confirm(self.profile, patch={"appearance": {"jewelry": {"emblem": "gold crescent"}}}, active_changes={},
                          expected_revision=1, operation_id="new-fact", user_confirmation="User approved gold emblem")
            with self.assertRaisesRegex(manager.StylePackError, "changed since preparation"):
                manager.assert_profile_plan_current(self.paths, plan)


class FirstApprovalRetryTests(unittest.TestCase):
    def test_interrupted_first_publication_retries_same_character(self):
        workspace = ROOT / ".test_profile_approval" / uuid.uuid4().hex
        workspace.mkdir(parents=True)
        try:
            paths = manager.make_paths(workspace, "TEST")
            with redirect_stdout(io.StringIO()):
                manager.command_init(Namespace(workspace=workspace, style_name="TEST", source=""))
            pending = paths.generations / "00_PENDING" / "original-request"
            pending.mkdir(parents=True)
            images = [workspace / name for name in ("base.png", "face.png", "front.png", "side.png", "back.png")]
            for index, image in enumerate(images):
                Image.new("RGB", (16, 16), (40 + index, 60, 80)).save(image)
            args = Namespace(workspace=workspace, style_name="TEST", user_approved=True, request_id="original-request",
                             image=str(images[0]), face_reference=[str(images[1])], body_reference=[str(v) for v in images[2:]],
                             wardrobe_reference=[], accessory_reference=[], name="Test", notes="", approval_quote="User approved Test",
                             fidelity=90, risk_level="D1")
            real_confirm = manager.confirm_profile_state
            calls = 0
            def interrupted(*call_args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("simulated first publication failure")
                return real_confirm(*call_args, **kwargs)
            with patch.object(manager, "require_qa_passed_generation_for_approval", return_value={"character_id": "NEW"}), \
                 patch.object(manager, "require_generated_character_reference"), \
                 patch.object(manager, "approval_provenance", return_value=("parent", "", "", {}, "approved")), \
                 patch.object(manager, "generation_has_passed_qa", return_value=True), \
                 patch.object(manager, "confirm_profile_state", side_effect=interrupted):
                with self.assertRaisesRegex(manager.StylePackError, "publication failed"):
                    manager.command_approve_character(args)
                rows = manager.read_csv(paths.character_registry)
                self.assertEqual(len(rows), 1)
                profile = Path(rows[0]["profile_path"])
                with self.assertRaisesRegex(state.ProfileStateError, "requires confirmed profile publication"):
                    state.load_effective(profile)
                manager.command_approve_character(args)
            self.assertEqual([row["character_id"] for row in manager.read_csv(paths.character_registry)], ["CHAR_001"])
            self.assertEqual(len([row for row in manager.read_csv(paths.generation_manifest) if row["status"] == "APPROVED_CHARACTER_BASE"]), 1)
            self.assertEqual(state.load_effective(profile)["revision"], 1)
        finally:
            self_assert_path = workspace.resolve()
            if self_assert_path.is_relative_to(ROOT.resolve()):
                shutil.rmtree(workspace)


if __name__ == "__main__":
    unittest.main()
