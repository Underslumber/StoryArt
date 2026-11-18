"""Synthetic confirmed-profile lifecycle; no real character files are touched."""

import json
import hashlib
import shutil
import tempfile
import unittest
import uuid
import io
from concurrent.futures import ThreadPoolExecutor
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
            with_back_variant = {
                **resolved,
                "active_assets": {**resolved["active_assets"], "body_variant": [{"path": "approved-back.png"}]},
            }
            for mode in ("ASSEMBLY_ONLY", "ASSEMBLY_PLUS_FACE", "IDENTITY_STRICT", "AUTO", "ASSEMBLY_PLUS_VIEW"):
                with self.subTest(mode=mode):
                    scene = Namespace(**{**vars(args), "character_reference_mode": mode})
                    self.assertEqual("BODY_VARIANT" in manager.suppressed_profile_defaults(scene), mode != "ASSEMBLY_PLUS_VIEW")
                    manager.apply_confirmed_defaults(self.paths, scene, "CHAR_999", with_back_variant, set())
                    self.assertEqual(scene.body_variant_reference, ["approved-back.png"] if mode == "ASSEMBLY_PLUS_VIEW" else [])
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
    def test_character_approval_uses_author_gated_face_and_never_falls_back_to_later_staging_face(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            face_a = root / "face-a.png"
            face_b = root / "face-b.png"
            assembly = root / "assembly.png"
            Image.new("RGB", (8, 8), (1, 2, 3)).save(face_a)
            Image.new("RGB", (8, 8), (3, 2, 1)).save(face_b)
            Image.new("RGB", (8, 8), (2, 3, 1)).save(assembly)
            paths = type("Paths", (), {"generation_manifest": root / "GENERATION_MANIFEST.csv", "generations": root / "TEST_GENERATIONS"})()
            rows = [
                {"request_id": "request", "status": "STAGING", "style_file": str(face_b), "notes": "[STAGE_ID=01_FACE_IDENTITY]"},
                {"request_id": "request", "status": "TEST", "style_file": str(assembly), "notes": "[STAGE_ID=05_CHARACTER_ASSEMBLY]"},
            ]
            accepted = {"01_FACE_IDENTITY": {"path": str(face_a), "qa_passed": True}}
            for stage, color in (("02_PHYSIQUE_FRONT", (10, 0, 0)), ("03_PHYSIQUE_SIDE", (0, 10, 0)), ("04_PHYSIQUE_BACK", (0, 0, 10))):
                path = root / f"{stage}.png"
                Image.new("RGB", (8, 8), color).save(path)
                accepted[stage] = {"path": str(path), "qa_passed": True}
            by_file = {Path(row["style_file"]): row for row in rows}
            with patch.object(manager, "validated_stage_outputs", return_value=accepted), \
                 patch.object(manager, "select_character_approval_source_generation", return_value=(rows[1], {"record_status": "TEST", "qa_failed": [], "qa_contract": "fixture.json"})), \
                 patch.object(manager, "read_csv", return_value=rows), \
                 patch.object(manager, "newest_matching_generation", side_effect=lambda _paths, file: by_file.get(Path(file))), \
                 patch.object(manager, "generation_has_passed_qa", return_value=True):
                selected, source, _ = manager.resolve_character_approval_stage_files(paths, "request", root / "REFERENCE_PLAN.json", {})
            self.assertEqual(selected["01_FACE_IDENTITY"], face_a.resolve())
            self.assertEqual(selected["05_CHARACTER_ASSEMBLY"], assembly.resolve())
            self.assertEqual(source, rows[1])

            unresolved = {key: value for key, value in accepted.items() if key != "01_FACE_IDENTITY"}
            with patch.object(manager, "validated_stage_outputs", return_value=unresolved), \
                 patch.object(manager, "select_character_approval_source_generation", return_value=(rows[1], {"record_status": "TEST", "qa_failed": [], "qa_contract": "fixture.json"})), \
                 patch.object(manager, "read_csv", return_value=rows), \
                 patch.object(manager, "newest_matching_generation", side_effect=lambda _paths, file: by_file.get(Path(file))), \
                 patch.object(manager, "generation_has_passed_qa", return_value=True):
                selected, _, _ = manager.resolve_character_approval_stage_files(paths, "request", root / "REFERENCE_PLAN.json", {})
            self.assertNotIn("01_FACE_IDENTITY", selected)

    def test_replaced_face_invalidates_previously_accepted_assembly_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stages = {}
            for index, stage in enumerate(("01_FACE_IDENTITY", "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK")):
                path = root / f"{stage}.png"
                Image.new("RGB", (8, 8), (index, 40, 80)).save(path)
                stages[stage] = path
            old_face_hash = manager.sha256(stages["01_FACE_IDENTITY"])
            Image.new("RGB", (8, 8), (240, 1, 1)).save(stages["01_FACE_IDENTITY"])
            assembly = root / "assembly.png"
            Image.new("RGB", (8, 8), (80, 40, 0)).save(assembly)
            snapshot_path = assembly.with_suffix(assembly.suffix + ".qa-plan.json")
            def write_snapshot(face_hash):
                snapshot_path.write_text(json.dumps({"request_id": "request", "character_id": "NEW",
                    "execution_call": {"stage_id": "05_CHARACTER_ASSEMBLY", "stage_output_bindings": [
                        {"stage_id": stage, "sha256": face_hash if stage == "01_FACE_IDENTITY" else manager.sha256(path)}
                        for stage, path in stages.items()
                    ]}}), encoding="utf-8")
            write_snapshot(old_face_hash)
            with self.assertRaisesRegex(manager.StylePackError, "01_FACE_IDENTITY"):
                manager.validate_character_assembly_stage_snapshot(json.loads(snapshot_path.read_text()), "request", "NEW", stages)
            write_snapshot(manager.sha256(stages["01_FACE_IDENTITY"]))
            manager.validate_character_assembly_stage_snapshot(json.loads(snapshot_path.read_text()), "request", "NEW", stages)

    def test_assembly_validation_uses_canonical_manager_qa_snapshot_not_alias_sidecar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stages = {}
            bindings = []
            for index, stage in enumerate(("01_FACE_IDENTITY", "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK")):
                path = root / f"{stage}.png"
                Image.new("RGB", (8, 8), (index, 40, 80)).save(path)
                stages[stage] = path
                bindings.append({"stage_id": stage, "sha256": manager.sha256(path)})
            canonical = root / "assembly.png"
            alias = root / "alias.png"
            Image.new("RGB", (8, 8), (80, 40, 0)).save(canonical)
            alias.write_bytes(canonical.read_bytes())
            canonical_snapshot = canonical.with_suffix(canonical.suffix + ".qa-plan.json")
            canonical_snapshot.write_text(json.dumps({"request_id": "request", "character_id": "NEW",
                "execution_call": {"stage_id": "05_CHARACTER_ASSEMBLY", "stage_output_bindings": bindings}}), encoding="utf-8")
            alias_snapshot = alias.with_suffix(alias.suffix + ".qa-plan.json")
            bad_bindings = [dict(row) for row in bindings]
            bad_bindings[0]["sha256"] = "f" * 64
            alias_snapshot.write_text(json.dumps({"request_id": "request", "character_id": "NEW",
                "execution_call": {"stage_id": "05_CHARACTER_ASSEMBLY", "stage_output_bindings": bad_bindings}}), encoding="utf-8")
            contract = canonical.with_suffix(canonical.suffix + ".qa-contract.json")
            contract.write_text(json.dumps({"stage_id": "05_CHARACTER_ASSEMBLY", "plan_snapshot": str(canonical_snapshot)}), encoding="utf-8")
            row = {"style_file": str(canonical), "request_id": "request", "character_id": "NEW", "status": "TEST",
                   "notes": "[STAGE_ID=05_CHARACTER_ASSEMBLY]"}
            paths = type("Paths", (), {})()
            evidence = {"record_status": "TEST", "qa_failed": [], "qa_contract": str(contract)}
            manager.validate_character_assembly_stage_bindings(paths, "request", "NEW", canonical, stages, row, evidence)
            with self.assertRaisesRegex(manager.StylePackError, "01_FACE_IDENTITY"):
                canonical_snapshot.write_text(json.dumps({"request_id": "request", "character_id": "NEW",
                    "execution_call": {"stage_id": "05_CHARACTER_ASSEMBLY", "stage_output_bindings": bad_bindings}}), encoding="utf-8")
                manager.validate_character_assembly_stage_bindings(paths, "request", "NEW", canonical, stages, row, evidence)

    def test_retry_source_selection_skips_derived_approval_but_respects_newer_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "assembly.png"
            Image.new("RGB", (8, 8), (80, 40, 0)).save(image)
            snapshot = root / "plan.json"
            snapshot.write_text(json.dumps({"request_id": "request", "character_id": "NEW"}), encoding="utf-8")
            contract = root / "contract.json"
            contract.write_text(json.dumps({"stage_id": "05_CHARACTER_ASSEMBLY", "plan_snapshot": str(snapshot),
                                            "visual_review": {"attempt_id": "attempt-a"}}), encoding="utf-8")
            source = {"request_id": "request", "status": "TEST", "qa_output_sha256": manager.sha256(image),
                      "character_id": "NEW", "style_file": str(image),
                      "notes": "[STAGE_ID=05_CHARACTER_ASSEMBLY] [ATTEMPT_ID=attempt-a]"}
            derived = {**source, "status": "APPROVED_CHARACTER_BASE", "notes": "derived approval"}
            evidence = {"record_status": "TEST", "qa_failed": [], "qa_contract": str(contract),
                        "output_sha256": source["qa_output_sha256"]}
            paths = type("Paths", (), {"generation_manifest": root / "GENERATION_MANIFEST.csv", "generations": root / "TEST_GENERATIONS"})()
            with patch.object(manager, "read_csv", return_value=[source, derived]), \
                 patch.object(manager, "generation_qa_evidence", return_value=evidence), \
                 patch.object(manager, "load_execution_guard", return_value={}), \
                 patch.object(manager, "validate_stage_attempt_binding", return_value={"attempt_id": "attempt-a"}):
                self.assertIs(manager.select_character_approval_source_generation(
                    paths, image, "request", expected_stage="05_CHARACTER_ASSEMBLY",
                    expected_character_id="NEW", canonical_path=image), source)
            mutable_alias = root / "mutable-source.png"
            mutable_alias.write_bytes(b"changed after QA")
            with patch.object(manager, "read_csv", return_value=[{**source, "source_image": str(mutable_alias)}]), \
                 patch.object(manager, "generation_qa_evidence", return_value=evidence):
                with self.assertRaisesRegex(manager.StylePackError, "hash-verified TEST"):
                    manager.select_character_approval_source_generation(paths, mutable_alias, "request")
            rejected = {**source, "status": "REJECTED", "notes": "[STAGE_ID=05_CHARACTER_ASSEMBLY] [ATTEMPT_ID=attempt-b]"}
            with patch.object(manager, "read_csv", return_value=[source, derived, rejected]), \
                 patch.object(manager, "generation_qa_evidence", return_value=evidence), \
                 patch.object(manager, "load_execution_guard", return_value={}), \
                 patch.object(manager, "validate_stage_attempt_binding", return_value={"attempt_id": "attempt-a"}):
                with self.assertRaisesRegex(manager.StylePackError, "newer rejection"):
                    manager.select_character_approval_source_generation(
                        paths, image, "request", expected_stage="05_CHARACTER_ASSEMBLY",
                        expected_character_id="NEW", canonical_path=image)

    def test_early_copy_failure_does_not_consume_permanent_character_id(self):
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
            real_copy = manager.copy_approval_asset
            calls = 0
            def interrupt_copy(source, destination, staging_root):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise SystemExit("simulated hard interruption after durable intent")
                return real_copy(source, destination, staging_root)
            with patch.object(manager, "select_character_approval_source_generation", return_value={"character_id": "NEW"}), \
                 patch.object(manager, "require_generated_character_reference"), \
                 patch.object(manager, "assert_active_request_path"), \
                 patch.object(manager, "approval_provenance", return_value=("parent", "", "", {}, "approved")), \
                 patch.object(manager, "generation_has_passed_qa", return_value=True), \
                 patch.object(manager, "copy_approval_asset", side_effect=interrupt_copy):
                with self.assertRaisesRegex(SystemExit, "durable intent"):
                    manager.command_approve_character(args)
            character_root = paths.generations / "01_APPROVED_CHARACTERS"
            self.assertEqual(list(character_root.glob("CHAR_*")), [])
            self.assertEqual(len(list((character_root / ".approval-intents").glob("*.json"))), 1)
            with patch.object(manager, "select_character_approval_source_generation", return_value={"character_id": "NEW"}), \
                 patch.object(manager, "require_generated_character_reference"), \
                 patch.object(manager, "assert_active_request_path"), \
                 patch.object(manager, "approval_provenance", return_value=("parent", "", "", {}, "approved")), \
                 patch.object(manager, "generation_has_passed_qa", return_value=True):
                with redirect_stdout(io.StringIO()):
                    manager.command_approve_character(args)
            rows = manager.read_csv(paths.character_registry)
            self.assertEqual([row["character_id"] for row in rows], ["CHAR_001"])
        finally:
            if workspace.resolve().is_relative_to(ROOT.resolve()):
                shutil.rmtree(workspace)

    def test_promotion_before_registry_write_recovers_exact_reserved_character(self):
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
            real_write_csv = manager.write_csv
            def interrupt_registry(path, fields, rows):
                if Path(path) == paths.character_registry:
                    raise SystemExit("simulated stop after promotion before registry")
                return real_write_csv(path, fields, rows)
            with patch.object(manager, "select_character_approval_source_generation", return_value={"character_id": "NEW"}), \
                 patch.object(manager, "require_generated_character_reference"), \
                 patch.object(manager, "assert_active_request_path"), \
                 patch.object(manager, "approval_provenance", return_value=("parent", "", "", {}, "approved")), \
                 patch.object(manager, "generation_has_passed_qa", return_value=True), \
                 patch.object(manager, "write_csv", side_effect=interrupt_registry):
                with self.assertRaisesRegex(SystemExit, "before registry"):
                    manager.command_approve_character(args)
            character_root = paths.generations / "01_APPROVED_CHARACTERS"
            permanent = list(character_root.glob("CHAR_*"))
            self.assertEqual(len(permanent), 1)
            self.assertTrue((permanent[0] / "APPROVAL_OWNER.json").is_file())
            self.assertEqual(manager.read_csv(paths.character_registry), [])
            with patch.object(manager, "select_character_approval_source_generation", return_value={"character_id": "NEW"}), \
                 patch.object(manager, "require_generated_character_reference"), \
                 patch.object(manager, "assert_active_request_path"), \
                 patch.object(manager, "approval_provenance", return_value=("parent", "", "", {}, "approved")), \
                 patch.object(manager, "generation_has_passed_qa", return_value=True):
                with redirect_stdout(io.StringIO()):
                    manager.command_approve_character(args)
            rows = manager.read_csv(paths.character_registry)
            self.assertEqual([row["character_id"] for row in rows], ["CHAR_001"])
            self.assertEqual(len([row for row in manager.read_csv(paths.generation_manifest) if row["status"] == "APPROVED_CHARACTER_BASE"]), 1)
        finally:
            if workspace.resolve().is_relative_to(ROOT.resolve()):
                shutil.rmtree(workspace)

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
            with patch.object(manager, "select_character_approval_source_generation", return_value={"character_id": "NEW"}), \
                 patch.object(manager, "require_generated_character_reference"), \
                 patch.object(manager, "assert_active_request_path"), \
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
                changed_quote = Namespace(**{**vars(args), "approval_quote": "A different approval quote"})
                with self.assertRaisesRegex(manager.StylePackError, "different approval inputs"):
                    manager.command_approve_character(changed_quote)
                wardrobe = workspace / "changed-wardrobe.png"
                Image.new("RGB", (16, 16), (91, 92, 93)).save(wardrobe)
                changed_wardrobe = Namespace(**{**vars(args), "wardrobe_reference": [str(wardrobe)]})
                with self.assertRaisesRegex(manager.StylePackError, "different approval inputs"):
                    manager.command_approve_character(changed_wardrobe)
                face_copy = Path(rows[0]["face_references"].split(";")[0])
                face_bytes = face_copy.read_bytes()
                face_copy.write_bytes(b"tampered promoted FACE bytes")
                with self.assertRaisesRegex(manager.StylePackError, "reserved source"):
                    manager.command_approve_character(args)
                face_copy.write_bytes(face_bytes)
                with ThreadPoolExecutor(max_workers=2) as executor:
                    list(executor.map(manager.command_approve_character, [args, args]))
            self.assertEqual([row["character_id"] for row in manager.read_csv(paths.character_registry)], ["CHAR_001"])
            self.assertEqual(len([row for row in manager.read_csv(paths.generation_manifest) if row["status"] == "APPROVED_CHARACTER_BASE"]), 1)
            self.assertEqual(state.load_effective(profile)["revision"], 1)
        finally:
            self_assert_path = workspace.resolve()
            if self_assert_path.is_relative_to(ROOT.resolve()):
                shutil.rmtree(workspace)


if __name__ == "__main__":
    unittest.main()
