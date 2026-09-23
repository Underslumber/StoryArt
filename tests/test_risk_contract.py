from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generation_risk_assessor_contract", ROOT / "tools" / "generation_risk_assessor.py"
)
assert SPEC and SPEC.loader
risk = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = risk
SPEC.loader.exec_module(risk)
LEXICON = risk.load_lexicon(ROOT / "config" / "generation_risk_lexicon.json")


class RiskContractTests(unittest.TestCase):
    def make_report(self, folder: Path, prompt: str, image: Path, roles: list[str]) -> dict[str, object]:
        output = folder / "risk.json"
        args = Namespace(
            lexicon=risk.DEFAULT_LEXICON,
            text=prompt,
            prompt_file="",
            revised_text="",
            revised_prompt_file="",
            reference=[f"{image}::D2::0D::Нейтральный референс::{','.join(roles)}"],
            output=str(output),
            overwrite=False,
            json=False,
        )
        risk.command_assess(args)
        return json.loads(output.read_text(encoding="utf-8"))

    def test_exact_prompt_file_and_role_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "ref.png"
            image.write_bytes(b"same physical reference")
            report = self.make_report(folder, "A quiet adult portrait.", image, ["STYLE", "PRIMARY_FACE"])
            refs = [{"path": str(image), "active_roles": ["STYLE", "PRIMARY_FACE"]}]
            risk.validate_assessment(report, "A quiet adult portrait.", refs)
            with self.assertRaises(risk.RiskAssessmentError):
                risk.validate_assessment(report, "A quiet portrait.", refs)
            changed_payload = copy.deepcopy(report)
            changed_payload["original_prompt"]["text"] = "A different original."
            with self.assertRaises(risk.RiskAssessmentError):
                risk.validate_assessment(changed_payload, "A quiet adult portrait.", refs)
            with self.assertRaises(risk.RiskAssessmentError):
                risk.validate_assessment(report, "A quiet adult portrait.", [{**refs[0], "active_roles": ["STYLE"]}])
            image.write_bytes(b"modified physical reference")
            with self.assertRaises(risk.RiskAssessmentError):
                risk.validate_assessment(report, "A quiet adult portrait.", refs)

    def test_duplicate_physical_inputs_are_deduplicated_and_roles_unioned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "ref.png"
            image.write_bytes(b"identical")
            same_content = folder / "copy.png"
            same_content.write_bytes(b"identical")
            report = self.make_report(folder, "A plain landscape.", image, ["STYLE"])
            report["input_binding"]["references"][0]["active_roles"] = ["STYLE", "BR_0001:AUX_POSE"]
            report["references"][0]["active_roles"] = ["STYLE", "BR_0001:AUX_POSE"]
            risk.validate_assessment(report, "A plain landscape.", [
                {"path": str(image), "active_roles": ["STYLE"]},
                {"path": str(same_content), "active_roles": ["BR_0001:AUX_POSE"]},
            ])
            forged_roles = copy.deepcopy(report)
            forged_roles["references"][0]["active_roles"] = ["STYLE"]
            with self.assertRaisesRegex(risk.RiskAssessmentError, "roles"):
                risk.validate_assessment(forged_roles, "A plain landscape.", [
                    {"path": str(image), "active_roles": ["STYLE"]},
                    {"path": str(same_content), "active_roles": ["BR_0001:AUX_POSE"]},
                ])

    def test_arbitrary_combined_score_and_unbound_legacy_report_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "ref.png"
            image.write_bytes(b"reference")
            report = self.make_report(folder, "A quiet portrait.", image, ["STYLE"])
            refs = [{"path": str(image), "active_roles": ["STYLE"]}]
            forged = copy.deepcopy(report)
            forged["generation_risk"] = "D1"
            with self.assertRaises(risk.RiskAssessmentError):
                risk.validate_assessment(forged, "A quiet portrait.", refs)
            legacy = {key: value for key, value in report.items() if key != "input_binding"}
            with self.assertRaisesRegex(risk.RiskAssessmentError, "Legacy"):
                risk.validate_assessment(legacy, "A quiet portrait.", refs)

    def test_heuristic_separates_non_graphic_shootout_from_gore(self) -> None:
        ordinary = risk.evaluate_prompt("A fictional non-graphic shootout, no visible injuries.", LEXICON)
        gore = risk.evaluate_prompt("Graphic gore with an open wound and visible dismemberment.", LEXICON)
        self.assertLess(ordinary["score"], gore["score"])
        self.assertEqual(ordinary["risk"], "D1")
        self.assertTrue(ordinary["review_flags"])
        self.assertEqual(gore["risk"], "D8")

    def test_negation_does_not_erase_contradictory_graphic_request(self) -> None:
        result = risk.evaluate_prompt("No gore, but show graphic gore and an open wound.", LEXICON)
        self.assertEqual(result["risk"], "D8")
        self.assertTrue(result["review_flags"])


if __name__ == "__main__":
    unittest.main()
