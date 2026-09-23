"""Resolve a saved multi-stage attachment plan into concrete call slots.

Only QA-passed, request-local STAGING outputs may satisfy stage placeholders.
Targeted stage packs are deterministic technical references, never generated art.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


class GenerationCallContractError(RuntimeError):
    """The saved attachment plan cannot be safely resolved for a call."""


_STAGE_OUTPUT = re.compile(r"^<STAGE_OUTPUT:([^<>]+)>$")
_TARGETED_PACK = re.compile(r"^<TARGETED_STAGE_PACK:([^<>]+)>$")
_ANATOMY_SCOPE = {
    "01_FACE_IDENTITY": "FACE_IDENTITY_STAGE",
    "02_PHYSIQUE_FRONT": "PHYSIQUE_FRONT_STAGE",
    "03_PHYSIQUE_SIDE": "PHYSIQUE_SIDE_STAGE",
    "04_PHYSIQUE_BACK": "PHYSIQUE_BACK_STAGE",
}
_PANEL_SIZE = (640, 960)
_PADDING = 24
_GUTTER = 16


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _workflow(plan: dict[str, Any]) -> dict[str, Any]:
    workflow = plan.get("generation_workflow", plan)
    if not isinstance(workflow, dict):
        raise GenerationCallContractError("Saved plan has no generation_workflow object.")
    return workflow


def _validate_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    request_id = str(plan.get("request_id", "")).strip()
    if not request_id:
        raise GenerationCallContractError("Saved REFERENCE_PLAN has no top-level request_id.")
    workflow = _workflow(plan)
    if workflow.get("mode") == "SINGLE_PASS":
        slots = workflow.get("slots")
        if not isinstance(slots, list):
            raise GenerationCallContractError("SINGLE_PASS plan has no slots list.")
        limit = workflow.get("attachment_limit")
        if not isinstance(limit, int) or not 1 <= limit <= 5 or len(slots) > limit:
            raise GenerationCallContractError("SINGLE_PASS slots violate the 1-5 physical attachment limit.")
        return workflow, [], request_id
    if workflow.get("mode") != "MULTI_STAGE":
        raise GenerationCallContractError("Saved generation workflow mode must be SINGLE_PASS or MULTI_STAGE.")
    stages = workflow.get("stages")
    if not isinstance(stages, list) or not stages:
        raise GenerationCallContractError("MULTI_STAGE plan has no stages.")
    stage_ids = [str(stage.get("stage_id", "")) for stage in stages if isinstance(stage, dict)]
    if len(stage_ids) != len(stages) or any(not stage_id for stage_id in stage_ids) or len(set(stage_ids)) != len(stage_ids):
        raise GenerationCallContractError("MULTI_STAGE plan has missing or duplicate stage IDs.")
    return workflow, stages, request_id


def _validated_outputs(
    prior_stages: list[dict[str, Any]], stage_outputs: dict[str, dict[str, Any]], request_id: str
) -> dict[str, dict[str, Any]]:
    validated: dict[str, dict[str, Any]] = {}
    for stage in prior_stages:
        stage_id = str(stage["stage_id"])
        output = stage_outputs.get(stage_id)
        if not isinstance(output, dict):
            raise GenerationCallContractError(f"Required prior stage output is missing: {stage_id}.")
        if output.get("stage_id") != stage_id:
            raise GenerationCallContractError(f"Stage output identity mismatch for {stage_id}.")
        if output.get("request_id") != request_id:
            raise GenerationCallContractError(f"Stage output belongs to a different request: {stage_id}.")
        if output.get("status") != "STAGING" or output.get("qa_passed") is not True:
            raise GenerationCallContractError(f"Prior stage has not passed QA in STAGING: {stage_id}.")
        path = Path(str(output.get("path", ""))).resolve()
        expected_hash = str(output.get("sha256", "")).lower()
        if not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise GenerationCallContractError(f"Stage output path or SHA-256 is invalid: {stage_id}.")
        if _sha256(path) != expected_hash:
            raise GenerationCallContractError(f"Stage output bytes changed after QA: {stage_id}.")
        validated[stage_id] = {**output, "path": str(path), "sha256": expected_hash}
    return validated


def _placeholder_role(workflow: dict[str, Any], source_stage_id: str) -> str:
    for stage in workflow.get("stages", []):
        for slot in stage.get("slots", []):
            path = str(slot.get("path", ""))
            match = _STAGE_OUTPUT.fullmatch(path)
            if match and match.group(1) == source_stage_id:
                role = str(slot.get("stage_role", "")).strip()
                if role:
                    return role
    return _ANATOMY_SCOPE.get(source_stage_id, source_stage_id)


def _make_targeted_pack(
    source_ids: list[str],
    outputs: dict[str, dict[str, Any]],
    workflow: dict[str, Any],
    output_dir: Path,
    request_id: str,
) -> tuple[Path, Path, dict[str, Any]]:
    if not source_ids or len(set(source_ids)) != len(source_ids):
        raise GenerationCallContractError("Targeted stage pack requires a non-empty ordered unique source list.")
    missing = [source_id for source_id in source_ids if source_id not in outputs]
    if missing:
        raise GenerationCallContractError(f"Targeted pack sources are not validated prior stages: {', '.join(missing)}.")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_root = output_dir.resolve()
    source_records = [outputs[source_id] for source_id in source_ids]
    payload = {
        "schema_version": 1,
        "asset_type": "TARGETED_GENERATOR_COLLAGE",
        "request_id": request_id,
        "generator_safe": True,
        "scope": [_placeholder_role(workflow, source_id) for source_id in source_ids] + ["MULTIVIEW_CONSISTENCY"],
        "forbidden_transfer": ["NEW_FACE_IDENTITY", "NEW_STYLE", "NEW_WARDROBE", "NEW_BACKGROUND"],
        "sources": [
            {
                "role": _placeholder_role(workflow, source_id),
                "path": record["path"],
                "sha256": record["sha256"],
            }
            for source_id, record in zip(source_ids, source_records)
        ],
        "construction": "Deterministic side-by-side aspect-preserving resize; no source crop or in-place edits.",
    }
    identity = json.dumps(
        {"request_id": request_id, "sources": payload["sources"], "scope": payload["scope"], "forbidden_transfer": payload["forbidden_transfer"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    key = hashlib.sha256(identity).hexdigest()
    image_path = output_root / f"targeted-stage-pack-{key}.png"
    manifest_path = output_root / f"targeted-stage-pack-{key}.json"

    panels: list[Image.Image] = []
    for record in source_records:
        try:
            with Image.open(record["path"]) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                image.thumbnail(_PANEL_SIZE, Image.Resampling.LANCZOS)
                panels.append(image.copy())
        except Exception as exc:
            raise GenerationCallContractError(f"Cannot read targeted pack source image: {record['path']}.") from exc
    canvas_height = max(panel.height for panel in panels) + 2 * _PADDING
    canvas_width = len(panels) * _PANEL_SIZE[0] + (len(panels) - 1) * _GUTTER + 2 * _PADDING
    canvas = Image.new("RGB", (canvas_width, canvas_height), (245, 245, 245))
    for index, panel in enumerate(panels):
        x = _PADDING + index * (_PANEL_SIZE[0] + _GUTTER) + (_PANEL_SIZE[0] - panel.width) // 2
        y = _PADDING + (canvas_height - 2 * _PADDING - panel.height) // 2
        canvas.paste(panel, (x, y))
    payload["output"] = {
        "path": image_path.name,
        "dimensions": f"{canvas_width}x{canvas_height}",
    }
    import io

    encoded = io.BytesIO()
    canvas.save(encoded, format="PNG", optimize=False, compress_level=9)
    image_bytes = encoded.getvalue()
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    payload["output"]["sha256"] = image_hash
    manifest_bytes = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

    for path, expected in ((image_path, image_bytes), (manifest_path, manifest_bytes)):
        if path.exists() and path.read_bytes() != expected:
            raise GenerationCallContractError(f"Refusing to overwrite conflicting targeted pack artifact: {path}.")
    if output_root not in image_path.resolve().parents or output_root not in manifest_path.resolve().parents:
        raise GenerationCallContractError("Targeted pack output escaped the request output directory.")
    if not image_path.exists():
        image_path.write_bytes(image_bytes)
    if not manifest_path.exists():
        manifest_path.write_bytes(manifest_bytes)
    return image_path, manifest_path, payload


def _deduplicate(slots: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for slot in slots:
        digest = str(slot.get("sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise GenerationCallContractError("A resolved call slot has no concrete SHA-256.")
        if digest not in grouped:
            grouped[digest] = {**slot, "sha256": digest}
            grouped[digest]["active_roles"] = list(dict.fromkeys(slot.get("active_roles", [])))
        else:
            roles = grouped[digest].setdefault("active_roles", [])
            for role in slot.get("active_roles", []):
                if role not in roles:
                    roles.append(role)
    result = list(grouped.values())
    if not 1 <= limit <= 5 or len(result) > limit:
        raise GenerationCallContractError(f"Resolved call needs {len(result)} physical attachments; limit is {limit}.")
    for index, slot in enumerate(result, 1):
        slot["slot"] = index
        slot["active_roles"] = sorted(slot.get("active_roles", []))
        slot["physically_attach"] = True
    return result


def resolve_stage_slots(
    plan: dict[str, Any],
    stage_id: str,
    stage_outputs: dict[str, dict[str, Any]],
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Resolve one stage's placeholders against validated same-request outputs."""
    workflow, stages, request_id = _validate_plan(plan)
    if workflow.get("mode") == "SINGLE_PASS":
        return list(workflow["slots"])
    ordered_ids = [str(stage["stage_id"]) for stage in stages]
    if stage_id not in ordered_ids:
        raise GenerationCallContractError(f"Stage is not present in the saved plan: {stage_id}.")
    position = ordered_ids.index(stage_id)
    prior_stages = stages[:position]
    validated = _validated_outputs(prior_stages, stage_outputs, request_id)
    current_stage = stages[position]
    slots = current_stage.get("slots")
    if not isinstance(slots, list):
        raise GenerationCallContractError(f"Stage has no slots list: {stage_id}.")
    resolved: list[dict[str, Any]] = []
    for source in slots:
        slot = dict(source)
        path_value = str(slot.get("path", ""))
        stage_match = _STAGE_OUTPUT.fullmatch(path_value)
        pack_match = _TARGETED_PACK.fullmatch(path_value)
        if stage_match:
            source_id = stage_match.group(1)
            record = validated.get(source_id)
            if record is None:
                raise GenerationCallContractError(f"Stage placeholder does not refer to a validated prior output: {source_id}.")
            slot["path"] = record["path"]
            slot["sha256"] = record["sha256"]
            slot.pop("generated_stage_output", None)
            slot.pop("planned_targeted_pack", None)
            slot.pop("targeted_pack_sources", None)
            resolved.append(slot)
        elif pack_match:
            source_ids = [part for part in pack_match.group(1).split("+") if part]
            image_path, manifest_path, _ = _make_targeted_pack(source_ids, validated, workflow, Path(output_dir), request_id)
            slot["path"] = str(image_path)
            slot["sha256"] = _sha256(image_path)
            slot["manifest_path"] = str(manifest_path)
            slot.pop("generated_stage_output", None)
            slot.pop("planned_targeted_pack", None)
            slot.pop("targeted_pack_sources", None)
            resolved.append(slot)
        else:
            resolved.append(slot)
    limit = current_stage.get("attachment_limit", workflow.get("attachment_limit"))
    return _deduplicate(resolved, int(limit) if isinstance(limit, int) else 0)
