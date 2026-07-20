from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import hashlib
import json
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


risk = load_module("generation_risk_assessor", ROOT / "tools" / "generation_risk_assessor.py")
manager = load_module("style_pack_manager", ROOT / "tools" / "style_pack_manager.py")
LEXICON = risk.load_lexicon(ROOT / "config" / "generation_risk_lexicon.json")


class PromptRiskTests(unittest.TestCase):
    def test_safe_prompt_is_d1(self):
        result = risk.evaluate_prompt(
            "Взрослый персонаж 30 лет, нейтральная каталожная поза, полностью непрозрачная одежда.",
            LEXICON,
        )
        self.assertEqual(result["risk"], "D1")

    def test_youth_alone_is_contextual_not_automatic_d10(self):
        result = risk.evaluate_prompt("Подросток читает книгу в библиотеке.", LEXICON)
        self.assertEqual(result["risk"], "D2")

    def test_youth_plus_sexualization_is_hard_d10(self):
        result = risk.evaluate_prompt("Young-looking teen in extreme micro bikini.", LEXICON)
        self.assertEqual(result["risk"], "D10")
        self.assertTrue(result["hard_rules"])

    def test_explicit_request_is_not_reduced_by_safe_words(self):
        result = risk.evaluate_prompt(
            "Adult character, non-sexual context, fully opaque, but explicit nudity is visible.",
            LEXICON,
        )
        self.assertGreaterEqual(result["score"], 9)
        self.assertTrue(any(not item["applied"] for item in result["modifiers"] if int(item["delta"][:-1]) < 0))

    def test_two_d6_references_add_synergy(self):
        score, modifiers = risk.combined_score(2, [
            {"effective_risk": "D6"},
            {"effective_risk": "D7"},
        ])
        self.assertEqual(score, 8)
        self.assertEqual(modifiers[0]["delta"], "+1D")

    def test_reference_label_ends_with_d_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "reference.png"
            image.write_bytes(b"test-image")
            parsed = risk.parse_reference_spec(f"{image}::D4::+1D::Композиционный риск")
        self.assertTrue(parsed["label"].endswith("[D4]"))
        self.assertEqual(parsed["effective_risk"], "D5")


class StartupMenuTests(unittest.TestCase):
    def make_args(self, **updates):
        values = {
            "style_name": "SAMPLE",
            "startup_selection_mode": "NEW",
            "reuse_startup_from": "",
            "startup_menu_surface": "NATIVE_CONTEXT_MENU",
            "user_requested_reselection": False,
            "startup_choice": "OPTION_1",
            "startup_choice_user_quote": "Первый вариант",
            "startup_option": [
                "OPTION_1=Рекомендуемый: 90% и подключить BODY_REFERENCE_LIBRARY",
                "OPTION_2=Контекстный мягкий вариант",
                "OPTION_3=Контекстный строгий вариант",
            ],
            "custom_parameters_user_quote": "",
            "fidelity": 90,
            "aux_body_decision": "SELECTED",
        }
        values.update(updates)
        return Namespace(**values)

    def test_default_is_90_and_library_selected(self):
        result = manager.parse_startup_interaction(self.make_args())
        self.assertEqual(result["selected"], "OPTION_1")
        self.assertTrue(result["options"][0]["recommended"])

    def test_default_rejects_wrong_fidelity(self):
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(fidelity=70))

    def test_default_rejects_disabled_library_wording(self):
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(startup_option=[
                "OPTION_1=90% без BODY_REFERENCE_LIBRARY",
                "OPTION_2=Контекстный мягкий вариант",
                "OPTION_3=Контекстный строгий вариант",
            ]))

    def test_custom_requires_missing_values_quote(self):
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(startup_choice="CUSTOM"))

    def test_complete_custom_description_needs_no_optional_followup(self):
        result = manager.parse_startup_interaction(self.make_args(
            startup_choice="CUSTOM",
            startup_choice_user_quote="Указать свой вариант",
            custom_parameters_user_quote="90%, библиотеку подключить, портрет 9:16, полный рост, нейтральная поза",
        ))
        self.assertTrue(result["custom_description_treated_as_complete"])
        self.assertTrue(result["optional_follow_up_questions_forbidden"])

    def test_same_chat_reuse_does_not_present_menu_again(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "REFERENCE_PLAN.json"
            original = manager.parse_startup_interaction(self.make_args())
            original["resolved_parameters"] = {
                "fidelity": 90,
                "aux_body_decision": "SELECTED",
            }
            source.write_text(json.dumps({
                "schema_version": 5,
                "style_name": "SAMPLE",
                "startup_parameter_selection": original,
            }), encoding="utf-8")
            reused = manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="REUSE",
                reuse_startup_from=str(source),
                startup_choice="",
                startup_choice_user_quote="",
                startup_option=[],
            ))
            self.assertEqual(reused["selection_state"], "REUSED_IN_SAME_CHAT")
            self.assertFalse(reused["menu_presented_this_turn"])
            self.assertEqual(reused["menu_surface_this_turn"], "NOT_PRESENTED_REUSED_SELECTION")

    def test_reuse_rejects_silent_profile_change(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "REFERENCE_PLAN.json"
            original = manager.parse_startup_interaction(self.make_args())
            original["resolved_parameters"] = {
                "fidelity": 90,
                "aux_body_decision": "SELECTED",
            }
            source.write_text(json.dumps({
                "schema_version": 5,
                "style_name": "SAMPLE",
                "startup_parameter_selection": original,
            }), encoding="utf-8")
            with self.assertRaises(manager.StylePackError):
                manager.parse_startup_interaction(self.make_args(
                    startup_selection_mode="REUSE",
                    reuse_startup_from=str(source),
                    fidelity=70,
                ))

    def test_native_unavailable_requires_explicit_user_confirmation(self):
        result = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="USER_CONFIRMATION",
            startup_menu_surface="USER_REPLY_AFTER_NATIVE_UNAVAILABLE",
            startup_choice_user_quote="Делай рекомендуемый вариант",
            startup_option=[],
        ))
        self.assertEqual(result["selection_state"], "USER_CONFIRMED_AFTER_NATIVE_UNAVAILABLE")
        self.assertEqual(result["selected"], "OPTION_1")
        self.assertFalse(result["menu_presented_this_turn"])
        self.assertEqual(result["options"], [])

    def test_auto_default_is_forbidden(self):
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="AUTO_DEFAULT",
            ))

    def test_user_confirmation_requires_explicit_reply(self):
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="USER_CONFIRMATION",
                startup_menu_surface="USER_REPLY_AFTER_NATIVE_UNAVAILABLE",
                startup_choice_user_quote="",
                startup_option=[],
            ))

    def test_risk_gate_requires_every_selected_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "style.png"
            image.write_bytes(b"style")
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            report = root / "risk.json"
            report.write_text(json.dumps({
                "schema_version": 1,
                "generation_risk": "D3",
                "original_prompt": {"risk": "D2"},
                "revised_prompt": None,
                "references": [{
                    "sha256": digest,
                    "content_and_reference_risk": "D3",
                }],
            }), encoding="utf-8")
            path, loaded = manager.load_and_validate_risk_assessment(
                str(report),
                {"style": [{"path": str(image), "sha256": digest}]},
                [],
            )
            self.assertEqual(path, report.resolve())
            self.assertEqual(loaded["generation_risk"], "D3")

    def test_risk_gate_rejects_unassessed_selected_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "style.png"
            image.write_bytes(b"style")
            report = root / "risk.json"
            report.write_text(json.dumps({
                "schema_version": 1,
                "generation_risk": "D2",
                "original_prompt": {"risk": "D2"},
                "revised_prompt": None,
                "references": [],
            }), encoding="utf-8")
            with self.assertRaises(manager.StylePackError):
                manager.load_and_validate_risk_assessment(
                    str(report),
                    {"style": [{"path": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}]},
                    [],
                )


if __name__ == "__main__":
    unittest.main()
