from argparse import Namespace
import json

from PIL import Image
import pytest

from tools import clothing_details, item_reference_manager as items, task_execution_guard as guard


@pytest.mark.parametrize("recover", [False, True])
def test_wardrobe_cannot_finish_after_only_the_generated_card(tmp_path, monkeypatch, recover):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    state_path = tmp_path / "EXECUTION_GUARD.json"
    state = guard.create_guard(state_path, request_id="wardrobe", goal="Create a wardrobe package.",
                               deliverable="Full card and clothing details.", task_kind="IMAGE_GENERATION",
                               required_stages=["WARDROBE_CARD", "ITEM_DESIGN", "ITEM_SCALE_FIT"])
    plan = {
        "request_id": "wardrobe", "generation_purpose": "SCENE",
        "startup_parameter_selection": {
            "menu_contract": "CLOTHING_PLAN",
            "delegation": {"clothing_plan": {"item_kind": "CLOTHING", "request_id": "wardrobe",
                                             "clothing_details_layout": {"id": "CLOTHING_DETAILS_V1"}}},
        },
    }
    if recover:
        plan["startup_parameter_selection"]["delegation"]["clothing_plan"].pop("clothing_details_layout")
    else:
        guard.reconcile_plan_required_stages(state, plan)
    card = tmp_path / "WARDROBE_CARD.png"
    Image.new("RGB", (600, 400), "#786b69").save(card)
    card_hash = clothing_details.sha256(card)
    snapshot = tmp_path / "EXECUTED_PLAN_FIXTURE.json"
    snapshot.write_text(json.dumps(plan), encoding="utf-8")
    state["attempts"] = [{
        "attempt_id": "wardrobe-attempt", "stage": "SINGLE_PASS", "task_revision": 0,
        "status": "DELIVERED" if recover else "RESULT_AVAILABLE", "result_status": "TEST",
        "result_evidence": [str(card)],
        "provider_artifact": {"path": str(card), "sha256": card_hash, "result_status": "TEST"},
        "execution_snapshot": {"reference_plan_snapshot_path": str(snapshot),
                               "reference_plan_snapshot_sha256": clothing_details.sha256(snapshot)},
    }]
    if recover:
        state["task_revision"] = 1
        state["events"].append({"event": "USER_CORRECTION", "stage": "CLOTHING_DETAILS", "correction_impact": "PRESERVE"})
    guard.atomic_write_json(state_path, state)
    if recover:
        assert guard.continuation_guidance(state)["action"] == "RECOVER_WARDROBE_DETAILS"
        state = guard.recover_wardrobe_details(state_path)
    assert guard.pending_required_stages(state) == ["WARDROBE_CARD", "CLOTHING_DETAILS"]
    qa = {"checks": {"WARDROBE_CARD": {"status": "PASS", "sha256": card_hash, "evidence": "Original card."}}}
    visual_qa = tmp_path / "ITEM_VISUAL_QA.json"
    visual_qa.write_text(json.dumps(qa), encoding="utf-8")
    state = guard.checkpoint(state_path, event="STAGE_COMPLETED", stage="WARDROBE_CARD",
                             evidence=[str(card)], summary="The original card passed QA.")
    assert guard.completion_block_reason(state) == "Task completion is forbidden while required stages remain pending: CLOTHING_DETAILS"
    assert guard.continuation_guidance(state)["action"] == "ASSEMBLE_AND_REVIEW_WARDROBE_PACKAGE"
    with pytest.raises(guard.GuardError, match="clothing-details sheet"):
        guard.checkpoint(state_path, event="RESULT_DELIVERED", attempt_id="wardrobe-attempt",
                         delivery_evidence=[str(card)], summary="Attempt to deliver only the card.")

    regions = tmp_path / "REGIONS.json"
    regions.write_text(json.dumps({
        "card_sha256": card_hash, "title": "Деловой комплект", "details_heading": "Крой и фурнитура",
        "front": [150, 35, 295, 390], "materials_and_palette": [455, 35, 590, 390],
        "details": [{"box": [165, 130, 270, 180], "caption": "Три пуговицы"}],
    }), encoding="utf-8")
    output = items.build_clothing_details(Namespace(card_image=card, regions=regions, replace=False, recover=recover))
    sheet = tmp_path / "CLOTHING_DETAILS.png"
    assert output["path"] == str(sheet)
    assert card_hash == clothing_details.sha256(card)
    qa["checks"]["CLOTHING_DETAILS"] = {
        "status": "PASS", "sha256": output["sha256"], "evidence": "Isolated outfit and source details.",
    }
    visual_qa.write_text(json.dumps(qa), encoding="utf-8")
    state = guard.checkpoint(state_path, event="STAGE_COMPLETED", stage="CLOTHING_DETAILS",
                             evidence=[str(sheet)], summary="The isolated details sheet passed QA.")
    assert not guard.pending_required_stages(state)
    with pytest.raises(guard.GuardError, match="clothing-details sheet paths"):
        guard.checkpoint(state_path, event="RESULT_DELIVERED", attempt_id="wardrobe-attempt",
                         delivery_evidence=[str(card)], summary="Omit the ready clothing-details file.")
    guard.checkpoint(state_path, event="RESULT_DELIVERED", attempt_id="wardrobe-attempt",
                     delivery_evidence=[str(card), str(sheet)], summary="Deliver both images.")
    complete = guard.checkpoint(state_path, event="COMPLETE", summary="Finish the wardrobe package.")
    assert complete["status"] == "COMPLETE"
    assert complete["attempts"] == state["attempts"] if recover else len(complete["attempts"]) == 1
    sheet.write_bytes(card.read_bytes())
    assert "CLOTHING_DETAILS" in guard.completion_block_reason(guard.load_guard(state_path))
