from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import style_calibration_manager as calibration


class StyleCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.archive_temp = tempfile.TemporaryDirectory()
        self.archive_patch = patch.object(
            calibration, "GENERATION_RESULTS_ROOT", Path(self.archive_temp.name) / "GENERATION_RESULTS"
        )
        self.archive_patch.start()

    def tearDown(self):
        self.archive_patch.stop()
        self.archive_temp.cleanup()

    def make_style_analysis(self, folder: Path) -> Path:
        path = folder / "style_analysis.json"
        path.write_text(json.dumps({
            "source_scope": "Complete reviewed local style pool.",
            "reviewed_source_count": 12,
            "invariant_core_features": ["selective facial edges"],
            "optional_variation": ["background density"],
            "forbidden_drift": ["photoreal pores", "flat cel anime"],
            "facial_construction": ["compact modeled nose", "almond eyes", "compact lips"],
            "working_hypothesis": "Rendered comic construction with soft facial value edges.",
        }), encoding="utf-8")
        return path

    def style_prompt(self, round_number: int, label: str) -> str:
        return (
            f"Round {round_number} prompt {label}: selective facial edges, compact modeled nose, "
            f"almond eyes, grouped hair masses, and language hypothesis {label}."
        )

    def make_strategy(
        self, folder: Path, name: str = "strategy.json", *, round_number: int = 1
    ) -> Path:
        path = folder / name
        roles = ("TARGET", "ALTERNATIVE", "CHAOS_PROBE", "CONTROL")
        path.write_text(json.dumps({
            "hypothesis": "Test four controlled style-transfer routes.",
            "prompt_experiment_id": f"ROUND_{round_number:02d}_PROMPT_EXPERIMENT",
            "parent_prompt_experiment_id": (
                "STYLE_ANALYSIS" if round_number == 1 else f"ROUND_{round_number - 1:02d}_PROMPT_EXPERIMENT"
            ),
            "retained_prior_findings": ["selective facial edges", "compact modeled nose"],
            "superseded_prior_findings": [],
            "excluded_style_noise": ["generic beautiful illustration", "repeated rendering synonyms"],
            "controlled_constants": ["same face", "same expression", "same crop"],
            "generation_contract": {
                "output_count": 1,
                "single_generator_call": True,
                "layout": "2X2",
                "panel_labels": ["A", "B", "C", "D"],
            },
            "full_quartet_prompt": (
                "Create one composite 2x2 calibration art in one generator call with panels A, B, C, and D. "
                "Keep the same face, expression, crop, light, and background across all panels; apply the four "
                "declared style hypotheses only inside their corresponding panels."
            ),
            "comparison_contract": {
                "baseline_label": "A",
                "target_fidelity": 100,
                "minimum_expected_delta_percent": 10,
            },
            "variants": [
                {
                    "label": chr(64 + index),
                    "role": roles[index - 1],
                    "expected_delta_from_baseline_percent": 0 if index == 1 else index * 10,
                    "full_style_prompt": self.style_prompt(round_number, chr(64 + index)),
                    "tested_language_elements": [f"term set {index}", f"construction {index}"],
                    "prompt_hypothesis": f"Prompt wording hypothesis {index}.",
                    "changes": [f"style route {index}"],
                    "diagnostic_negative_elements": ["hard continuous nose outline"] if index == 3 else [],
                    "prompt_focus": f"focus {index}",
                    "reference_route": f"route {index}",
                }
                for index in range(1, 5)
            ],
        }), encoding="utf-8")
        return path

    def make_quartet_art(self, folder: Path, round_number: int) -> Path:
        path = folder / f"r{round_number}_quartet_2x2.png"
        path.write_bytes(f"round={round_number};one-art;panels=A,B,C,D".encode())
        return path

    def make_distinction(self, folder: Path, round_number: int, *, passed: bool = True) -> Path:
        ids = [f"R{round_number:02d}_C{index:02d}" for index in range(1, 5)]
        pairs = []
        for left in range(4):
            for right in range(left + 1, 4):
                delta = 10
                if not passed and left == 0 and right == 1:
                    delta = 5
                pairs.append({
                    "left": ids[left],
                    "right": ids[right],
                    "visible_style_difference_percent": delta,
                    "observable_differences": ["visible controlled style difference"],
                })
        path = folder / f"distinction_{round_number}_{'pass' if passed else 'fail'}.json"
        path.write_text(json.dumps({
            "review_status": "PASS" if passed else "FAIL",
            "minimum_visible_delta_percent": 10 if passed else 5,
            "same_identity_constants": "PASS",
            "chaos_probe_visible": "PASS",
            "pairwise_checks": pairs,
            "collapsed_pairs": [] if passed else [[ids[0], ids[1]]],
            "decision": "SHOW_TO_USER" if passed else "REGENERATE",
        }), encoding="utf-8")
        return path

    def pass_distinction(self, state_path: Path, folder: Path, round_number: int):
        return calibration.record_distinction(
            state_path,
            round_number=round_number,
            report_path=self.make_distinction(folder, round_number),
        )

    def make_adaptation(self, folder: Path, round_number: int) -> Path:
        path = folder / f"adaptation_{round_number}.json"
        path.write_text(json.dumps({
            "observations": ["Candidate 4 preserved eye construction best."],
            "preserve": ["hard lash shape"],
            "change": ["reduce realistic skin gradients"],
            "drift_signals": ["polygonal concept-art planes"],
            "next_round_strategy": "Keep the best style route and test it on a new face.",
            "prompt_findings": {
                "variant_prompt_findings": [
                    {
                        "label": chr(64 + index),
                        "effective_elements": [f"effective term {index}"] if index != 3 else [],
                        "harmful_or_neutral_elements": ["chaos term"] if index == 3 else [],
                        "construction_effect": f"Visible language effect {index}.",
                        "next_action": f"Refine prompt hypothesis {index}.",
                    }
                    for index in range(1, 5)
                ],
                "cross_variant_findings": ["Term set A outperformed the chaos wording."],
                "retained_core_features": ["selective facial edges", "compact modeled nose"],
                "removed_or_deemphasized_elements": ["generic polish wording"],
                "next_prompt_experiment": {
                    "id": f"ROUND_{round_number + 1:02d}_PROMPT_EXPERIMENT",
                    "hypotheses": ["Test four new word and construction combinations."],
                },
            },
        }), encoding="utf-8")
        return path

    def make_conclusion(self, folder: Path, *, last_round: int = 4) -> Path:
        path = folder / "conclusion.json"
        final_prompt = self.style_prompt(last_round, "A") + " Final compact contour hierarchy."
        path.write_text(json.dumps({
            "summary": "The tested route generalizes across four identities.",
            "reliable_style_features": ["eye construction", "nose color blocks"],
            "identity_preservation_rules": ["keep face outline and spacing"],
            "prompt_strategy": ["edit the style base rather than average styles"],
            "reference_strategy": ["one style authority plus one identity crop"],
            "drift_rejection_rules": ["reject generic digital-art rendering"],
            "final_style_prompt": final_prompt,
            "prompt_vocabulary": {
                "preferred_terms": ["selective facial edges"],
                "conditional_synonyms": ["rendered comic"],
                "harmful_or_neutral_terms": ["generic polish"],
                "helpful_constructions": ["geometry before surface rendering"],
                "distracting_details": ["unverified decorative adjectives"],
            },
            "prompt_evidence_by_round": [
                {"round": number, "best_prompt_label": "A", "language_conclusion": f"Finding {number}"}
                for number in range(1, last_round + 1)
            ],
            "excluded_style_noise": ["generic digital art", "beautiful detailed illustration"],
            "recommended_fidelity": 90,
        }), encoding="utf-8")
        return path

    def test_default_is_four_rounds_of_one_art_with_four_panels(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            state_path = folder / "STYLE_CALIBRATION.json"
            state = calibration.create_state(
                state_path,
                calibration_id="aroma-v1",
                style_name="AROMA",
                style_analysis_path=self.make_style_analysis(folder),
                authorization_quote="Please calibrate this style.",
            )
            self.assertEqual(state["target_rounds"], 4)
            self.assertEqual(state["minimum_rounds"], 2)
            self.assertEqual(state["batch_size"], 4)
            self.assertEqual(state["arts_per_round"], 1)
            self.assertEqual(state["user_authorization"]["quote"], "Please calibrate this style.")

    def test_start_requires_explicit_authorization_quote(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            with self.assertRaisesRegex(calibration.StyleCalibrationError, "explicit user request or consent"):
                calibration.create_state(
                    folder / "state.json", calibration_id="no-consent", style_name="AROMA",
                    style_analysis_path=self.make_style_analysis(folder),
                )

    def test_composite_art_is_archived_byte_identically_without_losing_working_path(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            state_path = folder / "state.json"
            calibration.create_state(
                state_path, calibration_id="archive-check", style_name="AROMA",
                style_analysis_path=self.make_style_analysis(folder),
                authorization_quote="Please calibrate this style.",
            )
            calibration.open_round(
                state_path, face_id="temporary-face", face_description="A temporary test subject.",
                strategy_path=self.make_strategy(folder),
            )
            original = self.make_quartet_art(folder, 1)
            first = calibration.record_quartet_art(state_path, round_number=1, image_path=original)
            archive_path = Path(first["rounds"][0]["quartet_art_archive_path"])
            original_path = first["rounds"][0]["quartet_art_path"]
            self.assertEqual(original_path, str(original))
            self.assertTrue(original.is_file())
            self.assertTrue(archive_path.is_file())
            self.assertEqual(archive_path.read_bytes(), original.read_bytes())
            self.assertEqual(first["rounds"][0]["quartet_art_archive_sha256"], calibration.sha256(original))

    def test_reduced_round_target_requires_user_approval(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            with self.assertRaises(calibration.StyleCalibrationError):
                calibration.create_state(
                    folder / "blocked.json",
                    calibration_id="short",
                    style_name="AROMA",
                    style_analysis_path=self.make_style_analysis(folder),
                    authorization_quote="Please calibrate this style.",
                    target_rounds=2,
                )
            state = calibration.create_state(
                folder / "approved.json",
                calibration_id="short",
                style_name="AROMA",
                style_analysis_path=self.make_style_analysis(folder),
                authorization_quote="Please calibrate this style.",
                target_rounds=2,
                user_approved_reduced_rounds=True,
            )
            self.assertEqual(state["target_rounds"], 2)

    def test_active_legacy_protocol_cannot_continue_with_four_outputs(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            state_path = folder / "legacy-active.json"
            calibration.create_state(
                state_path,
                calibration_id="legacy-active",
                style_name="AROMA",
                style_analysis_path=self.make_style_analysis(folder),
                authorization_quote="Please calibrate this style.",
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["protocol_version"] = 2
            state.pop("arts_per_round", None)
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(calibration.StyleCalibrationError, "cannot continue"):
                calibration.open_round(
                    state_path,
                    face_id="subject-a",
                    face_description="Temporary subject A",
                    strategy_path=self.make_strategy(folder),
                )

    def test_style_analysis_is_required_before_first_round(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            invalid = folder / "invalid_analysis.json"
            invalid.write_text(json.dumps({"source_scope": "guessed label"}), encoding="utf-8")
            with self.assertRaisesRegex(calibration.StyleCalibrationError, "missing required fields"):
                calibration.create_state(
                    folder / "state.json",
                    calibration_id="test",
                    style_name="AROMA",
                    style_analysis_path=invalid,
                    authorization_quote="Please calibrate this style.",
                )

    def test_round_requires_one_composite_art_and_rejects_four_files(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            state_path = folder / "state.json"
            calibration.create_state(
                state_path, calibration_id="test", style_name="AROMA",
                style_analysis_path=self.make_style_analysis(folder),
                authorization_quote="Please calibrate this style.",
            )
            calibration.open_round(
                state_path,
                face_id="face-a",
                face_description="Adult face A",
                strategy_path=self.make_strategy(folder),
            )
            with self.assertRaises(calibration.StyleCalibrationError):
                calibration.record_candidates(
                    state_path,
                    round_number=1,
                    assignments=[f"R01_C01={self.make_quartet_art(folder, 1)}"],
                )
            state = calibration.record_quartet_art(
                state_path,
                round_number=1,
                image_path=self.make_quartet_art(folder, 1),
            )
            self.assertEqual(state["next_required_action"], "RECORD_DISTINCTION_QA")
            self.assertEqual(state["rounds"][0]["quartet_art_path"], str(self.make_quartet_art(folder, 1).resolve()))
            self.assertEqual(
                len({item["image_path"] for item in state["rounds"][0]["candidates"]}), 1
            )
            state = self.pass_distinction(state_path, folder, 1)
            self.assertEqual(state["next_required_action"], "SUBMIT_USER_FEEDBACK")

    def test_high_fidelity_strategy_requires_ten_percent_and_chaos_probe(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            state_path = folder / "state.json"
            calibration.create_state(
                state_path, calibration_id="test", style_name="AROMA",
                style_analysis_path=self.make_style_analysis(folder),
                authorization_quote="Please calibrate this style.",
            )
            strategy = json.loads(self.make_strategy(folder).read_text(encoding="utf-8"))
            strategy["comparison_contract"]["minimum_expected_delta_percent"] = 5
            bad_delta = folder / "bad_delta.json"
            bad_delta.write_text(json.dumps(strategy), encoding="utf-8")
            with self.assertRaisesRegex(calibration.StyleCalibrationError, "at least 10"):
                calibration.open_round(
                    state_path, face_id="face-a", face_description="Adult face A", strategy_path=bad_delta
                )
            strategy = json.loads(self.make_strategy(folder).read_text(encoding="utf-8"))
            strategy["variants"][2]["role"] = "ALTERNATIVE"
            no_chaos = folder / "no_chaos.json"
            no_chaos.write_text(json.dumps(strategy), encoding="utf-8")
            with self.assertRaisesRegex(calibration.StyleCalibrationError, "CHAOS_PROBE"):
                calibration.open_round(
                    state_path, face_id="face-a", face_description="Adult face A", strategy_path=no_chaos
                )
            strategy = json.loads(self.make_strategy(folder).read_text(encoding="utf-8"))
            strategy["variants"][0]["full_style_prompt"] = "style " * 300
            bloated = folder / "bloated_prompt.json"
            bloated.write_text(json.dumps(strategy), encoding="utf-8")
            with self.assertRaisesRegex(calibration.StyleCalibrationError, "remove non-essential wording"):
                calibration.open_round(
                    state_path, face_id="face-a", face_description="Adult face A", strategy_path=bloated
                )
            strategy = json.loads(self.make_strategy(folder).read_text(encoding="utf-8"))
            strategy["variants"][1]["full_style_prompt"] = strategy["variants"][0]["full_style_prompt"]
            duplicate_prompts = folder / "duplicate_prompts.json"
            duplicate_prompts.write_text(json.dumps(strategy), encoding="utf-8")
            with self.assertRaisesRegex(calibration.StyleCalibrationError, "distinct full_style_prompt"):
                calibration.open_round(
                    state_path,
                    face_id="face-a",
                    face_description="Adult face A",
                    strategy_path=duplicate_prompts,
                )

    def test_collapsed_quartet_is_hidden_and_must_be_regenerated(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            state_path = folder / "state.json"
            calibration.create_state(
                state_path, calibration_id="test", style_name="AROMA",
                style_analysis_path=self.make_style_analysis(folder),
                authorization_quote="Please calibrate this style.",
            )
            calibration.open_round(
                state_path,
                face_id="face-a",
                face_description="Adult face A",
                strategy_path=self.make_strategy(folder),
            )
            calibration.record_quartet_art(
                state_path, round_number=1, image_path=self.make_quartet_art(folder, 1)
            )
            with self.assertRaises(calibration.StyleCalibrationError):
                calibration.submit_feedback(
                    state_path,
                    round_number=1,
                    mode="MIN_TO_MAX",
                    ranking="R01_C01,R01_C02,R01_C03,R01_C04",
                )
            state = calibration.record_distinction(
                state_path,
                round_number=1,
                report_path=self.make_distinction(folder, 1, passed=False),
            )
            self.assertEqual(state["next_required_action"], "RECORD_CANDIDATES")
            self.assertEqual(len(state["rounds"][0]["rejected_candidate_sets"]), 1)
            self.assertTrue(all(not item["image_path"] for item in state["rounds"][0]["candidates"]))
            self.assertFalse(state["rounds"][0]["quartet_art_path"])

    def test_new_round_requires_new_face_and_ai_adaptation(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            state_path = folder / "state.json"
            calibration.create_state(
                state_path, calibration_id="test", style_name="AROMA",
                style_analysis_path=self.make_style_analysis(folder),
                authorization_quote="Please calibrate this style.",
            )
            calibration.open_round(
                state_path,
                face_id="face-a",
                face_description="Adult face A",
                strategy_path=self.make_strategy(folder),
            )
            calibration.record_quartet_art(
                state_path, round_number=1, image_path=self.make_quartet_art(folder, 1)
            )
            self.pass_distinction(state_path, folder, 1)
            calibration.submit_feedback(
                state_path,
                round_number=1,
                mode="MIN_TO_MAX",
                ranking="R01_C01,R01_C02,R01_C03,R01_C04",
            )
            with self.assertRaises(calibration.StyleCalibrationError):
                calibration.open_round(
                    state_path,
                    face_id="face-b",
                    face_description="Adult face B",
                    strategy_path=self.make_strategy(folder, "strategy2.json", round_number=2),
                )
            calibration.record_adaptation(
                state_path,
                round_number=1,
                analysis_path=self.make_adaptation(folder, 1),
            )
            with self.assertRaisesRegex(calibration.StyleCalibrationError, "proposed next prompt experiment id"):
                calibration.open_round(
                    state_path,
                    face_id="face-b",
                    face_description="Adult face B",
                    strategy_path=self.make_strategy(folder, "stale_prompt.json", round_number=1),
                )
            missing_prior = json.loads(
                self.make_strategy(folder, "missing_prior_source.json", round_number=2).read_text(encoding="utf-8")
            )
            missing_prior["retained_prior_findings"] = ["selective facial edges"]
            missing_prior_path = folder / "missing_prior.json"
            missing_prior_path.write_text(json.dumps(missing_prior), encoding="utf-8")
            with self.assertRaisesRegex(calibration.StyleCalibrationError, "cannot be dropped"):
                calibration.open_round(
                    state_path,
                    face_id="face-b",
                    face_description="Adult face B",
                    strategy_path=missing_prior_path,
                )
            with self.assertRaises(calibration.StyleCalibrationError):
                calibration.open_round(
                    state_path,
                    face_id="face-a",
                    face_description="Repeated face A",
                    strategy_path=self.make_strategy(folder, "strategy2.json", round_number=2),
                )
            state = calibration.open_round(
                state_path,
                face_id="face-b",
                face_description="Adult face B",
                strategy_path=self.make_strategy(folder, "strategy2.json", round_number=2),
            )
            self.assertEqual(state["rounds"][1]["face_id"], "FACE_B")

    def test_percentage_feedback_and_early_stop_require_direct_flag(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            state_path = folder / "state.json"
            calibration.create_state(
                state_path, calibration_id="test", style_name="AROMA",
                style_analysis_path=self.make_style_analysis(folder),
                authorization_quote="Please calibrate this style.",
            )
            for round_number, face in ((1, "face-a"), (2, "face-b")):
                calibration.open_round(
                    state_path,
                    face_id=face,
                    face_description=f"Adult {face}",
                    strategy_path=self.make_strategy(
                        folder, f"strategy{round_number}.json", round_number=round_number
                    ),
                )
                calibration.record_quartet_art(
                    state_path,
                    round_number=round_number,
                    image_path=self.make_quartet_art(folder, round_number),
                )
                self.pass_distinction(state_path, folder, round_number)
                calibration.submit_feedback(
                    state_path,
                    round_number=round_number,
                    mode="PERCENT",
                    scores=[
                        f"R{round_number:02d}_C01=10",
                        f"R{round_number:02d}_C02=40",
                        f"R{round_number:02d}_C03=75",
                        f"R{round_number:02d}_C04=95",
                    ],
                )
                if round_number == 1:
                    calibration.record_adaptation(
                        state_path,
                        round_number=1,
                        analysis_path=self.make_adaptation(folder, 1),
                    )
            with self.assertRaises(calibration.StyleCalibrationError):
                calibration.finalize(state_path, conclusion_path=self.make_conclusion(folder, last_round=2))
            state = calibration.finalize(
                state_path,
                conclusion_path=self.make_conclusion(folder, last_round=2),
                user_approved_early_stop=True,
            )
            self.assertEqual(state["status"], "FINALIZED")
            self.assertTrue(state["final_conclusion"]["early_stop"])

    def test_complete_four_by_four_cycle(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            state_path = folder / "state.json"
            calibration.create_state(
                state_path, calibration_id="test", style_name="AROMA",
                style_analysis_path=self.make_style_analysis(folder),
                authorization_quote="Please calibrate this style.",
            )
            for round_number in range(1, 5):
                calibration.open_round(
                    state_path,
                    face_id=f"face-{round_number}",
                    face_description=f"New adult face {round_number}",
                    strategy_path=self.make_strategy(
                        folder, f"strategy{round_number}.json", round_number=round_number
                    ),
                )
                calibration.record_quartet_art(
                    state_path,
                    round_number=round_number,
                    image_path=self.make_quartet_art(folder, round_number),
                )
                self.pass_distinction(state_path, folder, round_number)
                calibration.submit_feedback(
                    state_path,
                    round_number=round_number,
                    mode="MIN_TO_MAX",
                    ranking=",".join(f"R{round_number:02d}_C{index:02d}" for index in range(1, 5)),
                )
                if round_number < 4:
                    calibration.record_adaptation(
                        state_path,
                        round_number=round_number,
                        analysis_path=self.make_adaptation(folder, round_number),
                    )
            state = calibration.finalize(state_path, conclusion_path=self.make_conclusion(folder))
            self.assertEqual(state["status"], "FINALIZED")
            self.assertEqual(state["final_conclusion"]["completed_rounds"], 4)
            self.assertEqual(len(state["final_conclusion"]["round_summary"]), 4)

    def test_apply_to_style_preserves_explicit_score_semantics(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            state_path = folder / "state.json"
            calibration.create_state(
                state_path,
                calibration_id="test-apply",
                style_name="AROMA",
                style_analysis_path=self.make_style_analysis(folder),
                authorization_quote="Please calibrate this style.",
                target_rounds=2,
                user_approved_reduced_rounds=True,
            )
            for round_number in (1, 2):
                calibration.open_round(
                    state_path,
                    face_id=f"face-{round_number}",
                    face_description=f"Adult face {round_number}",
                    strategy_path=self.make_strategy(
                        folder, f"strategy{round_number}.json", round_number=round_number
                    ),
                )
                calibration.record_quartet_art(
                    state_path, round_number=round_number, image_path=self.make_quartet_art(folder, round_number)
                )
                self.pass_distinction(state_path, folder, round_number)
                calibration.submit_feedback(
                    state_path,
                    round_number=round_number,
                    mode="PERCENT",
                    scores=[
                        f"R{round_number:02d}_C01=92",
                        f"R{round_number:02d}_C02=85",
                        f"R{round_number:02d}_C03=70",
                        f"R{round_number:02d}_C04=80",
                    ],
                )
                if round_number == 1:
                    calibration.record_adaptation(
                        state_path, round_number=1, analysis_path=self.make_adaptation(folder, 1)
                    )
            calibration.finalize(state_path, conclusion_path=self.make_conclusion(folder, last_round=2))
            pack = folder / "AROMA_PROJECT_PACK"
            pack.mkdir()
            (pack / ".style-pack.json").write_text(json.dumps({"style_name": "AROMA"}), encoding="utf-8")
            applied = calibration.apply_to_style(state_path, style_pack=pack)
            application = applied["style_application"]
            self.assertEqual(application["maximum_explicit_user_percentage"], 92)
            self.assertFalse(application["user_confirmed_100"])
            self.assertTrue(Path(applied["active_calibration_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
