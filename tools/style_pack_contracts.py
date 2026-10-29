"""Canvas, scene and body-proportion contracts plus anthropometric QA for StoryArt plans.

Split from style_pack_manager.py; the manager re-exports every name defined here.
"""

from __future__ import annotations

import argparse
import re

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
        "auxiliary body": getattr(args, "aux_body", []),
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

