import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from tools import chatgpt_web_image as web
from tools import chatgpt_web_image_handoff as packets
from tools import storyart_orchestrator as orchestrator
from tools import task_execution_guard as guard
from tests.test_task_execution_guard import _native_default_call

ROOT = Path(__file__).resolve().parents[1]


class WebImageHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.request = self.workspace / "запрос с пробелами"
        self.request.mkdir()
        (self.workspace / "config").mkdir()
        shutil.copyfile(ROOT / "config/model_routes.json", self.workspace / "config/model_routes.json")
        for patcher in (
            patch.dict(os.environ, {"CODEX_THREAD_ID": "web-handoff-isolated-unit-test"}),
            patch.object(guard, "_chat_request_root", return_value=self.workspace / "registry"),
            patch.dict("sys.modules", {"task_execution_guard": guard}),
            patch.object(packets, "ROOT", self.workspace),
            patch.object(orchestrator, "PROJECT_ROOT", self.workspace),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            call = _native_default_call(self.request, "A simple graphite landscape, without people.")
        call["stage_id"] = "SCENE"
        self.guard_path = self.request / "EXECUTION_GUARD.json"
        guard.create_guard(self.guard_path, request_id="request-1", goal="Draw the requested scene through Web.",
                           deliverable="One downloaded image.", task_kind="IMAGE_GENERATION_NATIVE_DEFAULT", required_stages=["SCENE"])
        guard.checkpoint(self.guard_path, event="READY_FOR_EXECUTION", summary="Exact call ready", execution_call=call)
        started = guard.checkpoint(self.guard_path, event="EXECUTION_STARTED", summary="Authorized image", execution_call=call,
                                   output_contract="REQUESTED_DELIVERABLE")
        self.call_path = self.request / "execution_call.json"
        web.save(self.call_path, call)
        self.proof_path = self.request / "proof.json"
        proof = {"validated": True, "request_id": "request-1", "references": [],
                 "prompt_sha256": call["prompt"]["text_sha256"], "guard_path": str(self.guard_path),
                 "execution_call_path": str(self.call_path), "attempt_id": started["active_attempt"]["attempt_id"]}
        web.save(self.proof_path, proof)
        self.project_path = self.workspace / "config/project.local.json"
        web.save(self.project_path, {"project_id": "g-p-abc", "project_name": "Private test destination",
                                     "project_url": "https://chatgpt.com/g/g-p-abc/project"})
        attempt = web.authorized_native_attempt(started, call, proof)
        self.journal = self.request / "web.json"
        self.output = self.request / "result.png"
        state = {"schema_version": 1, "project_binding": web.project_binding(self.project_path),
                 "prompt": call["prompt"]["text"], "prompt_sha256": call["prompt"]["text_sha256"],
                 "references": [], "oracle_root": str(self.workspace / "local-oracle"),
                 "model_strategy": "current", "model_label": None, "profile_dir": str(self.workspace / "private-profile"),
                 "requested_output": str(self.output), "oracle_home": str(self.request / "web.oracle-runtime"),
                 "opt_in": True, "safety_history": "CLEAR", "neutral_test": False,
                 "windows_isolated_browser": True, "browser_mode": "windows-isolated",
                 "scenario_evidence": {"path": str(self.proof_path), "sha256": web.digest(self.proof_path.read_bytes()),
                    "attempt": attempt, "plan": {"path": str(self.call_path), "sha256": web.digest(self.call_path.read_bytes())},
                    "guard": {"path": str(self.guard_path)}, "branch": "NATIVE_DEFAULT"}, "events": []}
        web.pin_runtime_config(state)
        web.event(state, "PREPARED")
        web.save(self.journal, state)
        web.save(self.request / ".chatgpt-web-image-active.json", {"journal": str(self.journal)})
        self.spec_path = self.request / "final-spec.md"
        self.spec = "Итоговое ТЗ №1\nГрафитный пейзаж, без людей и референсов.\nНикаких видимых окон; только ChatGPT Web.\n"
        self.spec_path.write_bytes(self.spec.encode("utf-8"))

    def create_packet(self):
        return packets.create(self.journal, self.spec_path, workspace=self.workspace)

    def transport(self, payload, timeout):
        yield {"type": "receipt", "session_id": "original-test-session"}
        output = Path(payload["oracle_home"]) / "generated/result.png"
        output.parent.mkdir(parents=True)
        Image.new("RGB", (32, 32), "white").save(output)
        yield {"type": "result", "result": {"sessionId": "original-test-session", "status": "completed",
               "images": [{"kind": "image", "path": str(output)}]}}

    def test_final_spec_roundtrip_role_and_idempotent_handoff(self):
        first = self.create_packet()
        raw = Path(first["packet_path"]).read_bytes()
        packet = packets.validate_packet(first["packet_path"], workspace=self.workspace, for_submission=True)
        self.assertEqual(packet["final_specification"]["text"], self.spec)
        self.assertEqual(packet["prompt"]["text"], web.load(self.journal)["prompt"])
        self.assertEqual(first["agent_type"], "web_image")
        self.assertEqual(first["handoff"]["execution_profile"], {"model": "gpt-6.1-sol", "reasoning_effort": "low"})
        self.assertEqual(first["handoff"]["allowed_write_paths"], [orchestrator.relative_project_path(self.request)])
        second = self.create_packet()
        self.assertEqual(second["handoff"]["handoff_id"], first["handoff"]["handoff_id"])
        self.assertEqual(Path(first["packet_path"]).read_bytes(), raw)
        state = orchestrator.load_state(self.request / "ORCHESTRATION_STATE.json")
        self.assertEqual(len(state["handoffs"]), 1)
        result = web.execute(self.journal, transport=self.transport)
        self.assertEqual(result["status"], "DOWNLOADED")
        packets.validate_packet(first["packet_path"], workspace=self.workspace)
        with self.assertRaises(ValueError):
            packets.validate_packet(first["packet_path"], workspace=self.workspace, for_submission=True)

    def test_changed_final_spec_blocks_before_unknown_or_transport(self):
        self.create_packet()
        self.spec_path.write_text(self.spec + "Новая поправка.", encoding="utf-8")
        with self.assertRaises(ValueError):
            web.execute(self.journal, transport=lambda *_: self.fail("No provider call permitted"))
        self.assertEqual(web.load(self.journal)["status"], "PREPARED")

    def test_final_spec_preserves_literal_crlf_and_unicode(self):
        literal = self.spec.replace("\n", "\r\n")
        self.spec_path.write_bytes(literal.encode("utf-8"))
        result = self.create_packet()
        packet = packets.validate_packet(result["packet_path"], workspace=self.workspace)
        self.assertEqual(packet["final_specification"]["text"], literal)

    def test_changed_packet_blocks_send(self):
        result = self.create_packet()
        packet_path = Path(result["packet_path"])
        packet = json.loads(packet_path.read_bytes())
        packet["provider"] = "BUILTIN"
        packet["packet_sha256"] = packets.packet_seal(packet)
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        with self.assertRaises(ValueError):
            web.execute(self.journal, transport=lambda *_: self.fail("No fallback send"))
        self.assertEqual(web.load(self.journal)["status"], "PREPARED")

    def test_changed_project_blocks_send_without_journal_mutation(self):
        self.create_packet()
        baseline = self.journal.read_bytes()
        web.save(self.project_path, {"project_id": "g-p-other", "project_name": "Other project",
                                    "project_url": "https://chatgpt.com/g/g-p-other/project"})
        with self.assertRaises(ValueError):
            web.execute(self.journal, transport=lambda *_: self.fail("Changed destination must not send"))
        self.assertEqual(self.journal.read_bytes(), baseline)

    def test_dispatch_without_packet_or_outside_scope_rejected(self):
        state_path = self.request / "ORCHESTRATION_STATE.json"
        orchestrator.initialize_state(state_path, self.guard_path)
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.dispatch_handoff(state_path, "WEB_IMAGE", "Draw", [str(self.spec_path)],
                                          [str(self.request)], [], "SCENE", "REQUESTED_DELIVERABLE")
        outside = self.workspace / "other-request"
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.validate_writer_paths("WEB_IMAGE", self.request, [outside])
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.dispatch_handoff(state_path, "GENERATOR_OPERATOR", "Draw", [],
                                          [str(self.request)], [], "SCENE", "REQUESTED_DELIVERABLE")

    def test_final_spec_outside_request_is_not_read(self):
        other = self.workspace / "other-spec.md"
        other.write_text("Other request", encoding="utf-8")
        with self.assertRaises(ValueError):
            packets.create(self.journal, other, workspace=self.workspace)
        self.assertFalse((self.request / "web.web-image-packet.json").exists())

    def test_extra_input_cannot_expand_packet_authority(self):
        result = self.create_packet()
        packet = packets.validate_packet(result["packet_path"], workspace=self.workspace)
        other = self.workspace / "another-request" / "private-spec.md"
        other.parent.mkdir()
        other.write_text("Private synthetic specification", encoding="utf-8")
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "exactly match its sealed packet"):
            orchestrator.dispatch_handoff(
                self.request / "ORCHESTRATION_STATE.json", "WEB_IMAGE", "Execute sealed packet",
                packet["input_paths"] + [str(other)], [str(self.request)], [],
                packet["stage"], "REQUESTED_DELIVERABLE")


if __name__ == "__main__":
    unittest.main()
