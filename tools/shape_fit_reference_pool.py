"""Registry-backed metadata adapter for the SODABABYX shape/fit pool.

This adapter keeps collection review separate from author approval and generation
eligibility. It never edits or copies source images.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ShapeFitError(ValueError):
    pass


SUPPORTED_ROLES = frozenset({
    "BODY_SHAPE_REFERENCE",
    "CLOTHING_FIT_REFERENCE",
    "POSE_REFERENCE",
    "MOUTH_EXPRESSION_REFERENCE",
})
ROLE_CATEGORIES = {
    "BODY_SHAPE_REFERENCE": frozenset({"BODY_SHAPE", "BODY_PART_SHAPE"}),
    "CLOTHING_FIT_REFERENCE": frozenset({"FABRIC_FIT"}),
    "POSE_REFERENCE": frozenset({"POSE_ACTION"}),
    "MOUTH_EXPRESSION_REFERENCE": frozenset({"BODY_PART_SHAPE"}),
}
SUBJECTS = frozenset({"MALE", "FEMALE"})
PLAN_ROLES = {
    "BODY_SHAPE_REFERENCE": "AUX_BODY_SHAPE",
    "CLOTHING_FIT_REFERENCE": "AUX_CLOTHING_BEHAVIOR",
    "POSE_REFERENCE": "AUX_POSE",
    "MOUTH_EXPRESSION_REFERENCE": "AUX_MOUTH_EXPRESSION",
}


def plan_references(workspace: Path, values: list[str], target: dict[str, Any]) -> list[dict[str, Any]]:
    """Bind explicit sheet/role/variant choices to the ordinary reviewed-call path."""
    try:
        from tools.reference_compatibility import validate_reference_compatibility
    except ModuleNotFoundError:
        from reference_compatibility import validate_reference_compatibility

    subject = {"MALE_ANATOMY": "MALE", "FEMALE_ANATOMY": "FEMALE"}.get(target.get("target_anatomy"), "")
    result = []
    for value in values:
        parts = value.split("=")
        if len(parts) != 3:
            raise ShapeFitError("--shape-fit-reference requires ID=ROLE=VARIANT")
        ident, role, variant = parts
        selection = select_candidate(workspace, "SODABABYX", ident, role, subject, variant)
        if any(item["path"] == selection["path"] for item in result):
            raise ShapeFitError("select only one role/variant per shape/fit sheet in a call")
        record = {
            "ref_id": ident, "mode": role, "pool": "SODABABYX",
            "selection_spec": value, "variant": selection["variant"],
            "path": selection["path"], "sha256": selection["sha256"],
            "source_manifest_sha256": selection["source_manifest_sha256"],
            "curated_manifest_sha256": selection["curated_manifest_sha256"],
            "active_roles": [PLAN_ROLES[role]], "source_character_id": "UNKNOWN",
            "subject_label": selection["subject_label"], "subject_label_status": "CONFIRMED",
            "subject_label_sha256": selection["sha256"],
            "subject_label_evidence": selection["provenance"]["subject_label_evidence"],
            "style_influence": "FORBIDDEN", "review": selection["review"],
            "transfer_contract": "Use only the named variant and role. Preserve confirmed character identity and permanent proportions, selected clothing design and coverage, and the chosen rendering style. Never transfer palette, shading or linework.",
        }
        for key in ("singles_manifest_sha256", "subject_usage_scope"):
            if key in selection:
                record[key] = selection[key]
        record["compatibility"] = validate_reference_compatibility(target, record, record["active_roles"])
        if not record["compatibility"]["compatible"]:
            raise ShapeFitError(record["compatibility"]["reason"])
        result.append(record)
    return result


def revalidate_plan_reference(workspace: Path, record: dict[str, Any], target: dict[str, Any], roles: list[str]) -> None:
    fresh = plan_references(workspace, [record["selection_spec"]], target)[0]
    for key in ("path", "sha256", "source_manifest_sha256", "curated_manifest_sha256", "singles_manifest_sha256", "variant", "active_roles", "transfer_contract", "subject_label", "subject_label_status", "subject_label_sha256", "subject_label_evidence", "subject_usage_scope"):
        if record.get(key) != fresh.get(key):
            raise ShapeFitError(f"planned shape/fit reference is stale: {key}")
    if set(roles) != set(fresh["active_roles"]):
        raise ShapeFitError("shape/fit attachment role differs from its selected role")


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _safe(root: Path, relative: str, workspace: Path) -> Path:
    rel = Path(str(relative).replace("\\", "/"))
    if not relative or rel.is_absolute() or ".." in rel.parts:
        raise ShapeFitError("unsafe or empty source path")
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
        path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ShapeFitError("source path escapes the pool or workspace") from exc
    return path


def _registry(workspace: Path, pool: str) -> tuple[dict[str, Any], Path]:
    workspace = workspace.resolve()
    registry_path = workspace / "config/reference_pools.json"
    pools = _json(registry_path).get("pools", {})
    if pool not in pools:
        raise ShapeFitError(f"unknown reference pool: {pool}")
    config = pools[pool]
    if config.get("adapter") != "shape_fit":
        raise ShapeFitError(f"pool is not a shape_fit adapter: {pool}")
    root = (workspace / config.get("root", "")).resolve()
    try:
        root.relative_to(workspace)
    except ValueError as exc:
        raise ShapeFitError("pool root escapes workspace") from exc
    return config, root


def _workspace_file(workspace: Path, relative: str) -> Path:
    path = (workspace / str(relative)).resolve()
    try:
        path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ShapeFitError("manifest path escapes workspace") from exc
    return path


def _source_binding(curated: dict[str, Any], source: dict[str, Any]) -> bool:
    digest = curated.get("sha256")
    return bool(
        digest
        and source.get("id") == curated.get("id")
        and source.get("sha256") == digest
        and source.get("local_path") == curated.get("local_path")
        and source.get("review_binding_sha256") == digest
        and curated.get("review_binding_sha256") == digest
        and source.get("review_status") == curated.get("review_status")
        and source.get("subject_label") == curated.get("subject_label")
        and not source.get("excluded")
        and not source.get("duplicate_of")
        and not curated.get("excluded")
        and not curated.get("duplicate_of")
    )


def _normalized(row: dict[str, Any], source: dict[str, Any], binding: bool) -> dict[str, Any]:
    label = row.get("subject_label", "UNKNOWN")
    confirmed = (
        binding
        and label in SUBJECTS
        and row.get("subject_label_status") == "CONFIRMED"
        and row.get("subject_label_sha256") == row.get("sha256")
    )
    unresolved = not confirmed
    provenance = {
        "source_profile": row.get("source_profile", ""),
        "post_url": row.get("post_url", ""),
        "review_status": row.get("review_status", ""),
        "review_binding_sha256": row.get("review_binding_sha256", ""),
        "source_manifest_id": source.get("id", ""),
        "source_manifest_sha256": source.get("sha256", ""),
        "duplicate_of": row.get("duplicate_of", ""),
        "excluded": row.get("excluded", False),
    }
    if not unresolved:
        provenance["source_image_url"] = row.get("source_image_url", "")
    result = {
        "id": row.get("id", ""),
        "title": row.get("title_ru", ""),
        "tags": list(row.get("tags", [])),
        "categories": list(row.get("categories", [])),
        "subject_label": label,
        "subject_label_status": "CONFIRMED" if confirmed else "UNKNOWN",
        "sha256": row.get("sha256", ""),
        "relative_path": "" if unresolved else row.get("local_path", ""),
        "role": "",
        "variants": list(row.get("variants", [])),
        "review_state": row.get("review_status", "NOT_CHECKED") if binding else "SOURCE_BINDING_STALE",
        "record_binding_status": "CURRENT" if binding else "STALE",
        "provenance": provenance,
        "raw": row,
    }
    return result


def catalog_records(workspace: Path, pool: str) -> tuple[Path, list[dict[str, Any]]]:
    """Return normalized manager records while withholding unresolved media paths."""
    config, root = _registry(workspace, pool)
    curated_path = _workspace_file(workspace, config["manifest"])
    source_path = _workspace_file(workspace, config["source_manifest"])
    curated = _json(curated_path).get("records", [])
    source = _json(source_path).get("records", [])
    sources: dict[str, list[dict[str, Any]]] = {}
    for row in source:
        sources.setdefault(str(row.get("id", "")), []).append(row)
    result = []
    seen: set[str] = set()
    for row in curated:
        ident = str(row.get("id", ""))
        if not ident or ident in seen:
            # Keep duplicate IDs visible to validation but never selectable.
            result.append(_normalized(row, {}, False))
            continue
        seen.add(ident)
        matches = sources.get(ident, [])
        source_row = matches[0] if len(matches) == 1 else {}
        binding = len(matches) == 1 and _source_binding(row, source_row)
        result.append(_normalized(row, source_row, binding))
    return root, result


def select_candidate(
    workspace: Path, pool: str, ident: str, role: str, subject: str, variant: str
) -> dict[str, Any]:
    """Validate the current registry bindings and export one unapproved candidate."""
    workspace = workspace.resolve()
    config, root = _registry(workspace, pool)
    if "__" in ident:
        return _select_single(workspace, config, root, ident, role, subject, variant)
    curated_path = _workspace_file(workspace, config["manifest"])
    source_path = _workspace_file(workspace, config["source_manifest"])
    curated_rows = _json(curated_path).get("records", [])
    source_rows = _json(source_path).get("records", [])
    candidates = [row for row in curated_rows if row.get("id") == ident]
    sources = [row for row in source_rows if row.get("id") == ident]
    if len(candidates) != 1 or len(sources) != 1:
        raise ShapeFitError("candidate/source ID is missing or duplicated")
    row, source = candidates[0], sources[0]
    if not _source_binding(row, source):
        raise ShapeFitError("source manifest, duplicate, exclusion, or review binding is stale")
    if row.get("review_status") != "REVIEWED_WITH_LIMITATIONS" or not isinstance(row.get("review"), dict):
        raise ShapeFitError("candidate lacks the required bounded visual review")
    if row.get("style_influence") != "FORBIDDEN":
        raise ShapeFitError("style influence must remain forbidden")
    if row.get("generation_eligible") is not False:
        raise ShapeFitError("collection records must remain generation-ineligible")
    if row.get("source_character_id") != "UNKNOWN":
        raise ShapeFitError("collection records cannot claim character identity")
    if role not in SUPPORTED_ROLES or role not in row.get("allowed_roles", []):
        raise ShapeFitError("role is not supported by this source record")
    if not (set(row.get("categories", [])) & ROLE_CATEGORIES[role]):
        raise ShapeFitError("reviewed categories do not support the requested role")
    forbidden = set(row.get("forbidden_roles", []))
    if role in forbidden or role in {"STYLE", "IDENTITY", "BODY_BUILD_TARGET"}:
        raise ShapeFitError("role is forbidden by the source contract")
    label = row.get("subject_label", "UNKNOWN")
    if row.get("subject_label_status") != "CONFIRMED" or label not in SUBJECTS:
        raise ShapeFitError("unknown or quarantined subject; image path withheld")
    if row.get("subject_label_sha256") != row.get("sha256"):
        raise ShapeFitError("subject label is not bound to the current source hash")
    if not row.get("subject_label_evidence") or row.get("subject_label_evidence") == "UNKNOWN":
        raise ShapeFitError("subject label lacks review evidence")
    if str(subject).upper() != label:
        raise ShapeFitError("requested subject must exactly match confirmed source label")
    variants = row.get("variants", [])
    if variants:
        selected = [item for item in variants if item.get("id") == variant]
        if len(selected) != 1:
            raise ShapeFitError("an explicit valid --variant is required for this sheet")
        selected_variant = selected[0]
    else:
        if variant:
            raise ShapeFitError("candidate has no selectable variants")
        selected_variant = {"id": "WHOLE_IMAGE", "label": "Whole image", "position": 1, "location_rule": "Use the reviewed source as a whole."}
    path = _safe(root, row.get("local_path", ""), workspace)
    if not path.is_file() or _sha(path) != row.get("sha256"):
        raise ShapeFitError("candidate source file/hash is stale or missing")
    return {
        "schema_version": 1,
        "type": "CANDIDATE_SELECTION",
        "pool": pool,
        "candidate_id": ident,
        "role": role,
        "subject_label": label,
        "variant": selected_variant,
        "sha256": row["sha256"],
        "path": str(path),
        "source_manifest_sha256": _sha(source_path),
        "curated_manifest_sha256": _sha(curated_path),
        "review": row["review"],
        "restrictions": {
            "generation_eligible": False,
            "approval_status": "NOT_APPROVED",
            "style_influence": "FORBIDDEN",
            "source_character_id": "UNKNOWN",
            "forbidden_roles": sorted(forbidden),
            "rights_status": row.get("rights_status", "UNVERIFIED_LOCAL_REFERENCE_ONLY"),
        },
        "provenance": {
            "source_profile": row.get("source_profile", ""),
            "post_url": row.get("post_url", ""),
            "source_image_url": row.get("source_image_url", ""),
            "local_path": row.get("local_path", ""),
            "source_manifest_path": str(source_path),
            "curated_manifest_path": str(curated_path),
            "source_manifest_sha256": source.get("sha256", ""),
            "review_binding_sha256": row.get("review_binding_sha256", ""),
            "subject_label_evidence": row.get("subject_label_evidence", ""),
        },
    }


def _select_single(workspace: Path, config: dict[str, Any], root: Path, ident: str,
                   role: str, subject: str, variant: str) -> dict[str, Any]:
    """Select an accepted crop without relabelling its subject or its parent sheet."""
    manifest = root / "SINGLES_MANIFEST.json"
    data = _json(manifest)
    matches = [row for row in data.get("records", []) if row.get("id") == ident]
    if len(matches) != 1 or not data.get("collection_acceptance", {}).get("quote"):
        raise ShapeFitError("single requires a unique accepted collection record")
    row = matches[0]
    if row.get("collection_accepted") is not True or not row.get("visual_review"):
        raise ShapeFitError("single lacks collection acceptance or crop review")
    curated_path = _workspace_file(workspace, config["manifest"])
    source_path = _workspace_file(workspace, config["source_manifest"])
    parents = [x for x in _json(curated_path).get("records", []) if x.get("id") == row.get("source_id")]
    sources = [x for x in _json(source_path).get("records", []) if x.get("id") == row.get("source_id")]
    if len(parents) != 1 or len(sources) != 1 or not _source_binding(parents[0], sources[0]):
        raise ShapeFitError("single parent source binding is stale")
    parent = parents[0]
    if row.get("source_sha256") != parent.get("sha256") or row.get("source_path") != parent.get("local_path"):
        raise ShapeFitError("single provenance differs from its registered parent")
    original = _safe(root, row["source_path"], workspace)
    if not original.is_file() or _sha(original) != row["source_sha256"]:
        raise ShapeFitError("single parent bytes are stale")
    if row.get("style_influence") != "FORBIDDEN" or row.get("generation_eligible") is not False:
        raise ShapeFitError("single must not claim style or global generation approval")
    mouth = row.get("subcategory") == "mouth"
    if role == "MOUTH_EXPRESSION_REFERENCE":
        if not mouth:
            raise ShapeFitError("mouth expression role requires an isolated mouth record")
    elif role not in SUPPORTED_ROLES or role not in parent.get("allowed_roles", []) or mouth:
        raise ShapeFitError("role is not supported by this single")
    label = row.get("subject_label")
    if (row.get("subject_label_status") != "CONFIRMED"
            or row.get("subject_label_sha256") != row.get("sha256")
            or not row.get("subject_label_evidence")):
        raise ShapeFitError("single subject classification is stale or unreviewed")
    if str(subject).upper() not in SUBJECTS:
        raise ShapeFitError("single requires an explicit male or female target")
    neutral_mouth = (label == "NEUTRAL" and mouth and role == "MOUTH_EXPRESSION_REFERENCE"
                     and row.get("generation_subject_gate") == "NEUTRAL_REQUIRES_SCOPED_REVIEW")
    if not neutral_mouth and (label not in SUBJECTS or label != str(subject).upper()):
        raise ShapeFitError("requested subject must exactly match confirmed source label; neutral poses are forbidden")
    if variant != row.get("variant_id"):
        raise ShapeFitError("single requires its explicit exact --variant")
    path = _safe(root, row.get("local_path", ""), workspace)
    if not path.is_file() or _sha(path) != row.get("sha256"):
        raise ShapeFitError("single source file/hash is stale or missing")
    return {
        "schema_version": 1, "type": "CANDIDATE_SELECTION", "pool": "SODABABYX",
        "candidate_id": ident, "role": role, "subject_label": label,
        "subject_usage_scope": "MOUTH_EXPRESSION_ONLY" if neutral_mouth else "MATCH_TARGET_SEX",
        "variant": {"id": variant, "label": row.get("name_ru", row.get("label", variant)),
                    "location_rule": "Use this isolated single only, not other variants on its parent sheet."},
        "sha256": row["sha256"], "path": str(path),
        "source_manifest_sha256": _sha(source_path), "curated_manifest_sha256": _sha(curated_path),
        "singles_manifest_sha256": _sha(manifest),
        "review": {"observations": row["visual_review"], "limitations": row.get("limitations", "")},
        "restrictions": {"generation_eligible": False, "approval_status": "NOT_APPROVED",
                         "style_influence": "FORBIDDEN", "source_character_id": "UNKNOWN"},
        "provenance": {"source_id": row["source_id"], "source_path": row["source_path"],
                       "source_sha256": row["source_sha256"], "source_bbox_px": row.get("source_bbox_px"),
                       "subject_label_evidence": row["subject_label_evidence"],
                       "collection_acceptance": data["collection_acceptance"]},
    }
