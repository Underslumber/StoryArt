"""Public CLI trajectories: synthetic fixtures, no image-provider calls.

These test execution continuity, not visual quality of generated art.
"""
import json
import unittest

try:
    from tests import test_guard_cli_lifecycle as fixtures
except ImportError:
    import test_guard_cli_lifecycle as fixtures


class CompletionCliTests(unittest.TestCase):
    setUp = fixtures.GuardCliLifecycleTests.setUp
    run_tool = fixtures.GuardCliLifecycleTests.run_tool
    checkpoint = fixtures.GuardCliLifecycleTests.checkpoint
    read_state = fixtures.GuardCliLifecycleTests.read_state
    start_attempt = fixtures.GuardCliLifecycleTests.start_attempt

    def status(self):
        result = self.run_tool("task_execution_guard.py", "status", "--state", str(self.state))
        return json.loads(result.stdout)

    def test_four_rejections_remain_actionable_and_fifth_can_finish(self):
        diagnoses = 0
        for index, layer in enumerate(("IDENTITY", "IDENTITY+PROMPT", "", "WARDROBE")):
            attempt = self.start_attempt()
            arguments = ["--attempt-id", attempt]
            if layer:
                arguments += ["--qa-layer", layer]
            self.checkpoint("ATTEMPT_REJECTED", *arguments)
            state = self.status()
            self.assertEqual(state["status"], "ACTIVE")
            self.assertFalse(state["continuation"]["can_complete"])
            self.assertNotEqual(state["continuation"]["action"], "WAIT_FOR_USER")
            self.checkpoint("COMPLETE", succeeds=False)
            if index == 1:
                self.assertEqual(state["next_required_action"], "ESCALATION_ORCHESTRATOR_REQUIRED")
            if state["next_required_action"] == "ESCALATION_ORCHESTRATOR_REQUIRED":
                diagnoses += 1
                pending = next(row for row in reversed(state["escalation_incidents"])
                               if row["status"] in {"REQUIRED", "CONSUMED"})
                self.checkpoint(
                    "ESCALATION_ORCHESTRATOR_RECORDED", "--stage", pending["stage"],
                    "--qa-layer", pending.get("qa_layer") or "UNSPECIFIED",
                    "--evidence", f"Synthetic diagnosis {index}: simplify layout and revise failed requirement from original sources",
                )
        self.assertGreaterEqual(diagnoses, 2, "Further failed strategies must be diagnosable again without a terminal ceiling")
        attempt = self.start_attempt()
        self.checkpoint("VISIBLE_RESULT", "--attempt-id", attempt,
                        "--result-status", "TEST", "--evidence", str(self.output))
        self.assertFalse(self.status()["continuation"]["can_complete"])
        self.checkpoint("RESULT_DELIVERED", "--attempt-id", attempt,
                        "--delivery-evidence", "Synthetic fixture delivery receipt")
        self.checkpoint("COMPLETE")
        final = self.status()
        self.assertEqual(final["status"], "COMPLETE")
        self.assertEqual(len(final["attempts"]), 5)
        self.assertTrue(final["continuation"]["can_complete"])

    def test_internal_failure_is_not_user_permission_request(self):
        self.checkpoint("BLOCKER", "--hard-blocker", "--safe-routes-exhausted")
        state = self.status()
        self.assertEqual(state["status"], "ACTIVE")
        self.assertFalse(state["continuation"]["can_complete"])
        self.assertNotEqual(state["continuation"]["action"], "WAIT_FOR_USER")
        self.checkpoint("RECOVERY_RESOLVED", "--evidence", "Synthetic input repair complete")
        attempt = self.start_attempt()
        self.assertTrue(attempt)

    def test_unknown_operation_requires_reconciliation_not_duplicate(self):
        attempt = self.start_attempt()
        self.checkpoint("ATTEMPT_UNKNOWN", "--attempt-id", attempt,
                        "--reconciliation-evidence", "Synthetic timeout; provider outcome unknown")
        state = self.status()
        self.assertFalse(state["continuation"]["can_complete"])
        self.assertEqual(state["continuation"]["action"], "RECONCILE_ATTEMPT")
        self.checkpoint("READY_FOR_EXECUTION", "--reference-plan", str(self.plan), succeeds=False)


if __name__ == "__main__":
    unittest.main()
