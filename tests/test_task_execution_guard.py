from __future__ import annotations

import tempfile
import unittest
import hashlib
import json
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import task_execution_guard as guard
from tools import generation_risk_assessor as risk
from tools import generation_call_contract as call_contract


BASE_TIME = datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc)
_raw_checkpoint = guard.checkpoint
_fixture_plans: dict[str, Path] = {}


def _checkpoint_fixture(path, **kwargs):
    event = str(kwargs.get("event", "")).upper()
    plan_path = _fixture_plans.get(str(Path(path).resolve()))
    if plan_path and event in {"READY_FOR_EXECUTION", "EXECUTION_STARTED"}:
        current = guard.load_guard(path)
        if event == "EXECUTION_STARTED" and current.get("ready_binding"):
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            execution_call = plan["execution_call"]
        else:
            execution_call = None
        if execution_call is None:
            stage = kwargs.get("stage") or "SINGLE_PASS"
            prompt = "Fixture executable prompt"
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            source_path = plan_path.parent / "reference.png"
            source_path.write_bytes(b"fixture-reference")
            execution_call = {
                "request_id": "request-1",
                "stage_id": stage,
                "prompt": {"text": prompt, "text_sha256": prompt_hash},
                "user_selections": {
                    "style": {"choice": "PROJECT_STYLE:fixture style", "user_quote": "Use this fixture style."},
                    "reference_policy": {"choice": "USER_ATTACHED_REFERENCES", "user_quote": "Use these fixture references."},
                    "character": {"choice": "NONE", "user_quote": "No named project character in this fixture."},
                },
                "slots": [{
                    "path": str(source_path.resolve()),
                    "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                    "active_roles": ["STYLE"],
                }],
                "stage_output_bindings": [],
                "targeted_pack_bindings": [],
            }
            lexicon_path = risk.DEFAULT_LEXICON
            lexicon = risk.load_lexicon(lexicon_path)
            assessed_prompt = risk.evaluate_prompt(prompt, lexicon)
            reference_row = {
                "path": str(source_path.resolve()),
                "sha256": execution_call["slots"][0]["sha256"],
                "active_roles": ["STYLE"],
                "content_and_reference_risk": "D1",
                "use_impact": "0D",
                "effective_risk": "D1",
                "reason_ru": "Fixture role and use are bound for this test.",
            }
            binding = risk._reference_bindings([reference_row])
            combined, modifiers = risk.combined_score(int(assessed_prompt["score"]), [reference_row])
            report = {
                "input_binding": {
                    "lexicon_sha256": hashlib.sha256(lexicon_path.read_bytes()).hexdigest(),
                    "prompt_sha256": prompt_hash,
                    "references": binding,
                },
                "references": [reference_row], "original_prompt": assessed_prompt,
                "revised_prompt": None, "original_combined_risk": risk.d_label(combined),
                "generation_risk": risk.d_label(combined), "combined_modifiers": modifiers,
            }
            execution_call["risk_assessment"] = report
            plan = {
                "request_id": "request-1", "gate_status": "READY_FOR_GENERATION",
                "style_name": "fixture style", "generation_purpose": "SCENE",
                "risk_assessment": {"prompt": {"text": prompt, "text_sha256": prompt_hash}, **report},
                "generation_workflow": {"mode": "SINGLE_PASS", "slots": execution_call["slots"]},
                "execution_call": execution_call,
            }
            plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
        kwargs.setdefault("reference_plan", plan_path)
        kwargs.setdefault("execution_call", execution_call)
        if event == "EXECUTION_STARTED" and not current.get("ready_binding") and current.get("next_required_action") != "ESCALATION_ORCHESTRATOR_REQUIRED":
            _raw_checkpoint(path, event="READY_FOR_EXECUTION", summary="Executable fixture plan is ready.",
                            reference_plan=plan_path, execution_call=execution_call,
                            stage=kwargs.get("stage"), now=kwargs.get("now", BASE_TIME))
    if event in {"VISIBLE_RESULT", "ATTEMPT_REJECTED", "ATTEMPT_REFUSED", "ATTEMPT_UNKNOWN", "RESULT_DELIVERED"}:
        state = guard.load_guard(path)
        if not kwargs.get("attempt_id"):
            active = state.get("active_attempt")
            if active:
                kwargs["attempt_id"] = active["attempt_id"]
            elif state.get("attempts"):
                kwargs["attempt_id"] = state["attempts"][-1]["attempt_id"]
        if event == "VISIBLE_RESULT":
            kwargs.setdefault("result_status", "TEST")
        if event == "ATTEMPT_UNKNOWN":
            kwargs.setdefault("reconciliation_evidence", "fixture timeout receipt")
    return _raw_checkpoint(path, **kwargs)


def _native_default_call(folder: Path, prompt: str) -> dict[str, object]:
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    report_path = folder / f"risk-{prompt_hash[:12]}.json"
    risk.command_assess(type("Args", (), {
        "lexicon": risk.DEFAULT_LEXICON,
        "text": prompt,
        "prompt_file": "",
        "revised_text": "",
        "revised_prompt_file": "",
        "reference": [],
        "output": str(report_path),
        "overwrite": False,
        "json": False,
    })())
    return {
        "request_id": "request-1",
        "prompt": {"text": prompt, "text_sha256": prompt_hash},
        "user_selections": {
            "style": {"choice": "GENERATOR_DEFAULT", "user_quote": "I choose the generator default style."},
            "reference_policy": {"choice": "NO_REFERENCES", "user_quote": "I choose no references."},
            "character": {"choice": "NONE", "user_quote": "There is no project character in this request."},
        },
        "risk_assessment": json.loads(report_path.read_text(encoding="utf-8")),
    }


class TaskExecutionGuardTests(unittest.TestCase):
    def test_multistage_character_identity_anchor_must_be_in_lineage(self):
        assembly = Path("approved/CHAR_001/assembly.png").resolve()
        workflow = {"stages": [
            {"stage_id": "02_BODY_POSE", "slots": [{
                "path": str(assembly), "sha256": "a" * 64,
                "active_roles": ["CHARACTER_ASSEMBLY"],
            }]},
            {"stage_id": "04_CHARACTER_COMPOSITE", "slots": [{
                "path": "<STAGE_OUTPUT:02_BODY_POSE>", "sha256": "STAGE_OUTPUT:02_BODY_POSE",
                "active_roles": ["BODY"],
            }]},
            {"stage_id": "05_FINAL_SCENE", "slots": [{
                "path": "<STAGE_OUTPUT:04_CHARACTER_COMPOSITE>", "sha256": "STAGE_OUTPUT:04_CHARACTER_COMPOSITE",
                "active_roles": ["CHARACTER_COMPOSITE"],
            }]},
        ]}
        self.assertTrue(guard._stage_has_identity_anchor(workflow, "05_FINAL_SCENE", assembly, "a" * 64))
        self.assertFalse(guard._stage_has_identity_anchor(workflow, "05_FINAL_SCENE", assembly, "b" * 64))

    def test_project_style_only_allows_approved_face_body_identity_by_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "CHAR_001"
            root.mkdir()
            style = Path(folder) / "style.png"
            outside_body = Path(folder) / "unapproved-body.png"
            slots = [
                {"path": str(style), "active_roles": ["STYLE"]},
                {"path": str(root / "assembly.png"), "active_roles": ["CHARACTER_ASSEMBLY"]},
                {"path": str(root / "face.png"), "active_roles": ["FACE"]},
                {"path": str(root / "body.png"), "active_roles": ["BODY"]},
            ]
            guard.validate_project_style_only_slots(slots, "PROJECT_STYLE:TEST", "CHAR_001", root)
            slots[-1] = {"path": str(outside_body), "active_roles": ["BODY"]}
            with self.assertRaisesRegex(guard.GuardError, "optional non-style"):
                guard.validate_project_style_only_slots(slots, "PROJECT_STYLE:TEST", "CHAR_001", root)

    def test_project_style_only_accepts_only_explicitly_validated_stage_lineage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            style = root / "style.png"
            derived = root / "qa-passed-stage.png"
            arbitrary = root / "unverified.png"
            slots = [
                {"path": str(style), "active_roles": ["STYLE"]},
                {"path": str(derived), "active_roles": ["FACE"]},
            ]
            guard.validate_project_style_only_slots(
                slots, "PROJECT_STYLE:TEST", "NONE", None, {derived.resolve()},
            )
            slots[-1] = {"path": str(arbitrary), "active_roles": ["FACE"]}
            with self.assertRaisesRegex(guard.GuardError, "optional non-style"):
                guard.validate_project_style_only_slots(
                    slots, "PROJECT_STYLE:TEST", "NONE", None, {derived.resolve()},
                )

    def test_project_style_only_is_explicit_and_keeps_named_identity_allowed(self):
        base = {
            "user_selections": {
                "style": {"choice": "PROJECT_STYLE:TEST", "user_quote": "Use project style."},
                "reference_policy": {"choice": "PROJECT_STYLE_ONLY", "user_quote": "No optional references."},
                "character": {"choice": "NONE", "user_quote": "No named character."},
            }
        }
        self.assertEqual(
            guard.validate_user_generation_selections(base, no_references=False)["reference_policy"],
            "PROJECT_STYLE_ONLY",
        )
        base["user_selections"]["character"]["choice"] = "CHAR_001"
        self.assertEqual(
            guard.validate_user_generation_selections(base, no_references=False)["character"],
            "CHAR_001",
        )
        base["user_selections"]["character"]["choice"] = "NONE"
        base["user_selections"]["reference_policy"]["choice"] = "BODY_LIBRARY_ONLY"
        self.assertEqual(
            guard.validate_user_generation_selections(base, no_references=False)["reference_policy"],
            "BODY_LIBRARY_ONLY",
        )

    def test_project_style_only_accepts_selected_textual_style_without_optional_references(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            self.checkpoint_fixture(path, event="READY_FOR_EXECUTION", summary="Bind the selected project style.", now=BASE_TIME)
            plan_path = _fixture_plans[str(path.resolve())]
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            call = plan["execution_call"]
            call["user_selections"]["reference_policy"]["choice"] = "PROJECT_STYLE_ONLY"
            plan["execution_call"] = call
            plan["user_selections"] = call["user_selections"]
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            binding = guard.validate_reference_plan(plan_path, execution_call=call)
            self.assertEqual(binding["execution_call_sha256"], hashlib.sha256(
                json.dumps(call, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest())

    def test_reference_plan_readiness_cannot_use_plan_only_without_materialized_call(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            self.checkpoint_fixture(path, event="READY_FOR_EXECUTION", summary="Build a fixture call.", now=BASE_TIME)
            plan_path = _fixture_plans[str(path.resolve())]
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan.pop("execution_call")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaisesRegex(guard.GuardError, "materialized exact execution_call"):
                guard.validate_reference_plan(plan_path)

    def test_execution_start_rejects_a_changed_call_after_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            self.checkpoint_fixture(path, event="READY_FOR_EXECUTION", summary="Bind exact fixture call.", now=BASE_TIME)
            plan_path = _fixture_plans[str(path.resolve())]
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            changed_call = json.loads(json.dumps(plan["execution_call"]))
            changed_call["user_selections"]["style"]["user_quote"] = "Use another project style."
            with self.assertRaisesRegex(guard.GuardError, "exactly match the call saved"):
                _raw_checkpoint(
                    path, event="EXECUTION_STARTED", reference_plan=plan_path,
                    execution_call=changed_call, summary="Reject changed selection after READY.",
                    output_contract="REQUESTED_DELIVERABLE", now=BASE_TIME,
                )

    def test_locked_character_registry_matching_uses_exact_name_or_explicit_alias(self):
        row = {
            "character_id": "CHAR_001",
            "name": "Anna",
            "notes": "Character Anna (Annabelle) appears in a scene.",
        }
        self.assertTrue(guard.registry_identity_matches_locked_name("CHAR_001", row, "CHAR_001"))
        self.assertTrue(guard.registry_identity_matches_locked_name("Anna", row, "CHAR_001"))
        self.assertTrue(guard.registry_identity_matches_locked_name("Annabelle", row, "CHAR_001"))
        self.assertFalse(guard.registry_identity_matches_locked_name("Ann", row, "CHAR_001"))

    def make_guard(self, folder: str, **updates):
        values = {
            "request_id": "request-1",
            "goal": "Create one character image.",
            "deliverable": "A visible generated PNG.",
            "task_kind": "IMAGE_GENERATION",
            "allowed_scope": ["STYLE_GENERATIONS/00_PENDING/request-1", "GENERATION_RESULTS"],
            "required_stages": [],
            "max_minutes_without_execution": 20,
            "max_preflight_actions": 3,
            "max_execution_minutes": 20,
            "now": BASE_TIME,
        }
        values.update(updates)
        path = Path(folder) / "EXECUTION_GUARD.json"
        result = guard.create_guard(path, **values)
        if values["task_kind"] == "IMAGE_GENERATION":
            plan_path = Path(folder) / "REFERENCE_PLAN.json"
            _fixture_plans[str(path.resolve())] = plan_path
        return path, result

    def checkpoint_fixture(self, path: Path, **kwargs):
        return _checkpoint_fixture(path, **kwargs)

    def strict_start(self, path: Path, *, stage: str | None = None):
        self.checkpoint_fixture(path, event="READY_FOR_EXECUTION", stage=stage,
                                summary="Exact fixture call is ready.", now=BASE_TIME)
        plan_path = _fixture_plans[str(path.resolve())]
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        return _raw_checkpoint(
            path, event="EXECUTION_STARTED", stage=stage,
            reference_plan=plan_path, execution_call=plan["execution_call"],
            summary="Start the exact ready fixture call.", now=BASE_TIME,
        )

    def test_image_edit_direct_chat_lifecycle_needs_no_plan_or_local_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, task_kind="IMAGE_EDIT")
            started = _raw_checkpoint(
                path,
                event="EXECUTION_STARTED",
                summary="Edit the user-attached source with the user-attached mask.",
                now=BASE_TIME,
            )
            attempt_id = started["active_attempt"]["attempt_id"]
            available = _raw_checkpoint(
                path,
                event="VISIBLE_RESULT",
                attempt_id=attempt_id,
                summary="Edited image delivered directly in chat.",
                now=BASE_TIME,
            )
            self.assertEqual(available["available_results"][-1]["status"], "AVAILABLE")
            done = _raw_checkpoint(
                path,
                event="COMPLETE",
                summary="Requested in-place image edit completed.",
                now=BASE_TIME,
            )
            self.assertEqual(done["status"], "COMPLETE")

    def test_cli_accepts_image_edit_while_generation_remains_default(self):
        args = guard.make_parser().parse_args([
            "start", "--state", "guard.json", "--request-id", "edit-1",
            "--goal", "Edit supplied image.", "--deliverable", "Edited image",
            "--task-kind", "IMAGE_EDIT",
        ])
        self.assertEqual(args.task_kind, "IMAGE_EDIT")
        default_args = guard.make_parser().parse_args([
            "start", "--state", "guard.json", "--request-id", "gen-1",
            "--goal", "Generate image.", "--deliverable", "Generated image",
        ])
        self.assertEqual(default_args.task_kind, "IMAGE_GENERATION")

    def test_standalone_image_guard_kind_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(guard.GuardError, "Standalone image requests outside StoryArt"):
                self.make_guard(folder, task_kind="USER_REQUESTED_IMAGE")
        with self.assertRaises(SystemExit):
            guard.make_parser().parse_args([
                "start", "--state", "guard.json", "--request-id", "direct-1",
                "--goal", "Create a standalone image.", "--deliverable", "Generated image",
                "--task-kind", "USER_REQUESTED_IMAGE",
            ])
        default_args = guard.make_parser().parse_args([
            "start", "--state", "guard.json", "--request-id", "gen-2",
            "--goal", "Generate image.", "--deliverable", "Generated image",
        ])
        self.assertEqual(default_args.task_kind, "IMAGE_GENERATION")

    def test_native_default_generation_completes_single_output_lifecycle_without_plan(self):
        prompt = "A small watercolor fox beneath a pine tree."
        with tempfile.TemporaryDirectory() as folder:
            execution_call = _native_default_call(Path(folder), prompt)
            path, _ = self.make_guard(folder, task_kind="IMAGE_GENERATION_NATIVE_DEFAULT")
            ready = _raw_checkpoint(
                path,
                event="READY_FOR_EXECUTION",
                summary="Bind the exact native/default prompt for one requested image.",
                execution_call=execution_call,
                now=BASE_TIME,
            )
            self.assertEqual(ready["status"], "READY")
            self.assertEqual(ready["ready_binding"]["prompt_sha256"], execution_call["prompt"]["text_sha256"])
            started = _raw_checkpoint(
                path,
                event="EXECUTION_STARTED",
                summary="Generate the requested single image with the native/default style.",
                execution_call=execution_call,
                output_contract="REQUESTED_DELIVERABLE",
                now=BASE_TIME,
            )
            attempt_id = started["active_attempt"]["attempt_id"]
            image_path = Path(folder) / "requested.png"
            image_path.write_bytes(b"visible image evidence")
            visible = _raw_checkpoint(
                path,
                event="VISIBLE_RESULT",
                summary="The requested image is available.",
                evidence=[str(image_path)],
                attempt_id=attempt_id,
                result_status="TEST",
                now=BASE_TIME,
            )
            self.assertEqual(visible["available_results"][-1]["status"], "TEST")
            delivered = _raw_checkpoint(
                path,
                event="RESULT_DELIVERED",
                summary="The requested image was delivered.",
                attempt_id=attempt_id,
                delivery_evidence=["Image returned in the user-visible response."],
                now=BASE_TIME,
            )
            self.assertEqual(delivered["latest_delivered_attempt_id"], attempt_id)
            complete = _raw_checkpoint(
                path, event="COMPLETE", summary="The requested image task is complete.", now=BASE_TIME
            )
            self.assertEqual(complete["status"], "COMPLETE")

    def test_native_default_generation_rejects_changed_prompt_after_readiness(self):
        with tempfile.TemporaryDirectory() as folder:
            call_a = _native_default_call(Path(folder), "A blue fox in a forest.")
            call_b = _native_default_call(Path(folder), "A red fox in a forest.")
            path, _ = self.make_guard(folder, task_kind="IMAGE_GENERATION_NATIVE_DEFAULT")
            _raw_checkpoint(
                path,
                event="READY_FOR_EXECUTION",
                summary="Bind the exact native/default prompt.",
                execution_call=call_a,
                now=BASE_TIME,
            )
            with self.assertRaisesRegex(guard.GuardError, "differs from the prompt contract bound at readiness"):
                _raw_checkpoint(
                    path,
                    event="EXECUTION_STARTED",
                    summary="Start with a changed prompt.",
                    execution_call=call_b,
                    output_contract="REQUESTED_DELIVERABLE",
                    now=BASE_TIME,
                )

    def test_native_default_generation_requires_prompt_bound_empty_reference_risk_report(self):
        prompt = "A paper boat on a calm lake."
        with tempfile.TemporaryDirectory() as folder:
            execution_call = _native_default_call(Path(folder), prompt)
            path, _ = self.make_guard(folder, task_kind="IMAGE_GENERATION_NATIVE_DEFAULT")
            missing_report = {key: value for key, value in execution_call.items() if key != "risk_assessment"}
            with self.assertRaisesRegex(guard.GuardError, "requires an embedded exact-call risk_assessment"):
                _raw_checkpoint(
                    path,
                    event="READY_FOR_EXECUTION",
                    summary="Require the risk report bound to the prompt.",
                    execution_call=missing_report,
                    now=BASE_TIME,
                )
            invalid_report = json.loads(json.dumps(execution_call))
            invalid_report["risk_assessment"]["input_binding"]["prompt_sha256"] = "0" * 64
            with self.assertRaisesRegex(guard.GuardError, "Risk assessment prompt hash does not match"):
                _raw_checkpoint(
                    path,
                    event="READY_FOR_EXECUTION",
                    summary="Reject a risk report bound to a different prompt.",
                    execution_call=invalid_report,
                    now=BASE_TIME,
                )

    def test_native_default_generation_requires_explicit_user_selections(self):
        prompt = "A paper boat on a calm lake."
        with tempfile.TemporaryDirectory() as folder:
            execution_call = _native_default_call(Path(folder), prompt)
            path, _ = self.make_guard(folder, task_kind="IMAGE_GENERATION_NATIVE_DEFAULT")
            missing = dict(execution_call)
            missing.pop("user_selections")
            with self.assertRaisesRegex(guard.GuardError, "requires explicit user_selections"):
                _raw_checkpoint(path, event="READY_FOR_EXECUTION", summary="Missing user choices.",
                                execution_call=missing, now=BASE_TIME)
            named_character = json.loads(json.dumps(execution_call))
            named_character["user_selections"]["character"] = {
                "choice": "CHAR_001", "user_quote": "Draw Chance."
            }
            with self.assertRaisesRegex(guard.GuardError, "named project character"):
                _raw_checkpoint(path, event="READY_FOR_EXECUTION", summary="No identity references.",
                                execution_call=named_character, now=BASE_TIME)

    def test_custom_user_style_must_be_present_in_the_exact_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            self.checkpoint_fixture(path, event="READY_FOR_EXECUTION", summary="Build a valid fixture call.", now=BASE_TIME)
            plan_path = _fixture_plans[str(path.resolve())]
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            call = plan["execution_call"]
            call["user_selections"]["style"] = {
                "choice": "USER_STYLE:oil pastel", "user_quote": "Use an oil pastel style."
            }
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaisesRegex(guard.GuardError, "does not contain the user's selected custom style"):
                guard.validate_reference_plan(plan_path, execution_call=call)

    def test_native_default_generation_rejects_locked_named_character_even_if_call_says_none(self):
        with tempfile.TemporaryDirectory() as folder:
            execution_call = _native_default_call(Path(folder), "A fantasy scene with Chance.")
            path, _ = self.make_guard(
                folder,
                task_kind="IMAGE_GENERATION_NATIVE_DEFAULT",
                invariants=["character=Chance"],
            )
            with self.assertRaisesRegex(guard.GuardError, "named project character cannot use"):
                _raw_checkpoint(path, event="READY_FOR_EXECUTION", summary="Attempt default route.",
                                execution_call=execution_call, now=BASE_TIME)

    def test_native_default_generation_rejects_plan_or_reference_slots(self):
        prompt = "A single image."
        with tempfile.TemporaryDirectory() as folder:
            base_call = _native_default_call(Path(folder), prompt)
            path, _ = self.make_guard(folder, task_kind="IMAGE_GENERATION_NATIVE_DEFAULT")
            with self.assertRaisesRegex(guard.GuardError, "does not accept plan or reference slots"):
                _raw_checkpoint(
                    path,
                    event="READY_FOR_EXECUTION",
                    summary="Do not allow an implicit plan or reference slot.",
                    execution_call={**base_call, "slots": []},
                    now=BASE_TIME,
                )
            with self.assertRaisesRegex(guard.GuardError, "supports exactly one image output"):
                _raw_checkpoint(
                    path,
                    event="READY_FOR_EXECUTION",
                    summary="Do not allow a multi-image call in the native/default lane.",
                    execution_call={**base_call, "num_outputs": 2},
                    now=BASE_TIME,
                )

    def test_execution_start_rechecks_unattached_prior_stage_authority(self):
        from tools.style_pack_manager import make_paths
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = make_paths(workspace, "STYLE")
            request_id = "request-1"
            pending = paths.generations / "00_PENDING" / request_id
            pending.mkdir(parents=True)
            plan_path = pending / "REFERENCE_PLAN.json"
            reference = workspace / "current.png"
            reference.write_bytes(b"reference")
            call = {
                "request_id": request_id, "stage_id": "02_CLOTHING",
                "slots": [{"path": str(reference), "sha256": guard.file_sha256(reference), "active_roles": ["STYLE"]}],
                "stage_output_bindings": [],
            }
            plan = {
                "style_name": "STYLE", "request_id": request_id, "character_id": "CHAR_001",
                "generation_workflow": {"mode": "MULTI_STAGE", "stages": [
                    {"stage_id": "01_FACE_IDENTITY", "slots": []},
                    {"stage_id": "02_CLOTHING", "slots": call["slots"]},
                ]},
                "execution_call": call,
            }
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            with paths.generation_manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["request_id", "status", "notes"])
                writer.writeheader()
                writer.writerow({"request_id": request_id, "status": "REJECTED", "notes": "[STAGE_ID=01_FACE_IDENTITY]"})
            guard_path = pending / "EXECUTION_GUARD.json"
            guard.create_guard(guard_path, request_id=request_id, goal="Create a character.",
                               deliverable="One image.", task_kind="IMAGE_GENERATION", now=BASE_TIME)
            state = guard.load_guard(guard_path)
            state["next_required_action"] = "EXECUTION_STARTED_OR_BLOCKER"
            state["ready_binding"] = {"stage": "02_CLOTHING"}
            guard.atomic_write_json(guard_path, state)
            with patch("tools.task_execution_guard.validate_reference_plan", return_value={"stage": "02_CLOTHING"}):
                with self.assertRaisesRegex(guard.GuardError, "required prior stage output: 01_FACE_IDENTITY"):
                    _raw_checkpoint(
                        guard_path, event="EXECUTION_STARTED", stage="02_CLOTHING",
                        reference_plan=plan_path, execution_call=call,
                        summary="Start after the prior stage was rejected.", now=BASE_TIME,
                    )

    def test_numbered_physique_stage_ids_enforce_swimwear_start_and_completion_gates(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["02_PHYSIQUE_FRONT"])
            with self.assertRaisesRegex(guard.GuardError, "swimwear-rung"):
                self.strict_start(path, stage="02_PHYSIQUE_FRONT")
            state = guard.load_guard(path)
            self.assertEqual(state["next_required_action"], "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER")
            plan_path = _fixture_plans[str(path.resolve())]
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            started = _raw_checkpoint(
                path, event="EXECUTION_STARTED", stage="02_PHYSIQUE_FRONT", swimwear_rung="EXTREME_MICRO",
                reference_plan=plan_path, execution_call=plan["execution_call"],
                summary="Start canonical numbered physique stage.", now=BASE_TIME,
            )
            self.assertEqual(started["active_swimwear_attempt"]["stage"], "02_PHYSIQUE_FRONT")
            image = Path(folder) / "front.png"
            image.write_bytes(b"png-placeholder")
            attempt_id = started["active_attempt"]["attempt_id"]
            _raw_checkpoint(
                path, event="VISIBLE_RESULT", attempt_id=attempt_id, result_status="STAGING",
                evidence=[str(image)], summary="A stage result is ready for topology QA.", now=BASE_TIME,
            )
            with self.assertRaisesRegex(guard.GuardError, "observed-topology"):
                _raw_checkpoint(
                    path, event="STAGE_COMPLETED", stage="02_PHYSIQUE_FRONT", swimwear_rung="EXTREME_MICRO",
                    evidence=[str(image)], summary="Completion cannot skip the clothing topology check.", now=BASE_TIME,
                )

    def test_direct_start_duplicate_start_and_changed_reference_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            with self.assertRaisesRegex(guard.GuardError, "preceding READY"):
                _raw_checkpoint(path, event="EXECUTION_STARTED", summary="Start without readiness.", now=BASE_TIME)
            self.checkpoint_fixture(path, event="READY_FOR_EXECUTION", summary="Bind a ready call.", now=BASE_TIME)
            plan_path = _fixture_plans[str(path.resolve())]
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            started = _raw_checkpoint(path, event="EXECUTION_STARTED", reference_plan=plan_path,
                                      execution_call=plan["execution_call"], summary="Start once.", now=BASE_TIME)
            with self.assertRaisesRegex(guard.GuardError, "duplicate in-flight"):
                _raw_checkpoint(path, event="EXECUTION_STARTED", reference_plan=plan_path,
                                execution_call=plan["execution_call"], summary="Duplicate call.", now=BASE_TIME)
            self.assertEqual(len(guard.load_guard(path)["attempts"]), 1)

    def test_ready_binding_rejects_changed_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            self.checkpoint_fixture(path, event="READY_FOR_EXECUTION", summary="Bind a ready call.", now=BASE_TIME)
            plan_path = _fixture_plans[str(path.resolve())]
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            Path(plan["execution_call"]["slots"][0]["path"]).write_bytes(b"changed after readiness")
            with self.assertRaisesRegex(guard.GuardError, "hash changed"):
                _raw_checkpoint(path, event="EXECUTION_STARTED", reference_plan=plan_path,
                                execution_call=plan["execution_call"], summary="Start changed call.", now=BASE_TIME)

    def test_availability_qa_delivery_and_completion_are_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            started = self.strict_start(path)
            attempt_id = started["active_attempt"]["attempt_id"]
            self.assertEqual(started["active_attempt"]["stage"], "SINGLE_PASS")
            image = Path(folder) / "result.png"
            image.write_bytes(b"png-placeholder")
            reloaded = guard.load_guard(path)
            self.assertEqual(reloaded["active_attempt"]["attempt_id"], attempt_id)
            available = _raw_checkpoint(path, event="VISIBLE_RESULT", attempt_id=attempt_id,
                                        result_status="TEST", evidence=[str(image)],
                                        summary="Registered QA-passed result is available.", now=BASE_TIME)
            self.assertEqual(available["attempts"][-1]["status"], "RESULT_AVAILABLE")
            with self.assertRaisesRegex(guard.GuardError, "delivered TEST"):
                _raw_checkpoint(path, event="COMPLETE", summary="Complete before delivery.", now=BASE_TIME)
            delivered = _raw_checkpoint(path, event="RESULT_DELIVERED", attempt_id=attempt_id,
                                        delivery_evidence=["Codex panel displayed result"],
                                        summary="Delivered in the user interface.", now=BASE_TIME)
            self.assertEqual(delivered["attempts"][-1]["status"], "DELIVERED")
            done = _raw_checkpoint(path, event="COMPLETE", summary="All requested work completed.", now=BASE_TIME)
            self.assertEqual(done["status"], "COMPLETE")

    def test_rejected_result_cannot_be_delivered_or_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            started = self.strict_start(path)
            attempt_id = started["active_attempt"]["attempt_id"]
            image = Path(folder) / "rejected.png"
            image.write_bytes(b"png-placeholder")
            _raw_checkpoint(path, event="VISIBLE_RESULT", attempt_id=attempt_id, result_status="REJECTED",
                            evidence=[str(image)], summary="QA rejected output remains available.", now=BASE_TIME)
            with self.assertRaisesRegex(guard.GuardError, "TEST result"):
                _raw_checkpoint(path, event="RESULT_DELIVERED", attempt_id=attempt_id,
                                delivery_evidence=["link"], summary="Try to deliver rejected output.", now=BASE_TIME)

    def test_user_correction_invalidates_prior_delivery_for_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            started = self.strict_start(path)
            attempt_id = started["active_attempt"]["attempt_id"]
            image = Path(folder) / "result.png"
            image.write_bytes(b"png-placeholder")
            _raw_checkpoint(path, event="VISIBLE_RESULT", attempt_id=attempt_id, result_status="TEST",
                            evidence=[str(image)], summary="QA accepted result.", now=BASE_TIME)
            _raw_checkpoint(path, event="RESULT_DELIVERED", attempt_id=attempt_id,
                            delivery_evidence=["Codex panel receipt"], summary="Delivered result.", now=BASE_TIME)
            corrected = _raw_checkpoint(path, event="USER_CORRECTION", summary="User requests a correction.", now=BASE_TIME)
            self.assertEqual(corrected["task_revision"], 1)
            with self.assertRaisesRegex(guard.GuardError, "task revision"):
                _raw_checkpoint(path, event="COMPLETE", summary="Stale prior output cannot finish correction.", now=BASE_TIME)

    def test_stop_requires_reconciliation_then_explicit_resume_and_late_result_stays_paused(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            started = self.strict_start(path)
            attempt_id = started["active_attempt"]["attempt_id"]
            stopped = _raw_checkpoint(path, event="STOP", attempt_id=attempt_id,
                                      summary="Operator stopped waiting on the provider.", now=BASE_TIME)
            self.assertEqual(stopped["status"], "STOPPED")
            with self.assertRaisesRegex(guard.GuardActionRequired, "UNKNOWN"):
                _raw_checkpoint(path, event="USER_RESUMED", summary="Resume too early.", now=BASE_TIME)
            image = Path(folder) / "late.png"
            image.write_bytes(b"png-placeholder")
            late = _raw_checkpoint(path, event="VISIBLE_RESULT", attempt_id=attempt_id,
                                   result_status="STAGING", evidence=[str(image)],
                                   summary="A late provider result is recorded while paused.", now=BASE_TIME)
            self.assertEqual(late["attempts"][-1]["status"], "UNKNOWN")
            reconciled = _raw_checkpoint(path, event="ATTEMPT_RECONCILED", attempt_id=attempt_id,
                                         outcome="AVAILABLE", result_status="STAGING", evidence=[str(image)],
                                         reconciliation_evidence="Provider receipt says operation completed.",
                                         summary="Reconcile late output without resuming.", now=BASE_TIME)
            self.assertEqual(reconciled["status"], "STOPPED")
            with self.assertRaisesRegex(guard.GuardError, "Cannot start execution"):
                _raw_checkpoint(path, event="EXECUTION_STARTED", summary="No implicit retry.", now=BASE_TIME)
            resumed = _raw_checkpoint(path, event="USER_RESUMED", summary="Explicitly resume after reconciliation.", now=BASE_TIME)
            self.assertEqual(resumed["status"], "ACTIVE")

    def test_refusal_is_distinct_from_qa_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            started = self.strict_start(path)
            attempt_id = started["active_attempt"]["attempt_id"]
            refused = _raw_checkpoint(path, event="ATTEMPT_REFUSED", attempt_id=attempt_id,
                                      reconciliation_evidence="Provider refused the submitted operation.",
                                      summary="Provider refusal, no result returned.", now=BASE_TIME)
            self.assertEqual(refused["attempts"][-1]["status"], "REFUSED")
            self.assertFalse(refused["available_results"])

    def test_cancel_requires_matching_outstanding_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            started = self.strict_start(path)
            attempt_id = started["active_attempt"]["attempt_id"]
            with self.assertRaisesRegex(guard.GuardError, "exact --attempt-id"):
                _raw_checkpoint(path, event="CANCEL", reconciliation_evidence="Operator cancelled.",
                                summary="Cancel the operation.", now=BASE_TIME)
            cancelled = _raw_checkpoint(path, event="CANCEL", attempt_id=attempt_id,
                                        reconciliation_evidence="Operator cancellation receipt.",
                                        summary="Cancel the exact active operation.", now=BASE_TIME)
            self.assertEqual(cancelled["attempts"][-1]["status"], "CANCELLED")
            _raw_checkpoint(path, event="USER_RESUMED", summary="Explicitly resume after cancellation.", now=BASE_TIME)
            with self.assertRaisesRegex(guard.GuardError, "preceding READY"):
                _raw_checkpoint(path, event="EXECUTION_STARTED", summary="No duplicate operation.", now=BASE_TIME)

    def test_older_delivered_result_cannot_complete_after_newer_failed_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            first = self.strict_start(path)
            first_id = first["active_attempt"]["attempt_id"]
            image = Path(folder) / "first.png"
            image.write_bytes(b"first-result")
            _raw_checkpoint(path, event="VISIBLE_RESULT", attempt_id=first_id, result_status="TEST",
                            evidence=[str(image)], summary="First TEST result is available.", now=BASE_TIME)
            _raw_checkpoint(path, event="RESULT_DELIVERED", attempt_id=first_id,
                            delivery_evidence=["Displayed in task panel"], summary="Delivered first TEST.", now=BASE_TIME)
            second = self.strict_start(path)
            second_id = second["active_attempt"]["attempt_id"]
            _raw_checkpoint(path, event="ATTEMPT_REFUSED", attempt_id=second_id,
                            reconciliation_evidence="Provider refused the second operation.",
                            summary="Second attempt was refused.", now=BASE_TIME)
            with self.assertRaisesRegex(guard.GuardError, "latest attempt"):
                _raw_checkpoint(path, event="COMPLETE", summary="Old delivery cannot complete this request.", now=BASE_TIME)

    def test_stopped_stage_cannot_complete_or_reopen_until_explicit_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, task_kind="GENERAL", required_stages=["FRONT"])
            image = Path(folder) / "front.png"
            image.write_bytes(b"stage")
            _raw_checkpoint(path, event="STOP", summary="Stop during preparation.", now=BASE_TIME)
            for event in ("STAGE_COMPLETED", "STAGE_REOPENED"):
                with self.assertRaisesRegex(guard.GuardError, "explicitly resume"):
                    _raw_checkpoint(path, event=event, stage="FRONT", evidence=[str(image)],
                                    summary="Paused work cannot change stage state.", now=BASE_TIME)
            _raw_checkpoint(path, event="USER_RESUMED", summary="Explicitly resume.", now=BASE_TIME)
            done = _raw_checkpoint(path, event="STAGE_COMPLETED", stage="FRONT", evidence=[str(image)],
                                   summary="Complete stage after resume.", now=BASE_TIME)
            self.assertEqual(done["required_stages"][0]["status"], "COMPLETED")

    def test_resolved_target_pack_provenance_and_dedup_roles_are_bound(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan_path = root / "REFERENCE_PLAN.json"
            seed = root / "seed.png"
            source_one = root / "one.png"
            source_two = root / "two.png"
            for path in (seed, source_one, source_two):
                Image.new("RGB", (3, 3), "red").save(path)
            request_id = "request-1"
            stage_one = "01_FACE_IDENTITY"
            stage_two = "02_PHYSIQUE_FRONT"
            stage_three = "03_PHYSIQUE_SIDE"
            plan = {
                "request_id": request_id,
                "generation_workflow": {
                    "mode": "MULTI_STAGE",
                    "attachment_limit": 5,
                    "stages": [
                        {"stage_id": stage_one, "slots": [{"path": str(seed), "sha256": guard.file_sha256(seed), "active_roles": ["STYLE"]}]},
                        {"stage_id": stage_two, "slots": [{"path": f"<STAGE_OUTPUT:{stage_one}>", "active_roles": ["FACE"]}]},
                        {"stage_id": stage_three, "slots": [
                            {"path": f"<STAGE_OUTPUT:{stage_one}>", "active_roles": ["FACE"]},
                            {"path": f"<STAGE_OUTPUT:{stage_two}>", "active_roles": ["BODY"]},
                            {"path": f"<TARGETED_STAGE_PACK:{stage_one}+{stage_two}>", "active_roles": ["MULTIVIEW"], "stage_role": "MULTIVIEW"},
                        ]},
                    ],
                },
            }
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            outputs = {
                stage_one: {"path": str(source_one), "sha256": guard.file_sha256(source_one), "request_id": request_id,
                            "reference_plan": str(plan_path.resolve()), "stage_id": stage_one, "status": "STAGING", "qa_passed": True},
                stage_two: {"path": str(source_two), "sha256": guard.file_sha256(source_two), "request_id": request_id,
                            "reference_plan": str(plan_path.resolve()), "stage_id": stage_two, "status": "STAGING", "qa_passed": True},
            }
            resolved = call_contract.resolve_stage_slots(
                plan, stage_three, outputs, root / "TECHNICAL_REFERENCES"
            )
            pack = next(slot for slot in resolved if slot.get("manifest_path"))
            placeholder = f"<TARGETED_STAGE_PACK:{stage_one}+{stage_two}>"
            call = {
                "request_id": request_id,
                "stage_id": stage_three,
                "slots": resolved,
                "stage_output_bindings": list(outputs.values()),
                "targeted_pack_bindings": [{"request_id": request_id, "stage_id": stage_three,
                    "placeholder": placeholder, "source_stage_ids": [stage_one, stage_two],
                    "path": pack["path"], "sha256": pack["sha256"], "manifest_path": pack["manifest_path"]}],
            }
            guard._validate_resolved_execution_slots(
                plan_path, plan, plan["generation_workflow"], plan["generation_workflow"]["stages"][2]["slots"], call, request_id
            )
            # The duplicate source bytes collapse into one physical slot while retaining both exact roles.
            dedup_source = next(slot for slot in resolved if slot["sha256"] == outputs[stage_one]["sha256"])
            self.assertEqual(dedup_source["active_roles"], ["BODY", "FACE"])
            forged = json.loads(json.dumps(call))
            forged["targeted_pack_bindings"][0]["request_id"] = "other-request"
            with self.assertRaises(guard.GuardError):
                guard._validate_resolved_execution_slots(
                    plan_path, plan, plan["generation_workflow"], plan["generation_workflow"]["stages"][2]["slots"], forged, request_id
                )
            forged = json.loads(json.dumps(call))
            forged["slots"][0]["active_roles"].append("UNPLANNED_ROLE")
            with self.assertRaisesRegex(guard.GuardError, "exact planned role"):
                guard._validate_resolved_execution_slots(
                    plan_path, plan, plan["generation_workflow"], plan["generation_workflow"]["stages"][2]["slots"], forged, request_id
                )
            manifest_path = Path(pack["manifest_path"])
            manifest_bytes = manifest_path.read_bytes()
            forged_manifest = json.loads(manifest_bytes)
            forged_manifest["sources"][0]["path"] = str(root / "unregistered.png")
            manifest_path.write_text(json.dumps(forged_manifest), encoding="utf-8")
            try:
                with self.assertRaisesRegex(guard.GuardError, "provenance"):
                    guard._validate_resolved_execution_slots(
                        plan_path, plan, plan["generation_workflow"], plan["generation_workflow"]["stages"][2]["slots"], call, request_id
                    )
            finally:
                manifest_path.write_bytes(manifest_bytes)
            source_one.write_bytes(b"tampered source")
            with self.assertRaisesRegex(guard.GuardError, "changed"):
                guard._validate_resolved_execution_slots(
                    plan_path, plan, plan["generation_workflow"], plan["generation_workflow"]["stages"][2]["slots"], call, request_id
                )

    def test_general_nonimage_task_keeps_direct_start_and_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, task_kind="GENERAL")
            started = _raw_checkpoint(path, event="EXECUTION_STARTED", summary="Run a non-image task.", now=BASE_TIME)
            result = _raw_checkpoint(path, event="VISIBLE_RESULT", summary="Text output is available.", now=BASE_TIME)
            self.assertEqual(result["phase"], "RESULT_AVAILABLE")
            done = _raw_checkpoint(path, event="COMPLETE", summary="Non-image task completed.", now=BASE_TIME)
            self.assertEqual(done["status"], "COMPLETE")

    def test_stop_and_resume_are_available_during_preparation(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            stopped = _raw_checkpoint(path, event="STOP", summary="Stop preparation.", now=BASE_TIME)
            self.assertEqual(stopped["status"], "STOPPED")
            with self.assertRaises(guard.GuardError):
                _raw_checkpoint(path, event="READY_FOR_EXECUTION", reference_plan="missing.json",
                                summary="Cannot ready while stopped.", now=BASE_TIME)
            resumed = _raw_checkpoint(path, event="USER_RESUMED", summary="Explicitly resume preparation.", now=BASE_TIME)
            self.assertEqual(resumed["phase"], "PREFLIGHT")

    def test_start_locks_goal_and_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            path, state = self.make_guard(folder)
            self.assertEqual(state["goal_lock"], "Create one character image.")
            self.assertEqual(state["status"], "ACTIVE")
            self.assertEqual(guard.load_guard(path)["allowed_scope"][1], "GENERATION_RESULTS")

    def test_start_locks_required_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            _, state = self.make_guard(folder, required_stages=["FACE", "FRONT", "SIDE", "BACK", "ASSEMBLY"])
            self.assertEqual(guard.pending_required_stages(state), ["FACE", "FRONT", "SIDE", "BACK", "ASSEMBLY"])

    def test_action_budget_stops_more_preflight_without_ending_task(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, max_preflight_actions=2)
            self.checkpoint_fixture(path, event="PREFLIGHT", summary="Inspect style.", now=BASE_TIME)
            with self.assertRaises(guard.GuardActionRequired):
                self.checkpoint_fixture(path, event="PREFLIGHT", summary="Inspect another file.", now=BASE_TIME)
            state = guard.load_guard(path)
            self.assertEqual(state["status"], "ACTIVE")
            self.assertEqual(state["phase"], "WATCHDOG")
            self.assertEqual(
                state["next_required_action"],
                "READY_FOR_EXECUTION_OR_EXECUTION_STARTED_OR_BLOCKER",
            )
            self.assertIn("action budget", state["watchdog_reason"])

    def test_time_budget_triggers_nonterminal_watchdog(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, max_minutes_without_execution=10)
            state = guard.guard_status(path, now=BASE_TIME + timedelta(minutes=11))
            self.assertEqual(state["status"], "ACTIVE")
            self.assertEqual(state["phase"], "WATCHDOG")
            self.assertIn("time budget", state["watchdog_reason"])
            self.assertEqual(state["events"][-1]["event"], "WATCHDOG_TRIGGERED")

    def test_prepare_gateway_remains_available_after_watchdog(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, max_minutes_without_execution=10)
            state = guard.require_active_guard(
                path,
                "request-1",
                now=BASE_TIME + timedelta(minutes=11),
            )
            self.assertEqual(state["status"], "ACTIVE")
            self.assertEqual(state["phase"], "WATCHDOG")

            ready = self.checkpoint_fixture(
                path,
                event="READY_FOR_EXECUTION",
                summary="Reference plan is ready after watchdog recovery.",
                now=BASE_TIME + timedelta(minutes=11),
            )
            self.assertEqual(ready["status"], "READY")
            self.assertEqual(ready["phase"], "READY_FOR_EXECUTION")
            self.assertNotIn("watchdog_reason", ready)

    def test_legacy_action_required_state_recovers_without_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path, state = self.make_guard(folder, max_minutes_without_execution=10)
            state["status"] = "ACTION_REQUIRED"
            state["phase"] = "GUARD_STOP"
            state["next_required_action"] = "EXECUTION_STARTED_OR_BLOCKER"
            state["action_required_reason"] = "Legacy timer cutoff."
            guard.atomic_write_json(path, state)

            recovered = guard.require_active_guard(
                path,
                "request-1",
                now=BASE_TIME + timedelta(minutes=11),
            )
            self.assertEqual(recovered["status"], "ACTIVE")
            self.assertEqual(recovered["phase"], "WATCHDOG")
            self.assertNotIn("action_required_reason", recovered)

    def test_execution_can_start_directly_after_watchdog(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, max_minutes_without_execution=10)
            guard.guard_status(path, now=BASE_TIME + timedelta(minutes=11))
            state = self.checkpoint_fixture(
                path,
                event="EXECUTION_STARTED",
                summary="Start the requested image after watchdog recovery.",
                now=BASE_TIME + timedelta(minutes=11),
            )
            self.assertEqual(state["status"], "ACTIVE")
            self.assertEqual(state["phase"], "EXECUTION")
            self.assertNotIn("watchdog_reason", state)

    def test_execution_wait_watchdog_still_accepts_visible_result(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, max_execution_minutes=10)
            self.checkpoint_fixture(
                path,
                event="EXECUTION_STARTED",
                summary="Start the requested image.",
                now=BASE_TIME,
            )
            watched = guard.guard_status(path, now=BASE_TIME + timedelta(minutes=11))
            self.assertEqual(watched["status"], "ACTIVE")
            self.assertEqual(watched["phase"], "WATCHDOG")
            self.assertEqual(
                watched["next_required_action"],
                "CHECK_EXECUTION_OR_VISIBLE_RESULT_OR_BLOCKER",
            )

            image = Path(folder) / "late-result.png"
            image.write_bytes(b"png-placeholder")
            result = self.checkpoint_fixture(
                path,
                event="VISIBLE_RESULT",
                summary="The in-flight call completed after the watchdog check.",
                evidence=[str(image)],
                now=BASE_TIME + timedelta(minutes=12),
            )
            self.assertEqual(result["status"], "ACTIVE")
            self.assertEqual(result["phase"], "RESULT_AVAILABLE")

    def test_waiting_for_user_pauses_time_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, max_minutes_without_execution=10)
            self.checkpoint_fixture(path, event="WAITING_FOR_USER", summary="Need profile choice.", now=BASE_TIME + timedelta(minutes=2))
            self.checkpoint_fixture(path, event="USER_RESUMED", summary="User selected 90 percent.", now=BASE_TIME + timedelta(hours=2))
            state = guard.guard_status(path, now=BASE_TIME + timedelta(hours=2, minutes=5))
            self.assertEqual(state["status"], "ACTIVE")

    def test_ready_for_execution_forbids_more_preflight(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            self.checkpoint_fixture(path, event="READY_FOR_EXECUTION", summary="Plan is ready.", now=BASE_TIME)
            with self.assertRaises(guard.GuardActionRequired):
                self.checkpoint_fixture(path, event="PREFLIGHT", summary="Rewrite workflow.", now=BASE_TIME)

    def test_ready_allows_one_exact_call_validation_then_requires_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            self.checkpoint_fixture(path, event="READY_FOR_EXECUTION", summary="Plan is ready.", now=BASE_TIME)
            state = self.checkpoint_fixture(
                path,
                event="CALL_VALIDATED",
                summary="Exact prompt and physical attachments passed the risk check.",
                now=BASE_TIME,
            )
            self.assertEqual(state["next_required_action"], "EXECUTION_STARTED_OR_BLOCKER")
            with self.assertRaises(guard.GuardError):
                self.checkpoint_fixture(
                    path,
                    event="CALL_VALIDATED",
                    summary="Attempt another validation pass.",
                    now=BASE_TIME,
                )

    def test_scope_change_requires_explicit_user_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            with self.assertRaises(guard.GuardError):
                self.checkpoint_fixture(path, event="SCOPE_CHANGE", summary="Edit the manager tests.", now=BASE_TIME)
            state = self.checkpoint_fixture(
                path,
                event="SCOPE_CHANGE",
                summary="User asked to change the manager.",
                user_approved_scope_change=True,
                now=BASE_TIME,
            )
            self.assertEqual(len(state["scope_changes"]), 1)

    def test_user_approved_scope_change_resumes_blocked_task(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["FRAME_01"])
            self.checkpoint_fixture(
                path,
                event="BLOCKER",
                summary="The manager policy blocks the requested reference role.",
                hard_blocker=True,
                safe_routes_exhausted=True,
                now=BASE_TIME,
            )
            state = self.checkpoint_fixture(
                path,
                event="SCOPE_CHANGE",
                summary="User authorized the narrow manager-policy change.",
                user_approved_scope_change=True,
                now=BASE_TIME + timedelta(minutes=1),
            )
            self.assertEqual(state["status"], "ACTIVE")
            self.assertEqual(state["phase"], "PREFLIGHT")
            self.assertNotIn("blocker", state)

    def test_image_result_requires_real_file_after_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            self.checkpoint_fixture(path, event="READY_FOR_EXECUTION", summary="Plan is ready.", now=BASE_TIME)
            self.checkpoint_fixture(path, event="EXECUTION_STARTED", summary="Image generator called.", now=BASE_TIME)
            image = Path(folder) / "result.png"
            image.write_bytes(b"png-placeholder")
            state = self.checkpoint_fixture(
                path,
                event="VISIBLE_RESULT",
                summary="First image produced.",
                evidence=[str(image)],
                now=BASE_TIME + timedelta(minutes=3),
            )
            self.assertIsNotNone(state["first_visible_result_at"])
            self.assertEqual(state["phase"], "RESULT_AVAILABLE")

    def test_unrequested_technical_generation_is_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["PHYSIQUE_SIDE"])
            with self.assertRaisesRegex(guard.GuardError, "Unrequested auxiliary image generation"):
                self.checkpoint_fixture(
                    path,
                    event="EXECUTION_STARTED",
                    stage="PHYSIQUE_SIDE",
                    swimwear_rung="EXTREME_MICRO",
                    summary="Generate a neutral STAGING_ONLY side mannequin for proportions.",
                    now=BASE_TIME,
                )

    def test_cli_style_image_execution_requires_output_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            with self.assertRaisesRegex(guard.GuardError, "output-contract"):
                self.checkpoint_fixture(
                    path,
                    event="EXECUTION_STARTED",
                    summary="Generate the requested character image.",
                    output_contract=None,
                    now=BASE_TIME,
                )

    def test_user_requested_technical_generation_requires_and_records_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["PHYSIQUE_SIDE"])
            with self.assertRaisesRegex(guard.GuardError, "extra-generation-evidence"):
                self.checkpoint_fixture(
                    path,
                    event="EXECUTION_STARTED",
                    stage="PHYSIQUE_SIDE",
                    swimwear_rung="EXTREME_MICRO",
                    summary="Generate the mannequin explicitly requested by the user.",
                    output_contract="USER_REQUESTED_EXTRA",
                    user_approved_extra_generation=True,
                    now=BASE_TIME,
                )
            state = self.checkpoint_fixture(
                path,
                event="EXECUTION_STARTED",
                stage="PHYSIQUE_SIDE",
                swimwear_rung="EXTREME_MICRO",
                summary="Generate the mannequin explicitly requested by the user.",
                output_contract="USER_REQUESTED_EXTRA",
                user_approved_extra_generation=True,
                extra_generation_evidence="Сгенерируй отдельный технический манекен.",
                now=BASE_TIME,
            )
            self.assertTrue(state["events"][-1]["user_approved_extra_generation"])
            self.assertIn("технический манекен", state["events"][-1]["extra_generation_evidence"])

    def test_complete_rejects_pending_required_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["FACE", "BACK", "ASSEMBLY"])
            self.checkpoint_fixture(path, event="EXECUTION_STARTED", summary="Generate face.", now=BASE_TIME)
            image = Path(folder) / "face.png"
            image.write_bytes(b"png-placeholder")
            self.checkpoint_fixture(
                path,
                event="VISIBLE_RESULT",
                summary="Face produced.",
                evidence=[str(image)],
                now=BASE_TIME,
            )
            self.checkpoint_fixture(
                path,
                event="STAGE_COMPLETED",
                stage="FACE",
                summary="Face passed QA.",
                evidence=[str(image)],
                now=BASE_TIME,
            )
            with self.assertRaises(guard.GuardActionRequired) as raised:
                self.checkpoint_fixture(path, event="COMPLETE", summary="Stop after face.", now=BASE_TIME)
            self.assertIn("BACK, ASSEMBLY", str(raised.exception))
            self.assertEqual(guard.load_guard(path)["status"], "ACTIVE")

    def test_all_required_stages_allow_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["BACK", "ASSEMBLY"])
            outputs = []
            for stage in ("BACK", "ASSEMBLY"):
                self.checkpoint_fixture(path, event="EXECUTION_STARTED", summary=f"Generate {stage}.", now=BASE_TIME)
                image = Path(folder) / f"{stage.lower()}.png"
                image.write_bytes(b"png-placeholder")
                outputs.append(image)
                self.checkpoint_fixture(
                    path,
                    event="VISIBLE_RESULT",
                    summary=f"{stage} produced.",
                    evidence=[str(image)],
                    now=BASE_TIME,
                )
                self.checkpoint_fixture(
                    path,
                    event="STAGE_COMPLETED",
                    stage=stage,
                    summary=f"{stage} passed QA.",
                    evidence=[str(image)],
                    now=BASE_TIME,
                )
            final_attempt = guard.load_guard(path)["attempts"][-1]
            self.checkpoint_fixture(
                path, event="RESULT_DELIVERED", attempt_id=final_attempt["attempt_id"],
                delivery_evidence=["fixture UI delivery receipt"],
                summary="Final QA-passed result delivered to the user.", now=BASE_TIME,
            )
            state = self.checkpoint_fixture(path, event="COMPLETE", summary="Full kit completed.", now=BASE_TIME)
            self.assertEqual(state["status"], "COMPLETE")

    def test_user_correction_preserves_remaining_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["SIDE", "BACK"])
            image = Path(folder) / "side.png"
            image.write_bytes(b"png-placeholder")
            self.checkpoint_fixture(
                path,
                event="STAGE_COMPLETED",
                stage="SIDE",
                summary="User-selected side view retained.",
                evidence=[str(image)],
                now=BASE_TIME,
            )
            state = self.checkpoint_fixture(
                path,
                event="USER_CORRECTION",
                summary="Keep SIDE and continue the original kit.",
                now=BASE_TIME,
            )
            self.assertEqual(state["status"], "ACTIVE")
            self.assertEqual(guard.pending_required_stages(state), ["BACK"])
            self.assertEqual(state["events"][-1]["event"], "USER_CORRECTION")

    def test_ambiguous_correction_cannot_change_locked_invariant(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, invariants=["camera_view=REAR"])
            with self.assertRaisesRegex(guard.GuardActionRequired, "ask the user"):
                self.checkpoint_fixture(
                    path,
                    event="USER_CORRECTION",
                    correction_impact="AMBIGUOUS",
                    summary="The wording may or may not request a new camera view.",
                    now=BASE_TIME,
                )
            self.assertEqual(guard.load_guard(path)["locked_invariants"]["camera_view"], "REAR")

    def test_preserving_correction_keeps_invariants_and_execution_must_assert_them(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(
                folder,
                invariants=["camera_view=REAR", "orientation=LANDSCAPE"],
            )
            state = self.checkpoint_fixture(
                path,
                event="USER_CORRECTION",
                correction_impact="PRESERVE",
                summary="Fix anatomy and fingers without changing the camera.",
                now=BASE_TIME,
            )
            self.assertEqual(state["events"][-1]["correction_impact"], "PRESERVE")
            with self.assertRaisesRegex(guard.GuardError, "locked task invariants"):
                self.checkpoint_fixture(
                    path,
                    event="EXECUTION_STARTED",
                    invariant_assertions=["camera_view=FRONT", "orientation=LANDSCAPE"],
                    summary="Incorrectly switch the camera.",
                    now=BASE_TIME,
                )
            started = self.checkpoint_fixture(
                path,
                event="EXECUTION_STARTED",
                invariant_assertions=["camera_view=REAR", "orientation=LANDSCAPE"],
                summary="Generate with the locked rear landscape composition.",
                now=BASE_TIME,
            )
            self.assertEqual(started["phase"], "EXECUTION")
            self.assertEqual(started["events"][-1]["invariant_assertions"]["camera_view"], "REAR")

    def test_invariant_change_requires_exact_user_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, invariants=["camera_view=REAR"])
            with self.assertRaisesRegex(guard.GuardError, "exact"):
                self.checkpoint_fixture(
                    path,
                    event="USER_CORRECTION",
                    correction_impact="CHANGE",
                    invariant_changes=["camera_view=FRONT"],
                    user_approved_invariant_change=True,
                    summary="Switch the camera.",
                    now=BASE_TIME,
                )
            changed = self.checkpoint_fixture(
                path,
                event="USER_CORRECTION",
                correction_impact="CHANGE",
                invariant_changes=["camera_view=FRONT"],
                user_approved_invariant_change=True,
                invariant_change_evidence="Сделай следующий кадр спереди.",
                summary="User explicitly changed the camera.",
                now=BASE_TIME,
            )
            self.assertEqual(changed["locked_invariants"]["camera_view"], "FRONT")
            self.assertEqual(
                changed["events"][-1]["invariant_change_evidence"],
                "Сделай следующий кадр спереди.",
            )

    def test_user_correction_can_reopen_completed_task_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["FACE", "ASSEMBLY"])
            face = Path(folder) / "face.png"
            assembly = Path(folder) / "assembly.png"
            face.write_bytes(b"face-placeholder")
            assembly.write_bytes(b"assembly-placeholder")
            for stage, image in (("FACE", face), ("ASSEMBLY", assembly)):
                self.checkpoint_fixture(
                    path,
                    event="EXECUTION_STARTED",
                    summary=f"Generate {stage}.",
                    now=BASE_TIME,
                )
                self.checkpoint_fixture(
                    path,
                    event="VISIBLE_RESULT",
                    summary=f"{stage} produced.",
                    evidence=[str(image)],
                    now=BASE_TIME,
                )
                self.checkpoint_fixture(
                    path,
                    event="STAGE_COMPLETED",
                    stage=stage,
                    summary=f"{stage} passed QA.",
                    evidence=[str(image)],
                    now=BASE_TIME,
                )
            final_attempt = guard.load_guard(path)["attempts"][-1]
            self.checkpoint_fixture(
                path, event="RESULT_DELIVERED", attempt_id=final_attempt["attempt_id"],
                delivery_evidence=["fixture UI delivery receipt"],
                summary="Initial final result delivered.", now=BASE_TIME,
            )
            self.checkpoint_fixture(path, event="COMPLETE", summary="Initial kit complete.", now=BASE_TIME)

            corrected = self.checkpoint_fixture(
                path,
                event="USER_CORRECTION",
                summary="User found drift in the final assembly.",
                now=BASE_TIME,
            )
            self.assertEqual(corrected["status"], "ACTIVE")
            self.assertEqual(corrected["phase"], "CORRECTION")

            reopened = self.checkpoint_fixture(
                path,
                event="STAGE_REOPENED",
                stage="ASSEMBLY",
                summary="Reopen only the corrected final assembly.",
                now=BASE_TIME,
            )
            self.assertEqual(guard.pending_required_stages(reopened), ["ASSEMBLY"])
            face_stage = next(item for item in reopened["required_stages"] if item["id"] == "FACE")
            self.assertEqual(face_stage["status"], "COMPLETED")

    def test_rejected_attempt_keeps_multistage_task_active(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["FRONT", "SIDE", "BACK"])
            self.checkpoint_fixture(path, event="EXECUTION_STARTED", summary="Generate FRONT.", now=BASE_TIME)
            state = self.checkpoint_fixture(
                path,
                event="ATTEMPT_REJECTED",
                stage="FRONT",
                summary="The exact generator call was rejected; continue the approved fallback route.",
                now=BASE_TIME,
            )
            self.assertEqual(state["status"], "ACTIVE")
            self.assertEqual(state["phase"], "ATTEMPT_REJECTED")
            self.assertEqual(state["next_required_action"], "NEXT_SAFE_EXECUTION")
            self.assertEqual(guard.pending_required_stages(state), ["FRONT", "SIDE", "BACK"])

    def test_corrected_same_layer_second_failure_requires_one_escalation(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["FRONT"])
            self.checkpoint_fixture(path, event="EXECUTION_STARTED", stage="FRONT", summary="Generate FRONT.", now=BASE_TIME)
            self.checkpoint_fixture(path, event="ATTEMPT_REJECTED", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="First attempt failed face geometry.", now=BASE_TIME)
            self.checkpoint_fixture(path, event="USER_CORRECTION", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="Correct face geometry only.", now=BASE_TIME)
            self.checkpoint_fixture(path, event="EXECUTION_STARTED", stage="FRONT", summary="Generate corrected FRONT.", now=BASE_TIME)
            self.checkpoint_fixture(path, event="ATTEMPT_REJECTED", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="Corrected attempt still failed face geometry.", now=BASE_TIME)
            state = guard.load_guard(path)
            self.assertEqual(state["next_required_action"], "ESCALATION_ORCHESTRATOR_REQUIRED")
            with self.assertRaises(guard.GuardActionRequired):
                self.checkpoint_fixture(path, event="EXECUTION_STARTED", stage="FRONT", summary="Incorrect extra retry.", now=BASE_TIME)
            evidence = Path(folder) / "work-order.md"
            evidence.write_text("Luna/Sol work order", encoding="utf-8")
            state = self.checkpoint_fixture(
                path, event="ESCALATION_ORCHESTRATOR_RECORDED", stage="FRONT", qa_layer="FACE_GEOMETRY",
                evidence=[str(evidence)], summary="Astra returned one bounded Luna/Sol work order.", now=BASE_TIME,
            )
            self.assertEqual(state["next_required_action"], "NEXT_SAFE_EXECUTION")

    def test_unbound_or_unrelated_correction_does_not_trigger_escalation(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["FRONT"])
            self.checkpoint_fixture(path, event="USER_CORRECTION", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="Correction before any failure.", now=BASE_TIME)
            self.checkpoint_fixture(path, event="EXECUTION_STARTED", stage="FRONT", summary="Generate FRONT.", now=BASE_TIME)
            self.checkpoint_fixture(path, event="ATTEMPT_REJECTED", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="First face failure.", now=BASE_TIME)
            self.checkpoint_fixture(path, event="USER_CORRECTION", stage="FRONT", qa_layer="BODY_PROPORTIONS", summary="Unrelated correction.", now=BASE_TIME)
            self.checkpoint_fixture(path, event="EXECUTION_STARTED", stage="FRONT", summary="Retry FRONT.", now=BASE_TIME)
            state = self.checkpoint_fixture(path, event="ATTEMPT_REJECTED", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="Second face failure.", now=BASE_TIME)
            self.assertEqual(state["next_required_action"], "NEXT_SAFE_EXECUTION")

    def test_physique_swimwear_ladder_starts_at_extreme_micro(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["PHYSIQUE_FRONT"])
            with self.assertRaises(guard.GuardError) as raised:
                self.checkpoint_fixture(
                    path,
                    event="EXECUTION_STARTED",
                    stage="PHYSIQUE_FRONT",
                    swimwear_rung="BIKINI",
                    summary="Incorrectly skip the default first rung.",
                    now=BASE_TIME,
                )
            self.assertIn("ladder jump", str(raised.exception))

    def test_physique_swimwear_ladder_advances_only_after_recorded_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["PHYSIQUE_FRONT"])
            self.checkpoint_fixture(
                path,
                event="EXECUTION_STARTED",
                stage="PHYSIQUE_FRONT",
                swimwear_rung="EXTREME_MICRO",
                summary="Generate the mandatory default extreme-micro rung.",
                now=BASE_TIME,
            )
            self.checkpoint_fixture(
                path,
                event="ATTEMPT_REJECTED",
                stage="PHYSIQUE_FRONT",
                swimwear_rung="EXTREME_MICRO",
                rung_routes_exhausted=True,
                summary="The exact extreme-micro call was rejected.",
                now=BASE_TIME,
            )
            state = self.checkpoint_fixture(
                path,
                event="EXECUTION_STARTED",
                stage="PHYSIQUE_FRONT",
                swimwear_rung="BIKINI",
                summary="Advance to ordinary bikini after the recorded failure.",
                now=BASE_TIME,
            )
            self.assertEqual(state["active_swimwear_attempt"]["swimwear_rung"], "BIKINI")

    def test_single_rejection_does_not_unlock_next_swimwear_rung(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["PHYSIQUE_FRONT"])
            self.checkpoint_fixture(
                path,
                event="EXECUTION_STARTED",
                stage="PHYSIQUE_FRONT",
                swimwear_rung="EXTREME_MICRO",
                summary="Try one exact route for the fixed extreme-micro target.",
                now=BASE_TIME,
            )
            self.checkpoint_fixture(
                path,
                event="ATTEMPT_REJECTED",
                stage="PHYSIQUE_FRONT",
                swimwear_rung="EXTREME_MICRO",
                summary="One prompt and BODY combination failed; other routes remain.",
                now=BASE_TIME,
            )
            with self.assertRaises(guard.GuardError) as raised:
                self.checkpoint_fixture(
                    path,
                    event="EXECUTION_STARTED",
                    stage="PHYSIQUE_FRONT",
                    swimwear_rung="BIKINI",
                    summary="Incorrectly advance after one failed route.",
                    now=BASE_TIME,
                )
            self.assertIn("rung-routes-exhausted", str(raised.exception))

    def test_wrong_observed_topology_cannot_complete_physique_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["PHYSIQUE_FRONT"])
            self.checkpoint_fixture(
                path,
                event="EXECUTION_STARTED",
                stage="PHYSIQUE_FRONT",
                swimwear_rung="EXTREME_MICRO",
                summary="Generate the mandatory default rung.",
                now=BASE_TIME,
            )
            image = Path(folder) / "front.png"
            image.write_bytes(b"png-placeholder")
            self.checkpoint_fixture(
                path,
                event="VISIBLE_RESULT",
                summary="A visible front result was produced.",
                evidence=[str(image)],
                now=BASE_TIME,
            )
            with self.assertRaises(guard.GuardError) as raised:
                self.checkpoint_fixture(
                    path,
                    event="STAGE_COMPLETED",
                    stage="PHYSIQUE_FRONT",
                    swimwear_rung="EXTREME_MICRO",
                    observed_topology="SPORT_TOP",
                    summary="Incorrectly accept a sports top.",
                    evidence=[str(image)],
                    now=BASE_TIME,
                )
            self.assertIn("hard gate failed", str(raised.exception))

    def test_direct_user_override_requires_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["PHYSIQUE_FRONT"])
            with self.assertRaises(guard.GuardError):
                self.checkpoint_fixture(
                    path,
                    event="EXECUTION_STARTED",
                    stage="PHYSIQUE_FRONT",
                    swimwear_rung="CUSTOM",
                    user_swimwear_override=True,
                    summary="Try a custom user-requested coverage.",
                    now=BASE_TIME,
                )

    def test_ordinary_rejection_cannot_become_terminal_blocker(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["FRONT", "SIDE"])
            with self.assertRaises(guard.GuardActionRequired) as raised:
                self.checkpoint_fixture(
                    path,
                    event="BLOCKER",
                    summary="One reference set was rejected.",
                    now=BASE_TIME,
                )
            self.assertIn("ATTEMPT_REJECTED", str(raised.exception))
            self.assertEqual(guard.load_guard(path)["status"], "ACTIVE")

    def test_hard_blocker_requires_exhausted_safe_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["FRONT"])
            state = self.checkpoint_fixture(
                path,
                event="BLOCKER",
                summary="The external generator is unavailable and every approved safe route is exhausted.",
                hard_blocker=True,
                safe_routes_exhausted=True,
                now=BASE_TIME,
            )
            self.assertEqual(state["status"], "BLOCKED")
            self.assertTrue(state["events"][-1]["safe_routes_exhausted"])

    def test_user_correction_resumes_erroneously_blocked_task(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["FRONT", "SIDE"])
            self.checkpoint_fixture(
                path,
                event="BLOCKER",
                summary="All approved safe routes were believed to be exhausted.",
                hard_blocker=True,
                safe_routes_exhausted=True,
                now=BASE_TIME,
            )
            state = self.checkpoint_fixture(
                path,
                event="USER_CORRECTION",
                summary="Use the existing approved safe route and finish every stage.",
                now=BASE_TIME + timedelta(minutes=1),
            )
            self.assertEqual(state["status"], "ACTIVE")
            self.assertEqual(state["next_required_action"], "NEXT_SAFE_EXECUTION_OR_COMPLETE")
            self.assertNotIn("blocker", state)
            self.assertEqual(guard.pending_required_stages(state), ["FRONT", "SIDE"])

    def test_wait_after_execution_requires_exhausted_safe_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["FRONT"])
            self.checkpoint_fixture(path, event="EXECUTION_STARTED", summary="Generate FRONT.", now=BASE_TIME)
            with self.assertRaises(guard.GuardActionRequired):
                self.checkpoint_fixture(
                    path,
                    event="WAITING_FOR_USER",
                    summary="Ask for another clothing decision.",
                    now=BASE_TIME,
                )
            self.assertEqual(guard.load_guard(path)["status"], "ACTIVE")

    def test_stage_can_be_reopened_after_correction(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["SIDE"])
            image = Path(folder) / "side.png"
            image.write_bytes(b"png-placeholder")
            self.checkpoint_fixture(
                path,
                event="STAGE_COMPLETED",
                stage="SIDE",
                summary="Side passed QA.",
                evidence=[str(image)],
                now=BASE_TIME,
            )
            state = self.checkpoint_fixture(
                path,
                event="STAGE_REOPENED",
                stage="SIDE",
                summary="User requested a correction to SIDE.",
                now=BASE_TIME,
            )
            self.assertEqual(guard.pending_required_stages(state), ["SIDE"])
            self.assertEqual(state["events"][-1]["stage"], "SIDE")


if __name__ == "__main__":
    unittest.main()
