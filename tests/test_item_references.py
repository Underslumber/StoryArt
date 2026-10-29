from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest

from tools import item_reference_manager as items


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    generations = workspace / "STYLE_GENERATIONS"
    generations.mkdir()
    profile = generations / "01_APPROVED_CHARACTERS" / "CHAR_007" / "PROFILE.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text("approved", encoding="utf-8")
    assembly = profile.parent / "assembly.png"
    assembly.write_bytes(b"synthetic exact approved character assembly")
    request = generations / "00_PENDING" / "request-1"
    request.mkdir(parents=True)
    source_sheet = request / "item-sheet.png"
    source_sheet.write_bytes(b"synthetic separated item sheet")
    design = request / "design-crop.png"
    design.write_bytes(b"synthetic isolated item crop")
    scale = request / "scale-crop.png"
    scale.write_bytes(b"synthetic exact character scale crop")
    paths = items.manager.StylePaths(workspace, "STYLE", "STYLE", workspace / "STYLE_PROJECT_PACK", generations)
    monkeypatch.setattr(items.manager, "assert_active_request_path", lambda _path: None)
    monkeypatch.setattr(items, "_registered_character", lambda *_args: (paths, profile, assembly))
    monkeypatch.setattr(items.manager, "_approved_profile_identity", lambda *_args: {"target_anatomy": "FEMALE_ANATOMY"})
    plan = {
        "schema_version": 1,
        "status": "ITEM_PRODUCTION_PLAN",
        "request_id": "request-1",
        "style_name": "STYLE",
        "character_id": "CHAR_007",
        "character_profile": str(profile),
        "character_assembly": str(assembly),
        "character_assembly_sha256": digest(assembly),
        "item_id": "ITEM_SWORD",
        "item_kind": "WEAPON",
        "item_name": "Sword",
        "version": "v1",
        "design_spec": "steel sword",
        "dimensions": "blade length 1.1 m; grip length 0.2 m",
        "material": "steel and leather",
        "construction": "single-edged blade, wrapped grip",
        "attachment_constraints": "may lie beside character",
        "required_qa": list(items.QA_COMMON),
    }
    plan_path = request / "ITEM_REFERENCE_PLAN.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    qa = {
        "checks": {name: {"status": "PASS", "evidence": f"reviewed {name}"} for name in items.QA_COMMON},
        "item_design_source": {
            "sha256": digest(design), "subject_label": "NO_SUBJECT",
            "subject_label_evidence": "isolated crop contains only the item",
            "source_sheet_path": str(source_sheet), "source_sheet_sha256": digest(source_sheet),
        },
        "item_scale_fit_source": {
            "sha256": digest(scale), "character_id": "CHAR_007",
            "character_assembly_sha256": digest(assembly), "subject_label": "FEMALE",
            "subject_label_sha256": digest(scale), "subject_label_evidence": "reviewed exact character match",
            "source_sheet_path": str(source_sheet), "source_sheet_sha256": digest(source_sheet),
            "coverage": "THREE_QUARTER", "anatomy_compatibility": "FEMALE_ANATOMY",
            "anatomy_evidence_source": "reviewed exact character identity",
        },
    }
    qa_path = request / "item-qa.json"
    qa_path.write_text(json.dumps(qa), encoding="utf-8")
    args = argparse.Namespace(workspace=workspace, item_plan=str(plan_path), design_image=str(design),
                              scale_fit_image=str(scale), qa_json=str(qa_path), user_approved=True,
                              approval_quote="Approved item version v1.")
    return workspace, paths, request, profile, assembly, plan_path, qa_path, args


def test_item_reference_approval_and_registered_pair(lifecycle):
    workspace, paths, *_rest, args = lifecycle
    approved = items.approve(args)
    manifest = Path(approved["manifest_path"])
    loaded = items.validate_manifest(workspace, manifest, style_name="STYLE", character_id="CHAR_007")
    assert loaded["item_kind"] == "WEAPON"
    assert loaded["item_design"]["subject_label"] == "NO_SUBJECT"
    assert loaded["item_scale_fit"]["character_id"] == "CHAR_007"
    assert items.registered_asset(paths, Path(loaded["item_design"]["path"]))["asset_role"] == "ITEM_DESIGN"
    assert items.registered_asset(paths, Path(loaded["item_scale_fit"]["path"]))["asset_role"] == "ITEM_SCALE_FIT"
    assert items.list_items(workspace, "STYLE", "CHAR_007")[0]["manifest_path"] == str(manifest)


def test_no_subject_cannot_be_assigned_to_character_scale_source(lifecycle):
    workspace, _paths, _request, _profile, _assembly, _plan, qa_path, args = lifecycle
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    qa["item_scale_fit_source"]["subject_label"] = "NO_SUBJECT"
    qa_path.write_text(json.dumps(qa), encoding="utf-8")
    with pytest.raises(items.ItemReferenceError, match="MALE/FEMALE"):
        items.approve(args)


def test_design_and_scale_must_be_distinct_images(lifecycle):
    _workspace, _paths, _request, _profile, _assembly, _plan, _qa, args = lifecycle
    args.scale_fit_image = args.design_image
    with pytest.raises(items.ItemReferenceError, match="two separate"):
        items.approve(args)


def test_approved_item_source_tampering_invalidates_registry_binding(lifecycle):
    workspace, _paths, *_rest, args = lifecycle
    approved = items.approve(args)
    design = Path(approved["item_design"]["path"])
    design.write_bytes(b"changed after approval")
    with pytest.raises(items.ItemReferenceError, match="changed"):
        items.validate_manifest(workspace, Path(approved["manifest_path"]))


def test_selected_item_pair_becomes_two_physical_slots_and_revalidates(lifecycle):
    workspace, paths, *_rest, args = lifecycle
    approved = items.approve(args)
    item = items.validate_manifest(workspace, Path(approved["manifest_path"]))
    selected = {}
    for key, item_key in (("item_design", "item_design"), ("item_scale_fit", "item_scale_fit")):
        source = item[item_key]
        scale_qa = item["qa"]["item_scale_fit_source"]
        selected[key] = [{
            **source,
            "item_manifest": item["manifest_path"],
            "item_id": item["item_id"],
            "version": item["version"],
            "item_kind": item["item_kind"],
            "source_character_id": item["character_id"],
            "subject_label": "NO_SUBJECT" if key == "item_design" else scale_qa["subject_label"],
            "subject_label_sha256": source["sha256"],
            "subject_label_evidence": "ITEM_REFERENCE_REGISTRY" if key == "item_design" else scale_qa["subject_label_evidence"],
            "coverage": scale_qa.get("coverage", "UNKNOWN") if key == "item_scale_fit" else "ITEM_ONLY",
            "anatomy_compatibility": scale_qa.get("anatomy_compatibility", "UNKNOWN") if key == "item_scale_fit" else "UNKNOWN",
            "anatomy_evidence_source": scale_qa.get("anatomy_evidence_source", "UNKNOWN") if key == "item_scale_fit" else "UNKNOWN",
        }]
    slots = items.manager.build_attachment_plan(selected, [], 5)
    assert {role for slot in slots for role in slot["active_roles"]} == {"ITEM_DESIGN", "ITEM_SCALE_FIT"}
    plan = {
        "identity_style_name": "STYLE",
        "character_id": "CHAR_007",
        "selected_references": selected,
        "target_identity_compatibility": {"target": {
            "character_id": "CHAR_007", "target_anatomy": "FEMALE_ANATOMY",
            "anatomy_evidence_source": "approved profile",
        }, "references": []},
    }
    design_record = selected["item_design"][0]
    design_path = Path(design_record["path"])
    items.manager.validate_resolved_slot_compatibility(
        paths, plan, {"active_roles": ["ITEM_DESIGN"]}, design_path,
    )
    scale_record = selected["item_scale_fit"][0]
    items.manager.validate_resolved_slot_compatibility(
        paths, plan, {"active_roles": ["ITEM_SCALE_FIT"]}, Path(scale_record["path"]),
    )
    plan["target_identity_compatibility"]["target"]["target_anatomy"] = "MALE_ANATOMY"
    with pytest.raises(items.manager.StylePackError, match="BLOCKED"):
        items.manager.validate_resolved_slot_compatibility(
            paths, plan, {"active_roles": ["ITEM_SCALE_FIT"]}, Path(scale_record["path"]),
        )


def test_item_prompt_requires_exact_character_scale_without_forced_holding(lifecycle):
    _workspace, _paths, _request, _profile, _assembly, plan_path, _qa, _args = lifecycle
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan.update({"item_kind": "WEAPON", "item_name": "Sword", "version": "v1", "character_id": "CHAR_007",
                 "design_spec": "sword", "dimensions": "1.1m", "material": "steel",
                 "construction": "wrapped grip", "attachment_constraints": "may lie beside the character"})
    prompt = items.build_item_prompt(plan)
    assert "exact approved character CHAR_007" in prompt
    assert "may be beside the character" in prompt
    assert "do not force holding or wearing" in prompt
    assert "ITEM_DESIGN" in prompt and "ITEM_SCALE_FIT" in prompt


def test_clothing_prompt_and_qa_require_distinct_fit_and_layer_checks(lifecycle):
    _workspace, _paths, request, _profile, assembly, plan_path, qa_path, _args = lifecycle
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan.update({"item_kind": "CLOTHING", "item_name": "Coat", "version": "v1", "character_id": "CHAR_007",
                 "design_spec": "long coat", "dimensions": "to knee", "material": "wool",
                 "construction": "lined", "attachment_constraints": "collar and cuffs fit"})
    plan["required_qa"] = list(items.QA_COMMON + items.QA_CLOTHING)
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    with pytest.raises(items.ItemReferenceError, match="CLOTHING_FIT"):
        items._validate_qa(plan, qa, digest(request / "design-crop.png"), digest(request / "scale-crop.png"), digest(assembly))
    prompt = items.build_item_prompt(plan)
    assert "show the garment worn by the exact character" in prompt
    assert "fit, layer order, and contact points" in prompt


def test_clothing_approval_ignores_mutable_plan_qa_shortcut(lifecycle):
    _workspace, _paths, _request, _profile, assembly, plan_path, _qa, args = lifecycle
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan.update({"item_kind": "CLOTHING", "required_qa": []})
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(items.ItemReferenceError, match="CLOTHING_FIT"):
        items.approve(args)


def test_scale_fit_label_must_match_approved_character(lifecycle):
    _workspace, _paths, _request, _profile, _assembly, _plan, qa_path, args = lifecycle
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    qa["item_scale_fit_source"].update({"subject_label": "MALE", "anatomy_compatibility": "MALE_ANATOMY"})
    qa_path.write_text(json.dumps(qa), encoding="utf-8")
    with pytest.raises(items.ItemReferenceError, match="match the approved character profile"):
        items.approve(args)


def test_manifest_path_is_rejected_before_reading_arbitrary_json(lifecycle, monkeypatch):
    workspace, _paths, request, *_rest = lifecycle
    arbitrary = request / "not-an-approved-item.json"
    arbitrary.write_text("not json", encoding="utf-8")
    def unexpected_read(_path):
        raise AssertionError("arbitrary manifest bytes must not be read")
    monkeypatch.setattr(items, "_read_json", unexpected_read)
    with pytest.raises(items.ItemReferenceError, match="outside the approved character item library"):
        items.validate_manifest(workspace, arbitrary)


def test_legacy_scene_stages_have_no_typed_item_qa():
    stages = items.manager.build_multistage_attachment_plan({}, [], 5, purpose="SCENE")
    final_scene = next(stage for stage in stages if stage["stage_id"] == "05_FINAL_SCENE")
    assert not set(items.QA_COMMON + items.QA_CLOTHING).intersection(final_scene["required_qa"])


def _typed_item_runtime_qa_fixture(mode="SINGLE_PASS", stage_id="SINGLE_PASS", **item_qa):
    from argparse import Namespace

    design = {"item_manifest": "approved-item.json", "item_id": "ITEM_COAT", "version": "v1", "item_kind": "CLOTHING"}
    scale = dict(design)
    plan = {
        "generation_workflow": {"mode": mode, "stages": [{
            "stage_id": "05_FINAL_SCENE", "required_qa": list(items.QA_COMMON + items.QA_CLOTHING),
        }]},
        "execution_call": {"stage_id": stage_id},
        "generation_purpose": "SCENE", "semantic_qa_schema": 0,
        "selected_references": {"item_design": [design], "item_scale_fit": [scale]},
        "face_review": {"face_visible": False}, "canvas_contract": {"full_figure": False},
    }
    defaults = {
        "stage_id": "" if mode == "SINGLE_PASS" else stage_id,
        "qa_attachments": "PASS", "qa_canvas": "PASS", "qa_stage_layer": "PASS",
        "qa_face": "PASS", "qa_body_silhouette": "PASS", "qa_body_proportions": "PASS",
        "visual_anatomy_status": "PASS", "visual_defects_status": "PASS", "visual_prompt_status": "PASS",
    }
    defaults.update(item_qa)
    return plan, Namespace(**defaults)


def test_typed_clothing_qa_missing_and_fail_block_single_pass_record():
    plan, args = _typed_item_runtime_qa_fixture()
    with pytest.raises(items.manager.StylePackError, match="ITEM_DESIGN|CLOTHING_FIT"):
        items.manager.evaluate_generation_qa(plan, args)

    qa_flags = {
        "qa_item_design": "PASS", "qa_item_version": "PASS", "qa_item_dimensions": "PASS",
        "qa_item_material_construction": "PASS", "qa_item_character_scale": "PASS",
        "qa_clothing_fit": "PASS", "qa_layer_order": "PASS", "qa_contact_constraints": "PASS",
    }
    qa_flags["qa_item_design"] = "FAIL"
    plan, args = _typed_item_runtime_qa_fixture(**qa_flags)
    failed, _stage, required, results = items.manager.evaluate_generation_qa(plan, args, include_results=True)
    assert set(items.QA_COMMON + items.QA_CLOTHING).issubset(required)
    assert "ITEM_DESIGN" in failed
    assert results["ITEM_DESIGN"] == "FAIL"


def test_typed_clothing_qa_is_required_for_multi_stage_final_scene():
    qa_flags = {
        "qa_item_design": "PASS", "qa_item_version": "PASS", "qa_item_dimensions": "PASS",
        "qa_item_material_construction": "PASS", "qa_item_character_scale": "PASS",
        "qa_clothing_fit": "PASS", "qa_layer_order": "PASS", "qa_contact_constraints": "PASS",
    }
    plan, args = _typed_item_runtime_qa_fixture("MULTI_STAGE", "05_FINAL_SCENE", **qa_flags)
    failed, stage, required, results = items.manager.evaluate_generation_qa(plan, args, include_results=True)
    assert stage == "05_FINAL_SCENE"
    assert not failed
    assert set(items.QA_COMMON + items.QA_CLOTHING).issubset(required)
    assert all(results[layer] == "PASS" for layer in items.QA_COMMON + items.QA_CLOTHING)


@pytest.mark.parametrize("mode,stage_id", [("SINGLE_PASS", "SINGLE_PASS"), ("MULTI_STAGE", "05_FINAL_SCENE")])
@pytest.mark.parametrize("missing_layer", items.QA_COMMON + items.QA_CLOTHING)
def test_missing_schema_keeps_each_typed_item_qa_mandatory(mode, stage_id, missing_layer):
    qa_flags = {"qa_" + layer.lower(): "PASS" for layer in items.QA_COMMON + items.QA_CLOTHING}
    plan, args = _typed_item_runtime_qa_fixture(mode, stage_id, **qa_flags)
    plan.pop("semantic_qa_schema")
    failed, _, required, results = items.manager.evaluate_generation_qa(plan, args, include_results=True)
    assert not failed
    assert all(results[layer] == "PASS" for layer in items.QA_COMMON + items.QA_CLOTHING)

    setattr(args, "qa_" + missing_layer.lower(), "NOT_CHECKED")
    with pytest.raises(items.manager.StylePackError, match=missing_layer):
        items.manager.evaluate_generation_qa(plan, args)

    setattr(args, "qa_" + missing_layer.lower(), "FAIL")
    failed, _, _, results = items.manager.evaluate_generation_qa(plan, args, include_results=True)
    assert missing_layer in failed
    assert results[missing_layer] == "FAIL"


def test_structured_generation_request_accepts_repeatable_item_reference_array():
    parser = items.manager.build_parser()
    prepare = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction)).choices["prepare-generation"]
    item_action = next(action for action in prepare._actions if action.dest == "item_reference")
    assert isinstance(item_action, argparse._AppendAction)
    assert item_action.default == []
