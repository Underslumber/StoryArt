from __future__ import annotations

import copy
import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import author_scene_completion as storage
from tools import style_pack_manager as manager
from tools import task_execution_guard as guard
from tools import storyart_completion_hook as hook


class AuthorSceneCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.workspace = self.base / "project"
        self.paths = manager.make_paths(self.workspace, "TEST")
        self.root = self.paths.generations / "00_PENDING" / "request-1"
        self.root.mkdir(parents=True)
        self.guard_path = self.root / "EXECUTION_GUARD.json"
        self.output = self.root / "output.png"
        self.output.write_bytes(b"original-provider-bytes")
        self.saved = self.paths.generations / "01_APPROVED_CHARACTERS/CHAR_001_Test/02_SCENES/scene.png"
        self.saved.parent.mkdir(parents=True)
        self.saved.write_bytes(self.output.read_bytes())
        self.sha = storage.digest(self.output)
        self.host = self.base / "host"
        self.sessions = self.host / "sessions"
        self.sessions.mkdir(parents=True)
        self.provider = self.host / "generated_images/chat-1/provider.png"
        self.provider.parent.mkdir(parents=True)
        self.provider.write_bytes(self.output.read_bytes())
        self.log = self.sessions / "rollout-chat-1.jsonl"
        self.log_rows = [
            {"type": "session_meta", "payload": {"id": "chat-1"}},
            {"timestamp": "2026-11-06T10:01:00Z", "type": "response_item", "payload": {
                "type": "custom_tool_call_output", "output": [
                    {"type": "input_text", "text": str(self.provider) + " The generated image is already displayed to the user"},
                    {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(self.output.read_bytes()).decode()}]}},
            {"timestamp": "2026-11-06T10:02:00Z", "type": "response_item", "payload": {
                "type": "message", "id": "message-1", "role": "user", "content": [{"text": "Save this scene"}]}},
        ]
        self.write_log()
        self.call = {"prompt": {"text": "scene"}, "slots": []}
        self.snapshot_path = self.root / "EXECUTED_PLAN.json"
        self.plan = {"generation_purpose": "SCENE", "request_id": "request-1", "character_id": "CHAR_001",
                     "pack_path": str(self.paths.pack), "style_name": "TEST", "execution_call": self.call}
        self.write_plan()
        snapshot = {"request_purpose": "SCENE", "reference_plan_snapshot_path": str(self.snapshot_path),
                    "reference_plan_snapshot_sha256": storage.digest(self.snapshot_path), "execution_call": self.call,
                    "execution_call_sha256": hashlib.sha256(json.dumps(self.call, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                    "prompt_sha256": hashlib.sha256(b"scene").hexdigest()}
        self.attempt = {"attempt_id": "attempt-1", "status": "ACTIVE", "task_revision": 2,
                        "started_at": "2026-11-06T10:00:00Z", "execution_snapshot": snapshot,
                        "provider_artifact": {"path": str(self.output), "sha256": self.sha},
                        "qa_results": {"BODY_PROPORTIONS": "FAIL"}}
        self.state = {"request_id": "request-1", "owner_chat_id": "chat-1", "task_revision": 2,
                      "task_kind": "IMAGE_GENERATION", "request_purpose": "SCENE", "status": "ACTIVE",
                      "required_stages": [{"id": "SINGLE_PASS", "status": "PENDING"}],
                      "attempts": [self.attempt], "active_attempt": self.attempt, "events": []}
        self.notes = "Author accepted this exact scene for storage despite the following QA findings: hands still large; author_acceptance_quote=Save this scene"
        self.row = {"request_id": "request-1", "character_id": "CHAR_001", "status": "APPROVED_SCENE",
                    "generation_id": "GEN-1", "style_file": str(self.saved), "qa_output_sha256": self.sha, "notes": self.notes}
        self.write_rows([self.row])
        self.receipt = {"schema_version": 1, "request_id": "request-1", "character_id": "CHAR_001",
                        "task_revision": 2, "attempt_id": "attempt-1", "generation_id": "GEN-1",
                        "output_sha256": self.sha, "approved_scene_path": str(self.saved),
                        "approval_quote": "Save this scene", "technical_findings": self.notes,
                        "delivery_evidence": ["original image displayed"], "host_session_log": str(self.log),
                        "host_delivery": self.line_binding(2), "provider_output_path": str(self.provider),
                        "host_user_messages": [{**self.line_binding(3), "message_id": "message-1", "quote": "Save this scene"}]}
        self.receipt_path = self.root / storage.RECEIPT_NAME
        self.write_receipt()
        self.addCleanup(patch.stopall)
        patch.object(storage, "SESSION_ROOT", self.sessions).start()

    def write_log(self):
        self.log.write_text("\n".join(json.dumps(r) for r in self.log_rows) + "\n", encoding="utf-8")

    def line_binding(self, line):
        return {"line": line, "sha256": hashlib.sha256(self.log.read_bytes().splitlines()[line - 1]).hexdigest()}

    def write_plan(self):
        self.snapshot_path.write_text(json.dumps(self.plan), encoding="utf-8")

    def write_rows(self, rows):
        manager.write_csv(self.paths.generation_manifest, manager.GENERATION_FIELDS, rows)

    def write_receipt(self):
        self.receipt_path.write_text(json.dumps(self.receipt), encoding="utf-8")

    def validate(self):
        return storage.validate(self.guard_path, self.state, self.receipt_path)

    def test_valid_scene_keeps_technical_failure(self):
        before = copy.deepcopy(self.state)
        self.assertEqual(self.validate()["generation_id"], "GEN-1")
        self.assertEqual(self.state, before)

    def test_checkpoint_complete_and_idempotent_replay_preserve_qa_and_artifact(self):
        with patch.object(guard, "load_guard", side_effect=lambda p: json.loads(Path(p).read_text())), patch.object(guard, "_read_binding", return_value=None):
            self.guard_path.write_text(json.dumps(self.state))
            state = guard.checkpoint(self.guard_path, event="AUTHOR_SCENE_STORED", summary="Author accepted and saved", evidence=[str(self.receipt_path)])
            self.assertIsNone(state["active_attempt"])
            self.assertEqual(state["attempts"][-1]["qa_results"], {"BODY_PROPORTIONS": "FAIL"})
            self.assertEqual(state["attempts"][-1]["provider_artifact"], self.attempt["provider_artifact"])
            self.assertNotIn("result_evidence", state["attempts"][-1])
            self.assertEqual(state, guard.checkpoint(self.guard_path, event="AUTHOR_SCENE_STORED", summary="Replay", evidence=[str(self.receipt_path)]))
            state = guard.checkpoint(self.guard_path, event="COMPLETE", summary="Accepted storage completed")
            self.assertEqual(state["status"], "COMPLETE")
            registry = self.workspace / ".agent/chat_requests"
            chat_key = hashlib.sha256(b"chat-1").hexdigest()
            folder_key = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()
            for name, payload in {
                "focus/" + chat_key + ".json": {"thread_id": "chat-1", "task_kind": "IMAGE_GENERATION", "guard_path": str(self.guard_path.resolve())},
                "chats/" + chat_key + ".json": {"thread_id": "chat-1", "active_folder": str(self.root.resolve())},
                "folders/" + folder_key + ".json": {"thread_id": "chat-1", "active_folder": str(self.root.resolve())},
            }.items():
                file = registry / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_text(json.dumps(payload))
            event = {"hook_event_name": "Stop", "session_id": "chat-1", "cwd": str(self.workspace)}
            self.assertEqual(hook.decide(event, self.workspace), {})
            self.saved.write_bytes(b"changed")
            self.assertIsNotNone(guard.completion_block_reason(state, self.guard_path))
            self.assertEqual(hook.decide(event, self.workspace)["decision"], "block")

    def test_forged_quote_rejected_even_if_native_notes_self_match(self):
        self.receipt["host_user_messages"][0]["quote"] = "Invented acceptance"
        self.write_receipt()
        with self.assertRaises(ValueError): self.validate()

    def test_permission_before_delivery_rejected(self):
        self.log_rows[2]["timestamp"] = "2026-11-06T09:59:00Z"
        self.write_log()
        self.receipt["host_user_messages"][0].update(self.line_binding(3))
        self.write_receipt()
        with self.assertRaises(ValueError): self.validate()

    def test_agent_printed_delivery_text_without_original_inline_image_rejected(self):
        self.log_rows[1]["payload"]["output"] = self.log_rows[1]["payload"]["output"][:1]
        self.write_log()
        self.receipt["host_delivery"] = self.line_binding(2)
        self.write_receipt()
        with self.assertRaises(ValueError): self.validate()

    def test_foreign_chat_rejected(self):
        self.state["owner_chat_id"] = "other-chat"
        with self.assertRaises(ValueError): self.validate()

    def test_intervening_user_or_image_and_duplicate_approval_rejected(self):
        original = copy.deepcopy(self.log_rows)
        for intervening in [
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "No"}]}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output", "output": [{"type": "input_image", "image_url": "other"}]}}
        ]:
            self.log_rows = original[:2] + [intervening] + original[2:]
            self.write_log()
            self.receipt["host_user_messages"][0].update(self.line_binding(4))
            self.write_receipt()
            with self.assertRaises(ValueError): self.validate()
        self.log_rows = original; self.write_log()
        self.receipt["host_user_messages"][0].update(self.line_binding(3))
        self.receipt["host_user_messages"] *= 2
        self.write_receipt()
        with self.assertRaises(ValueError): self.validate()

    def test_existing_delivered_test_is_not_rewritten(self):
        self.state["attempts"][-1].update(status="DELIVERED", result_status="TEST", delivered_at="earlier")
        before = copy.deepcopy(self.state)
        with patch.object(guard, "load_guard", return_value=self.state):
            with self.assertRaises(guard.GuardError):
                guard.checkpoint(self.guard_path, event="AUTHOR_SCENE_STORED", summary="No overwrite", evidence=[str(self.receipt_path)])
        self.assertEqual(self.state, before)

    def test_owner_and_host_log_hash_required(self):
        self.state["owner_chat_id"] = None
        with self.assertRaises(ValueError): self.validate()
        self.state["owner_chat_id"] = "chat-1"
        self.receipt["host_user_messages"][0]["sha256"] = "0" * 64
        self.write_receipt()
        with self.assertRaises(ValueError): self.validate()

    def test_duplicate_or_changed_registry_rejected(self):
        for rows in [[self.row, self.row], [{**self.row, "qa_output_sha256": "0" * 64}]]:
            self.write_rows(rows)
            with self.assertRaises(ValueError): self.validate()

    def test_missing_or_changed_snapshot_rejected(self):
        self.snapshot_path.write_text("{}")
        with self.assertRaises(ValueError): self.validate()

    def test_wrong_scene_path_rejected(self):
        outside = self.workspace / "elsewhere.png"
        outside.write_bytes(self.output.read_bytes())
        self.row["style_file"] = str(outside)
        self.receipt["approved_scene_path"] = str(outside)
        self.write_rows([self.row]); self.write_receipt()
        with self.assertRaises(ValueError): self.validate()

    def test_wrong_purpose_revision_unknown_and_mixed_acceptance_rejected(self):
        baseline = copy.deepcopy(self.state)
        for mutation in [lambda s: s.update(request_purpose="CHARACTER_BASE"),
                         lambda s: s.update(task_revision=3),
                         lambda s: s["attempts"][-1].update(status="UNKNOWN"),
                         lambda s: s.update(author_contract_acceptance={"receipt_path": "other"})]:
            self.state = copy.deepcopy(baseline); mutation(self.state)
            with self.assertRaises(ValueError): self.validate()

    def test_modified_receipt_after_acceptance_blocks_completion(self):
        with patch.object(guard, "load_guard", return_value=self.state):
            state = guard.checkpoint(self.guard_path, event="AUTHOR_SCENE_STORED", summary="Accepted", evidence=[str(self.receipt_path)])
        self.receipt["delivery_evidence"] = ["Changed later"]
        self.write_receipt()
        self.assertIsNotNone(guard.completion_block_reason(state, self.guard_path))

    def test_correction_after_complete_retires_old_proof_and_accepts_new_revision(self):
        with patch.object(guard, "load_guard", side_effect=lambda p: json.loads(Path(p).read_text())), patch.object(guard, "_read_binding", return_value=None), patch.object(guard, "_write_execution_focus"):
            self.guard_path.write_text(json.dumps(self.state))
            guard.checkpoint(self.guard_path, event="AUTHOR_SCENE_STORED", summary="Accepted", evidence=[str(self.receipt_path)])
            state = guard.checkpoint(self.guard_path, event="COMPLETE", summary="Done")
            state = guard.checkpoint(self.guard_path, event="USER_CORRECTION", summary="Change this scene", stage="SINGLE_PASS", correction_impact="PRESERVE")
            self.assertNotIn("author_scene_storage", state)
            archived = Path(state["author_scene_storage_history"][0]["receipt_archive"])
            self.assertEqual(storage.digest(archived), storage.digest(self.receipt_path))
            self.output.write_bytes(b"different-provider-output-for-new-revision")
            self.provider.write_bytes(self.output.read_bytes())
            self.saved.write_bytes(self.output.read_bytes())
            self.sha = storage.digest(self.output)
            self.row.update(generation_id="GEN-2", qa_output_sha256=self.sha)
            self.write_rows([self.row])
            new_attempt = copy.deepcopy(self.attempt)
            new_attempt.update(attempt_id="attempt-2", task_revision=state["task_revision"], started_at="2026-11-06T10:03:00Z")
            new_attempt["provider_artifact"] = {"path": str(self.output), "sha256": self.sha}
            state["attempts"].append(new_attempt); state["active_attempt"] = new_attempt
            self.log_rows.append({**copy.deepcopy(self.log_rows[1]), "timestamp": "2026-11-06T10:04:00Z"})
            self.log_rows[-1]["payload"]["output"][1]["image_url"] = "data:image/png;base64," + base64.b64encode(self.output.read_bytes()).decode()
            self.log_rows.append({**copy.deepcopy(self.log_rows[2]), "timestamp": "2026-11-06T10:05:00Z"})
            self.log_rows[-1]["payload"]["id"] = "message-2"
            self.write_log()
            self.receipt.update(attempt_id="attempt-2", task_revision=state["task_revision"], output_sha256=self.sha,
                                generation_id="GEN-2", host_delivery=self.line_binding(4),
                                host_user_messages=[{**self.line_binding(5), "message_id": "message-2", "quote": "Save this scene"}])
            self.write_receipt(); self.guard_path.write_text(json.dumps(state))
            state = guard.checkpoint(self.guard_path, event="AUTHOR_SCENE_STORED", summary="New revision accepted", evidence=[str(self.receipt_path)])
            state = guard.checkpoint(self.guard_path, event="COMPLETE", summary="New revision done")
            self.assertEqual(state["latest_delivered_attempt_id"], "attempt-2")
            self.assertEqual(state["status"], "COMPLETE")
            self.assertTrue(guard.completion_continuation(state, self.guard_path)["can_complete"])

    def test_explicit_stage_reopen_retires_storage_binding(self):
        with patch.object(guard, "load_guard", side_effect=lambda p: json.loads(Path(p).read_text())):
            self.guard_path.write_text(json.dumps(self.state))
            guard.checkpoint(self.guard_path, event="AUTHOR_SCENE_STORED", summary="Accepted", evidence=[str(self.receipt_path)])
            state = guard.checkpoint(self.guard_path, event="STAGE_REOPENED", summary="Recheck", stage="SINGLE_PASS")
            self.assertNotIn("author_scene_storage", state)
            self.assertEqual(state["required_stages"][0]["status"], "PENDING")

    def test_partial_receipt_archive_does_not_block_reopen(self):
        with patch.object(guard, "load_guard", side_effect=lambda p: json.loads(Path(p).read_text())):
            self.guard_path.write_text(json.dumps(self.state))
            guard.checkpoint(self.guard_path, event="AUTHOR_SCENE_STORED", summary="Accepted", evidence=[str(self.receipt_path)])
            archive = self.root / ("AUTHOR_SCENE_COMPLETION_" + storage.digest(self.receipt_path) + ".json")
            archive.write_bytes(b"partial interrupted write")
            guard.checkpoint(self.guard_path, event="STAGE_REOPENED", summary="Recheck", stage="SINGLE_PASS")
            self.assertEqual(storage.digest(archive), storage.digest(self.receipt_path))

    def test_complete_rejects_relocated_receipt_root(self):
        with patch.object(guard, "load_guard", return_value=self.state):
            state = guard.checkpoint(self.guard_path, event="AUTHOR_SCENE_STORED", summary="Accepted", evidence=[str(self.receipt_path)])
        self.assertIsNone(guard.completion_block_reason(state, self.guard_path))
        self.assertIsNotNone(guard.completion_block_reason(state, self.workspace / "other/EXECUTION_GUARD.json"))

    def test_other_registry_row_cannot_bind_old_bytes_to_same_path(self):
        self.write_rows([self.row, {**self.row, "request_id": "old-request", "status": "TEST", "qa_output_sha256": "0" * 64}])
        with self.assertRaises(ValueError): self.validate()

    def test_reparse_points_rejected(self):
        with patch.object(Path, "lstat") as info:
            info.return_value.st_mode = 0o100644
            info.return_value.st_file_attributes = 0x400
            with self.assertRaises(ValueError): storage.plain_file(self.receipt_path)


if __name__ == "__main__":
    unittest.main()
