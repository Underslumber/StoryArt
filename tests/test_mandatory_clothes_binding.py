import hashlib
import json
from pathlib import Path

import pytest

from tools import generation_request
from tools import style_pack_contracts as contracts
from tools import style_pack_manager as manager
from tools import task_execution_guard as guard


def _write(path: Path, value: bytes) -> tuple[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return str(path.resolve()), hashlib.sha256(value).hexdigest()


def _valid_authorization(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    request = tmp_path / "00_PENDING" / "request-one"
    request.mkdir(parents=True)
    source_path, source_hash = _write(request / "robe.png", b"approved request clothing source")
    assembly_path, assembly_hash = _write(tmp_path / "approved" / "assembly.png", b"approved assembly")
    approved_files = {}
    for stage in ("01_FACE_IDENTITY", "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY"):
        approved_bytes = b"approved assembly" if stage == "05_CHARACTER_ASSEMBLY" else stage.encode()
        base_path, base_hash = _write(request / f"{stage}.png", approved_bytes)
        approved_files[stage] = {"path": base_path, "sha256": base_hash}
    item = {"status": "ITEM_PRODUCTION_PLAN", "request_id": "request-one", "character_id": "CHAR_002",
            "item_kind": "CLOTHING", "style_name": "LORANDESORE", "character_assembly": assembly_path,
            "character_assembly_sha256": assembly_hash}
    item_path, item_hash = _write(request / "item.json", json.dumps(item).encode())
    continuation = {"request_id": "request-one", "approval_quote": "Утвердить показанную базу и продолжить комплект одежды",
                    "approval_source": "native user approval fixture", "approved_files": approved_files}
    continuation_path = request / "AUTHOR_FIVE_IDENTITY_APPROVAL.json"
    continuation_raw = json.dumps(continuation, ensure_ascii=False).encode()
    _write(continuation_path, continuation_raw)
    permission = {"quote": "Разрешить узкое исправление и продолжить комплект",
                  "source": "native user permission fixture", "scope": "Narrow mandatoryoriginalCLOTHES consent binding"}
    permission_path = request / "AUTHOR_MANDATORY_CLOTHES_REPAIR_PERMISSION.json"
    permission_raw = json.dumps(permission, ensure_ascii=False).encode()
    _write(permission_path, permission_raw)
    authorization = {
        "schema_version": 1, "request_id": "request-one", "character_id": "CHAR_002",
        "generation_purpose": "SCENE", "stage_id": "SINGLE_PASS", "item_kind": "CLOTHING",
        "physical_role": "CLOTHES", "style_name": "LORANDESORE", "fidelity": 90,
        "aux_body_decision": "SELECTED", "reference_policy": "BODY_LIBRARY_ONLY",
        "item_plan_path": item_path, "item_plan_sha256": item_hash,
        "clothes_reference_path": source_path, "clothes_reference_sha256": source_hash,
        "source_reference_id": "REF_00217", "subject_label": "MALE", "subject_label_sha256": source_hash,
        "character_assembly_path": assembly_path, "character_assembly_sha256": assembly_hash,
        "character_profile_path": str((tmp_path / "approved" / "CHARACTER_PROFILE.yaml").resolve()),
        "authorization_quote": "Теперь сделай из этого персонажа + комплект одежды. Полный набор",
        "continuation_authorization_quote": continuation["approval_quote"],
        "continuation_authorization_path": str(continuation_path.resolve()),
        "continuation_authorization_sha256": hashlib.sha256(continuation_raw).hexdigest(),
        "tool_repair_permission_quote": permission["quote"], "tool_repair_permission_path": str(permission_path.resolve()),
        "tool_repair_permission_sha256": hashlib.sha256(permission_raw).hexdigest(),
        "unseen_clothing_author_approval_granted": False,
    }
    auth_path = request / "MANDATORY_CLOTHES_AUTHORIZATION.json"
    auth_path.write_text(json.dumps(authorization, ensure_ascii=False), encoding="utf-8")
    plan = {"request_id": "request-one", "character_id": "CHAR_002", "generation_purpose": "SCENE",
            "style_name": "LORANDESORE"}
    return auth_path, plan


def test_exact_mandatory_clothes_authorization_binds_source_item_and_assembly(tmp_path):
    path, plan = _valid_authorization(tmp_path)
    bound = contracts.validate_mandatory_clothes_authorization(path, request_id="request-one",
                                                               character_id="CHAR_002", plan=plan)
    assert bound["reference_id"] == "REF_00217"
    assert bound["role"] == "CLOTHES"
    assert bound["item_plan_path"].endswith("item.json")
    assert bound["assembly_sha256"]


def test_unrelated_profile_update_does_not_revoke_immutable_authorization_proof(tmp_path):
    path, plan = _valid_authorization(tmp_path)
    profile = Path(json.loads(path.read_text(encoding="utf-8"))["character_profile_path"])
    profile.write_text("name: Updated unrelated default\n", encoding="utf-8")
    assert contracts.validate_mandatory_clothes_authorization(path, request_id="request-one",
        character_id="CHAR_002", plan=plan)["reference_id"] == "REF_00217"


def test_prepare_generation_json_parser_exposes_mandatory_clothes_opt_in():
    parser = generation_request._prepare_parser()
    actions = {action.dest: action for action in parser._actions}
    assert "mandatory_clothes_authorization" in actions
    assert actions["mandatory_clothes_authorization"].default == ""


@pytest.mark.parametrize("change", ["request", "character", "stage", "role", "kind", "subject", "source_hash", "item_hash"])
def test_mandatory_clothes_authorization_rejects_contract_tampering(tmp_path, change):
    path, plan = _valid_authorization(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    key, value = {
        "request": ("request_id", "other-request"), "character": ("character_id", "CHAR_003"),
        "stage": ("stage_id", "02_PHYSIQUE_FRONT"), "role": ("physical_role", "POSE"),
        "kind": ("item_kind", "ACCESSORY"), "subject": ("subject_label", "FEMALE"),
        "source_hash": ("clothes_reference_sha256", "0" * 64), "item_hash": ("item_plan_sha256", "1" * 64),
    }[change]
    record[key] = value
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(contracts.StylePackError):
        contracts.validate_mandatory_clothes_authorization(path, request_id="request-one",
                                                           character_id="CHAR_002", plan=plan)


def test_body_library_only_stays_strict_without_opt_in(tmp_path, monkeypatch):
    paths = manager.make_paths(tmp_path, "LORANDESORE")
    monkeypatch.setattr(manager, "validated_stage_outputs", lambda *args: {})
    plan = {"request_id": "request-one", "character_id": "NONE", "generation_purpose": "SCENE",
            "startup_parameter_selection": {"resolved_parameters": {"aux_body_decision": "SELECTED"}}}
    clothes = tmp_path / "optional-clothes.png"
    clothes.write_bytes(b"not authorized")
    call = {"slots": [{"path": str(clothes), "sha256": manager.sha256(clothes),
                       "active_roles": ["CLOTHES"], "physically_attach": True}]}
    selections = {"character": {"choice": "NONE"}, "reference_policy": {"choice": "BODY_LIBRARY_ONLY"},
                  "style": {"choice": "PROJECT_STYLE:LORANDESORE"}}
    with pytest.raises(manager.StylePackError, match="BODY_LIBRARY_ONLY"):
        manager.validate_reference_policy_slots(paths, plan, call, selections)


def _manager_binding_fixture(tmp_path: Path):
    auth_path, plan = _valid_authorization(tmp_path)
    paths = manager.make_paths(tmp_path, "LORANDESORE")
    identity_root = paths.generations / "01_APPROVED_CHARACTERS" / "CHAR_002_Test"
    identity_root.mkdir(parents=True)
    assembly_path, assembly_hash = _write(identity_root / "assembly.png", b"approved assembly")
    profile = identity_root / "CHARACTER_PROFILE.yaml"
    profile.write_text("name: Test\n", encoding="utf-8")
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    item_path = Path(auth["item_plan_path"])
    item = json.loads(item_path.read_text(encoding="utf-8"))
    item.update(character_assembly=assembly_path, character_assembly_sha256=assembly_hash)
    item_raw = json.dumps(item).encode()
    item_path.write_bytes(item_raw)
    auth.update(character_assembly_path=assembly_path, character_assembly_sha256=assembly_hash,
                character_profile_path=str(profile.resolve()), item_plan_sha256=hashlib.sha256(item_raw).hexdigest())
    source_path, source_hash = _write(paths.pack / "01_WORK" / "CLOTHES_CROPS" / "robe.png", b"approved request clothing source")
    auth.update(clothes_reference_path=source_path, clothes_reference_sha256=source_hash, subject_label_sha256=source_hash)
    auth_path.write_text(json.dumps(auth, ensure_ascii=False), encoding="utf-8")
    style_path, style_hash = _write(paths.pack / "01_WORK" / "STYLE_CROPS" / "style.png", b"style")
    face_path, face_hash = _write(identity_root / "face.png", b"approved face")
    registry = [{"reference_id": "REF_00217", "stored_relative_path": "01_WORK/CLOTHES_CROPS/robe.png",
        "status": "APPROVED", "reference_scope": "REQUEST", "request_id": "request-one",
        "primary_role": "CLOTHES_CORE", "subject_label": "MALE", "subject_label_status": "REVIEWED",
        "subject_label_sha256": source_hash, "source_sha256": source_hash, "generator_safe": "YES", "user_approved": "YES"}]
    plan.update({"style_name": "LORANDESORE", "pack_path": str(paths.pack), "identity_style_name": "LORANDESORE",
        "identity_pack_path": str(paths.pack), "mandatory_clothes_authorization_path": str(auth_path.resolve()),
        "generation_workflow": {"mode": "SINGLE_PASS"},
        "startup_parameter_selection": {"resolved_parameters": {"aux_body_decision": "SELECTED"}},
        "selected_references": {"character_assembly": {"path": assembly_path, "sha256": assembly_hash},
                               "primary_face": {"path": face_path, "sha256": face_hash}}})
    binding = contracts.validate_mandatory_clothes_authorization(auth_path, request_id="request-one",
                                                                 character_id="CHAR_002", plan=plan)
    plan["mandatory_clothes_authorization"] = binding
    call = {"stage_id": "SINGLE_PASS", "slots": [
        {"path": style_path, "sha256": style_hash, "active_roles": ["STYLE"], "physically_attach": True},
        {"path": face_path, "sha256": face_hash, "active_roles": ["FACE_IDENTITY"], "physically_attach": True},
        {"path": assembly_path, "sha256": assembly_hash, "active_roles": ["CHARACTER_ASSEMBLY"], "physically_attach": True},
        {"path": source_path, "sha256": source_hash, "active_roles": ["CLOTHES"], "physically_attach": True},
    ]}
    return paths, identity_root, auth_path, plan, call, registry


def test_manager_accepts_exact_authorized_physical_clothes_slot(tmp_path, monkeypatch):
    paths, _, _, plan, call, registry = _manager_binding_fixture(tmp_path)
    monkeypatch.setattr(manager, "validated_stage_outputs", lambda *args: {})
    monkeypatch.setattr(manager, "validate_plan_reference", lambda *args, **kwargs: {"sha256": plan["mandatory_clothes_authorization"]["source_sha256"]})
    monkeypatch.setattr(manager, "read_csv", lambda path: registry)
    selections = {"character": {"choice": "CHAR_002"}, "reference_policy": {"choice": "BODY_LIBRARY_ONLY"},
                  "style": {"choice": "PROJECT_STYLE:LORANDESORE"}}
    manager.validate_reference_policy_slots(paths, plan, call, selections)
    altered = dict(call, slots=[*call["slots"][:-1], dict(call["slots"][-1], sha256="0" * 64)])
    with pytest.raises(manager.StylePackError):
        manager.validate_reference_policy_slots(paths, plan, altered, selections)


@pytest.mark.parametrize("workflow,stage", [("MULTI_STAGE", "SINGLE_PASS"), ("SINGLE_PASS", "02_PHYSIQUE_FRONT")])
def test_manager_rejects_consent_when_actual_workflow_or_call_stage_differs(tmp_path, monkeypatch, workflow, stage):
    paths, _, _, plan, call, registry = _manager_binding_fixture(tmp_path)
    monkeypatch.setattr(manager, "validated_stage_outputs", lambda *args: {})
    monkeypatch.setattr(manager, "validate_plan_reference", lambda *args, **kwargs: {"sha256": plan["mandatory_clothes_authorization"]["source_sha256"]})
    monkeypatch.setattr(manager, "read_csv", lambda path: registry)
    plan["generation_workflow"] = {"mode": workflow}
    call["stage_id"] = stage
    selections = {"character": {"choice": "CHAR_002"}, "reference_policy": {"choice": "BODY_LIBRARY_ONLY"},
                  "style": {"choice": "PROJECT_STYLE:LORANDESORE"}}
    with pytest.raises(manager.StylePackError, match="actual SINGLE_PASS"):
        manager.validate_reference_policy_slots(paths, plan, call, selections)


def test_guard_revalidates_exact_authorized_physical_clothes_slot(tmp_path, monkeypatch):
    paths, identity_root, auth_path, plan, call, registry = _manager_binding_fixture(tmp_path)
    monkeypatch.setattr(manager, "validate_plan_reference", lambda *args, **kwargs: {"sha256": plan["mandatory_clothes_authorization"]["source_sha256"]})
    monkeypatch.setattr(manager, "read_csv", lambda path: registry)
    selected_slots = call["slots"]
    source = guard.validate_mandatory_clothes_call_binding(plan, selected_slots, auth_path.parent, identity_root,
                                                            actual_mode="SINGLE_PASS", actual_stage="SINGLE_PASS")
    assert source == Path(plan["mandatory_clothes_authorization"]["source_path"])
    with pytest.raises(guard.GuardError):
        guard.validate_mandatory_clothes_call_binding(plan, selected_slots[:-1], auth_path.parent, identity_root,
                                                       actual_mode="SINGLE_PASS", actual_stage="SINGLE_PASS")


@pytest.mark.parametrize("mode,stage", [("MULTI_STAGE", "SINGLE_PASS"), ("SINGLE_PASS", "02_PHYSIQUE_FRONT")])
def test_guard_rejects_consent_when_actual_workflow_or_selected_stage_differs(tmp_path, monkeypatch, mode, stage):
    _, identity_root, auth_path, plan, call, registry = _manager_binding_fixture(tmp_path)
    monkeypatch.setattr(manager, "validate_plan_reference", lambda *args, **kwargs: {"sha256": plan["mandatory_clothes_authorization"]["source_sha256"]})
    monkeypatch.setattr(manager, "read_csv", lambda path: registry)
    plan["generation_workflow"] = {"mode": "SINGLE_PASS"}
    with pytest.raises(guard.GuardError, match="actual SINGLE_PASS"):
        guard.validate_mandatory_clothes_call_binding(plan, call["slots"], auth_path.parent, identity_root,
                                                       actual_mode=mode, actual_stage=stage)


def _base_state(ids):
    return {"request_purpose": "CHARACTER_BASE", "status": "ACTIVE", "task_kind": "IMAGE_GENERATION",
            "required_stages": [{"id": stage, "status": "PENDING", "evidence": []} for stage in ids],
            "attempts": [], "scope_changes": []}


def test_completion_accepts_only_recorded_single_pass_tail_after_exact_base_prefix():
    ids = [*guard.CHARACTER_BASE_STAGES, "SINGLE_PASS"]
    state = _base_state(ids)
    assert "five-stage prefix" in guard.completion_block_reason(state)
    state["scope_changes"] = [{"added_required_stages": ["SINGLE_PASS"]}]
    assert guard.completion_block_reason(state).startswith("Task completion is forbidden while required stages remain pending")


def test_guard_allows_scene_clothing_plan_after_five_accepted_base_stages(tmp_path, monkeypatch):
    auth_path, plan = _valid_authorization(tmp_path)
    binding = contracts.validate_mandatory_clothes_authorization(auth_path, request_id="request-one",
        character_id="CHAR_002", plan=plan)
    plan.update(generation_workflow={"mode": "SINGLE_PASS"}, fidelity=90,
        auxiliary_body_reference_decision="SELECTED", mandatory_clothes_authorization_path=str(auth_path.resolve()),
        mandatory_clothes_authorization=binding)
    state = {"request_purpose": "CHARACTER_BASE", "request_id": "request-one", "task_revision": 9,
        "required_stages": [{"id": stage, "status": "COMPLETED", "evidence": [f"{stage}.png"],
            "accepted_attempt_binding": {"stage": stage}} for stage in guard.CHARACTER_BASE_STAGES]
            + [{"id": "SINGLE_PASS", "status": "PENDING", "evidence": []}],
        "scope_changes": [{"added_required_stages": ["SINGLE_PASS"]}], "attempts": []}
    monkeypatch.setattr(guard, "validate_stage_attempt_binding", lambda state, stage, evidence, **kwargs: {"stage": stage})
    guard.reconcile_plan_required_stages(state, plan)
    assert state["request_purpose"] == "SCENE"
    assert [row["id"] for row in state["required_stages"]] == [*guard.CHARACTER_BASE_STAGES, "SINGLE_PASS"]


@pytest.mark.parametrize("mutation", ["missing_authorization", "missing_scope_change", "wrong_extra_stage", "pending_base_stage"])
def test_guard_rejects_unrecorded_or_incomplete_base_continuation(tmp_path, monkeypatch, mutation):
    auth_path, plan = _valid_authorization(tmp_path)
    binding = contracts.validate_mandatory_clothes_authorization(auth_path, request_id="request-one",
        character_id="CHAR_002", plan=plan)
    plan.update(generation_workflow={"mode": "SINGLE_PASS"}, fidelity=90,
        auxiliary_body_reference_decision="SELECTED", mandatory_clothes_authorization_path=str(auth_path.resolve()),
        mandatory_clothes_authorization=binding)
    entries = [{"id": stage, "status": "COMPLETED", "evidence": [f"{stage}.png"],
        "accepted_attempt_binding": {"stage": stage}} for stage in guard.CHARACTER_BASE_STAGES]
    entries.append({"id": "SINGLE_PASS", "status": "PENDING", "evidence": []})
    state = {"request_purpose": "CHARACTER_BASE", "request_id": "request-one", "task_revision": 9,
        "required_stages": entries, "scope_changes": [{"added_required_stages": ["SINGLE_PASS"]}], "attempts": []}
    if mutation == "missing_authorization":
        plan.pop("mandatory_clothes_authorization_path")
    elif mutation == "missing_scope_change":
        state["scope_changes"] = []
    elif mutation == "wrong_extra_stage":
        state["required_stages"][-1]["id"] = "WARDROBE"
    else:
        state["required_stages"][2]["status"] = "PENDING"
    monkeypatch.setattr(guard, "validate_stage_attempt_binding", lambda state, stage, evidence, **kwargs: {"stage": stage})
    with pytest.raises(guard.GuardActionRequired):
        guard.reconcile_plan_required_stages(state, plan)


@pytest.mark.parametrize("ids", [
    ["02_PHYSIQUE_FRONT", *guard.CHARACTER_BASE_STAGES[1:]],
    [*guard.CHARACTER_BASE_STAGES, "SINGLE_PASS", "SINGLE_PASS"],
    [*guard.CHARACTER_BASE_STAGES, "WARDROBE"],
])
def test_completion_rejects_missing_reordered_duplicate_or_unrecorded_tail(ids):
    state = _base_state(ids)
    state["scope_changes"] = [{"added_required_stages": ["SINGLE_PASS"]}]
    assert "five-stage prefix" in guard.completion_block_reason(state)
