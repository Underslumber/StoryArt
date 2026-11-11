from pathlib import Path

import pytest

from tools import style_pack_manager as manager


@pytest.mark.parametrize("mutation", [None, "hash", "path", "role", "no_intent", "no_quote", "scene"])
def test_body_library_choice_preserves_explicit_new_face_identity(tmp_path, monkeypatch, mutation):
    paths = manager.StylePaths(tmp_path, "TEST", "TEST", tmp_path / "TEST_PROJECT_PACK", tmp_path / "TEST_GENERATIONS")
    source = tmp_path / "face.png"
    source.write_bytes(b"explicit-author-face")
    sha = manager.sha256(source)
    plan = {"request_id": "request", "character_id": "NEW", "generation_purpose": "CHARACTER_BASE",
        "startup_parameter_selection": {"resolved_parameters": {"aux_body_decision": "SELECTED"}},
        "new_face_reference_purpose": {"mode": "USER_IDENTITY_INTENT", "scope": "NEW_CHARACTER_BASE",
            "evidence": "Create this exact selected person as a character", "sources": [{"path": str(source),
                "sha256": sha, "physical_slot_role": "PRIMARY_FACE"}]}}
    slot = {"path": str(source), "sha256": sha, "active_roles": ["PRIMARY_FACE"]}
    if mutation == "hash": slot["sha256"] = "0" * 64
    if mutation == "path": slot["path"] = str(tmp_path / "unrelated.png")
    if mutation == "role": slot["active_roles"] = ["BODY"]
    if mutation == "no_intent": plan["new_face_reference_purpose"]["mode"] = "STYLE_SEED"
    if mutation == "no_quote": plan["new_face_reference_purpose"]["evidence"] = ""
    if mutation == "scene": plan["generation_purpose"] = "SCENE"
    monkeypatch.setattr(manager, "validated_stage_outputs", lambda *args: {})
    selections = {"reference_policy": {"choice": "BODY_LIBRARY_ONLY"}, "character": {"choice": "NEW"}}
    if mutation is None:
        manager.validate_reference_policy_slots(paths, plan, {"slots": [slot]}, selections)
    else:
        with pytest.raises(manager.StylePackError, match="BODY_LIBRARY_ONLY"):
            manager.validate_reference_policy_slots(paths, plan, {"slots": [slot]}, selections)
