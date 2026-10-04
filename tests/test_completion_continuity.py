from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import generation_risk_assessor as risk
from tools import task_execution_guard as guard
from tools import storyart_orchestrator as orchestrator


BASE = datetime(2026, 9, 29, tzinfo=timezone.utc)


class CompletionContinuityTests(unittest.TestCase):
    def _call(self, folder: Path, prompt: str) -> dict[str, object]:
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        report_path = folder / f"risk-{prompt_hash[:12]}.json"
        risk.command_assess(SimpleNamespace(
            lexicon=risk.DEFAULT_LEXICON,
            text=prompt,
            prompt_file="",
            revised_text="",
            revised_prompt_file="",
            reference=[],
            output=str(report_path),
            overwrite=False,
            json=False,
        ))
        return {
            "request_id": "continuity-test",
            "prompt": {"text": prompt, "text_sha256": prompt_hash},
            "user_selections": {
                "style": {"choice": "GENERATOR_DEFAULT", "user_quote": "Use the default style."},
                "reference_policy": {"choice": "NO_REFERENCES", "user_quote": "Use no references."},
                "character": {"choice": "NONE", "user_quote": "No named character."},
            },
            "risk_assessment": json.loads(report_path.read_text(encoding="utf-8")),
        }

    def _start(self, state_path: Path, folder: Path, prompt: str, now: datetime = BASE) -> str:
        call = self._call(folder, prompt)
        guard.checkpoint(
            state_path, event="READY_FOR_EXECUTION", summary="Exact revised call is ready.",
            execution_call=call, now=now,
        )
        guard.checkpoint(state_path, event="CALL_VALIDATED", summary="Validated the exact call.", now=now)
        state = guard.checkpoint(
            state_path, event="EXECUTION_STARTED", summary="Start the requested image call.",
            execution_call=call, now=now,
        )
        return str(state["active_attempt"]["attempt_id"])

    def _reject(self, state_path: Path, folder: Path, prompt: str, qa_layer: str, *, include_stage: bool = True) -> dict[str, object]:
        attempt_id = self._start(state_path, folder, prompt)
        artifact = folder / f"rejected-{len(guard.load_guard(state_path)['attempts'])}.png"
        artifact.write_bytes(b"rejected image output")
        guard.checkpoint(
            state_path, event="VISIBLE_RESULT", attempt_id=attempt_id,
            evidence=[str(artifact)], result_status="REJECTED",
            summary="A real image output is available for review.", now=BASE,
        )
        kwargs: dict[str, object] = {
            "event": "ATTEMPT_REJECTED", "attempt_id": attempt_id,
            "qa_layer": qa_layer, "summary": "The image failed visual QA; revise the strategy.", "now": BASE,
        }
        if include_stage:
            kwargs["stage"] = "SINGLE_PASS"
        return guard.checkpoint(state_path, **kwargs)

    def _new_image_guard(self, folder: Path, **kwargs) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "EXECUTION_GUARD.json"
        guard.create_guard(
            path,
            request_id="continuity-test",
            goal="Create the requested image.",
            deliverable="One reviewed and delivered image.",
            task_kind="IMAGE_GENERATION_NATIVE_DEFAULT",
            now=BASE,
            **kwargs,
        )
        return path

    def test_changed_or_missing_qa_labels_escalate_by_stage_and_complete_after_many_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            state_path = self._new_image_guard(folder)

            first = self._reject(state_path, folder, "Initial requested scene.", "IDENTITY+PROMPT_ADHERENCE", include_stage=False)
            self.assertEqual(first["next_required_action"], "NEXT_SAFE_EXECUTION")
            guard.checkpoint(
                state_path, event="USER_CORRECTION", stage="SINGLE_PASS",
                qa_layer="PROMPT_ADHERENCE", summary="Revise the failed scene while preserving the request.", now=BASE,
            )

            second = self._reject(state_path, folder, "First revised scene.", "IDENTITY_GEOMETRY")
            self.assertEqual(second["next_required_action"], "ESCALATION_ORCHESTRATOR_REQUIRED")
            self.assertEqual(second["escalation_incidents"][-1]["stage"], "SINGLE_PASS")
            evidence = folder / "work-order.md"
            evidence.write_text("Evidence-based revised strategy using original approved sources.", encoding="utf-8")
            guard.checkpoint(
                state_path, event="ESCALATION_ORCHESTRATOR_RECORDED", stage="SINGLE_PASS",
                qa_layer="IDENTITY_GEOMETRY", evidence=[str(evidence)],
                summary="Record the bounded strategy work order.", now=BASE,
            )

            guard.checkpoint(
                state_path, event="USER_CORRECTION", stage="SINGLE_PASS", qa_layer="COMPOSITION",
                summary="Apply the revised strategy without changing the requested scene.", now=BASE,
            )
            third = self._reject(state_path, folder, "Second revised scene.", "COMPOSITION")
            self.assertEqual(third["next_required_action"], "ESCALATION_ORCHESTRATOR_REQUIRED")
            self.assertEqual(len(third["escalation_incidents"]), 2)
            evidence2 = folder / "work-order-2.md"
            evidence2.write_text("Second bounded recovery strategy.", encoding="utf-8")
            guard.checkpoint(
                state_path, event="ESCALATION_ORCHESTRATOR_RECORDED", stage="SINGLE_PASS",
                qa_layer="COMPOSITION", evidence=[str(evidence2)],
                summary="Record the next bounded strategy work order.", now=BASE,
            )
            guard.checkpoint(
                state_path, event="USER_CORRECTION", stage="SINGLE_PASS",
                summary="Apply the new strategy.", now=BASE,
            )

            final_attempt = self._start(state_path, folder, "Accepted revised scene.")
            delivered = folder / "accepted.png"
            delivered.write_bytes(b"accepted image output")
            guard.checkpoint(
                state_path, event="VISIBLE_RESULT", attempt_id=final_attempt,
                evidence=[str(delivered)], result_status="TEST",
                summary="The revised image passed visual review.", now=BASE,
            )
            guard.checkpoint(
                state_path, event="RESULT_DELIVERED", attempt_id=final_attempt,
                delivery_evidence=["user-visible delivery receipt"],
                summary="Deliver the accepted image.", now=BASE,
            )
            complete = guard.checkpoint(state_path, event="COMPLETE", summary="Requested image delivered.", now=BASE)
            self.assertEqual(complete["status"], "COMPLETE")
            self.assertEqual(len(complete["attempts"]), 4)
            self.assertEqual(guard.completion_continuation(complete)["action"], "COMPLETE_NOW")

    def test_watchdog_preserves_ready_execution_result_and_explicit_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            state_path = self._new_image_guard(folder, max_minutes_without_execution=1, max_execution_minutes=1)
            call = self._call(folder, "Exact request before provider start.")
            ready = guard.checkpoint(
                state_path, event="READY_FOR_EXECUTION", summary="Exact call ready.", execution_call=call, now=BASE,
            )
            watched_ready = guard.guard_status(state_path, now=BASE + timedelta(minutes=2))
            self.assertEqual(watched_ready["status"], "READY")
            self.assertEqual(watched_ready["phase"], "READY_FOR_EXECUTION")
            self.assertEqual(watched_ready["next_required_action"], ready["next_required_action"])
            self.assertEqual(watched_ready["continuation"]["action"], "CONTINUE_GENERATION")

            guard.checkpoint(state_path, event="CALL_VALIDATED", summary="Validated exact call.", now=BASE)
            started = guard.checkpoint(
                state_path, event="EXECUTION_STARTED", summary="Start requested image.", execution_call=call, now=BASE,
            )
            watched_execution = guard.guard_status(state_path, now=BASE + timedelta(minutes=2))
            self.assertEqual(watched_execution["phase"], "EXECUTION")
            self.assertEqual(watched_execution["active_attempt"]["attempt_id"], started["active_attempt"]["attempt_id"])
            self.assertEqual(watched_execution["continuation"]["action"], "CHECK_EXECUTION")

            stopped = guard.checkpoint(state_path, event="STOP", summary="The user explicitly stopped the task.", now=BASE)
            after_stop = guard.guard_status(state_path, now=BASE + timedelta(hours=1))
            self.assertEqual(after_stop["status"], stopped["status"])
            self.assertEqual(after_stop["continuation"]["action"], "USER_STOPPED")

    def test_completion_rechecks_artifact_bytes_and_completion_is_not_status_only(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            state_path = self._new_image_guard(folder)
            attempt_id = self._start(state_path, folder, "Delivered image task.")
            artifact = folder / "delivered.png"
            artifact.write_bytes(b"reviewed bytes")
            guard.checkpoint(
                state_path, event="VISIBLE_RESULT", attempt_id=attempt_id,
                evidence=[str(artifact)], result_status="TEST", summary="QA passed.", now=BASE,
            )
            guard.checkpoint(
                state_path, event="RESULT_DELIVERED", attempt_id=attempt_id,
                delivery_evidence=["delivery receipt"], summary="Delivered to user.", now=BASE,
            )
            state = guard.load_guard(state_path)
            self.assertTrue(guard.completion_continuation(state)["can_complete"])
            artifact.write_bytes(b"changed after review")
            self.assertFalse(guard.completion_continuation(guard.load_guard(state_path))["can_complete"])
            with self.assertRaisesRegex(guard.GuardError, "changed after review"):
                guard.checkpoint(state_path, event="COMPLETE", summary="Must not fake completion.", now=BASE)

    def test_stale_test_result_after_correction_is_not_advised_for_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            state_path = self._new_image_guard(folder)
            attempt_id = self._start(state_path, folder, "Requested scene before correction.")
            artifact = folder / "old-test.png"
            artifact.write_bytes(b"old TEST output")
            guard.checkpoint(
                state_path, event="VISIBLE_RESULT", attempt_id=attempt_id, evidence=[str(artifact)],
                result_status="TEST", summary="Old TEST result is available.", now=BASE,
            )
            corrected = guard.checkpoint(
                state_path, event="USER_CORRECTION", stage="SINGLE_PASS",
                summary="Revise the current requested scene.", now=BASE,
            )
            self.assertNotEqual(guard.completion_continuation(corrected)["action"], "DELIVER_RESULT")
            self.assertFalse(guard.completion_continuation(corrected)["can_complete"])

    def test_focus_tracks_general_turn_without_replacing_active_image_folder(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"CODEX_THREAD_ID": "focus-test-thread"}):
            registry = Path(temp) / "chat_requests"
            with patch.object(guard, "_chat_request_root", return_value=registry):
                image_folder = Path(temp) / "STYLE_GENERATIONS" / "00_PENDING" / "image-request"
                image_guard = self._new_image_guard(image_folder)
                image_binding = guard._read_binding(guard._chat_binding_path("focus-test-thread"))
                general_path = Path(temp) / "GENERAL" / "EXECUTION_GUARD.json"
                general_path.parent.mkdir(parents=True)
                guard.create_guard(
                    general_path, request_id="general", goal="Repair internal tools.",
                    deliverable="Runtime repair", task_kind="GENERAL", now=BASE,
                )
                focus = guard.execution_focus_binding("focus-test-thread")
                self.assertEqual(focus["task_kind"], "GENERAL")
                self.assertEqual(guard._read_binding(guard._chat_binding_path("focus-test-thread")), image_binding)

                guard.checkpoint(
                    image_guard, event="WAITING_FOR_USER", summary="Await explicit image-task resume.", now=BASE,
                )
                guard.checkpoint(
                    image_guard, event="USER_RESUMED", summary="The user resumed the image request.", now=BASE,
                )
                focus = guard.execution_focus_binding("focus-test-thread")
                self.assertEqual(focus["task_kind"], "IMAGE_GENERATION_NATIVE_DEFAULT")
                self.assertEqual(Path(focus["guard_path"]), image_guard.resolve())

    def test_interrupted_escalation_dispatch_reuses_reserved_handoff_id(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            root = Path(temp)
            request = root / "STYLE_GENERATIONS" / "00_PENDING" / "request-1"
            request.mkdir(parents=True)
            guard_path = request / "EXECUTION_GUARD.json"
            guard_path.write_text(json.dumps({
                "schema_version": 1,
                "request_id": "request-1",
                "goal_lock": "Create the requested image.",
                "primary_deliverable": "Image",
                "status": "ACTIVE",
                "phase": "ESCALATION_REQUIRED",
                "next_required_action": "ESCALATION_ORCHESTRATOR_REQUIRED",
                "escalation_incidents": [{
                    "key": "incident", "stage": "FRAME", "qa_layer": "IDENTITY",
                    "status": "CONSUMED", "handoff_id": "H001",
                }],
            }), encoding="utf-8")
            state_path = request / "ORCHESTRATION_STATE.json"
            state_path.write_text(json.dumps({
                "schema_version": orchestrator.STATE_SCHEMA_VERSION,
                "request_root": str(request), "guard_path": str(guard_path), "handoffs": [],
            }), encoding="utf-8")
            handoff_dir = request / "ORCHESTRATION_HANDOFFS"
            handoff_dir.mkdir()
            (handoff_dir / "H001_escalation_orchestrator.json").write_text(json.dumps({
                "handoff_id": "H001", "role": "ESCALATION_ORCHESTRATOR", "stage": "FRAME", "status": "PENDING",
            }), encoding="utf-8")

            handoff = orchestrator.dispatch_handoff(
                state_path, "ESCALATION_ORCHESTRATOR", "Resume same recovery analysis.",
                [str(guard_path)], [], [], "FRAME", "", "IDENTITY",
            )
            self.assertEqual(handoff["handoff_id"], "H001")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual([item["handoff_id"] for item in state["handoffs"]], ["H001"])


if __name__ == "__main__":
    unittest.main()
