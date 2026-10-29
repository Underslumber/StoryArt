#!/usr/bin/env python3
"""Import and query group-aware, independently reviewed pose references."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

EXPECTED_ANGLES = ["front", "oblique", "side", "high", "low"]
ELIGIBLE_LABELS = {"MALE", "FEMALE"}
REVIEW_QA = {"PASS", "FAIL", "NOT_CHECKED"}
REVIEW_SAFETY = {"SAFE", "REJECTED_METADATA", "REJECTED_CONTENT", "REVIEW_REQUIRED"}
ANATOMY = {"RELIABLE_FOR_POSE", "UNCERTAIN", "INVALID"}
MANIFEST_FIELDS = [
    "ref_id", "filename", "source_filename", "sha256", "source_sha256", "source_medium",
    "primary_family", "pose", "view", "camera_angle", "framing", "body_build",
    "proportion_tags", "anatomy_behavior", "foreshortening", "clothing_interaction",
    "object_interaction", "contact_points", "anatomy_reliability", "visible_presentation",
    "anatomy_compatibility", "anatomy_evidence_source", "subject_label",
    "subject_label_evidence", "subject_label_sha256", "subject_label_status",
    "allowed_roles", "not_for_roles", "safety_status",
    "generator_safe", "generator_path", "style_influence", "use_for", "do_not_use_for",
    "notes", "group_id", "source_id", "angle", "pose_name_ja", "pose_name_zh",
    "pose_name_ru", "category_ru", "pose_family", "review_notes", "review_evidence",
    "selection_available", "reference_path", "selection_policy",
]
ALL_SOURCE_IMAGES = "ALL_SOURCE_IMAGES"


class PoseReferenceError(ValueError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        raise PoseReferenceError(f"Cannot read JSON {path}: {e}") from e


def source_rows(data: Any) -> list[dict[str, Any]]:
    rows = data.get("images", data.get("records", data.get("items"))) if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows or not all(isinstance(x, dict) for x in rows):
        raise PoseReferenceError("Source index must be a non-empty array or contain images/records/items array.")
    required = {"id", "system_ja", "system_zh", "pose_number", "pose_name_ja", "pose_name_zh",
                "category_ja", "category_zh", "angle_ja", "angle_zh", "source_relative_path", "sha256"}
    seen: set[str] = set()
    for row in rows:
        missing = required - row.keys()
        if missing:
            raise PoseReferenceError(f"Source index row lacks fields: {', '.join(sorted(missing))}")
        sid = str(row["id"])
        if not sid or sid in seen:
            raise PoseReferenceError(f"Empty or duplicate source id: {sid!r}")
        seen.add(sid)
    return rows


def angle_key(value: str) -> str:
    s = value.strip().lower()
    aliases = {"front": "front", "正面": "front", "正面视角": "front", "正面視角": "front",
               "oblique": "oblique", "斜め": "oblique", "斜角": "oblique", "斜视": "oblique",
               "side": "side", "側面": "side", "侧面": "side", "横": "side",
               "high": "high", "高角度": "high", "俯瞰": "high", "俯视": "high", "上から": "high",
               "上からの視点": "high", "上方": "high", "上面": "high", "从上": "high", "由上": "high",
               "low": "low", "低角度": "low", "仰瞰": "low", "仰视": "low", "下から": "low",
               "下からの視点": "low", "下方": "low", "下面": "low", "从下": "low", "由下": "low"}
    if s not in aliases:
        raise PoseReferenceError(f"Unrecognized angle: {value}")
    return aliases[s]


def classify_angle(angle_ja: str, angle_zh: str, override: str = "") -> str:
    def optional(value: str) -> str:
        if not value:
            return ""
        try:
            return angle_key(value)
        except PoseReferenceError:
            return ""
    if override:
        result = angle_key(override)
        ja, zh = optional(angle_ja), optional(angle_zh)
        if ja and ja != result or zh and zh != result:
            raise PoseReferenceError(f"Angle override conflicts with source labels: {angle_ja!r}, {angle_zh!r}, {override!r}")
        return result
    ja, zh = optional(angle_ja), optional(angle_zh)
    if ja and zh and ja != zh:
        raise PoseReferenceError(f"Japanese and Chinese source angle labels disagree: {angle_ja!r} vs {angle_zh!r}")
    if ja or zh:
        return ja or zh
    raise PoseReferenceError(f"No recognized Japanese or Chinese source angle: {angle_ja!r}, {angle_zh!r}")


def safe_relative(root: Path, value: str) -> Path:
    rel = Path(value.replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise PoseReferenceError(f"Source path escapes root: {value}")
    resolved_root = root.resolve()
    candidate = (resolved_root / rel).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise PoseReferenceError(f"Source path escapes root: {value}")
    return candidate


def token(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def group_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row["system_ja"]), str(row["system_zh"]), str(row["pose_number"])


def read_manifest_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def group_id(row: dict[str, Any]) -> str:
    raw = "\x1f".join(group_key(row))
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return "ANHAO_" + "_".join(token(v) for v in group_key(row)) + "_" + suffix


def source_commit(rows: list[dict[str, Any]]) -> str:
    commits = {str(row.get("source_commit", "")).strip() for row in rows if str(row.get("source_commit", "")).strip()}
    return next(iter(commits)) if len(commits) == 1 else "UNAVAILABLE"


def review_map(data: Any, ids: set[str]) -> dict[str, dict[str, Any]]:
    rows = data.get("reviews") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise PoseReferenceError("Review file must contain a reviews array.")
    out: dict[str, dict[str, Any]] = {}
    for entry in rows:
        if not isinstance(entry, dict):
            raise PoseReferenceError("Every review must be an object.")
        sid = str(entry.get("source_id", ""))
        if sid not in ids or sid in out:
            raise PoseReferenceError(f"Unknown or duplicate review source_id: {sid}")
        qa = str(entry.get("visual_qa_status", "NOT_CHECKED")).upper()
        safety = str(entry.get("safety_status", "REVIEW_REQUIRED")).upper()
        rel = str(entry.get("anatomy_reliability", "UNCERTAIN")).upper()
        label = str(entry.get("subject_label", "UNKNOWN")).upper()
        if qa not in REVIEW_QA or safety not in REVIEW_SAFETY or rel not in ANATOMY:
            raise PoseReferenceError(f"Invalid review state for {sid}.")
        if label not in ELIGIBLE_LABELS | {"UNKNOWN"}:
            raise PoseReferenceError(f"Invalid subject label for {sid}: {label}")
        out[sid] = {**entry, "visual_qa_status": qa, "safety_status": safety,
                    "anatomy_reliability": rel, "subject_label": label}
    return out


def admitted(view: dict[str, Any], path: Path) -> bool:
    return bool(path.is_file() and view.get("sha256") and digest(path) == view["sha256"]
                and view.get("subject_label") in ELIGIBLE_LABELS
                and view.get("subject_label_sha256") == view.get("sha256")
                and view.get("subject_label_status") == "CONFIRMED"
                and view.get("visual_qa_status") == "PASS"
                and view.get("safety_status") == "SAFE"
                and view.get("anatomy_reliability") == "RELIABLE_FOR_POSE"
                and bool(view.get("subject_label_evidence"))
                and bool(view.get("review_evidence") or view.get("review_notes")))


def import_atlas(args: argparse.Namespace) -> dict[str, Any]:
    index_path, root, reviews_path = args.source_index.resolve(), args.source_root.resolve(), args.review_file.resolve()
    if not index_path.is_file() or not root.is_dir() or not reviews_path.is_file():
        raise PoseReferenceError("Source index, source root, and review file must exist.")
    rows = source_rows(json_read(index_path))
    reviews = review_map(json_read(reviews_path), {str(r["id"]) for r in rows})
    library = args.library_root.resolve()
    if library.exists() and any(library.iterdir()):
        raise PoseReferenceError(f"Refusing to overwrite non-empty library: {library}")
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    manifest: list[dict[str, str]] = []
    for offset, row in enumerate(rows):
        sid, gid = str(row["id"]), group_id(row)
        review = reviews.get(sid, {})
        angle = classify_angle(str(row.get("angle_ja", "")), str(row.get("angle_zh", "")), str(review.get("angle_override", "")))
        src = safe_relative(root, str(row["source_relative_path"]))
        source_hash = str(row["sha256"]).lower()
        present = src.is_file() and bool(source_hash) and digest(src) == source_hash
        relative = f"sources/anhao/img/{src.name}" if present else ""
        dest = library / relative if present else None
        if present and dest:
            if dest.exists() and digest(dest) != source_hash:
                raise PoseReferenceError(f"Different hash already occupies {dest}")
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                if digest(dest) != source_hash:
                    dest.unlink(missing_ok=True)
                    raise PoseReferenceError(f"Copied source hash mismatch: {sid}")
        label = str(review.get("subject_label", "UNKNOWN"))
        review_hash = str(review.get("sha256", "" )).lower()
        review_current = bool(present and review_hash == source_hash)
        evidence = str(review.get("subject_label_evidence", ""))
        effective_label = label if review_current and label in ELIGIBLE_LABELS and evidence else "UNKNOWN"
        view = {"source_id": sid, "angle": angle, "sha256": source_hash, "relative_path": relative,
                "source_present": present, "subject_label": effective_label,
                "subject_label_sha256": source_hash if effective_label in ELIGIBLE_LABELS else "",
                "subject_label_evidence": evidence if review_current else "",
                "subject_label_status": "CONFIRMED" if effective_label in ELIGIBLE_LABELS else "UNKNOWN",
                "visual_qa_status": review.get("visual_qa_status", "NOT_CHECKED") if review_current else "NOT_CHECKED",
                "safety_status": review.get("safety_status", "REVIEW_REQUIRED") if review_current else "REVIEW_REQUIRED",
                "anatomy_reliability": review.get("anatomy_reliability", "UNCERTAIN") if review_current else "UNCERTAIN",
                "generator_eligible": False,
                "review_notes": str(review.get("review_notes", "")) if review_current else "Review missing or hash does not match current bytes.",
                "review_evidence": str(review.get("review_evidence", "")) if review_current else ""}
        if dest and admitted(view, dest):
            view["generator_eligible"] = True
        key = group_key(row)
        review = reviews.get(sid, {})
        text_meta = " ".join(str(row.get(k, "")) for k in ("system_ja", "system_zh", "pose_name_ja", "pose_name_zh", "category_ja", "category_zh")).casefold()
        framing = "UPPER_BODY" if "上半身" in text_meta or "upper body" in text_meta else "FULL_BODY"
        group = grouped.setdefault(key, {"group_id": gid, "provider": "anhao", "system": key[0],
            "system_zh": key[1], "pose_number": key[2], "pose_name_ja": str(row["pose_name_ja"]),
            "pose_name_zh": str(row["pose_name_zh"]), "category_ja": str(row["category_ja"]),
            "category_zh": str(row["category_zh"]), "pose_name_ru": str(review.get("pose_name_ru", "")),
            "category_ru": str(review.get("category_ru", "")), "pose_family": str(review.get("pose_family", "")),
            "multiview_relation": "SOURCE_GROUP_APPROXIMATE", "source_angles": [], "expected_angles": list(EXPECTED_ANGLES),
            "views": []})
        if angle != "unknown" and angle not in group["source_angles"]:
            group["source_angles"].append(angle)
        group["views"].append(view)
        ref = f"BR_{10000 + offset:05d}"
        manifest.append({"ref_id": ref, "filename": src.name if present else "", "source_filename": src.name,
            "sha256": source_hash, "source_sha256": source_hash, "source_medium": "ILLUSTRATION_LINE_ART", "primary_family": "POSE_ATLAS",
            "pose": token(row["pose_number"]), "view": angle.upper(), "camera_angle": angle.upper(),
            "framing": framing, "body_build": "SOURCE_DEPICTION_ONLY", "proportion_tags": "",
            "anatomy_behavior": "POSE_DEFORMATION;WEIGHT_DISTRIBUTION", "foreshortening": "REVIEW_REQUIRED",
            "clothing_interaction": "", "object_interaction": "", "contact_points": "",
            "anatomy_reliability": view["anatomy_reliability"], "visible_presentation": "UNKNOWN",
            "anatomy_compatibility": "MALE_ANATOMY" if view["subject_label"] == "MALE" else "FEMALE_ANATOMY" if view["subject_label"] == "FEMALE" else "UNKNOWN",
            "anatomy_evidence_source": view["subject_label_evidence"] or "UNKNOWN", "subject_label": view["subject_label"],
            "subject_label_evidence": view["subject_label_evidence"],
            "subject_label_sha256": view["subject_label_sha256"], "subject_label_status": view["subject_label_status"],
            "allowed_roles": "STAGING_ONLY;POSE_SOFT;AUX_POSE;AUX_CAMERA",
            "not_for_roles": "BODY_BUILD_TARGET;IDENTITY;STYLE", "safety_status": view["safety_status"],
            "generator_safe": "YES" if view["generator_eligible"] else "NO",
            "generator_path": relative if view["generator_eligible"] else "", "style_influence": "FORBIDDEN",
            "use_for": "Selected pose staging and camera only.",
            "do_not_use_for": "Body build, anatomy transfer, identity, face, clothing design, style.",
            "notes": view["review_notes"], "group_id": gid, "source_id": sid, "angle": angle,
            "pose_name_ja": str(row["pose_name_ja"]), "pose_name_zh": str(row["pose_name_zh"]),
            "pose_name_ru": str(review.get("pose_name_ru", "")), "category_ru": str(review.get("category_ru", "")),
            "pose_family": str(review.get("pose_family", "")), "review_notes": view["review_notes"],
            "review_evidence": view["review_evidence"]})
    groups = []
    for group in grouped.values():
        group["available_angles"] = sorted({v["angle"] for v in group["views"] if v["generator_eligible"] and v["angle"] != "unknown"})
        group["status"] = "UNAVAILABLE" if not group["available_angles"] else "FULL" if set(group["available_angles"]) == set(EXPECTED_ANGLES) else "PARTIAL"
        group["source_view_count"] = len(group["views"])
        groups.append(group)
    library.mkdir(parents=True, exist_ok=True)
    commit = source_commit(rows)
    (library / "POSE_GROUPS.json").write_text(json.dumps({"schema_version": 1, "source_count": len(rows),
        "group_count": len(groups), "source_index_sha256": digest(index_path), "review_file_sha256": digest(reviews_path),
        "source_index_commit": commit, "source_index": str(index_path), "source_root": str(root),
        "groups": groups}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (library / "BODY_REFERENCE_MANIFEST.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        writer.writeheader(); writer.writerows(manifest)
    provenance = {"schema_version": 1, "provider": "anhao", "source_index_sha256": digest(index_path),
                  "review_file_sha256": digest(reviews_path), "source_index": str(index_path),
                  "source_index_commit": commit, "source_root": str(root), "source_count": len(rows), "group_count": len(groups),
                  "imported_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")}
    (library / "IMPORT_PROVENANCE.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "CATALOG_COMPLETE", "source_count": len(rows), "group_count": len(groups),
            "eligible_view_count": sum(v["generator_eligible"] for g in groups for v in g["views"]), "library_root": str(library)}


def validate_library(library: Path) -> dict[str, Any]:
    groups_file, manifest_file = library / "POSE_GROUPS.json", library / "BODY_REFERENCE_MANIFEST.csv"
    if not groups_file.is_file() or not manifest_file.is_file():
        raise PoseReferenceError("POSE_GROUPS.json or BODY_REFERENCE_MANIFEST.csv is missing.")
    data = json_read(groups_file)
    if data.get("schema_version") != 1 or not isinstance(data.get("groups"), list):
        raise PoseReferenceError("Unsupported POSE_GROUPS schema.")
    rows = read_manifest_rows(manifest_file)
    if len(rows) != int(data.get("source_count", -1)):
        raise PoseReferenceError("Manifest row count differs from catalog source_count.")
    policy = str(data.get("selection_policy", "")).upper()
    if policy not in {"", ALL_SOURCE_IMAGES}:
        raise PoseReferenceError(f"Unknown collection selection_policy: {policy}")
    all_source_images = policy == ALL_SOURCE_IMAGES
    seen_ids: set[str] = set(); views_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for group in data["groups"]:
        if all_source_images and str(group.get("selection_policy", ALL_SOURCE_IMAGES)).upper() != ALL_SOURCE_IMAGES:
            raise PoseReferenceError(f"Group selection_policy conflicts with the collection policy: {group.get('group_id', '')}")
        if not all(k in group for k in ("group_id", "provider", "system", "pose_number", "expected_angles", "views", "available_angles", "status")):
            raise PoseReferenceError("Group is missing required fields.")
        for view in group["views"]:
            if all_source_images and str(view.get("selection_policy", ALL_SOURCE_IMAGES)).upper() != ALL_SOURCE_IMAGES:
                raise PoseReferenceError(f"View selection_policy conflicts with the collection policy: {view.get('source_id', '')}")
            sid = str(view.get("source_id", ""))
            if not sid or sid in seen_ids: raise PoseReferenceError(f"Duplicate/empty catalog source id: {sid}")
            seen_ids.add(sid); views_by_id[sid] = (group, view)
            path = safe_relative(library, str(view.get("relative_path", ""))) if view.get("relative_path") else None
            actual = bool(path and path.is_file() and digest(path) == view.get("sha256"))
            if all_source_images:
                available = view.get("selection_available") is True
                reference_path = str(view.get("reference_path", view.get("relative_path", "")))
                if available and (not actual or not reference_path or reference_path != view.get("relative_path")):
                    raise PoseReferenceError(f"Available selection source path/hash is invalid: {sid}")
                if not available and reference_path:
                    raise PoseReferenceError(f"Unavailable selection source has a reference_path: {sid}")
            if view.get("generator_eligible") and not (actual and path and admitted(view, path)):
                raise PoseReferenceError(f"Eligible view failed current-byte/review admission: {sid}")
            if not actual and view.get("generator_eligible"):
                raise PoseReferenceError(f"Stale eligible source: {sid}")
        angles = sorted({v["angle"] for v in group["views"] if (v.get("selection_available") if all_source_images else v.get("generator_eligible")) and v.get("angle") != "unknown"})
        expected_status = "UNAVAILABLE" if not angles else "FULL" if set(angles) == set(EXPECTED_ANGLES) else "PARTIAL"
        if group.get("available_angles") != angles or group.get("status") != expected_status:
            raise PoseReferenceError(f"Derived angle state is inconsistent for {group['group_id']}.")
    if len(seen_ids) != len(rows): raise PoseReferenceError("Catalog views and manifest rows differ.")
    rows_by_id = {r.get("source_id"): r for r in rows}
    if len(rows_by_id) != len(rows) or set(rows_by_id) != set(seen_ids): raise PoseReferenceError("Manifest source ids do not match catalog.")
    for sid, (group, view) in views_by_id.items():
        row = rows_by_id[sid]
        expected_safe = bool(view.get("generator_eligible"))
        if all_source_images:
            expected_available = bool(view.get("selection_available"))
            expected_path = str(view.get("reference_path", view.get("relative_path", "")))
            if (row.get("selection_available") != ("YES" if expected_available else "NO")
                    or row.get("reference_path", "") != expected_path
                    or row.get("selection_policy") != ALL_SOURCE_IMAGES):
                raise PoseReferenceError(f"Manifest selection availability/path mismatch for {sid}.")
        if (row.get("group_id") != group["group_id"]
                or row.get("sha256") != view.get("sha256")
                or row.get("source_sha256") != view.get("sha256")
                or row.get("subject_label") != view.get("subject_label")
                or row.get("subject_label_sha256") != view.get("subject_label_sha256")
                or row.get("subject_label_status") != view.get("subject_label_status")
                or row.get("generator_safe") != ("YES" if expected_safe else "NO")
                or row.get("generator_path") != (view.get("relative_path", "") if expected_safe else "")
                or row.get("allowed_roles") != "STAGING_ONLY;POSE_SOFT;AUX_POSE;AUX_CAMERA"
                or row.get("not_for_roles") != "BODY_BUILD_TARGET;IDENTITY;STYLE"):
            raise PoseReferenceError(f"Manifest admission/group mismatch for {sid}.")
    eligible = sum(bool(v.get("generator_eligible")) for _, v in views_by_id.values())
    available = sum(bool(v.get("selection_available")) for _, v in views_by_id.values()) if all_source_images else eligible
    return {"status": "CATALOG_COMPLETE", "catalog_sources": len(rows), "groups": len(data["groups"]),
            "eligible_views": eligible, "eligibility": "AVAILABLE" if eligible else "NONE",
            "selection_policy": policy or "LEGACY_GENERATOR_ELIGIBILITY",
            "selection_available_views": available, "library_root": str(library)}


def reference_context(library_root: Path | str, target_anatomy: str = "ANY", query: str = "", group_id_filter: str = "", angle: str = "", source_id: str = "") -> dict[str, Any]:
    target = target_anatomy.upper()
    library = Path(library_root).resolve()
    data = json_read(library / "POSE_GROUPS.json")
    all_source_images = str(data.get("selection_policy", "")).upper() == ALL_SOURCE_IMAGES
    valid_targets = {"MALE_ANATOMY", "FEMALE_ANATOMY", "ANY"} if all_source_images else {"MALE_ANATOMY", "FEMALE_ANATOMY"}
    if target not in valid_targets:
        raise PoseReferenceError("target_anatomy must be MALE_ANATOMY or FEMALE_ANATOMY.")
    manifest = {r["source_id"]: r for r in read_manifest_rows(library / "BODY_REFERENCE_MANIFEST.csv")}
    wanted_label = "MALE" if target == "MALE_ANATOMY" else "FEMALE" if target == "FEMALE_ANATOMY" else ""
    query_l = query.casefold()
    requested_angle = angle_key(angle) if angle else ""
    if source_id:
        matches = [(group, view) for group in data.get("groups", []) for view in group.get("views", []) if view.get("source_id") == source_id]
        if len(matches) != 1:
            raise PoseReferenceError(f"Selected source_id is missing or ambiguous: {source_id}")
        group, view = matches[0]
        selectable = bool(view.get("selection_available")) if all_source_images else bool(view.get("generator_eligible"))
        if ((wanted_label and (view.get("subject_label_status") != "CONFIRMED" or view.get("subject_label") != wanted_label))
                or not selectable or (requested_angle and view.get("angle") != requested_angle)
                or (group_id_filter and group.get("group_id") != group_id_filter)):
            raise PoseReferenceError(f"Selected source_id is incompatible or unavailable: {source_id}")
        path = safe_relative(library, str(view.get("relative_path", "")))
        row = manifest.get(source_id, {})
        selection_path = row.get("reference_path", "") if all_source_images else row.get("generator_path", "")
        if ((not all_source_images and not admitted(view, path)) or not path.is_file()
                or digest(path) != view.get("sha256") or selection_path != view.get("relative_path")
                or row.get("sha256") != view.get("sha256")):
            raise PoseReferenceError(f"Selected source_id is stale or no longer admitted: {source_id}")
        selected_view = {"ref_id": row["ref_id"], "source_id": source_id, "group_id": group["group_id"],
                         "angle": view["angle"], "sha256": view["sha256"], "path": str(path),
                         "subject_label": view["subject_label"], "subject_label_sha256": view["subject_label_sha256"],
                         "subject_label_status": view["subject_label_status"],
                         "subject_label_evidence": view["subject_label_evidence"],
                         "review_evidence": view["review_evidence"],
                         "notes": row.get("notes", view.get("review_notes", "")),
                         "allowed_roles": ["STAGING_ONLY", "POSE_SOFT", "AUX_POSE", "AUX_CAMERA"],
                         "selection_available": True if all_source_images else None,
                         "selection_policy": ALL_SOURCE_IMAGES if all_source_images else ""}
        group_result = {"group_id": group["group_id"], "pose_name_ja": group["pose_name_ja"],
                        "pose_name_zh": group["pose_name_zh"], "pose_name_ru": group.get("pose_name_ru", ""),
                        "category_ja": group["category_ja"], "category_zh": group["category_zh"],
                        "category_ru": group.get("category_ru", ""), "pose_family": group.get("pose_family", ""),
                        "multiview_relation": group.get("multiview_relation", "SOURCE_GROUP_APPROXIMATE"),
                        "available_angles": group["available_angles"], "views": [selected_view]}
        return {"target_anatomy": target, "groups": [group_result], "view_count": 1}
    result = []
    for group in data.get("groups", []):
        if group_id_filter and group["group_id"] != group_id_filter: continue
        searchable = " ".join(str(group.get(k, "")) for k in ("group_id", "pose_name_ja", "pose_name_zh", "pose_name_ru", "category_ja", "category_zh", "category_ru", "pose_family", "system", "system_zh")).casefold()
        if query_l and query_l not in searchable: continue
        views = []
        for view in group["views"]:
            # Filter subject metadata before resolving or exposing a source path.
            if wanted_label and (view.get("subject_label_status") != "CONFIRMED" or view.get("subject_label") != wanted_label): continue
            if not (view.get("selection_available") if all_source_images else view.get("generator_eligible")): continue
            if requested_angle and view.get("angle") != requested_angle: continue
            path = safe_relative(library, str(view.get("relative_path", "")))
            if not path.is_file() or digest(path) != view.get("sha256"): continue
            if not all_source_images and not admitted(view, path): continue
            row = manifest.get(view["source_id"], {})
            selection_path = row.get("reference_path", "") if all_source_images else row.get("generator_path", "")
            if selection_path != view["relative_path"] or row.get("sha256") != view.get("sha256"): continue
            row = manifest.get(view["source_id"], {})
            views.append({"ref_id": row.get("ref_id", ""), "source_id": view["source_id"],
                          "group_id": group["group_id"], "angle": view["angle"], "sha256": view["sha256"],
                          "path": str(path), "subject_label": view["subject_label"],
                          "subject_label_sha256": view["subject_label_sha256"],
                          "subject_label_status": view["subject_label_status"],
                          "subject_label_evidence": view["subject_label_evidence"],
                          "review_evidence": view["review_evidence"],
                          "notes": row.get("notes", view.get("review_notes", "")),
                          "allowed_roles": ["STAGING_ONLY", "POSE_SOFT", "AUX_POSE", "AUX_CAMERA"],
                          "selection_available": True if all_source_images else None,
                          "selection_policy": ALL_SOURCE_IMAGES if all_source_images else ""})
        if views:
            if not requested_angle and not all_source_images:
                # A group represents approximate sibling illustrations; the default binding is one view.
                priority = {name: i for i, name in enumerate(EXPECTED_ANGLES)}
                views = [min(views, key=lambda item: (priority.get(item["angle"], len(priority)), item["source_id"]))]
            result.append({"group_id": group["group_id"], "pose_name_ja": group["pose_name_ja"],
                           "pose_name_zh": group["pose_name_zh"], "pose_name_ru": group.get("pose_name_ru", ""),
                           "category_ja": group["category_ja"], "category_zh": group["category_zh"],
                           "category_ru": group.get("category_ru", ""), "pose_family": group.get("pose_family", ""),
                           "multiview_relation": group.get("multiview_relation", "SOURCE_GROUP_APPROXIMATE"),
                           "available_angles": group["available_angles"], "views": views})
        elif requested_angle and group_id_filter and group.get("group_id") == group_id_filter and requested_angle in group.get("source_angles", []):
            raise PoseReferenceError(f"Selected angle is stale or unavailable: {group_id_filter}/{requested_angle}")
    return {"target_anatomy": target, "groups": result, "view_count": sum(len(g["views"]) for g in result)}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import-atlas")
    imp.add_argument("--source-index", type=Path, required=True); imp.add_argument("--source-root", type=Path, required=True)
    imp.add_argument("--review-file", type=Path, required=True); imp.add_argument("--library-root", type=Path, required=True)
    imp.add_argument("--json", action="store_true")
    val = sub.add_parser("validate"); val.add_argument("--library-root", type=Path, required=True); val.add_argument("--json", action="store_true")
    ctx = sub.add_parser("context"); ctx.add_argument("--library-root", type=Path, required=True)
    ctx.add_argument("--target-anatomy", default="ANY"); ctx.add_argument("--query", default="")
    ctx.add_argument("--group-id", default=""); ctx.add_argument("--angle", default=""); ctx.add_argument("--source-id", default=""); ctx.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = import_atlas(args) if args.command == "import-atlas" else validate_library(args.library_root.resolve()) if args.command == "validate" else reference_context(args.library_root, args.target_anatomy, args.query, args.group_id, args.angle, args.source_id)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else "\n".join(f"{k}={v}" for k, v in result.items()))
        return 0
    except (PoseReferenceError, OSError, KeyError, TypeError) as e:
        print(json.dumps({"status": "ERROR", "error": str(e)}, ensure_ascii=False) if getattr(args, "json", False) else f"ERROR={e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
