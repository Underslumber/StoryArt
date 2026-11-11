from argparse import Namespace
import hashlib
import json
from pathlib import Path

import pytest

from tools.pose_reference_manager import import_atlas
from tools.style_pack_manager import StylePackError, validate_prompt_only_body_library_review


def fixture(tmp_path: Path, label="FEMALE"):
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.jpg").write_bytes(b"catalog-hash-fixture")
    sha = hashlib.sha256(b"catalog-hash-fixture").hexdigest()
    row = {"id": "one", "system_ja": "sys", "system_zh": "sys", "pose_number": "P001",
           "pose_name_ja": "stand", "pose_name_zh": "stand", "category_ja": "base", "category_zh": "base",
           "angle_ja": "front", "angle_zh": "front", "source_relative_path": "one.jpg",
           "sha256": sha, "width": 100, "height": 200, "source_commit": "test"}
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"images": [row]}), encoding="utf-8")
    reviews = tmp_path / "reviews.json"
    reviews.write_text(json.dumps({"schema_version": 1, "reviews": [{"source_id": "one", "sha256": sha,
        "subject_label": label, "subject_label_evidence": "reviewed source label", "visual_qa_status": "PASS",
        "safety_status": "SAFE", "anatomy_reliability": "RELIABLE_FOR_POSE", "review_notes": "reviewed",
        "review_evidence": "test source review"}]}), encoding="utf-8")
    library = tmp_path / "BODY_REFERENCE_LIBRARY" / "POSE_ATLAS"
    import_atlas(Namespace(source_index=index, source_root=source, review_file=reviews, library_root=library))
    registry = {"active_auxiliary_type": "POSE_ATLAS", "types": {"POSE_ATLAS": {
        "enabled": True, "authority": "SOFT_POSE_ONLY", "root": "BODY_REFERENCE_LIBRARY/POSE_ATLAS",
        "manifest": "BODY_REFERENCE_LIBRARY/POSE_ATLAS/BODY_REFERENCE_MANIFEST.csv"}}}
    config = tmp_path / "config" / "body_reference_types.json"
    config.parent.mkdir()
    config.write_text(json.dumps(registry), encoding="utf-8")
    return Namespace(workspace=tmp_path, target_anatomy="MALE_ANATOMY", prompt_only_physique=True,
        aux_body_decision="SELECTED", body_library_candidates_reviewed=0, body_library_relevant_candidates_total=0,
        aux_body_selection_note="All eligible male metadata checked: no confirmed male sources."), library, config


def test_verified_empty_target_pool_keeps_selected_choice(tmp_path):
    args, _, _ = fixture(tmp_path)
    validate_prompt_only_body_library_review(args)
    assert args.aux_body_decision == "SELECTED"
    assert args.body_library_candidates_reviewed == args.body_library_relevant_candidates_total == 0


def test_false_empty_pool_with_male_source_rejected(tmp_path):
    args, _, _ = fixture(tmp_path, "MALE")
    with pytest.raises(StylePackError, match="positive"):
        validate_prompt_only_body_library_review(args)


@pytest.mark.parametrize("change", ["disabled", "other_type", "wrong_path", "missing_catalog", "stale_bytes", "unknown_target", "no_note", "negative_count"])
def test_empty_fallback_fails_closed(tmp_path, change):
    args, library, config = fixture(tmp_path)
    registry = json.loads(config.read_text())
    if change == "disabled": registry["types"]["POSE_ATLAS"]["enabled"] = False
    if change == "other_type": registry["active_auxiliary_type"] = "NATURAL"
    if change == "wrong_path": registry["types"]["POSE_ATLAS"]["root"] = "other"
    config.write_text(json.dumps(registry))
    if change == "missing_catalog": (library / "POSE_GROUPS.json").unlink()
    if change == "stale_bytes":
        data = json.loads((library / "POSE_GROUPS.json").read_text())
        (library / data["groups"][0]["views"][0]["relative_path"]).write_bytes(b"changed")
    if change == "unknown_target": args.target_anatomy = "UNKNOWN"
    if change == "no_note": args.aux_body_selection_note = ""
    if change == "negative_count": args.body_library_relevant_candidates_total = -1
    with pytest.raises((StylePackError, ValueError)):
        validate_prompt_only_body_library_review(args)


def test_positive_pool_review_still_required(tmp_path):
    args, _, _ = fixture(tmp_path)
    args.body_library_relevant_candidates_total = 2
    with pytest.raises(StylePackError, match="reviewed 0 of 2"):
        validate_prompt_only_body_library_review(args)
    args.body_library_candidates_reviewed = 2
    validate_prompt_only_body_library_review(args)
