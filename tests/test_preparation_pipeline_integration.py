"""Exercise the bundled preparation entry point with the real project CLIs."""
import json
import unittest

from tests import test_generation_workflow_integration as workflow_fixture
from tools import style_pack_manager as manager


class BundledPreparationIntegrationTests(unittest.TestCase):
    def test_real_bundle_and_ready_retry_preserve_existing_artifacts(self):
        fixture = workflow_fixture.GenerationWorkflowIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original_cli = fixture._cli
        request_file = fixture.workspace / "request.json"
        call_file = fixture.workspace / "call.json"
        call_file.write_text(json.dumps({
            "prompt_text": "Create one synthetic stone observatory artifact on a plain dark backdrop.",
            "reference_ratings": [{
                "path": str(fixture.style_reference), "active_roles": ["STYLE"],
                "content_and_reference_risk": "D1", "use_impact": "+0D",
                "reason_ru": "Synthetic fixture contains only a plain color field.",
            }],
        }), encoding="utf-8")

        def bundled_cli(script, *args, succeeds=True):
            if script == "style_pack_manager.py" and args[0] == "prepare-generation":
                namespace = manager.build_parser().parse_args([str(arg) for arg in args])
                values = {key: value for key, value in vars(namespace).items()
                          if key not in {"command", "handler"} and value not in (None, "")}
                request_file.write_text(json.dumps(values, default=str), encoding="utf-8")
                return original_cli("generation_request.py", "--request-file", request_file,
                                    "--call-file", call_file, succeeds=succeeds)
            return original_cli(script, *args, succeeds=succeeds)

        fixture._cli = bundled_cli
        guard_path, plan_path = fixture._prepare_scene(
            "bundled-preparation", fidelity=90, startup_mode="USER_CONFIRMATION",
            startup_choice="OPTION_2", startup_choice_quote="2",
            startup_options=(
                "OPTION_1=Recommended 90% fidelity; select BODY_REFERENCE_LIBRARY",
                "OPTION_2=Contextual 90% fidelity; decline BODY_REFERENCE_LIBRARY",
                "OPTION_3=Contextual 70% fidelity; decline BODY_REFERENCE_LIBRARY",
            ),
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["gate_status"], "READY_FOR_GENERATION")
        guard = json.loads(guard_path.read_text(encoding="utf-8"))
        self.assertFalse(guard.get("active_attempt"))
        tracked = [plan_path, guard_path, *plan_path.parent.rglob("*RISK*.json")]
        before = {str(path): path.read_bytes() for path in tracked}
        result = original_cli("generation_request.py", "--request-file", request_file,
                              "--call-file", call_file, succeeds=False)
        self.assertIn("ALREADY_PREPARED", result.stdout + result.stderr)
        self.assertEqual(before, {str(path): path.read_bytes() for path in tracked})


if __name__ == "__main__":
    unittest.main()
