"""Public CLI regressions using synthetic files and no generator/provider calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


class GuardCliLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.state = self.folder / "EXECUTION_GUARD.json"
        self.plan = self.folder / "REFERENCE_PLAN.json"
        self.reference = self.folder / "reference.png"
        self.output = self.folder / "output.png"
        Image.new("RGB", (12, 12), "navy").save(self.reference)
        Image.new("RGB", (12, 12), "green").save(self.output)
        risk_file = self.folder / "risk.json"
        self.run_tool(
            "generation_risk_assessor.py", "--text", "An adult fictional character in a navy suit.",
            "--reference", f"{self.reference}::D1::0D::Synthetic reviewed style fixture::STYLE",
            "--output", str(risk_file),
        )
        report = json.loads(risk_file.read_text(encoding="utf-8"))
        slot = {
            "path": str(self.reference),
            "sha256": hashlib.sha256(self.reference.read_bytes()).hexdigest(),
            "active_roles": ["STYLE"],
            "physically_attach": True,
        }
        self.plan.write_text(json.dumps({
            "schema_version": 6,
            "request_id": "cli-scene",
            "gate_status": "READY_FOR_GENERATION",
            "generation_purpose": "SCENE",
            "character_id": "CHAR_001",
            "risk_assessment": {**report, "prompt": report["original_prompt"]},
            "generation_workflow": {
                "mode": "SINGLE_PASS", "slots": [slot],
                "attachment_limit": 5, "attachments_used": 1,
            },
            "execution_call": {
                "request_id": "cli-scene",
                "stage_id": "SINGLE_PASS", "prompt": report["original_prompt"],
                "slots": [slot], "risk_assessment": report,
                "stage_output_bindings": [], "targeted_pack_bindings": [],
            },
        }), encoding="utf-8")
        self.run_tool(
            "task_execution_guard.py", "start", "--state", str(self.state),
            "--request-id", "cli-scene", "--goal", "One requested character scene.",
            "--deliverable", "One visible art.",
        )

    def run_tool(self, tool, *arguments, succeeds=True):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools" / tool), *arguments],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20,
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def checkpoint(self, event, *arguments, succeeds=True):
        return self.run_tool(
            "task_execution_guard.py", "checkpoint", "--state", str(self.state),
            "--event", event, "--summary", f"CLI fixture {event}", *arguments,
            succeeds=succeeds,
        )

    def read_state(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def start_attempt(self):
        self.checkpoint("READY_FOR_EXECUTION", "--reference-plan", str(self.plan))
        self.checkpoint(
            "EXECUTION_STARTED", "--reference-plan", str(self.plan),
            "--output-contract", "REQUESTED_DELIVERABLE",
        )
        return self.read_state()["active_attempt"]["attempt_id"]

    def test_cli_result_requires_delivery_and_attempt_survives_reload(self):
        attempt = self.start_attempt()
        self.checkpoint(
            "EXECUTION_STARTED", "--reference-plan", str(self.plan),
            "--output-contract", "REQUESTED_DELIVERABLE", succeeds=False,
        )
        self.checkpoint(
            "VISIBLE_RESULT", "--attempt-id", attempt, "--result-status", "TEST",
            "--evidence", str(self.output),
        )
        self.checkpoint("COMPLETE", succeeds=False)
        saved = self.read_state()
        self.assertEqual(saved["attempts"][-1]["status"], "RESULT_AVAILABLE")
        self.checkpoint(
            "RESULT_DELIVERED", "--attempt-id", attempt,
            "--delivery-evidence", "Synthetic UI message receipt for output.png",
        )
        self.checkpoint("COMPLETE")
        self.assertEqual(self.read_state()["status"], "COMPLETE")

    def test_cli_stop_reconciliation_never_implicitly_resumes(self):
        attempt = self.start_attempt()
        self.checkpoint("STOP")
        self.checkpoint("READY_FOR_EXECUTION", "--reference-plan", str(self.plan), succeeds=False)
        self.checkpoint(
            "ATTEMPT_RECONCILED", "--attempt-id", attempt, "--outcome", "CANCELLED",
            "--reconciliation-evidence", "Synthetic provider receipt: operation cancelled, no output",
        )
        self.checkpoint("READY_FOR_EXECUTION", "--reference-plan", str(self.plan), succeeds=False)
        self.checkpoint("USER_RESUMED")
        new_attempt = self.start_attempt()
        self.assertNotEqual(new_attempt, attempt)
        self.assertEqual(self.read_state()["attempts"][0]["status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()
