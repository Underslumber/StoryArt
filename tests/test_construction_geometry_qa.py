import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tools import style_pack_manager as manager
from tools.style_pack_contracts import (
    CONSTRUCTION_GEOMETRY_METHOD_ID,
    CONSTRUCTION_GEOMETRY_NUMERIC_SOURCE,
    CONSTRUCTION_GEOMETRY_VERSION,
)


REQUEST_ID = "geometry-request"
CHARACTER_ID = "CHAR_001"
STAGE_ID = "02_PHYSIQUE_FRONT"
VIEW = "FRONT"
VALUES = {
    "head_units": 7.8,
    "pubic_height_fraction": 0.50,
    "lower_to_upper_leg_ratio": 0.94,
    "ankle_width_head_ratio": 0.24,
    "foot_length_head_ratio": 0.95,
    "neck_head_ratio": 0.35,
    "neck_jaw_ratio": 0.738,
}


def make_plan():
    return {
        "request_id": REQUEST_ID,
        "character_id": CHARACTER_ID,
        "generation_purpose": "CHARACTER_BASE",
        "character_base_contract": {"age_group": "ADULT"},
        "body_proportion_contract": {},
        "generation_workflow": {
            "mode": "MULTI_STAGE",
            "stages": [{"stage_id": STAGE_ID, "required_qa": ["BODY_PROPORTIONS", "LIMB_PROPORTIONS"]}],
        },
        "execution_call": {"stage_id": STAGE_ID},
    }


def make_args():
    args = argparse.Namespace(
        stage_id=STAGE_ID,
        qa_attachments="PASS",
        qa_canvas="PASS",
        qa_stage_layer="PASS",
        qa_face="NOT_CHECKED",
        qa_body_silhouette="NOT_CHECKED",
        qa_body_proportions="NOT_CHECKED",
        qa_limb_proportions="NOT_CHECKED",
        visual_anatomy_status="PASS",
        visual_defects_status="PASS",
        visual_prompt_status="PASS",
    )
    return args


def write_evidence(root, monkeypatch, *, values=None, approval=True, validate=True):
    monkeypatch.setattr(manager, "assert_active_request_path", lambda _path: None)
    root.mkdir(parents=True, exist_ok=True)
    output = root / "output.png"
    output.write_bytes(b"the exact output image")
    diagram = root / "scheme.png"
    diagram.write_bytes(b"approved geometry diagram")
    geometry = root / "geometry.json"
    geometry.write_text(json.dumps({"request_id": REQUEST_ID, "values": values or VALUES}), encoding="utf-8")
    approval_path = root / "author-approval.json"
    if approval:
        approval_path.write_text(json.dumps({
            "schema_version": 1,
            "request_id": REQUEST_ID,
            "character_id": CHARACTER_ID,
            "approval_quote": "Утвердить эту конструктивную геометрию",
            "approval_source": "native structured answer in this chat",
            "diagram_path": str(diagram),
            "diagram_sha256": manager.sha256(diagram),
            "geometry_path": str(geometry),
            "geometry_sha256": manager.sha256(geometry),
            "scope": "CONSTRUCTION_GEOMETRY_ONLY",
            "approves_previous_illustration": False,
            "numeric_visible_landmarks_remain": "NOT_CHECKED",
        }, ensure_ascii=False), encoding="utf-8")
    receipt = root / "construction-receipt.json"
    receipt.write_text(json.dumps({
        "schema_version": 1,
        "request_id": REQUEST_ID,
        "character_id": CHARACTER_ID,
        "output_sha256": manager.sha256(output),
        "stage_id": STAGE_ID,
        "view": VIEW,
        "method_id": CONSTRUCTION_GEOMETRY_METHOD_ID,
        "method_version": CONSTRUCTION_GEOMETRY_VERSION,
        "numeric_source": CONSTRUCTION_GEOMETRY_NUMERIC_SOURCE,
        "diagram": {"path": str(diagram), "sha256": manager.sha256(diagram)},
        "geometry_source": {"path": str(geometry), "sha256": manager.sha256(geometry)},
        "approval_evidence": {"path": str(approval_path), "sha256": manager.sha256(approval_path) if approval else "0" * 64},
        "geometry_values": values or VALUES,
        "image_scheme_consistency": {
            "status": "PASS",
            "reviewer": "Independent reviewer",
            "evidence": "Compared the rendered silhouette and visible joint flow with the approved construction scheme across the whole body.",
        },
    }), encoding="utf-8")
    validated = None
    if validate:
        validated = manager.validate_construction_geometry_receipt(
            receipt,
            request_root=root,
            request_id=REQUEST_ID,
            character_id=CHARACTER_ID,
            output=output,
            stage_id=STAGE_ID,
            plan=make_plan(),
        )
    return output, diagram, geometry, approval_path, receipt, validated


def bind_qa_args(args, root, output, receipt):
    plan_path = root / "REFERENCE_PLAN.json"
    if not plan_path.is_file():
        plan_path.write_text(json.dumps(make_plan()), encoding="utf-8")
    args.image = str(output)
    args.reference_plan = str(plan_path)
    args.construction_geometry_receipt = str(receipt)
    return args


def test_no_receipt_keeps_numeric_layers_mandatory():
    args = make_args()
    with pytest.raises(manager.StylePackError, match="BODY_PROPORTIONS, LIMB_PROPORTIONS"):
        manager.evaluate_generation_qa(make_plan(), args, include_results=True)


def test_construction_geometry_replaces_only_unchecked_numeric_requirements(tmp_path, monkeypatch):
    root = tmp_path / "00_PENDING" / REQUEST_ID
    output, _diagram, _geometry, _approval, receipt, _proof = write_evidence(root, monkeypatch)
    args = bind_qa_args(make_args(), root, output, receipt)
    failed, stage, required, results = manager.evaluate_generation_qa(make_plan(), args, include_results=True)
    assert failed == []
    assert stage == STAGE_ID
    assert "AUTHOR_CONFIRMED_CONSTRUCTION_GEOMETRY" in required
    assert "BODY_PROPORTIONS" not in required
    assert "LIMB_PROPORTIONS" not in required
    assert results["AUTHOR_CONFIRMED_CONSTRUCTION_GEOMETRY"] == "PASS"
    assert args._construction_geometry_numeric_results == {
        "BODY_PROPORTIONS": "NOT_CHECKED", "LIMB_PROPORTIONS": "NOT_CHECKED"
    }


def test_explicit_numeric_fail_cannot_be_waived(tmp_path, monkeypatch):
    root = tmp_path / "00_PENDING" / REQUEST_ID
    output, _diagram, _geometry, _approval, receipt, _proof = write_evidence(root, monkeypatch)
    args = bind_qa_args(make_args(), root, output, receipt)
    args.qa_limb_proportions = "FAIL"
    with pytest.raises(manager.StylePackError, match="cannot waive.*FAIL"):
        manager.evaluate_generation_qa(make_plan(), args, include_results=True)


def test_independent_numeric_pass_stays_pass_while_other_layer_uses_geometry(tmp_path, monkeypatch):
    root = tmp_path / "00_PENDING" / REQUEST_ID
    output, _diagram, _geometry, _approval, receipt, _proof = write_evidence(root, monkeypatch)
    args = bind_qa_args(make_args(), root, output, receipt)
    args.qa_body_proportions = "PASS"
    _failed, _stage, required, results = manager.evaluate_generation_qa(make_plan(), args, include_results=True)
    assert "BODY_PROPORTIONS" in required
    assert results["BODY_PROPORTIONS"] == "PASS"
    assert "LIMB_PROPORTIONS" not in required
    assert results["AUTHOR_CONFIRMED_CONSTRUCTION_GEOMETRY"] == "PASS"
    assert args._construction_geometry_numeric_results["LIMB_PROPORTIONS"] == "NOT_CHECKED"


def test_full_forged_private_header_cannot_replace_numeric_requirements():
    args = make_args()
    args._validated_construction_geometry = {
        "gate": "AUTHOR_CONFIRMED_CONSTRUCTION_GEOMETRY",
        "method_id": "AUTHOR_CONFIRMED_CONSTRUCTION_GEOMETRY",
        "method_version": "1",
        "numeric_source": "AUTHOR_CONFIRMED_CONSTRUCTION_NOT_VISIBLE_MEASUREMENT",
        "metric_validation": "PASS",
        "stage_id": STAGE_ID,
    }
    with pytest.raises(manager.StylePackError, match="Private construction geometry claims are ignored"):
        manager.evaluate_generation_qa(make_plan(), args, include_results=True)


@pytest.mark.parametrize("tamper", ["diagram", "geometry", "output"])
def test_hash_tampering_blocks_receipt(tmp_path, monkeypatch, tamper):
    output, diagram, geometry, _approval, receipt, _proof = write_evidence(tmp_path / "00_PENDING" / REQUEST_ID, monkeypatch)
    target = {"diagram": diagram, "geometry": geometry, "output": output}[tamper]
    target.write_bytes(target.read_bytes() + b"changed")
    with pytest.raises(manager.StylePackError, match="hash|SHA256|values"):
        manager.validate_construction_geometry_receipt(
            receipt, request_root=receipt.parent, request_id=REQUEST_ID,
            character_id=CHARACTER_ID, output=output, stage_id=STAGE_ID, plan=make_plan(),
        )


@pytest.mark.parametrize(
    "field,value",
    [("request_id", "another-request"), ("stage_id", "03_PHYSIQUE_SIDE"), ("view", "SIDE")],
)
def test_request_stage_and_view_mismatches_block_receipt(tmp_path, monkeypatch, field, value):
    root = tmp_path / "00_PENDING" / REQUEST_ID
    output, _diagram, _geometry, _approval, receipt, _proof = write_evidence(root, monkeypatch)
    data = json.loads(receipt.read_text(encoding="utf-8"))
    data[field] = value
    receipt.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(manager.StylePackError, match="request, character, stage, view, or method"):
        manager.validate_construction_geometry_receipt(
            receipt, request_root=root, request_id=REQUEST_ID,
            character_id=CHARACTER_ID, output=output, stage_id=STAGE_ID, plan=make_plan(),
        )


def test_cross_request_diagram_reference_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "00_PENDING" / REQUEST_ID
    output, _diagram, _geometry, _approval, receipt, _proof = write_evidence(root, monkeypatch)
    foreign = tmp_path / "00_PENDING" / "another-request" / "foreign.png"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"foreign diagram")
    data = json.loads(receipt.read_text(encoding="utf-8"))
    data["diagram"] = {"path": str(foreign), "sha256": manager.sha256(foreign)}
    receipt.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(manager.StylePackError, match="inside the current request folder"):
        manager.validate_construction_geometry_receipt(
            receipt, request_root=root, request_id=REQUEST_ID,
            character_id=CHARACTER_ID, output=output, stage_id=STAGE_ID, plan=make_plan(),
        )


def test_missing_author_approval_blocks_receipt(tmp_path, monkeypatch):
    root = tmp_path / "00_PENDING" / REQUEST_ID
    output, _diagram, _geometry, _approval, receipt, _proof = write_evidence(root, monkeypatch, approval=False, validate=False)
    with pytest.raises(manager.StylePackError, match="approval evidence must be a file"):
        manager.validate_construction_geometry_receipt(
            receipt, request_root=root, request_id=REQUEST_ID,
            character_id=CHARACTER_ID, output=output, stage_id=STAGE_ID, plan=make_plan(),
        )


def test_out_of_limits_geometry_is_not_accepted(tmp_path, monkeypatch):
    values = {**VALUES, "ankle_width_head_ratio": 0.31}
    _output, _diagram, _geometry, _approval, _receipt, _proof = write_evidence(
        tmp_path / "00_PENDING" / REQUEST_ID, monkeypatch, values=values, validate=False
    )
    # The receipt helper itself rejects values beyond the same anthropometric limit.
    with pytest.raises(manager.StylePackError, match="unchanged anthropometric limits"):
        manager.validate_construction_geometry_receipt(
            _receipt, request_root=_receipt.parent, request_id=REQUEST_ID,
            character_id=CHARACTER_ID, output=_output, stage_id=STAGE_ID, plan=make_plan(),
        )


def test_recorded_stage_replays_geometry_gate_and_blocks_stale_authority(tmp_path, monkeypatch):
    root = tmp_path / "STYLE_GENERATIONS" / "00_PENDING" / REQUEST_ID
    output, _diagram, _geometry, approval, receipt_path, _proof = write_evidence(root, monkeypatch)
    style_file = root / "CHARACTER_KITS" / "PHYSIQUE_FRONT" / "stage.png"
    style_file.parent.mkdir(parents=True)
    style_file.write_bytes(output.read_bytes())
    plan = make_plan()
    plan["generation_workflow"]["stages"][0]["required_qa"].append("BODY_SILHOUETTE")
    plan["generation_workflow"]["stages"].append({"stage_id": "03_PHYSIQUE_SIDE", "required_qa": []})
    plan["execution_call"]["prompt"] = {"text": "Canonical adult front physique with balanced proportions."}
    plan_path = root / "REFERENCE_PLAN.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    args = bind_qa_args(make_args(), root, style_file, receipt_path)
    args.qa_body_silhouette = "PASS"
    _failed, _stage, required, results = manager.evaluate_generation_qa(plan, args, include_results=True)
    assert "AUTHOR_CONFIRMED_CONSTRUCTION_GEOMETRY" in required
    assert "BODY_PROPORTIONS" not in required and "LIMB_PROPORTIONS" not in required
    assert args._construction_geometry_numeric_results == {
        "BODY_PROPORTIONS": "NOT_CHECKED", "LIMB_PROPORTIONS": "NOT_CHECKED"
    }
    review = {
        "author": "Test reviewer", "request_id": REQUEST_ID, "attempt_id": "attempt-1",
        "task_revision": 0, "output_sha256": manager.sha256(style_file),
        "prompt_sha256": hashlib.sha256(plan["execution_call"]["prompt"]["text"].encode()).hexdigest(),
        "executed_plan_sha256": manager.sha256(plan_path),
        "anatomy_review": {"status": "PASS"},
        "visible_defect_review": {"status": "PASS"},
        "prompt_adherence": {"status": "PASS"},
    }
    evidence_path = manager.write_generation_qa_evidence(
        style_file, plan_path, stage_id=STAGE_ID, qa_required=required, qa_results=results,
        construction_geometry=manager.validate_construction_geometry_receipt(
            receipt_path, request_root=root, request_id=REQUEST_ID, character_id=CHARACTER_ID,
            output=style_file, stage_id=STAGE_ID, plan=plan,
        ),
        numeric_qa_results=args._construction_geometry_numeric_results,
        status="STAGING", visual_review=review,
    )
    binding = manager.qa_manifest_digests(style_file, evidence_path)
    row = {
        "request_id": REQUEST_ID, "character_id": CHARACTER_ID, "status": "STAGING",
        "style_file": str(style_file), "qa_evidence": str(evidence_path),
        "notes": f"[STAGE_ID={STAGE_ID}]", **binding,
    }
    paths = manager.make_paths(tmp_path, "STYLE")
    manager.write_csv(paths.generation_manifest, manager.GENERATION_FIELDS, [row])
    assert manager.generation_candidate_accessible(row, style_file)
    assert row["qa_output_sha256"] == manager.sha256(style_file)
    assert row["qa_receipt_sha256"] == manager.sha256(evidence_path)
    assert row["qa_contract_sha256"] == manager.sha256(style_file.with_suffix(style_file.suffix + ".qa-contract.json"))
    assert row["qa_plan_sha256"] == manager.sha256(style_file.with_suffix(style_file.suffix + ".qa-plan.json"))
    contract_data = json.loads(style_file.with_suffix(style_file.suffix + ".qa-contract.json").read_text(encoding="utf-8"))
    assert manager.validate_construction_geometry_receipt(
        receipt_path, request_root=root, request_id=REQUEST_ID, character_id=CHARACTER_ID,
        output=style_file, stage_id=STAGE_ID, plan=json.loads(style_file.with_suffix(style_file.suffix + ".qa-plan.json").read_text(encoding="utf-8")),
    ) == contract_data["construction_geometry"]
    assert set(contract_data["qa_results"]) == set(contract_data["expected_qa_layers"])
    assert all(value == "PASS" for value in contract_data["qa_results"].values())
    visual = contract_data["visual_review"]
    snapshot_path = style_file.with_suffix(style_file.suffix + ".qa-plan.json")
    prompt_text = json.loads(snapshot_path.read_text(encoding="utf-8"))["execution_call"]["prompt"]["text"]
    assert visual["executed_plan_sha256"] == manager.sha256(snapshot_path)
    assert visual["prompt_sha256"] == hashlib.sha256(prompt_text.encode()).hexdigest()
    assert visual["output_sha256"] == manager.sha256(style_file)
    assert visual["request_id"] == REQUEST_ID and visual["attempt_id"]
    assert isinstance(visual["task_revision"], int) and visual["task_revision"] >= 0
    assert manager.generation_qa_evidence(row) is not None
    manager.validate_prior_stages(paths, plan, REQUEST_ID, "03_PHYSIQUE_SIDE")
    approval.write_text(approval.read_text(encoding="utf-8").replace(
        "CONSTRUCTION_GEOMETRY_ONLY", "OTHER_SCOPE"
    ), encoding="utf-8")
    assert manager.generation_qa_evidence(row) is None
    with pytest.raises(manager.StylePackError, match="02_PHYSIQUE_FRONT"):
        manager.validate_prior_stages(paths, plan, REQUEST_ID, "03_PHYSIQUE_SIDE")


def test_record_generation_stage_with_geometry_receipt_through_later_stage_replay():
    integration_path = Path(__file__).with_name("test_generation_workflow_integration.py")
    spec = importlib.util.spec_from_file_location("storyart_geometry_workflow_integration", integration_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    fixture = module.GenerationWorkflowIntegrationTests("test_all_five_canonical_stages_resolve_packs_pass_qa_and_complete")
    original_cli = fixture._cli
    captured = {}

    def intercept_recording(script, *raw_args, succeeds=True):
        args = [str(item) for item in raw_args]
        if script == "style_pack_manager.py" and len(args) > 0 and args[0] == "record-generation":
            pairs = {args[index]: args[index + 1] for index in range(1, len(args) - 1) if args[index].startswith("--")}
            if pairs.get("--stage-id") == "02_PHYSIQUE_FRONT":
                request_id = pairs["--request-id"]
                request_root = Path(pairs["--reference-plan"]).resolve().parent
                output = Path(pairs["--image"]).resolve()
                integration_values = {**VALUES, "head_units": 7.4}
                diagram = request_root / "test-construction.png"
                diagram.write_bytes(b"synthetic approved construction diagram")
                geometry = request_root / "test-construction.json"
                geometry.write_text(json.dumps({"request_id": request_id, "values": integration_values}), encoding="utf-8")
                approval = request_root / "test-approval.json"
                approval.write_text(json.dumps({
                    "schema_version": 1, "request_id": request_id, "character_id": "NEW",
                    "approval_quote": "Test fixture approves construction only.",
                    "approval_source": "synthetic integration fixture",
                    "diagram_path": str(diagram), "diagram_sha256": manager.sha256(diagram),
                    "geometry_path": str(geometry), "geometry_sha256": manager.sha256(geometry),
                    "scope": "CONSTRUCTION_GEOMETRY_ONLY", "approves_previous_illustration": False,
                    "numeric_visible_landmarks_remain": "NOT_CHECKED",
                }), encoding="utf-8")
                receipt = request_root / "test-construction-receipt.json"
                receipt.write_text(json.dumps({
                    "schema_version": 1, "request_id": request_id, "character_id": "NEW",
                    "output_sha256": manager.sha256(output), "stage_id": STAGE_ID, "view": VIEW,
                    "method_id": CONSTRUCTION_GEOMETRY_METHOD_ID, "method_version": CONSTRUCTION_GEOMETRY_VERSION,
                    "numeric_source": CONSTRUCTION_GEOMETRY_NUMERIC_SOURCE,
                    "diagram": {"path": str(diagram), "sha256": manager.sha256(diagram)},
                    "geometry_source": {"path": str(geometry), "sha256": manager.sha256(geometry)},
                    "approval_evidence": {"path": str(approval), "sha256": manager.sha256(approval)},
                    "geometry_values": integration_values,
                    "image_scheme_consistency": {
                        "status": "PASS", "reviewer": "Synthetic test reviewer",
                        "evidence": "Synthetic image compared to the test construction geometry scheme.",
                    },
                }), encoding="utf-8")
                captured.update(request_id=request_id, request_root=request_root,
                                approval=approval, receipt=receipt)
                filtered = []
                skip = False
                for index, item in enumerate(args):
                    if skip:
                        skip = False
                        continue
                    if item in {"--qa-body-proportions", "--qa-limb-proportions", "--limb-qa-evidence"}:
                        skip = True
                        continue
                    filtered.append(item)
                filtered.extend(("--construction-geometry-receipt", str(receipt)))
                args = filtered
        return original_cli(script, *args, succeeds=succeeds)

    fixture._cli = intercept_recording
    fixture.setUp()
    try:
        fixture._run_multistage_character_base(child=False)
        rows = manager.read_csv(fixture.generations / "GENERATION_MANIFEST.csv")
        front = next(row for row in rows if row.get("request_id") == captured["request_id"]
                     and f"[STAGE_ID={STAGE_ID}]" in row.get("notes", ""))
        assert manager.generation_qa_evidence(front) is not None
        contract = json.loads(Path(front["qa_evidence"]).with_name(Path(front["qa_evidence"]).name.replace("qa-evidence", "qa-contract")).read_text(encoding="utf-8"))
        assert contract["numeric_qa_results"] == {
            "BODY_PROPORTIONS": "NOT_CHECKED", "LIMB_PROPORTIONS": "NOT_CHECKED"
        }
        assert contract["qa_results"]["AUTHOR_CONFIRMED_CONSTRUCTION_GEOMETRY"] == "PASS"
        paths = manager.make_paths(fixture.workspace, fixture.style_name)
        plan = json.loads((captured["request_root"] / "REFERENCE_PLAN.json").read_text(encoding="utf-8"))
        manager.validate_prior_stages(paths, plan, captured["request_id"], "03_PHYSIQUE_SIDE")
        approval = Path(captured["approval"])
        approval.write_text(approval.read_text(encoding="utf-8").replace(
            "CONSTRUCTION_GEOMETRY_ONLY", "UNAUTHORIZED_SCOPE"
        ), encoding="utf-8")
        with pytest.raises(manager.StylePackError, match="02_PHYSIQUE_FRONT"):
            manager.validate_prior_stages(paths, plan, captured["request_id"], "03_PHYSIQUE_SIDE")
    finally:
        fixture.doCleanups()
