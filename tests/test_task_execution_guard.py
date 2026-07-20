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

    def test_action_budget_stops_more_preflight(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, max_preflight_actions=2)
            guard.checkpoint(path, event="PREFLIGHT", summary="Inspect style.", now=BASE_TIME)
            with self.assertRaises(guard.GuardActionRequired):
                guard.checkpoint(path, event="PREFLIGHT", summary="Inspect another file.", now=BASE_TIME)
            state = guard.load_guard(path)
            self.assertEqual(state["status"], "ACTION_REQUIRED")
            self.assertEqual(state["next_required_action"], "EXECUTION_STARTED_OR_BLOCKER")

    def test_time_budget_stops_silent_preparation(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.make_guard(folder, max_minutes_without_execution=10)
            state = guard.guard_status(path, now=BASE_TIME + timedelta(minutes=11))
            self.assertEqual(state["status"], "ACTION_REQUIRED")

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


if __name__ == "__main__":
    unittest.main()
