import argparse
import copy
import hashlib
import json

import pytest

from tools import style_pack_manager as manager
from tools.style_pack_common import StylePackError
from tools.style_rendering_contract import (
    BEGIN, CRITERIA, build_style_contract, render_style_authority, required_style_regions,
    validate_style_contract, validate_style_review,
)
from tools import task_execution_guard as guard


@pytest.fixture
def plan(tmp_path):
    source = tmp_path / "approved-source.dat"
    source.write_bytes(b"synthetic reviewed STYLE bytes, no generated image")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    p = {"schema_version": 7, "semantic_qa_schema": 5, "request_id": "synthetic-style-test",
         "style_name": "TEST_LINE_STYLE", "fidelity": 90, "generation_purpose": "SCENE",
         "face_review": {"face_visible": True}, "canvas_contract": {"full_figure": True},
         "selected_references": {"style": [{"path": str(source), "sha256": digest}]},
         "selected_source_reviews": [{"path": str(source), "sha256": digest, "role": "STYLE",
             "review_outcome": "PASS", "view": "FULL_RESOLUTION",
             "review_findings": "Observed thin selective line and flat graphic material groups.",
             "limitations": "Rendering only; no geometry or costume authority."}]}
    p["style_rendering_contract"] = build_style_contract(p)
    p["execution_call"] = {"slots": [{"path": str(source), "sha256": digest, "active_roles": ["STYLE"],
                                      "slot": 2, "physically_attach": True}], "prompt": {}}
    p["execution_call"]["prompt"]["text"] = render_style_authority(p, "Preserve the exact requested subject and scene.", p["execution_call"]["slots"])
    return p


def review_for(plan):
    return {"style_review": {"status": "PASS",
        "sources": [{k: s[k] for k in ("path", "sha256")} for s in plan["style_rendering_contract"]["sources"]],
        "criteria": {key: {"status": "PASS", "evidence": f"Synthetic comparison of {key}: source and output use the same observed rendering principle."} for key in CRITERIA},
        "regions": {key: {"status": "PASS", "evidence": f"Synthetic {key} comparison: source's line and grouped forms persist in this visible output region."} for key in required_style_regions(plan, "SINGLE_PASS")}}}


def test_source_authority_uses_actual_observations_and_preserves_other_styles(plan):
    contract = plan["style_rendering_contract"]
    assert contract["sources"][0]["observed_rendering"].startswith("Observed thin selective line")
    prompt = plan["execution_call"]["prompt"]["text"]
    assert "slot(s) 2" in prompt
    assert "broad brush" not in prompt  # Does not turn line-art into LORANDESORE.
    assert prompt.endswith("Preserve the exact requested subject and scene.")
    assert render_style_authority(plan, prompt, plan["execution_call"]["slots"]) == prompt
    validate_style_contract(plan, plan["execution_call"], check_files=True)


@pytest.mark.parametrize("mutation", ["contract", "source_bytes", "role", "omitted", "not_physical", "prompt"])
def test_execution_rejects_lost_changed_or_reassigned_style(plan, mutation):
    if mutation == "contract":
        plan["style_rendering_contract"]["sources"][0]["limitations"] = "different"
    elif mutation == "source_bytes":
        from pathlib import Path
        Path(plan["style_rendering_contract"]["sources"][0]["path"]).write_bytes(b"changed")
    elif mutation == "role":
        plan["execution_call"]["slots"][0]["active_roles"] = ["BODY"]
    elif mutation == "omitted":
        plan["execution_call"]["slots"] = []
    elif mutation == "not_physical":
        plan["execution_call"]["slots"][0]["physically_attach"] = False
    else:
        plan["execution_call"]["prompt"]["text"] = "Only copy the identity rendering."
    with pytest.raises(StylePackError):
        validate_style_contract(plan, plan["execution_call"], check_files=True)


def test_guard_independently_rejects_missing_style_before_other_readiness_gates(plan, tmp_path):
    plan["gate_status"] = "READY_FOR_GENERATION"
    plan["execution_call"]["slots"] = []
    path = tmp_path / "REFERENCE_PLAN.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(guard.GuardError, match="physically attached"):
        guard.validate_reference_plan(path, request_id=plan["request_id"])


@pytest.mark.parametrize("mutation", ["missing", "wrong_source", "palette_only", "body_missing", "body_na", "contradiction", "invented_percent"])
def test_blanket_style_pass_is_insufficient(plan, mutation):
    report = review_for(plan)
    s = report["style_review"]
    if mutation == "missing":
        report.clear()
    elif mutation == "wrong_source":
        s["sources"][0]["sha256"] = "0" * 64
    elif mutation == "palette_only":
        s["criteria"] = {"colour_structure": s["criteria"]["colour_structure"]}
    elif mutation == "body_missing":
        del s["regions"]["body"]
    elif mutation == "body_na":
        s["regions"]["body"].update(status="NOT_APPLICABLE", reason="Ignore the visibly full figure in this output.")
    elif mutation == "contradiction":
        s["criteria"]["edges"]["status"] = "FAIL"
    else:
        s["measured_fidelity"] = 90
    with pytest.raises(StylePackError):
        validate_style_review(plan, report, "SINGLE_PASS")


def test_style_failure_and_not_checked_remain_honest(plan):
    report = review_for(plan)
    report["style_review"]["criteria"]["edges"]["status"] = "FAIL"
    report["style_review"]["status"] = "FAIL"
    assert validate_style_review(plan, report, "SINGLE_PASS")["status"] == "FAIL"
    report["style_review"]["criteria"]["edges"]["status"] = "NOT_CHECKED"
    report["style_review"]["status"] = "NOT_CHECKED"
    assert validate_style_review(plan, report, "SINGLE_PASS")["status"] == "NOT_CHECKED"


@pytest.mark.parametrize("observed", ["FAIL", "NOT_CHECKED"])
def test_cli_style_pass_cannot_override_comparative_result(plan, observed):
    from tools.style_pack_qa import evaluate_generation_qa
    from tools.style_pack_manager import build_parser
    args = build_parser().parse_args(["record-generation", "--workspace", ".", "--style-name", "TEST",
                                     "--request-id", "TEST", "--image", "synthetic.png", "--description", "fixture",
                                     "--character-id", "NEW", "--attempt-id", "fixture", "--reference-plan", "fixture.json",
                                     "--visual-review-json", "review.json"])
    for name in vars(args):
        if name.startswith("qa_"):
            setattr(args, name, "PASS")
    args.visual_anatomy_status = args.visual_defects_status = args.visual_prompt_status = "PASS"
    args.visual_style_status = observed
    args.stage_id = ""
    plan["generation_workflow"] = {"mode": "SINGLE_PASS"}
    if observed == "NOT_CHECKED":
        with pytest.raises(StylePackError, match="QA was not performed: STYLE"):
            evaluate_generation_qa(plan, args)
    else:
        assert "STYLE" in evaluate_generation_qa(plan, args)[0]


def test_legacy_snapshot_and_generator_default_are_not_rewritten(plan):
    legacy = copy.deepcopy(plan)
    legacy["schema_version"] = 6
    del legacy["style_rendering_contract"]
    assert render_style_authority(legacy, "historical prompt") == "historical prompt"
    assert validate_style_review(legacy, {}, "SINGLE_PASS") is None
    default = {"schema_version": 7, "selected_references": {"style": []}, "style_rendering_contract": None}
    assert render_style_authority(default, "user chose generator default") == "user chose generator default"


def test_back_view_does_not_require_review_of_an_invisible_face(plan):
    assert "face" not in required_style_regions(plan, "04_PHYSIQUE_BACK")
    assert "body" in required_style_regions(plan, "04_PHYSIQUE_BACK")
    assert "face" in required_style_regions(plan, "03_PHYSIQUE_SIDE")


def test_new_plan_cannot_drop_style_contract_at_qa(plan):
    del plan["style_rendering_contract"]
    with pytest.raises(StylePackError, match="contract differs"):
        validate_style_review(plan, {}, "SINGLE_PASS")


def test_manager_visual_review_requires_comparison_bound_to_output_and_snapshot(plan, tmp_path):
    out = tmp_path / "fixture.dat"
    out.write_bytes(b"synthetic output")
    prompt = plan["execution_call"]["prompt"]["text"]
    ph = hashlib.sha256(prompt.encode()).hexdigest()
    snapshot = {"execution_call": plan["execution_call"], "prompt_sha256": ph,
                "reference_plan_snapshot_sha256": "1" * 64}
    report = {"schema_version": 1, "request_id": plan["request_id"], "attempt_id": "attempt",
        "task_revision": 1, "output_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "prompt_sha256": ph, "executed_plan_sha256": "1" * 64, "author": "synthetic reviewer",
        "anatomy_review": {"status": "PASS", "checked_scope": "Synthetic full-frame anatomy scope.", "findings": []},
        "visible_defect_review": {"status": "PASS", "checked_scope": "Synthetic full-frame artifacts scope.", "findings": []},
        "prompt_adherence": {"status": "PASS", "checked_scope": "FULL_EXECUTED_PROMPT", "all_explicit_constraints_assessed": True,
            "constraints": [{"constraint": c, "status": "PASS", "evidence": "Synthetic prompt comparison for " + c}
                            for c in manager.prompt_review_clauses(prompt)]}}
    path = tmp_path / "review.json"
    def check():
        path.write_text(json.dumps(report), encoding="utf-8")
        return manager.validate_visual_review(path, request_id=plan["request_id"], attempt_id="attempt", task_revision=1,
                                            output=out, execution_snapshot=snapshot, plan=plan, stage_id="SINGLE_PASS")
    with pytest.raises(StylePackError, match="style_review"):
        check()
    report.update(review_for(plan))
    assert check()["style_review"]["status"] == "PASS"
    out.write_bytes(b"different output")
    with pytest.raises(StylePackError, match="output_sha256"):
        check()
