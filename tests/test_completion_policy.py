"""Regression checks for contradictory agent-facing completion instructions.

These supplement runtime trajectories; text checks alone do not prove liveness.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = (
    "AGENTS.example.md",
    "docs/REQUEST_FIDELITY.md",
    "docs/EFFICIENT_WORKFLOW.md",
    "docs/GENERATION_RULES.md",
    "docs/PORTABLE_GENERATION_RULES.md",
    "docs/EXECUTION_GUARD.md",
    "docs/STYLE_TRANSFER_CALIBRATION.md",
    "docs/SCENE_GENERATION_WORKFLOW.md",
    "skills/storyart-orchestrator/SKILL.md",
    "skills/storyart-orchestrator/references/roles.md",
    "skills/storyart-orchestrator/references/handoff-contract.md",
)


class CompletionPolicyTests(unittest.TestCase):
    def test_standard_checks_include_both_test_formats(self):
        script = (ROOT / "scripts/bootstrap.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("-m pytest @testFiles", script)
        self.assertIn("requirements-dev.txt", script)
        dependencies = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8-sig")
        self.assertIn("pytest", dependencies)
        self.assertIn("-r requirements.txt", dependencies)

    def test_old_terminal_repair_prescriptions_are_removed(self):
        # Exact formerly active prescriptions from the incident, not a broad
        # ban on 'stop' (explicit user stop and external limits must survive).
        obsolete = (
            "Retain the overall limit of two ordinary repair loops.",
            "Соблюдай установленный сценарий восстановления и лимиты попыток",
            "If identity resolution/attachment fails, stop and report",
            "Stop if resolution or attachment fails.",
            "If the profile or required assets cannot be resolved or attached, stop",
            "после двух отклонений по `STYLE` в одном запросе",
            "`SCENE + NONE` всегда создаётся одним реальным вызовом.",
            "Stop if identity or assets are missing;",
            "Report a blocker only from a resolver miss or a concrete resolver/tool error.",
            "Если персонажа или источники нельзя разрешить/прикрепить, остановись",
            "If the character or required assets cannot be resolved or attached, stop and state the blocker",
            "Keep `SCENE + NONE` single-pass.",
        )
        for relative in ENTRYPOINTS:
            path = ROOT / relative
            # GENERATION_RULES is a local optional nonportable reference.
            if not path.exists() and relative == "docs/GENERATION_RULES.md":
                continue
            content = " ".join(path.read_text(encoding="utf-8-sig").split())
            for prescription in obsolete:
                with self.subTest(path=relative, prescription=prescription):
                    self.assertNotIn(prescription, content)

    def test_primary_entrypoints_route_to_completion_contract(self):
        for relative in (
            "AGENTS.example.md", "docs/EFFICIENT_WORKFLOW.md",
            "docs/PORTABLE_GENERATION_RULES.md",
            "skills/storyart-orchestrator/SKILL.md",
        ):
            with self.subTest(path=relative):
                self.assertIn("REQUEST_FIDELITY.md", (ROOT / relative).read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
