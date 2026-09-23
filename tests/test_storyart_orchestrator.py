import importlib.util
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "storyart_orchestrator.py"
SPEC = importlib.util.spec_from_file_location("storyart_orchestrator", MODULE_PATH)
orchestrator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(orchestrator)


class StoryArtOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.request_root = Path(self.temp.name)
        self.guard_path = self.request_root / "EXECUTION_GUARD.json"
        self.state_path = self.request_root / "ORCHESTRATION_STATE.json"
        self.guard_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "request_id": "test-request",
                    "goal_lock": "Create one requested StoryArt frame",
                    "primary_deliverable": "Visible frame",
                    "status": "ACTIVE",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_project_config_template_uses_economical_gpt6_defaults(self):
        config_path = ROOT / "config" / "codex.project.example.toml"
        config_text = config_path.read_text(encoding="utf-8")
        config = tomllib.loads(config_text)

        self.assertEqual(config["model"], "gpt-6-luna")
        self.assertEqual(config["model_reasoning_effort"], "medium")
        self.assertEqual(config["agents"]["default_subagent_model"], "gpt-6-luna")
        self.assertEqual(config["agents"]["default_subagent_reasoning_effort"], "high")
        allowed_models = {"gpt-6-astra", "gpt-6-sol", "gpt-6-luna"}
        configured_models = {config["model"], config["agents"]["default_subagent_model"]}
        self.assertLessEqual(configured_models, allowed_models)

    def test_sol_high_gate_is_consistent_across_routing_contracts(self):
        required_condition = (
            "Sol High requires evidence of a substantive Luna failure for either "
            "complex implementation or repair; complexity alone does not qualify."
        )
        paths = (
            ROOT / "docs" / "EFFICIENT_WORKFLOW.md",
            ROOT / "skills" / "storyart-orchestrator" / "SKILL.md",
            ROOT / "skills" / "storyart-orchestrator" / "references" / "roles.md",
            ROOT / "skills" / "storyart-orchestrator" / "references" / "handoff-contract.md",
        )
        for path in paths:
            with self.subTest(path=path):
                contract_text = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn(required_condition, contract_text)

    def test_sol_low_is_reserved_for_planning_integration_and_review(self):
        routing_paths = (
            ROOT / "docs" / "EFFICIENT_WORKFLOW.md",
            ROOT / "skills" / "storyart-orchestrator" / "SKILL.md",
            ROOT / "skills" / "storyart-orchestrator" / "references" / "roles.md",
            ROOT / "skills" / "storyart-orchestrator" / "references" / "handoff-contract.md",
        )
        for path in routing_paths:
            with self.subTest(path=path):
                self.assertNotIn("Sol Medium", path.read_text(encoding="utf-8"))

        workflow = (ROOT / "docs" / "EFFICIENT_WORKFLOW.md").read_text(encoding="utf-8")
        self.assertIn("| Code planning/integration and independent review | Sol Low |", workflow)
        self.assertIn("| Critical independent QA | fresh `gpt-6-sol`, Low |", workflow)

    def initialize(self):
        return orchestrator.initialize_state(self.state_path, self.guard_path)

    def test_state_must_live_beside_guard(self):
        other = self.request_root / "nested" / "ORCHESTRATION_STATE.json"
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "beside"):
            orchestrator.initialize_state(other, self.guard_path)

    def test_init_derives_locked_goal_from_guard(self):
        state = self.initialize()
        self.assertEqual(state["request_id"], "test-request")
        self.assertEqual(state["goal_lock"], "Create one requested StoryArt frame")
        self.assertEqual(state["handoffs"], [])

    def test_read_only_role_rejects_write_path(self):
        self.initialize()
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "read-only"):
            orchestrator.dispatch_handoff(
                self.state_path,
                "STYLE_LIBRARIAN",
                "Inspect the style",
                [str(self.guard_path)],
                [str(self.request_root)],
                [],
                "",
                "",
            )

    def test_generator_requires_stage_and_requested_contract(self):
        self.initialize()
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "requires --stage"):
            orchestrator.dispatch_handoff(
                self.state_path,
                "GENERATOR_OPERATOR",
                "Generate the declared frame",
                [str(self.guard_path)],
                [str(self.request_root)],
                [],
                "",
                "REQUESTED_DELIVERABLE",
            )

    def test_dependency_must_be_done(self):
        self.initialize()
        first = orchestrator.dispatch_handoff(
            self.state_path,
            "STYLE_LIBRARIAN",
            "Inspect the style",
            [str(self.guard_path)],
            [],
            [],
            "",
            "",
        )
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "DONE is required"):
            orchestrator.dispatch_handoff(
                self.state_path,
                "CALL_PLANNER",
                "Prepare one call",
                [str(self.guard_path)],
                [],
                [first["handoff_id"]],
                "",
                "",
            )

    def test_complete_handoff_unlocks_dependency(self):
        self.initialize()
        first = orchestrator.dispatch_handoff(
            self.state_path,
            "STYLE_LIBRARIAN",
            "Inspect the style",
            [str(self.guard_path)],
            [],
            [],
            "",
            "",
        )
        orchestrator.complete_handoff(
            self.state_path,
            first["handoff_id"],
            "DONE",
            "Selected one compatible style reference.",
            [str(self.guard_path)],
        )
        second = orchestrator.dispatch_handoff(
            self.state_path,
            "CALL_PLANNER",
            "Prepare one call",
            [str(self.guard_path)],
            [],
            [first["handoff_id"]],
            "",
            "",
        )
        self.assertEqual(second["status"], "PENDING")

    def test_generator_cannot_write_to_infrastructure(self):
        self.initialize()
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "infrastructure"):
            orchestrator.dispatch_handoff(
                self.state_path,
                "GENERATOR_OPERATOR",
                "Generate the declared frame",
                [str(self.guard_path)],
                [str(ROOT / "tools")],
                [],
                "FRAME_01",
                "REQUESTED_DELIVERABLE",
            )

    def test_registrar_cannot_write_outside_generation_data(self):
        self.initialize()
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "REGISTRAR may write"):
            orchestrator.dispatch_handoff(
                self.state_path,
                "REGISTRAR",
                "Record the accepted frame",
                [str(self.guard_path)],
                [str(ROOT / "README.md")],
                [],
                "FRAME_01",
                "",
            )

    def test_agent_prompt_contains_explicit_paths(self):
        self.initialize()
        handoff = orchestrator.dispatch_handoff(
            self.state_path,
            "STYLE_LIBRARIAN",
            "Inspect the style",
            [str(self.guard_path)],
            [],
            [],
            "",
            "",
        )
        self.assertIn("EXECUTION_GUARD.json", handoff["agent_prompt"])
        self.assertIn("Allowed writes: NONE (read-only)", handoff["agent_prompt"])

    def test_escalation_orchestrator_is_due_read_only_astra_profile(self):
        self.initialize()
        guard_data = json.loads(self.guard_path.read_text(encoding="utf-8"))
        guard_data.update({
            "next_required_action": "ESCALATION_ORCHESTRATOR_REQUIRED",
            "escalation_incidents": [{"key": "x", "stage": "FRAME", "qa_layer": "STYLE", "status": "REQUIRED"}],
        })
        self.guard_path.write_text(json.dumps(guard_data), encoding="utf-8")
        handoff = orchestrator.dispatch_handoff(
            self.state_path, "ESCALATION_ORCHESTRATOR", "Return a recovery work order.",
            [str(self.guard_path)], [], [], "FRAME", "", "STYLE",
        )
        self.assertEqual(handoff["execution_profile"]["model"], "gpt-6-astra")
        self.assertEqual(handoff["execution_profile"]["reasoning_effort"], "low")
        self.assertIn("do not use tools", handoff["agent_prompt"])
        self.assertIn("Luna or Sol executor", handoff["agent_prompt"])
        self.assertIn("Sol High requires evidence of a substantive Luna failure", handoff["agent_prompt"])
        self.assertIn("complex implementation or repair", handoff["agent_prompt"])
        self.assertIn("complexity alone does not qualify", handoff["agent_prompt"])
        self.assertNotIn("Sol High only for complex", handoff["agent_prompt"])
        self.assertNotIn("Terra", handoff["agent_prompt"])
        guard_data = json.loads(self.guard_path.read_text(encoding="utf-8"))
        self.assertEqual(guard_data["escalation_incidents"][0]["status"], "CONSUMED")
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "due corrected"):
            orchestrator.dispatch_handoff(
                self.state_path, "ESCALATION_ORCHESTRATOR", "Duplicate work order.",
                [str(self.guard_path)], [], [], "FRAME", "", "STYLE",
            )

    def test_consumed_escalation_never_reopens_after_interruption(self):
        self.initialize()
        guard_data = json.loads(self.guard_path.read_text(encoding="utf-8"))
        guard_data.update({
            "next_required_action": "ESCALATION_ORCHESTRATOR_REQUIRED",
            "escalation_incidents": [{"key": "x", "stage": "FRAME", "qa_layer": "STYLE", "status": "CONSUMED"}],
        })
        self.guard_path.write_text(json.dumps(guard_data), encoding="utf-8")
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "due corrected"):
            orchestrator.dispatch_handoff(
                self.state_path, "ESCALATION_ORCHESTRATOR", "Restart interrupted work order.",
                [str(self.guard_path)], [], [], "FRAME", "", "STYLE",
            )

    def test_concurrent_escalation_dispatch_is_denied_by_consumption_lock(self):
        self.initialize()
        guard_data = json.loads(self.guard_path.read_text(encoding="utf-8"))
        guard_data.update({
            "next_required_action": "ESCALATION_ORCHESTRATOR_REQUIRED",
            "escalation_incidents": [{"key": "x", "stage": "FRAME", "qa_layer": "STYLE", "status": "REQUIRED"}],
        })
        self.guard_path.write_text(json.dumps(guard_data), encoding="utf-8")
        lock_path = self.guard_path.with_suffix(self.guard_path.suffix + ".escalation.lock")
        lock_path.write_text("locked", encoding="utf-8")
        try:
            with self.assertRaisesRegex(orchestrator.OrchestratorError, "already being consumed"):
                orchestrator.dispatch_handoff(
                    self.state_path, "ESCALATION_ORCHESTRATOR", "Concurrent work order.",
                    [str(self.guard_path)], [], [], "FRAME", "", "STYLE",
                )
        finally:
            lock_path.unlink(missing_ok=True)

    def test_escalation_orchestrator_rejects_non_due_or_write_handoff(self):
        self.initialize()
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "read-only"):
            orchestrator.dispatch_handoff(
                self.state_path, "ESCALATION_ORCHESTRATOR", "Return a work order.",
                [str(self.guard_path)], [str(self.request_root)], [], "FRAME", "", "STYLE",
            )

    def test_build_style_skills_creates_local_adapters(self):
        output = self.request_root / "style-skills"
        fake_styles = [
            {
                "style_name": "Test Style",
                "slug": "TEST_STYLE",
                "pack_path": str(self.request_root),
                "generations_path": str(self.request_root),
                "management": "MANAGED",
                "local_readiness": "READY",
                "can_generate": True,
                "source_images": 3,
                "work_images": 2,
                "characters": 1,
            }
        ]
        with patch.object(orchestrator, "query_ready_styles", return_value=fake_styles):
            index = orchestrator.build_style_skills(output)
        self.assertEqual(len(index["styles"]), 1)
        skill_root = output / "storyart-style-test-style"
        self.assertTrue((skill_root / "SKILL.md").is_file())
        self.assertTrue((skill_root / "agents" / "openai.yaml").is_file())
        self.assertTrue((skill_root / "references" / "style.json").is_file())
        self.assertEqual(index["styles"][0]["local_readiness"], "READY")
        self.assertTrue(index["styles"][0]["can_generate"])

    def test_style_skill_validation_rejects_images(self):
        output = self.request_root / "style-skills"
        fake_styles = [
            {
                "style_name": "Test Style",
                "slug": "TEST_STYLE",
                "pack_path": str(self.request_root),
                "generations_path": str(self.request_root),
                "management": "MANAGED",
                "local_readiness": "READY",
                "can_generate": True,
                "source_images": 3,
                "work_images": 2,
                "characters": 1,
            }
        ]
        with patch.object(orchestrator, "query_ready_styles", return_value=fake_styles):
            orchestrator.build_style_skills(output)
        (output / "storyart-style-test-style" / "copied-reference.png").write_bytes(b"x")
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "must not contain images"):
            orchestrator.validate_style_skills(output)


if __name__ == "__main__":
    unittest.main()
