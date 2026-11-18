"""DELEGATED NEW is scoped to character creation; no provider calls."""
import copy
import json
from types import SimpleNamespace

import pytest

from tools import style_pack_manager as manager
from tools import style_pack_startup as startup_checks


@pytest.fixture
def delegated_new(tmp_path):
    receipt = {
        "request_id": "delegated-new", "current_chat_id": "test-chat", "message_id": "test-message",
        "user_quote": "Выбери всё самостоятельно", "agent_reason": "Synthetic delegated character creation",
        "agent_choices": {"style": "PROJECT_STYLE:SAMPLE", "character": "NEW", "fidelity": 90,
                          "aux_body_decision": "SELECTED", "reference_policy": "BODY_LIBRARY_ONLY"},
    }
    path = tmp_path / "delegation.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    args = manager.build_parser().parse_args([
        "prepare-generation", "--style-name", "SAMPLE", "--request-id", "delegated-new",
        "--fidelity", "90", "--character-id", "NEW", "--generation-purpose", "CHARACTER_BASE",
        "--startup-selection-mode", "DELEGATED", "--delegated-selection-json", str(path),
        "--confirmed-chat-id", "test-chat", "--confirmed-message-id", "test-message",
        "--aux-body-decision", "SELECTED",
    ])
    return receipt, path, args


def selections(startup):
    return {k: {"choice": v, "user_quote": startup["user_choice_quote"]}
            for k, v in startup["resolved_user_selections"].items()}


def test_new_character_delegation_survives_saved_startup_validation(delegated_new):
    receipt, _, args = delegated_new
    startup = json.loads(json.dumps(manager.parse_startup_interaction(args)))
    context = {"generation_purpose": "CHARACTER_BASE", "character_id": "NEW"}
    assert manager.validate_startup_profile_evidence(startup, 90, **context) == (90, "SELECTED")
    manager.validate_menu_user_selections(startup, selections(startup), **context)
    assert startup["delegation"] == receipt
    assert startup["body_library_provenance"]["selected_by"] == "AGENT"
    assert startup["menu_presented_this_turn"] is False


@pytest.mark.parametrize("purpose", ["SCENE", "TECHNICAL_TEST", ""])
def test_new_is_rejected_outside_character_base_at_preparation(delegated_new, purpose):
    _, _, args = delegated_new
    args.generation_purpose = purpose
    with pytest.raises(manager.StylePackError, match="NEW CHARACTER_BASE"):
        manager.parse_startup_interaction(args)


@pytest.mark.parametrize("purpose,character", [
    ("SCENE", "NEW"), ("TECHNICAL_TEST", "NEW"), ("", "NEW"),
    ("CHARACTER_BASE", "NONE"), ("CHARACTER_BASE", "CHAR_001"), ("CHARACTER_BASE", ""),
])
def test_saved_new_rejects_changed_or_missing_execution_context(delegated_new, purpose, character):
    _, _, args = delegated_new
    saved = json.loads(json.dumps(manager.parse_startup_interaction(args)))
    context = {"generation_purpose": purpose, "character_id": character}
    with pytest.raises(manager.StylePackError, match="NEW CHARACTER_BASE"):
        manager.validate_startup_profile_evidence(saved, 90, **context)
    with pytest.raises(manager.StylePackError, match="NEW CHARACTER_BASE"):
        manager.validate_menu_user_selections(saved, selections(saved), **context)


def test_new_cannot_authorize_itself_without_external_context(delegated_new):
    receipt, _, _ = delegated_new
    receipt["generation_purpose"] = "CHARACTER_BASE"
    with pytest.raises(manager.StylePackError, match="NEW CHARACTER_BASE"):
        startup_checks.validate_delegation_receipt(receipt)


@pytest.mark.parametrize("character", ["NONE", "CHAR_001"])
def test_character_base_rejects_existing_or_absent_identity(delegated_new, character):
    receipt, path, args = delegated_new
    receipt["agent_choices"]["character"] = character
    path.write_text(json.dumps(receipt), encoding="utf-8")
    args.character_id = character
    with pytest.raises(manager.StylePackError, match="CHARACTER_BASE requires NEW"):
        manager.parse_startup_interaction(args)


@pytest.mark.parametrize("field,value", [("fidelity", 70), ("aux_body_decision", "DECLINED")])
def test_saved_new_rejects_parameter_tampering(delegated_new, field, value):
    _, _, args = delegated_new
    saved = copy.deepcopy(manager.parse_startup_interaction(args))
    saved["resolved_parameters"][field] = value
    with pytest.raises(manager.StylePackError):
        manager.validate_startup_profile_evidence(
            saved, 90, generation_purpose="CHARACTER_BASE", character_id="NEW")


def test_saved_new_rejects_quote_and_identity_tampering(delegated_new):
    _, _, args = delegated_new
    saved = manager.parse_startup_interaction(args)
    changed = copy.deepcopy(saved)
    changed["delegation"]["user_quote"] = "выбери позу"
    with pytest.raises(manager.StylePackError, match="Full explicit delegation"):
        manager.validate_startup_profile_evidence(
            changed, 90, generation_purpose="CHARACTER_BASE", character_id="NEW")
    changed_selections = selections(saved)
    changed_selections["character"]["choice"] = "CHAR_999"
    with pytest.raises(manager.StylePackError, match="Executable selections differ"):
        manager.validate_menu_user_selections(
            saved, changed_selections, generation_purpose="CHARACTER_BASE", character_id="NEW")


def test_nonexistent_character_still_fails_approved_registry_lookup(tmp_path):
    paths = SimpleNamespace(character_registry=tmp_path / "CHARACTER_REGISTRY.csv", generations=tmp_path)
    with pytest.raises(manager.StylePackError, match="matching APPROVED character-registry profile"):
        manager._approved_profile_identity(paths, tmp_path / "missing-profile.yaml", "CHAR_999")