"""Canvas, scene and body-proportion contracts plus anthropometric QA for StoryArt plans.

Split from style_pack_manager.py; the manager re-exports every name defined here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

try:
    from tools.style_pack_common import StylePackError
except ModuleNotFoundError:  # Direct execution from tools/
    from style_pack_common import StylePackError


DEFAULT_ASPECT_BY_ORIENTATION = {"PORTRAIT": "9:16", "LANDSCAPE": "16:9"}

STANDARD_ASPECT_RATIOS = {"9:16", "16:9"}

SCENE_KINDS = ("LOCATION", "PHENOMENON", "ARTIFACT", "MIXED")

SCENE_OUTPUT_USES = ("GENERAL_ART", "WALLPAPER", "PROMO_POSTER")

ANTHROPOMETRIC_QA_LIMITS = {
    # Conservative StoryArt production limits for an adult canonical standing view.
    # Ratios are measured on the rendered figure, not inferred from the prompt.
    "head_units": (7.0, 8.25),
    "pubic_height_fraction": (0.47, 0.53),
    "lower_to_upper_leg_ratio": (0.75, 1.20),
    "ankle_width_head_ratio": (0.12, 0.30),
    "foot_length_head_ratio": (0.75, 1.20),
    "neck_head_ratio": (0.20, 0.48),
    "neck_jaw_ratio": (0.50, 0.90),
}

CONSTRUCTION_GEOMETRY_METHOD_ID = "AUTHOR_CONFIRMED_CONSTRUCTION_GEOMETRY"
CONSTRUCTION_GEOMETRY_VERSION = "1"
CONSTRUCTION_GEOMETRY_NUMERIC_SOURCE = "AUTHOR_CONFIRMED_CONSTRUCTION_NOT_VISIBLE_MEASUREMENT"


LOCAL_DERIVATIVE_KIND = "LOCAL_DETERMINISTIC_FOREFOOT_CORRECTION"
LOCAL_DERIVATIVE_ROI = (270, 1525, 560, 1643)


def validate_local_derivative_receipt(
    receipt_path: Path,
    *,
    request_root: Path,
    request_id: str,
    character_id: str,
    stage_id: str,
    output: Path,
    guard_path: Path,
    attempt_id: str,
    allow_accepted_stage_replay: bool = False,
) -> dict[str, object]:
    """Recompute and bind one narrowly scoped, current-request SIDE derivative.

    The recipe is only hash checked; this validator never imports or executes it.
    The derivative is not represented as provider bytes or art approval.
    """
    from PIL import Image, ImageChops

    request_root = request_root.resolve()
    guard_path = guard_path.resolve()
    output = output.resolve()

    def inside(path: Path, label: str) -> Path:
        resolved = path.expanduser().resolve()
        if not resolved.is_file() or not resolved.is_relative_to(request_root):
            raise StylePackError(f"Local derivative {label} must be an existing file in the exact active request folder.")
        return resolved

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    try:
        receipt_path = inside(receipt_path, "receipt")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        guard = json.loads(guard_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read local derivative receipt or execution guard: {error}") from error
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        raise StylePackError("Local derivative receipt must use schema_version=1.")
    if (request_root.name != request_id or receipt.get("request_id") != request_id
            or guard.get("request_id") != request_id or character_id.upper() != "NEW"
            or receipt.get("character_id") != "NEW" or receipt.get("generation_purpose") != "CHARACTER_BASE"
            or receipt.get("stage_id") != "03_PHYSIQUE_SIDE"
            or stage_id.upper() != "03_PHYSIQUE_SIDE"
            or receipt.get("kind") != LOCAL_DERIVATIVE_KIND):
        raise StylePackError("Local derivative receipt is outside the exact NEW CHARACTER_BASE SIDE request scope.")
    base = guard.get("character_base_contract")
    if (not isinstance(base, dict) or base.get("age_group") != "ADULT"
            or base.get("subject_anatomy") != "MALE_ANATOMY"):
        raise StylePackError("Local derivative requires the guard's adult MALE CHARACTER_BASE contract.")
    if guard_path.parent.resolve() != request_root:
        raise StylePackError("Local derivative execution guard must be in the exact active request folder.")
    attempts = guard.get("attempts")
    candidates = [row for row in attempts if isinstance(row, dict) and row.get("attempt_id") == attempt_id] if isinstance(attempts, list) else []
    if len(candidates) != 1:
        raise StylePackError("Local derivative must bind exactly one existing provider attempt.")
    attempt = candidates[0]
    side_attempts = [row for row in attempts if isinstance(row, dict)
                      and row.get("stage") == "03_PHYSIQUE_SIDE"
                      and row.get("task_revision") == guard.get("task_revision")]
    latest_attempt_id = next((row.get("attempt_id") for row in reversed(attempts) if isinstance(row, dict)), None)
    source_is_current = bool(
        side_attempts and side_attempts[-1] is attempt
        and latest_attempt_id == attempt_id
        and attempt.get("task_revision") == guard.get("task_revision")
        and attempt.get("status") in {"RESULT_AVAILABLE", "DELIVERED"}
    )
    if (attempt.get("status") not in {"RESULT_AVAILABLE", "DELIVERED"}
            or (not source_is_current and not allow_accepted_stage_replay)):
        raise StylePackError("Local derivative must bind the current SIDE result or its unchanged accepted stage proof.")
    snapshot = attempt.get("execution_snapshot")
    provider = attempt.get("provider_artifact")
    snap_provider = snapshot.get("provider_artifact") if isinstance(snapshot, dict) else None
    if (not isinstance(snapshot, dict) or not isinstance(provider, dict)
            or attempt.get("stage") != "03_PHYSIQUE_SIDE"
            or snapshot.get("request_id") != request_id or snapshot.get("attempt_id") != attempt_id
            or snapshot.get("request_purpose") != "CHARACTER_BASE"
            or snapshot.get("execution_stage") != "03_PHYSIQUE_SIDE"):
        raise StylePackError("Local derivative is not bound to an executed adult CHARACTER_BASE SIDE attempt.")
    if not isinstance(snap_provider, dict):
        snap_provider = provider
    provider_path = inside(Path(str(provider.get("path", ""))), "original provider source")
    provider_hash = str(provider.get("sha256", "")).lower()
    if (provider_path != inside(Path(str(snap_provider.get("path", ""))), "snapshot provider source")
            or provider_hash != str(snap_provider.get("sha256", "")).lower()
            or not re.fullmatch(r"[0-9a-f]{64}", provider_hash)
            or digest(provider_path) != provider_hash):
        raise StylePackError("Local derivative original provider path/hash differs from the immutable attempt binding.")
    if receipt.get("source_attempt_id") != attempt_id:
        raise StylePackError("Local derivative source attempt differs from the exact provider attempt.")
    executed_plan_path = inside(Path(str(receipt.get("source_executed_plan_path", ""))), "immutable executed plan")
    executed_plan_hash = str(receipt.get("source_executed_plan_sha256", "")).lower()
    if (executed_plan_path != Path(str(snapshot.get("reference_plan_snapshot_path", ""))).resolve()
            or executed_plan_hash != str(snapshot.get("reference_plan_snapshot_sha256", "")).lower()
            or not re.fullmatch(r"[0-9a-f]{64}", executed_plan_hash)
            or digest(executed_plan_path) != executed_plan_hash):
        raise StylePackError("Local derivative source immutable executed-plan path/hash is stale.")

    def receipt_file(path_key: str, hash_key: str, label: str) -> Path:
        file = inside(Path(str(receipt.get(path_key, ""))), label)
        expected = str(receipt.get(hash_key, "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest(file) != expected:
            raise StylePackError(f"Local derivative {label} hash is missing or stale.")
        return file

    source = receipt_file("source_image", "source_sha256", "source")
    derivative = receipt_file("output_image", "output_sha256", "output")
    mask_path = receipt_file("mask_path", "mask_sha256", "mask")
    directive_path = receipt_file("directive_path", "directive_sha256", "directive")
    recipe_path = receipt_file("recipe_path", "recipe_sha256", "recipe")
    if (source != provider_path or str(receipt.get("source_sha256", "")).lower() != provider_hash
            or derivative != output):
        raise StylePackError("Local derivative source must be the exact provider bytes and output must be the submitted image.")

    try:
        directive = json.loads(directive_path.read_text(encoding="utf-8"))
        operation_path = inside(Path(str(directive.get("operation_specification", ""))), "operation specification")
        operation_hash = str(directive.get("operation_specification_sha256", "")).lower()
        operation = json.loads(operation_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError) as error:
        raise StylePackError(f"Local derivative directive or operation specification is invalid: {error}") from error
    if (directive.get("schema_version") != 1 or directive.get("request_id") != request_id
            or directive.get("scope") != "CURRENT_SIDE_LOCAL_FOOT_CORRECTION_AND_NATIVE_DERIVATIVE_REGISTRATION"
            or not isinstance(directive.get("user_directive"), str)
            or not directive["user_directive"].strip()
            or "\ufffd" in directive["user_directive"]
            or not isinstance(directive.get("source"), str)
            or "Current chat structured free-text reply" not in directive["source"]
            or not isinstance(directive.get("authority_basis"), str)
            or "Existing full-kit work authorization" not in directive["authority_basis"]
            or "exact reply retained" not in directive["authority_basis"]
            or not isinstance(directive.get("forbidden_changes"), str)
            or "Everything outside declared region" not in directive["forbidden_changes"]
            or "fakeQA" not in directive["forbidden_changes"]
            or directive.get("art_approval") is not False
            or Path(str(directive.get("source_image", ""))).resolve() != source
            or str(directive.get("source_sha256", "")).lower() != provider_hash
            or not re.fullmatch(r"[0-9a-f]{64}", operation_hash)
            or digest(operation_path) != operation_hash):
        raise StylePackError("Local derivative directive lacks its exact current-chat context, execution authority, or source binding.")
    if (not isinstance(operation, dict) or operation.get("schema_version") != 1
            or operation.get("status") != "PROPOSED_NOT_EXECUTED"
            or Path(str(operation.get("source_image", ""))).resolve() != source
            or operation.get("source_sha256", "").lower() != provider_hash
            or not isinstance(operation.get("scope"), str)
            or "forefoot" not in operation["scope"].lower()
            or "no full-frame generation" not in operation["scope"].lower()
            or tuple(operation.get("roi", ())) != LOCAL_DERIVATIVE_ROI
            or operation.get("fixed_ankle_x") != 475
            or operation.get("fixed_heel_x") != 532
            or operation.get("target_heel_to_toe") != 190
            or operation.get("vertical_displacement") not in (None, 0)
            or operation.get("approval") is not None):
        raise StylePackError("Local derivative operation specification is not the authorized fixed-ankle 190-pixel forefoot correction.")
    geometry_path = receipt_file("geometry_path", "geometry_sha256", "approved geometry")
    approval_path = receipt_file("geometry_approval_path", "geometry_approval_sha256", "geometry approval")
    try:
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StylePackError(f"Local derivative approved construction source is invalid: {error}") from error
    dimensions = geometry.get("design_dimensions", {}) if isinstance(geometry, dict) else {}
    if (not isinstance(geometry, dict) or geometry.get("request_id") != request_id
            or geometry.get("character_id") != "NEW"
            or not isinstance(dimensions, dict) or dimensions.get("side_foot_length") != 190
            or not isinstance(approval, dict) or approval.get("request_id") != request_id
            or approval.get("character_id") != "NEW"
            or approval.get("geometry_sha256", "").lower() != digest(geometry_path)
            or not isinstance(approval.get("approval_quote"), str) or not approval["approval_quote"].strip()
            or not isinstance(approval.get("approval_source"), str) or not approval["approval_source"].strip()
            or approval.get("scope") != "CONSTRUCTION_GEOMETRY_ONLY"
            or approval.get("approves_previous_illustration") is not False
            or approval.get("numeric_visible_landmarks_remain") != "NOT_CHECKED"):
        raise StylePackError("Local derivative 190-pixel target lacks its exact scoped approved construction source.")
    if tuple(receipt.get("roi", ())) != LOCAL_DERIVATIVE_ROI or receipt.get("target_heel_to_toe") != 190:
        raise StylePackError("Local derivative receipt ROI or target length differs from the authorized operation.")
    if (receipt.get("fixed_ankle_x") != 475 or receipt.get("fixed_heel_x") != 532
            or receipt.get("vertical_displacement") != 0
            or receipt.get("state") != "POSTPROCESSED_QA_PENDING"):
        raise StylePackError("Local derivative receipt changes the fixed ankle, posterior heel, or foot vertical alignment.")

    try:
        with Image.open(source) as im:
            if im.mode != "RGB":
                raise StylePackError("Local derivative source must be an RGB image.")
            src = im.copy()
        with Image.open(derivative) as im:
            if im.mode != "RGB":
                raise StylePackError("Local derivative output must be an RGB image.")
            dst = im.copy()
        with Image.open(mask_path) as im:
            mask = im.convert("L").point(lambda value: 255 if value > 0 else 0)
    except OSError as error:
        raise StylePackError(f"Local derivative images could not be decoded: {error}") from error
    if src.size != dst.size or src.size != mask.size or src.mode != "RGB" or dst.mode != "RGB":
        raise StylePackError("Local derivative source, output, and mask must have the same canvas dimensions.")
    x0, y0, x1, y1 = LOCAL_DERIVATIVE_ROI
    mask_bbox = mask.getbbox()
    if mask_bbox is None or mask_bbox[0] < x0 or mask_bbox[1] < y0 or mask_bbox[2] > x1 or mask_bbox[3] > y1:
        raise StylePackError("Local derivative mask must be nonempty and contained inside the fixed forefoot ROI.")
    difference = ImageChops.difference(src, dst)
    changed = ImageChops.lighter(
        ImageChops.lighter(difference.getchannel(0), difference.getchannel(1)),
        difference.getchannel(2),
    )
    changed = changed.point(lambda value: 255 if value > 0 else 0)
    outside_mask = ImageChops.multiply(changed, ImageChops.invert(mask))
    if outside_mask.getbbox() is not None:
        raise StylePackError("Local derivative changes pixels outside its hash-bound mask.")
    changed_bbox = changed.getbbox()
    if changed_bbox is None or changed_bbox[0] < x0 or changed_bbox[1] < y0 or changed_bbox[2] > x1 or changed_bbox[3] > y1:
        raise StylePackError("Local derivative changed-pixel bounding box must be nonempty and contained in the authorized ROI.")
    if (ImageChops.difference(src.crop((475, 0, src.width, src.height)), dst.crop((475, 0, dst.width, dst.height))).getbbox() is not None
            or ImageChops.difference(src.crop((0, 0, src.width, y0)), dst.crop((0, 0, dst.width, y0))).getbbox() is not None):
        raise StylePackError("Local derivative changed the fixed ankle/posterior heel or upper-body pixels.")
    changed_count = changed.histogram()[255]
    if receipt.get("changed_pixel_count") != changed_count:
        raise StylePackError("Local derivative changed-pixel count differs from recomputed pixels.")
    proof = {
        "schema_version": 1,
        "kind": LOCAL_DERIVATIVE_KIND,
        "request_id": request_id,
        "character_id": character_id,
        "stage_id": stage_id.upper(),
        "attempt_id": attempt_id,
        "task_revision": int(attempt.get("task_revision", -1)),
        "provider_artifact": {"path": str(provider_path), "sha256": provider_hash},
        "source_executed_plan_path": str(executed_plan_path),
        "source_executed_plan_sha256": executed_plan_hash,
        "derived_artifact": {"path": str(derivative), "sha256": digest(derivative)},
        "receipt_path": str(receipt_path),
        "receipt_sha256": digest(receipt_path),
        "mask_path": str(mask_path), "mask_sha256": digest(mask_path),
        "directive_path": str(directive_path), "directive_sha256": digest(directive_path),
        "recipe_path": str(recipe_path), "recipe_sha256": digest(recipe_path),
        "operation_specification_path": str(operation_path), "operation_specification_sha256": digest(operation_path),
        "geometry_path": str(geometry_path), "geometry_sha256": digest(geometry_path),
        "geometry_approval_path": str(approval_path), "geometry_approval_sha256": digest(approval_path),
        "guard_path": str(guard_path),
        "roi": list(LOCAL_DERIVATIVE_ROI),
        "changed_pixel_count": changed_count,
        "changed_bbox": list(changed_bbox),
        "art_approval": False,
    }
    if allow_accepted_stage_replay:
        required_stages = guard.get("required_stages")
        stage_rows = [row for row in required_stages if isinstance(row, dict)
                      and str(row.get("id", "")).upper() == "03_PHYSIQUE_SIDE"] if isinstance(required_stages, list) else []
        if len(stage_rows) != 1:
            raise StylePackError("Local derivative replay requires the exact SIDE required-stage record.")
        stage_row = stage_rows[0]
        stage_events = [event for event in guard.get("events", []) if isinstance(event, dict)
                        and str(event.get("stage", "")).upper() == "03_PHYSIQUE_SIDE"
                        and event.get("event") in {"STAGE_COMPLETED", "STAGE_REOPENED"}]
        if stage_row.get("status") == "COMPLETED":
            binding = stage_row.get("accepted_attempt_binding")
            derived = proof.get("derived_artifact", {})
            if (not isinstance(binding, dict)
                    or binding.get("attempt_id") != attempt_id
                    or binding.get("task_revision") != attempt.get("task_revision")
                    or binding.get("result_status") != attempt.get("result_status")
                    or Path(str(binding.get("artifact_path", ""))).resolve() != provider_path
                    or str(binding.get("artifact_sha256", "")).lower() != provider_hash
                    or binding.get("derived_artifact") != proof
                    or not isinstance(derived, dict)):
                raise StylePackError("Accepted SIDE stage no longer binds the exact local derivative and original provider result.")
            stage_evidence = Path(str(binding.get("stage_evidence_path", ""))).resolve()
            stage_evidence_hash = str(binding.get("stage_evidence_sha256", "")).lower()
            recorded_hashes = stage_row.get("evidence_sha256", {})
            if (not stage_evidence.is_file() or not stage_evidence.is_relative_to(request_root)
                    or digest(stage_evidence) != stage_evidence_hash
                    or stage_evidence_hash != str(derived.get("sha256", "")).lower()
                    or not isinstance(recorded_hashes, dict)
                    or str(recorded_hashes.get(str(stage_evidence), "")).lower() != stage_evidence_hash):
                raise StylePackError("Accepted SIDE stage evidence was reopened or changed after derivative registration.")
            if stage_events and stage_events[-1].get("event") != "STAGE_COMPLETED":
                raise StylePackError("SIDE stage was reopened after accepting the local derivative.")
        else:
            if (not source_is_current
                    or (stage_events and stage_events[-1].get("event") == "STAGE_REOPENED")):
                raise StylePackError("An unaccepted or reopened SIDE stage cannot use historical derivative replay.")
    return proof


def construction_geometry_violations(
    values: object,
    plan: dict[str, object] | None = None,
) -> list[str]:
    """Validate design-geometry ratios against the unchanged adult limits.

    These are dimensions from an approved construction scheme, never claims
    about measurements taken from the rendered image.
    """
    if not isinstance(values, dict):
        raise StylePackError("Construction geometry values must be an object.")
    names = (
        "head_units", "pubic_height_fraction", "lower_to_upper_leg_ratio",
        "ankle_width_head_ratio", "foot_length_head_ratio", "neck_head_ratio",
        "neck_jaw_ratio",
    )
    numeric: dict[str, float] = {}
    for name in names:
        try:
            numeric[name] = float(values[name])
        except (KeyError, TypeError, ValueError):
            raise StylePackError(f"Construction geometry requires numeric {name}.")
        if numeric[name] <= 0:
            raise StylePackError(f"Construction geometry {name} must be greater than zero.")
    limits = dict(ANTHROPOMETRIC_QA_LIMITS)
    body_contract = plan.get("body_proportion_contract", {}) if isinstance(plan, dict) else {}
    height_contract = body_contract.get("height_in_heads", {}) if isinstance(body_contract, dict) else {}
    if isinstance(height_contract, dict) and height_contract.get("mode") == "EXPLICIT_RANGE":
        try:
            lower, upper = float(height_contract["minimum"]), float(height_contract["maximum"])
        except (KeyError, TypeError, ValueError):
            raise StylePackError("Construction geometry body-height contract requires numeric bounds.")
        if lower <= 0 or upper < lower:
            raise StylePackError("Construction geometry body-height contract has invalid bounds.")
        limits["head_units"] = (max(limits["head_units"][0], lower), min(limits["head_units"][1], upper))
    measured = {
        "head_units": numeric["head_units"],
        "pubic_height_fraction": numeric["pubic_height_fraction"],
        "lower_to_upper_leg_ratio": numeric["lower_to_upper_leg_ratio"],
        "ankle_width_head_ratio": numeric["ankle_width_head_ratio"],
        "foot_length_head_ratio": numeric["foot_length_head_ratio"],
        "neck_head_ratio": numeric["neck_head_ratio"],
        "neck_jaw_ratio": numeric["neck_jaw_ratio"],
    }
    violations = []
    for name, value in measured.items():
        minimum, maximum = limits[name]
        if not minimum <= value <= maximum:
            violations.append(f"{name}={value:.4g} outside {minimum:g}-{maximum:g}")
    return violations


def validate_mandatory_clothes_authorization(path: str | Path, *, request_id: str,
                                             character_id: str, plan: dict[str, object]) -> dict[str, object]:
    """Validate narrow request-bound consent for one mandatory CLOTHES source."""
    authorization_path = Path(path).expanduser().resolve()
    request_root = authorization_path.parent
    if authorization_path.name != "MANDATORY_CLOTHES_AUTHORIZATION.json":
        raise StylePackError("Mandatory CLOTHES authorization must use the request-bound authorization record.")
    try:
        raw = authorization_path.read_bytes()
        record = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read mandatory CLOTHES authorization: {error}") from error
    if not isinstance(record, dict):
        raise StylePackError("Mandatory CLOTHES authorization must be a JSON object.")
    request_id = str(request_id).strip()
    character_id = str(character_id).strip().upper()
    expected = {"schema_version": 1, "request_id": request_id, "character_id": character_id,
        "generation_purpose": "SCENE", "stage_id": "SINGLE_PASS", "item_kind": "CLOTHING",
        "physical_role": "CLOTHES", "style_name": "LORANDESORE", "fidelity": 90,
        "aux_body_decision": "SELECTED", "reference_policy": "BODY_LIBRARY_ONLY",
        "source_reference_id": "REF_00217", "subject_label": "MALE",
        "authorization_quote": "Теперь сделай из этого персонажа + комплект одежды. Полный набор",
        "continuation_authorization_quote": "Утвердить показанную базу и продолжить комплект одежды",
        "tool_repair_permission_quote": "Разрешить узкое исправление и продолжить комплект",
        "unseen_clothing_author_approval_granted": False}
    if any(record.get(key) != value for key, value in expected.items()):
        raise StylePackError("Mandatory CLOTHES authorization does not match the exact SCENE/SINGLE_PASS request contract.")
    source = Path(str(record.get("clothes_reference_path", ""))).expanduser().resolve()
    source_hash = str(record.get("clothes_reference_sha256", "")).lower()
    item_plan = Path(str(record.get("item_plan_path", ""))).expanduser().resolve()
    item_hash = str(record.get("item_plan_sha256", "")).lower()
    assembly = Path(str(record.get("character_assembly_path", ""))).expanduser().resolve()
    assembly_hash = str(record.get("character_assembly_sha256", "")).lower()
    for label, file, digest in (("CLOTHES", source, source_hash), ("item plan", item_plan, item_hash),
                                ("approved assembly", assembly, assembly_hash)):
        if (not file.is_file() or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or hashlib.sha256(file.read_bytes()).hexdigest() != digest):
            raise StylePackError(f"Mandatory CLOTHES {label} path or hash is missing or stale.")
    if record.get("subject_label_sha256") != source_hash:
        raise StylePackError("Mandatory CLOTHES source subject label is not bound to the current source hash.")
    for file in (item_plan, Path(str(record.get("continuation_authorization_path", ""))).expanduser().resolve(),
                 Path(str(record.get("tool_repair_permission_path", ""))).expanduser().resolve()):
        if not file.is_relative_to(request_root):
            raise StylePackError("Mandatory CLOTHES authority and item-plan files must remain inside this active request folder.")
    try:
        item_record = json.loads(item_plan.read_text(encoding="utf-8"))
        assembly_path = Path(str(record.get("character_assembly_path", ""))).expanduser().resolve()
        continuation = json.loads(Path(str(record["continuation_authorization_path"])).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read mandatory CLOTHES item plan or continuation proof: {error}") from error
    accepted_base = continuation.get("approved_files", {}) if isinstance(continuation, dict) else {}
    accepted_assembly = accepted_base.get("05_CHARACTER_ASSEMBLY", {}) if isinstance(accepted_base, dict) else {}
    expected_base_stages = ("01_FACE_IDENTITY", "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY")
    if not isinstance(accepted_base, dict) or set(accepted_base) != set(expected_base_stages):
        raise StylePackError("Mandatory CLOTHES continuation proof must preserve all five ordered canonical identity outputs.")
    for stage in expected_base_stages:
        approved = accepted_base.get(stage)
        approved_path = Path(str(approved.get("path", ""))).expanduser().resolve() if isinstance(approved, dict) else Path()
        approved_hash = str(approved.get("sha256", "")).lower() if isinstance(approved, dict) else ""
        if (not approved_path.is_relative_to(request_root) or not approved_path.is_file()
                or not re.fullmatch(r"[0-9a-f]{64}", approved_hash)
                or hashlib.sha256(approved_path.read_bytes()).hexdigest() != approved_hash):
            raise StylePackError(f"Mandatory CLOTHES continuation has missing or stale accepted {stage} proof.")
    if (not isinstance(item_record, dict) or item_record.get("status") != "ITEM_PRODUCTION_PLAN"
            or item_record.get("request_id") != request_id or item_record.get("character_id") != character_id
            or item_record.get("item_kind") != "CLOTHING" or item_record.get("style_name") != "LORANDESORE"
            or Path(str(item_record.get("character_assembly", ""))).expanduser().resolve() != assembly_path
            or str(item_record.get("character_assembly_sha256", "")).lower() != assembly_hash
            or not isinstance(continuation, dict)
            or continuation.get("request_id") != request_id
            or continuation.get("approval_quote") != record.get("continuation_authorization_quote")
            or not Path(str(accepted_assembly.get("path", ""))).expanduser().is_file()
            or str(accepted_assembly.get("sha256", "")).lower() != assembly_hash):
        raise StylePackError("Mandatory CLOTHES item plan or accepted assembly proof differs from this request.")
    for prefix in ("continuation_authorization", "tool_repair_permission"):
        proof = Path(str(record.get(prefix + "_path", ""))).expanduser().resolve()
        digest = str(record.get(prefix + "_sha256", "")).lower()
        if not proof.is_file() or not re.fullmatch(r"[0-9a-f]{64}", digest) or hashlib.sha256(proof.read_bytes()).hexdigest() != digest:
            raise StylePackError(f"Mandatory CLOTHES {prefix} proof is missing or stale.")
        try:
            proof_record = json.loads(proof.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StylePackError(f"Cannot read mandatory CLOTHES {prefix} proof: {error}") from error
        proof_quote = (proof_record.get("approval_quote") if isinstance(proof_record, dict) and prefix == "continuation_authorization"
                       else proof_record.get("quote") if isinstance(proof_record, dict) else None)
        if not isinstance(proof_record, dict) or proof_quote != record.get(prefix + "_quote"):
            raise StylePackError(f"Mandatory CLOTHES {prefix} quote differs from its bound proof.")
        if prefix == "continuation_authorization" and not str(proof_record.get("approval_source", "")).strip():
            raise StylePackError("Mandatory CLOTHES continuation lacks native author-approval provenance.")
        if prefix == "tool_repair_permission" and (not str(proof_record.get("source", "")).strip()
                or "mandatoryoriginalCLOTHES" not in str(proof_record.get("scope", ""))):
            raise StylePackError("Mandatory CLOTHES tool-repair permission does not bind this narrow repair scope.")
    if (str(plan.get("request_id", "")) != request_id
            or str(plan.get("character_id", "")).upper() != character_id
            or str(plan.get("generation_purpose", "")).upper() != "SCENE"
            or str(plan.get("style_name", "")).upper() != "LORANDESORE"):
        raise StylePackError("Mandatory CLOTHES consent is bound to a different request, character, purpose, or style.")
    return {"path": str(authorization_path), "sha256": hashlib.sha256(raw).hexdigest(),
            "source_path": str(source), "source_sha256": source_hash, "reference_id": "REF_00217",
            "role": "CLOTHES", "subject_label": "MALE", "item_plan_path": str(item_plan),
            "item_plan_sha256": item_hash, "assembly_path": str(assembly), "assembly_sha256": assembly_hash}


def validate_construction_dimension_guide_authorization(
    authorization_path: Path,
    *,
    request_root: Path,
    request_id: str,
    character_id: str,
    stage_id: str,
) -> dict[str, object]:
    """Validate the narrowly authorized, hash-bound SIDE dimension guide."""
    authorization_path = authorization_path.resolve()
    request_root = request_root.resolve()
    if not authorization_path.is_file() or not authorization_path.is_relative_to(request_root):
        raise StylePackError("Construction dimension guide authorization must be inside the active request folder.")
    try:
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read construction dimension guide authorization: {error}") from error
    if not isinstance(authorization, dict) or authorization.get("schema_version") != 1:
        raise StylePackError("Construction dimension guide authorization must use schema_version=1.")
    if (authorization.get("request_id") != request_id or authorization.get("character_id") != character_id
            or authorization.get("stage_id") != stage_id or request_id != authorization_path.parent.name
            or character_id != "NEW" or stage_id != "03_PHYSIQUE_SIDE"):
        raise StylePackError("Construction dimension guide authorization is bound to a different request, character, or stage.")
    if (authorization.get("source_kind") != "DETERMINISTIC_DIMENSION_ONLY_NO_SUBJECT"
            or authorization.get("physical_role") != "COMPOSITION"
            or authorization.get("subject_label") != "NO_SUBJECT"):
        raise StylePackError("Construction dimension guide must be a NO_SUBJECT COMPOSITION source.")
    guide_path = Path(str(authorization.get("guide_path", ""))).expanduser().resolve()
    registered_path = Path(str(authorization.get("registered_reference_path", ""))).expanduser().resolve()
    if not guide_path.is_file() or guide_path != registered_path:
        raise StylePackError("Construction dimension guide must match the exact registered reference path.")
    guide_hash = hashlib.sha256(guide_path.read_bytes()).hexdigest()
    expected_guide_hash = str(authorization.get("guide_sha256", "")).lower()
    if (not re.fullmatch(r"[0-9a-f]{64}", expected_guide_hash)
            or guide_hash != expected_guide_hash
            or str(authorization.get("registered_reference_sha256", "")).lower() != guide_hash
            or str(authorization.get("subject_label_sha256", "")).lower() != guide_hash):
        raise StylePackError("Construction dimension guide or NO_SUBJECT label hash is missing or stale.")

    def request_file(path_key: str, hash_key: str, label: str) -> tuple[Path, str]:
        path = Path(str(authorization.get(path_key, ""))).expanduser().resolve()
        digest = str(authorization.get(hash_key, "")).lower()
        if not path.is_file() or not path.is_relative_to(request_root):
            raise StylePackError(f"Construction dimension guide {label} must be in the active request folder.")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or actual != digest:
            raise StylePackError(f"Construction dimension guide {label} hash is missing or stale.")
        return path, digest

    geometry_path, geometry_hash = request_file("geometry_path", "geometry_sha256", "geometry source")
    approval_path, approval_hash = request_file(
        "approved_geometry_evidence", "approved_geometry_evidence_sha256", "author approval"
    )
    try:
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Construction dimension guide geometry or approval is invalid JSON: {error}") from error
    dimensions = geometry.get("design_dimensions") if isinstance(geometry, dict) else None
    if (not isinstance(geometry, dict) or geometry.get("request_id") != request_id
            or geometry.get("character_id") != character_id or not isinstance(dimensions, dict)
            or dimensions.get("total_height") != authorization.get("design_height")
            or dimensions.get("head_length") != authorization.get("design_head")
            or dimensions.get("side_foot_length") != authorization.get("design_foot")
            or authorization.get("design_height") != 1560 or authorization.get("design_head") != 200
            or authorization.get("design_foot") != 190 or authorization.get("profile_direction") != "LEFT"):
        raise StylePackError("Construction dimension guide values do not match the approved 1560/200/190 SIDE geometry.")
    if (not isinstance(approval, dict) or approval.get("schema_version") != 1
            or approval.get("request_id") != request_id or approval.get("character_id") != character_id
            or approval.get("scope") != "CONSTRUCTION_GEOMETRY_ONLY"
            or approval.get("approves_previous_illustration") is not False
            or approval.get("numeric_visible_landmarks_remain") != "NOT_CHECKED"
            or Path(str(approval.get("geometry_path", ""))).resolve() != geometry_path
            or str(approval.get("geometry_sha256", "")).lower() != geometry_hash
            or not isinstance(approval.get("approval_quote"), str)
            or not approval["approval_quote"].strip()
            or not str(authorization.get("authorization_quote", "")).strip()
            or str(authorization.get("authorization_quote", "")) != "Создать и приложить размерную схему SIDE"
            or not isinstance(authorization.get("authorization_source"), str)
            or not authorization["authorization_source"].strip()
            or not str(authorization.get("subject_label_evidence", "")).strip()
            or authorization.get("previous_attempt_pixels_used") is not False
            or not re.fullmatch(r"REF_\d{5}", str(authorization.get("reference_id", "")))
            or not {"identity", "style", "clothing", "tattoos", "palette", "rendering", "text_in_final_art"}.issubset(
                {str(item).strip().lower() for item in authorization.get("forbidden_influence", [])}
            )):
        raise StylePackError("Construction dimension guide lacks the exact current author consent or bound geometry approval.")
    consent_scope = str(authorization.get("authorization_scope", ""))
    if any(required not in consent_scope for required in (
        "current90%LORANDESORE", "selectedBODY_REFERENCE_LIBRARY", "mandatorySTYLE/FACE/FRONT retained",
    )):
        raise StylePackError("Construction dimension guide consent does not preserve the selected style, body library, and mandatory identity references.")
    return {
        "authorization_path": str(authorization_path),
        "authorization_sha256": hashlib.sha256(authorization_path.read_bytes()).hexdigest(),
        "request_id": request_id,
        "character_id": character_id,
        "stage_id": stage_id,
        "physical_role": "COMPOSITION",
        "subject_label": "NO_SUBJECT",
        "subject_label_sha256": guide_hash,
        "guide_path": str(guide_path),
        "guide_sha256": guide_hash,
        "geometry_path": str(geometry_path),
        "geometry_sha256": geometry_hash,
        "approved_geometry_evidence": str(approval_path),
        "approved_geometry_evidence_sha256": approval_hash,
        "reference_id": str(authorization.get("reference_id", "")),
        "authorization_quote": str(authorization["authorization_quote"]),
        "authorization_scope": str(authorization.get("authorization_scope", "")),
    }


def validate_construction_geometry_receipt(
    receipt_path: Path,
    *,
    request_root: Path,
    request_id: str,
    character_id: str,
    output: Path,
    stage_id: str,
    plan: dict[str, object],
) -> dict[str, object]:
    """Replay the complete file/hash/approval contract before an alternate QA gate is trusted."""
    request_root = request_root.resolve()
    eligible_views = {
        "02_PHYSIQUE_FRONT": "FRONT", "03_PHYSIQUE_SIDE": "SIDE",
        "04_PHYSIQUE_BACK": "BACK", "05_CHARACTER_ASSEMBLY": "ASSEMBLY",
    }
    body_contract = plan.get("character_base_contract")
    if (plan.get("generation_purpose") != "CHARACTER_BASE" or not isinstance(body_contract, dict)
            or body_contract.get("age_group") != "ADULT" or stage_id not in eligible_views):
        raise StylePackError("Construction geometry receipt is restricted to canonical adult CHARACTER_BASE body stages.")
    receipt_path = receipt_path if receipt_path.is_absolute() else request_root / receipt_path
    receipt_path = receipt_path.resolve()

    def local_file(raw: object, label: str) -> tuple[Path, str]:
        if not isinstance(raw, dict):
            raise StylePackError(f"Construction geometry {label} binding is missing.")
        path_value = Path(str(raw.get("path", "")))
        candidate = path_value if path_value.is_absolute() else request_root / path_value
        candidate = candidate.resolve()
        digest = str(raw.get("sha256", "")).lower()
        if not candidate.is_relative_to(request_root) or not candidate.is_file():
            raise StylePackError(f"Construction geometry {label} must be a file inside the current request folder.")
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or actual != digest:
            raise StylePackError(f"Construction geometry {label} hash is missing or stale.")
        return candidate, digest

    if not receipt_path.is_relative_to(request_root) or not receipt_path.is_file():
        raise StylePackError("Construction geometry receipt must be inside the current request folder.")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read construction geometry receipt: {error}") from error
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        raise StylePackError("Construction geometry receipt must be a JSON object with schema_version=1.")
    expected_view = eligible_views[stage_id]
    expected_fields = {
        "request_id": request_id, "character_id": character_id, "stage_id": stage_id,
        "view": expected_view, "method_id": CONSTRUCTION_GEOMETRY_METHOD_ID,
        "method_version": CONSTRUCTION_GEOMETRY_VERSION,
        "numeric_source": CONSTRUCTION_GEOMETRY_NUMERIC_SOURCE,
    }
    if any(receipt.get(key) != value for key, value in expected_fields.items()):
        raise StylePackError("Construction geometry receipt request, character, stage, view, or method binding mismatch.")
    output_digest = hashlib.sha256(output.read_bytes()).hexdigest()
    if str(receipt.get("output_sha256", "")).lower() != output_digest:
        raise StylePackError("Construction geometry receipt is not bound to this exact output image SHA256.")
    diagram_path, diagram_hash = local_file(receipt.get("diagram"), "construction diagram")
    geometry_path, geometry_hash = local_file(receipt.get("geometry_source"), "geometry source")
    approval_path, approval_hash = local_file(receipt.get("approval_evidence"), "author approval evidence")
    try:
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Construction geometry source or author approval is invalid JSON: {error}") from error
    if not isinstance(geometry, dict) or geometry.get("request_id") != request_id:
        raise StylePackError("Construction geometry source does not bind the active request.")
    if not isinstance(approval, dict) or approval.get("schema_version") != 1:
        raise StylePackError("Separate author approval evidence must be a JSON object with schema_version=1.")
    quote = approval.get("approval_quote")
    if (approval.get("scope") != "CONSTRUCTION_GEOMETRY_ONLY"
            or approval.get("approves_previous_illustration") is not False
            or approval.get("numeric_visible_landmarks_remain") != "NOT_CHECKED"
            or not str(approval.get("approval_source", "")).strip()
            or not isinstance(quote, str) or len(quote.strip()) < 8
            or approval.get("request_id") != request_id
            or approval.get("character_id") != character_id
            or Path(str(approval.get("diagram_path", ""))).resolve() != diagram_path
            or Path(str(approval.get("geometry_path", ""))).resolve() != geometry_path
            or str(approval.get("diagram_sha256", "")).lower() != diagram_hash
            or str(approval.get("geometry_sha256", "")).lower() != geometry_hash):
        raise StylePackError("Separate author approval must bind both diagram and geometry hashes, request, character, and exact quote.")
    values = geometry.get("values")
    if not isinstance(values, dict):
        design = geometry.get("design_measurements")
        try:
            hip_knee = float(design["hip_knee"])
            if not isinstance(design, dict) or hip_knee <= 0:
                raise ValueError
            values = {
                "head_units": design["head_units"],
                "pubic_height_fraction": design["pubic_height_fraction"],
                "lower_to_upper_leg_ratio": float(design["knee_ankle"]) / hip_knee,
                "ankle_width_head_ratio": design["ankle_width"],
                "foot_length_head_ratio": design["foot_length"],
                "neck_head_ratio": design["neck_head_ratio"],
                "neck_jaw_ratio": design["neck_jaw_ratio"],
            }
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
            raise StylePackError("Geometry source contains missing or invalid design dimensions.") from error
    if receipt.get("geometry_values") != values:
        raise StylePackError("Construction geometry receipt values differ from its hash-bound geometry source.")
    violations = construction_geometry_violations(values, plan)
    if violations:
        raise StylePackError("Construction geometry values exceed the unchanged anthropometric limits: " + "; ".join(violations))
    consistency = receipt.get("image_scheme_consistency")
    evidence_text = str(consistency.get("evidence", "")).strip().casefold() if isinstance(consistency, dict) else ""
    if (not isinstance(consistency, dict) or consistency.get("status") != "PASS"
            or not str(consistency.get("reviewer", "")).strip() or len(evidence_text) < 40
            or not any(term in evidence_text for term in ("image", "render", "output", "изображ", "кадр", "силуэт"))
            or not any(term in evidence_text for term in ("scheme", "geometry", "construction", "схем", "геометр"))
            or not any(term in evidence_text for term in ("compared", "compare", "comparison", "сопостав", "сравн"))):
        raise StylePackError("Image-to-scheme consistency requires PASS, an identified reviewer, and substantive evidence.")
    return {
        "schema_version": 1, "gate": "AUTHOR_CONFIRMED_CONSTRUCTION_GEOMETRY",
        "request_id": request_id, "character_id": character_id,
        "output_sha256": output_digest, "stage_id": stage_id, "view": expected_view,
        "method_id": CONSTRUCTION_GEOMETRY_METHOD_ID, "method_version": CONSTRUCTION_GEOMETRY_VERSION,
        "numeric_source": CONSTRUCTION_GEOMETRY_NUMERIC_SOURCE,
        "receipt_path": str(receipt_path), "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "approval_evidence": {"path": str(approval_path), "sha256": approval_hash, "approval_quote": quote},
        "diagram": {"path": str(diagram_path), "sha256": diagram_hash},
        "geometry_source": {"path": str(geometry_path), "sha256": geometry_hash},
        "geometry_values": values, "metric_validation": "PASS",
        "image_scheme_consistency": consistency,
    }



def normalize_aspect_ratio(value: str) -> str:
    return value.strip().lower().replace("x", ":").replace("к", ":")



def build_canvas_contract(args: argparse.Namespace) -> dict[str, object]:
    orientation = args.orientation.upper()
    default_ratio = DEFAULT_ASPECT_BY_ORIENTATION[orientation]
    ratio = normalize_aspect_ratio(args.aspect_ratio) if args.aspect_ratio else default_ratio
    if not re.fullmatch(r"\d+(?::\d+)", ratio):
        raise StylePackError("Aspect ratio must use W:H, for example 9:16 or 16:9.")
    width, height = (int(part) for part in ratio.split(":", 1))
    if width <= 0 or height <= 0:
        raise StylePackError("Aspect ratio dimensions must be positive.")
    actual_orientation = "LANDSCAPE" if width > height else "PORTRAIT" if height > width else "SQUARE"
    nonstandard = ratio not in STANDARD_ASPECT_RATIOS or actual_orientation != orientation
    if nonstandard and not args.user_approved_nonstandard_aspect:
        raise StylePackError(
            f"Nonstandard or orientation-mismatched aspect ratio {ratio} requires direct user approval via "
            "--user-approved-nonstandard-aspect. Defaults are 9:16 portrait and 16:9 landscape."
        )
    character_free_scene = (
        str(getattr(args, "generation_purpose", "")).upper() == "SCENE"
        and str(getattr(args, "character_id", "")).upper() == "NONE"
    )
    if character_free_scene:
        return {
            "orientation": orientation,
            "aspect_ratio": ratio,
            "used_default_ratio": not bool(args.aspect_ratio),
            "nonstandard_user_approved": bool(args.user_approved_nonstandard_aspect),
            "framing": "SCENE_COMPOSITION",
            "target_pose_family": "NOT_APPLICABLE",
            "full_figure": False,
            "body_visible": False,
            "subject_height_percent": None,
            "required_margins": "Preserve the crop/copy safe areas declared by scene_contract.",
            "vertical_stretch_forbidden": False,
        }
    full_figure = args.framing in {"FULL_BODY", "THREE_QUARTER"}
    return {
        "orientation": orientation,
        "aspect_ratio": ratio,
        "used_default_ratio": not bool(args.aspect_ratio),
        "nonstandard_user_approved": bool(args.user_approved_nonstandard_aspect),
        "framing": args.framing,
        "target_pose_family": args.target_pose_family,
        "full_figure": full_figure,
        "body_visible": args.framing in {"FULL_BODY", "THREE_QUARTER", "HALF_BODY"},
        "subject_height_percent": [72, 88] if args.framing == "FULL_BODY" else [60, 88],
        "required_margins": "Visible headroom and floor/foot margin; do not stretch the figure to fill the canvas.",
        "vertical_stretch_forbidden": True,
    }



def build_scene_contract(
    args: argparse.Namespace,
    purpose: str,
    character_id: str,
) -> dict[str, object]:
    """Resolve scene subject/use while keeping character identity opt-in."""

    if purpose != "SCENE":
        return {"applicable": False}
    if not re.fullmatch(r"CHAR_\d+|NONE", character_id):
        raise StylePackError("SCENE requires --character-id NONE or an approved CHAR_NNN identifier.")

    scene_kind = args.scene_kind.upper()
    output_use = args.scene_output_use.upper()
    text_safe_zone = args.text_safe_zone.upper()
    if scene_kind not in SCENE_KINDS:
        raise StylePackError("SCENE requires --scene-kind LOCATION, PHENOMENON, ARTIFACT, or MIXED.")
    if output_use not in SCENE_OUTPUT_USES:
        raise StylePackError("SCENE requires --scene-output-use GENERAL_ART, WALLPAPER, or PROMO_POSTER.")
    if output_use == "PROMO_POSTER" and text_safe_zone == "NONE":
        raise StylePackError("PROMO_POSTER requires a non-NONE --text-safe-zone for later deterministic typography.")
    if output_use != "PROMO_POSTER" and text_safe_zone != "NONE":
        raise StylePackError("--text-safe-zone is reserved for PROMO_POSTER scenes.")

    has_character = character_id != "NONE"
    if has_character:
        return {
            "applicable": True,
            "scene_kind": scene_kind,
            "output_use": output_use,
            "has_character": True,
            "character_policy": "EXPLICIT_APPROVED_CHARACTER",
            "subject_source": "REFERENCE_PLUS_PROMPT" if getattr(args, "subject_reference", "") else "PROMPT_ONLY",
            "text_safe_zone": text_safe_zone,
            "typography_policy": (
                "RESERVE_COPY_SAFE_AREA; ADD_EXACT_COPY_DETERMINISTICALLY_AFTER_ART_GENERATION"
                if output_use == "PROMO_POSTER"
                else "NO_TEXT_OR_WATERMARK"
            ),
            "crop_policy": (
                "KEEP_FOCAL_SUBJECT_AND_HORIZON_INSIDE_CENTER_CROP_SAFE_AREA_FOR_FILL_FIT_SPAN"
                if output_use == "WALLPAPER"
                else "KEEP_PRIMARY_SUBJECT_AND_COPY_SAFE_AREA_CLEAR_OF_TRIM_EDGES"
                if output_use == "PROMO_POSTER"
                else "PRESERVE_CLEAR_FOCAL_HIERARCHY"
            ),
        }

    forbidden = {
        "character assembly": getattr(args, "character_assembly", ""),
        "primary face": getattr(args, "primary_face", ""),
        "supporting face": getattr(args, "supporting_face", ""),
        "expression reference": getattr(args, "expression_reference", ""),
        "body": getattr(args, "body_reference", ""),
        "pose": getattr(args, "pose_reference", ""),
        "clothes": getattr(args, "clothes_reference", ""),
        "accessory": getattr(args, "accessory_reference", []),
        "face variant": getattr(args, "face_variant_reference", []),
        "body variant": getattr(args, "body_variant_reference", []),
        # Anonymous scene subjects may use the selected library for gesture,
        # while identity and permanent body-build references remain forbidden.
        # The manager still resolves registry IDs, hashes, subject compatibility
        # and full-resolution source reviews before accepting these attachments.
        "auxiliary body": [
            value for value in getattr(args, "aux_body", [])
            if not re.fullmatch(r"BR_\d+=STAGING_ONLY", str(value).strip(), re.IGNORECASE)
        ],
        "coverage front": getattr(args, "coverage_front_reference", ""),
        "coverage side": getattr(args, "coverage_side_reference", ""),
        "coverage back": getattr(args, "coverage_back_reference", ""),
    }
    present = [name for name, value in forbidden.items() if value]
    if present:
        raise StylePackError(
            "SCENE with --character-id NONE forbids character reference families and CHARACTER_BASE-only coverage assets. "
            "Use an approved CHAR_NNN when character identity, body, pose, or clothes must be attached. "
            "Remove: " + ", ".join(present)
        )
    if not getattr(args, "subject_reference", "") and not getattr(args, "scene_subject_from_prompt", False):
        raise StylePackError(
            "Character-free SCENE requires --subject-reference or explicit --scene-subject-from-prompt."
        )

    return {
        "applicable": True,
        "scene_kind": scene_kind,
        "output_use": output_use,
        "has_character": False,
        "character_policy": "ANONYMOUS_SUBJECT; NO_CHARACTER_REFERENCE_FAMILIES; NO_APPROVED_IDENTITY_LOCK",
        "subject_source": "REFERENCE_PLUS_PROMPT" if getattr(args, "subject_reference", "") else "PROMPT_ONLY",
        "text_safe_zone": text_safe_zone,
        "typography_policy": (
            "RESERVE_COPY_SAFE_AREA; ADD_EXACT_COPY_DETERMINISTICALLY_AFTER_ART_GENERATION"
            if output_use == "PROMO_POSTER"
            else "NO_TEXT_OR_WATERMARK"
        ),
        "crop_policy": (
            "KEEP_FOCAL_SUBJECT_AND_HORIZON_INSIDE_CENTER_CROP_SAFE_AREA_FOR_FILL_FIT_SPAN"
            if output_use == "WALLPAPER"
            else "KEEP_PRIMARY_SUBJECT_AND_COPY_SAFE_AREA_CLEAR_OF_TRIM_EDGES"
            if output_use == "PROMO_POSTER"
            else "PRESERVE_CLEAR_FOCAL_HIERARCHY"
        ),
    }



def build_scene_prompt_source_contract(
    purpose: str,
    character_id: str,
    selected_local_roles: dict[str, str],
) -> dict[str, dict[str, str]]:
    """Declare optional scene roles sourced from the exact executable prompt."""
    if purpose.upper() != "SCENE" or not re.fullmatch(r"CHAR_\d+", character_id.upper()):
        return {}
    return {
        role: {
            "source": "EXACT_EXECUTABLE_PROMPT",
            "evidence_required": "USER_SPECIFIED_SCENE_TEXT",
        }
        for role in ("POSE", "CLOTHES", "ACCESSORY", "LIGHTING", "BACKGROUND", "COMPOSITION")
        if not str(selected_local_roles.get(role, "")).strip()
    }



def parse_head_height_contract(value: str, required: bool, child_character: bool = False) -> dict[str, object]:
    text = value.strip().upper()
    if text == "CHILD_AGE_APPROPRIATE":
        if not child_character:
            raise StylePackError("CHILD_AGE_APPROPRIATE body height is available only for an explicitly identified CHILD character.")
        return {
            "mode": "CHILD_AGE_APPROPRIATE",
            "source": "PROMPT_BODY_SPEC",
            "numeric_head_count": None,
            "adult_anthropometric_bounds": False,
        }
    if text == "SOURCE_LOCK":
        if required:
            raise StylePackError(
                "A new full-body character requires an explicit --body-height-heads range, for example 6.5-7.0."
            )
        return {"mode": "SOURCE_LOCK"}
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[-:]\s*(\d+(?:\.\d+)?)\s*", value)
    if not match:
        raise StylePackError("--body-height-heads must be SOURCE_LOCK, CHILD_AGE_APPROPRIATE for CHILD, or a numeric range such as 6.5-7.0.")
    minimum, maximum = float(match.group(1)), float(match.group(2))
    if not (4.0 <= minimum <= maximum <= 10.0):
        raise StylePackError("Body height in heads must be an ordered range within 4.0-10.0.")
    return {"mode": "EXPLICIT_RANGE", "minimum": minimum, "maximum": maximum}



def build_body_proportion_contract(
    args: argparse.Namespace,
    selected: dict[str, object],
    auxiliary: list[dict[str, object]],
    is_new_character: bool,
    technical_test: bool = False,
) -> dict[str, object]:
    dominant = args.dominant_body_source.upper()
    target_family = args.target_pose_family.upper()
    coverage = args.body_source_coverage.upper()
    source_family = args.body_source_pose_family.upper()
    full_body_target = args.framing == "FULL_BODY"
    body_targets = [row for row in auxiliary if row["mode"] == "BODY_BUILD_TARGET"]

    if not is_new_character and not technical_test and not args.allow_body_identity_change:
        if dominant != "CHARACTER_BODY":
            raise StylePackError("An existing character with locked anatomy must use CHARACTER_BODY as the dominant body source.")
        if body_targets:
            raise StylePackError("A locked existing character cannot use an auxiliary BODY_BUILD_TARGET.")
    elif dominant == "CHARACTER_BODY" and is_new_character:
        raise StylePackError("A NEW character cannot use CHARACTER_BODY as its dominant source.")

    dominant_aux: dict[str, object] | None = None
    if dominant.startswith("BR_"):
        matches = [row for row in body_targets if row["ref_id"] == dominant]
        if len(matches) != 1:
            raise StylePackError(
                f"Dominant body source {dominant} must be connected exactly once as {dominant}=BODY_BUILD_TARGET."
            )
        dominant_aux = matches[0]
        if len(body_targets) != 1:
            raise StylePackError("Exactly one auxiliary BODY_BUILD_TARGET may define permanent proportions.")
        manifest_family = str(dominant_aux.get("primary_family", "")).upper()
        manifest_framing = str(dominant_aux.get("framing", "")).upper()
        if source_family != manifest_family:
            raise StylePackError(
                f"Declared body source pose family {source_family} does not match {dominant} metadata {manifest_family}."
            )
        if full_body_target and manifest_framing != "FULL":
            raise StylePackError(
                f"{dominant} has framing {manifest_framing}, so it cannot control complete full-body proportions."
            )
        if "AUX_BODY_BUILD" in set(dominant_aux.get("not_for_roles", [])):
            raise StylePackError(f"{dominant} is explicitly forbidden as a permanent body-build source.")
    elif dominant == "STYLE_BODY":
        if body_targets:
            raise StylePackError(
                "STYLE_BODY is dominant, but an auxiliary BODY_BUILD_TARGET is also connected. "
                "Change the auxiliary reference to STAGING_ONLY or make its BR id dominant."
            )
        if "body" not in selected:
            raise StylePackError("STYLE_BODY requires a selected local BODY reference.")
    elif dominant == "CHARACTER_BODY":
        if body_targets and not args.allow_body_identity_change:
            raise StylePackError("CHARACTER_BODY cannot compete with BODY_BUILD_TARGET without an explicit anatomy change.")
    elif dominant == "PROMPT_BODY_SPEC":
        if not is_new_character or technical_test:
            raise StylePackError("PROMPT_BODY_SPEC is reserved for a new prompt-led CHARACTER_BASE.")
        if not getattr(args, "prompt_only_physique", False):
            raise StylePackError("PROMPT_BODY_SPEC requires --prompt-only-physique.")
        if body_targets or "body" in selected or "pose" in selected:
            raise StylePackError("PROMPT_BODY_SPEC cannot compete with visual BODY, POSE, or BODY_BUILD_TARGET inputs.")
        if args.aux_body_decision.upper() == "SELECTED" and not args.aux_body_selection_note.strip():
            raise StylePackError(
                "PROMPT_BODY_SPEC with BODY_REFERENCE_LIBRARY selected requires an explicit note explaining why no safe candidate is attached."
            )
    else:
        raise StylePackError("--dominant-body-source must be PROMPT_BODY_SPEC, STYLE_BODY, CHARACTER_BODY, or a connected BR_NNNN.")

    if full_body_target:
        if coverage != "FULL_BODY":
            raise StylePackError(
                f"Full-body output requires a FULL_BODY dominant source; {coverage} cannot define leg-to-torso length."
            )
        if source_family != target_family and dominant not in {"CHARACTER_BODY", "PROMPT_BODY_SPEC"}:
            raise StylePackError(
                f"Full-body {target_family} output cannot take permanent proportions from a {source_family} source. "
                "Use a same-family full-body source; seated or lying references may only support staging/torso details."
            )
        if not args.body_silhouette_notes.strip():
            raise StylePackError(
                "Full-body output requires --body-silhouette-notes covering shoulders, torso, waist, hips, thighs, and leg-to-torso ratio."
            )
    is_child_character = str(getattr(args, "character_age_group", "")).upper() == "CHILD"
    head_contract = parse_head_height_contract(
        args.body_height_heads,
        required=is_new_character and full_body_target,
        child_character=is_child_character,
    )
    if is_child_character and dominant == "PROMPT_BODY_SPEC" and head_contract["mode"] != "CHILD_AGE_APPROPRIATE":
        raise StylePackError(
            "Prompt-led CHILD physique requires --body-height-heads CHILD_AGE_APPROPRIATE; do not use adult numeric ranges or claim a missing source lock."
        )
    if head_contract["mode"] == "CHILD_AGE_APPROPRIATE" and dominant != "PROMPT_BODY_SPEC":
        raise StylePackError("CHILD_AGE_APPROPRIATE body height requires PROMPT_BODY_SPEC as the dominant source.")
    child_shape_lock = [
        "age_appropriate_head_to_body_scale",
        "shoulder_to_torso_silhouette",
        "waist_and_hip_silhouette",
        "thigh_and_leg_to_torso_silhouette",
        "consistent_proportions_across_views",
    ]
    return {
        "dominant_source": dominant,
        "single_dominant_source": True,
        "specification_type": "TEXT" if dominant == "PROMPT_BODY_SPEC" else "VISUAL_REFERENCE",
        "source_coverage": coverage,
        "source_pose_family": source_family,
        "target_pose_family": target_family,
        "height_in_heads": head_contract,
        "user_approved_nonstandard_proportions": bool(
            getattr(args, "user_approved_nonstandard_proportions", False)
        ),
        "silhouette_notes": args.body_silhouette_notes,
        "locked_measurements": child_shape_lock if is_child_character else [
            "shoulder_width", "bust_volume", "ribcage_width", "waist_width", "hip_width",
            "glute_volume", "thigh_volume", "leg_to_torso_ratio", "overall_head_count",
        ],
        "child_proportion_policy": (
            "AGE_APPROPRIATE_CHILD_GEOMETRY_NO_ADULT_NUMERIC_THRESHOLDS"
            if is_child_character else None
        ),
        "pose_may_not_change_permanent_proportions": True,
        "approved_character_body_may_use_separate_pose_staging": dominant == "CHARACTER_BODY",
    }



def parse_limb_qa_evidence(evidence: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in evidence.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.strip() and value.strip():
            parsed[key.strip().lower()] = value.strip()
    return parsed



def anthropometric_qa_violations(
    evidence: str,
    plan: dict[str, object] | None = None,
) -> list[str]:
    values = parse_limb_qa_evidence(evidence)
    landmark_requirements = {
        "hip_landmark": "FEMORAL_HEAD_CENTER",
        "knee_landmark": "KNEE_JOINT_CENTER",
        "ankle_landmark": "TALOCRURAL_JOINT_CENTER",
    }
    for name, expected in landmark_requirements.items():
        actual = values.get(name, "").upper()
        if actual != expected:
            raise StylePackError(
                f"Schema-4 anthropometric QA requires {name}={expected}; "
                "pubic/crotch points and heel/toe endpoints cannot substitute for joint centers."
            )
    crown_landmark = values.get("crown_landmark", "").upper()
    if crown_landmark not in {"CRANIAL_VERTEX", "CRANIAL_VERTEX_ESTIMATED"}:
        raise StylePackError(
            "Schema-4 anthropometric QA requires crown_landmark=CRANIAL_VERTEX|CRANIAL_VERTEX_ESTIMATED; "
            "the highest hair, hood, hat, or ornament pixel cannot substitute for the anatomical crown."
        )
    foot_pose = values.get("foot_pose", "").upper()
    if foot_pose not in {"FLAT", "TIPTOE", "PLANTAR_FLEXED", "DORSIFLEXED"}:
        raise StylePackError(
            "Schema-4 anthropometric QA requires foot_pose=FLAT|TIPTOE|PLANTAR_FLEXED|DORSIFLEXED."
        )
    projection_view = values.get("view", "").upper()
    if projection_view not in {"FRONT", "SIDE", "BACK", "ASSEMBLY"}:
        raise StylePackError("Schema-4 anthropometric QA requires view=FRONT|SIDE|BACK|ASSEMBLY.")
    foot_length_mode = values.get("foot_length_mode", "").upper()
    if foot_length_mode not in {"MEASURED", "FORESHORTENED_DEFERRED"}:
        raise StylePackError(
            "Schema-4 anthropometric QA requires foot_length_mode=MEASURED|FORESHORTENED_DEFERRED."
        )
    if foot_length_mode == "FORESHORTENED_DEFERRED" and projection_view not in {"FRONT", "BACK"}:
        raise StylePackError(
            "FORESHORTENED_DEFERRED foot length is allowed only for FRONT or BACK; SIDE must measure heel-to-toe length."
        )
    if values.get("heel_endpoint", "").upper() != "VISIBLE" or values.get("toe_endpoint", "").upper() != "VISIBLE":
        raise StylePackError(
            "Schema-4 anthropometric QA requires heel_endpoint=VISIBLE and toe_endpoint=VISIBLE."
        )
    numeric_names = (
        "head_units",
        "pubic_height_fraction",
        "hip_knee",
        "knee_ankle",
        "ankle_width",
        "neck_head_ratio",
        "neck_jaw_ratio",
        "landmark_confidence",
        "crown_confidence",
    )
    numeric: dict[str, float] = {}
    for name in numeric_names:
        try:
            numeric[name] = float(values[name])
        except (KeyError, TypeError, ValueError):
            raise StylePackError(
                f"Schema-4 anthropometric QA requires a numeric {name}= value; words such as matched are invalid."
            )
        if numeric[name] <= 0:
            raise StylePackError(f"Schema-4 anthropometric QA requires {name}= to be greater than zero.")
    if foot_length_mode == "MEASURED":
        try:
            numeric["foot_length"] = float(values["foot_length"])
        except (KeyError, TypeError, ValueError):
            raise StylePackError("Schema-4 measured foot length requires a numeric foot_length= value.")
        if numeric["foot_length"] <= 0:
            raise StylePackError("Schema-4 anthropometric QA requires foot_length= to be greater than zero.")
    if not 0.80 <= numeric["landmark_confidence"] <= 1.0:
        raise StylePackError(
            "Schema-4 anthropometric QA requires landmark_confidence=0.80-1.00. "
            "An uncertain joint estimate cannot produce PASS or an automatic proportion FAIL."
        )
    if not 0.80 <= numeric["crown_confidence"] <= 1.0:
        raise StylePackError(
            "Schema-4 anthropometric QA requires crown_confidence=0.80-1.00. "
            "Hair-volume uncertainty cannot produce a head-count PASS or automatic FAIL."
        )

    measured = {
        "head_units": numeric["head_units"],
        "pubic_height_fraction": numeric["pubic_height_fraction"],
        "lower_to_upper_leg_ratio": numeric["knee_ankle"] / numeric["hip_knee"],
        "ankle_width_head_ratio": numeric["ankle_width"],
        "neck_head_ratio": numeric["neck_head_ratio"],
        "neck_jaw_ratio": numeric["neck_jaw_ratio"],
    }
    if foot_length_mode == "MEASURED":
        measured["foot_length_head_ratio"] = numeric["foot_length"]
    limits = dict(ANTHROPOMETRIC_QA_LIMITS)
    body_contract = plan.get("body_proportion_contract", {}) if isinstance(plan, dict) else {}
    height_contract = body_contract.get("height_in_heads", {}) if isinstance(body_contract, dict) else {}
    if isinstance(height_contract, dict) and height_contract.get("mode") == "EXPLICIT_RANGE":
        try:
            contracted_minimum = float(height_contract["minimum"])
            contracted_maximum = float(height_contract["maximum"])
        except (KeyError, TypeError, ValueError):
            raise StylePackError("Schema-4 body height contract requires numeric minimum and maximum values.")
        if contracted_minimum <= 0 or contracted_maximum < contracted_minimum:
            raise StylePackError("Schema-4 body height contract has an invalid explicit range.")
        global_minimum, global_maximum = ANTHROPOMETRIC_QA_LIMITS["head_units"]
        limits["head_units"] = (
            max(global_minimum, contracted_minimum),
            min(global_maximum, contracted_maximum),
        )

    violations = []
    for name, value in measured.items():
        minimum, maximum = limits[name]
        if not minimum <= value <= maximum:
            violations.append(f"{name}={value:.4g} outside {minimum:g}-{maximum:g}")
    return violations

