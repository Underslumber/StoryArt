"""Generation QA evaluation and style calibration evidence for StoryArt.

Split from style_pack_manager.py; the manager re-exports every name defined here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from tools.style_pack_common import StylePackError
    from tools.style_pack_contracts import anthropometric_qa_violations, parse_limb_qa_evidence
except ModuleNotFoundError:  # Direct execution from tools/
    from style_pack_common import StylePackError
    from style_pack_contracts import anthropometric_qa_violations, parse_limb_qa_evidence


ITEM_QA_COMMON = (
    "ITEM_DESIGN", "ITEM_VERSION", "ITEM_DIMENSIONS", "ITEM_MATERIAL_CONSTRUCTION", "ITEM_CHARACTER_SCALE",
)


ITEM_QA_CLOTHING = ("CLOTHING_FIT", "LAYER_ORDER", "CONTACT_CONSTRAINTS")



def load_style_calibration_evidence(
    value: str,
    *,
    required: bool,
    style_name: str,
    pack_path: str = "",
) -> dict[str, object]:
    """Validate a finalized multi-face calibration before production generation."""

    resolved_from_active_profile = False
    if not value and pack_path:
        active_path = (
            Path(pack_path).resolve()
            / "02_LOCAL_ONLY_DO_NOT_UPLOAD"
            / "CALIBRATIONS"
            / "ACTIVE_STYLE_CALIBRATION.json"
        )
        if active_path.is_file():
            value = str(active_path)
            resolved_from_active_profile = True
    if not value:
        if required:
            raise StylePackError(
                "Style calibration is required for this request. Finish at least 2x4 (preferred 4x4) and pass "
                "--style-calibration-state pointing to its finalized state."
            )
        return {
            "required": False,
            "status": "NOT_REQUIRED",
            "path": "",
        }
    path = Path(value).resolve()
    if not path.is_file():
        raise StylePackError(f"Style calibration state does not exist: {path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read style calibration state: {error}") from error
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise StylePackError("Style calibration state must use schema_version 1.")
    if str(state.get("style_name", "")).strip().casefold() != style_name.strip().casefold():
        raise StylePackError("Style calibration belongs to a different style pack.")
    if state.get("status") != "FINALIZED" or state.get("next_required_action") != "NONE":
        raise StylePackError("Style calibration is incomplete; production remains paused until FINALIZED.")
    rounds = state.get("rounds")
    conclusion = state.get("final_conclusion")
    if not isinstance(rounds, list) or len(rounds) < 2 or not isinstance(conclusion, dict):
        raise StylePackError("Finalized style calibration must contain at least two scored quartets and an AI conclusion.")
    completed_rounds = int(conclusion.get("completed_rounds", 0) or 0)
    if completed_rounds < 2 or int(state.get("batch_size", 0) or 0) != 4:
        raise StylePackError("Style calibration evidence must contain at least 2x4 scored candidates.")
    target_rounds = int(state.get("target_rounds", 4) or 4)
    if target_rounds < 4 and not state.get("reduced_rounds_user_approved"):
        raise StylePackError("A two- or three-round calibration has no recorded direct user approval.")
    if completed_rounds < target_rounds and not (
        conclusion.get("early_stop") and conclusion.get("early_stop_user_approved")
    ):
        raise StylePackError("Early calibration finalization has no recorded direct user approval.")
    if completed_rounds != len(rounds):
        raise StylePackError("Finalized calibration round count does not match its conclusion.")
    protocol = int(state.get("protocol_version", 1) or 1)
    if protocol >= 3 and int(state.get("arts_per_round", 0) or 0) != 1:
        raise StylePackError("Protocol-v3 calibration must contain exactly one composite art per round.")
    seen_faces: set[str] = set()
    for index, item in enumerate(rounds, 1):
        if not isinstance(item, dict):
            raise StylePackError("Style calibration contains an invalid round record.")
        face_id = str(item.get("face_id", "")).strip().upper()
        if not face_id or face_id in seen_faces:
            raise StylePackError("Every calibration quartet must use one new temporary subject.")
        seen_faces.add(face_id)
        candidates = item.get("candidates")
        feedback = item.get("feedback")
        if (
            not isinstance(candidates, list)
            or len(candidates) != 4
            or not all(isinstance(candidate, dict) for candidate in candidates)
            or not isinstance(feedback, dict)
        ):
            raise StylePackError("Every finalized calibration round must contain four scored candidates.")
        if any(candidate.get("user_score") is None for candidate in candidates):
            raise StylePackError("Every finalized quartet needs all four panels scored by the user.")
        hashes = {str(candidate.get("image_sha256", "")) for candidate in candidates if isinstance(candidate, dict)}
        if len(hashes) != 4 or "" in hashes:
            raise StylePackError("Every finalized quartet needs four distinct panel identities.")
        if protocol >= 3:
            quartet_path = str(item.get("quartet_art_path", "")).strip()
            quartet_hash = str(item.get("quartet_art_sha256", "")).strip()
            panel_labels = [str(candidate.get("panel_label", "")).strip().upper() for candidate in candidates]
            source_paths = {str(candidate.get("image_path", "")).strip() for candidate in candidates}
            source_hashes = {str(candidate.get("source_art_sha256", "")).strip() for candidate in candidates}
            if (
                not quartet_path
                or not quartet_hash
                or panel_labels != ["A", "B", "C", "D"]
                or source_paths != {quartet_path}
                or source_hashes != {quartet_hash}
            ):
                raise StylePackError(
                    "Protocol-v3 quartet must source panels A/B/C/D from one generated composite art, not four outputs."
                )
        if feedback.get("mode") not in {"PERCENT", "MIN_TO_MAX"}:
            raise StylePackError("Calibration feedback must use PERCENT or MIN_TO_MAX.")
        if protocol >= 2:
            distinction = item.get("distinction_qa")
            if not isinstance(distinction, dict) or distinction.get("review_status") != "PASS":
                raise StylePackError("Protocol-v2 calibration requires passed distinction QA for every quartet.")
            if float(distinction.get("minimum_visible_delta_percent", 0) or 0) < 10:
                raise StylePackError("Protocol-v2 quartet has less than 10% verified visual distance.")
            if distinction.get("decision") != "SHOW_TO_USER" or distinction.get("collapsed_pairs"):
                raise StylePackError("Collapsed or hidden protocol-v2 quartet cannot be production evidence.")
        if index < len(rounds) and not isinstance(item.get("ai_adaptation"), dict):
            raise StylePackError("Every completed quartet before the last needs an AI adaptation record.")
    if protocol >= 2:
        final_style_prompt = " ".join(str(conclusion.get("final_style_prompt", "")).split())
        excluded_noise = conclusion.get("excluded_style_noise")
        prompt_vocabulary = conclusion.get("prompt_vocabulary")
        prompt_evidence = conclusion.get("prompt_evidence_by_round")
        if not final_style_prompt or len(final_style_prompt) > 1200:
            raise StylePackError("Protocol-v2 calibration needs a concise final_style_prompt of at most 1200 characters.")
        if not isinstance(excluded_noise, list) or not excluded_noise:
            raise StylePackError("Protocol-v2 calibration needs a non-empty excluded_style_noise list.")
        if not isinstance(prompt_vocabulary, dict):
            raise StylePackError("Protocol-v2 calibration needs prompt_vocabulary evidence from the four prompt variants.")
        if not isinstance(prompt_evidence, list) or len(prompt_evidence) != completed_rounds:
            raise StylePackError("Protocol-v2 calibration needs prompt evidence for every completed quartet.")
    return {
        "required": required,
        "status": "FINALIZED",
        "path": str(path),
        "resolved_from_active_profile": resolved_from_active_profile,
        "calibration_id": state.get("calibration_id", ""),
        "completed_rounds": completed_rounds,
        "batch_size": 4,
        "feedback_modes": sorted({
            str(item.get("feedback", {}).get("mode", ""))
            for item in rounds
            if isinstance(item, dict) and isinstance(item.get("feedback"), dict)
        }),
        "final_conclusion": conclusion,
    }



def evaluate_generation_qa(
    plan: dict[str, object],
    args: argparse.Namespace,
    *,
    include_results: bool = False,
) -> tuple[list[str], str, list[str]] | tuple[list[str], str, list[str], dict[str, str]]:
    """Return failed checks, resolved stage id, and all QA checks required for the record."""

    workflow = plan["generation_workflow"]
    required = ["ATTACHMENTS", "CANVAS", "STAGE_LAYER"]
    required.extend(("ANATOMY_REVIEW", "VISIBLE_DEFECTS", "PROMPT_ADHERENCE"))
    exact_call = plan.get("execution_call", {})
    stage_id = str(exact_call.get("stage_id") or "SINGLE_PASS").upper() if isinstance(exact_call, dict) else "SINGLE_PASS"
    if workflow["mode"] == "MULTI_STAGE":
        stage_id = args.stage_id.upper()
        stages = workflow["stages"]
        matches = [stage for stage in stages if stage["stage_id"] == stage_id]
        if len(matches) != 1:
            known = ", ".join(stage["stage_id"] for stage in stages)
            raise StylePackError(f"--stage-id must name one prepared stage: {known}.")
        required.extend(matches[0].get("required_qa", []))
    elif args.stage_id:
        raise StylePackError("--stage-id is only valid for a MULTI_STAGE reference plan.")
    else:
        if plan.get("face_review", {}).get("face_visible"):
            required.append("FACE_GEOMETRY")
        if plan.get("canvas_contract", {}).get("full_figure"):
            required.extend(("BODY_SILHOUETTE", "BODY_PROPORTIONS"))

    selected_references = plan.get("selected_references", {})
    selected_design = selected_references.get("item_design", []) if isinstance(selected_references, dict) else []
    selected_scale = selected_references.get("item_scale_fit", []) if isinstance(selected_references, dict) else []
    selected_design = selected_design if isinstance(selected_design, list) else [selected_design]
    selected_scale = selected_scale if isinstance(selected_scale, list) else [selected_scale]
    item_design_records = [record for record in selected_design if isinstance(record, dict)]
    item_scale_records = [record for record in selected_scale if isinstance(record, dict)]
    if bool(item_design_records) != bool(item_scale_records):
        raise StylePackError("Typed item QA requires both approved ITEM_DESIGN and ITEM_SCALE_FIT selections.")
    item_bindings = lambda records: {
        (str(record.get("item_manifest", "")), str(record.get("item_id", "")).upper(),
         str(record.get("version", "")), str(record.get("item_kind", "")).upper())
        for record in records
    }
    if item_design_records and item_bindings(item_design_records) != item_bindings(item_scale_records):
        raise StylePackError("Typed item QA cannot be derived from mismatched design and scale/fit selections.")
    current_call = plan.get("execution_call", {})
    current_call_slots = current_call.get("slots", []) if isinstance(current_call, dict) else []
    item_qa_stage = stage_id == "SINGLE_PASS" or any(
        {str(role).upper() for role in slot.get("active_roles", [])} & {"ITEM_DESIGN", "ITEM_SCALE_FIT"}
        for slot in current_call_slots if isinstance(slot, dict)
    )
    item_kinds = {str(record.get("item_kind", "")).upper() for record in item_design_records}
    if item_design_records and item_qa_stage:
        if not item_kinds or not item_kinds.issubset({"ACCESSORY", "WEAPON", "TOOL", "CLOTHING"}):
            raise StylePackError("Typed item QA requires known item_kind metadata from the selected approved items.")
        required.extend(ITEM_QA_COMMON)
        if "CLOTHING" in item_kinds:
            required.extend(ITEM_QA_CLOTHING)

    semantic_qa_schema = int(plan.get("semantic_qa_schema", 0) or 0)
    semantic_qa = semantic_qa_schema >= 1
    if semantic_qa and "STYLE" not in required:
        required.append("STYLE")
    if (
        semantic_qa_schema >= 2
        and stage_id in {"02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY"}
        and "BODY_RENDERING_STYLE" not in required
    ):
        required.append("BODY_RENDERING_STYLE")
    if (
        semantic_qa_schema >= 3
        and stage_id in {"02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY"}
        and "LIMB_PROPORTIONS" not in required
    ):
        required.append("LIMB_PROPORTIONS")
    scene_contract = plan.get("scene_contract")
    if (
        semantic_qa
        and plan.get("generation_purpose") == "SCENE"
        and isinstance(scene_contract, dict)
        and scene_contract.get("applicable")
        and not scene_contract.get("has_character")
    ):
        required.extend(
            (
                "SUBJECT_ACCURACY",
                "NO_UNREQUESTED_CHARACTERS",
                "FOCAL_HIERARCHY",
                "LIGHTING",
                "BACKGROUND",
                "COMPOSITION",
            )
        )
        if scene_contract.get("output_use") == "WALLPAPER":
            required.extend(("DISTANCE_READABILITY", "DESKTOP_USABILITY"))
        elif scene_contract.get("output_use") == "PROMO_POSTER":
            required.extend(("POSTER_READABILITY", "COPY_SAFE_AREA"))
        if scene_contract.get("scene_kind") == "LOCATION":
            required.append("DEPTH_AND_SCALE")
        elif scene_contract.get("scene_kind") == "PHENOMENON":
            required.append("PHENOMENON_CAUSALITY")
        elif scene_contract.get("scene_kind") == "ARTIFACT":
            required.append("ARTIFACT_INTEGRITY")
    if plan.get("generation_purpose") == "SCENE" and isinstance(scene_contract, dict) and scene_contract.get("applicable") and scene_contract.get("has_character"):
        required.extend(plan.get("scene_semantic_requirements", [
            "SUBJECT_ACCURACY", "CLOTHING", "POSE_CONTACTS", "OBJECTS_AND_ACTION", "BACKGROUND", "COMPOSITION"
        ]))

    qa_values = {
        "ATTACHMENTS": args.qa_attachments,
        "CANVAS": args.qa_canvas,
        "STAGE_LAYER": args.qa_stage_layer,
        "FACE_GEOMETRY": args.qa_face,
        "BODY_SILHOUETTE": args.qa_body_silhouette,
        "BODY_PROPORTIONS": args.qa_body_proportions,
        "LIMB_PROPORTIONS": getattr(args, "qa_limb_proportions", "NOT_CHECKED"),
        "ANATOMY_REVIEW": getattr(args, "visual_anatomy_status", "NOT_CHECKED"),
        "VISIBLE_DEFECTS": getattr(args, "visual_defects_status", "NOT_CHECKED"),
        "PROMPT_ADHERENCE": getattr(args, "visual_prompt_status", "NOT_CHECKED"),
    }
    if semantic_qa:
        view_value = getattr(args, "qa_view", "NOT_CHECKED")
        qa_values.update({
            "STYLE": getattr(args, "qa_style", "NOT_CHECKED"),
            "BODY_RENDERING_STYLE": getattr(args, "qa_body_style", "NOT_CHECKED"),
            "EXPRESSION": getattr(args, "qa_expression", "NOT_CHECKED"),
            "NEUTRAL_BACKDROP": getattr(args, "qa_neutral_backdrop", "NOT_CHECKED"),
            "FRONT_VIEW": view_value,
            "SIDE_VIEW": view_value,
            "BACK_VIEW": view_value,
            "SAFE_COVERAGE": getattr(args, "qa_safe_coverage", "NOT_CHECKED"),
            "CLOTHING_TOPOLOGY": getattr(args, "qa_clothing_topology", "NOT_CHECKED"),
            "MULTIVIEW_CONSISTENCY": getattr(args, "qa_multiview_consistency", "NOT_CHECKED"),
            "CLOTHING": getattr(args, "qa_clothing", "NOT_CHECKED"),
            "POSE_CONTACTS": getattr(args, "qa_pose_contacts", "NOT_CHECKED"),
            "CAMERA": getattr(args, "qa_camera", "NOT_CHECKED"),
            "LIGHTING": getattr(args, "qa_lighting", "NOT_CHECKED"),
            "BACKGROUND": getattr(args, "qa_background", "NOT_CHECKED"),
            "COMPOSITION": getattr(args, "qa_composition", "NOT_CHECKED"),
            "SUBJECT_ACCURACY": getattr(args, "qa_subject_accuracy", "NOT_CHECKED"),
            "OBJECTS_AND_ACTION": getattr(args, "qa_objects_action", "NOT_CHECKED"),
            "NO_UNREQUESTED_CHARACTERS": getattr(args, "qa_no_unrequested_characters", "NOT_CHECKED"),
            "FOCAL_HIERARCHY": getattr(args, "qa_focal_hierarchy", "NOT_CHECKED"),
            "DISTANCE_READABILITY": getattr(args, "qa_distance_readability", "NOT_CHECKED"),
            "DESKTOP_USABILITY": getattr(args, "qa_desktop_usability", "NOT_CHECKED"),
            "POSTER_READABILITY": getattr(args, "qa_poster_readability", "NOT_CHECKED"),
            "COPY_SAFE_AREA": getattr(args, "qa_copy_safe_area", "NOT_CHECKED"),
            "DEPTH_AND_SCALE": getattr(args, "qa_depth_and_scale", "NOT_CHECKED"),
            "PHENOMENON_CAUSALITY": getattr(args, "qa_phenomenon_causality", "NOT_CHECKED"),
            "ARTIFACT_INTEGRITY": getattr(args, "qa_artifact_integrity", "NOT_CHECKED"),
        })
    qa_values.update({
        "ITEM_DESIGN": getattr(args, "qa_item_design", "NOT_CHECKED"),
        "ITEM_VERSION": getattr(args, "qa_item_version", "NOT_CHECKED"),
        "ITEM_DIMENSIONS": getattr(args, "qa_item_dimensions", "NOT_CHECKED"),
        "ITEM_MATERIAL_CONSTRUCTION": getattr(args, "qa_item_material_construction", "NOT_CHECKED"),
        "ITEM_CHARACTER_SCALE": getattr(args, "qa_item_character_scale", "NOT_CHECKED"),
        "CLOTHING_FIT": getattr(args, "qa_clothing_fit", "NOT_CHECKED"),
        "LAYER_ORDER": getattr(args, "qa_layer_order", "NOT_CHECKED"),
        "CONTACT_CONSTRAINTS": getattr(args, "qa_contact_constraints", "NOT_CHECKED"),
    })
    required_names = sorted(set(required))
    typed_item_qa_names = set(ITEM_QA_COMMON) | set(ITEM_QA_CLOTHING)
    missing = [
        name for name in required_names
        if (semantic_qa or name in qa_values or name in typed_item_qa_names)
        and qa_values.get(name, "NOT_CHECKED") == "NOT_CHECKED"
    ]
    if missing:
        raise StylePackError("Required post-generation QA was not performed: " + ", ".join(missing))
    if "LIMB_PROPORTIONS" in required_names and qa_values["LIMB_PROPORTIONS"] == "PASS":
        evidence = str(getattr(args, "limb_qa_evidence", "") or "").strip()
        child_base = isinstance(plan.get("character_base_contract"), dict) and plan["character_base_contract"].get("age_group") == "CHILD"
        required_tokens = ("age_group=CHILD", "view=", "proportions_reviewed=PASS") if child_base else ("source=", "head_units=", "hip_knee=", "knee_ankle=", "ankle_width=", "foot_length=")
        if semantic_qa_schema >= 4 and not child_base:
            required_tokens += (
                "pubic_height_fraction=", "neck_head_ratio=", "neck_jaw_ratio=", "foot_pose=",
                "hip_landmark=", "knee_landmark=", "ankle_landmark=", "landmark_confidence=",
                "crown_landmark=", "crown_confidence=",
                "view=", "foot_length_mode=", "heel_endpoint=", "toe_endpoint=",
            )
        missing_tokens = [token for token in required_tokens if token not in evidence]
        if missing_tokens:
            raise StylePackError(
                "LIMB_PROPORTIONS=PASS requires --limb-qa-evidence with: "
                + ", ".join(required_tokens)
            )
        if child_base:
            values = parse_limb_qa_evidence(evidence)
            expected_view = {
                "02_PHYSIQUE_FRONT": "FRONT", "03_PHYSIQUE_SIDE": "SIDE",
                "04_PHYSIQUE_BACK": "BACK", "05_CHARACTER_ASSEMBLY": "ASSEMBLY",
            }.get(stage_id)
            if (values.get("age_group", "").upper() != "CHILD"
                    or values.get("proportions_reviewed", "").upper() != "PASS"
                    or not expected_view or values.get("view", "").upper() != expected_view):
                raise StylePackError(
                    f"CHILD LIMB_PROPORTIONS evidence must confirm age_group=CHILD, view={expected_view}, and proportions_reviewed=PASS."
                )
        if semantic_qa_schema >= 4 and not child_base:
            body_contract = plan.get("body_proportion_contract", {})
            user_override = bool(
                isinstance(body_contract, dict)
                and body_contract.get("user_approved_nonstandard_proportions")
            )
            violations = anthropometric_qa_violations(evidence, plan)
            if violations and not user_override:
                qa_values["LIMB_PROPORTIONS"] = "FAIL"
                qa_values["BODY_PROPORTIONS"] = "FAIL"
    failed = [name for name in required_names if qa_values.get(name) == "FAIL"]
    results = {name: str(qa_values.get(name, "NOT_CHECKED")) for name in required_names}
    if include_results:
        return failed, stage_id, required_names, results
    return failed, stage_id, required_names

