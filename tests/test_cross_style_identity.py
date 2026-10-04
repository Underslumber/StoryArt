from pathlib import Path
from unittest.mock import patch
import pytest
from tools import style_pack_manager as manager
from tools import generation_request


def cross_plan(tmp_path):
    render = manager.make_paths(tmp_path, "LORANDESORE")
    identity = manager.make_paths(tmp_path, "KATE_FOX")
    (identity.generations / "01_APPROVED_CHARACTERS" / "CHAR_002_Beal").mkdir(parents=True)
    (render.generations / "01_APPROVED_CHARACTERS" / "CHAR_002_Other").mkdir(parents=True)
    plan = {"identity_style_name": identity.style_name, "identity_pack_path": str(identity.pack),
            "generation_purpose": "SCENE", "character_id": "CHAR_002"}
    return render, identity, plan


def test_same_style_legacy_binding(tmp_path):
    paths = manager.make_paths(tmp_path, "KATE_FOX")
    assert manager.identity_paths_for_plan(paths, {}) == paths


@pytest.mark.parametrize("tamper", ["path", "purpose", "character", "call", "missing"])
def test_cross_namespace_tamper_rejected(tmp_path, tamper):
    render, identity, plan = cross_plan(tmp_path)
    if tamper == "path": plan["identity_pack_path"] = str(render.pack)
    if tamper == "purpose": plan["generation_purpose"] = "CHARACTER_BASE"
    if tamper == "character": plan["character_id"] = "NEW"
    if tamper == "call": plan["execution_call"] = {"identity_style_name": "LORANDESORE"}
    if tamper == "missing": del plan["identity_pack_path"]
    with pytest.raises(manager.StylePackError): manager.identity_paths_for_plan(render, plan)


def test_same_id_collision_uses_identity_profile(tmp_path):
    render, identity, plan = cross_plan(tmp_path)
    plan["confirmed_character_profile"] = {"name": "Beal"}
    with patch.object(manager, "_approved_profile_identity") as approved, patch.object(manager, "verify_plan_binding"), patch.object(manager, "effective_character_profile", return_value={"name": "Beal"}) as effective:
        manager.assert_profile_plan_current(render, plan)
    assert effective.call_args.args == (identity, "CHAR_002")
    assert approved.call_args.args[0] == identity
    assert manager.character_folder(identity, "CHAR_002") != manager.character_folder(render, "CHAR_002")


def test_approved_only_policy_accepts_identity_pack_not_render_collision(tmp_path):
    render, identity, plan = cross_plan(tmp_path)
    assembly = manager.character_folder(identity, "CHAR_002") / "01_BASE" / "assembly.png"
    slot = {"path": str(assembly), "sha256": "x", "active_roles": ["FACE", "BODY"]}
    selections = {"character": {"choice": "CHAR_002"}, "reference_policy": {"choice": "APPROVED_CHARACTER_REFERENCES"}}
    manager.validate_reference_policy_slots(render, plan, {"slots": [slot]}, selections)
    wrong = dict(slot, path=str(manager.character_folder(render, "CHAR_002") / "assembly.png"))
    with patch.object(manager, "validated_stage_outputs", return_value={}):
        with pytest.raises(manager.StylePackError): manager.validate_reference_policy_slots(render, plan, {"slots": [wrong]}, selections)


def test_identity_role_revalidated_against_source_registry(tmp_path):
    render, identity, plan = cross_plan(tmp_path)
    file = manager.character_folder(identity, "CHAR_002") / "wardrobe.png"
    file.write_bytes(b"synthetic wardrobe fixture")
    plan["target_identity_compatibility"] = {"target": {}}
    plan["selected_references"] = {"clothes": {"path": str(file)}}
    with patch.object(manager, "registered_approved_character_asset", return_value={"character_id": "CHAR_002", "asset_role": "WARDROBE"}) as asset, patch.object(manager, "style_reference_compatibility_metadata", return_value={}) as metadata, patch.object(manager, "enforce_reference_compatibility"):
        manager.validate_resolved_slot_compatibility(render, plan, {"active_roles": ["CLOTHES"]}, file)
        assert asset.call_args.args[0] == identity
        assert metadata.call_args.args[0] == identity
        with pytest.raises(manager.StylePackError): manager.validate_resolved_slot_compatibility(render, plan, {"active_roles": ["FACE"]}, file)


def test_request_parser_exposes_independent_identity():
    parser = generation_request._prepare_parser()
    actions = {action.dest: action for action in parser._actions}
    assert "identity_style_name" in actions
    assert actions["identity_style_name"].default == ""
