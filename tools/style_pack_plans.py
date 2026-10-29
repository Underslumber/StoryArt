"""Generation workflow and attachment plans for StoryArt reference plans.

Split from style_pack_manager.py; the manager re-exports every name defined here.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

try:
    from tools.style_pack_common import StylePackError
except ModuleNotFoundError:  # Direct execution from tools/
    from style_pack_common import StylePackError


def build_attachment_plan(
    selected: dict[str, object],
    auxiliary: list[dict[str, object]],
    limit: int,
) -> list[dict[str, object]]:
    if not 1 <= limit <= 5:
        raise StylePackError("Attachment limit must be within 1-5 for the current generator workflow.")
    grouped: dict[str, dict[str, object]] = {}

    def add(record: dict[str, object], role: str) -> None:
        digest = str(record["sha256"])
        item = grouped.setdefault(digest, {"path": record["path"], "sha256": digest, "active_roles": []})
        roles = item["active_roles"]
        if role not in roles:
            roles.append(role)

    for key, value in selected.items():
        role = key.upper()
        if isinstance(value, list):
            for record in value:
                add(record, role)
        else:
            add(value, role)
    for record in auxiliary:
        for role in record["active_roles"]:
            add(record, f"{record['ref_id']}:{role}")
    attachments = list(grouped.values())
    if len(attachments) > limit:
        details = "; ".join(
            f"{Path(str(item['path'])).name} ({','.join(item['active_roles'])})" for item in attachments
        )
        raise StylePackError(
            f"Reference plan needs {len(attachments)} physical attachments but the limit is {limit}. "
            "No BODY, face, or style reference may be silently omitted. Reuse a genuinely multi-role full-resolution file, "
            "release a category explicitly, or create a manifest-approved targeted collage. Planned: " + details
        )
    for slot, item in enumerate(attachments, 1):
        item["slot"] = slot
        item["active_roles"] = sorted(item["active_roles"])
        item["physically_attach"] = True
    return attachments



def build_multistage_attachment_plan(
    selected: dict[str, object],
    auxiliary: list[dict[str, object]],
    limit: int,
    purpose: str = "SCENE",
    character_age_group: str = "ADULT",
    character_garment: str = "",
) -> list[dict[str, object]]:
    """Build a bounded layer-by-layer workflow when one pass cannot carry every hard reference."""

    if not 1 <= limit <= 5:
        raise StylePackError("Attachment limit must be within 1-5 for the current generator workflow.")
    style_records = selected.get("style", [])
    if not isinstance(style_records, list):
        raise StylePackError("Multi-stage STYLE references must be a list.")
    style_anchor = style_records[0] if style_records else None
    stages: list[dict[str, object]] = []

    def source_records(keys: Sequence[str], include_style: bool = True) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        if include_style and style_stage_record is not None:
            records.append({**style_stage_record, "stage_role": "STYLE"})
        for key in keys:
            value = selected.get(key)
            if not value:
                continue
            values = value if isinstance(value, list) else [value]
            for record in values:
                records.append({**record, "stage_role": key.upper()})
        return records

    def placeholder(stage_id: str, role: str) -> dict[str, object]:
        return {
            "path": f"<STAGE_OUTPUT:{stage_id}>",
            "sha256": f"STAGE_OUTPUT:{stage_id}",
            "stage_role": role,
            "generated_stage_output": True,
        }

    def targeted_stage_pack(stage_ids: Sequence[str], role: str) -> dict[str, object]:
        pack_id = "+".join(stage_ids)
        return {
            "path": f"<TARGETED_STAGE_PACK:{pack_id}>",
            "sha256": f"TARGETED_STAGE_PACK:{pack_id}",
            "stage_role": role,
            "generated_stage_output": True,
            "planned_targeted_pack": True,
            "targeted_pack_sources": list(stage_ids),
        }

    def add_stage(stage_id: str, purpose: str, records: list[dict[str, object]], qa: Sequence[str]) -> None:
        grouped: dict[str, dict[str, object]] = {}
        for record in records:
            digest = str(record["sha256"])
            item = grouped.setdefault(
                digest,
                {
                    "path": record["path"],
                    "sha256": digest,
                    "active_roles": [],
                    "physically_attach": True,
                    "generated_stage_output": bool(record.get("generated_stage_output")),
                },
            )
            if record.get("generated_stage_output"):
                # Keep the stage's semantic role for exact generated-source and
                # targeted-pack provenance checks at READY/START.
                item["stage_role"] = str(record.get("stage_role", ""))
            if record.get("planned_targeted_pack"):
                item["planned_targeted_pack"] = True
                item["targeted_pack_sources"] = list(record.get("targeted_pack_sources", []))
            role = str(record["stage_role"])
            if role not in item["active_roles"]:
                item["active_roles"].append(role)
        slots = list(grouped.values())
        if len(slots) > limit:
            detail = "; ".join(f"{Path(str(item['path'])).name} ({','.join(item['active_roles'])})" for item in slots)
            raise StylePackError(
                f"Multi-stage step {stage_id} still needs {len(slots)} attachments, above limit {limit}. "
                "Reduce conflicting references or create a manifest-approved targeted collage. Planned: " + detail
            )
        for number, item in enumerate(slots, 1):
            item["slot"] = number
            item["active_roles"] = sorted(item["active_roles"])
        stages.append({
            "stage_id": stage_id,
            "purpose": purpose,
            "attachment_limit": limit,
            "attachments_used": len(slots),
            "slots": slots,
            "required_qa": list(qa),
            "output_status": "STAGING_PASS_REQUIRED",
            "may_feed_next_stage_only_after_qa_pass": True,
        })

    style_stage_record = style_anchor
    if purpose != "CHARACTER_BASE" and len(style_records) > 1:
        add_stage(
            "00_STYLE_SYNTHESIS",
            "Resolve the compatible shared rendering language from all selected style references.",
            [{**record, "stage_role": "STYLE"} for record in style_records],
            ("STYLE",),
        )
        style_stage_record = placeholder("00_STYLE_SYNTHESIS", "STYLE_STAGE")

    face_keys = [key for key in ("primary_face", "supporting_face", "expression", "face_variant") if key in selected]
    has_face_variant = bool(selected.get("face_variant"))
    has_body_variant = bool(selected.get("body_variant"))
    typed_item_kinds = {
        str(record.get("item_kind", "")).upper()
        for value in (selected.get("item_design", []),)
        for record in (value if isinstance(value, list) else [value])
        if isinstance(record, dict)
    }
    has_accessory = bool(selected.get("accessory")) or bool(typed_item_kinds & {"ACCESSORY", "WEAPON", "TOOL"})
    has_typed_item = bool(selected.get("item_design") and selected.get("item_scale_fit"))
    face_variant_condition = (
        " Preserve every selected FACE_VARIANT as a distinct approved detail; do not reinterpret it as canonical identity or merge it into another role."
        if has_face_variant else ""
    )
    body_variant_condition = (
        " Preserve the selected BODY_VARIANT details through this stage without silently replacing the canonical character base."
        if has_body_variant else ""
    )
    accessory_condition = (
        " Preserve the selected approved ACCESSORY design, scale, placement, and visible details through this stage."
        if has_accessory else ""
    )
    item_condition = (
        " Preserve the selected typed item's exact approved version, dimensions, ratios, material, construction, and character-relative scale. Use its ITEM_DESIGN view for object detail and ITEM_SCALE_FIT only for size/fit with this exact character. Do not invent holding or wearing; an accessory, weapon, or tool may remain beside the character."
        if has_typed_item else ""
    )
    typed_item_qa = (
        ("ITEM_DESIGN", "ITEM_VERSION", "ITEM_DIMENSIONS", "ITEM_MATERIAL_CONSTRUCTION", "ITEM_CHARACTER_SCALE")
        + (("CLOTHING_FIT", "LAYER_ORDER", "CONTACT_CONSTRAINTS") if "CLOTHING" in typed_item_kinds else ())
    ) if has_typed_item else ()
    if face_keys and purpose != "CHARACTER_BASE":
        face_inputs = source_records(face_keys)
        if "character_assembly" in selected:
            face_inputs.extend(source_records(["character_assembly"], include_style=False))
        add_stage(
            "01_FACE_IDENTITY",
            "Resolve face geometry and expression in the source drawing style; do not invent body proportions." + face_variant_condition,
            face_inputs,
            ("FACE_GEOMETRY", "EXPRESSION", "STYLE"),
        )

    body_records = source_records([key for key in ("character_assembly", "body", "pose", "body_variant") if key in selected])
    for record in auxiliary:
        body_records.append({**record, "stage_role": f"{record['ref_id']}:{record['mode']}"})

    if purpose == "CHARACTER_BASE":
        child = character_age_group == "CHILD"
        garment = character_garment or "the prompt-defined safety garment"
        noun = "child" if child else "adult"
        side_description = (
            f"Create the canonical child full-body side view of the same physique on a plain neutral backdrop. Deterministically pack the passed FACE and FRONT outputs without cropping and attach that generator-safe targeted pack as their single physical slot. Match the same age-appropriate silhouette and limb proportions; keep {garment} opaque and consistent without altering the external silhouette."
            if child else
            "Create the canonical adult full-body side view of the same physique on a plain neutral backdrop. Deterministically pack the passed FACE and FRONT outputs without cropping and attach that generator-safe targeted pack as their single physical slot. Match height, torso depth, abdomen, pelvis, glutes, thighs, spinal curve, and limb proportions; keep the prompt-defined safety garment consistent without altering the external silhouette."
        )
        back_description = (
            f"Create the canonical child full-body back view of the same physique on a plain neutral backdrop. Deterministically pack the passed FACE, FRONT, and SIDE outputs without cropping and attach that generator-safe targeted pack as their single physical slot. Match the same age-appropriate silhouette and limb proportions; keep {garment} opaque and consistent without altering the external silhouette."
            if child else
            "Create the canonical adult full-body back view of the same physique on a plain neutral backdrop. Deterministically pack the passed FACE, FRONT, and SIDE outputs without cropping and attach that generator-safe targeted pack as their single physical slot. Match the front and side views' height, shoulders, torso, waist, pelvis, glutes, thighs, and limbs; keep the prompt-defined safety garment consistent without altering the external silhouette."
        )
        def coverage_records(keys: Sequence[str]) -> list[dict[str, object]]:
            records = source_records(keys, include_style=False)
            for record in records:
                record["stage_role"] = "CLOTHING_TOPOLOGY"
            return records

        face_purpose = (
            f"Resolve the canonical close face geometry and base expression first in the selected style. The child wears {garment}; show the required T-shirt in this close face crop, with the required lower garment maintained outside the crop. This passed face becomes a mandatory hard identity attachment for every physique projection. Do not add accessories or a designed environment."
            if child else
            "Resolve the canonical close face geometry and base expression first in the selected style. This passed face becomes a mandatory hard identity attachment for every physique projection; do not add character wardrobe, accessories, or a designed environment."
        )
        add_stage(
            "01_FACE_IDENTITY",
            face_purpose,
            source_records(face_keys),
            ("FACE_GEOMETRY", "EXPRESSION", "STYLE", "NEUTRAL_BACKDROP"),
        )
        face_output = placeholder("01_FACE_IDENTITY", "FACE_IDENTITY_STAGE")
        front_records = [*body_records, face_output, *coverage_records(["coverage_front"])]
        add_stage(
            "02_PHYSIQUE_FRONT",
            f"Create the canonical {noun} full-body front view on a plain neutral backdrop. Preserve the complete silhouette and render {garment}; use optional CLOTHING_TOPOLOGY evidence only when the user directly requested it.",
            front_records,
            ("FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "LIMB_PROPORTIONS", "FRONT_VIEW", "SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "STYLE", "BODY_RENDERING_STYLE", *(('CLOTHING',) if child else ())),
        )
        front_output = placeholder("02_PHYSIQUE_FRONT", "PHYSIQUE_FRONT_STAGE")
        face_front_pack = targeted_stage_pack(
            ("01_FACE_IDENTITY", "02_PHYSIQUE_FRONT"),
            "FACE_FRONT_TARGETED_PACK",
        )
        side_records = [*body_records, face_front_pack, *coverage_records(["coverage_front", "coverage_side"])]
        add_stage(
            "03_PHYSIQUE_SIDE",
            side_description,
            side_records,
            ("FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "LIMB_PROPORTIONS", "SIDE_VIEW", "SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "MULTIVIEW_CONSISTENCY", "STYLE", "BODY_RENDERING_STYLE", *(('CLOTHING',) if child else ())),
        )
        side_output = placeholder("03_PHYSIQUE_SIDE", "PHYSIQUE_SIDE_STAGE")
        face_front_side_pack = targeted_stage_pack(
            ("01_FACE_IDENTITY", "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE"),
            "FACE_FRONT_SIDE_TARGETED_PACK",
        )
        back_records = [*body_records, face_front_side_pack, *coverage_records(["coverage_back"])]
        add_stage(
            "04_PHYSIQUE_BACK",
            back_description,
            back_records,
            ("FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "LIMB_PROPORTIONS", "BACK_VIEW", "SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "MULTIVIEW_CONSISTENCY", "STYLE", "BODY_RENDERING_STYLE", *(('CLOTHING',) if child else ())),
        )
        assembly_inputs = [
            *([{**style_stage_record, "stage_role": "STYLE"}] if style_stage_record is not None else []),
            face_output,
            front_output,
            side_output,
            placeholder("04_PHYSIQUE_BACK", "PHYSIQUE_BACK_STAGE"),
        ]
        add_stage(
            "05_CHARACTER_ASSEMBLY",
            (f"Create a neutral canonical 3/4 character assembly from the verified face and front/side/back physique. Preserve the child's required {garment}; do not add accessories or a designed background and do not reopen identity or proportions." if child else "Create a neutral canonical 3/4 character assembly from the verified face and front/side/back physique. Do not add a designed background, outfit, or accessories and do not reopen identity or proportions."),
            assembly_inputs,
            ("FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "LIMB_PROPORTIONS", "MULTIVIEW_CONSISTENCY", "NEUTRAL_BACKDROP", "STYLE", "BODY_RENDERING_STYLE", *(('SAFE_COVERAGE', 'CLOTHING_TOPOLOGY', 'CLOTHING') if child else ())),
        )
        return stages

    add_stage(
        "02_BODY_POSE",
        "Resolve the dominant silhouette, proportions, pose, camera, contacts, and foreshortening on a neutral outfit/background." + body_variant_condition,
        body_records,
        ("BODY_SILHOUETTE", "BODY_PROPORTIONS", "POSE_CONTACTS", "CAMERA", "STYLE"),
    )

    body_output = placeholder("02_BODY_POSE", "BODY_POSE_STAGE")
    character_base_output = body_output
    identity_anchor_records = source_records(["character_assembly"], include_style=False) if "character_assembly" in selected else []
    if "clothes" in selected or "accessory" in selected or typed_item_kinds:
        add_stage(
            "03_CLOTHING",
            "Apply selected clothing and accessories to the verified body/pose without changing its silhouette, anatomy, or camera." + body_variant_condition + accessory_condition,
            [*([{**style_stage_record, "stage_role": "STYLE"}] if style_stage_record is not None else []), body_output,
             *identity_anchor_records,
             *source_records([key for key in ("clothes", "accessory", "item_design", "item_scale_fit") if key in selected], include_style=False)],
            ("CLOTHING", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "STYLE", *typed_item_qa),
        )
        character_base_output = placeholder("03_CLOTHING", "CLOTHING_STAGE")

    composite_inputs = [*([{**style_stage_record, "stage_role": "STYLE"}] if style_stage_record is not None else []),
                        character_base_output, *identity_anchor_records]
    if face_keys:
        composite_inputs.append(placeholder("01_FACE_IDENTITY", "FACE_IDENTITY_STAGE"))
    add_stage(
        "04_CHARACTER_COMPOSITE",
        "Combine the verified face with the verified clothed body; preserve both layers exactly." + face_variant_condition + body_variant_condition + accessory_condition,
        composite_inputs,
        ("FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "CLOTHING", "STYLE"),
    )

    final_inputs = [
        *([{**style_stage_record, "stage_role": "STYLE"}] if style_stage_record is not None else []),
        placeholder("04_CHARACTER_COMPOSITE", "CHARACTER_COMPOSITE_STAGE"),
        *identity_anchor_records,
        *source_records(
            [key for key in ("lighting", "background", "composition", "item_design", "item_scale_fit") if key in selected],
            include_style=False,
        ),
    ]
    add_stage(
        "05_FINAL_SCENE",
        "Place the verified character into the final scene without reopening face or body design." + face_variant_condition + body_variant_condition + accessory_condition + item_condition,
        final_inputs,
        ("CANVAS", "FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "LIGHTING", "BACKGROUND", "COMPOSITION", "STYLE", *typed_item_qa),
    )
    return stages



def build_generation_workflow(
    args: argparse.Namespace,
    selected: dict[str, object],
    auxiliary: list[dict[str, object]],
) -> dict[str, object]:
    mode = args.reference_workflow.upper()
    purpose = getattr(args, "generation_purpose", "SCENE").upper()
    character_free_scene = purpose == "SCENE" and str(getattr(args, "character_id", "NONE")).upper() == "NONE"
    if character_free_scene:
        if mode == "MULTI_STAGE":
            raise StylePackError(
                "A character-free SCENE is one requested key-art deliverable. MULTI_STAGE would create unrequested service images; "
                "reduce the reference set to the physical attachment limit."
            )
        slots = build_attachment_plan(selected, auxiliary, args.attachment_limit)
        return {
            "mode": "SINGLE_PASS",
            "purpose": purpose,
            "attachment_limit": args.attachment_limit,
            "attachments_used": len(slots),
            "slots": slots,
            "all_selected_references_physically_attached": True,
            "unrequested_staging_forbidden": True,
        }
    if purpose == "CHARACTER_BASE" and mode == "SINGLE_PASS":
        raise StylePackError("CHARACTER_BASE requires MULTI_STAGE so face, front/side/back physique, and assembly are verified separately.")
    if purpose == "CHARACTER_BASE":
        mode = "MULTI_STAGE"
    if mode in {"AUTO", "SINGLE_PASS"}:
        try:
            slots = build_attachment_plan(selected, auxiliary, args.attachment_limit)
            return {
                "mode": "SINGLE_PASS",
                "attachment_limit": args.attachment_limit,
                "attachments_used": len(slots),
                "slots": slots,
                "all_selected_references_physically_attached": True,
            }
        except StylePackError as error:
            if mode == "SINGLE_PASS":
                raise
            if purpose == "SCENE":
                raise StylePackError(
                    "A SCENE is one requested deliverable and cannot auto-expand into generated helper images. "
                    "Reduce the attached references or explicitly request MULTI_STAGE with user authorization."
                ) from error
    stages = build_multistage_attachment_plan(
        selected, auxiliary, args.attachment_limit, purpose,
        str(getattr(args, "character_age_group", "ADULT") or "ADULT").upper(),
        str(getattr(args, "character_base_garment", "") or ""),
    )
    return {
        "mode": "MULTI_STAGE",
        "purpose": purpose,
        "attachment_limit": args.attachment_limit,
        "stages": stages,
        "staging_outputs_are_request_local": True,
        "staging_outputs_are_not_character_or_style_anchors": True,
        "all_source_conditions_propagate_through_verified_layers": True,
    }

