#!/usr/bin/env python3
"""Manage evidence-based multi-round style-transfer calibration.

One calibration round is generated as exactly one composite art containing
four labeled panels of the same temporary face. Every later round must use a
new face. The user scores all four panels either with percentages or by
ordering them from minimum to maximum style match. The next round cannot open
until an AI adaptation record explains how that feedback changes the prompt
and reference strategy.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = 1
PROTOCOL_VERSION = 3
SUPPORTED_PROTOCOL_VERSIONS = {1, 2, 3}
DEFAULT_ROUNDS = 4
MIN_ROUNDS = 2
BATCH_SIZE = 4
ARTS_PER_ROUND = 1
PANEL_LABELS = ("A", "B", "C", "D")
MIN_VISUAL_DELTA_PERCENT = 10.0
MAX_STYLE_PROMPT_CHARS = 1200
MAX_QUARTET_PROMPT_CHARS = 4000
FEEDBACK_MODES = {"PERCENT", "MIN_TO_MAX"}
VARIANT_ROLES = {"TARGET", "ALTERNATIVE", "CHAOS_PROBE", "CONTROL"}
REQUIRED_STRATEGY_FIELDS = {
    "hypothesis", "controlled_constants", "comparison_contract", "variants",
}
REQUIRED_ADAPTATION_FIELDS = {
    "observations", "preserve", "change", "drift_signals", "next_round_strategy",
}
REQUIRED_PROMPT_FINDINGS_FIELDS = {
    "variant_prompt_findings", "cross_variant_findings", "retained_core_features",
    "removed_or_deemphasized_elements", "next_prompt_experiment",
}
REQUIRED_CONCLUSION_FIELDS = {
    "summary", "reliable_style_features", "identity_preservation_rules",
    "prompt_strategy", "reference_strategy", "drift_rejection_rules",
    "recommended_fidelity",
}
REQUIRED_V2_CONCLUSION_FIELDS = {
    "final_style_prompt", "prompt_vocabulary", "prompt_evidence_by_round", "excluded_style_noise",
}
REQUIRED_DISTINCTION_FIELDS = {
    "review_status", "minimum_visible_delta_percent", "same_identity_constants",
    "chaos_probe_visible", "pairwise_checks", "collapsed_pairs", "decision",
}
REQUIRED_STYLE_ANALYSIS_FIELDS = {
    "source_scope", "reviewed_source_count", "invariant_core_features",
    "optional_variation", "forbidden_drift", "facial_construction",
    "working_hypothesis",
}


class StyleCalibrationError(RuntimeError):
    pass


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_token(value: str, fallback: str = "CALIBRATION") -> str:
    token = re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
    return token or fallback


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StyleCalibrationError(f"Cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise StyleCalibrationError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def require_nonempty_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list) or not value:
        raise StyleCalibrationError(f"{name} must be a non-empty JSON list.")
    return value


def require_fields(value: dict[str, object], fields: set[str], label: str) -> None:
    missing = sorted(field for field in fields if field not in value)
    if missing:
        raise StyleCalibrationError(f"{label} is missing required fields: {', '.join(missing)}")


def load_state(path: Path) -> dict[str, object]:
    state = read_json(path)
    if state.get("schema_version") != SCHEMA_VERSION:
        raise StyleCalibrationError(f"Unsupported calibration schema in {path}")
    validate_state(state)
    return state


def save_state(path: Path, state: dict[str, object]) -> None:
    state["updated_at"] = iso_now()
    validate_state(state)
    write_json(path, state)


def round_by_number(state: dict[str, object], number: int) -> dict[str, object]:
    rounds = state["rounds"]
    assert isinstance(rounds, list)
    matches = [item for item in rounds if item.get("round") == number]
    if len(matches) != 1:
        raise StyleCalibrationError(f"Calibration round {number} does not exist.")
    return matches[0]


def protocol_version(state: dict[str, object]) -> int:
    return int(state.get("protocol_version", 1) or 1)


def require_single_art_protocol(state: dict[str, object]) -> None:
    if state.get("status") == "ACTIVE" and protocol_version(state) < 3:
        raise StyleCalibrationError(
            "Active protocol-v1/v2 calibration cannot continue: it permits four separate generator outputs. "
            "Start a protocol-v3 calibration and generate one 2x2 composite art per round. "
            "Finalized legacy calibration remains readable and applicable."
        )


def normalized_prompt(value: object) -> str:
    return " ".join(str(value).split())


def validate_style_prompt(value: object, label: str) -> str:
    prompt = normalized_prompt(value)
    if not prompt:
        raise StyleCalibrationError(f"{label} cannot be blank.")
    if len(prompt) > MAX_STYLE_PROMPT_CHARS:
        raise StyleCalibrationError(
            f"{label} exceeds the {MAX_STYLE_PROMPT_CHARS}-character calibration limit; remove non-essential wording."
        )
    return prompt


def validate_generation_contract(strategy: dict[str, object]) -> None:
    contract = strategy.get("generation_contract")
    if not isinstance(contract, dict):
        raise StyleCalibrationError("Protocol-v3 strategy requires generation_contract.")
    require_fields(
        contract,
        {"output_count", "single_generator_call", "layout", "panel_labels"},
        "generation_contract",
    )
    if int(contract["output_count"]) != ARTS_PER_ROUND:
        raise StyleCalibrationError("Calibration round must generate exactly one composite art.")
    if contract["single_generator_call"] is not True:
        raise StyleCalibrationError("Calibration quartet must be produced by one generator call.")
    if str(contract["layout"]).strip().upper() != "2X2":
        raise StyleCalibrationError("Calibration composite art must use the 2x2 panel layout.")
    labels = tuple(str(item).strip().upper() for item in contract["panel_labels"])
    if labels != PANEL_LABELS:
        raise StyleCalibrationError("Calibration composite panels must be ordered A, B, C, D.")
    quartet_prompt = normalized_prompt(strategy.get("full_quartet_prompt", ""))
    if not quartet_prompt:
        raise StyleCalibrationError("Protocol-v3 strategy requires one full_quartet_prompt for the single art call.")
    if len(quartet_prompt) > MAX_QUARTET_PROMPT_CHARS:
        raise StyleCalibrationError(
            f"full_quartet_prompt exceeds the {MAX_QUARTET_PROMPT_CHARS}-character limit."
        )


def validate_strategy(strategy: dict[str, object], *, version: int = PROTOCOL_VERSION) -> None:
    required_fields = REQUIRED_STRATEGY_FIELDS if version >= 2 else {
        "hypothesis", "controlled_constants", "variants",
    }
    require_fields(strategy, required_fields, "Round strategy")
    if version >= 2:
        require_fields(
            strategy,
            {
                "prompt_experiment_id", "parent_prompt_experiment_id", "retained_prior_findings",
                "superseded_prior_findings", "excluded_style_noise",
            },
            "Round strategy",
        )
        if not str(strategy["prompt_experiment_id"]).strip():
            raise StyleCalibrationError("prompt_experiment_id cannot be blank.")
        if not str(strategy["parent_prompt_experiment_id"]).strip():
            raise StyleCalibrationError("parent_prompt_experiment_id cannot be blank.")
        require_nonempty_list(strategy["retained_prior_findings"], "retained_prior_findings")
        if not isinstance(strategy["superseded_prior_findings"], list):
            raise StyleCalibrationError("superseded_prior_findings must be a list.")
        require_nonempty_list(strategy["excluded_style_noise"], "excluded_style_noise")
    if version >= 3:
        validate_generation_contract(strategy)
    constants = require_nonempty_list(strategy["controlled_constants"], "controlled_constants")
    if not all(str(item).strip() for item in constants):
        raise StyleCalibrationError("controlled_constants cannot contain blank values.")
    variants = strategy["variants"]
    if not isinstance(variants, list) or len(variants) != BATCH_SIZE:
        raise StyleCalibrationError(f"Round strategy must contain exactly {BATCH_SIZE} variants.")
    labels: list[str] = []
    roles: list[str] = []
    full_prompts: list[str] = []
    comparison = strategy.get("comparison_contract", {})
    if version >= 2:
        if not isinstance(comparison, dict):
            raise StyleCalibrationError("comparison_contract must be a JSON object.")
        require_fields(
            comparison,
            {"baseline_label", "target_fidelity", "minimum_expected_delta_percent"},
            "comparison_contract",
        )
        try:
            minimum_delta = float(comparison["minimum_expected_delta_percent"])
            target_fidelity = float(comparison["target_fidelity"])
        except (TypeError, ValueError) as error:
            raise StyleCalibrationError("comparison_contract percentages must be numeric.") from error
        if minimum_delta < MIN_VISUAL_DELTA_PERCENT:
            raise StyleCalibrationError(
                f"Calibration variants must target at least {MIN_VISUAL_DELTA_PERCENT:g}% visible style distance."
            )
        if target_fidelity < 0 or target_fidelity > 100:
            raise StyleCalibrationError("target_fidelity must be between 0 and 100.")
    for index, variant in enumerate(variants, 1):
        if not isinstance(variant, dict):
            raise StyleCalibrationError(f"Strategy variant {index} must be an object.")
        required_variant_fields = {"label", "changes", "prompt_focus", "reference_route"}
        if version >= 2:
            required_variant_fields.update({
                "role", "expected_delta_from_baseline_percent", "diagnostic_negative_elements",
                "full_style_prompt", "tested_language_elements", "prompt_hypothesis",
            })
        require_fields(variant, required_variant_fields, f"Variant {index}")
        require_nonempty_list(variant["changes"], f"variants[{index}].changes")
        label = str(variant["label"]).strip()
        if not label or label in labels:
            raise StyleCalibrationError("Every strategy variant needs one unique non-empty label.")
        labels.append(label)
        if version >= 2:
            full_prompt = validate_style_prompt(variant["full_style_prompt"], f"variants[{index}].full_style_prompt")
            if full_prompt in full_prompts:
                raise StyleCalibrationError("All four calibration variants require distinct full_style_prompt values.")
            full_prompts.append(full_prompt)
            require_nonempty_list(variant["tested_language_elements"], f"variants[{index}].tested_language_elements")
            if not str(variant["prompt_hypothesis"]).strip():
                raise StyleCalibrationError(f"variants[{index}].prompt_hypothesis cannot be blank.")
            role = str(variant["role"]).strip().upper()
            if role not in VARIANT_ROLES:
                raise StyleCalibrationError(
                    f"Variant {index} role must be one of: {', '.join(sorted(VARIANT_ROLES))}."
                )
            roles.append(role)
            negatives = variant["diagnostic_negative_elements"]
            if not isinstance(negatives, list):
                raise StyleCalibrationError(f"variants[{index}].diagnostic_negative_elements must be a list.")
            try:
                delta = float(variant["expected_delta_from_baseline_percent"])
            except (TypeError, ValueError) as error:
                raise StyleCalibrationError(
                    f"variants[{index}].expected_delta_from_baseline_percent must be numeric."
                ) from error
            baseline_label = str(comparison["baseline_label"]).strip()
            if label == baseline_label:
                if delta != 0:
                    raise StyleCalibrationError("The baseline variant must use expected delta 0.")
            elif delta < float(comparison["minimum_expected_delta_percent"]):
                raise StyleCalibrationError(
                    f"Every non-baseline variant must differ by at least "
                    f"{float(comparison['minimum_expected_delta_percent']):g}% from the baseline."
                )
            if role == "CHAOS_PROBE" and not any(str(item).strip() for item in negatives):
                raise StyleCalibrationError(
                    "CHAOS_PROBE requires at least one explicit safe wrong-style diagnostic element."
                )
    if version >= 2:
        baseline_label = str(comparison["baseline_label"]).strip()
        if baseline_label not in labels:
            raise StyleCalibrationError("comparison_contract.baseline_label must match one variant label.")
        if roles.count("TARGET") != 1:
            raise StyleCalibrationError("Every quartet requires exactly one TARGET variant.")
        if roles.count("CONTROL") != 1:
            raise StyleCalibrationError("Every quartet requires exactly one CONTROL variant.")
        if float(comparison["target_fidelity"]) >= 90 and roles.count("CHAOS_PROBE") < 1:
            raise StyleCalibrationError(
                "Calibration at 90-100% requires a CHAOS_PROBE with safe negative-style elements."
            )


def validate_style_analysis(analysis: dict[str, object]) -> None:
    require_fields(analysis, REQUIRED_STYLE_ANALYSIS_FIELDS, "Style analysis")
    if not str(analysis["source_scope"]).strip():
        raise StyleCalibrationError("Style analysis source_scope cannot be blank.")
    try:
        reviewed = int(analysis["reviewed_source_count"])
    except (TypeError, ValueError) as error:
        raise StyleCalibrationError("reviewed_source_count must be a positive integer.") from error
    if reviewed < 1:
        raise StyleCalibrationError("Style analysis must review at least one real source.")
    for field in (
        "invariant_core_features", "optional_variation", "forbidden_drift", "facial_construction",
    ):
        values = require_nonempty_list(analysis[field], field)
        if not all(str(item).strip() for item in values):
            raise StyleCalibrationError(f"{field} cannot contain blank values.")
    if not str(analysis["working_hypothesis"]).strip():
        raise StyleCalibrationError("working_hypothesis cannot be blank.")


def load_style_analysis(path: Path) -> dict[str, object]:
    analysis = read_json(path)
    validate_style_analysis(analysis)
    return analysis


def validate_state(state: dict[str, object]) -> None:
    required = {
        "schema_version", "calibration_id", "style_name", "target_rounds",
        "minimum_rounds", "batch_size", "status", "rounds", "next_required_action",
    }
    require_fields(state, required, "Calibration state")
    target = int(state["target_rounds"])
    if target < MIN_ROUNDS or target > DEFAULT_ROUNDS:
        raise StyleCalibrationError(f"target_rounds must be between {MIN_ROUNDS} and {DEFAULT_ROUNDS}.")
    if int(state["minimum_rounds"]) != MIN_ROUNDS:
        raise StyleCalibrationError(f"minimum_rounds must remain {MIN_ROUNDS}.")
    if int(state["batch_size"]) != BATCH_SIZE:
        raise StyleCalibrationError(f"batch_size must remain exactly {BATCH_SIZE}.")
    rounds = state["rounds"]
    if not isinstance(rounds, list) or len(rounds) > target:
        raise StyleCalibrationError("Invalid calibration rounds list.")
    if protocol_version(state) not in SUPPORTED_PROTOCOL_VERSIONS:
        raise StyleCalibrationError("Unsupported calibration protocol_version.")
    if protocol_version(state) >= 2:
        style_analysis = state.get("style_analysis")
        if not isinstance(style_analysis, dict):
            raise StyleCalibrationError("Protocol-v2 calibration requires evidence-based style_analysis.")
        validate_style_analysis(style_analysis)
    if protocol_version(state) >= 3 and int(state.get("arts_per_round", 0)) != ARTS_PER_ROUND:
        raise StyleCalibrationError("Protocol-v3 calibration requires exactly one composite art per round.")
    seen_faces: set[str] = set()
    for expected, item in enumerate(rounds, 1):
        if not isinstance(item, dict) or item.get("round") != expected:
            raise StyleCalibrationError("Calibration rounds must be sequential.")
        face_id = str(item.get("face_id", "")).strip().upper()
        if not face_id or face_id in seen_faces:
            raise StyleCalibrationError("Every quartet must use one new, unique face_id.")
        seen_faces.add(face_id)
        candidates = item.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != BATCH_SIZE:
            raise StyleCalibrationError(f"Round {expected} must contain exactly {BATCH_SIZE} candidates.")
        expected_ids = [f"R{expected:02d}_C{index:02d}" for index in range(1, BATCH_SIZE + 1)]
        if [candidate.get("candidate_id") for candidate in candidates] != expected_ids:
            raise StyleCalibrationError(f"Round {expected} candidate identifiers are invalid.")
        if protocol_version(state) >= 3:
            if [candidate.get("panel_label") for candidate in candidates] != list(PANEL_LABELS):
                raise StyleCalibrationError(f"Round {expected} panels must be labeled A, B, C, D.")
            populated = [candidate for candidate in candidates if candidate.get("image_path")]
            if populated:
                paths = {candidate.get("image_path") for candidate in populated}
                source_hashes = {candidate.get("source_art_sha256") for candidate in populated}
                if len(populated) != BATCH_SIZE or len(paths) != 1 or len(source_hashes) != 1 or "" in source_hashes:
                    raise StyleCalibrationError(
                        f"Round {expected} must source all four panels from one composite art."
                    )
        if protocol_version(state) >= 2 and item.get("status") in {"AWAITING_USER_FEEDBACK", "FEEDBACK_RECORDED", "ADAPTED"}:
            distinction = item.get("distinction_qa")
            if not isinstance(distinction, dict) or distinction.get("review_status") != "PASS":
                raise StyleCalibrationError(
                    f"Round {expected} cannot reach user feedback without passed distinction QA."
                )


def create_state(
    path: Path,
    *,
    calibration_id: str,
    style_name: str,
    style_analysis_path: Path,
    target_rounds: int = DEFAULT_ROUNDS,
    user_approved_reduced_rounds: bool = False,
) -> dict[str, object]:
    if path.exists():
        raise StyleCalibrationError(f"Refusing to overwrite calibration state: {path}")
    if target_rounds < MIN_ROUNDS or target_rounds > DEFAULT_ROUNDS:
        raise StyleCalibrationError(f"--rounds must be {MIN_ROUNDS}, 3, or {DEFAULT_ROUNDS}; default is {DEFAULT_ROUNDS}.")
    if target_rounds < DEFAULT_ROUNDS and not user_approved_reduced_rounds:
        raise StyleCalibrationError(
            "Four quartets are the default. Starting only two or three requires --user-approved-reduced-rounds."
        )
    now = iso_now()
    style_analysis = load_style_analysis(style_analysis_path)
    state: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "calibration_id": safe_token(calibration_id),
        "style_name": style_name.strip(),
        "target_rounds": target_rounds,
        "minimum_rounds": MIN_ROUNDS,
        "batch_size": BATCH_SIZE,
        "arts_per_round": ARTS_PER_ROUND,
        "reduced_rounds_user_approved": user_approved_reduced_rounds,
        "created_at": now,
        "updated_at": now,
        "status": "ACTIVE",
        "next_required_action": "OPEN_ROUND",
        "rounds": [],
        "final_conclusion": None,
        "style_analysis": {
            **style_analysis,
            "analysis_path": str(style_analysis_path.resolve()),
            "analysis_sha256": sha256(style_analysis_path),
        },
    }
    save_state(path, state)
    return state


def open_round(
    path: Path,
    *,
    face_id: str,
    face_description: str,
    strategy_path: Path,
) -> dict[str, object]:
    state = load_state(path)
    require_single_art_protocol(state)
    if state["status"] != "ACTIVE" or state["next_required_action"] != "OPEN_ROUND":
        raise StyleCalibrationError(f"Next required action is {state['next_required_action']}, not OPEN_ROUND.")
    rounds = state["rounds"]
    assert isinstance(rounds, list)
    if len(rounds) >= int(state["target_rounds"]):
        raise StyleCalibrationError("All target rounds are already open; finalize the calibration.")
    token = safe_token(face_id, "FACE")
    if any(str(item["face_id"]).upper() == token for item in rounds):
        raise StyleCalibrationError("Every quartet requires a new temporary subject; this subject was already used.")
    if not face_description.strip():
        raise StyleCalibrationError("--subject-description is required to lock the temporary subject for this quartet.")
    strategy = read_json(strategy_path)
    validate_strategy(strategy, version=protocol_version(state))
    number = len(rounds) + 1
    if protocol_version(state) >= 2 and number == 1:
        if str(strategy.get("parent_prompt_experiment_id", "")).strip() != "STYLE_ANALYSIS":
            raise StyleCalibrationError("The first round parent_prompt_experiment_id must be STYLE_ANALYSIS.")
    if protocol_version(state) >= 2 and number > 1:
        previous = rounds[-1]
        adaptation = previous.get("ai_adaptation")
        findings = adaptation.get("prompt_findings") if isinstance(adaptation, dict) else None
        next_experiment = findings.get("next_prompt_experiment") if isinstance(findings, dict) else None
        if not isinstance(next_experiment, dict):
            raise StyleCalibrationError("The previous round has no validated next prompt experiment.")
        expected_experiment = str(next_experiment.get("id", "")).strip()
        actual_experiment = str(strategy.get("prompt_experiment_id", "")).strip()
        expected_parent = str(previous.get("strategy", {}).get("prompt_experiment_id", "")).strip()
        actual_parent = str(strategy.get("parent_prompt_experiment_id", "")).strip()
        if actual_experiment != expected_experiment or actual_parent != expected_parent:
            raise StyleCalibrationError(
                "The next round must use the proposed next prompt experiment id, reference its parent experiment, "
                "and supply four new prompt hypotheses."
            )
        previous_core = {
            normalized_prompt(item)
            for item in findings.get("retained_core_features", [])
            if normalized_prompt(item)
        }
        retained = {
            normalized_prompt(item)
            for item in strategy.get("retained_prior_findings", [])
            if normalized_prompt(item)
        }
        superseded: dict[str, str] = {}
        for item in strategy.get("superseded_prior_findings", []):
            if not isinstance(item, dict):
                raise StyleCalibrationError("Each superseded_prior_findings item must be an object.")
            require_fields(item, {"finding", "user_correction_quote"}, "superseded_prior_findings item")
            finding = normalized_prompt(item["finding"])
            quote = normalized_prompt(item["user_correction_quote"])
            if not finding or not quote:
                raise StyleCalibrationError("Superseding a prior finding requires the finding and direct user correction quote.")
            superseded[finding] = quote
        missing = sorted(previous_core - retained - set(superseded))
        if missing:
            raise StyleCalibrationError(
                "Previously confirmed prompt findings cannot be dropped. Retain them or record the direct user correction: "
                + "; ".join(missing)
            )
    candidates = []
    for index, variant in enumerate(strategy["variants"], 1):
        candidates.append({
            "candidate_id": f"R{number:02d}_C{index:02d}",
            "panel_label": PANEL_LABELS[index - 1] if protocol_version(state) >= 3 else "",
            "variant": variant,
            "status": "PLANNED",
            "image_path": "",
            "image_sha256": "",
            "user_score": None,
            "user_rank": None,
        })
    rounds.append({
        "round": number,
        "face_id": token,
        "face_description": face_description.strip(),
        "strategy": strategy,
        "status": "AWAITING_CANDIDATES",
        "candidates": candidates,
        "feedback": None,
        "ai_adaptation": None,
        "distinction_qa": None,
        "rejected_candidate_sets": [],
        "quartet_art_path": "",
        "quartet_art_sha256": "",
    })
    state["next_required_action"] = "RECORD_CANDIDATES"
    save_state(path, state)
    return state


def parse_assignments(values: Iterable[str], label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        key = key.strip().upper()
        item = item.strip()
        if not separator or not key or not item:
            raise StyleCalibrationError(f"{label} must use ID=VALUE: {value}")
        if key in parsed:
            raise StyleCalibrationError(f"Duplicate {label} id: {key}")
        parsed[key] = item
    return parsed


def record_candidates(path: Path, *, round_number: int, assignments: Iterable[str]) -> dict[str, object]:
    state = load_state(path)
    require_single_art_protocol(state)
    if protocol_version(state) >= 3:
        raise StyleCalibrationError(
            "Protocol-v3 forbids four separate candidate files. Use record-quartet-art with one composite art."
        )
    current = round_by_number(state, round_number)
    if current["status"] != "AWAITING_CANDIDATES" or state["next_required_action"] != "RECORD_CANDIDATES":
        raise StyleCalibrationError("This round is not waiting for candidates.")
    parsed = parse_assignments(assignments, "candidate")
    expected = [item["candidate_id"] for item in current["candidates"]]
    if sorted(parsed) != sorted(expected):
        raise StyleCalibrationError("Record exactly the four planned candidate ids.")
    hashes: set[str] = set()
    for candidate in current["candidates"]:
        image = Path(parsed[candidate["candidate_id"]]).resolve()
        if not image.is_file():
            raise StyleCalibrationError(f"Candidate image does not exist: {image}")
        digest = sha256(image)
        if digest in hashes:
            raise StyleCalibrationError("All four candidate files must be distinct outputs.")
        hashes.add(digest)
        candidate.update({
            "status": "AWAITING_DISTINCTION_QA" if protocol_version(state) >= 2 else "READY_FOR_USER_FEEDBACK",
            "image_path": str(image),
            "image_sha256": digest,
        })
    if protocol_version(state) >= 2:
        current["status"] = "AWAITING_DISTINCTION_QA"
        current["distinction_qa"] = None
        state["next_required_action"] = "RECORD_DISTINCTION_QA"
    else:
        current["status"] = "AWAITING_USER_FEEDBACK"
        state["next_required_action"] = "SUBMIT_USER_FEEDBACK"
    save_state(path, state)
    return state


def record_quartet_art(path: Path, *, round_number: int, image_path: Path) -> dict[str, object]:
    state = load_state(path)
    require_single_art_protocol(state)
    if protocol_version(state) < 3:
        raise StyleCalibrationError("record-quartet-art requires calibration protocol version 3 or newer.")
    current = round_by_number(state, round_number)
    if current["status"] != "AWAITING_CANDIDATES" or state["next_required_action"] != "RECORD_CANDIDATES":
        raise StyleCalibrationError("This round is not waiting for a composite calibration art.")
    image = image_path.resolve()
    if not image.is_file():
        raise StyleCalibrationError(f"Composite calibration art does not exist: {image}")
    digest = sha256(image)
    current["quartet_art_path"] = str(image)
    current["quartet_art_sha256"] = digest
    for candidate in current["candidates"]:
        panel_label = str(candidate["panel_label"])
        candidate.update({
            "status": "AWAITING_DISTINCTION_QA",
            "image_path": str(image),
            "source_art_sha256": digest,
            "image_sha256": hashlib.sha256(f"{digest}|PANEL|{panel_label}".encode("utf-8")).hexdigest(),
        })
    current["status"] = "AWAITING_DISTINCTION_QA"
    current["distinction_qa"] = None
    state["next_required_action"] = "RECORD_DISTINCTION_QA"
    save_state(path, state)
    return state


def _pair_key(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left.strip().upper(), right.strip().upper())))


def record_distinction(path: Path, *, round_number: int, report_path: Path) -> dict[str, object]:
    state = load_state(path)
    require_single_art_protocol(state)
    if protocol_version(state) < 2:
        raise StyleCalibrationError("Distinction QA is available only for calibration protocol version 2.")
    current = round_by_number(state, round_number)
    if current["status"] != "AWAITING_DISTINCTION_QA" or state["next_required_action"] != "RECORD_DISTINCTION_QA":
        raise StyleCalibrationError("This round is not waiting for distinction QA.")
    report = read_json(report_path)
    require_fields(report, REQUIRED_DISTINCTION_FIELDS, "Distinction QA")
    candidate_ids = [str(item["candidate_id"]).upper() for item in current["candidates"]]
    expected_pairs = {
        _pair_key(candidate_ids[left], candidate_ids[right])
        for left in range(BATCH_SIZE)
        for right in range(left + 1, BATCH_SIZE)
    }
    pairwise = report["pairwise_checks"]
    if not isinstance(pairwise, list) or len(pairwise) != len(expected_pairs):
        raise StyleCalibrationError("Distinction QA must compare all six candidate pairs exactly once.")
    seen_pairs: set[tuple[str, str]] = set()
    visible_deltas: list[float] = []
    for index, item in enumerate(pairwise, 1):
        if not isinstance(item, dict):
            raise StyleCalibrationError(f"pairwise_checks[{index}] must be an object.")
        require_fields(
            item,
            {"left", "right", "visible_style_difference_percent", "observable_differences"},
            f"pairwise_checks[{index}]",
        )
        pair = _pair_key(str(item["left"]), str(item["right"]))
        if pair not in expected_pairs or pair in seen_pairs:
            raise StyleCalibrationError("Distinction QA pair ids are missing, duplicated, or invalid.")
        seen_pairs.add(pair)
        try:
            delta = float(item["visible_style_difference_percent"])
        except (TypeError, ValueError) as error:
            raise StyleCalibrationError("Visible style differences must be numeric percentages.") from error
        if delta < 0 or delta > 100:
            raise StyleCalibrationError("Visible style differences must be between 0 and 100.")
        visible_deltas.append(delta)
        require_nonempty_list(item["observable_differences"], f"pairwise_checks[{index}].observable_differences")
    if seen_pairs != expected_pairs:
        raise StyleCalibrationError("Distinction QA must cover every unordered candidate pair.")
    collapsed_pairs = report["collapsed_pairs"]
    if not isinstance(collapsed_pairs, list):
        raise StyleCalibrationError("collapsed_pairs must be a list.")
    for item in collapsed_pairs:
        if not isinstance(item, list) or len(item) != 2 or _pair_key(str(item[0]), str(item[1])) not in expected_pairs:
            raise StyleCalibrationError("collapsed_pairs contains an invalid candidate pair.")
    try:
        reported_minimum = float(report["minimum_visible_delta_percent"])
    except (TypeError, ValueError) as error:
        raise StyleCalibrationError("minimum_visible_delta_percent must be numeric.") from error
    actual_minimum = min(visible_deltas)
    if abs(reported_minimum - actual_minimum) > 0.01:
        raise StyleCalibrationError("minimum_visible_delta_percent must equal the minimum of all six pair checks.")
    normalized = {
        **report,
        "review_status": str(report["review_status"]).upper(),
        "same_identity_constants": str(report["same_identity_constants"]).upper(),
        "chaos_probe_visible": str(report["chaos_probe_visible"]).upper(),
        "decision": str(report["decision"]).upper(),
        "minimum_visible_delta_percent": actual_minimum,
        "recorded_at": iso_now(),
        "report_path": str(report_path.resolve()),
        "report_sha256": sha256(report_path),
    }
    passed = (
        normalized["review_status"] == "PASS"
        and normalized["same_identity_constants"] == "PASS"
        and normalized["chaos_probe_visible"] == "PASS"
        and normalized["decision"] == "SHOW_TO_USER"
        and not collapsed_pairs
        and actual_minimum >= MIN_VISUAL_DELTA_PERCENT
    )
    if normalized["review_status"] == "PASS" and not passed:
        raise StyleCalibrationError(
            "Distinction QA cannot PASS unless identity constants pass, the chaos probe is visible, "
            f"all pairs differ by at least {MIN_VISUAL_DELTA_PERCENT:g}%, and no pair collapsed."
        )
    if passed:
        current["distinction_qa"] = normalized
        for candidate in current["candidates"]:
            candidate["status"] = "READY_FOR_USER_FEEDBACK"
        current["status"] = "AWAITING_USER_FEEDBACK"
        state["next_required_action"] = "SUBMIT_USER_FEEDBACK"
    else:
        rejected_sets = current.setdefault("rejected_candidate_sets", [])
        assert isinstance(rejected_sets, list)
        rejected_sets.append({
            "attempt": len(rejected_sets) + 1,
            "candidates": copy.deepcopy(current["candidates"]),
            "quartet_art_path": current.get("quartet_art_path", ""),
            "quartet_art_sha256": current.get("quartet_art_sha256", ""),
            "distinction_qa": normalized,
            "rejected_at": iso_now(),
            "reason": "Calibration variants were not visibly distinct enough for user scoring.",
        })
        for candidate in current["candidates"]:
            candidate.update({
                "status": "PLANNED",
                "image_path": "",
                "image_sha256": "",
                "source_art_sha256": "",
                "user_score": None,
                "user_rank": None,
            })
        current["quartet_art_path"] = ""
        current["quartet_art_sha256"] = ""
        current["distinction_qa"] = normalized
        current["status"] = "AWAITING_CANDIDATES"
        state["next_required_action"] = "RECORD_CANDIDATES"
    save_state(path, state)
    return state


def submit_feedback(
    path: Path,
    *,
    round_number: int,
    mode: str,
    scores: Iterable[str] = (),
    ranking: str = "",
    comment: str = "",
) -> dict[str, object]:
    state = load_state(path)
    require_single_art_protocol(state)
    current = round_by_number(state, round_number)
    if current["status"] != "AWAITING_USER_FEEDBACK" or state["next_required_action"] != "SUBMIT_USER_FEEDBACK":
        raise StyleCalibrationError("This round is not waiting for user feedback.")
    mode = mode.upper()
    if mode not in FEEDBACK_MODES:
        raise StyleCalibrationError("Feedback mode must be PERCENT or MIN_TO_MAX.")
    candidate_ids = [item["candidate_id"] for item in current["candidates"]]
    normalized: dict[str, float]
    if mode == "PERCENT":
        parsed = parse_assignments(scores, "score")
        if sorted(parsed) != sorted(candidate_ids):
            raise StyleCalibrationError("Percentage feedback requires one score for every candidate.")
        normalized = {}
        for candidate_id, raw in parsed.items():
            try:
                value = float(raw)
            except ValueError as error:
                raise StyleCalibrationError(f"Invalid percentage for {candidate_id}: {raw}") from error
            if value < 0 or value > 100:
                raise StyleCalibrationError("Percentages must be between 0 and 100.")
            normalized[candidate_id] = value
        order = sorted(candidate_ids, key=lambda item: (normalized[item], item))
    else:
        order = [item.strip().upper() for item in ranking.split(",") if item.strip()]
        if len(order) != BATCH_SIZE or len(set(order)) != BATCH_SIZE or sorted(order) != sorted(candidate_ids):
            raise StyleCalibrationError("--ranking must contain all four candidate ids once, ordered minimum to maximum match.")
        steps = [0.0, 33.333, 66.667, 100.0]
        normalized = dict(zip(order, steps))
    rank_by_id = {candidate_id: index for index, candidate_id in enumerate(order, 1)}
    for candidate in current["candidates"]:
        candidate["user_score"] = normalized[candidate["candidate_id"]]
        candidate["user_rank"] = rank_by_id[candidate["candidate_id"]]
        candidate["status"] = "USER_SCORED"
    current["feedback"] = {
        "mode": mode,
        "order_min_to_max": order,
        "scores": normalized,
        "comment": comment.strip(),
        "recorded_at": iso_now(),
    }
    current["status"] = "FEEDBACK_RECORDED"
    if round_number == int(state["target_rounds"]):
        state["next_required_action"] = "FINALIZE"
    else:
        state["next_required_action"] = "RECORD_AI_ADAPTATION_OR_FINALIZE"
    save_state(path, state)
    return state


def record_adaptation(path: Path, *, round_number: int, analysis_path: Path) -> dict[str, object]:
    state = load_state(path)
    require_single_art_protocol(state)
    current = round_by_number(state, round_number)
    if current["status"] != "FEEDBACK_RECORDED" or state["next_required_action"] != "RECORD_AI_ADAPTATION_OR_FINALIZE":
        raise StyleCalibrationError("AI adaptation is not currently allowed for this round.")
    analysis = read_json(analysis_path)
    require_fields(analysis, REQUIRED_ADAPTATION_FIELDS, "AI adaptation")
    for field in ("observations", "preserve", "change", "drift_signals"):
        require_nonempty_list(analysis[field], field)
    if not str(analysis["next_round_strategy"]).strip():
        raise StyleCalibrationError("next_round_strategy cannot be blank.")
    if protocol_version(state) >= 2:
        prompt_findings = analysis.get("prompt_findings")
        if not isinstance(prompt_findings, dict):
            raise StyleCalibrationError("Protocol-v2 adaptation requires prompt_findings across all four prompts.")
        require_fields(prompt_findings, REQUIRED_PROMPT_FINDINGS_FIELDS, "prompt_findings")
        for field in ("cross_variant_findings", "retained_core_features", "removed_or_deemphasized_elements"):
            require_nonempty_list(prompt_findings[field], f"prompt_findings.{field}")
        rows = prompt_findings["variant_prompt_findings"]
        if not isinstance(rows, list) or len(rows) != BATCH_SIZE:
            raise StyleCalibrationError("variant_prompt_findings must analyze all four prompt variants.")
        expected_labels = {
            str(candidate.get("variant", {}).get("label", "")).strip()
            for candidate in current["candidates"]
        }
        found_labels: set[str] = set()
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise StyleCalibrationError(f"variant_prompt_findings[{index}] must be an object.")
            require_fields(
                row,
                {"label", "effective_elements", "harmful_or_neutral_elements", "construction_effect", "next_action"},
                f"variant_prompt_findings[{index}]",
            )
            label = str(row["label"]).strip()
            if label in found_labels:
                raise StyleCalibrationError("variant_prompt_findings labels must be unique.")
            found_labels.add(label)
            if not isinstance(row["effective_elements"], list) or not isinstance(row["harmful_or_neutral_elements"], list):
                raise StyleCalibrationError("Prompt finding element fields must be lists.")
            if not str(row["construction_effect"]).strip() or not str(row["next_action"]).strip():
                raise StyleCalibrationError("Every prompt finding needs construction_effect and next_action.")
        if found_labels != expected_labels:
            raise StyleCalibrationError("variant_prompt_findings must cover the four strategy labels exactly once.")
        next_experiment = prompt_findings["next_prompt_experiment"]
        if not isinstance(next_experiment, dict):
            raise StyleCalibrationError("next_prompt_experiment must be an object.")
        require_fields(next_experiment, {"id", "hypotheses"}, "next_prompt_experiment")
        if not str(next_experiment["id"]).strip():
            raise StyleCalibrationError("next_prompt_experiment.id cannot be blank.")
        require_nonempty_list(next_experiment["hypotheses"], "next_prompt_experiment.hypotheses")
    current["ai_adaptation"] = analysis
    current["status"] = "ADAPTED"
    state["next_required_action"] = "OPEN_ROUND"
    save_state(path, state)
    return state


def finalize(
    path: Path,
    *,
    conclusion_path: Path,
    user_approved_early_stop: bool = False,
) -> dict[str, object]:
    state = load_state(path)
    require_single_art_protocol(state)
    rounds = state["rounds"]
    assert isinstance(rounds, list)
    completed = [item for item in rounds if item["status"] in {"FEEDBACK_RECORDED", "ADAPTED"}]
    if len(completed) < MIN_ROUNDS:
        raise StyleCalibrationError(f"At least {MIN_ROUNDS} fully scored quartets are required.")
    target = int(state["target_rounds"])
    if len(completed) < target and not user_approved_early_stop:
        raise StyleCalibrationError(
            f"Preferred calibration requires {target} rounds; early finalization needs --user-approved-early-stop."
        )
    if completed[-1]["status"] not in {"FEEDBACK_RECORDED", "ADAPTED"}:
        raise StyleCalibrationError("The latest quartet needs user feedback before finalization.")
    conclusion = read_json(conclusion_path)
    required_conclusion = set(REQUIRED_CONCLUSION_FIELDS)
    if protocol_version(state) >= 2:
        required_conclusion.update(REQUIRED_V2_CONCLUSION_FIELDS)
    require_fields(conclusion, required_conclusion, "Final AI conclusion")
    for field in (
        "reliable_style_features", "identity_preservation_rules", "prompt_strategy",
        "reference_strategy", "drift_rejection_rules",
    ):
        require_nonempty_list(conclusion[field], field)
    if protocol_version(state) >= 2:
        final_prompt = validate_style_prompt(conclusion["final_style_prompt"], "final_style_prompt")
        require_nonempty_list(conclusion["excluded_style_noise"], "excluded_style_noise")
        vocabulary = conclusion["prompt_vocabulary"]
        if not isinstance(vocabulary, dict):
            raise StyleCalibrationError("prompt_vocabulary must be an object.")
        require_fields(
            vocabulary,
            {"preferred_terms", "conditional_synonyms", "harmful_or_neutral_terms", "helpful_constructions", "distracting_details"},
            "prompt_vocabulary",
        )
        for field in vocabulary:
            if not isinstance(vocabulary[field], list):
                raise StyleCalibrationError(f"prompt_vocabulary.{field} must be a list.")
        evidence = conclusion["prompt_evidence_by_round"]
        if not isinstance(evidence, list) or len(evidence) != len(completed):
            raise StyleCalibrationError("prompt_evidence_by_round must summarize every completed quartet.")
    summary = []
    for item in completed:
        best = max(item["candidates"], key=lambda candidate: float(candidate["user_score"]))
        worst = min(item["candidates"], key=lambda candidate: float(candidate["user_score"]))
        summary.append({
            "round": item["round"],
            "face_id": item["face_id"],
            "feedback_mode": item["feedback"]["mode"],
            "best_candidate": best["candidate_id"],
            "best_score": best["user_score"],
            "worst_candidate": worst["candidate_id"],
            "worst_score": worst["user_score"],
        })
    state["final_conclusion"] = {
        **conclusion,
        "round_summary": summary,
        "completed_rounds": len(completed),
        "early_stop": len(completed) < target,
        "early_stop_user_approved": user_approved_early_stop,
        "finalized_at": iso_now(),
    }
    state["status"] = "FINALIZED"
    state["next_required_action"] = "NONE"
    save_state(path, state)
    return state


def explicit_percentage_summary(state: dict[str, object]) -> dict[str, object]:
    """Summarize only percentages explicitly supplied by the user.

    MIN_TO_MAX feedback is deliberately excluded because its synthetic 100-point
    endpoint is a rank normalization, not a user claim of perfect fidelity.
    """

    explicit_scores: list[float] = []
    rounds = state.get("rounds", [])
    if isinstance(rounds, list):
        for item in rounds:
            if not isinstance(item, dict):
                continue
            feedback = item.get("feedback")
            if not isinstance(feedback, dict) or feedback.get("mode") != "PERCENT":
                continue
            scores = feedback.get("scores", {})
            if isinstance(scores, dict):
                for value in scores.values():
                    try:
                        explicit_scores.append(float(value))
                    except (TypeError, ValueError):
                        continue
    return {
        "maximum_explicit_user_percentage": max(explicit_scores) if explicit_scores else None,
        "user_confirmed_100": any(value >= 100 for value in explicit_scores),
        "explicit_percentage_count": len(explicit_scores),
    }


def apply_to_style(path: Path, *, style_pack: Path) -> dict[str, object]:
    """Persist a finalized request-local calibration as the active local profile."""

    state = load_state(path)
    if state.get("status") != "FINALIZED" or state.get("next_required_action") != "NONE":
        raise StyleCalibrationError("Only a finalized calibration can be applied to a style pack.")
    pack = style_pack.resolve()
    metadata_path = pack / ".style-pack.json"
    if not metadata_path.is_file():
        raise StyleCalibrationError(f"Style pack metadata does not exist: {metadata_path}")
    metadata = read_json(metadata_path)
    if str(metadata.get("style_name", "")).strip().casefold() != str(state["style_name"]).strip().casefold():
        raise StyleCalibrationError("Calibration belongs to a different style pack.")

    source = path.resolve()
    applied_at = iso_now()
    applied_state = copy.deepcopy(state)
    applied_state["style_application"] = {
        "status": "ACTIVE",
        "applied_at": applied_at,
        "style_pack": str(pack),
        "source_state_path": str(source),
        "source_state_sha256": sha256(source),
        **explicit_percentage_summary(state),
    }
    target_root = pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "CALIBRATIONS"
    archive_path = target_root / f"{safe_token(str(state['calibration_id']))}.json"
    active_path = target_root / "ACTIVE_STYLE_CALIBRATION.json"
    if archive_path.exists():
        existing = read_json(archive_path)
        existing_source = existing.get("style_application", {})
        existing_hash = existing_source.get("source_state_sha256", "") if isinstance(existing_source, dict) else ""
        if existing_hash != applied_state["style_application"]["source_state_sha256"]:
            raise StyleCalibrationError(f"Calibration archive already exists with different evidence: {archive_path}")
    else:
        write_json(archive_path, applied_state)
    write_json(active_path, applied_state)
    applied_state["applied_archive_path"] = str(archive_path)
    applied_state["active_calibration_path"] = str(active_path)
    return applied_state


def print_status(state: dict[str, object], path: Path) -> None:
    rounds = state["rounds"]
    assert isinstance(rounds, list)
    print(f"STATE={path.resolve()}")
    print(f"STYLE={state['style_name']}")
    print(f"STATUS={state['status']}")
    print(f"ROUNDS={len(rounds)}/{state['target_rounds']}")
    print(f"BATCH_SIZE={state['batch_size']}")
    print(f"NEXT_REQUIRED_ACTION={state['next_required_action']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage four-round calibration; each round is one 2x2 composite art with user feedback."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Create a calibration; defaults to four quartets.")
    start.add_argument("--state", type=Path, required=True)
    start.add_argument("--calibration-id", required=True)
    start.add_argument("--style-name", required=True)
    start.add_argument("--style-analysis-file", type=Path, required=True)
    start.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, choices=(2, 3, 4))
    start.add_argument("--user-approved-reduced-rounds", action="store_true")

    open_parser = subparsers.add_parser("open-round", help="Open the next quartet with a new temporary subject.")
    open_parser.add_argument("--state", type=Path, required=True)
    subject_id = open_parser.add_mutually_exclusive_group(required=True)
    subject_id.add_argument("--subject-id")
    subject_id.add_argument("--face-id", help=argparse.SUPPRESS)
    subject_description = open_parser.add_mutually_exclusive_group(required=True)
    subject_description.add_argument("--subject-description")
    subject_description.add_argument("--face-description", help=argparse.SUPPRESS)
    open_parser.add_argument("--strategy-file", type=Path, required=True)

    candidates = subparsers.add_parser(
        "record-candidates", help="Rejected legacy route retained only to explain why four files are forbidden."
    )
    candidates.add_argument("--state", type=Path, required=True)
    candidates.add_argument("--round", type=int, required=True)
    candidates.add_argument("--candidate", action="append", required=True, help="RNN_CNN=path; repeat four times.")

    quartet_art = subparsers.add_parser(
        "record-quartet-art",
        help="Record the one generated 2x2 art containing panels A, B, C, and D.",
    )
    quartet_art.add_argument("--state", type=Path, required=True)
    quartet_art.add_argument("--round", type=int, required=True)
    quartet_art.add_argument("--art", type=Path, required=True)

    distinction = subparsers.add_parser(
        "record-distinction",
        help="Record the mandatory visual-distance QA before showing a quartet to the user.",
    )
    distinction.add_argument("--state", type=Path, required=True)
    distinction.add_argument("--round", type=int, required=True)
    distinction.add_argument("--report-file", type=Path, required=True)

    feedback = subparsers.add_parser("submit-feedback", help="Record user percentages or minimum-to-maximum order.")
    feedback.add_argument("--state", type=Path, required=True)
    feedback.add_argument("--round", type=int, required=True)
    feedback.add_argument("--mode", required=True, choices=("PERCENT", "MIN_TO_MAX"))
    feedback.add_argument("--score", action="append", default=[], help="RNN_CNN=0..100; repeat four times for PERCENT.")
    feedback.add_argument("--ranking", default="", help="Four ids ordered minimum to maximum, comma-separated.")
    feedback.add_argument("--comment", default="")

    adaptation = subparsers.add_parser("record-adaptation", help="Record how feedback changes the next quartet.")
    adaptation.add_argument("--state", type=Path, required=True)
    adaptation.add_argument("--round", type=int, required=True)
    adaptation.add_argument("--analysis-file", type=Path, required=True)

    final_parser = subparsers.add_parser("finalize", help="Write the reusable final style-transfer conclusion.")
    final_parser.add_argument("--state", type=Path, required=True)
    final_parser.add_argument("--conclusion-file", type=Path, required=True)
    final_parser.add_argument("--user-approved-early-stop", action="store_true")

    apply_parser = subparsers.add_parser(
        "apply-to-style",
        help="Persist a finalized calibration as the active local style calibration.",
    )
    apply_parser.add_argument("--state", type=Path, required=True)
    apply_parser.add_argument("--style-pack", type=Path, required=True)

    status = subparsers.add_parser("status", help="Validate and print current calibration state.")
    status.add_argument("--state", type=Path, required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "start":
            state = create_state(
                args.state,
                calibration_id=args.calibration_id,
                style_name=args.style_name,
                style_analysis_path=args.style_analysis_file,
                target_rounds=args.rounds,
                user_approved_reduced_rounds=args.user_approved_reduced_rounds,
            )
        elif args.command == "open-round":
            state = open_round(
                args.state,
                face_id=args.subject_id or args.face_id,
                face_description=args.subject_description or args.face_description,
                strategy_path=args.strategy_file,
            )
        elif args.command == "record-candidates":
            state = record_candidates(args.state, round_number=args.round, assignments=args.candidate)
        elif args.command == "record-quartet-art":
            state = record_quartet_art(args.state, round_number=args.round, image_path=args.art)
        elif args.command == "record-distinction":
            state = record_distinction(
                args.state,
                round_number=args.round,
                report_path=args.report_file,
            )
        elif args.command == "submit-feedback":
            state = submit_feedback(
                args.state,
                round_number=args.round,
                mode=args.mode,
                scores=args.score,
                ranking=args.ranking,
                comment=args.comment,
            )
        elif args.command == "record-adaptation":
            state = record_adaptation(args.state, round_number=args.round, analysis_path=args.analysis_file)
        elif args.command == "finalize":
            state = finalize(
                args.state,
                conclusion_path=args.conclusion_file,
                user_approved_early_stop=args.user_approved_early_stop,
            )
        elif args.command == "apply-to-style":
            state = apply_to_style(args.state, style_pack=args.style_pack)
        else:
            state = load_state(args.state)
        print_status(state, args.state)
        return 0
    except StyleCalibrationError as error:
        print(f"CALIBRATION_ERROR={error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
