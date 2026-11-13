import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import task_execution_guard as guard
from tools import user_style_reference as user_style
from tests.test_task_execution_guard import _native_default_call


class UserStyleReferenceTests(unittest.TestCase):
    quote = "Use the original attached female drawings as the style."

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "request"
        self.path = self.folder / "EXECUTION_GUARD.json"
        self.env = patch.dict(os.environ, {"CODEX_THREAD_ID": "user-style-test"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.registry = patch.object(guard, "_chat_request_root", return_value=self.root / "registry")
        self.registry.start()
        self.addCleanup(self.registry.stop)
        guard.create_guard(self.path, request_id="request-1", goal="Compare female body shapes",
            deliverable="One comparison sheet", task_kind="IMAGE_GENERATION_NATIVE_DEFAULT",
            invariants=["STYLE=GENERATOR_DEFAULT", "REFERENCES=NONE"], required_stages=["SINGLE_PASS"])
        self.source = self.root / "original.png"
        self.source.write_bytes(b"original-user-attached-style-bytes")
        digest = user_style._hash(self.source)
        self.selection = {
            "request_id": "request-1", "chat_id": "user-style-test", "user_quote": self.quote,
            "target_subject_label": "FEMALE", "target_evidence": "Adult female body study requested.",
            "sources": [{"path": str(self.source), "source_path": str(self.source), "sha256": digest,
                "active_roles": ["STYLE"], "physically_attach": True, "origin": "USER_CHAT_ATTACHMENT",
                "chat_id": "user-style-test", "message_evidence": "Original attachment in this chat",
                "subject_label": "FEMALE", "subject_label_status": "REVIEWED",
                "subject_label_sha256": digest, "subject_label_evidence": "Female figures on the attached sheet",
                "review": {"view": "FULL_RESOLUTION", "outcome": "PASS",
                    "findings": "Blue contour and broad flat shadow groups.",
                    "limitations": "Style only; no clothing or identity transfer."},
                "risk": "D2", "use_impact": "+0D", "risk_reason": "Clothed adult figure-style example."}],
        }
        self.selection_path = self.folder / "selection.json"
        guard.atomic_write_json(self.selection_path, self.selection)
        self.prompt = self.folder / "prompt.txt"
        self.prompt.write_text("Draw an adult female comparison sheet in the attached drawing style.", encoding="utf-8")

    def correct(self):
        return guard.checkpoint(self.path, event="USER_CORRECTION", summary="Explicit style-source correction",
            stage="SINGLE_PASS", correction_impact="CHANGE", user_approved_invariant_change=True,
            invariant_change_evidence=self.quote,
            invariant_changes=["STYLE=USER_STYLE:ORIGINAL_ATTACHMENTS", "REFERENCES=USER_ATTACHMENTS"])

    def prepare(self):
        return user_style.prepare(argparse.Namespace(guard=str(self.path), selection=str(self.selection_path),
            prompt_file=str(self.prompt), user_quote=self.quote,
            plan_name="USER_STYLE_PLAN.json", call_name="USER_STYLE_CALL.json"))

    def load_call(self, files):
        return json.loads(Path(files["execution_call"]).read_text(encoding="utf-8"))

    def test_correction_preserves_history_and_completes_reference_bound_lifecycle(self):
        native = _native_default_call(self.folder, "An adult female reference sheet.")
        guard.checkpoint(self.path, event="READY_FOR_EXECUTION", summary="Native ready", execution_call=native)
        started = guard.checkpoint(self.path, event="EXECUTION_STARTED", summary="Requested sheet", execution_call=native)
        old_id = started["active_attempt"]["attempt_id"]
        old_image = self.folder / "old.png"
        old_image.write_bytes(b"original-provider-output")
        guard.checkpoint(self.path, event="VISIBLE_RESULT", summary="Native result", attempt_id=old_id,
                         result_status="TEST", evidence=[str(old_image)])
        self.correct()
        guard.checkpoint(self.path, event="RECOVERY_REQUIRED", summary="Need supported reference-style transition")
        before = guard.load_guard(self.path)
        history = copy.deepcopy(before["attempts"])
        files = self.prepare()
        selected = guard.load_guard(self.path)
        self.assertEqual(selected["attempts"], history)
        self.assertEqual(selected["task_revision"], before["task_revision"])
        self.assertEqual(selected["task_kind"], user_style.TASK_KIND)
        self.assertNotIn("recovery", selected)
        self.assertTrue(selected["last_recovery"]["resolved_at"])
        self.assertEqual(old_image.read_bytes(), b"original-provider-output")
        call = self.load_call(files)
        self.assertIsNone(json.loads(Path(files["reference_plan"]).read_text())["fidelity"])
        guard.checkpoint(self.path, event="READY_FOR_EXECUTION", summary="STYLE ready",
                         execution_call=call, reference_plan=files["reference_plan"])
        started = guard.checkpoint(self.path, event="EXECUTION_STARTED", summary="Requested corrected sheet",
                                   execution_call=call, reference_plan=files["reference_plan"])
        attempt = started["active_attempt"]
        self.assertTrue(Path(attempt["execution_snapshot"]["reference_plan_snapshot_path"]).is_file())
        output = self.folder / "corrected.png"
        output.write_bytes(b"corrected-provider-output")
        guard.checkpoint(self.path, event="VISIBLE_RESULT", summary="Corrected image", attempt_id=attempt["attempt_id"],
                         evidence=[str(output)], result_status="TEST")
        guard.checkpoint(self.path, event="STAGE_COMPLETED", stage="SINGLE_PASS", summary="Visual review ACCEPT",
                         evidence=[str(output)])
        guard.checkpoint(self.path, event="RESULT_DELIVERED", summary="Inline result delivered",
                         attempt_id=attempt["attempt_id"], delivery_evidence=["Provider inline result"])
        complete = guard.checkpoint(self.path, event="COMPLETE", summary="All requirements verified")
        self.assertEqual(complete["status"], "COMPLETE")

    def test_transition_without_explicit_source_correction_is_rejected_without_mutation(self):
        before = self.path.read_bytes()
        with self.assertRaises(guard.GuardError):
            self.prepare()
        self.assertEqual(self.path.read_bytes(), before)

    def test_next_body_correction_reprepares_same_sources_without_reasking_style(self):
        self.correct()
        files = self.prepare()
        call = self.load_call(files)
        guard.checkpoint(self.path, event="READY_FOR_EXECUTION", summary="Ready", execution_call=call,
                         reference_plan=files["reference_plan"])
        first = guard.checkpoint(self.path, event="EXECUTION_STARTED", summary="Requested sheet",
                                 execution_call=call, reference_plan=files["reference_plan"])
        output = self.folder / "first.png"
        output.write_bytes(b"first-provider-result")
        guard.checkpoint(self.path, event="VISIBLE_RESULT", summary="First result",
            attempt_id=first["active_attempt"]["attempt_id"], evidence=[str(output)], result_status="REJECTED")
        corrected = guard.checkpoint(self.path, event="USER_CORRECTION", summary="Make figure shapes stronger",
            stage="SINGLE_PASS", correction_impact="PRESERVE")
        history = copy.deepcopy(corrected["attempts"])
        self.prompt.write_text("Draw the same adult female sheet with stronger shape contrasts.", encoding="utf-8")
        files2 = user_style.prepare(argparse.Namespace(guard=str(self.path), selection=str(self.selection_path),
            prompt_file=str(self.prompt), user_quote=self.quote,
            plan_name="USER_STYLE_PLAN_2.json", call_name="USER_STYLE_CALL_2.json"))
        self.assertEqual(guard.load_guard(self.path)["attempts"], history)
        call2 = self.load_call(files2)
        guard.checkpoint(self.path, event="READY_FOR_EXECUTION", summary="New revision ready",
            execution_call=call2, reference_plan=files2["reference_plan"])
        started = guard.checkpoint(self.path, event="EXECUTION_STARTED", summary="Requested corrected sheet",
            execution_call=call2, reference_plan=files2["reference_plan"])
        self.assertEqual(started["active_attempt"]["task_revision"], corrected["task_revision"])

    def test_foreign_chat_or_subject_label_is_rejected_without_guard_mutation(self):
        self.correct()
        before = self.path.read_bytes()
        for key, value in (("chat_id", "other-chat"), ("subject_label", "MALE"),
                           ("subject_label_sha256", "0" * 64), ("active_roles", ["BODY"])):
            with self.subTest(key=key):
                selection = copy.deepcopy(self.selection)
                selection["sources"][0][key] = value
                guard.atomic_write_json(self.selection_path, selection)
                args = argparse.Namespace(guard=str(self.path), selection=str(self.selection_path),
                    prompt_file=str(self.prompt), user_quote=self.quote,
                    plan_name=f"PLAN_{key}.json", call_name=f"CALL_{key}.json")
                with self.assertRaises(guard.GuardError):
                    user_style.prepare(args)
                self.assertEqual(self.path.read_bytes(), before)

    def test_source_change_after_ready_blocks_execution(self):
        self.correct()
        files = self.prepare()
        call = self.load_call(files)
        guard.checkpoint(self.path, event="READY_FOR_EXECUTION", summary="Ready", execution_call=call,
                         reference_plan=files["reference_plan"])
        self.source.write_bytes(b"changed-original")
        with self.assertRaises(guard.GuardError):
            guard.checkpoint(self.path, event="EXECUTION_STARTED", summary="Requested sheet", execution_call=call,
                             reference_plan=files["reference_plan"])
        self.assertIsNone(guard.load_guard(self.path)["active_attempt"])

    def test_unknown_attempt_cannot_be_replaced_by_transition(self):
        self.correct()
        state = guard.load_guard(self.path)
        state["attempts"].append({"attempt_id": "unknown", "status": "UNKNOWN"})
        guard.atomic_write_json(self.path, state)
        before = self.path.read_bytes()
        with self.assertRaises(guard.GuardError):
            self.prepare()
        self.assertEqual(self.path.read_bytes(), before)

    def test_implicit_reference_and_named_character_cannot_enter_style_only_lane(self):
        self.correct()
        files = self.prepare()
        call = self.load_call(files)
        with self.assertRaises(guard.GuardError):
            user_style.validate_call(call, "request-1", files["reference_plan"], "CHAR_001")
        for field, value in (("num_last_images_to_include", 1), ("n", 2)):
            amended = {**call, field: value}
            with self.subTest(field=field), self.assertRaises(guard.GuardError):
                user_style.validate_call(amended, "request-1", files["reference_plan"])

    def test_shape_regions_are_bound_as_subject_without_allowing_identity_role(self):
        self.correct()
        source = self.selection["sources"][0]
        source.update(active_roles=["STYLE", "SUBJECT"], subject_scope="SCHEMATIC_SHAPE_GUIDE",
                      subject_regions=["top-left small-frame outline", "top-middle broad-shoulder feminine outline"],
                      subject_evidence="Use the two marked examples for forms 01 and 09.")
        guard.atomic_write_json(self.selection_path, self.selection)
        files = self.prepare()
        call = self.load_call(files)
        self.assertEqual(call["slots"][0]["active_roles"], ["STYLE", "SUBJECT"])
        self.assertEqual(call["risk_assessment"]["references"][0]["active_roles"], ["STYLE", "SUBJECT"])
        self.assertIn("Schematic shape regions:", call["prompt"]["text"])
        plan_path = Path(files["reference_plan"])
        for key, value in (("subject_scope", "CHARACTER_IDENTITY"), ("subject_regions", [])):
            broken = copy.deepcopy(call)
            broken["slots"][0][key] = value
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["sources"] = broken["slots"]
            guard.atomic_write_json(plan_path, plan)
            broken["style_plan_sha256"] = user_style._hash(plan_path)
            with self.subTest(key=key), self.assertRaises(guard.GuardError):
                user_style.validate_call(broken, "request-1", plan_path)


if __name__ == "__main__":
    unittest.main()
