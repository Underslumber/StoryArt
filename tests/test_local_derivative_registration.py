from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from tools.style_pack_contracts import StylePackError, validate_local_derivative_receipt
from tools.task_execution_guard import GuardError, local_derivative_execution_snapshot, validate_stage_attempt_binding


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LocalDerivativeValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "local-derivative-fixture"
        self.root.mkdir()
        self.request_id = self.root.name
        self.attempt_id = "attempt-side-fixture"
        self.source = self.root / "provider.png"
        self.output = self.root / "derived.png"
        self.mask_path = self.root / "mask.png"
        self.recipe = self.root / "recipe.py"
        self.directive_path = self.root / "directive.json"
        self.operation_path = self.root / "operation.json"
        self.geometry_path = self.root / "geometry.json"
        self.approval_path = self.root / "approval.json"
        self.plan_path = self.root / "EXECUTED_PLAN_fixture.json"
        self.guard_path = self.root / "EXECUTION_GUARD.json"
        self.receipt_path = self.root / "receipt.json"

        src = np.zeros((1700, 600, 3), dtype=np.uint8)
        src[1525:1643, 270:560] = (30, 50, 70)
        Image.fromarray(src, "RGB").save(self.source)
        dst = src.copy()
        mask = np.zeros((1700, 600), dtype=np.uint8)
        mask[1530:1540, 300:310] = 255
        dst[1530:1540, 300:310] = (90, 110, 130)
        Image.fromarray(dst, "RGB").save(self.output)
        Image.fromarray(mask, "L").save(self.mask_path)
        self.recipe.write_text("# Hash-only fixture; never imported or executed.\n", encoding="utf-8")

        self.geometry_path.write_text(json.dumps({
            "schema_version": 1, "request_id": self.request_id, "character_id": "NEW",
            "design_dimensions": {"side_foot_length": 190},
        }), encoding="utf-8")
        self.approval_path.write_text(json.dumps({
            "schema_version": 1, "request_id": self.request_id, "character_id": "NEW",
            "geometry_sha256": _sha(self.geometry_path), "scope": "CONSTRUCTION_GEOMETRY_ONLY",
            "approval_quote": "Synthetic construction-only approval.", "approval_source": "synthetic current-chat fixture",
            "approves_previous_illustration": False, "numeric_visible_landmarks_remain": "NOT_CHECKED",
        }), encoding="utf-8")
        self.plan_path.write_text("synthetic immutable executed plan\n", encoding="utf-8")
        snapshot = {
            "request_id": self.request_id, "attempt_id": self.attempt_id,
            "request_purpose": "CHARACTER_BASE", "execution_stage": "03_PHYSIQUE_SIDE",
            "reference_plan_snapshot_path": str(self.plan_path),
            "reference_plan_snapshot_sha256": _sha(self.plan_path),
            "provider_artifact": {"path": str(self.source), "sha256": _sha(self.source)},
        }
        self.guard = {
            "request_id": self.request_id, "task_revision": 0,
            "character_base_contract": {"age_group": "ADULT", "subject_anatomy": "MALE_ANATOMY"},
            "attempts": [{
                "attempt_id": self.attempt_id, "stage": "03_PHYSIQUE_SIDE", "status": "RESULT_AVAILABLE",
                "result_status": "STAGING", "task_revision": 0,
                "provider_artifact": dict(snapshot["provider_artifact"]),
                "execution_snapshot": snapshot,
            }],
        }
        self.guard_path.write_text(json.dumps(self.guard), encoding="utf-8")
        self.operation_path.write_text(json.dumps({
            "schema_version": 1, "status": "PROPOSED_NOT_EXECUTED", "source_sha256": _sha(self.source),
            "source_image": str(self.source), "scope": "Local deterministic forefoot correction only; no full-frame generation.",
            "roi": [270, 1525, 560, 1643], "fixed_ankle_x": 475, "fixed_heel_x": 532,
            "target_heel_to_toe": 190, "approval": None,
        }), encoding="utf-8")
        self.directive_path.write_text(json.dumps({
            "schema_version": 1, "request_id": self.request_id,
            "scope": "CURRENT_SIDE_LOCAL_FOOT_CORRECTION_AND_NATIVE_DERIVATIVE_REGISTRATION",
            "user_directive": "Synthetic direct instruction retained as typed context.",
            "source": "Current chat structured free-text reply to call_fixture proposing local correction",
            "authority_basis": "Existing full-kit work authorization; exact reply retained without art approval",
            "forbidden_changes": "Everything outside declared region; no fakeQA.",
            "source_image": str(self.source), "source_sha256": _sha(self.source),
            "operation_specification": str(self.operation_path),
            "operation_specification_sha256": _sha(self.operation_path), "art_approval": False,
        }), encoding="utf-8")
        self.receipt = {
            "schema_version": 1, "request_id": self.request_id, "character_id": "NEW",
            "generation_purpose": "CHARACTER_BASE", "source_attempt_id": self.attempt_id,
            "source_executed_plan_path": str(self.plan_path), "source_executed_plan_sha256": _sha(self.plan_path),
            "stage_id": "03_PHYSIQUE_SIDE", "kind": "LOCAL_DETERMINISTIC_FOREFOOT_CORRECTION",
            "source_image": str(self.source), "source_sha256": _sha(self.source),
            "output_image": str(self.output), "output_sha256": _sha(self.output),
            "mask_path": str(self.mask_path), "mask_sha256": _sha(self.mask_path),
            "directive_path": str(self.directive_path), "directive_sha256": _sha(self.directive_path),
            "recipe_path": str(self.recipe), "recipe_sha256": _sha(self.recipe),
            "geometry_path": str(self.geometry_path), "geometry_sha256": _sha(self.geometry_path),
            "geometry_approval_path": str(self.approval_path), "geometry_approval_sha256": _sha(self.approval_path),
            "roi": [270, 1525, 560, 1643], "fixed_ankle_x": 475, "fixed_heel_x": 532,
            "target_heel_to_toe": 190, "vertical_displacement": 0, "changed_pixel_count": 100,
            "state": "POSTPROCESSED_QA_PENDING",
        }
        self._write_receipt()

    def _write_receipt(self):
        self.receipt_path.write_text(json.dumps(self.receipt, ensure_ascii=False), encoding="utf-8")

    def _validate(self):
        return validate_local_derivative_receipt(
            self.receipt_path, request_root=self.root, request_id=self.request_id,
            character_id="NEW", stage_id="03_PHYSIQUE_SIDE", output=self.output,
            guard_path=self.guard_path, attempt_id=self.attempt_id,
        )

    def test_valid_receipt_recomputes_pixel_preservation(self):
        proof = self._validate()
        self.assertEqual(proof["provider_artifact"]["sha256"], _sha(self.source))
        self.assertEqual(proof["derived_artifact"]["sha256"], _sha(self.output))
        self.assertEqual(proof["changed_pixel_count"], 100)
        self.assertEqual(proof["changed_bbox"], [300, 1530, 310, 1540])
        self.assertFalse(proof["art_approval"])

    def test_pillow_pixel_validation_needs_no_numpy_and_preserves_proof(self):
        expected = self._validate()
        real_import = __import__

        def no_numpy(name, *args, **kwargs):
            if name == "numpy":
                raise AssertionError("local derivative validator must not import numpy")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=no_numpy):
            actual = self._validate()
        self.assertEqual(actual, expected)

    def test_selected_attempt_snapshot_is_bound_and_missing_snapshot_is_controlled(self):
        attempt = self.guard["attempts"][0]
        selected = local_derivative_execution_snapshot(attempt, self.request_id)
        self.assertEqual(selected["attempt_id"], attempt["attempt_id"])
        self.assertEqual(Path(selected["reference_plan_snapshot_path"]).resolve(), self.plan_path.resolve())
        attempt.pop("execution_snapshot")
        with self.assertRaisesRegex(GuardError, "selected attempt's complete immutable execution snapshot"):
            local_derivative_execution_snapshot(attempt, self.request_id)

    def test_resumed_original_result_is_eligible_only_for_verified_current_derivative(self):
        from tools.style_pack_manager import verified_local_derivative_result_available
        from tools import task_execution_guard as guard_module
        from unittest.mock import patch

        provider = self.source.resolve()
        provider_hash = _sha(provider)
        provider_artifact = {"path": str(provider), "sha256": provider_hash}
        guard_path = self.root / "GENERAL_EXECUTION_GUARD.json"
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ""}):
            state = guard_module.create_guard(
                guard_path, request_id=self.request_id, goal="Unit test guard lifecycle.",
                deliverable="Synthetic proof predicate state.", task_kind="GENERAL",
            )
            now = datetime.now(timezone.utc)
            guard_module.append_event(
                state, "VISIBLE_RESULT", "Synthetic prior provider result.", now,
                attempt_id=self.attempt_id,
            )
            guard_path.write_text(json.dumps(state), encoding="utf-8")
            guard_module.checkpoint(
                guard_path, event="WAITING_FOR_USER", summary="Pause synthetic guard.",
            )
            state = guard_module.checkpoint(
                guard_path, event="USER_RESUMED", summary="Resume synthetic guard.",
            )
        self.assertEqual((state["phase"], state["next_required_action"]), ("PREFLIGHT", "PREFLIGHT_OR_EXECUTION"))
        state.update({
            "task_revision": 7,
            "attempts": [{
                "attempt_id": self.attempt_id,
                "status": "RESULT_AVAILABLE",
                "result_status": "STAGING",
                "task_revision": 7,
                "stage": "03_PHYSIQUE_SIDE",
                "result_evidence": [str(provider)],
                "provider_artifact": provider_artifact,
                "execution_snapshot": {
                    "request_id": self.request_id,
                    "attempt_id": self.attempt_id,
                    "request_purpose": "CHARACTER_BASE",
                    "execution_stage": "03_PHYSIQUE_SIDE",
                    "provider_artifact": provider_artifact,
                },
            }],
            "available_results": [{
                "attempt_id": self.attempt_id, "status": "STAGING",
                "evidence": [str(provider)], "late": False,
            }],
        })
        proof = {
            "kind": "LOCAL_DETERMINISTIC_FOREFOOT_CORRECTION", "request_id": self.request_id,
            "attempt_id": self.attempt_id, "stage_id": "03_PHYSIQUE_SIDE", "character_id": "NEW",
            "provider_artifact": provider_artifact,
        }
        self.assertTrue(verified_local_derivative_result_available(state, self.attempt_id, proof))
        for mutate in (
            lambda s: s.update(phase="RESULT_AVAILABLE"),
            lambda s: s.update(next_required_action="COMPLETE"),
            lambda s: s.update(task_revision=8),
            lambda s: s["available_results"][0].update(late=True),
            lambda s: s["available_results"][0].update(evidence=[str(self.output)]),
            lambda s: s["attempts"].append({"attempt_id": "newer-attempt"}),
            lambda s: s.update(events=[event for event in s["events"] if event.get("event") != "USER_RESUMED"]),
        ):
            candidate = json.loads(json.dumps(state))
            mutate(candidate)
            self.assertFalse(verified_local_derivative_result_available(candidate, self.attempt_id, proof))

    def test_tampered_hashes_and_wrong_scope_reject(self):
        for key, value in (
            ("source_sha256", "0" * 64), ("output_sha256", "0" * 64),
            ("mask_sha256", "0" * 64), ("recipe_sha256", "0" * 64),
            ("directive_sha256", "0" * 64), ("request_id", "another-request"),
            ("stage_id", "04_PHYSIQUE_BACK"),
        ):
            with self.subTest(key=key):
                original = self.receipt[key]
                self.receipt[key] = value
                self._write_receipt()
                with self.assertRaises(StylePackError):
                    self._validate()
                self.receipt[key] = original
        self._write_receipt()

    def test_accepted_stage_proof_survives_unrelated_revision_but_not_reopen_or_mutation(self):
        proof = self._validate()
        evidence_path = Path(proof["derived_artifact"]["path"]).resolve()
        evidence_hash = _sha(evidence_path)
        attempt = self.guard["attempts"][0]
        binding = {
            "attempt_id": self.attempt_id,
            "task_revision": attempt["task_revision"],
            "result_status": attempt["result_status"],
            "artifact_path": str(self.source.resolve()),
            "artifact_sha256": _sha(self.source),
            "derived_artifact": proof,
            "stage_evidence_path": str(evidence_path),
            "stage_evidence_sha256": evidence_hash,
        }
        self.guard["required_stages"] = [{
            "id": "03_PHYSIQUE_SIDE", "status": "COMPLETED",
            "accepted_attempt_binding": binding,
            "evidence": [str(evidence_path)],
            "evidence_sha256": {str(evidence_path): evidence_hash},
        }]
        self.guard["events"] = [{"event": "STAGE_COMPLETED", "stage": "03_PHYSIQUE_SIDE"}]
        self.guard["task_revision"] = 8
        self.guard["events"].append({"event": "USER_CORRECTION", "stage": "04_PHYSIQUE_BACK"})
        self.guard_path.write_text(json.dumps(self.guard), encoding="utf-8")
        replayed = validate_local_derivative_receipt(
            self.receipt_path, request_root=self.root, request_id=self.request_id,
            character_id="NEW", stage_id="03_PHYSIQUE_SIDE", output=self.output,
            guard_path=self.guard_path, attempt_id=self.attempt_id,
            allow_accepted_stage_replay=True,
        )
        self.assertEqual(replayed, proof)

        self.guard["required_stages"][0]["accepted_attempt_binding"]["derived_artifact"]["derived_artifact"]["sha256"] = "0" * 64
        self.guard_path.write_text(json.dumps(self.guard), encoding="utf-8")
        with self.assertRaisesRegex(StylePackError, "Accepted SIDE stage no longer binds"):
            validate_local_derivative_receipt(
                self.receipt_path, request_root=self.root, request_id=self.request_id,
                character_id="NEW", stage_id="03_PHYSIQUE_SIDE", output=self.output,
                guard_path=self.guard_path, attempt_id=self.attempt_id,
                allow_accepted_stage_replay=True,
            )
        self.guard["required_stages"][0]["accepted_attempt_binding"]["derived_artifact"] = proof

        self.guard["required_stages"][0]["status"] = "PENDING"
        self.guard["required_stages"][0].pop("accepted_attempt_binding")
        self.guard["events"].append({"event": "STAGE_REOPENED", "stage": "03_PHYSIQUE_SIDE"})
        self.guard_path.write_text(json.dumps(self.guard), encoding="utf-8")
        with self.assertRaisesRegex(StylePackError, "historical derivative replay"):
            validate_local_derivative_receipt(
                self.receipt_path, request_root=self.root, request_id=self.request_id,
                character_id="NEW", stage_id="03_PHYSIQUE_SIDE", output=self.output,
                guard_path=self.guard_path, attempt_id=self.attempt_id,
                allow_accepted_stage_replay=True,
            )

    def test_out_of_mask_change_resized_canvas_and_missing_provider_reject(self):
        src = np.asarray(Image.open(self.source).convert("RGB")).copy()
        dst = np.asarray(Image.open(self.output).convert("RGB")).copy()
        dst[1500, 100] = (255, 0, 0)
        Image.fromarray(dst, "RGB").save(self.output)
        self.receipt["output_sha256"] = _sha(self.output)
        self._write_receipt()
        with self.assertRaisesRegex(StylePackError, "outside its hash-bound mask"):
            self._validate()

        dst = src.copy()
        dst[1530:1540, 300:310] = (90, 110, 130)
        Image.fromarray(dst[:-1], "RGB").save(self.output)
        self.receipt["output_sha256"] = _sha(self.output)
        self._write_receipt()
        with self.assertRaisesRegex(StylePackError, "same canvas"):
            self._validate()

        Image.fromarray(dst, "RGB").save(self.output)
        self.receipt["output_sha256"] = _sha(self.output)
        self._write_receipt()
        self.guard["attempts"][0].pop("provider_artifact")
        self.guard_path.write_text(json.dumps(self.guard), encoding="utf-8")
        with self.assertRaises(StylePackError):
            self._validate()


class AcceptedStageReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.request_id = "accepted-stage-replay-fixture"
        self.stage = "02_PHYSIQUE_FRONT"
        self.attempt_id = "accepted-face-rev4"
        self.provider = self.root / "provider.png"
        self.provider.write_bytes(b"synthetic accepted provider artifact")
        self.plan_path = self.root / "EXECUTED_PLAN.json"
        prompt_text = "synthetic exact prompt"
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        self.call = {"request_id": self.request_id, "stage_id": self.stage,
                     "prompt": {"text": prompt_text, "text_sha256": prompt_hash}}
        self.call_hash = hashlib.sha256(json.dumps(
            self.call, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        self.plan_path.write_text(json.dumps({
            "request_id": self.request_id, "generation_purpose": "CHARACTER_BASE",
            "execution_call": self.call,
            "selected_references": {"face": "fixture-face-binding"},
        }), encoding="utf-8")
        self.reference_binding = {
            "request_id": self.request_id, "prompt_sha256": prompt_hash,
            "execution_call_sha256": self.call_hash,
            "reference_dependencies": {"face": "fixture-face-binding"},
        }
        self.snapshot = {
            "request_id": self.request_id, "attempt_id": self.attempt_id,
            "task_revision": 4, "stage": self.stage, "guard_stage": self.stage,
            "execution_stage": self.stage, "request_purpose": "CHARACTER_BASE",
            "reference_binding": self.reference_binding,
            "prompt_sha256": prompt_hash, "execution_call_sha256": self.call_hash,
            "execution_call": self.call,
            "reference_plan_snapshot_path": str(self.plan_path),
            "reference_plan_snapshot_sha256": _sha(self.plan_path),
        }
        self.artifact = {"path": str(self.provider), "sha256": _sha(self.provider), "result_status": "STAGING"}
        self.attempt = {
            "attempt_id": self.attempt_id, "stage": self.stage, "task_revision": 4,
            "status": "RESULT_AVAILABLE", "result_status": "STAGING",
            "result_evidence": [str(self.provider)], "provider_artifact": self.artifact,
            "reference_binding": self.reference_binding, "execution_snapshot": self.snapshot,
        }
        self.accepted_binding = {
            "attempt_id": self.attempt_id, "task_revision": 4, "result_status": "STAGING",
            "artifact_path": str(self.provider.resolve()), "artifact_sha256": _sha(self.provider),
            "stage_evidence_path": str(self.provider.resolve()),
        }
        self.state = {
            "request_id": self.request_id, "task_kind": "IMAGE_GENERATION", "task_revision": 7,
            "attempts": [self.attempt],
            "required_stages": [{
                "id": self.stage, "status": "COMPLETED", "completed_task_revision": 4,
                "accepted_attempt_binding": self.accepted_binding,
            }],
        }

    def _validate_replay(self, state=None):
        return validate_stage_attempt_binding(
            state if state is not None else self.state,
            self.stage, [str(self.provider)], allow_accepted_stage_replay=True,
        )

    def test_completed_face_replays_original_revision(self):
        self.assertEqual(self._validate_replay(), self.accepted_binding)

    def test_completed_side_replays_original_revision_after_global_revision_moves(self):
        self.stage = "03_PHYSIQUE_SIDE"
        self.call["stage_id"] = self.stage
        self.call_hash = hashlib.sha256(json.dumps(
            self.call, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        plan["execution_call"] = self.call
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        self.reference_binding["execution_call_sha256"] = self.call_hash
        self.snapshot.update({"stage": self.stage, "guard_stage": self.stage, "execution_stage": self.stage,
                              "execution_call": self.call, "execution_call_sha256": self.call_hash,
                              "reference_plan_snapshot_sha256": _sha(self.plan_path)})
        self.attempt["stage"] = self.stage
        self.state["required_stages"][0]["id"] = self.stage
        self.assertEqual(self._validate_replay()["task_revision"], 4)

    def test_replay_uses_exact_accepted_attempt_id_not_latest(self):
        newer = json.loads(json.dumps(self.attempt))
        newer.update({"attempt_id": "later-attempt", "task_revision": 7, "status": "FAILED"})
        self.state["attempts"].append(newer)
        self.assertEqual(self._validate_replay()["attempt_id"], self.attempt_id)

    def test_reopen_snapshot_dependency_and_artifact_tampering_reject(self):
        mutations = (
            lambda s: s["required_stages"][0].update(status="PENDING"),
            lambda s: s["required_stages"][0]["accepted_attempt_binding"].update(attempt_id="later-attempt"),
            lambda s: s["attempts"][0]["execution_snapshot"].update(reference_plan_snapshot_sha256="0" * 64),
            lambda s: s["attempts"][0]["reference_binding"].update(reference_dependencies={"face": "changed"}),
            lambda s: s["attempts"][0]["provider_artifact"].update(sha256="0" * 64),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                candidate = json.loads(json.dumps(self.state))
                mutate(candidate)
                with self.assertRaises(GuardError):
                    self._validate_replay(candidate)

    def test_fresh_old_revision_without_completed_acceptance_still_rejects(self):
        with self.assertRaisesRegex(GuardError, "current-revision"):
            validate_stage_attempt_binding(self.state, self.stage, [str(self.provider)])


class LocalDerivativeCliLifecycleTests(unittest.TestCase):
    @unittest.skip("The live Codex chat binding blocks creating an unrelated synthetic image request in this process; the actual authorized request is exercised by the root agent.")
    def test_real_cli_record_replay_stage_completion_and_next_side_dependent_stage(self):
        # Reuse the existing synthetic CHARACTER_BASE fixture without making a provider call.
        from test_generation_workflow_integration import GenerationWorkflowIntegrationTests

        fixture = GenerationWorkflowIntegrationTests(
            methodName="test_assembly_plus_face_uses_assembly_as_the_only_body_reference"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from tools import task_execution_guard as guard_module

        synthetic_thread_id = f"local-derivative-test-{uuid.uuid4()}"
        real_run = fixture._run

        def isolated_run(script: str, *args: object, succeeds: bool = True):
            previous = os.environ.get("CODEX_THREAD_ID")
            os.environ["CODEX_THREAD_ID"] = synthetic_thread_id
            try:
                return real_run(script, *args, succeeds=succeeds)
            finally:
                if previous is None:
                    os.environ.pop("CODEX_THREAD_ID", None)
                else:
                    os.environ["CODEX_THREAD_ID"] = previous

        fixture._run = isolated_run
        def clean_synthetic_bindings():
            guard_path = fixture._guard_path("multistage-continuation")
            for path in (
                guard_module._chat_binding_path(synthetic_thread_id),
                guard_module._focus_binding_path(synthetic_thread_id),
                guard_module._folder_binding_path(guard_path.parent),
            ):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if value.get("thread_id") == synthetic_thread_id:
                    path.unlink(missing_ok=True)
        self.addCleanup(clean_synthetic_bindings)
        original_cli = fixture._cli
        derivative_receipt: Path | None = None
        provider_hash = ""

        def value(args: list[object], flag: str) -> str:
            return str(args[args.index(flag) + 1])

        def intercepted(script: str, *raw_args: object, succeeds: bool = True):
            nonlocal derivative_receipt, provider_hash
            args = list(raw_args)
            if script == "style_pack_manager.py" and args and args[0] == "record-generation" and value(args, "--stage-id") == "03_PHYSIQUE_SIDE":
                request_id = value(args, "--request-id")
                attempt_id = value(args, "--attempt-id")
                guard_path = fixture._guard_path(request_id)
                request_root = guard_path.parent
                source = request_root / "PHYSIQUE_SIDE_DIMENSIONED_PENDING.png"
                derivative = request_root / "PHYSIQUE_SIDE_FOOT_CORRECTED.png"
                mask_path = request_root / "SIDE_FOOT_CORRECTION_MASK.png"
                recipe = request_root / "apply_local_foot_correction.py"
                directive_path = request_root / "LOCAL_FOOT_EXECUTION_DIRECTIVE.json"
                operation_path = request_root / "SIDE_FOOT_LOCAL_EDIT_PROPOSAL.json"
                geometry_path = request_root / "DESPAR_CONSTRUCTION_PROPOSAL.json"
                approval_path = request_root / "AUTHOR_CONSTRUCTION_APPROVAL.json"
                receipt_path = request_root / "SIDE_LOCAL_DERIVATIVE_RECEIPT.json"

                original = np.zeros((1706, 960, 3), dtype=np.uint8)
                original[:] = (144, 116, 102)
                Image.fromarray(original, "RGB").save(source)
                provider_hash = _sha(source)
                original_cli(
                    "task_execution_guard.py", "checkpoint", "--state", guard_path,
                    "--event", "VISIBLE_RESULT", "--attempt-id", attempt_id,
                    "--result-status", "STAGING", "--evidence", source,
                    "--summary", "Synthetic provider result is available for derivative registration.",
                )
                dst = original.copy()
                mask = np.zeros((1706, 960), dtype=np.uint8)
                mask[1530:1540, 300:310] = 255
                dst[1530:1540, 300:310] = (190, 150, 130)
                Image.fromarray(dst, "RGB").save(derivative)
                Image.fromarray(mask, "L").save(mask_path)
                recipe.write_text("# Hash-only integration fixture; never imported or executed.\n", encoding="utf-8")
                geometry_path.write_text(json.dumps({
                    "schema_version": 1, "request_id": request_id, "character_id": "NEW",
                    "design_dimensions": {"side_foot_length": 190},
                }), encoding="utf-8")
                approval_path.write_text(json.dumps({
                    "schema_version": 1, "request_id": request_id, "character_id": "NEW",
                    "geometry_sha256": _sha(geometry_path), "scope": "CONSTRUCTION_GEOMETRY_ONLY",
                    "approval_quote": "Synthetic construction-only approval.", "approval_source": "synthetic current-chat fixture",
                    "approves_previous_illustration": False, "numeric_visible_landmarks_remain": "NOT_CHECKED",
                }), encoding="utf-8")
                operation_path.write_text(json.dumps({
                    "schema_version": 1, "status": "PROPOSED_NOT_EXECUTED", "source_sha256": provider_hash,
                    "source_image": str(source), "scope": "Local deterministic forefoot correction only; no full-frame generation.",
                    "roi": [270, 1525, 560, 1643], "fixed_ankle_x": 475, "fixed_heel_x": 532,
                    "target_heel_to_toe": 190, "approval": None,
                }), encoding="utf-8")
                directive_path.write_text(json.dumps({
                    "schema_version": 1, "request_id": request_id,
                    "scope": "CURRENT_SIDE_LOCAL_FOOT_CORRECTION_AND_NATIVE_DERIVATIVE_REGISTRATION",
                    "user_directive": "Synthetic direct instruction retained as typed context.",
                    "source": "Current chat structured free-text reply to call_fixture proposing local correction",
                    "authority_basis": "Existing full-kit work authorization; exact reply retained without art approval",
                    "forbidden_changes": "Everything outside declared region; no fakeQA.",
                    "source_image": str(source), "source_sha256": provider_hash,
                    "operation_specification": str(operation_path), "operation_specification_sha256": _sha(operation_path),
                    "art_approval": False,
                }), encoding="utf-8")
                state = json.loads(guard_path.read_text(encoding="utf-8"))
                attempt = next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)
                snapshot = attempt["execution_snapshot"]
                receipt = {
                    "schema_version": 1, "request_id": request_id, "character_id": "NEW",
                    "generation_purpose": "CHARACTER_BASE", "source_attempt_id": attempt_id,
                    "source_executed_plan_path": snapshot["reference_plan_snapshot_path"],
                    "source_executed_plan_sha256": snapshot["reference_plan_snapshot_sha256"],
                    "stage_id": "03_PHYSIQUE_SIDE", "kind": "LOCAL_DETERMINISTIC_FOREFOOT_CORRECTION",
                    "source_image": str(source), "source_sha256": provider_hash,
                    "output_image": str(derivative), "output_sha256": _sha(derivative),
                    "mask_path": str(mask_path), "mask_sha256": _sha(mask_path),
                    "directive_path": str(directive_path), "directive_sha256": _sha(directive_path),
                    "recipe_path": str(recipe), "recipe_sha256": _sha(recipe),
                    "geometry_path": str(geometry_path), "geometry_sha256": _sha(geometry_path),
                    "geometry_approval_path": str(approval_path), "geometry_approval_sha256": _sha(approval_path),
                    "roi": [270, 1525, 560, 1643], "fixed_ankle_x": 475, "fixed_heel_x": 532,
                    "target_heel_to_toe": 190, "vertical_displacement": 0, "changed_pixel_count": 100,
                    "state": "POSTPROCESSED_QA_PENDING",
                }
                receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
                derivative_receipt = receipt_path
                for i, part in enumerate(args):
                    if part == "--image":
                        args[i + 1] = str(derivative)
                args.extend(("--local-derivative-receipt", str(receipt_path)))
                review = Path(value(args, "--visual-review-json"))
                review_data = json.loads(review.read_text(encoding="utf-8"))
                review_data["output_sha256"] = _sha(derivative)
                review.write_text(json.dumps(review_data), encoding="utf-8")
            elif (script == "task_execution_guard.py" and args and args[0] == "checkpoint"
                  and value(args, "--event") == "STAGE_COMPLETED"
                  and value(args, "--stage") == "03_PHYSIQUE_SIDE"):
                assert derivative_receipt is not None
                args.extend(("--local-derivative-receipt", str(derivative_receipt)))
            return original_cli(script, *args, succeeds=succeeds)

        fixture._cli = intercepted
        fixture._run_multistage_character_base(child=False, anatomy="MALE_ANATOMY")
        self.assertIsNotNone(derivative_receipt)
        final_guard = fixture._guard_path("multistage-continuation")
        state = json.loads(final_guard.read_text(encoding="utf-8"))
        side_attempt = next(row for row in state["attempts"] if row.get("stage") == "03_PHYSIQUE_SIDE")
        self.assertEqual(side_attempt["provider_artifact"]["sha256"], provider_hash)
        self.assertEqual(Path(side_attempt["provider_artifact"]["path"]).name, "PHYSIQUE_SIDE_DIMENSIONED_PENDING.png")
        self.assertEqual(side_attempt["derived_artifact"]["derived_artifact"]["sha256"], _sha(Path(side_attempt["derived_artifact"]["derived_artifact"]["path"])))
        self.assertEqual(side_attempt["status"], "DELIVERED")
        self.assertEqual([row["status"] for row in state["required_stages"]], ["COMPLETED"] * 5)


if __name__ == "__main__":
    unittest.main()
