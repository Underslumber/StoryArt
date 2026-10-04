"""Hook decisions, ownership and user interruption; no actual Codex turns."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import storyart_completion_hook as hook


class CompletionHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.session = "test-chat"
        self.path = self.root / "STYLE_GENERATIONS" / "00_PENDING" / "request" / "EXECUTION_GUARD.json"
        self.registry = self.root / ".agent" / "chat_requests"
        self.key = hashlib.sha256(self.session.encode()).hexdigest()
        self.focus = self.registry / "focus" / f"{self.key}.json"
        self.write(self.focus, {"thread_id": self.session, "task_kind": "IMAGE_GENERATION", "guard_path": str(self.path)})
        self.write(self.registry / "chats" / f"{self.key}.json",
                   {"thread_id": self.session, "active_folder": str(self.path.parent)})
        folder_key = hashlib.sha256(str(self.path.parent).encode()).hexdigest()
        self.write(self.registry / "folders" / f"{folder_key}.json",
                   {"thread_id": self.session, "active_folder": str(self.path.parent)})
        self.state = {"schema_version": 1, "request_id": "request", "owner_chat_id": self.session,
                      "task_kind": "IMAGE_GENERATION", "status": "ACTIVE", "phase": "ATTEMPT_REJECTED",
                      "required_stages": [{"id": "SCENE", "status": "PENDING"}], "attempts": []}
        self.write(self.path, self.state)
        self.event = {"hook_event_name": "Stop", "session_id": self.session, "cwd": str(self.root)}

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def decide(self, action="CONTINUE_GENERATION", complete=False):
        with patch.object(hook.guard, "completion_continuation", return_value={
            "action": action, "can_complete": complete, "reason": "Fixture remaining work",
        }):
            return hook.decide(self.event, self.root)

    def test_rejected_art_continues_and_no_one_shot_retry_cap(self):
        self.assertEqual(self.decide()["decision"], "block")
        self.event["stop_hook_active"] = True
        for _ in range(5):
            result = self.decide()
            self.assertEqual(result["decision"], "block")
            self.assertIn("Diagnose why", result["reason"])

    def test_command_entrypoint_returns_continuation_without_mutating_guard(self):
        scripts = self.root / "tools"
        scripts.mkdir()
        shutil.copy2(Path(hook.__file__), scripts / "storyart_completion_hook.py")
        shutil.copy2(Path(hook.guard.__file__), scripts / "task_execution_guard.py")
        before = self.path.read_bytes()
        for event_name, expected in (("Stop", "block"), ("Interrupt", None)):
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(scripts / "storyart_completion_hook.py")],
                input=json.dumps({**self.event, "hook_event_name": event_name}),
                text=True, encoding="utf-8", capture_output=True, timeout=15, cwd=self.root,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout).get("decision"), expected)
        self.assertEqual(self.path.read_bytes(), before)

    def test_real_guard_continuation_keeps_pending_deliverable_open(self):
        result = hook.decide(self.event, self.root)
        self.assertEqual(result["decision"], "block")
        self.assertIn("SCENE", result["reason"])
        self.write(self.path, {**self.state, "status": "COMPLETE"})
        self.assertEqual(hook.decide(self.event, self.root)["decision"], "block")

    def test_owned_missing_or_corrupt_guard_requires_recovery(self):
        for content in ("{torn-json", "[]", None):
            with self.subTest(content=content):
                if content is None:
                    self.path.unlink()
                else:
                    self.path.write_text(content, encoding="utf-8")
                result = hook.decide(self.event, self.root)
                self.assertEqual(result["decision"], "block")
                self.assertIn("Recover only this chat-bound request", result["reason"])

    def test_owned_malformed_attempts_require_recovery_but_do_not_override_stop(self):
        self.write(self.path, {**self.state, "attempts": ["invalid attempt row"]})
        self.assertEqual(hook.decide(self.event, self.root)["decision"], "block")
        self.write(self.path, {**self.state, "attempts": ["invalid attempt row"], "status": "STOPPED"})
        self.assertEqual(hook.decide(self.event, self.root), {})

    def test_real_guard_creation_sets_image_focus_and_general_supersedes_it(self):
        image_path = self.root / "STYLE_GENERATIONS" / "00_PENDING" / "real" / "EXECUTION_GUARD.json"
        # Isolated real registry/guard calls: no project data or host binding.
        self.focus.unlink()
        (self.registry / "chats" / f"{self.key}.json").unlink()
        with patch.dict(os.environ, {"CODEX_THREAD_ID": self.session}), patch.object(
            hook.guard, "_chat_request_root", return_value=self.registry
        ):
            hook.guard.create_guard(image_path, request_id="real", goal="fixture art",
                                    deliverable="fixture result", task_kind="IMAGE_GENERATION",
                                    allowed_scope=[], required_stages=["SCENE"])
            self.assertEqual(hook.decide(self.event, self.root)["decision"], "block")
            hook.guard.create_guard(self.root / ".agent" / "repair" / "EXECUTION_GUARD.json",
                                    request_id="repair", goal="repair process", deliverable="repair report",
                                    task_kind="GENERAL", allowed_scope=[])
            self.assertEqual(hook.decide(self.event, self.root), {})

    def test_real_complete_is_idempotent_and_old_revision_is_not_accepted(self):
        artifact = self.path.parent / "fixture.png"
        artifact.write_bytes(b"synthetic fixture; not generated art")
        attempt = {"attempt_id": "done", "status": "DELIVERED", "task_revision": 0,
                   "provider_artifact": {"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}}
        complete = {**self.state, "status": "COMPLETE", "required_stages": [], "task_revision": 0,
                    "attempts": [attempt], "latest_delivered_attempt_id": "done",
                    "latest_delivered_result_status": "TEST", "delivered_result_at": "fixture"}
        self.write(self.path, complete)
        before = self.path.read_bytes()
        self.assertEqual(hook.decide(self.event, self.root), {})
        self.assertEqual(hook.decide(self.event, self.root), {})
        self.assertEqual(self.path.read_bytes(), before)
        self.write(self.path, {**complete, "task_revision": 1})
        self.assertEqual(hook.decide(self.event, self.root)["decision"], "block")
        self.write(self.path, {**complete, "latest_delivered_result_status": "REJECTED"})
        self.assertEqual(hook.decide(self.event, self.root)["decision"], "block")

    def test_unknown_operation_requests_reconciliation(self):
        result = self.decide("RECONCILE_ATTEMPT")
        self.assertIn("RECONCILE_ATTEMPT", result["reason"])
        self.assertIn("never fabricate PASS", result["reason"])

    def test_real_unknown_attempt_blocks_final_without_overriding_explicit_stop(self):
        unknown = {**self.state, "status": "ACTIVE", "phase": "ATTEMPT_UNKNOWN",
                   "attempts": [{"attempt_id": "unknown", "status": "UNKNOWN"}],
                   "next_required_action": "RECONCILE_UNKNOWN_ATTEMPT"}
        self.write(self.path, unknown)
        result = hook.decide(self.event, self.root)
        self.assertEqual(result["decision"], "block")
        self.assertIn("RECONCILE_ATTEMPT", result["reason"])
        self.write(self.path, {**unknown, "status": "STOPPED"})
        self.assertEqual(hook.decide(self.event, self.root), {})

    def test_owned_corrupt_identity_requires_repair_without_other_folder_access(self):
        for corrupt in ({"owner_chat_id": "foreign"}, {"request_id": "wrong"}, {"task_kind": "GENERAL"}):
            with self.subTest(corrupt=corrupt):
                self.write(self.path, {**self.state, **corrupt})
                before = self.path.read_bytes()
                with patch.object(hook.guard, "completion_continuation") as continuation:
                    result = hook.decide(self.event, self.root)
                    continuation.assert_not_called()
                self.assertEqual(result["decision"], "block")
                self.assertIn("Recover only this owned guard", result["reason"])
                self.assertEqual(self.path.read_bytes(), before)

    def test_explicit_stop_cancel_and_user_wait_are_respected(self):
        for status in ("STOPPED", "CANCELLED", "WAITING_FOR_USER"):
            with self.subTest(status=status):
                self.write(self.path, {**self.state, "status": status})
                self.assertEqual(self.decide(), {})

    def test_external_boundary_allows_explanation_internal_does_not(self):
        for kind in ("EXTERNAL", "SAFETY", "USER_INPUT"):
            self.write(self.path, {**self.state, "status": "BLOCKED", "blocker_kind": kind})
            self.assertEqual(self.decide(), {})
        self.write(self.path, {**self.state, "status": "BLOCKED", "blocker_kind": "INTERNAL"})
        self.assertEqual(self.decide()["decision"], "block")

    def test_only_valid_complete_allows_final(self):
        self.write(self.path, {**self.state, "status": "COMPLETE"})
        self.assertEqual(self.decide(complete=True), {})
        self.assertEqual(self.decide(complete=False)["decision"], "block")

    def test_general_focus_does_not_resume_old_image_request(self):
        self.write(self.focus, {"thread_id": self.session, "task_kind": "GENERAL", "guard_path": str(self.path)})
        self.assertEqual(self.decide(), {})

    def test_other_project_worker_and_interrupt_are_noop(self):
        for extra in ({"cwd": str(self.root / "another")}, {"agent_id": "worker"}, {"hook_event_name": "Interrupt"}):
            with self.subTest(extra=extra):
                self.assertEqual(hook.decide({**self.event, **extra}, self.root), {})

    def test_unbound_chat_is_noop(self):
        self.event["session_id"] = "unrelated"
        self.assertEqual(self.decide(), {})

    def test_foreign_guard_is_not_read(self):
        self.write(self.registry / "chats" / f"{self.key}.json", {"thread_id": "foreign", "active_folder": str(self.path.parent)})
        with patch.object(hook.guard, "completion_continuation") as continuation:
            result = hook.decide(self.event, self.root)
            continuation.assert_not_called()
        self.assertIn("systemMessage", result)
        self.assertNotIn("decision", result)

    def test_forged_path_outside_request_is_not_read(self):
        self.write(self.focus, {"thread_id": self.session, "task_kind": "IMAGE_GENERATION", "guard_path": str(self.root / "outside.json")})
        self.assertIn("systemMessage", self.decide())


if __name__ == "__main__":
    unittest.main()
