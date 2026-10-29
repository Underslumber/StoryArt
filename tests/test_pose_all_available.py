import copy
import csv
import hashlib
import json
import argparse
from pathlib import Path

import pytest

from tools.body_reference_type_router import load_registry, reference_context as routed_context
from tools.pose_reference_manager import PoseReferenceError, reference_context, validate_library
from tools.style_pack_manager import StylePackError, command_body_ref_context, parse_aux_body_references


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_library(tmp_path: Path):
    library = tmp_path / "BODY_REFERENCE_LIBRARY" / "POSE_ATLAS"
    source_dir = library / "sources"
    source_dir.mkdir(parents=True)
    specs = [
        ("s1", "ANHAO_g1", "front", "FEMALE", "CONFIRMED", "FAIL", b"view-front"),
        ("s2", "ANHAO_g1", "side", "UNKNOWN", "UNKNOWN", "NOT_CHECKED", b"view-side"),
        ("s3", "ANHAO_g1", "high", "MALE", "CONFIRMED", "PASS", b"view-high"),
    ]
    views, rows = [], []
    for i, (sid, gid, angle, label, label_status, qa, content) in enumerate(specs):
        rel = f"sources/{sid}.jpg"
        (library / rel).write_bytes(content)
        digest = sha(content)
        views.append({"source_id": sid, "angle": angle, "sha256": digest, "relative_path": rel,
                      "reference_path": rel, "selection_available": True,
                      "subject_label": label, "subject_label_status": label_status,
                      "subject_label_sha256": digest if label_status == "CONFIRMED" else "",
                      "subject_label_evidence": "reviewed" if label_status == "CONFIRMED" else "",
                      "visual_qa_status": qa, "safety_status": "REVIEW_REQUIRED",
                      "anatomy_reliability": "UNCERTAIN", "generator_eligible": False,
                      "review_notes": "advisory note", "review_evidence": ""})
        rows.append({"ref_id": f"BR_{i:04d}", "source_id": sid, "group_id": gid,
                     "sha256": digest, "source_sha256": digest, "subject_label": label,
                     "subject_label_sha256": views[-1]["subject_label_sha256"],
                     "subject_label_status": label_status, "generator_safe": "NO", "generator_path": "",
                     "allowed_roles": "STAGING_ONLY;POSE_SOFT;AUX_POSE;AUX_CAMERA",
                     "not_for_roles": "BODY_BUILD_TARGET;IDENTITY;STYLE", "selection_available": "YES",
                     "reference_path": rel, "selection_policy": "ALL_SOURCE_IMAGES", "angle": angle,
                     "notes": "advisory note"})
    group = {"group_id": "ANHAO_g1", "provider": "anhao", "system": "test", "pose_number": "P1",
             "pose_name_ja": "立つ", "pose_name_zh": "站立", "category_ja": "basic", "category_zh": "基础",
             "expected_angles": ["front", "oblique", "side", "high", "low"],
             "source_angles": ["front", "side", "high"], "available_angles": ["front", "high", "side"],
             "status": "PARTIAL", "source_view_count": 3, "views": views}
    (library / "POSE_GROUPS.json").write_text(json.dumps({"schema_version": 1, "source_count": 3,
        "group_count": 1, "selection_policy": "ALL_SOURCE_IMAGES", "groups": [group]}, ensure_ascii=False), encoding="utf-8")
    with (library / "BODY_REFERENCE_MANIFEST.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    registry = copy.deepcopy(load_registry())
    registry["active_auxiliary_type"] = "POSE_ATLAS"
    registry["types"]["POSE_ATLAS"]["enabled"] = True
    config = tmp_path / "config"; config.mkdir()
    (config / "body_reference_types.json").write_text(json.dumps(registry), encoding="utf-8")
    return library, registry


def test_all_available_exposes_every_view_and_keeps_advisories(tmp_path):
    library, registry = make_library(tmp_path)
    assert validate_library(library)["status"] == "CATALOG_COMPLETE"
    context = reference_context(library)
    assert context["view_count"] == 3
    assert {v["source_id"] for g in context["groups"] for v in g["views"]} == {"s1", "s2", "s3"}
    assert next(v for v in context["groups"][0]["views"] if v["source_id"] == "s2")["subject_label"] == "UNKNOWN"
    male = reference_context(library, "MALE_ANATOMY")
    assert [v["source_id"] for g in male["groups"] for v in g["views"]] == ["s3"]
    routed = routed_context("POSE_ATLAS", {"target_anatomy": "UNKNOWN", "query": ""}, tmp_path, registry)
    assert routed["view_count"] == 3
    import io
    from contextlib import redirect_stdout
    output = io.StringIO()
    args = argparse.Namespace(workspace=tmp_path, pose="", family="", group_id="", view="", camera_angle="",
                              target_anatomy="UNKNOWN", target_anatomy_evidence="UNKNOWN", target_prompt="",
                              target_name="", character_profile="", character_id="NONE")
    with redirect_stdout(output):
        command_body_ref_context(args)
    assert json.loads(output.getvalue())["view_count"] == 3


def test_explicit_selected_quality_fail_view_is_attachable_with_exact_label(tmp_path):
    library, _ = make_library(tmp_path)
    row = next(csv.DictReader((library / "BODY_REFERENCE_MANIFEST.csv").open(encoding="utf-8-sig", newline="")))
    selected = parse_aux_body_references(tmp_path, [row["ref_id"] + "=STAGING_ONLY"], "SELECTED", "", False, False,
        {"target_anatomy": "FEMALE_ANATOMY", "anatomy_evidence_source": "CURRENT_PROMPT_EXPLICIT:female"})
    assert selected[0]["source_id"] == "s1"
    assert selected[0]["path"].endswith("s1.jpg")
    male = next(row for row in csv.DictReader((library / "BODY_REFERENCE_MANIFEST.csv").open(encoding="utf-8-sig", newline=""))
                if row["source_id"] == "s3")
    with pytest.raises(StylePackError):
        parse_aux_body_references(tmp_path, [male["ref_id"] + "=STAGING_ONLY"], "SELECTED", "", False, False,
            {"target_anatomy": "FEMALE_ANATOMY", "anatomy_evidence_source": "CURRENT_PROMPT_EXPLICIT:female"})


def test_stale_source_hash_blocks_selection_without_sibling_substitution(tmp_path):
    library, _ = make_library(tmp_path)
    selected = reference_context(library, source_id="s2")["groups"][0]["views"][0]
    Path(selected["path"]).write_bytes(b"changed bytes")
    with pytest.raises(PoseReferenceError, match="stale"):
        reference_context(library, source_id="s2")
    with pytest.raises(PoseReferenceError, match="path/hash"):
        validate_library(library)

