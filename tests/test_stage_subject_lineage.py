import csv
import json
from unittest.mock import patch

import pytest

from tools import style_pack_manager as manager


def plan(label="MALE_ANATOMY", evidence="approved profile", request="request"):
    return {"request_id": request, "character_id": "CHAR_001", "target_identity_compatibility": {
        "target": {"character_id": "CHAR_001", "target_anatomy": label, "anatomy_evidence_source": evidence}}}


@pytest.mark.parametrize("source_label,current_label,source_evidence,current_evidence,allowed", [
    ("MALE_ANATOMY", "MALE_ANATOMY", "approved profile", "approved profile", True),
    ("FEMALE_ANATOMY", "FEMALE_ANATOMY", "approved profile", "approved profile", True),
    ("FEMALE_ANATOMY", "MALE_ANATOMY", "approved profile", "approved profile", False),
    ("MALE_ANATOMY", "FEMALE_ANATOMY", "approved profile", "approved profile", False),
    ("MALE_ANATOMY", "MALE_ANATOMY", "old evidence", "new evidence", False),
    ("MALE_ANATOMY", "MALE_ANATOMY", "UNKNOWN", "UNKNOWN", False),
    ("MALE_ANATOMY", "MALE_ANATOMY", "", "", False),
])
def test_stage_outputs_retain_qa_snapshot_subject(tmp_path, source_label, current_label, source_evidence, current_evidence, allowed):
    paths = manager.make_paths(tmp_path, "TEST")
    plan_path = paths.generations / "00_PENDING/request/REFERENCE_PLAN.json"
    plan_path.parent.mkdir(parents=True)
    current = plan(current_label, current_evidence)
    plan_path.write_text(json.dumps(current), encoding="utf-8")
    snapshot = plan_path.parent / "snapshot.json"
    snapshot.write_text(json.dumps(plan(source_label, source_evidence)), encoding="utf-8")
    output = plan_path.parent / "stage.bin"
    output.write_bytes(b"synthetic QA-bound source")
    contract = plan_path.parent / "contract.json"
    contract.write_text(json.dumps({"stage_id": "STAGE", "plan_snapshot": str(snapshot)}), encoding="utf-8")
    with paths.generation_manifest.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["request_id", "character_id", "status", "style_file", "reference_plan", "notes"])
        writer.writeheader()
        writer.writerow({"request_id": "request", "character_id": "CHAR_001", "status": "STAGING", "style_file": str(output),
                         "reference_plan": str(plan_path.resolve()), "notes": "[STAGE_ID=STAGE]"})
    receipt = {"record_status": "STAGING", "qa_failed": [], "qa_contract": str(contract)}
    with patch.object(manager, "generation_qa_evidence", return_value=receipt):
        outputs = manager.validated_stage_outputs(paths, plan_path, current)
    assert bool(outputs) is allowed
    if allowed:
        record = outputs["STAGE"]
        assert record["subject_label"] == source_label
        assert record["subject_label_sha256"] == manager.sha256(output)
        assert record["plan_snapshot_sha256"] == manager.sha256(snapshot)
        assert source_evidence in record["subject_label_evidence"]


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("source_label", ["MALE_ANATOMY", "FEMALE_ANATOMY", "UNKNOWN"])
def test_final_stage_reference_cannot_be_reclassified(tmp_path, packed, source_label):
    paths = manager.make_paths(tmp_path, "TEST")
    folder = paths.generations / "00_PENDING/request"
    folder.mkdir(parents=True)
    source = folder / "source.bin"
    source.write_bytes(b"validated output")
    source_record = {"path": str(source.resolve()), "sha256": manager.sha256(source), "subject_label": source_label,
                     "subject_label_evidence": "QA_PLAN_SNAPSHOT_SHA256=verified:original evidence", "snapshot_subject_evidence": "approved profile",
                     "source_character_id": "CHAR_001"}
    file = source
    slot = {"active_roles": ["ART_POSE_SOFT"]}
    if packed:
        file = folder / "pack.bin"
        file.write_bytes(b"verified source pack")
        manifest = folder / "pack.json"
        manifest.write_text(json.dumps({"request_id": "request", "sources": [{"path": str(source), "sha256": manager.sha256(source)}]}), encoding="utf-8")
        slot["manifest_path"] = str(manifest)
    with patch.object(manager, "validated_stage_outputs", return_value={"STAGE": source_record}), \
         patch.object(manager, "enforce_reference_compatibility", wraps=manager.enforce_reference_compatibility) as check:
        if source_label == "MALE_ANATOMY":
            manager.validate_resolved_slot_compatibility(paths, plan(), slot, file.resolve())
            assert check.call_args.args[1]["subject_label_evidence"].startswith("QA_PLAN_SNAPSHOT_SHA256=")
        else:
            with pytest.raises(manager.StylePackError):
                manager.validate_resolved_slot_compatibility(paths, plan(), slot, file.resolve())


def test_single_pass_registration_preserves_validated_named_stage():
    args = manager.build_parser().parse_args([
        "record-generation", "--style-name", "TEST", "--image", "received.png", "--request-id", "request",
        "--description", "received requested portrait", "--character-id", "CHAR_001", "--attempt-id", "attempt",
        "--reference-plan", "plan.json", "--visual-review-json", "review.json",
        "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
    ])
    args.visual_anatomy_status = args.visual_defects_status = args.visual_prompt_status = "PASS"
    document = {"generation_workflow": {"mode": "SINGLE_PASS"}, "execution_call": {"stage_id": "PORTRAIT"}}
    _, stage, _ = manager.evaluate_generation_qa(document, args)
    assert stage == "PORTRAIT"
