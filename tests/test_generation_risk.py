from __future__ import annotations

import importlib.util
import csv
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
            "startup_choice_user_quote": "OPTION_1",
            "startup_option": [
                "OPTION_1=Recommended 90% fidelity; select BODY_REFERENCE_LIBRARY",
                "OPTION_2=Contextual 90% fidelity; decline BODY_REFERENCE_LIBRARY",
                "OPTION_3=Contextual 70% fidelity; decline BODY_REFERENCE_LIBRARY",
            ],
            "confirmed_chat_id": "chat-1",
            "confirmed_message_id": "msg-1",
            "confirmed_body_library_user_quote": "",
            "confirmed_body_library_chat_id": "",
            "confirmed_body_library_message_id": "",
            "custom_parameters_user_quote": "",
            "fidelity": 90,
            "aux_body_decision": "SELECTED",
        }
        values.update(updates)
        return Namespace(**values)

    def test_option_one_records_its_confirmed_profile(self):
        result = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="USER_CONFIRMATION",
            startup_menu_surface="TEXT_NUMBERED_MENU",
            startup_choice_user_quote="1",
        ))
        self.assertEqual(result["selected"], "OPTION_1")
        self.assertTrue(result["options"][0]["recommended"])
        self.assertEqual(result["resolved_parameters"], {"fidelity": 90, "aux_body_decision": "SELECTED"})
        self.assertEqual(manager.validate_startup_profile_evidence(result), (90, "SELECTED"))
        self.assertTrue(result["profile_confirmation_complete"])
        self.assertTrue(result["same_profile_reconfirmation_forbidden"])
        self.assertTrue(result["style_confirmation_complete"])
        self.assertEqual(result["confirmed_style_name"], "SAMPLE")

    def test_unformed_master_candidate_is_request_local_only_and_requires_profile_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = manager.make_paths(Path(folder), "SAMPLE")
            paths.pack.mkdir(parents=True)
            metadata_path = paths.pack / ".style-pack.json"
            metadata_path.write_text(json.dumps({"status": "REVIEW_REQUIRED"}), encoding="utf-8")
            candidate = paths.pack / "01_WORK" / "STYLE_CROPS" / "MASTER_STYLE_fixture.png"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"fixture-image")
            paths.references.parent.mkdir(parents=True, exist_ok=True)
            with paths.references.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("stored_relative_path", "primary_role", "status", "generator_safe"))
                writer.writeheader()
                writer.writerow({"stored_relative_path": "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png", "primary_role": "MASTER_STYLE", "status": "TEST", "generator_safe": "YES"})

            with self.assertRaisesRegex(manager.StylePackError, "unformed style-pack source"):
                manager.validate_plan_reference(paths, str(candidate), "STYLE")
            with self.assertRaisesRegex(manager.StylePackError, "unformed style-pack source"):
                manager.validate_plan_reference(
                    paths, str(candidate), "FACE", allow_request_local_style_candidate=True
                )

            off_role = paths.pack / "01_WORK" / "FACE_CROPS" / "MASTER_STYLE_fixture.png"
            off_role.parent.mkdir(parents=True)
            off_role.write_bytes(b"fixture-image")
            with self.assertRaisesRegex(manager.StylePackError, "unformed style-pack source"):
                manager.validate_plan_reference(
                    paths, str(off_role), "STYLE", allow_request_local_style_candidate=True
                )

            selected = manager.validate_plan_reference(
                paths, str(candidate), "STYLE", allow_request_local_style_candidate=True
            )
            self.assertEqual(selected["status"], "REQUEST_LOCAL_STYLE_CANDIDATE")
            self.assertEqual(selected["reference_scope"], "CURRENT_REQUEST_ONLY")
            self.assertFalse(selected["permanent_anchor"])
            with paths.references.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("stored_relative_path", "primary_role", "status", "generator_safe"))
                writer.writeheader()
                writer.writerow({"stored_relative_path": "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png", "primary_role": "MASTER_STYLE", "status": "REJECTED", "generator_safe": "YES"})
            with self.assertRaisesRegex(manager.StylePackError, "unformed style-pack source"):
                manager.validate_plan_reference(paths, str(candidate), "STYLE", allow_request_local_style_candidate=True)
            with paths.references.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("stored_relative_path", "primary_role", "status", "generator_safe"))
                writer.writeheader()
                positive_row = {"stored_relative_path": "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png", "primary_role": "MASTER_STYLE", "status": "TEST", "generator_safe": "YES"}
                writer.writerow(positive_row)
                writer.writerow(positive_row)
            self.assertFalse(manager.has_positive_master_style_manifest_entry(paths, candidate.relative_to(paths.pack)))
            self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8"))["status"], "REVIEW_REQUIRED")

    def test_option_one_cannot_silently_override_numeric_or_library_profile(self):
        with self.assertRaisesRegex(manager.StylePackError, "must match it"):
            manager.parse_startup_interaction(self.make_args(fidelity=70))

    def test_option_one_requires_its_explicit_library_profile(self):
        with self.assertRaisesRegex(manager.StylePackError, "must match it"):
            manager.parse_startup_interaction(self.make_args(aux_body_decision="NOT_SELECTED"))

    def test_custom_requires_missing_values_quote(self):
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(startup_choice="CUSTOM", startup_choice_user_quote="CUSTOM"))

    def test_complete_custom_description_needs_no_optional_followup(self):
        result = manager.parse_startup_interaction(self.make_args(
            startup_choice="CUSTOM",
            startup_choice_user_quote="CUSTOM",
            custom_parameters_user_quote="90%, библиотеку подключить, портрет 9:16, полный рост, нейтральная поза",
        ))
        self.assertTrue(result["custom_description_treated_as_complete"])
        self.assertTrue(result["optional_follow_up_questions_forbidden"])

    def test_same_chat_reuse_does_not_present_menu_again(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "REFERENCE_PLAN.json"
            original = manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="DIRECT_CONFIRMATION",
                confirmed_chat_id="chat-1",
                confirmed_message_id="msg-1",
                confirmed_parameters_user_quote="Keep 90% fidelity and select BODY_REFERENCE_LIBRARY.",
            ))
            original["resolved_parameters"] = {"fidelity": 90, "aux_body_decision": "SELECTED"}
            source.write_text(json.dumps({
                "schema_version": 5,
                "style_name": "SAMPLE",
                "startup_parameter_selection": original,
            }), encoding="utf-8")
            reused = manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="REUSE",
                reuse_startup_from=str(source),
                reuse_chat_id="chat-1",
                reuse_message_id="msg-1",
                fidelity=90,
                aux_body_decision="SELECTED",
                startup_choice="",
                startup_choice_user_quote="",
                startup_option=[],
            ))
            self.assertEqual(reused["selection_state"], "REUSED_IN_SAME_CHAT")
            self.assertFalse(reused["menu_presented_this_turn"])
            self.assertEqual(reused["menu_surface_this_turn"], "NOT_PRESENTED_REUSED_SELECTION")

    def test_menu_reuse_profile_change_uses_direct_correction_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "REFERENCE_PLAN.json"
            original = manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="USER_CONFIRMATION", startup_menu_surface="TEXT_NUMBERED_MENU",
                startup_choice="OPTION_2", startup_choice_user_quote="2", fidelity=90,
                aux_body_decision="DECLINED",
            ))
            source.write_text(json.dumps({
                "schema_version": 6, "style_name": "SAMPLE", "startup_parameter_selection": original,
            }), encoding="utf-8")
            reused = manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="REUSE", reuse_startup_from=str(source),
                reuse_chat_id="chat-1", reuse_message_id="msg-1", fidelity=70,
                aux_body_decision="DECLINED", user_requested_reselection=True,
                confirmed_chat_id="chat-1", confirmed_message_id="msg-correction",
                confirmed_parameters_user_quote="Use 70% fidelity and decline BODY_REFERENCE_LIBRARY.",
            ))
            self.assertEqual(reused["selection_state"], "REUSED_WITH_USER_RESELECTION")
            self.assertEqual(reused["selected"], "DIRECT_CONFIRMED")
            self.assertEqual(manager.validate_startup_profile_evidence(reused), (70, "DECLINED"))

    def test_reuse_rejects_silent_profile_change(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "REFERENCE_PLAN.json"
            original = manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="DIRECT_CONFIRMATION",
                confirmed_chat_id="chat-1",
                confirmed_message_id="msg-1",
                confirmed_parameters_user_quote="Keep 90% fidelity and select BODY_REFERENCE_LIBRARY.",
            ))
            original["resolved_parameters"] = {"fidelity": 90, "aux_body_decision": "SELECTED"}
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
                    reuse_chat_id="chat-1",
                    reuse_message_id="msg-1",
                    aux_body_decision="SELECTED",
                ))

    def test_direct_current_chat_confirmation_needs_no_preset(self):
        result = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="DIRECT_CONFIRMATION",
            confirmed_chat_id="chat-7",
            confirmed_message_id="msg-12",
            confirmed_parameters_user_quote="Use 70% fidelity and decline BODY_REFERENCE_LIBRARY.",
            fidelity=70,
            aux_body_decision="DECLINED",
            startup_choice="",
            startup_choice_user_quote="",
            startup_option=[],
        ))
        self.assertEqual(result["selection_state"], "DIRECT_PARAMETERS_CONFIRMED")
        self.assertEqual(result["confirmed_provenance"]["chat_id"], "chat-7")
        self.assertEqual(result["resolved_parameters"]["aux_body_decision"], "DECLINED")

    def test_direct_confirmation_reports_only_missing_provenance(self):
        missing = manager.startup_clarification_requirements(self.make_args(
            startup_selection_mode="DIRECT_CONFIRMATION",
            confirmed_chat_id="chat-7",
            confirmed_message_id="",
            confirmed_parameters_user_quote="",
        ))
        self.assertEqual(missing, ["message_id", "body_library_confirmation", "fidelity_quote"])

    def test_reuse_requires_matching_source_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "REFERENCE_PLAN.json"
            original = manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="DIRECT_CONFIRMATION",
                confirmed_chat_id="chat-1",
                confirmed_message_id="msg-1",
                confirmed_parameters_user_quote="Keep 90% fidelity and select BODY_REFERENCE_LIBRARY.",
            ))
            original["resolved_parameters"] = {"fidelity": 90, "aux_body_decision": "SELECTED"}
            source.write_text(json.dumps({"schema_version": 5, "style_name": "SAMPLE", "startup_parameter_selection": original}), encoding="utf-8")
            with self.assertRaises(manager.StylePackError):
                manager.parse_startup_interaction(self.make_args(
                    startup_selection_mode="REUSE", reuse_startup_from=str(source),
                    reuse_chat_id="different-chat", reuse_message_id="msg-1",
                    aux_body_decision="SELECTED",
                ))

    def test_default_mode_text_menu_records_all_options(self):
        result = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="USER_CONFIRMATION",
            startup_menu_surface="TEXT_NUMBERED_MENU",
            startup_choice_user_quote="1",
        ))
        self.assertEqual(result["selection_state"], "USER_CONFIRMED_FROM_TEXT_MENU")
        self.assertEqual(result["selected"], "OPTION_1")
        self.assertTrue(result["menu_presented_this_turn"])
        self.assertEqual([item["id"] for item in result["options"]], [*manager.STARTUP_CHOICES])

    def test_default_mode_text_menu_accepts_contextual_option(self):
        result = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="USER_CONFIRMATION",
            startup_menu_surface="TEXT_NUMBERED_MENU",
            startup_choice="OPTION_2",
            startup_choice_user_quote="2",
            fidelity=90,
            aux_body_decision="DECLINED",
        ))
        self.assertEqual(result["selected"], "OPTION_2")
        self.assertEqual(result["menu_surface_this_turn"], "TEXT_NUMBERED_MENU")

    def test_option_three_uses_its_contextual_displayed_profile(self):
        result = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="USER_CONFIRMATION",
            startup_menu_surface="TEXT_NUMBERED_MENU",
            startup_option=[
                "OPTION_1=Recommended 90% fidelity; select BODY_REFERENCE_LIBRARY",
                "OPTION_2=Contextual 90% fidelity; decline BODY_REFERENCE_LIBRARY",
                "OPTION_3=Contextual 70% fidelity; decline BODY_REFERENCE_LIBRARY",
            ],
            startup_choice="OPTION_3", startup_choice_user_quote="3",
            fidelity=70, aux_body_decision="DECLINED",
        ))
        self.assertEqual(result["resolved_parameters"], {"fidelity": 70, "aux_body_decision": "DECLINED"})

    def test_saved_menu_evidence_accepts_literal_choice_and_rejects_tampering(self):
        saved = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="USER_CONFIRMATION", startup_menu_surface="TEXT_NUMBERED_MENU",
            startup_choice="OPTION_2", startup_choice_user_quote="2", fidelity=90,
            aux_body_decision="DECLINED",
        ))
        self.assertEqual(manager.validate_startup_profile_evidence(saved), (90, "DECLINED"))
        wrong_choice = json.loads(json.dumps(saved))
        wrong_choice["selected"] = "OPTION_3"
        with self.assertRaises(manager.StylePackError):
            manager.validate_startup_profile_evidence(wrong_choice)
        wrong_profile = json.loads(json.dumps(saved))
        wrong_profile["resolved_parameters"]["fidelity"] = 70
        with self.assertRaises(manager.StylePackError):
            manager.validate_startup_profile_evidence(wrong_profile)
        wrong_quote = json.loads(json.dumps(saved))
        wrong_quote["user_choice_quote"] = "3"
        with self.assertRaises(manager.StylePackError):
            manager.validate_startup_profile_evidence(wrong_quote)

    def test_saved_custom_menu_evidence_uses_full_profile_quote(self):
        saved = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="USER_CONFIRMATION", startup_menu_surface="TEXT_NUMBERED_MENU",
            startup_choice="CUSTOM", startup_choice_user_quote="4", fidelity=70,
            aux_body_decision="DECLINED",
            custom_parameters_user_quote="Use 70% fidelity and decline BODY_REFERENCE_LIBRARY.",
        ))
        self.assertEqual(manager.validate_startup_profile_evidence(saved), (70, "DECLINED"))

    def test_custom_accepts_natural_complete_quote_but_rejects_incomplete_or_mismatched(self):
        natural = "Use 70% fidelity and decline BODY_REFERENCE_LIBRARY."
        saved = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="USER_CONFIRMATION", startup_menu_surface="TEXT_NUMBERED_MENU",
            startup_choice="CUSTOM", startup_choice_user_quote=natural, fidelity=70,
            aux_body_decision="DECLINED",
        ))
        self.assertEqual(saved["user_choice_quote"], natural)
        self.assertEqual(saved["custom_parameters_user_quote"], natural)
        self.assertEqual(manager.validate_startup_profile_evidence(saved), (70, "DECLINED"))
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="USER_CONFIRMATION", startup_menu_surface="TEXT_NUMBERED_MENU",
                startup_choice="CUSTOM", startup_choice_user_quote="Я хочу свой вариант.", fidelity=70,
                aux_body_decision="DECLINED",
            ))
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="USER_CONFIRMATION", startup_menu_surface="TEXT_NUMBERED_MENU",
                startup_choice="CUSTOM", startup_choice_user_quote=natural, fidelity=90,
                aux_body_decision="DECLINED",
            ))
        mismatched = json.loads(json.dumps(saved))
        mismatched["custom_parameters_user_quote"] = "Use 90% fidelity and select BODY_REFERENCE_LIBRARY."
        with self.assertRaises(manager.StylePackError):
            manager.validate_startup_profile_evidence(mismatched)

    def test_custom_rejects_conflicting_library_clauses_and_numeric_prefixes(self):
        conflicting_quotes = (
            "Use 70% fidelity and select BODY_REFERENCE_LIBRARY, but do not use BODY_REFERENCE_LIBRARY.",
            "Use 70% fidelity, select BODY_REFERENCE_LIBRARY and do not use BODY_REFERENCE_LIBRARY.",
            "Use 70% fidelity and decline BODY_REFERENCE_LIBRARY, but use BODY_REFERENCE_LIBRARY.",
            "Use 70% fidelity without BODY_REFERENCE_LIBRARY, but select BODY_REFERENCE_LIBRARY.",
            "Use 70% fidelity; BODY_REFERENCE_LIBRARY is enabled, BODY_REFERENCE_LIBRARY is disabled.",
        )
        for quote in conflicting_quotes:
            with self.subTest(quote=quote), self.assertRaises(manager.StylePackError):
                manager.parse_startup_interaction(self.make_args(
                    startup_selection_mode="USER_CONFIRMATION", startup_menu_surface="TEXT_NUMBERED_MENU",
                    startup_choice="CUSTOM", startup_choice_user_quote=quote, fidelity=70,
                    aux_body_decision="DECLINED",
                ))
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="USER_CONFIRMATION", startup_menu_surface="TEXT_NUMBERED_MENU",
                startup_choice="CUSTOM", startup_choice_user_quote="Use 500% fidelity and decline BODY_REFERENCE_LIBRARY.",
                fidelity=50, aux_body_decision="DECLINED",
            ))
        natural_choice = "Use 70% fidelity and decline BODY_REFERENCE_LIBRARY."
        with self.assertRaisesRegex(manager.StylePackError, "Natural CUSTOM choice quote does not match"):
            manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="USER_CONFIRMATION", startup_menu_surface="TEXT_NUMBERED_MENU",
                startup_choice="CUSTOM", startup_choice_user_quote=natural_choice, fidelity=90,
                aux_body_decision="SELECTED",
                custom_parameters_user_quote="Use 90% fidelity and select BODY_REFERENCE_LIBRARY.",
            ))

    def test_custom_decision_is_library_directed_not_generic_unrelated_use(self):
        quote = "Use 70% fidelity; do not use BODY_REFERENCE_LIBRARY; use only the style reference."
        saved = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="USER_CONFIRMATION", startup_menu_surface="TEXT_NUMBERED_MENU",
            startup_choice="CUSTOM", startup_choice_user_quote=quote, fidelity=70,
            aux_body_decision="DECLINED",
        ))
        self.assertEqual(manager.validate_startup_profile_evidence(saved), (70, "DECLINED"))
        enabled = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="USER_CONFIRMATION", startup_menu_surface="TEXT_NUMBERED_MENU",
            startup_choice="CUSTOM", startup_choice_user_quote="Use 70% fidelity; BODY_REFERENCE_LIBRARY is enabled.",
            fidelity=70, aux_body_decision="SELECTED",
        ))
        self.assertEqual(manager.validate_startup_profile_evidence(enabled), (70, "SELECTED"))

    def test_style_name_yes_is_not_a_profile_selection(self):
        with self.assertRaisesRegex(manager.StylePackError, "numbered or named menu choice"):
            manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="USER_CONFIRMATION",
                startup_menu_surface="TEXT_NUMBERED_MENU",
                startup_choice="OPTION_1",
                startup_choice_user_quote="да",
            ))

    def test_direct_fidelity_quote_plus_separate_library_reply_is_valid(self):
        result = manager.parse_startup_interaction(self.make_args(
            startup_selection_mode="DIRECT_CONFIRMATION",
            confirmed_chat_id="chat-7", confirmed_message_id="msg-fidelity",
            confirmed_parameters_user_quote="Use 70% fidelity.", fidelity=70,
            aux_body_decision="DECLINED",
            confirmed_body_library_chat_id="chat-7",
            confirmed_body_library_message_id="msg-library",
            confirmed_body_library_user_quote="Decline BODY_REFERENCE_LIBRARY.",
        ))
        self.assertEqual(result["body_library_provenance"]["message_id"], "msg-library")

    def test_unresolved_legacy_profile_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "REFERENCE_PLAN.json"
            original = manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="DIRECT_CONFIRMATION",
                confirmed_parameters_user_quote="Keep 90% fidelity and select BODY_REFERENCE_LIBRARY.",
            ))
            original["resolved_parameters"]["aux_body_decision"] = "NOT_SELECTED"
            source.write_text(json.dumps({
                "schema_version": 5, "style_name": "SAMPLE", "startup_parameter_selection": original,
            }), encoding="utf-8")
            with self.assertRaisesRegex(manager.StylePackError, "unresolved legacy profile"):
                manager.parse_startup_interaction(self.make_args(
                    startup_selection_mode="REUSE", reuse_startup_from=str(source),
                    reuse_chat_id="chat-1", reuse_message_id="msg-1", aux_body_decision="SELECTED",
                ))

    def test_default_mode_rejects_old_hidden_confirmation_surface(self):
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="USER_CONFIRMATION",
                startup_menu_surface="USER_REPLY_AFTER_NATIVE_UNAVAILABLE",
                startup_choice_user_quote="1",
            ))

    def test_auto_default_is_forbidden(self):
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="AUTO_DEFAULT",
            ))

    def test_user_confirmation_requires_explicit_reply(self):
        with self.assertRaises(manager.StylePackError):
            manager.parse_startup_interaction(self.make_args(
                startup_selection_mode="USER_CONFIRMATION",
                startup_menu_surface="TEXT_NUMBERED_MENU",
                startup_choice_user_quote="",
            ))

    def test_risk_gate_requires_every_selected_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "style.png"
            image.write_bytes(b"style")
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            report = root / "risk.json"
            prompt_text = "A portrait in a neutral studio."
            command = risk.build_parser().parse_args([
                "--text", prompt_text,
                "--reference", f"{image}::D3::+0D::Low-content identity reference::FACE",
                "--output", str(report),
            ])
            risk.command_assess(command)
            path, loaded = manager.load_and_validate_risk_assessment(
                str(report),
                prompt_text,
                [{"slot": "face", "path": str(image), "sha256": digest, "active_roles": ["FACE"]}],
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
                    "A portrait in a neutral studio.",
                    [{"slot": "face", "path": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest(), "active_roles": ["FACE"]}],
                )


class SemanticGenerationQATests(unittest.TestCase):
    def make_args(self, **updates):
        values = {
            "stage_id": "01_FACE_IDENTITY",
            "qa_attachments": "PASS",
            "qa_canvas": "PASS",
            "qa_stage_layer": "PASS",
            "qa_face": "PASS",
            "qa_body_silhouette": "NOT_CHECKED",
            "qa_body_proportions": "NOT_CHECKED",
            "qa_limb_proportions": "NOT_CHECKED",
            "limb_qa_evidence": "",
            "qa_style": "PASS",
            "qa_body_style": "PASS",
            "qa_expression": "PASS",
            "qa_neutral_backdrop": "PASS",
            "qa_view": "NOT_CHECKED",
            "qa_safe_coverage": "NOT_CHECKED",
            "qa_clothing_topology": "NOT_CHECKED",
            "qa_multiview_consistency": "NOT_CHECKED",
            "qa_clothing": "NOT_CHECKED",
            "qa_pose_contacts": "NOT_CHECKED",
            "qa_camera": "NOT_CHECKED",
            "qa_lighting": "NOT_CHECKED",
            "qa_background": "NOT_CHECKED",
            "qa_composition": "NOT_CHECKED",
        }
        values.update(updates)
        return Namespace(**values)

    def face_plan(self, semantic=True):
        plan = {
            "generation_workflow": {
                "mode": "MULTI_STAGE",
                "stages": [{
                    "stage_id": "01_FACE_IDENTITY",
                    "required_qa": ["FACE_GEOMETRY", "EXPRESSION", "STYLE", "NEUTRAL_BACKDROP"],
                }],
            },
        }
        if semantic:
            plan["semantic_qa_schema"] = 1
        return plan

    def test_new_plan_requires_separate_style_check(self):
        with self.assertRaisesRegex(manager.StylePackError, "STYLE"):
            manager.evaluate_generation_qa(
                self.face_plan(),
                self.make_args(qa_style="NOT_CHECKED"),
            )

    def test_style_failure_is_independent_rejection_reason(self):
        failed, stage_id, required = manager.evaluate_generation_qa(
            self.face_plan(),
            self.make_args(qa_style="FAIL"),
        )
        self.assertEqual(stage_id, "01_FACE_IDENTITY")
        self.assertIn("STYLE", required)
        self.assertEqual(failed, ["STYLE"])

    def test_old_in_flight_plan_keeps_legacy_compatibility(self):
        args = self.make_args(qa_style="NOT_CHECKED")
        failed, _, required = manager.evaluate_generation_qa(self.face_plan(semantic=False), args)
        self.assertEqual(failed, [])
        self.assertIn("STYLE", required)

    def test_physique_requires_view_coverage_and_clothing_topology(self):
        plan = {
            "semantic_qa_schema": 1,
            "generation_workflow": {
                "mode": "MULTI_STAGE",
                "stages": [{
                    "stage_id": "02_PHYSIQUE_FRONT",
                    "required_qa": [
                        "FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "FRONT_VIEW",
                        "SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "STYLE",
                    ],
                }],
            },
        }
        with self.assertRaisesRegex(manager.StylePackError, "CLOTHING_TOPOLOGY"):
            manager.evaluate_generation_qa(
                plan,
                self.make_args(
                    stage_id="02_PHYSIQUE_FRONT",
                    qa_body_silhouette="PASS",
                    qa_body_proportions="PASS",
                    qa_view="PASS",
                    qa_safe_coverage="PASS",
                ),
            )

    def test_new_physique_plan_requires_independent_body_rendering_style(self):
        plan = {
            "semantic_qa_schema": 2,
            "generation_workflow": {
                "mode": "MULTI_STAGE",
                "stages": [{
                    "stage_id": "02_PHYSIQUE_FRONT",
                    "required_qa": [
                        "FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "FRONT_VIEW",
                        "SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "STYLE", "BODY_RENDERING_STYLE",
                    ],
                }],
            },
        }
        with self.assertRaisesRegex(manager.StylePackError, "BODY_RENDERING_STYLE"):
            manager.evaluate_generation_qa(
                plan,
                self.make_args(
                    stage_id="02_PHYSIQUE_FRONT",
                    qa_body_silhouette="PASS",
                    qa_body_proportions="PASS",
                    qa_view="PASS",
                    qa_safe_coverage="PASS",
                    qa_clothing_topology="PASS",
                    qa_body_style="NOT_CHECKED",
                ),
            )

    def limb_plan(self):
        return {
            "semantic_qa_schema": 3,
            "generation_workflow": {
                "mode": "MULTI_STAGE",
                "stages": [{
                    "stage_id": "02_PHYSIQUE_FRONT",
                    "required_qa": [
                        "FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS",
                        "LIMB_PROPORTIONS", "FRONT_VIEW", "SAFE_COVERAGE",
                        "CLOTHING_TOPOLOGY", "STYLE", "BODY_RENDERING_STYLE",
                    ],
                }],
            },
        }

    def anthropometric_plan(self, user_override=False):
        plan = self.limb_plan()
        plan["semantic_qa_schema"] = 4
        plan["body_proportion_contract"] = {
            "user_approved_nonstandard_proportions": user_override,
            "height_in_heads": {
                "mode": "EXPLICIT_RANGE",
                "minimum": 7.5,
                "maximum": 8.0,
            },
        }
        return plan

    def valid_anthropometric_evidence(self, **updates):
        values = {
            "source": "approved_front_overlay",
            "head_units": "7.7",
            "pubic_height_fraction": "0.50",
            "hip_knee": "310",
            "knee_ankle": "300",
            "ankle_width": "0.21",
            "foot_length": "0.95",
            "view": "SIDE",
            "foot_length_mode": "MEASURED",
            "heel_endpoint": "VISIBLE",
            "toe_endpoint": "VISIBLE",
            "neck_head_ratio": "0.34",
            "neck_jaw_ratio": "0.68",
            "foot_pose": "FLAT",
            "hip_landmark": "FEMORAL_HEAD_CENTER",
            "knee_landmark": "KNEE_JOINT_CENTER",
            "ankle_landmark": "TALOCRURAL_JOINT_CENTER",
            "landmark_confidence": "0.95",
            "crown_landmark": "CRANIAL_VERTEX_ESTIMATED",
            "crown_confidence": "0.92",
        }
        values.update({key: str(value) for key, value in updates.items()})
        return "; ".join(f"{key}={value}" for key, value in values.items())

    def limb_args(self, **updates):
        values = {
            "stage_id": "02_PHYSIQUE_FRONT",
            "qa_body_silhouette": "PASS",
            "qa_body_proportions": "PASS",
            "qa_limb_proportions": "PASS",
            "qa_view": "PASS",
            "qa_safe_coverage": "PASS",
            "qa_clothing_topology": "PASS",
            "qa_body_style": "PASS",
        }
        values.update(updates)
        return self.make_args(**values)

    def test_schema_three_physique_requires_limb_proportions(self):
        with self.assertRaisesRegex(manager.StylePackError, "LIMB_PROPORTIONS"):
            manager.evaluate_generation_qa(
                self.limb_plan(),
                self.limb_args(qa_limb_proportions="NOT_CHECKED"),
            )

    def test_limb_pass_requires_structured_evidence(self):
        with self.assertRaisesRegex(manager.StylePackError, "limb-qa-evidence"):
            manager.evaluate_generation_qa(self.limb_plan(), self.limb_args())

    def test_limb_pass_accepts_complete_structured_evidence(self):
        evidence = (
            "source=approved_front_overlay; head_units=matched; hip_knee=matched; "
            "knee_ankle=matched; ankle_width=matched; foot_length=matched"
        )
        failed, _, required = manager.evaluate_generation_qa(
            self.limb_plan(),
            self.limb_args(limb_qa_evidence=evidence),
        )
        self.assertEqual(failed, [])
        self.assertIn("LIMB_PROPORTIONS", required)

    def test_limb_failure_is_independent_rejection_reason(self):
        failed, _, required = manager.evaluate_generation_qa(
            self.limb_plan(),
            self.limb_args(qa_limb_proportions="FAIL"),
        )
        self.assertIn("LIMB_PROPORTIONS", required)
        self.assertIn("LIMB_PROPORTIONS", failed)

    def test_schema_four_rejects_non_numeric_evidence(self):
        evidence = self.valid_anthropometric_evidence(head_units="matched")
        with self.assertRaisesRegex(manager.StylePackError, "numeric head_units"):
            manager.evaluate_generation_qa(
                self.anthropometric_plan(),
                self.limb_args(limb_qa_evidence=evidence),
            )

    def test_schema_four_accepts_realistic_numeric_evidence(self):
        failed, _, _ = manager.evaluate_generation_qa(
            self.anthropometric_plan(),
            self.limb_args(limb_qa_evidence=self.valid_anthropometric_evidence()),
        )
        self.assertEqual(failed, [])

    def test_schema_four_requires_explicit_foot_pose(self):
        evidence = self.valid_anthropometric_evidence()
        evidence = "; ".join(part for part in evidence.split("; ") if not part.startswith("foot_pose="))
        with self.assertRaisesRegex(manager.StylePackError, "foot_pose"):
            manager.evaluate_generation_qa(
                self.anthropometric_plan(),
                self.limb_args(limb_qa_evidence=evidence),
            )

    def test_schema_four_accepts_tiptoe_without_counting_foot_as_shin(self):
        failed, _, _ = manager.evaluate_generation_qa(
            self.anthropometric_plan(),
            self.limb_args(
                limb_qa_evidence=self.valid_anthropometric_evidence(
                    foot_pose="TIPTOE", hip_knee=300, knee_ankle=300
                )
            ),
        )
        self.assertEqual(failed, [])

    def test_schema_four_front_defers_foreshortened_foot_length(self):
        failed, _, _ = manager.evaluate_generation_qa(
            self.anthropometric_plan(),
            self.limb_args(
                limb_qa_evidence=self.valid_anthropometric_evidence(
                    view="FRONT",
                    foot_length_mode="FORESHORTENED_DEFERRED",
                    foot_length="DEFERRED_TO_SIDE",
                )
            ),
        )
        self.assertEqual(failed, [])

    def test_schema_four_side_cannot_defer_foot_length(self):
        evidence = self.valid_anthropometric_evidence(
            view="SIDE",
            foot_length_mode="FORESHORTENED_DEFERRED",
            foot_length="DEFERRED",
        )
        with self.assertRaisesRegex(manager.StylePackError, "SIDE must measure"):
            manager.evaluate_generation_qa(
                self.anthropometric_plan(),
                self.limb_args(limb_qa_evidence=evidence),
            )

    def test_schema_four_rejects_pubic_point_as_hip_joint(self):
        evidence = self.valid_anthropometric_evidence(hip_landmark="PUBIC_LANDMARK")
        with self.assertRaisesRegex(manager.StylePackError, "FEMORAL_HEAD_CENTER"):
            manager.evaluate_generation_qa(
                self.anthropometric_plan(),
                self.limb_args(limb_qa_evidence=evidence),
            )

    def test_schema_four_rejects_toes_as_ankle_joint(self):
        evidence = self.valid_anthropometric_evidence(ankle_landmark="TOE_ENDPOINT")
        with self.assertRaisesRegex(manager.StylePackError, "TALOCRURAL_JOINT_CENTER"):
            manager.evaluate_generation_qa(
                self.anthropometric_plan(),
                self.limb_args(limb_qa_evidence=evidence),
            )

    def test_schema_four_rejects_hair_top_as_anatomical_crown(self):
        evidence = self.valid_anthropometric_evidence(crown_landmark="HAIR_SILHOUETTE_TOP")
        with self.assertRaisesRegex(manager.StylePackError, "CRANIAL_VERTEX"):
            manager.evaluate_generation_qa(
                self.anthropometric_plan(),
                self.limb_args(limb_qa_evidence=evidence),
            )

    def test_schema_four_blocks_low_crown_confidence(self):
        evidence = self.valid_anthropometric_evidence(crown_confidence=0.55)
        with self.assertRaisesRegex(manager.StylePackError, "crown_confidence"):
            manager.evaluate_generation_qa(
                self.anthropometric_plan(),
                self.limb_args(limb_qa_evidence=evidence),
            )

    def test_schema_four_blocks_low_confidence_landmarks(self):
        evidence = self.valid_anthropometric_evidence(landmark_confidence=0.62)
        with self.assertRaisesRegex(manager.StylePackError, "landmark_confidence"):
            manager.evaluate_generation_qa(
                self.anthropometric_plan(),
                self.limb_args(limb_qa_evidence=evidence),
            )

    def test_schema_four_auto_rejects_extreme_head_count(self):
        failed, _, _ = manager.evaluate_generation_qa(
            self.anthropometric_plan(),
            self.limb_args(limb_qa_evidence=self.valid_anthropometric_evidence(head_units=9.2)),
        )
        self.assertIn("BODY_PROPORTIONS", failed)
        self.assertIn("LIMB_PROPORTIONS", failed)

    def test_schema_four_enforces_narrower_plan_head_count(self):
        failed, _, _ = manager.evaluate_generation_qa(
            self.anthropometric_plan(),
            self.limb_args(limb_qa_evidence=self.valid_anthropometric_evidence(head_units=7.2)),
        )
        self.assertIn("BODY_PROPORTIONS", failed)
        self.assertIn("LIMB_PROPORTIONS", failed)

    def test_schema_four_auto_rejects_midpoint_in_thigh(self):
        failed, _, _ = manager.evaluate_generation_qa(
            self.anthropometric_plan(),
            self.limb_args(limb_qa_evidence=self.valid_anthropometric_evidence(pubic_height_fraction=0.58)),
        )
        self.assertIn("BODY_PROPORTIONS", failed)

    def test_schema_four_auto_rejects_long_lower_legs(self):
        failed, _, _ = manager.evaluate_generation_qa(
            self.anthropometric_plan(),
            self.limb_args(limb_qa_evidence=self.valid_anthropometric_evidence(hip_knee=250, knee_ankle=310)),
        )
        self.assertIn("LIMB_PROPORTIONS", failed)

    def test_schema_four_auto_rejects_long_thin_neck(self):
        failed, _, _ = manager.evaluate_generation_qa(
            self.anthropometric_plan(),
            self.limb_args(limb_qa_evidence=self.valid_anthropometric_evidence(neck_head_ratio=0.55, neck_jaw_ratio=0.44)),
        )
        self.assertIn("BODY_PROPORTIONS", failed)

    def test_schema_four_allows_direct_user_override(self):
        failed, _, _ = manager.evaluate_generation_qa(
            self.anthropometric_plan(user_override=True),
            self.limb_args(limb_qa_evidence=self.valid_anthropometric_evidence(head_units=9.2)),
        )
        self.assertEqual(failed, [])


class StyleCalibrationEvidenceTests(unittest.TestCase):
    def write_state(self, folder: str, **updates):
        rounds = []
        for round_number, mode in enumerate(("PERCENT", "MIN_TO_MAX", "PERCENT", "PERCENT"), 1):
            rounds.append({
                "round": round_number,
                "face_id": f"FACE_{round_number}",
                "feedback": {"mode": mode},
                "ai_adaptation": {"next": round_number + 1} if round_number < 4 else None,
                "candidates": [{
                    "candidate_id": f"R{round_number:02d}_C{candidate_number:02d}",
                    "image_sha256": f"hash-{round_number}-{candidate_number}",
                    "user_score": candidate_number * 20,
                } for candidate_number in range(1, 5)],
            })
        value = {
            "schema_version": 1,
            "calibration_id": "CAL_TEST",
            "style_name": "SAMPLE",
            "target_rounds": 4,
            "batch_size": 4,
            "reduced_rounds_user_approved": False,
            "status": "FINALIZED",
            "next_required_action": "NONE",
            "rounds": rounds,
            "final_conclusion": {
                "completed_rounds": 4,
                "early_stop": False,
                "early_stop_user_approved": False,
            },
        }
        value.update(updates)
        path = Path(folder) / "CALIBRATION_STATE.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_required_calibration_cannot_be_omitted(self):
        with self.assertRaisesRegex(manager.StylePackError, "required"):
            manager.load_style_calibration_evidence("", required=True, style_name="SAMPLE")

    def test_incomplete_calibration_blocks_production(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_state(folder, status="ACTIVE", next_required_action="OPEN_ROUND")
            with self.assertRaisesRegex(manager.StylePackError, "incomplete"):
                manager.load_style_calibration_evidence(str(path), required=True, style_name="SAMPLE")

    def test_finalized_four_by_four_is_recorded_in_plan_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_state(folder)
            evidence = manager.load_style_calibration_evidence(
                str(path), required=True, style_name="sample"
            )
        self.assertEqual(evidence["status"], "FINALIZED")
        self.assertEqual(evidence["completed_rounds"], 4)
        self.assertEqual(evidence["feedback_modes"], ["MIN_TO_MAX", "PERCENT"])

    def test_required_calibration_resolves_active_applied_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder) / "SAMPLE_PROJECT_PACK"
            active = pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "CALIBRATIONS" / "ACTIVE_STYLE_CALIBRATION.json"
            active.parent.mkdir(parents=True)
            source = self.write_state(folder)
            active.write_bytes(source.read_bytes())
            evidence = manager.load_style_calibration_evidence(
                "",
                required=True,
                style_name="sample",
                pack_path=str(pack),
            )
        self.assertTrue(evidence["resolved_from_active_profile"])
        self.assertEqual(Path(evidence["path"]).name, "ACTIVE_STYLE_CALIBRATION.json")

    def test_protocol_v2_collapsed_quartet_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_state(folder, protocol_version=2)
            value = json.loads(path.read_text(encoding="utf-8"))
            for item in value["rounds"]:
                item["distinction_qa"] = {
                    "review_status": "PASS",
                    "minimum_visible_delta_percent": 5,
                    "decision": "SHOW_TO_USER",
                    "collapsed_pairs": [],
                }
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(manager.StylePackError, "less than 10"):
                manager.load_style_calibration_evidence(str(path), required=True, style_name="SAMPLE")

    def test_protocol_v3_requires_one_composite_art_per_round(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_state(folder, protocol_version=3, arts_per_round=1)
            value = json.loads(path.read_text(encoding="utf-8"))
            for round_number, item in enumerate(value["rounds"], 1):
                quartet_path = str(Path(folder) / f"round-{round_number}-quartet.png")
                quartet_hash = f"quartet-hash-{round_number}"
                item["quartet_art_path"] = quartet_path
                item["quartet_art_sha256"] = quartet_hash
                item["distinction_qa"] = {
                    "review_status": "PASS",
                    "minimum_visible_delta_percent": 10,
                    "decision": "SHOW_TO_USER",
                    "collapsed_pairs": [],
                }
                for candidate_number, candidate in enumerate(item["candidates"], 1):
                    candidate["panel_label"] = chr(64 + candidate_number)
                    candidate["image_path"] = quartet_path
                    candidate["source_art_sha256"] = quartet_hash
            value["final_conclusion"].update({
                "final_style_prompt": "Concise verified style prompt.",
                "excluded_style_noise": ["generic polish"],
                "prompt_vocabulary": {
                    "preferred_terms": ["selective edges"],
                    "conditional_synonyms": ["rendered comic"],
                    "harmful_or_neutral_terms": ["generic detailed"],
                    "helpful_constructions": ["shape before surface"],
                    "distracting_details": ["unverified decoration"],
                },
                "prompt_evidence_by_round": [
                    {"round": number, "finding": "verified"} for number in range(1, 5)
                ],
            })
            path.write_text(json.dumps(value), encoding="utf-8")
            evidence = manager.load_style_calibration_evidence(
                str(path), required=True, style_name="SAMPLE"
            )
            self.assertEqual(evidence["status"], "FINALIZED")

            value["rounds"][0]["candidates"][0]["image_path"] = str(Path(folder) / "separate-a.png")
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(manager.StylePackError, "one generated composite art"):
                manager.load_style_calibration_evidence(str(path), required=True, style_name="SAMPLE")


if __name__ == "__main__":
    unittest.main()
