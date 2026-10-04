from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import task_execution_guard as guard


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "EXECUTION_GUARD.json"
        guard.create_guard(self.path, request_id="recovery-test", goal="Complete requested work",
                           deliverable="Result", task_kind="GENERAL", allowed_scope=[self.tmp.name])

    def step(self, event, **kwargs):
        return guard.checkpoint(self.path, event=event, summary="Missing receipt or completed repair", **kwargs)

    def test_internal_blocker_does_not_require_user_even_with_old_flags(self):
        state = self.step("BLOCKER", hard_blocker=True, safe_routes_exhausted=True)
        self.assertEqual(state["status"], "ACTIVE")
        self.assertEqual(state["next_required_action"], "RECOVER_AND_CONTINUE")
        self.assertEqual(state["events"][-1]["event"], "RECOVERY_REQUIRED")
        self.assertNotIn("blocker", state)

    def test_repair_resumes_without_user_permission_or_budget_reset(self):
        before = guard.load_guard(self.path)
        self.step("RECOVERY_REQUIRED")
        with self.assertRaises(guard.GuardError):
            self.step("RECOVERY_RESOLVED")
        state = self.step("RECOVERY_RESOLVED", evidence=["Source rebound to current file hash"])
        self.assertEqual(state["next_required_action"], "PREFLIGHT_OR_EXECUTION")
        self.assertEqual(state["cycle_started_at"], before["cycle_started_at"])
        self.assertNotIn("recovery", state)
        self.assertTrue(state["last_recovery"]["evidence"])

    def test_old_internal_blocked_record_can_be_recovered(self):
        state = guard.load_guard(self.path)
        state.update(status="BLOCKED", phase="BLOCKED", blocker="Missing QA receipt")
        guard.atomic_write_json(self.path, state)
        state = self.step("RECOVERY_REQUIRED")
        self.assertEqual(state["status"], "ACTIVE")

    def test_external_boundary_requires_evidence_and_stays_distinct(self):
        with self.assertRaises(guard.GuardError):
            self.step("BLOCKER", blocker_kind="EXTERNAL", hard_blocker=True, safe_routes_exhausted=True)
        state = self.step("BLOCKER", blocker_kind="EXTERNAL", hard_blocker=True,
                          safe_routes_exhausted=True, evidence=["Provider service unavailable receipt"])
        self.assertEqual(state["status"], "BLOCKED")
        with self.assertRaises(guard.GuardError):
            self.step("RECOVERY_REQUIRED")

    def test_recovery_cannot_resume_user_stop_or_complete_unfinished_repair(self):
        self.step("RECOVERY_REQUIRED")
        with self.assertRaises(guard.GuardError):
            self.step("COMPLETE")
        self.step("STOP")
        with self.assertRaises(guard.GuardError):
            self.step("RECOVERY_REQUIRED")

    def test_active_provider_is_retained_and_reconciled_before_retry(self):
        state = self.step("EXECUTION_STARTED")
        attempt = state["active_attempt"]["attempt_id"]
        state = self.step("RECOVERY_REQUIRED")
        self.assertEqual(state["active_attempt"]["attempt_id"], attempt)
        self.assertEqual(state["phase"], "EXECUTION")
        self.assertEqual(state["next_required_action"], "CHECK_EXECUTION_OR_RECONCILE_ATTEMPT")
        with self.assertRaises(guard.GuardError):
            self.step("RECOVERY_RESOLVED", evidence=["Cannot yet resolve"])
        state = self.step("VISIBLE_RESULT", attempt_id=attempt)
        state = self.step("RECOVERY_RESOLVED", evidence=["Recovered the same provider result"])
        self.assertEqual(len(state["attempts"]), 1)
        self.assertEqual(state["phase"], "RESULT_AVAILABLE")
        self.assertEqual(state["attempts"][0]["status"], "RESULT_AVAILABLE")

    def test_unknown_attempt_is_never_cleared(self):
        state = guard.load_guard(self.path)
        state["attempts"] = [{"attempt_id": "unknown", "status": "UNKNOWN"}]
        guard.atomic_write_json(self.path, state)
        state = self.step("RECOVERY_REQUIRED")
        self.assertEqual(state["attempts"][0]["status"], "UNKNOWN")
        with self.assertRaises(guard.GuardError):
            self.step("RECOVERY_RESOLVED", evidence=["No actual reconciliation"])


    def test_artifact_survives_registration_failure_without_qa_pass(self):
        state = self.step("EXECUTION_STARTED")
        attempt_id = state["active_attempt"]["attempt_id"]
        image = self.path.parent / "provider.png"
        image.write_bytes(b"provider-original")
        self.step("ARTIFACT_RECEIVED", attempt_id=attempt_id, evidence=[str(image)])
        state = self.step("RECOVERY_REQUIRED")
        self.assertEqual(state["next_required_action"], "REVIEW_AND_REGISTER_EXISTING_ARTIFACT")
        self.assertEqual(state["active_attempt"]["provider_artifact"]["result_status"], "NOT_CHECKED")
        self.assertEqual(state["attempts"][0]["provider_artifact"]["sha256"], guard.file_sha256(image))
        with self.assertRaises(guard.GuardError):
            self.step("EXECUTION_STARTED")
        image.write_bytes(b"substituted")
        with self.assertRaises(guard.GuardError):
            self.step("ARTIFACT_RECEIVED", attempt_id=attempt_id, evidence=[str(image)])


if __name__ == "__main__":
    unittest.main()
