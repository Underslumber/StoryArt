import csv
import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools.pose_reference_manager import PoseReferenceError, import_atlas, reference_context, validate_library


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture(tmp_path: Path):
    root = tmp_path / "src"
    img = root / "img"
    img.mkdir(parents=True)
    rows = []
    reviews = []
    specs = [
        ("a1", "sysA", "系统甲", "P001", "front", b"male-front", "MALE", "PASS", "SAFE", "RELIABLE_FOR_POSE", "evidence-a1"),
        ("a2", "sysA", "系统甲", "P001", "side", b"male-side", "MALE", "FAIL", "SAFE", "RELIABLE_FOR_POSE", "evidence-a2"),
        ("a3", "sysA", "系统甲", "P001", "high", b"male-high", "MALE", "PASS", "SAFE", "RELIABLE_FOR_POSE", "evidence-a3"),
        ("b1", "sysB", "系统乙", "P001", "front", b"female-front", "FEMALE", "PASS", "SAFE", "RELIABLE_FOR_POSE", "evidence-b1"),
        ("b2", "sysB", "系统乙", "P001", "oblique", b"female-oblique", "FEMALE", "PASS", "SAFE", "RELIABLE_FOR_POSE", "evidence-b2"),
        ("b3", "sysB", "系统乙", "P001", "low", b"female-low", "FEMALE", "PASS", "SAFE", "RELIABLE_FOR_POSE", "evidence-b3"),
    ]
    for sid, sysja, syszh, pose, angle, content, label, qa, safety, reliability, evidence in specs:
        filename = sid + ".jpg"
        (img / filename).write_bytes(content)
        rows.append({"id": sid, "system_ja": sysja, "system_zh": syszh, "pose_number": pose,
                     "pose_name_ja": "立つ", "pose_name_zh": "站立", "category_ja": "基本", "category_zh": "基础",
                     "angle_ja": angle, "angle_zh": angle, "source_relative_path": "img/" + filename,
                     "sha256": sha(content), "width": 100, "height": 200, "source_commit": "sourcecommit072a"})
        reviews.append({"source_id": sid, "sha256": sha(content), "subject_label": label,
                        "subject_label_evidence": "visual-label-review", "visual_qa_status": qa,
                        "safety_status": safety, "anatomy_reliability": reliability,
                        "review_notes": "reviewed", "review_evidence": evidence,
                        "pose_name_ru": "Стоя", "category_ru": "Базовые", "pose_family": "standing"})
    # Deliberately omit expected angles in the source index, and force one stale review hash.
    reviews[-1]["sha256"] = "0" * 64
    index = tmp_path / "dataset_index.json"
    review = tmp_path / "reviews.json"
    index.write_text(json.dumps({"images": rows}, ensure_ascii=False), encoding="utf-8")
    review.write_text(json.dumps({"schema_version": 1, "reviews": reviews}, ensure_ascii=False), encoding="utf-8")
    return root, index, review, rows


def do_import(tmp_path, root, index, review):
    class Args:
        source_index, source_root, review_file = index, root, review
        library_root = tmp_path / "library"
    return import_atlas(Args()), Args.library_root


def test_import_preserves_grouping_and_independent_admission(tmp_path):
    root, index, review, _ = fixture(tmp_path)
    result, library = do_import(tmp_path, root, index, review)
    assert result["source_count"] == 6
    assert result["group_count"] == 2  # P001 is scoped to system/provider.
    assert result["eligible_view_count"] == 4
    assert validate_library(library)["status"] == "CATALOG_COMPLETE"
    groups = json.loads((library / "POSE_GROUPS.json").read_text(encoding="utf-8"))["groups"]
    assert {g["status"] for g in groups} == {"PARTIAL"}
    a = next(g for g in groups if g["system"] == "sysA")
    assert a["expected_angles"] == ["front", "oblique", "side", "high", "low"]
    assert a["source_angles"] == ["front", "side", "high"]
    assert a["available_angles"] == ["front", "high"]
    assert {v["angle"]: v["generator_eligible"] for v in a["views"]} == {"front": True, "side": False, "high": True}
    b = next(g for g in groups if g["system"] == "sysB")
    assert next(v for v in b["views"] if v["source_id"] == "b3")["subject_label"] == "UNKNOWN"
    with (library / "BODY_REFERENCE_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as f:
        manifest = list(csv.DictReader(f))
    assert len(manifest) == 6
    assert manifest[0]["ref_id"] == "BR_10000" and manifest[-1]["ref_id"] == "BR_10005"
    assert {"subject_label_sha256", "subject_label_status"}.issubset(manifest[0])
    assert manifest[0]["subject_label_sha256"] == manifest[0]["source_sha256"]
    assert manifest[0]["source_medium"] == "ILLUSTRATION_LINE_ART"
    assert all("BODY_BUILD_TARGET" in r["not_for_roles"] and "IDENTITY" in r["not_for_roles"] and "STYLE" in r["not_for_roles"] for r in manifest)
    assert all(r["allowed_roles"] == "STAGING_ONLY;POSE_SOFT;AUX_POSE;AUX_CAMERA" for r in manifest)
    provenance = json.loads((library / "IMPORT_PROVENANCE.json").read_text(encoding="utf-8"))
    assert provenance["source_index_commit"] == "sourcecommit072a"
    assert groups[0]["pose_name_ru"] == "Стоя" and groups[0]["category_ru"] == "Базовые"
    assert groups[0]["multiview_relation"] == "SOURCE_GROUP_APPROXIMATE"


def test_context_subject_filter_precedes_exposure_and_rehashes(tmp_path):
    root, index, review, _ = fixture(tmp_path)
    _, library = do_import(tmp_path, root, index, review)
    male = reference_context(library, "MALE_ANATOMY")
    female = reference_context(library, "FEMALE_ANATOMY")
    assert male["view_count"] == 1
    assert female["view_count"] == 1
    assert all(v["source_id"].startswith("a") for g in male["groups"] for v in g["views"])
    assert all(v["source_id"].startswith("b") for g in female["groups"] for v in g["views"])
    assert all(len(g["views"]) == 1 for g in male["groups"] + female["groups"])
    assert reference_context(library, "MALE_ANATOMY", query="стоя")["view_count"] == 1
    male_path = Path(male["groups"][0]["views"][0]["path"])
    selected = male["groups"][0]["views"][0]
    assert selected["ref_id"].startswith("BR_")
    assert selected["subject_label"] == "MALE"
    assert selected["subject_label_sha256"] == selected["sha256"]
    male_path.write_bytes(b"changed after validation")
    after_drift = reference_context(library, "MALE_ANATOMY")
    assert after_drift["view_count"] == 1
    assert Path(after_drift["groups"][0]["views"][0]["path"]) != male_path
    with pytest.raises(PoseReferenceError, match="stale"):
        reference_context(library, "MALE_ANATOMY", group_id_filter=selected["group_id"], angle=selected["angle"])
    with pytest.raises(PoseReferenceError, match="stale"):
        reference_context(library, "MALE_ANATOMY", source_id=selected["source_id"])
    with pytest.raises(PoseReferenceError):
        reference_context(library, "UNKNOWN")


def test_source_path_traversal_rejected_before_copy(tmp_path):
    root, index, review, rows = fixture(tmp_path)
    rows[0]["source_relative_path"] = "../escape.jpg"
    index.write_text(json.dumps({"images": rows}), encoding="utf-8")
    class Args:
        source_index, source_root, review_file = index, root, review
        library_root = tmp_path / "library"
    with pytest.raises(PoseReferenceError, match="escapes root"):
        import_atlas(Args())


def test_missing_source_is_catalogued_unavailable_not_admitted(tmp_path):
    root, index, review, rows = fixture(tmp_path)
    missing = rows[0]
    (root / missing["source_relative_path"]).unlink()
    _, library = do_import(tmp_path, root, index, review)
    data = json.loads((library / "POSE_GROUPS.json").read_text(encoding="utf-8"))
    a = next(g for g in data["groups"] if g["system"] == "sysA")
    assert a["status"] == "PARTIAL"
    assert next(v for v in a["views"] if v["source_id"] == "a1")["generator_eligible"] is False


def test_group_availability_requires_eligible_angles_not_source_inventory(tmp_path):
    root, index, review, _ = fixture(tmp_path)
    data = json.loads(review.read_text(encoding="utf-8"))
    for item in data["reviews"]:
        if item["source_id"].startswith("a"):
            item["visual_qa_status"] = "FAIL"
    review.write_text(json.dumps(data), encoding="utf-8")
    _, library = do_import(tmp_path, root, index, review)
    group = next(g for g in json.loads((library / "POSE_GROUPS.json").read_text(encoding="utf-8"))["groups"] if g["system"] == "sysA")
    assert group["source_angles"] == ["front", "side", "high"]
    assert group["available_angles"] == []
    assert group["status"] == "UNAVAILABLE"


def test_upper_body_source_system_is_not_promoted_to_full_body(tmp_path):
    root, index, review, rows = fixture(tmp_path)
    for row in rows:
        row["system_ja"] = "上半身1人"
    index.write_text(json.dumps({"images": rows}, ensure_ascii=False), encoding="utf-8")
    _, library = do_import(tmp_path, root, index, review)
    with (library / "BODY_REFERENCE_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as f:
        manifest = list(csv.DictReader(f))
    assert {row["framing"] for row in manifest} == {"UPPER_BODY"}


def test_japanese_high_low_aliases_and_strict_bilingual_consistency(tmp_path):
    root, index, review, rows = fixture(tmp_path)
    rows[0]["angle_ja"], rows[0]["angle_zh"] = "上から", "从上"
    rows[1]["angle_ja"], rows[1]["angle_zh"] = "下から", "从下"
    rows[2]["angle_ja"], rows[2]["angle_zh"] = "非標準表記", "从上"
    index.write_text(json.dumps({"images": rows}, ensure_ascii=False), encoding="utf-8")
    _, library = do_import(tmp_path, root, index, review)
    group = next(g for g in json.loads((library / "POSE_GROUPS.json").read_text(encoding="utf-8"))["groups"] if g["system"] == "sysA")
    assert [v["angle"] for v in group["views"][:2]] == ["high", "low"]
    assert group["views"][2]["angle"] == "high"  # Chinese fallback when Japanese is unmapped.

    root2, index2, review2, bad_rows = fixture(tmp_path / "conflict")
    bad_rows[0]["angle_ja"], bad_rows[0]["angle_zh"] = "上から", "正面"
    index2.write_text(json.dumps({"images": bad_rows}, ensure_ascii=False), encoding="utf-8")
    class Args:
        source_index, source_root, review_file = index2, root2, review2
        library_root = tmp_path / "conflict-library"
    with pytest.raises(PoseReferenceError, match="disagree"):
        import_atlas(Args())


def test_unknown_angle_fails_closed(tmp_path):
    root, index, review, rows = fixture(tmp_path)
    rows[0]["angle_ja"], rows[0]["angle_zh"] = "斜后方", "unknown"
    index.write_text(json.dumps({"images": rows}, ensure_ascii=False), encoding="utf-8")
    class Args:
        source_index, source_root, review_file = index, root, review
        library_root = tmp_path / "library"
    with pytest.raises(PoseReferenceError, match="recognized"):
        import_atlas(Args())


def test_imported_view_is_consumable_by_active_pose_atlas_parser(tmp_path):
    from tools.body_reference_type_router import load_registry
    from tools.style_pack_manager import parse_aux_body_references

    root, index, review, _ = fixture(tmp_path)
    registry = copy.deepcopy(load_registry())
    registry["active_auxiliary_type"] = "POSE_ATLAS"
    registry["types"]["POSE_ATLAS"]["enabled"] = True
    config = tmp_path / "config"
    config.mkdir()
    (config / "body_reference_types.json").write_text(json.dumps(registry), encoding="utf-8")
    library = tmp_path / "BODY_REFERENCE_LIBRARY" / "POSE_ATLAS"
    args = type("Args", (), {"source_index": index, "source_root": root, "review_file": review,
                              "library_root": library})
    import_atlas(args)
    rows = read_manifest(library / "BODY_REFERENCE_MANIFEST.csv")
    female = next(row for row in rows if row["subject_label"] == "FEMALE" and row["generator_safe"] == "YES")
    chosen = parse_aux_body_references(tmp_path, [female["ref_id"] + "=STAGING_ONLY"], "SELECTED", "", False, False,
                                       {"target_anatomy": "FEMALE_ANATOMY", "anatomy_evidence_source": "CURRENT_PROMPT_EXPLICIT:female"})
    assert chosen[0]["source_id"] == female["source_id"]
    assert chosen[0]["angle"] == female["angle"]
    assert chosen[0]["sha256"] == female["sha256"] == female["source_sha256"]


def read_manifest(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))



def test_every_shipped_group_has_russian_name_family_and_size():
    from tools.pose_reference_manager import localize_group
    root = Path(__file__).resolve().parents[1]
    groups = json.loads((root / "reference_packs/POSE_ATLAS/metadata/POSE_GROUPS.json").read_text(encoding="utf-8"))["groups"]
    for group in groups:
        filled = localize_group({k: group[k] for k in ("group_id", "category_ja", "system")})
        assert filled.get("pose_name_ru") and filled.get("category_ru"), group["group_id"]
        assert filled.get("pose_family") in {"STANDING", "SEATED", "LYING", "KNEELING", "CROUCHING", "OTHER"}
        assert filled.get("group_size") in {"SOLO_FULL_BODY", "SOLO_UPPER_BODY", "DUO"}


def test_existing_russian_names_are_not_overwritten():
    from tools.pose_reference_manager import localize_group
    group = localize_group({"group_id": "ANHAO_1_unknown_P001_c779b04e", "pose_name_ru": "Своё имя", "category_ja": "立ち", "system": "2人絡み"})
    assert group["pose_name_ru"] == "Своё имя"
    assert group["group_size"] == "DUO"
