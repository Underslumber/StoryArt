import copy
import json
from argparse import Namespace
from pathlib import Path
import pytest
from tools import style_pack_manager as m
from tools import task_execution_guard as g


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return m.sha256(path)


@pytest.fixture
def fixture(tmp_path):
    paths = m.make_paths(tmp_path, "RENDER")
    root = paths.generations / "00_PENDING" / "REQ"
    root.mkdir(parents=True)
    output = root / "image.png"
    output.write_bytes(b"immutable original image")
    rejected = root / "REJECTED" / "image.png"
    rejected.parent.mkdir()
    rejected.write_bytes(output.read_bytes())
    plan_path = root / "executed.json"
    plan_hash = write(plan_path, {"style_name": "RENDER", "pack_path": str(paths.pack), "generation_purpose": "SCENE"})
    qa = {key: "PASS" for key in ["ANATOMY_REVIEW", "VISIBLE_DEFECTS", "STYLE", "ATTACHMENTS", "FACE_GEOMETRY", "BODY_PROPORTIONS", "BODY_SILHOUETTE", "CLOTHING", "SUBJECT_ACCURACY", "POSE_CONTACTS", "OBJECTS_AND_ACTION"]}
    qa.update({"CANVAS": "FAIL", "COMPOSITION": "FAIL", "PROMPT_ADHERENCE": "FAIL"})
    visual = {"attempt_id": "ATTEMPT", "output_sha256": m.sha256(output), "prompt_adherence": {"constraints": [{"constraint": m.AGENT_CONTAINMENT_CLAUSE, "status": "FAIL"}]}}
    contract_path = root / "technical-contract.json"
    contract = {"qa_results": qa, "plan_snapshot": str(plan_path), "plan_content_sha256": plan_hash, "visual_review": visual}
    contract_hash = write(contract_path, contract)
    technical_path = root / "technical.json"
    technical = {"qa_contract": str(contract_path), "qa_contract_sha256": contract_hash, "visual_review": visual, "output_sha256": m.sha256(output)}
    technical_hash = write(technical_path, technical)
    source = {"generation_id": "GEN_SOURCE", "request_id": "REQ", "character_id": "CHAR_002", "style_name": "RENDER", "status": "REJECTED", "style_file": str(rejected), "archive_file": str(output), "qa_output_sha256": m.sha256(output), "qa_evidence": str(technical_path), "qa_receipt_sha256": technical_hash, "qa_contract_sha256": contract_hash, "qa_plan_sha256": plan_hash, "reference_plan": str(plan_path), "fidelity": "90", "risk_level": "D3"}
    m.write_csv(paths.generation_manifest, m.GENERATION_FIELDS, [source])
    attempt = {"attempt_id": "ATTEMPT", "task_revision": 0, "stage": "SINGLE_PASS", "status": "REJECTED", "result_status": "REJECTED", "provider_artifact": {"path": str(output), "sha256": m.sha256(output)}, "execution_snapshot": {"reference_plan_snapshot_path": str(plan_path), "reference_plan_snapshot_sha256": plan_hash}}
    state = {"schema_version": 1, "request_id": "REQ", "request_purpose": "SCENE", "task_kind": "IMAGE_GENERATION", "task_revision": 0, "status": "ACTIVE", "phase": "ESCALATION_RECORDED", "attempts": [attempt], "active_attempt": None, "required_stages": [{"id": "SCENE", "status": "PENDING", "evidence": []}], "events": []}
    write(root / "EXECUTION_GUARD.json", state)
    user = root / "USER_CONTRACT.md"
    user.write_text("Identity/style/anatomy locked. Flexible composition. No text.", encoding="utf-8")
    review = root / "independent.md"
    review.write_text("Independent reviewer: all user constraints PASS", encoding="utf-8")
    ids = ["identity", "body_anatomy", "wardrobe", "earrings", "style", "weapons", "hero_focus", "rengrot", "hammer_contact", "electricity", "barrier", "kennes", "location", "prohibitions", "causality", "composition_freedom"]
    report = {"schema_version": 1, "request_id": "REQ", "source_generation": "GEN_SOURCE", "attempt_id": "ATTEMPT", "output_sha256": m.sha256(output), "technical_qa_sha256": technical_hash, "waived_agent_clause": m.AGENT_CONTAINMENT_CLAUSE, "user_authorization_quote": m.AUTHOR_CONTRACT_AUTHORIZATION, "author_approval": "NOT_GIVEN", "scope_correction": {"origin": "AGENT_ADDED", "evidence": "Original author camera/composition explicitly flexible"}, "user_contract": {"path": str(user), "sha256": m.sha256(user)}, "author_visual": {"status": "PASS", "author": "independent reviewer", "independent_review_path": str(review), "independent_review_sha256": m.sha256(review), "full_user_contract_assessed": True, "criteria": [{"id": key, "status": "PASS", "evidence": "Independent visible evidence for " + key} for key in ids], "layers": {key: {"status": "PASS", "evidence": "Actual author requirement verified"} for key in qa}}}
    return paths, root, state, source, report, contract, technical


def test_eligible_correction_keeps_original_record_and_no_approval(fixture):
    paths, root, state, source, report, _, _ = fixture
    before = copy.deepcopy(m.read_csv(paths.generation_manifest))
    source_hash = m.sha256(Path(source["qa_evidence"]))
    write(root / "input.json", report)
    m.command_accept_author_contract(Namespace(style_name="RENDER", workspace=paths.workspace, request_id="REQ", source_generation="GEN_SOURCE", acceptance_json=str(root / "input.json")))
    rows = m.read_csv(paths.generation_manifest)
    assert rows[0] == before[0]
    assert rows[1]["status"] == "TEST" and rows[1]["parent_generation"] == "GEN_SOURCE"
    assert m.sha256(Path(source["qa_evidence"])) == source_hash
    changed = json.loads((root / "EXECUTION_GUARD.json").read_text())
    assert changed["attempts"] == state["attempts"]
    receipt = json.loads(Path(rows[1]["qa_evidence"]).read_text())
    assert receipt["execution_conformance"] == "FAIL" and receipt["author_approval"] == "NOT_GIVEN"
    binding = g.validate_stage_attempt_binding(changed, "SCENE", [str(root / "image.png")])
    assert binding["acceptance_generation_id"] == rows[1]["generation_id"]
    assert g.completion_block_reason(changed) is not None
    g.checkpoint(root / "EXECUTION_GUARD.json", event="RESULT_DELIVERED", summary="Delivered existing author-contract TEST", attempt_id="ATTEMPT", delivery_evidence=["codex://message/actual-delivery"])
    g.checkpoint(root / "EXECUTION_GUARD.json", event="STAGE_COMPLETED", summary="Existing scene reviewed and delivered", stage="SCENE", evidence=[str(root / "image.png")])
    delivered = json.loads((root / "EXECUTION_GUARD.json").read_text())
    assert g.completion_block_reason(delivered) is None
    assert delivered["attempts"] == state["attempts"]
    receipt_path = Path(rows[1]["qa_evidence"])
    original = receipt_path.read_bytes()
    receipt_path.write_bytes(original + b" ")
    assert g.completion_block_reason(delivered) is not None
    receipt_path.write_bytes(original)
    assert g.completion_block_reason(delivered) is None


@pytest.mark.parametrize("field,value", [("attempt_id", "OTHER"), ("output_sha256", "0"*64), ("technical_qa_sha256", "0"*64), ("waived_agent_clause", "Ignore wrong style"), ("user_authorization_quote", "yes"), ("author_approval", "APPROVED")])
def test_mismatch_and_arbitrary_override_denied(fixture, field, value):
    paths, _, state, _, report, _, _ = fixture
    report[field] = value
    with pytest.raises(m.StylePackError): m.validate_author_contract_acceptance(paths, state, report)


@pytest.mark.parametrize("layer", ["STYLE", "ANATOMY_REVIEW", "FACE_GEOMETRY", "BODY_PROPORTIONS", "CLOTHING"])
def test_actual_visual_fail_cannot_be_waived(fixture, layer):
    paths, root, state, source, report, contract, technical = fixture
    contract["qa_results"][layer] = "FAIL"
    digest = write(root / "technical-contract.json", contract)
    source["qa_contract_sha256"] = digest
    technical["qa_contract_sha256"] = digest
    digest = write(root / "technical.json", technical)
    source["qa_receipt_sha256"] = digest
    report["technical_qa_sha256"] = digest
    m.write_csv(paths.generation_manifest, m.GENERATION_FIELDS, [source])
    with pytest.raises(m.StylePackError): m.validate_author_contract_acceptance(paths, state, report)


def test_actual_author_constraint_not_waivable(fixture):
    paths, root, state, _, report, _, _ = fixture
    user = root / "USER_CONTRACT.md"
    user.write_text(m.AGENT_CONTAINMENT_CLAUSE)
    report["user_contract"]["sha256"] = m.sha256(user)
    with pytest.raises(m.StylePackError): m.validate_author_contract_acceptance(paths, state, report)


def test_incomplete_independent_review_denied(fixture):
    paths, _, state, _, report, _, _ = fixture
    report["author_visual"]["criteria"].pop()
    with pytest.raises(m.StylePackError): m.validate_author_contract_acceptance(paths, state, report)


@pytest.mark.parametrize("after_write", [False, True])
def test_retry_recovers_receipt_before_manifest_failure(fixture, monkeypatch, after_write):
    paths, root, state, source, report, _, _ = fixture
    write(root / "input.json", report)
    args = Namespace(style_name="RENDER", workspace=paths.workspace, request_id="REQ", source_generation="GEN_SOURCE", acceptance_json=str(root / "input.json"))
    append = m.append_generation
    def fail_append(*args, **kwargs):
        if after_write:
            append(*args, **kwargs)
        raise OSError("interrupted manifest write")
    monkeypatch.setattr(m, "append_generation", fail_append)
    with pytest.raises(OSError):
        m.command_accept_author_contract(args)
    receipt_path = root / "AUTHOR_ACCEPTANCE_GEN_SOURCE.json"
    receipt_bytes = receipt_path.read_bytes()
    monkeypatch.setattr(m, "append_generation", append)
    m.command_accept_author_contract(args)
    m.command_accept_author_contract(args)
    rows = m.read_csv(paths.generation_manifest)
    assert len(rows) == 2 and rows[0]["status"] == "REJECTED"
    assert rows[1]["generation_id"] == json.loads(receipt_bytes)["generation_id"]
    assert receipt_path.read_bytes() == receipt_bytes
    assert json.loads((root / "EXECUTION_GUARD.json").read_text())["attempts"] == state["attempts"]
