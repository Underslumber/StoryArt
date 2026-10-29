"""Unit checks for the CHARACTER_BASE driver: passport, gates, failure log, canvas and review rules."""

from __future__ import annotations

from argparse import Namespace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from tools import character_base as driver
from tools import style_pack_manager as manager


def complete_brief() -> dict:
    brief = driver.brief_template()
    brief["name"] = "Beal"
    for key in driver.BRIEF_REQUIRED_FIELDS:
        brief["fields"][key] = {"value": f"value of {key}", "source": "USER"}
    brief["must_not"] = ["pale muzzle", "small ears"]
    return brief


class CharacterBaseDriverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.request = self.root / "request"
        self.request.mkdir()
        self.guard_path = self.request / "EXECUTION_GUARD.json"
        self.write_guard({})

    def write_guard(self, completed: dict[str, Path]) -> None:
        rows = []
        for stage in driver.STAGES:
            evidence = completed.get(stage)
            row = {"id": stage, "status": "COMPLETED" if evidence else "PENDING", "evidence": []}
            if evidence:
                row["evidence"] = [str(evidence)]
                row["evidence_sha256"] = {str(evidence): hashlib.sha256(evidence.read_bytes()).hexdigest()}
            rows.append(row)
        self.guard_path.write_text(json.dumps({"request_id": "request", "required_stages": rows}), encoding="utf-8")

    def stage_file(self, name: str, color: tuple[int, int, int]) -> Path:
        path = self.root / f"{name}.png"
        Image.new("RGB", (90, 160), color).save(path)
        return path

    def store_brief(self, brief: dict | None = None) -> None:
        source = self.root / "brief.json"
        source.write_text(json.dumps(brief or complete_brief()), encoding="utf-8")
        args = driver.build_parser().parse_args([
            "brief", "--request-dir", str(self.request), "--brief-file", str(source),
            "--confirmation-quote", "Да, всё верно.",
        ])
        args.handler(args)

    def test_passport_requires_every_identity_field(self):
        brief = complete_brief()
        brief["fields"]["eyes"] = {"value": "", "source": "USER"}
        brief["fields"]["ears"] = {"value": "large", "source": "GUESS"}
        with self.assertRaises(driver.CharacterBaseError) as raised:
            driver.validate_brief(brief)
        self.assertIn("eyes is empty", str(raised.exception))
        self.assertIn("ears.source", str(raised.exception))

    def test_passport_revisions_keep_history_and_feed_prompt_and_profile(self):
        self.store_brief()
        changed = complete_brief()
        changed["fields"]["skin_or_fur"] = {"value": "all-black fur", "source": "CANON_IMAGE"}
        self.store_brief(changed)
        stored = driver.load_confirmed_brief(self.request)
        self.assertEqual(stored["revision"], 2)
        self.assertTrue((self.request / "CHARACTER_BRIEF_HISTORY" / "revision_1.json").is_file())
        prompt = driver.passport_prompt(stored)
        self.assertIn("Skin or fur: all-black fur.", prompt)
        self.assertIn("Never: pale muzzle; small ears.", prompt)
        patch = driver.profile_patch_from_brief(stored, [
            {"stage": "02_PHYSIQUE_FRONT", "finding": "Too muscular", "avoid": "Keep a lean build."},
            {"stage": "05_CHARACTER_ASSEMBLY", "finding": "Crooked axes", "avoid": ""},
        ])
        self.assertIn("all-black fur", patch["locked_invariants"]["skin_tone"])
        self.assertIn("Never: pale muzzle", patch["locked_invariants"]["permanent_features"])
        self.assertEqual(patch["known_failure_modes"], ["Keep a lean build.", "Crooked axes"])

    def test_next_walks_brief_two_user_gates_and_automatic_stages(self):
        self.assertEqual(driver.next_action(self.request)["action"], "CONFIRM_BRIEF")
        self.store_brief()
        first = driver.next_action(self.request)
        self.assertEqual((first["action"], first["stage"], first["user_gate_after"]), ("GENERATE_STAGE", "01_FACE_IDENTITY", True))
        face = self.stage_file("face", (200, 150, 120))
        self.write_guard({"01_FACE_IDENTITY": face})
        self.assertEqual(driver.next_action(self.request)["action"], "WAIT_USER_APPROVAL")
        args = driver.build_parser().parse_args([
            "gate", "--request-dir", str(self.request), "--stage", "01_FACE_IDENTITY", "--user-quote", "Это он.",
        ])
        args.handler(args)
        front_step = driver.next_action(self.request)
        self.assertEqual((front_step["stage"], front_step["swimwear_rung"]), ("02_PHYSIQUE_FRONT", "TWO_PIECE"))

        # A redone face invalidates the earlier approval.
        face.write_bytes(b"")
        Image.new("RGB", (90, 160), (10, 10, 10)).save(face)
        self.write_guard({"01_FACE_IDENTITY": face})
        self.assertEqual(driver.next_action(self.request)["action"], "WAIT_USER_APPROVAL")

        with self.assertRaisesRegex(driver.CharacterBaseError, "continue automatically"):
            driver.command_gate(Namespace(request_dir=str(self.request), stage="03_PHYSIQUE_SIDE", user_quote="ok"))

    def test_side_back_and_assembly_follow_front_without_a_gate(self):
        self.store_brief()
        files = {stage: self.stage_file(stage, (index * 30, 100, 100)) for index, stage in enumerate(driver.STAGES[:2])}
        self.write_guard(files)
        for stage in driver.USER_GATES:
            args = driver.build_parser().parse_args([
                "gate", "--request-dir", str(self.request), "--stage", stage, "--user-quote", "Да.",
            ])
            args.handler(args)
        step = driver.next_action(self.request)
        self.assertEqual((step["stage"], step["user_gate_after"]), ("03_PHYSIQUE_SIDE", False))
        files.update({stage: self.stage_file(stage, (90, 90, 90)) for stage in driver.STAGES[2:]})
        self.write_guard(files)
        self.assertEqual(driver.next_action(self.request)["action"], "SHOW_SHEET_AND_APPROVE")

    def test_failures_become_avoid_lines_once(self):
        driver.append_failure(self.request, "01_FACE_IDENTITY", "Face narrowed", "Keep the face wide.")
        driver.append_failure(self.request, "02_PHYSIQUE_FRONT", "Face narrowed again", "Keep the face wide.")
        driver.append_failure(self.request, "02_PHYSIQUE_FRONT", "Bodybuilder chest")
        self.assertEqual(driver.avoid_lines(self.request), ["Keep the face wide.", "Do not repeat: Bodybuilder chest"])

    def test_stage_required_qa_follows_plan_schema(self):
        plan = {
            "semantic_qa_schema": 3,
            "generation_workflow": {"stages": [{"stage_id": "03_PHYSIQUE_SIDE", "required_qa": ["SIDE_VIEW", "ANATOMY_REVIEW"]}]},
        }
        self.assertEqual(driver.stage_required_qa(plan, "03_PHYSIQUE_SIDE"), [
            "ATTACHMENTS", "CANVAS", "STAGE_LAYER", "SIDE_VIEW", "STYLE", "BODY_RENDERING_STYLE", "LIMB_PROPORTIONS",
        ])
        self.assertEqual(driver.stage_required_qa(plan, "01_FACE_IDENTITY"), ["ATTACHMENTS", "CANVAS", "STAGE_LAYER", "STYLE"])

    def test_fit_canvas_measures_and_extends_without_touching_the_original(self):
        tall = self.root / "tall.png"
        image = Image.new("RGB", (450, 800), (200, 196, 190))
        ImageDraw.Draw(image).rectangle((180, 20, 270, 780), fill=(40, 30, 30))
        image.save(tall)
        original = hashlib.sha256(tall.read_bytes()).hexdigest()
        output = self.root / "tall_fit.png"
        args = driver.build_parser().parse_args(["fit-canvas", "--image", str(tall), "--output", str(output)])
        args.handler(args)
        self.assertEqual(hashlib.sha256(tall.read_bytes()).hexdigest(), original)
        provenance = json.loads(output.with_suffix(".provenance.json").read_text(encoding="utf-8"))
        self.assertGreater(provenance["subject_height_percent_before"], 88)
        self.assertTrue(72 <= provenance["subject_height_percent_after"] <= 88)
        with Image.open(output) as extended:
            self.assertAlmostEqual(extended.width / extended.height, 450 / 800, places=2)
        top, bottom, _ = driver.subject_bounds(Image.open(output))
        self.assertGreater(top, 0)

    def test_sheet_is_preview_only_and_lists_each_full_file(self):
        files = {stage: self.stage_file(stage, (index * 40, 80, 120)) for index, stage in enumerate(driver.STAGES)}
        self.write_guard(files)
        args = driver.build_parser().parse_args(["sheet", "--request-dir", str(self.request), "--panel-height", "120"])
        args.handler(args)
        sheet = self.request / "REVIEW" / "CHARACTER_SHEET_PREVIEW.png"
        record = json.loads(sheet.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(record["purpose"], "PREVIEW_ONLY")
        self.assertEqual([row["path"] for row in record["sources"]], [str(files[stage]) for stage in driver.STAGES])


class RejectionFindingsReviewTests(unittest.TestCase):
    prompt = "Draw the front view. Keep the wide face, and keep the lean build."

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "output.png"
        Image.new("RGB", (32, 32), (1, 2, 3)).save(self.output)
        self.review_path = Path(temporary.name) / "review.json"
        self.snapshot = {
            "execution_call": {"prompt": {"text": self.prompt}},
            "prompt_sha256": hashlib.sha256(self.prompt.encode("utf-8")).hexdigest(),
            "reference_plan_snapshot_sha256": "a" * 64,
        }

    def validate(self, constraints: list[dict]) -> dict:
        report = {
            "schema_version": 1, "request_id": "r", "attempt_id": "a", "task_revision": 0,
            "output_sha256": hashlib.sha256(self.output.read_bytes()).hexdigest(),
            "prompt_sha256": self.snapshot["prompt_sha256"], "executed_plan_sha256": "a" * 64,
            "author": "reviewer",
            "anatomy_review": {"status": "PASS", "checked_scope": "Whole figure at full size.", "findings": []},
            "visible_defect_review": {"status": "PASS", "checked_scope": "Whole image at full size.", "findings": []},
            "prompt_adherence": {"status": "FAIL", "checked_scope": "REJECTION_FINDINGS", "constraints": constraints},
        }
        self.review_path.write_text(json.dumps(report), encoding="utf-8")
        return manager.validate_visual_review(
            self.review_path, request_id="r", attempt_id="a", task_revision=0, output=self.output,
            execution_snapshot=self.snapshot, plan={}, stage_id="02_PHYSIQUE_FRONT",
        )

    def test_rejected_output_lists_only_failed_clauses(self):
        clauses = manager.prompt_review_clauses(self.prompt)
        report = self.validate([
            {"constraint": clauses[1], "status": "FAIL", "evidence": "Face narrowed."},
            {"constraint": clauses[2], "status": "FAIL", "evidence": "Chest inflated."},
        ])
        self.assertEqual(report["prompt_adherence"]["status"], "FAIL")

    def test_rejection_findings_must_quote_prompt_clauses_in_order_with_evidence(self):
        clauses = manager.prompt_review_clauses(self.prompt)
        with self.assertRaisesRegex(manager.StylePackError, "at least one"):
            self.validate([])
        with self.assertRaisesRegex(manager.StylePackError, "must quote a clause"):
            self.validate([{"constraint": "invented requirement", "status": "FAIL", "evidence": "x"}])
        with self.assertRaisesRegex(manager.StylePackError, "prompt order"):
            self.validate([
                {"constraint": clauses[2], "status": "FAIL", "evidence": "x"},
                {"constraint": clauses[1], "status": "FAIL", "evidence": "y"},
            ])
        with self.assertRaisesRegex(manager.StylePackError, "FAIL status and evidence"):
            self.validate([{"constraint": clauses[1], "status": "PASS", "evidence": "x"}])


if __name__ == "__main__":
    unittest.main()
