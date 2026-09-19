from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import task_execution_guard as guard


BASE_TIME = datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc)


class TaskExecutionGuardTests(unittest.TestCase):
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
        return path, guard.create_guard(path, **values)

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
            guard.checkpoint(path, event="PREFLIGHT", summary="Inspect style.", now=BASE_TIME)
            with self.assertRaises(guard.GuardActionRequired):
                guard.checkpoint(path, event="PREFLIGHT", summary="Inspect another file.", now=BASE_TIME)
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

            ready = guard.checkpoint(
                path,
                event="READY_FOR_EXECUTION",
                summary="Reference plan is ready after watchdog recovery.",
                now=BASE_TIME + timedelta(minutes=11),
            )
            self.assertEqual(ready["status"], "ACTIVE")
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
            state = guard.checkpoint(
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
            guard.checkpoint(
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
            result = guard.checkpoint(
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
            guard.checkpoint(path, event="WAITING_FOR_USER", summary="Need profile choice.", now=BASE_TIME + timedelta(minutes=2))
            guard.checkpoint(path, event="USER_RESUMED", summary="User selected 90 percent.", now=BASE_TIME + timedelta(hours=2))
            state = guard.guard_status(path, now=BASE_TIME + timedelta(hours=2, minutes=5))
            self.assertEqual(state["status"], "ACTIVE")

    def test_ready_for_execution_forbids_more_preflight(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            guard.checkpoint(path, event="READY_FOR_EXECUTION", summary="Plan is ready.", now=BASE_TIME)
            with self.assertRaises(guard.GuardActionRequired):
                guard.checkpoint(path, event="PREFLIGHT", summary="Rewrite workflow.", now=BASE_TIME)

    def test_ready_allows_one_exact_call_validation_then_requires_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            guard.checkpoint(path, event="READY_FOR_EXECUTION", summary="Plan is ready.", now=BASE_TIME)
            state = guard.checkpoint(
                path,
                event="CALL_VALIDATED",
                summary="Exact prompt and physical attachments passed the risk check.",
                now=BASE_TIME,
            )
            self.assertEqual(state["next_required_action"], "EXECUTION_STARTED_OR_BLOCKER")
            with self.assertRaises(guard.GuardError):
                guard.checkpoint(
                    path,
                    event="CALL_VALIDATED",
                    summary="Attempt another validation pass.",
                    now=BASE_TIME,
                )

    def test_scope_change_requires_explicit_user_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder)
            with self.assertRaises(guard.GuardError):
                guard.checkpoint(path, event="SCOPE_CHANGE", summary="Edit the manager tests.", now=BASE_TIME)
            state = guard.checkpoint(
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
            guard.checkpoint(
                path,
                event="BLOCKER",
                summary="The manager policy blocks the requested reference role.",
                hard_blocker=True,
                safe_routes_exhausted=True,
                now=BASE_TIME,
            )
            state = guard.checkpoint(
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
            guard.checkpoint(path, event="READY_FOR_EXECUTION", summary="Plan is ready.", now=BASE_TIME)
            guard.checkpoint(path, event="EXECUTION_STARTED", summary="Image generator called.", now=BASE_TIME)
            image = Path(folder) / "result.png"
            image.write_bytes(b"png-placeholder")
            state = guard.checkpoint(
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
                guard.checkpoint(
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
                guard.checkpoint(
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
                guard.checkpoint(
                    path,
                    event="EXECUTION_STARTED",
                    stage="PHYSIQUE_SIDE",
                    swimwear_rung="EXTREME_MICRO",
                    summary="Generate the mannequin explicitly requested by the user.",
                    output_contract="USER_REQUESTED_EXTRA",
                    user_approved_extra_generation=True,
                    now=BASE_TIME,
                )
            state = guard.checkpoint(
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
            guard.checkpoint(path, event="EXECUTION_STARTED", summary="Generate face.", now=BASE_TIME)
            image = Path(folder) / "face.png"
            image.write_bytes(b"png-placeholder")
            guard.checkpoint(
                path,
                event="VISIBLE_RESULT",
                summary="Face produced.",
                evidence=[str(image)],
                now=BASE_TIME,
            )
            guard.checkpoint(
                path,
                event="STAGE_COMPLETED",
                stage="FACE",
                summary="Face passed QA.",
                evidence=[str(image)],
                now=BASE_TIME,
            )
            with self.assertRaises(guard.GuardActionRequired) as raised:
                guard.checkpoint(path, event="COMPLETE", summary="Stop after face.", now=BASE_TIME)
            self.assertIn("BACK, ASSEMBLY", str(raised.exception))
            self.assertEqual(guard.load_guard(path)["status"], "ACTIVE")

    def test_all_required_stages_allow_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["BACK", "ASSEMBLY"])
            outputs = []
            for stage in ("BACK", "ASSEMBLY"):
                guard.checkpoint(path, event="EXECUTION_STARTED", summary=f"Generate {stage}.", now=BASE_TIME)
                image = Path(folder) / f"{stage.lower()}.png"
                image.write_bytes(b"png-placeholder")
                outputs.append(image)
                guard.checkpoint(
                    path,
                    event="VISIBLE_RESULT",
                    summary=f"{stage} produced.",
                    evidence=[str(image)],
                    now=BASE_TIME,
                )
                guard.checkpoint(
                    path,
                    event="STAGE_COMPLETED",
                    stage=stage,
                    summary=f"{stage} passed QA.",
                    evidence=[str(image)],
                    now=BASE_TIME,
                )
            state = guard.checkpoint(path, event="COMPLETE", summary="Full kit completed.", now=BASE_TIME)
            self.assertEqual(state["status"], "COMPLETE")

    def test_user_correction_preserves_remaining_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["SIDE", "BACK"])
            image = Path(folder) / "side.png"
            image.write_bytes(b"png-placeholder")
            guard.checkpoint(
                path,
                event="STAGE_COMPLETED",
                stage="SIDE",
                summary="User-selected side view retained.",
                evidence=[str(image)],
                now=BASE_TIME,
            )
            state = guard.checkpoint(
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
                guard.checkpoint(
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
            state = guard.checkpoint(
                path,
                event="USER_CORRECTION",
                correction_impact="PRESERVE",
                summary="Fix anatomy and fingers without changing the camera.",
                now=BASE_TIME,
            )
            self.assertEqual(state["events"][-1]["correction_impact"], "PRESERVE")
            with self.assertRaisesRegex(guard.GuardError, "locked task invariants"):
                guard.checkpoint(
                    path,
                    event="EXECUTION_STARTED",
                    invariant_assertions=["camera_view=FRONT", "orientation=LANDSCAPE"],
                    summary="Incorrectly switch the camera.",
                    now=BASE_TIME,
                )
            started = guard.checkpoint(
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
                guard.checkpoint(
                    path,
                    event="USER_CORRECTION",
                    correction_impact="CHANGE",
                    invariant_changes=["camera_view=FRONT"],
                    user_approved_invariant_change=True,
                    summary="Switch the camera.",
                    now=BASE_TIME,
                )
            changed = guard.checkpoint(
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
                guard.checkpoint(
                    path,
                    event="EXECUTION_STARTED",
                    summary=f"Generate {stage}.",
                    now=BASE_TIME,
                )
                guard.checkpoint(
                    path,
                    event="VISIBLE_RESULT",
                    summary=f"{stage} produced.",
                    evidence=[str(image)],
                    now=BASE_TIME,
                )
                guard.checkpoint(
                    path,
                    event="STAGE_COMPLETED",
                    stage=stage,
                    summary=f"{stage} passed QA.",
                    evidence=[str(image)],
                    now=BASE_TIME,
                )
            guard.checkpoint(path, event="COMPLETE", summary="Initial kit complete.", now=BASE_TIME)

            corrected = guard.checkpoint(
                path,
                event="USER_CORRECTION",
                summary="User found drift in the final assembly.",
                now=BASE_TIME,
            )
            self.assertEqual(corrected["status"], "ACTIVE")
            self.assertEqual(corrected["phase"], "CORRECTION")

            reopened = guard.checkpoint(
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
            guard.checkpoint(path, event="EXECUTION_STARTED", summary="Generate FRONT.", now=BASE_TIME)
            state = guard.checkpoint(
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
            guard.checkpoint(path, event="EXECUTION_STARTED", stage="FRONT", summary="Generate FRONT.", now=BASE_TIME)
            guard.checkpoint(path, event="ATTEMPT_REJECTED", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="First attempt failed face geometry.", now=BASE_TIME)
            guard.checkpoint(path, event="USER_CORRECTION", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="Correct face geometry only.", now=BASE_TIME)
            guard.checkpoint(path, event="EXECUTION_STARTED", stage="FRONT", summary="Generate corrected FRONT.", now=BASE_TIME)
            guard.checkpoint(path, event="ATTEMPT_REJECTED", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="Corrected attempt still failed face geometry.", now=BASE_TIME)
            state = guard.load_guard(path)
            self.assertEqual(state["next_required_action"], "ESCALATION_ORCHESTRATOR_REQUIRED")
            with self.assertRaises(guard.GuardActionRequired):
                guard.checkpoint(path, event="EXECUTION_STARTED", stage="FRONT", summary="Incorrect extra retry.", now=BASE_TIME)
            evidence = Path(folder) / "work-order.md"
            evidence.write_text("Terra work order", encoding="utf-8")
            state = guard.checkpoint(
                path, event="ESCALATION_ORCHESTRATOR_RECORDED", stage="FRONT", qa_layer="FACE_GEOMETRY",
                evidence=[str(evidence)], summary="Astra returned one bounded Terra work order.", now=BASE_TIME,
            )
            self.assertEqual(state["next_required_action"], "NEXT_SAFE_EXECUTION")

    def test_unbound_or_unrelated_correction_does_not_trigger_escalation(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["FRONT"])
            guard.checkpoint(path, event="USER_CORRECTION", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="Correction before any failure.", now=BASE_TIME)
            guard.checkpoint(path, event="EXECUTION_STARTED", stage="FRONT", summary="Generate FRONT.", now=BASE_TIME)
            guard.checkpoint(path, event="ATTEMPT_REJECTED", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="First face failure.", now=BASE_TIME)
            guard.checkpoint(path, event="USER_CORRECTION", stage="FRONT", qa_layer="BODY_PROPORTIONS", summary="Unrelated correction.", now=BASE_TIME)
            guard.checkpoint(path, event="EXECUTION_STARTED", stage="FRONT", summary="Retry FRONT.", now=BASE_TIME)
            state = guard.checkpoint(path, event="ATTEMPT_REJECTED", stage="FRONT", qa_layer="FACE_GEOMETRY", summary="Second face failure.", now=BASE_TIME)
            self.assertEqual(state["next_required_action"], "NEXT_SAFE_EXECUTION")

    def test_physique_swimwear_ladder_starts_at_extreme_micro(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, required_stages=["PHYSIQUE_FRONT"])
            with self.assertRaises(guard.GuardError) as raised:
                guard.checkpoint(
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
            guard.checkpoint(
                path,
                event="EXECUTION_STARTED",
                stage="PHYSIQUE_FRONT",
                swimwear_rung="EXTREME_MICRO",
                summary="Generate the mandatory default extreme-micro rung.",
                now=BASE_TIME,
            )
            guard.checkpoint(
                path,
                event="ATTEMPT_REJECTED",
                stage="PHYSIQUE_FRONT",
                swimwear_rung="EXTREME_MICRO",
                rung_routes_exhausted=True,
                summary="The exact extreme-micro call was rejected.",
                now=BASE_TIME,
            )
            state = guard.checkpoint(
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
            guard.checkpoint(
                path,
                event="EXECUTION_STARTED",
                stage="PHYSIQUE_FRONT",
                swimwear_rung="EXTREME_MICRO",
                summary="Try one exact route for the fixed extreme-micro target.",
                now=BASE_TIME,
            )
            guard.checkpoint(
                path,
                event="ATTEMPT_REJECTED",
                stage="PHYSIQUE_FRONT",
                swimwear_rung="EXTREME_MICRO",
                summary="One prompt and BODY combination failed; other routes remain.",
                now=BASE_TIME,
            )
            with self.assertRaises(guard.GuardError) as raised:
                guard.checkpoint(
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
            guard.checkpoint(
                path,
                event="EXECUTION_STARTED",
                stage="PHYSIQUE_FRONT",
                swimwear_rung="EXTREME_MICRO",
                summary="Generate the mandatory default rung.",
                now=BASE_TIME,
            )
            image = Path(folder) / "front.png"
            image.write_bytes(b"png-placeholder")
            guard.checkpoint(
                path,
                event="VISIBLE_RESULT",
                summary="A visible front result was produced.",
                evidence=[str(image)],
                now=BASE_TIME,
            )
            with self.assertRaises(guard.GuardError) as raised:
                guard.checkpoint(
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
                guard.checkpoint(
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
                guard.checkpoint(
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
            state = guard.checkpoint(
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
            guard.checkpoint(
                path,
                event="BLOCKER",
                summary="All approved safe routes were believed to be exhausted.",
                hard_blocker=True,
                safe_routes_exhausted=True,
                now=BASE_TIME,
            )
            state = guard.checkpoint(
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
            guard.checkpoint(path, event="EXECUTION_STARTED", summary="Generate FRONT.", now=BASE_TIME)
            with self.assertRaises(guard.GuardActionRequired):
                guard.checkpoint(
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
            guard.checkpoint(
                path,
                event="STAGE_COMPLETED",
                stage="SIDE",
                summary="Side passed QA.",
                evidence=[str(image)],
                now=BASE_TIME,
            )
            state = guard.checkpoint(
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
