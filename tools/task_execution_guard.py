#!/usr/bin/env python3
"""Bound StoryArt task execution to the user's goal and visible deliverables.

The guard is intentionally small and deterministic.  It does not decide artistic
questions; it records the task contract and stops unbounded preparation, silent
scope expansion, and post-readiness drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


SCHEMA_VERSION = 1
DEFAULT_MAX_MINUTES_WITHOUT_EXECUTION = 20
DEFAULT_MAX_PREFLIGHT_ACTIONS = 12
DEFAULT_MAX_EXECUTION_MINUTES = 20
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
SWIMWEAR_RUNGS = ("EXTREME_MICRO", "BIKINI", "TWO_PIECE")
SWIMWEAR_TOPOLOGIES = (*SWIMWEAR_RUNGS, "SPORT_TOP", "ONE_PIECE", "SHORTS", "OTHER", "CUSTOM")
AUXILIARY_GENERATION_MARKERS = (
    "staging_only",
    "mannequin",
    "манекен",
    "blank base mesh",
    "gray dummy",
    "grey dummy",
    "technical dummy",
    "silhouette mask",
    "маска силуэта",
    "proportion plate",
    "таблица пропорций",
    "topology test frame",
    "тестовый кадр топологии",
    "service image",
    "служебное изображение",
)


class GuardError(RuntimeError):
    """Invalid guard state or forbidden transition."""


class GuardActionRequired(GuardError):
    """The task must produce the deliverable or report a blocker now."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_guard(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise GuardError(f"Execution guard does not exist: {path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GuardError(f"Cannot read execution guard: {error}") from error
    if state.get("schema_version") != SCHEMA_VERSION:
        raise GuardError(f"Unsupported execution guard schema: {state.get('schema_version')}")
    return state


def parse_invariant_assignments(items: Sequence[str], *, label: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for raw_item in items:
        raw = str(raw_item).strip()
        if "=" not in raw:
            raise GuardError(f"{label} must use NAME=VALUE: {raw_item}")
        raw_name, raw_value = raw.split("=", 1)
        name = raw_name.strip().casefold()
        value = raw_value.strip()
        if not name or not value:
            raise GuardError(f"{label} requires non-empty NAME and VALUE: {raw_item}")
        if name in assignments:
            raise GuardError(f"Duplicate {label}: {name}")
        assignments[name] = value
    return assignments


def validate_invariant_assertions(state: dict[str, object], assertions: Sequence[str]) -> dict[str, str]:
    locked = state.get("locked_invariants", {})
    if not isinstance(locked, dict):
        raise GuardError("Execution guard locked_invariants must be an object.")
    if not locked:
        return {}
    declared = parse_invariant_assignments(assertions, label="invariant assertion")
    missing = [name for name in locked if name not in declared]
    mismatched = [name for name, value in locked.items() if declared.get(name) != value]
    unexpected = [name for name in declared if name not in locked]
    if missing or mismatched or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if mismatched:
            details.append(
                "mismatched: " + ", ".join(
                    f"{name}={declared.get(name)!r}, expected {locked[name]!r}" for name in mismatched
                )
            )
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise GuardError(
            "Execution cannot silently change locked task invariants (" + "; ".join(details) + "). "
            "Preserve every invariant or record an explicit user-approved invariant change first."
        )
    return declared


def create_guard(
    path: Path,
    *,
    request_id: str,
    goal: str,
    deliverable: str,
    task_kind: str = "IMAGE_GENERATION",
    allowed_scope: Sequence[str] = (),
    required_stages: Sequence[str] = (),
    invariants: Sequence[str] = (),
    max_minutes_without_execution: int = DEFAULT_MAX_MINUTES_WITHOUT_EXECUTION,
    max_preflight_actions: int = DEFAULT_MAX_PREFLIGHT_ACTIONS,
    max_execution_minutes: int = DEFAULT_MAX_EXECUTION_MINUTES,
    now: datetime | None = None,
) -> dict[str, object]:
    if path.exists():
        raise GuardError(f"Execution guard already exists and will not be overwritten: {path}")
    if not request_id.strip() or not goal.strip() or not deliverable.strip():
        raise GuardError("request_id, goal, and deliverable are required.")
    if min(max_minutes_without_execution, max_preflight_actions, max_execution_minutes) <= 0:
        raise GuardError("All execution budgets must be positive.")
    normalized_stages: list[str] = []
    seen_stages: set[str] = set()
    for raw_stage in required_stages:
        stage = raw_stage.strip()
        if not stage:
            raise GuardError("Required stage names cannot be empty.")
        stage_key = stage.casefold()
        if stage_key in seen_stages:
            raise GuardError(f"Duplicate required stage: {stage}")
        seen_stages.add(stage_key)
        normalized_stages.append(stage)
    locked_invariants = parse_invariant_assignments(invariants, label="invariant")
    moment = now or utc_now()
    state: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id.strip(),
        "task_kind": task_kind.upper(),
        "goal_lock": goal.strip(),
        "primary_deliverable": deliverable.strip(),
        "locked_invariants": locked_invariants,
        "allowed_scope": [item for item in allowed_scope if item.strip()],
        "required_stages": [
            {
                "id": stage,
                "status": "PENDING",
                "completed_at": None,
                "evidence": [],
            }
            for stage in normalized_stages
        ],
        "started_at": iso_time(moment),
        "updated_at": iso_time(moment),
        "cycle_started_at": iso_time(moment),
        "cycle_paused_seconds": 0,
        "waiting_since": None,
        "execution_started_at": None,
        "active_attempt": None,
        "attempts": [],
        "available_results": [],
        "delivered_result_at": None,
        "latest_delivered_result_status": None,
        "task_revision": 0,
        "latest_delivered_attempt_id": None,
        "first_visible_result_at": None,
        "last_visible_result_at": None,
        "preflight_actions_in_cycle": 0,
        "scope_changes": [],
        "budgets": {
            "max_minutes_without_execution": max_minutes_without_execution,
            "max_preflight_actions": max_preflight_actions,
            "max_execution_minutes": max_execution_minutes,
        },
        "status": "ACTIVE",
        "phase": "PREFLIGHT",
        "next_required_action": "PREFLIGHT_OR_EXECUTION",
        "events": [{
            "at": iso_time(moment),
            "event": "STARTED",
            "summary": (
                "Explicit user request recorded: " + goal.strip()
                if task_kind.upper() == "USER_REQUESTED_IMAGE"
                else "Task contract locked before substantive work."
            ),
        }],
    }
    if task_kind.upper() == "USER_REQUESTED_IMAGE":
        state["explicit_user_request"] = goal.strip()
    atomic_write_json(path, state)
    return state


def required_stage_entries(state: dict[str, object]) -> list[dict[str, object]]:
    entries = state.get("required_stages", [])
    if not isinstance(entries, list):
        raise GuardError("Execution guard required_stages must be a list.")
    return entries


def pending_required_stages(state: dict[str, object]) -> list[str]:
    return [
        str(entry.get("id"))
        for entry in required_stage_entries(state)
        if entry.get("status") != "COMPLETED"
    ]


def normalize_stage_key(stage: str | None) -> str:
    value = str(stage or "").strip().casefold()
    return re.sub(r"^\d+_", "", value)


def find_required_stage(state: dict[str, object], stage: str | None) -> dict[str, object]:
    if not stage or not stage.strip():
        raise GuardError("This checkpoint requires --stage.")
    stage_key = normalize_stage_key(stage)
    matches: list[dict[str, object]] = []
    for entry in required_stage_entries(state):
        if normalize_stage_key(str(entry.get("id", ""))) == stage_key:
            matches.append(entry)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise GuardError(f"Ambiguous required-stage alias {stage!r}; use the exact configured id.")
    known = ", ".join(str(entry.get("id")) for entry in required_stage_entries(state)) or "none"
    raise GuardError(f"Unknown required stage {stage!r}. Configured stages: {known}")


def is_physique_stage(stage: str | None) -> bool:
    return normalize_stage_key(stage).upper().startswith("PHYSIQUE_")


def validate_image_execution_scope(
    state: dict[str, object],
    *,
    summary: str,
    output_contract: str | None,
    user_approved_extra_generation: bool,
    extra_generation_evidence: str,
) -> None:
    if state.get("task_kind") != "IMAGE_GENERATION":
        return
    contract = str(output_contract or "").upper()
    if contract not in {"REQUESTED_DELIVERABLE", "USER_REQUESTED_EXTRA"}:
        raise GuardError(
            "Image EXECUTION_STARTED requires --output-contract "
            "REQUESTED_DELIVERABLE|USER_REQUESTED_EXTRA. Every generator call must be bound to the user's task."
        )


    lowered = summary.casefold()
    marker = next((item for item in AUXILIARY_GENERATION_MARKERS if item in lowered), None)
    if contract == "USER_REQUESTED_EXTRA" and not user_approved_extra_generation:
        raise GuardError(
            "USER_REQUESTED_EXTRA requires --user-approved-extra-generation and the user's exact request."
        )
    if user_approved_extra_generation and contract != "USER_REQUESTED_EXTRA":
        raise GuardError("--user-approved-extra-generation requires --output-contract USER_REQUESTED_EXTRA.")
    if contract == "USER_REQUESTED_EXTRA" and not extra_generation_evidence.strip():
        raise GuardError(
            "An extra image generation requires --extra-generation-evidence with the user's direct request."
        )
    if marker and contract != "USER_REQUESTED_EXTRA":
        raise GuardError(
            "Unrequested auxiliary image generation is forbidden. The execution summary declares "
            f"{marker!r}, which is not a requested deliverable. Use deterministic non-generated QA, "
            "accept small natural variation, or obtain the user's direct request for that specific extra image."
        )



def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise GuardError(f"Cannot hash reference file {path}: {error}") from error
    return digest.hexdigest()


def _prompt_binding(plan: dict[str, object]) -> tuple[str, str]:
    risk = plan.get("risk_assessment")
    prompt = risk.get("prompt") if isinstance(risk, dict) else None
    if not isinstance(prompt, dict):
        raise GuardError("Executable REFERENCE_PLAN requires risk_assessment.prompt.text and text_sha256.")
    text = str(prompt.get("text", ""))
    digest = str(prompt.get("text_sha256", "")).lower()
    actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if not text.strip() or digest != actual:
        raise GuardError("REFERENCE_PLAN prompt text is empty or its text_sha256 does not match.")
    return text, digest


def _planned_placeholder_role(workflow: dict[str, object], source_stage_id: str) -> str:
    stages = workflow.get("stages", [])
    if isinstance(stages, list):
        for stage_row in stages:
            if not isinstance(stage_row, dict):
                continue
            for slot in stage_row.get("slots", []):
                if isinstance(slot, dict) and str(slot.get("path", "")) == f"<STAGE_OUTPUT:{source_stage_id}>":
                    role = str(slot.get("stage_role", "")).strip()
                    if role:
                        return role
    return {
        "01_FACE_IDENTITY": "FACE_IDENTITY_STAGE",
        "02_PHYSIQUE_FRONT": "PHYSIQUE_FRONT_STAGE",
        "03_PHYSIQUE_SIDE": "PHYSIQUE_SIDE_STAGE",
        "04_PHYSIQUE_BACK": "PHYSIQUE_BACK_STAGE",
    }.get(source_stage_id, source_stage_id)


def _validate_resolved_execution_slots(
    plan_path: Path,
    plan: dict[str, object],
    workflow: dict[str, object],
    stage_slots: list[dict[str, object]],
    execution_call: dict[str, object],
    request_id: str,
) -> None:
    if str(execution_call.get("request_id", "")) != request_id:
        raise GuardError("execution_call belongs to another request_id.")
    stage_outputs = execution_call.get("stage_output_bindings", [])
    if not isinstance(stage_outputs, list):
        raise GuardError("execution_call.stage_output_bindings must be a list.")
    outputs: dict[str, dict[str, object]] = {}
    for row in stage_outputs:
        if not isinstance(row, dict):
            raise GuardError("Invalid stage_output_bindings row.")
        stage_id = str(row.get("stage_id", ""))
        if not stage_id or stage_id in outputs:
            raise GuardError("Stage output bindings must have unique stage_id values.")
        if (
            row.get("request_id") != request_id
            or row.get("status") != "STAGING"
            or row.get("qa_passed") is not True
            or str(row.get("reference_plan", "")) != str(plan_path.resolve())
        ):
            raise GuardError(f"Stage output is not QA-passed STAGING for request {request_id}: {stage_id}.")
        source = Path(str(row.get("path", ""))).expanduser().resolve()
        expected_hash = str(row.get("sha256", "")).lower()
        if not source.is_file() or not expected_hash or file_sha256(source) != expected_hash:
            raise GuardError(f"Registered stage output path/hash is missing or changed: {stage_id}.")
        outputs[stage_id] = {**row, "path": str(source), "sha256": expected_hash}

    planned_real: dict[str, set[str]] = {}
    expected_generated: dict[str, set[str]] = {}
    expected_paths: dict[str, set[str]] = {}
    target_pack_slots: dict[str, tuple[dict[str, object], list[str]]] = {}
    required_output_ids: set[str] = set()
    for planned in stage_slots:
        source_path = str(planned.get("path", ""))
        roles = {str(role) for role in planned.get("active_roles", [])}
        stage_match = re.fullmatch(r"<STAGE_OUTPUT:([^<>]+)>", source_path)
        pack_match = re.fullmatch(r"<TARGETED_STAGE_PACK:([^<>]+)>", source_path)
        if stage_match:
            source_stage_id = stage_match.group(1)
            record = outputs.get(source_stage_id)
            if record is None or str(record.get("stage_id", "")) != source_stage_id:
                raise GuardError(f"A generated placeholder lacks an exact registered prior-stage binding: {source_stage_id}.")
            required_output_ids.add(source_stage_id)
            expected_generated.setdefault(str(record["sha256"]), set()).update(roles)
            expected_paths.setdefault(str(record["sha256"]), set()).add(str(Path(str(record["path"])).resolve()))
            continue
        if pack_match:
            sources = [value for value in pack_match.group(1).split("+") if value]
            if not sources or len(sources) != len(set(sources)):
                raise GuardError("Targeted pack placeholder has an invalid source stage list.")
            target_pack_slots[source_path] = (planned, sources)
            required_output_ids.update(sources)
            continue
        expected = str(planned.get("sha256", "")).lower()
        if not expected:
            raise GuardError(f"A non-generated planned slot has no SHA-256: {source_path}.")
        planned_real.setdefault(expected, set()).update(roles)
        expected_paths.setdefault(expected, set()).add(str(Path(source_path).expanduser().resolve()))

    actual: dict[str, set[str]] = {}
    actual_slots: dict[str, dict[str, object]] = {}
    resolved_slots = execution_call.get("slots", [])
    for slot in resolved_slots:
        if not isinstance(slot, dict):
            raise GuardError("execution_call contains an invalid resolved slot.")
        digest = str(slot.get("sha256", "")).lower()
        roles = {str(role) for role in slot.get("active_roles", [])}
        if not digest or not roles:
            raise GuardError("Resolved slots require a concrete SHA-256 and active_roles.")
        actual.setdefault(digest, set()).update(roles)
        actual_slots.setdefault(digest, slot)

    target_bindings = execution_call.get("targeted_pack_bindings", [])
    if not isinstance(target_bindings, list):
        raise GuardError("execution_call.targeted_pack_bindings must be a list.")
    binding_by_placeholder: dict[str, dict[str, object]] = {}
    for row in target_bindings:
        if not isinstance(row, dict):
            raise GuardError("Invalid targeted_pack_bindings row.")
        placeholder = str(row.get("placeholder", ""))
        if not placeholder or placeholder in binding_by_placeholder:
            raise GuardError("Targeted pack bindings require unique exact placeholder keys.")
        binding_by_placeholder[placeholder] = row

    for placeholder, (planned, source_ids) in target_pack_slots.items():
        planned_roles = {str(value) for value in planned.get("active_roles", [])}
        role = str(planned.get("stage_role", "")).strip()
        if not planned_roles:
            raise GuardError(f"Targeted pack placeholder has no planned active_roles: {placeholder}.")
        binding = binding_by_placeholder.get(placeholder)
        if not binding or binding.get("request_id") != request_id or str(binding.get("stage_id", "")) != str(execution_call.get("stage_id", "")):
            raise GuardError(f"Targeted pack has no exact request/stage placeholder binding: {placeholder}.")
        if binding.get("source_stage_ids") != source_ids:
            raise GuardError(f"Targeted pack binding sources differ from the planned placeholder: {placeholder}.")
        image_path = Path(str(binding.get("path", ""))).expanduser().resolve()
        manifest_path = Path(str(binding.get("manifest_path", ""))).expanduser().resolve()
        candidate = next((slot for slot in resolved_slots if isinstance(slot, dict)
                          and Path(str(slot.get("path", ""))).expanduser().resolve() == image_path
                          and Path(str(slot.get("manifest_path", ""))).expanduser().resolve() == manifest_path), None)
        if candidate is None or role not in set(str(value) for value in candidate.get("active_roles", [])):
            raise GuardError(f"Targeted pack binding does not match an exact resolved call slot: {placeholder}.")
        technical_dir = (plan_path.parent / "TECHNICAL_REFERENCES").resolve()
        if manifest_path.parent != technical_dir or image_path.parent != technical_dir or not manifest_path.is_file():
            raise GuardError("Targeted pack image and manifest must be request-local TECHNICAL_REFERENCES artifacts.")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise GuardError(f"Cannot read targeted pack manifest: {error}") from error
        output = manifest.get("output") if isinstance(manifest, dict) else None
        sources = manifest.get("sources") if isinstance(manifest, dict) else None
        if (
            not isinstance(manifest, dict)
            or manifest.get("asset_type") != "TARGETED_GENERATOR_COLLAGE"
            or manifest.get("generator_safe") is not True
            or manifest.get("request_id") != request_id
            or not isinstance(output, dict)
            or not isinstance(sources, list)
            or manifest_path.stem != image_path.stem
            or Path(str(output.get("path", ""))).name != image_path.name
            or str(output.get("sha256", "")).lower() != file_sha256(image_path)
            or str(candidate.get("sha256", "")).lower() != file_sha256(image_path)
            or str(binding.get("sha256", "")).lower() != file_sha256(image_path)
            or manifest.get("forbidden_transfer") != ["NEW_FACE_IDENTITY", "NEW_STYLE", "NEW_WARDROBE", "NEW_BACKGROUND"]
        ):
            raise GuardError("Targeted pack manifest does not bind this request and current image bytes.")
        expected_scope = [_planned_placeholder_role(workflow, source_id) for source_id in source_ids]
        expected_scope = list(dict.fromkeys([value for value in expected_scope if value] + ["MULTIVIEW_CONSISTENCY"]))
        if manifest.get("scope") != expected_scope:
            raise GuardError("Targeted pack scope differs from its exact planned source roles.")
        if len(sources) != len(source_ids):
            raise GuardError("Targeted pack provenance source count differs from its planned placeholder.")
        for source_id, record in zip(source_ids, sources):
            registered = outputs.get(source_id)
            if not isinstance(record, dict) or registered is None:
                raise GuardError(f"Targeted pack references an unregistered prior stage: {source_id}.")
            source_file = Path(str(record.get("path", ""))).resolve()
            if (
                Path(str(record.get("path", ""))).resolve() != Path(str(registered.get("path", ""))).resolve()
                or str(record.get("sha256", "")).lower() != str(registered.get("sha256", "")).lower()
                or not source_file.is_file()
                or file_sha256(source_file) != str(registered.get("sha256", "")).lower()
                or str(record.get("role", "")) != _planned_placeholder_role(workflow, source_id)
            ):
                raise GuardError(f"Targeted pack provenance does not match current QA-passed stage output: {source_id}.")
        if not planned_roles.issubset(set(str(value) for value in candidate.get("active_roles", []))):
            raise GuardError("Targeted pack resolved slot dropped its planned active_roles.")
        digest = str(candidate["sha256"]).lower()
        expected_generated.setdefault(digest, set()).update(planned_roles)
        expected_paths.setdefault(digest, set()).add(str(image_path.resolve()))
    if set(binding_by_placeholder) != set(target_pack_slots):
        raise GuardError("execution_call has extra or missing targeted pack bindings.")
    if set(outputs) != required_output_ids:
        raise GuardError("Stage output bindings include missing or unplanned stage IDs.")
    expected_hashes = set(planned_real) | set(expected_generated)
    if set(actual) != expected_hashes:
        raise GuardError("Resolved call contains omitted or unplanned source hashes.")
    expected_roles: dict[str, set[str]] = {}
    for source_map in (planned_real, expected_generated):
        for digest, roles in source_map.items():
            expected_roles.setdefault(digest, set()).update(roles)
    for digest, roles in expected_roles.items():
        if roles != actual.get(digest, set()):
            raise GuardError("Resolved call changed the exact planned role set for a physical source hash.")
        resolved_path = Path(str(actual_slots[digest].get("path", ""))).expanduser().resolve()
        if str(resolved_path) not in expected_paths.get(digest, set()):
            raise GuardError("Resolved call path does not correspond to a planned or registered physical source.")


def _validate_request_local_style_manifest(plan: dict[str, object], slots: list[object]) -> None:
    selected = plan.get("selected_references", {})
    references = selected.get("style", []) if isinstance(selected, dict) else []
    references = references if isinstance(references, list) else [references]
    request_local = [
        row for row in references
        if isinstance(row, dict) and row.get("status") == "REQUEST_LOCAL_STYLE_CANDIDATE"
    ]
    if not request_local:
        return
    try:
        from tools.style_pack_manager import has_positive_master_style_manifest_entry, make_paths
    except ImportError:
        try:
            from style_pack_manager import has_positive_master_style_manifest_entry, make_paths
        except ImportError as error:
            raise GuardError(f"Cannot load style manifest validator for request-local STYLE references: {error}") from error
    try:
        paths = make_paths(Path(str(plan.get("pack_path", ""))).resolve().parent, str(plan.get("style_name", "")))
    except (OSError, RuntimeError, ValueError) as error:
        raise GuardError(f"Cannot resolve the request-local STYLE pack: {error}") from error
    if not str(plan.get("pack_path", "")) or paths.pack.resolve() != Path(str(plan.get("pack_path", ""))).resolve():
        raise GuardError("Request-local STYLE plan has an invalid pack_path/style_name binding.")
    for reference in request_local:
        if (
            reference.get("reference_scope") != "CURRENT_REQUEST_ONLY"
            or reference.get("permanent_anchor") is not False
            or not reference.get("path")
        ):
            raise GuardError("Request-local STYLE reference has an invalid scope or anchor marker.")
        file = Path(str(reference["path"])).expanduser().resolve()
        slot = next(
            (
                item for item in slots
                if isinstance(item, dict)
                and item.get("path")
                and Path(str(item["path"])).expanduser().resolve() == file
            ),
            None,
        )
        if slot is None or "STYLE" not in {str(role).upper() for role in slot.get("active_roles", [])}:
            continue
        try:
            relative = file.relative_to(paths.pack.resolve())
        except ValueError as error:
            raise GuardError("Request-local STYLE reference escaped its selected style pack.") from error
        if not has_positive_master_style_manifest_entry(paths, relative):
            raise GuardError(f"Request-local STYLE reference is no longer positive and generator-safe in the manifest: {file}")

def validate_reference_plan(
    path_value: str | Path,
    stage: str | None = None,
    execution_call: dict[str, object] | None = None,
    request_id: str | None = None,
) -> dict[str, object]:
    """Validate and snapshot the exact executable prompt and its physical inputs."""
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise GuardError(f"Executable REFERENCE_PLAN does not exist: {path}")
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GuardError(f"Cannot read executable REFERENCE_PLAN: {error}") from error
    if not isinstance(plan, dict):
        raise GuardError("Executable REFERENCE_PLAN must be a JSON object.")
    if plan.get("gate_status") != "READY_FOR_GENERATION":
        raise GuardError("REFERENCE_PLAN gate_status must be READY_FOR_GENERATION.")
    if request_id and str(plan.get("request_id", "")) != request_id:
        raise GuardError("REFERENCE_PLAN belongs to another request_id.")
    if execution_call is None and isinstance(plan.get("execution_call"), dict):
        execution_call = plan["execution_call"]
    risk = plan.get("risk_assessment")
    prompt_info = risk.get("prompt") if isinstance(risk, dict) else None
    if execution_call is not None:
        prompt_info = execution_call.get("prompt")
        if not isinstance(prompt_info, dict):
            raise GuardError("execution_call requires prompt.text and prompt.text_sha256.")
        prompt_text = str(prompt_info.get("text", ""))
        prompt_hash = str(prompt_info.get("text_sha256", "")).lower()
        if not prompt_text.strip() or hashlib.sha256(prompt_text.encode("utf-8")).hexdigest() != prompt_hash:
            raise GuardError("execution_call prompt is empty or its text_sha256 does not match.")
        plan_prompt = risk.get("prompt") if isinstance(risk, dict) else None
        if isinstance(plan_prompt, dict) and str(plan_prompt.get("text_sha256", "")).lower() != prompt_hash:
            raise GuardError("execution_call prompt does not match the REFERENCE_PLAN prompt hash.")
        if isinstance(plan_prompt, str) and plan_prompt.strip() != prompt_text.strip():
            raise GuardError("execution_call prompt text does not match the REFERENCE_PLAN prompt.")
    else:
        plan_prompt, prompt_hash = _prompt_binding(plan)
        prompt_text = plan_prompt
    workflow = plan.get("generation_workflow")
    if not isinstance(workflow, dict):
        raise GuardError("REFERENCE_PLAN is missing generation_workflow.")
    mode = str(workflow.get("mode", "")).upper()
    if execution_call is not None:
        selected_stage = str(execution_call.get("stage_id") or stage or "SINGLE_PASS")
        if mode == "MULTI_STAGE" and not stage:
            raise GuardError("MULTI_STAGE readiness requires an explicit --stage.")
        if stage and normalize_stage_key(selected_stage) != normalize_stage_key(stage):
            raise GuardError("execution_call.stage_id must match the checkpoint --stage.")
        slots = execution_call.get("slots")
    elif mode == "SINGLE_PASS":
        selected_stage = "SINGLE_PASS"
        slots = workflow.get("slots")
    elif mode == "MULTI_STAGE":
        stages = workflow.get("stages")
        if not isinstance(stages, list) or not stages:
            raise GuardError("MULTI_STAGE plan has no executable stages.")
        requested = normalize_stage_key(stage)
        matches = [row for row in stages if isinstance(row, dict) and normalize_stage_key(str(row.get("stage_id", ""))) == requested]
        if not requested or len(matches) != 1:
            raise GuardError("MULTI_STAGE readiness requires --stage naming exactly one executable stage_id.")
        selected_stage = str(matches[0]["stage_id"])
        slots = matches[0].get("slots")
    else:
        raise GuardError("REFERENCE_PLAN generation_workflow.mode must be SINGLE_PASS or MULTI_STAGE.")
    if not isinstance(slots, list) or not slots:
        raise GuardError("Executable generation stage requires at least one physical reference slot.")
    _validate_request_local_style_manifest(plan, slots)
    planned_slots: list[dict[str, object]] | None = None
    if execution_call is not None:
        if mode == "SINGLE_PASS":
            planned_slots = workflow.get("slots") if isinstance(workflow.get("slots"), list) else None
        elif mode == "MULTI_STAGE":
            stages = workflow.get("stages")
            planned = next((row for row in stages if isinstance(row, dict) and normalize_stage_key(str(row.get("stage_id", ""))) == normalize_stage_key(selected_stage)), None)
            if planned is None:
                raise GuardError("execution_call.stage_id is not present in generation_workflow.stages.")
            planned_slots = planned.get("slots") if isinstance(planned.get("slots"), list) else None
        if planned_slots is None:
            raise GuardError("REFERENCE_PLAN has no planned slots for the selected execution call.")
        _validate_resolved_execution_slots(
            path, plan, workflow, planned_slots, execution_call,
            str(request_id or plan.get("request_id", "")),
        )
    bindings: list[dict[str, object]] = []
    for slot in slots:
        if not isinstance(slot, dict):
            raise GuardError("REFERENCE_PLAN contains an invalid reference slot.")
        if slot.get("generated_stage_output") or slot.get("planned_targeted_pack"):
            raise GuardError("Executable stage still has an unresolved generated placeholder; complete and register that stage first.")
        raw_path = str(slot.get("path", ""))
        roles = slot.get("active_roles")
        expected = str(slot.get("sha256", "")).lower()
        ref = Path(raw_path).expanduser().resolve()
        if not raw_path or not isinstance(roles, list) or not roles or not expected or not ref.is_file():
            raise GuardError("Each executable reference slot requires an existing path, sha256, and active_roles.")
        actual = file_sha256(ref)
        if actual != expected:
            raise GuardError(f"REFERENCE_PLAN source hash changed for {ref}.")
        bindings.append({"path": str(ref), "sha256": actual, "roles": sorted(str(role) for role in roles)})
    report = execution_call.get("risk_assessment") if execution_call is not None else None
    if not isinstance(report, dict):
        report = plan.get("risk_assessment")
    if not isinstance(report, dict):
        raise GuardError("Executable call requires an input-bound risk_assessment report.")
    try:
        from tools.generation_risk_assessor import RiskAssessmentError, validate_assessment
    except ImportError:
        try:
            from generation_risk_assessor import RiskAssessmentError, validate_assessment
        except ImportError as error:
            raise GuardError(f"Cannot load exact-call risk assessor: {error}") from error
    try:
        validate_assessment(
            report,
            prompt_text,
            [{"path": row["path"], "sha256": row["sha256"], "active_roles": row["roles"]} for row in bindings],
        )
    except RiskAssessmentError as error:
        raise GuardError(f"Exact-call risk assessment validation failed: {error}") from error
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "prompt_sha256": prompt_hash,
        "stage": selected_stage,
        "references": bindings,
    }
def swimwear_failures(state: dict[str, object], stage: str) -> set[str]:
    stage_key = normalize_stage_key(stage)
    return {
        str(event.get("swimwear_rung", "")).upper()
        for event in state.get("events", [])
        if event.get("event") == "ATTEMPT_REJECTED"
        and normalize_stage_key(str(event.get("stage", ""))) == stage_key
        and bool(event.get("rung_routes_exhausted"))
        and str(event.get("swimwear_rung", "")).upper() in SWIMWEAR_RUNGS
    }


def escalation_incident_key(
    state: dict[str, object], stage: str, qa_layer: str, *, include_current_rejection: bool = False
) -> str | None:
    """Identify a repeated failure separated by the latest explicit correction."""
    events = list(state.get("events", []))
    if not stage.strip() or not qa_layer.strip():
        return None
    def matches(row: dict[str, object]) -> bool:
        return (
            row.get("event") == "ATTEMPT_REJECTED"
            and normalize_stage_key(str(row.get("stage", ""))) == normalize_stage_key(stage)
            and str(row.get("qa_layer", "")).casefold() == qa_layer.casefold()
        )
    correction_index = next(
        (
            index
            for index in range(len(events) - 1, -1, -1)
            if events[index].get("event") == "USER_CORRECTION"
            and normalize_stage_key(str(events[index].get("corrects_stage", ""))) == normalize_stage_key(stage)
            and str(events[index].get("corrects_qa_layer", "")).casefold() == qa_layer.casefold()
            and events[index].get("corrects_attempt_at")
        ),
        -1,
    )
    if correction_index < 0:
        return None
    before_correction = [row for row in events[:correction_index] if matches(row)]
    if not any(str(row.get("at", "")) == str(events[correction_index]["corrects_attempt_at"]) for row in before_correction):
        return None
    after_correction = [
        row for row in events[correction_index + 1 :]
        if matches(row)
    ]
    if not before_correction or len(after_correction) + int(include_current_rejection) < 1:
        return None
    correction_at = str(events[correction_index].get("at", ""))
    return f"{correction_at}|{normalize_stage_key(stage)}|{qa_layer.casefold()}"


def validate_swimwear_execution_start(
    state: dict[str, object],
    *,
    stage: str | None,
    swimwear_rung: str | None,
    user_swimwear_override: bool,
    user_override_evidence: str,
) -> dict[str, object] | None:
    if not is_physique_stage(stage):
        return None
    entry = find_required_stage(state, stage)
    if entry.get("status") == "COMPLETED":
        raise GuardError(f"Cannot start another physique attempt for completed stage {entry.get('id')}.")
    rung = str(swimwear_rung or "").upper()
    if user_swimwear_override:
        if not user_override_evidence.strip():
            raise GuardError("A swimwear override requires --user-override-evidence with the user's direct instruction.")
        if rung not in (*SWIMWEAR_RUNGS, "CUSTOM"):
            raise GuardError("A user swimwear override still requires a declared rung or CUSTOM.")
    else:
        if rung not in SWIMWEAR_RUNGS:
            raise GuardError(
                "Adult physique execution requires --swimwear-rung EXTREME_MICRO|BIKINI|TWO_PIECE. "
                "Project defaults are authoritative unless the user directly overrides them."
            )
        failed = swimwear_failures(state, str(entry.get("id")))
        highest_allowed = 0
        if "EXTREME_MICRO" in failed:
            highest_allowed = 1
        if "BIKINI" in failed:
            if "EXTREME_MICRO" not in failed:
                raise GuardError("Recorded BIKINI failure is invalid without a prior EXTREME_MICRO failure.")
            highest_allowed = 2
        if SWIMWEAR_RUNGS.index(rung) > highest_allowed:
            required = SWIMWEAR_RUNGS[highest_allowed]
            raise GuardError(
                f"Swimwear ladder jump is forbidden for {entry.get('id')}: start or remain at {required}. "
                "Change prompts, real BODY candidates, reference combinations, and staging while keeping the same target. "
                "Advance only after ATTEMPT_REJECTED records --rung-routes-exhausted for the preceding rung."
            )
    return {
        "stage": str(entry.get("id")),
        "swimwear_rung": rung,
        "user_swimwear_override": bool(user_swimwear_override),
        "user_override_evidence": user_override_evidence.strip(),
    }


def validate_stage_evidence(state: dict[str, object], evidence: Sequence[str]) -> list[str]:
    evidence_paths = [str(Path(item).resolve()) for item in evidence]
    if state.get("task_kind") == "IMAGE_GENERATION":
        if not evidence_paths:
            raise GuardError("Image-generation stages require a real output path as evidence.")
        for item in evidence_paths:
            file = Path(item)
            if not file.is_file() or file.suffix.lower() not in IMAGE_EXTENSIONS:
                raise GuardError(f"Required-stage image evidence is missing or unsupported: {file}")
    return evidence_paths


def cycle_elapsed_seconds(state: dict[str, object], now: datetime) -> float:
    started = parse_time(str(state["cycle_started_at"]))
    elapsed = max(0.0, (now - started).total_seconds())
    paused = float(state.get("cycle_paused_seconds", 0))
    waiting_since = state.get("waiting_since")
    if waiting_since:
        paused += max(0.0, (now - parse_time(str(waiting_since))).total_seconds())
    return max(0.0, elapsed - paused)


def execution_elapsed_seconds(state: dict[str, object], now: datetime) -> float:
    started = state.get("execution_started_at")
    if not started:
        return 0.0
    return max(0.0, (now - parse_time(str(started))).total_seconds())


def append_event(state: dict[str, object], event: str, summary: str, now: datetime, **extra: object) -> None:
    events = list(state.get("events", []))
    record: dict[str, object] = {"at": iso_time(now), "event": event, "summary": summary.strip()}
    record.update(extra)
    events.append(record)
    state["events"] = events
    state["updated_at"] = iso_time(now)


def evaluate_limits(state: dict[str, object], now: datetime) -> str | None:
    if state.get("status") in {"BLOCKED", "COMPLETE"} or state.get("waiting_since"):
        return None
    budgets = state["budgets"]
    execution_started_at = state.get("execution_started_at")
    if execution_started_at:
        maximum = int(budgets["max_execution_minutes"]) * 60
        if execution_elapsed_seconds(state, now) >= maximum:
            return "Execution exceeded its silent wait budget; report the concrete blocker or visible result now."
        return None
    maximum_seconds = int(budgets["max_minutes_without_execution"]) * 60
    if cycle_elapsed_seconds(state, now) >= maximum_seconds:
        return "Preparation exceeded its time budget; start the primary execution or report a concrete blocker now."
    if int(state.get("preflight_actions_in_cycle", 0)) >= int(budgets["max_preflight_actions"]):
        return "Preparation exhausted its action budget; start the primary execution or report a concrete blocker now."
    return None


def persist_watchdog_state(path: Path, state: dict[str, object], reason: str, now: datetime) -> None:
    """Record a stalled cycle without turning the watchdog into a task cutoff."""
    if state.get("watchdog_reason") != reason:
        append_event(state, "WATCHDOG_TRIGGERED", reason, now)
    state["status"] = "ACTIVE"
    state["phase"] = "WATCHDOG"
    state["next_required_action"] = (
        "CHECK_EXECUTION_OR_VISIBLE_RESULT_OR_BLOCKER"
        if state.get("execution_started_at")
        else "READY_FOR_EXECUTION_OR_EXECUTION_STARTED_OR_BLOCKER"
    )
    state["watchdog_reason"] = reason
    state["watchdog_triggered_at"] = iso_time(now)
    state.pop("action_required_reason", None)
    atomic_write_json(path, state)


def assert_can_continue(path: Path, state: dict[str, object], now: datetime | None = None) -> None:
    moment = now or utc_now()
    reason = evaluate_limits(state, moment)
    if reason:
        persist_watchdog_state(path, state, reason, moment)
        raise GuardActionRequired(
            f"{reason} The task remains ACTIVE; stop additional preparation and continue through "
            "READY_FOR_EXECUTION, EXECUTION_STARTED, an execution-state check, or a concrete external blocker."
        )


def _matching_attempt(
    state: dict[str, object], attempt_id: str, *, allow_unknown: bool = False
) -> dict[str, object]:
    if not attempt_id.strip():
        raise GuardError("This checkpoint requires --attempt-id.")
    for attempt in reversed(state.get("attempts", [])):
        if attempt.get("attempt_id") == attempt_id:
            active = state.get("active_attempt")
            if isinstance(active, dict) and active.get("attempt_id") == attempt_id:
                state["active_attempt"] = attempt
            return attempt
    raise GuardError(f"Unknown attempt_id: {attempt_id}")


def validate_current_stage_output_authority(
    reference_plan: str | Path,
    execution_call: dict[str, object] | None,
    request_id: str,
) -> None:
    """Ensure resolved stage inputs still match the latest QA-passed manifest rows."""
    plan_path = Path(reference_plan).expanduser().resolve()
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GuardError(f"Cannot re-read generation plan for stage authority: {error}") from error
    if not isinstance(plan, dict):
        raise GuardError("Generation plan for stage authority must be a JSON object.")
    call = execution_call
    if call is None and isinstance(plan.get("execution_call"), dict):
        call = plan["execution_call"]
    if not isinstance(call, dict):
        return
    bindings = call.get("stage_output_bindings", [])
    slots = call.get("slots", [])
    if not isinstance(bindings, list):
        raise GuardError("execution_call.stage_output_bindings must be a list.")
    if not isinstance(slots, list):
        raise GuardError("execution_call.slots must be a list.")
    workflow = plan.get("generation_workflow", {})
    multi_stage = isinstance(workflow, dict) and str(workflow.get("mode", "")).upper() == "MULTI_STAGE"
    if not bindings and not slots and not multi_stage:
        return
    style_name = str(plan.get("style_name", "")).strip()
    if not style_name and not bindings and not multi_stage:
        # Legacy unit fixtures without a project manifest have no body-library or stage authority to recheck.
        return
    if not style_name or len(plan_path.parents) < 4:
        raise GuardError("Cannot resolve the style manifest for current stage output authority.")
    try:
        from tools import style_pack_manager as manager
    except ImportError:
        import style_pack_manager as manager
    try:
        paths = manager.make_paths(plan_path.parents[3], style_name)
        latest_outputs = manager.validated_stage_outputs(paths, plan_path, plan)
        for slot in slots:
            if not isinstance(slot, dict):
                raise GuardError("Invalid execution_call slot.")
            file = Path(str(slot.get("path", ""))).expanduser().resolve()
            if manager.is_relative_to(file, (paths.workspace / manager.BODY_LIBRARY_NAME).resolve()):
                manager.validate_resolved_slot_compatibility(paths, plan, slot, file)
    except (OSError, ValueError, manager.StylePackError) as error:
        raise GuardError(f"Cannot verify current execution input authority: {error}") from error
    if multi_stage:
        stages = workflow.get("stages", [])
        stage_ids = [str(item.get("stage_id", "")) for item in stages if isinstance(item, dict)] if isinstance(stages, list) else []
        selected_stage = str(call.get("stage_id", ""))
        if selected_stage not in stage_ids:
            raise GuardError(f"Current execution stage is missing from the generation workflow: {selected_stage}.")
        for required_stage in stage_ids[:stage_ids.index(selected_stage)]:
            if required_stage not in latest_outputs:
                raise GuardError(
                    f"Current generation manifest no longer authorizes required prior stage output: {required_stage}."
                )
    for row in bindings:
        if not isinstance(row, dict):
            raise GuardError("Invalid stage_output_bindings row.")
        stage_id = str(row.get("stage_id", ""))
        latest = latest_outputs.get(stage_id)
        expected_path = str(Path(str(row.get("path", ""))).expanduser().resolve())
        if (
            not isinstance(latest, dict)
            or latest.get("request_id") != request_id
            or latest.get("status") != "STAGING"
            or latest.get("qa_passed") is not True
            or latest.get("path") != expected_path
            or str(latest.get("sha256", "")).lower() != str(row.get("sha256", "")).lower()
        ):
            raise GuardError(
                f"Current generation manifest no longer authorizes the bound stage output: {stage_id}."
            )


def checkpoint(
    path: Path,
    *,
    event: str,
    summary: str,
    evidence: Sequence[str] = (),
    stage: str | None = None,
    user_approved_scope_change: bool = False,
    correction_impact: str | None = None,
    invariant_assertions: Sequence[str] = (),
    invariant_changes: Sequence[str] = (),
    user_approved_invariant_change: bool = False,
    invariant_change_evidence: str = "",
    hard_blocker: bool = False,
    safe_routes_exhausted: bool = False,
    user_decision_essential: bool = False,
    swimwear_rung: str | None = None,
    observed_topology: str | None = None,
    user_swimwear_override: bool = False,
    user_override_evidence: str = "",
    rung_routes_exhausted: bool = False,
    output_contract: str | None = "REQUESTED_DELIVERABLE",
    user_approved_extra_generation: bool = False,
    extra_generation_evidence: str = "",
    qa_layer: str = "",
    reference_plan: str | Path | None = None,
    execution_call: dict[str, object] | None = None,
    attempt_id: str | None = None,
    result_status: str | None = None,
    delivery_evidence: Sequence[str] = (),
    outcome: str | None = None,
    reconciliation_evidence: str = "",
    provider_operation_receipt: str = "",
    now: datetime | None = None,
) -> dict[str, object]:
    state = load_guard(path)
    moment = now or utc_now()
    event = event.upper()
    if state.get("task_kind") != "IMAGE_GENERATION" and not attempt_id and isinstance(state.get("active_attempt"), dict):
        attempt_id = str(state["active_attempt"].get("attempt_id", ""))
    if not summary.strip():
        raise GuardError("Every checkpoint requires a short factual summary.")
    asserted_invariants: dict[str, str] = {}
    applied_invariant_changes: dict[str, dict[str, str | None]] = {}
    correction_binding: dict[str, str] = {}

    if event == "WAITING_FOR_USER":
        if state.get("status") in {"BLOCKED", "COMPLETE", "STOPPED", "CANCELLED"}:
            raise GuardError(f"Cannot enter WAITING_FOR_USER from status {state.get('status')}.")
        if state.get("active_attempt"):
            raise GuardActionRequired("An active attempt must produce a result, refusal, stop, or timeout reconciliation before waiting.")
        if any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
            raise GuardActionRequired("Reconcile the UNKNOWN attempt before asking the user or pausing the task.")
        if state.get("waiting_since"):
            raise GuardError("The guard is already waiting for the user.")
        if (
            state.get("task_kind") == "IMAGE_GENERATION"
            and pending_required_stages(state)
            and (state.get("first_visible_result_at") or state.get("execution_started_at"))
            and not (user_decision_essential and safe_routes_exhausted)
        ):
            raise GuardActionRequired(
                "Waiting for the user cannot replace continued execution while required image stages remain. "
                "Continue an approved safe route, or declare both --user-decision-essential and "
                "--safe-routes-exhausted for a genuinely unavoidable decision."
            )
        state["waiting_since"] = iso_time(moment)
        state["status"] = "WAITING_FOR_USER"
        state["phase"] = "WAITING_FOR_USER"
        state["next_required_action"] = "USER_RESUMED"
    elif event == "USER_RESUMED":
        waiting_since = state.get("waiting_since")
        if state.get("status") in {"STOPPED", "CANCELLED"}:
            if any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
                raise GuardActionRequired("Reconcile every UNKNOWN attempt before USER_RESUMED.")
            state["cycle_started_at"] = iso_time(moment)
            state["cycle_paused_seconds"] = 0
        elif waiting_since:
            paused = max(0.0, (moment - parse_time(str(waiting_since))).total_seconds())
            state["cycle_paused_seconds"] = int(state.get("cycle_paused_seconds", 0) + paused)
        else:
            raise GuardError("USER_RESUMED requires a prior WAITING_FOR_USER, STOP, or CANCEL checkpoint.")
        state["waiting_since"] = None
        state["status"] = "ACTIVE"
        state["phase"] = "PREFLIGHT"
        state["next_required_action"] = "PREFLIGHT_OR_EXECUTION"
    elif event == "READY_FOR_EXECUTION":
        if state.get("status") in {"BLOCKED", "COMPLETE", "WAITING_FOR_USER", "STOPPED", "CANCELLED"} or state.get("waiting_since"):
            raise GuardError(f"READY_FOR_EXECUTION is forbidden from status {state.get('status')}.")
        if state.get("active_attempt") or any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
            raise GuardActionRequired("Reconcile the active or UNKNOWN attempt before another readiness checkpoint.")
        if state.get("next_required_action") == "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER":
            raise GuardError("Duplicate READY_FOR_EXECUTION is forbidden; validate or start the existing ready call.")
        if state.get("next_required_action") not in {
            "PREFLIGHT_OR_EXECUTION", "NEXT_SAFE_EXECUTION", "NEXT_SAFE_EXECUTION_OR_COMPLETE",
            "NEXT_EXECUTION_OR_COMPLETE_OR_BLOCKER", "NEXT_EXECUTION_OR_BLOCKER", "NEXT_EXECUTION_OR_COMPLETE",
            "READY_FOR_EXECUTION_OR_EXECUTION_STARTED_OR_BLOCKER",
        }:
            raise GuardError("READY_FOR_EXECUTION is not the next allowed transition.")
        if state.get("task_kind") == "IMAGE_GENERATION":
            if not reference_plan:
                raise GuardError("Image READY_FOR_EXECUTION requires --reference-plan.")
            binding = validate_reference_plan(reference_plan, stage, execution_call, str(state.get("request_id", "")))
            state["ready_binding"] = binding
        state["status"] = "READY"
        state["phase"] = "READY_FOR_EXECUTION"
        state["next_required_action"] = "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER"
        state.pop("watchdog_reason", None)
        state.pop("watchdog_triggered_at", None)
    elif event in {"STOP", "CANCEL"}:
        if event == "STOP":
            active = state.get("active_attempt")
            if isinstance(active, dict):
                active = _matching_attempt(state, str(active.get("attempt_id", "")))
                active["status"] = "UNKNOWN"
                active["stop_recorded_at"] = iso_time(moment)
                active["stop_summary"] = summary.strip()
                state["active_attempt"] = None
            unknown_exists = any(a.get("status") == "UNKNOWN" for a in state.get("attempts", []))
            state["status"] = "STOPPED"
            state["phase"] = "ATTEMPT_UNKNOWN" if unknown_exists else "STOPPED"
            state["next_required_action"] = "RECONCILE_UNKNOWN_ATTEMPT" if unknown_exists else "USER_RESUMED"
            state["execution_started_at"] = None
        else:
            if not reconciliation_evidence.strip():
                raise GuardError("CANCEL requires a durable operator cancellation receipt in reconciliation_evidence.")
            active = state.get("active_attempt")
            outstanding = active if isinstance(active, dict) else next(
                (a for a in reversed(state.get("attempts", [])) if a.get("status") == "UNKNOWN"), None
            )
            if outstanding is not None:
                if not attempt_id or str(outstanding.get("attempt_id")) != str(attempt_id):
                    raise GuardError("CANCEL of an outstanding operation requires its exact --attempt-id.")
                active = _matching_attempt(state, str(attempt_id), allow_unknown=True)
                if active.get("status") not in {"ACTIVE", "UNKNOWN"}:
                    raise GuardError("CANCEL requires the currently active or UNKNOWN attempt.")
                active["status"] = "CANCELLED"
                active["cancel_receipt"] = reconciliation_evidence.strip()
            elif attempt_id:
                raise GuardError("CANCEL cannot target an old or already resolved attempt.")
            state["active_attempt"] = None
            state["status"] = "CANCELLED"
            state["phase"] = "CANCELLED"
            state["next_required_action"] = "USER_DECISION"
    elif event == "SCOPE_CHANGE":
        if state.get("status") in {"WAITING_FOR_USER", "STOPPED", "CANCELLED"} or state.get("waiting_since"):
            raise GuardError("Resume the paused task explicitly before changing scope.")
        if not user_approved_scope_change:
            raise GuardError(
                "Scope expansion is forbidden inside the active task without explicit user approval. "
                "Report the blocker instead of editing project infrastructure."
            )
        changes = list(state.get("scope_changes", []))
        changes.append({"at": iso_time(moment), "summary": summary.strip()})
        requested_invariant_changes = parse_invariant_assignments(
            invariant_changes,
            label="invariant change",
        )
        if requested_invariant_changes:
            if not user_approved_invariant_change or not invariant_change_evidence.strip():
                raise GuardError(
                    "Changing or establishing task invariants requires --user-approved-invariant-change "
                    "and an exact --invariant-change-evidence quote."
                )
            locked = dict(state.get("locked_invariants", {}))
            previous_values = {name: locked.get(name) for name in requested_invariant_changes}
            locked.update(requested_invariant_changes)
            state["locked_invariants"] = locked
            changes[-1]["invariant_changes"] = {
                name: {"from": previous_values[name], "to": value}
                for name, value in requested_invariant_changes.items()
            }
            changes[-1]["invariant_change_evidence"] = invariant_change_evidence.strip()
        state["scope_changes"] = changes
        if state.get("status") == "BLOCKED":
            state["status"] = "ACTIVE"
            state["phase"] = "PREFLIGHT"
            state["next_required_action"] = "PREFLIGHT_OR_EXECUTION"
            state["cycle_started_at"] = iso_time(moment)
            state["cycle_paused_seconds"] = 0
            state["execution_started_at"] = None
            state["preflight_actions_in_cycle"] = 0
            state.pop("blocker", None)
    elif event == "PREFLIGHT":
        if state.get("next_required_action") in {
            "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER",
            "EXECUTION_STARTED_OR_BLOCKER",
            "READY_FOR_EXECUTION_OR_EXECUTION_STARTED_OR_BLOCKER",
            "CHECK_EXECUTION_OR_VISIBLE_RESULT_OR_BLOCKER",
        }:
            raise GuardActionRequired(
                "The task is ready or its watchdog fired; more preparation is forbidden, but the task remains active. "
                "Continue through the next execution transition or report a concrete external blocker."
            )
        assert_can_continue(path, state, moment)
        state["preflight_actions_in_cycle"] = int(state.get("preflight_actions_in_cycle", 0)) + 1
        state["phase"] = "PREFLIGHT"
    elif event == "CALL_VALIDATED":
        if state.get("next_required_action") != "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER":
            raise GuardError("CALL_VALIDATED is allowed exactly once after READY_FOR_EXECUTION.")
        state["status"] = "READY"
        state["phase"] = "CALL_VALIDATED"
        state["next_required_action"] = "EXECUTION_STARTED_OR_BLOCKER"
    elif event == "EXECUTION_STARTED":
        if state.get("status") in {"BLOCKED", "COMPLETE", "WAITING_FOR_USER", "STOPPED", "CANCELLED"} or state.get("waiting_since"):
            raise GuardError(f"Cannot start execution from status {state.get('status')}.")
        if state.get("active_attempt"):
            raise GuardError("A duplicate in-flight EXECUTION_STARTED is forbidden.")
        if state.get("next_required_action") == "ESCALATION_ORCHESTRATOR_REQUIRED":
            raise GuardActionRequired(
                "Two same-layer failures after an explicit correction require one read-only ESCALATION_ORCHESTRATOR result before another attempt."
            )
        if state.get("task_kind") == "IMAGE_GENERATION" and state.get("next_required_action") not in {"CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER", "EXECUTION_STARTED_OR_BLOCKER"}:
            raise GuardError("EXECUTION_STARTED requires one preceding READY_FOR_EXECUTION transition.")
        validate_image_execution_scope(
            state,
            summary=summary,
            output_contract=output_contract,
            user_approved_extra_generation=user_approved_extra_generation,
            extra_generation_evidence=extra_generation_evidence,
        )
        attempt_stage = stage
        if state.get("task_kind") == "IMAGE_GENERATION":
            if not reference_plan:
                raise GuardError("Image EXECUTION_STARTED requires --reference-plan.")
            binding = validate_reference_plan(reference_plan, stage, execution_call, str(state.get("request_id", "")))
            validate_current_stage_output_authority(reference_plan, execution_call, str(state.get("request_id", "")))
            if binding != state.get("ready_binding"):
                raise GuardError("Execution call differs from the ready prompt, plan, references, hashes, roles, or stage.")
            attempt_stage = str(binding.get("stage", ""))
        asserted_invariants = validate_invariant_assertions(state, invariant_assertions)
        active_swimwear_attempt = validate_swimwear_execution_start(
            state,
            stage=stage,
            swimwear_rung=swimwear_rung,
            user_swimwear_override=user_swimwear_override,
            user_override_evidence=user_override_evidence,
        )
        if active_swimwear_attempt:
            state["active_swimwear_attempt"] = active_swimwear_attempt
        new_attempt_id = str(attempt_id or uuid.uuid4())
        if any(row.get("attempt_id") == new_attempt_id for row in state.get("attempts", [])):
            raise GuardError("Duplicate attempt_id is forbidden.")
        record = {
            "attempt_id": new_attempt_id,
            "status": "ACTIVE",
            "started_at": iso_time(moment),
            "reference_binding": state.get("ready_binding"),
            "stage": attempt_stage,
            "task_revision": int(state.get("task_revision", 0)),
        }
        if provider_operation_receipt.strip():
            record["provider_operation_receipt"] = provider_operation_receipt.strip()
        state["attempts"] = [*state.get("attempts", []), record]
        state["active_attempt"] = record
        state.pop("ready_binding", None)
        state["status"] = "ACTIVE"
        state["phase"] = "EXECUTION"
        state["execution_started_at"] = iso_time(moment)
        state["next_required_action"] = "VISIBLE_RESULT_OR_BLOCKER"
        state.pop("watchdog_reason", None)
        state.pop("watchdog_triggered_at", None)
    elif event == "VISIBLE_RESULT":
        active = _matching_attempt(state, str(attempt_id or ""))
        if active.get("status") not in {"ACTIVE", "UNKNOWN"}:
            raise GuardError("VISIBLE_RESULT requires an active or UNKNOWN attempt.")
        late_after_stop = active.get("status") == "UNKNOWN" or state.get("status") == "STOPPED"
        evidence_paths = [str(Path(item).resolve()) for item in evidence]
        if state.get("task_kind") == "IMAGE_GENERATION":
            if not evidence_paths:
                raise GuardError("Image generation requires a real output path as visible-result evidence.")
            for item in evidence_paths:
                file = Path(item)
                if not file.is_file() or file.suffix.lower() not in IMAGE_EXTENSIONS:
                    raise GuardError(f"Visible image evidence is missing or unsupported: {file}")
        elif not evidence_paths and not summary.strip():
            raise GuardError("A visible result requires file evidence or a factual user-facing result summary.")
        status_value = str(result_status or "AVAILABLE").upper()
        if state.get("task_kind") == "IMAGE_GENERATION" and status_value not in {"TEST", "STAGING", "REJECTED"}:
            raise GuardError("Image result availability requires result_status TEST, STAGING, or REJECTED.")
        if late_after_stop:
            active["late_output"] = {"result_status": status_value, "evidence": evidence_paths, "available_at": iso_time(moment)}
        else:
            active["status"] = "RESULT_AVAILABLE"
            active["result_status"] = status_value
            active["result_evidence"] = evidence_paths
            active["result_available_at"] = iso_time(moment)
        state["available_results"] = [*state.get("available_results", []), {
            "attempt_id": active["attempt_id"], "status": status_value,
            "evidence": evidence_paths, "available_at": iso_time(moment), "late": late_after_stop,
        }]
        state["active_attempt"] = None
        timestamp = iso_time(moment)
        state["first_visible_result_at"] = state.get("first_visible_result_at") or timestamp
        state["last_visible_result_at"] = timestamp
        state["execution_started_at"] = None
        state["cycle_started_at"] = timestamp
        state["cycle_paused_seconds"] = 0
        state["preflight_actions_in_cycle"] = 0
        if not late_after_stop:
            state["status"] = "ACTIVE"
            state["phase"] = "RESULT_AVAILABLE"
            state["next_required_action"] = "NEXT_EXECUTION_OR_COMPLETE_OR_BLOCKER"
        else:
            state["phase"] = "LATE_RESULT_AVAILABLE"
            state["next_required_action"] = "RECONCILE_UNKNOWN_ATTEMPT"
        state.pop("watchdog_reason", None)
        state.pop("watchdog_triggered_at", None)
    elif event == "RESULT_DELIVERED":
        result = _matching_attempt(state, str(attempt_id or ""))
        if result.get("status") != "RESULT_AVAILABLE" or result.get("result_status") != "TEST":
            raise GuardError("RESULT_DELIVERED requires an available TEST result; STAGING and REJECTED cannot be delivered as final.")
        if int(result.get("task_revision", -1)) != int(state.get("task_revision", 0)):
            raise GuardError("A result from an earlier task revision cannot satisfy current delivery.")
        if not delivery_evidence or any(not str(item).strip() for item in delivery_evidence):
            raise GuardError("RESULT_DELIVERED requires a UI message/link receipt as --delivery-evidence.")
        result["status"] = "DELIVERED"
        result["delivered_at"] = iso_time(moment)
        result["delivery_evidence"] = [str(item).strip() for item in delivery_evidence]
        state["delivered_result_at"] = iso_time(moment)
        state["latest_delivered_result_status"] = result.get("result_status")
        state["latest_delivered_attempt_id"] = result["attempt_id"]
        state["phase"] = "RESULT_DELIVERED"
        state["next_required_action"] = "NEXT_EXECUTION_OR_COMPLETE_OR_BLOCKER"
    elif event == "ATTEMPT_UNKNOWN":
        active = _matching_attempt(state, str(attempt_id or ""))
        if active.get("status") != "ACTIVE":
            raise GuardError("ATTEMPT_UNKNOWN requires an active attempt.")
        active["status"] = "UNKNOWN"
        active["unknown_at"] = iso_time(moment)
        active["unknown_reason"] = summary.strip()
        state["active_attempt"] = None
        state["status"] = "ACTIVE"
        state["phase"] = "ATTEMPT_UNKNOWN"
        state["next_required_action"] = "RECONCILE_UNKNOWN_ATTEMPT"
        state["execution_started_at"] = None
    elif event == "ATTEMPT_RECONCILED":
        attempt = _matching_attempt(state, str(attempt_id or ""), allow_unknown=True)
        if attempt.get("status") != "UNKNOWN":
            raise GuardError("ATTEMPT_RECONCILED requires a timeout/stop attempt with UNKNOWN status.")
        resolved = str(outcome or "").upper()
        if resolved not in {"AVAILABLE", "REFUSED", "CANCELLED", "UNKNOWN"}:
            raise GuardError("ATTEMPT_RECONCILED outcome must be AVAILABLE, REFUSED, CANCELLED, or UNKNOWN.")
        if not reconciliation_evidence.strip():
            raise GuardError("ATTEMPT_RECONCILED requires durable provider/operator reconciliation evidence.")
        was_stopped = state.get("status") == "STOPPED"
        if resolved == "AVAILABLE":
            if not evidence or not result_status:
                raise GuardError("AVAILABLE reconciliation requires output evidence and explicit TEST/STAGING/REJECTED QA status.")
            if result_status.upper() not in {"TEST", "STAGING", "REJECTED"}:
                raise GuardError("Reconciled result_status must be TEST, STAGING, or REJECTED.")
            resolved_paths = [str(Path(item).resolve()) for item in evidence]
            resolved_hashes = [file_sha256(Path(item)) for item in resolved_paths]
            if state.get("task_kind") == "IMAGE_GENERATION" and any(
                not Path(item).is_file() or Path(item).suffix.lower() not in IMAGE_EXTENSIONS for item in resolved_paths
            ):
                raise GuardError("AVAILABLE reconciliation requires existing image output evidence.")
        else:
            resolved_paths = []
        attempt["reconciliation"] = {"outcome": resolved, "evidence": reconciliation_evidence.strip(), "at": iso_time(moment)}
        attempt["status"] = "UNKNOWN" if resolved == "UNKNOWN" else resolved
        state["status"] = "STOPPED" if resolved == "UNKNOWN" or was_stopped else "ACTIVE"
        state["phase"] = "ATTEMPT_UNKNOWN" if resolved == "UNKNOWN" else (f"ATTEMPT_{resolved}" if not was_stopped else "STOPPED")
        state["next_required_action"] = "RECONCILE_UNKNOWN_ATTEMPT" if resolved == "UNKNOWN" else ("USER_RESUMED" if was_stopped else "NEXT_SAFE_EXECUTION_OR_COMPLETE")
        if resolved == "AVAILABLE":
            state["available_results"] = [*state.get("available_results", []), {
                "attempt_id": attempt["attempt_id"], "status": result_status.upper(),
                "evidence": resolved_paths, "evidence_sha256": resolved_hashes,
                "available_at": iso_time(moment), "late": True,
            }]
            attempt["status"] = "RESULT_AVAILABLE"
            attempt["result_status"] = result_status.upper()
            attempt["result_evidence"] = resolved_paths
            state["active_attempt"] = None
        elif resolved in {"REFUSED", "CANCELLED"}:
            state["active_attempt"] = None
    elif event == "ATTEMPT_REFUSED":
        active = _matching_attempt(state, str(attempt_id or ""))
        current = state.get("active_attempt") or {}
        if current.get("attempt_id") != active.get("attempt_id") or active.get("status") != "ACTIVE":
            raise GuardError("ATTEMPT_REFUSED requires the current active attempt.")
        if not reconciliation_evidence.strip():
            raise GuardError("ATTEMPT_REFUSED requires provider/operator refusal receipt.")
        active["status"] = "REFUSED"
        active["refusal_receipt"] = reconciliation_evidence.strip()
        state["active_attempt"] = None
        state["execution_started_at"] = None
        state["phase"] = "ATTEMPT_REFUSED"
        state["next_required_action"] = "NEXT_SAFE_EXECUTION"
    elif event == "ATTEMPT_REJECTED":
        if state.get("status") in {"BLOCKED", "COMPLETE", "WAITING_FOR_USER", "STOPPED", "CANCELLED"}:
            raise GuardError(f"Cannot reject an attempt from status {state.get('status')}.")
        rejected_attempt = _matching_attempt(state, str(attempt_id or ""))
        if rejected_attempt.get("status") not in {"ACTIVE", "RESULT_AVAILABLE"}:
            raise GuardError("ATTEMPT_REJECTED requires the active attempt or its available result.")
        current = state.get("active_attempt") or {}
        if any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])) or (
            current and current.get("attempt_id") != rejected_attempt.get("attempt_id")
        ):
            raise GuardError("ATTEMPT_REJECTED cannot target an older result while another operation is outstanding.")
        if rejected_attempt.get("status") == "RESULT_AVAILABLE" and (
            not state.get("attempts") or state["attempts"][-1].get("attempt_id") != rejected_attempt.get("attempt_id")
        ):
            raise GuardError("ATTEMPT_REJECTED must target the latest available attempt.")
        rejection_kind = str(outcome or "QA_REJECTED").upper()
        if rejection_kind != "QA_REJECTED":
            raise GuardError("ATTEMPT_REJECTED records QA rejection only; use ATTEMPT_REFUSED or ATTEMPT_UNKNOWN for provider outcomes.")
        active_swimwear_attempt = state.get("active_swimwear_attempt")
        rejected_attempt["status"] = "REJECTED"
        rejected_attempt["rejection_summary"] = summary.strip()
        state["active_attempt"] = None
        if stage:
            find_required_stage(state, stage)
        if active_swimwear_attempt:
            expected_stage = str(active_swimwear_attempt.get("stage", ""))
            expected_rung = str(active_swimwear_attempt.get("swimwear_rung", ""))
            if not stage or normalize_stage_key(stage) != normalize_stage_key(expected_stage):
                raise GuardError(f"Rejected physique attempt must name --stage {expected_stage}.")
            if str(swimwear_rung or "").upper() != expected_rung:
                raise GuardError(f"Rejected physique attempt must name --swimwear-rung {expected_rung}.")
            if observed_topology and str(observed_topology).upper() not in SWIMWEAR_TOPOLOGIES:
                raise GuardError(f"Unsupported observed topology: {observed_topology}")
            state.pop("active_swimwear_attempt", None)
        state["execution_started_at"] = None
        state["cycle_started_at"] = iso_time(moment)
        state["cycle_paused_seconds"] = 0
        state["preflight_actions_in_cycle"] = 0
        state["status"] = "ACTIVE"
        # The current rejection is appended after this transition. It is the
        # second failure only when an earlier same-layer failure was addressed
        # through the latest explicit correction.
        incident = escalation_incident_key(
            state, str(stage or ""), qa_layer, include_current_rejection=True
        )
        incidents = list(state.get("escalation_incidents", []))
        prior = next((item for item in incidents if item.get("key") == incident), None) if incident else None
        if incident and prior is None:
            incidents.append({"key": incident, "stage": stage, "qa_layer": qa_layer, "status": "REQUIRED"})
            state["escalation_incidents"] = incidents
            state["phase"] = "ESCALATION_REQUIRED"
            state["next_required_action"] = "ESCALATION_ORCHESTRATOR_REQUIRED"
        else:
            state["phase"] = "ATTEMPT_REJECTED"
            state["next_required_action"] = "NEXT_SAFE_EXECUTION"
        state.pop("watchdog_reason", None)
        state.pop("watchdog_triggered_at", None)
    elif event == "ESCALATION_ORCHESTRATOR_RECORDED":
        incident = escalation_incident_key(state, str(stage or ""), qa_layer)
        if not incident or state.get("next_required_action") != "ESCALATION_ORCHESTRATOR_REQUIRED":
            raise GuardError("ESCALATION_ORCHESTRATOR_RECORDED requires the currently due corrected repeated-failure incident.")
        incidents = list(state.get("escalation_incidents", []))
        current = next((item for item in incidents if item.get("key") == incident), None)
        if current is None or current.get("status") not in {"REQUIRED", "CONSUMED"}:
            raise GuardError("This escalation incident is not pending.")
        if not evidence:
            raise GuardError("ESCALATION_ORCHESTRATOR_RECORDED requires evidence of the bounded work order.")
        current["status"] = "RECORDED"
        state["escalation_incidents"] = incidents
        state["phase"] = "ESCALATION_RECORDED"
        state["next_required_action"] = "NEXT_SAFE_EXECUTION"
    elif event == "USER_CORRECTION":
        if state.get("status") in {"WAITING_FOR_USER", "STOPPED", "CANCELLED"} or state.get("waiting_since"):
            raise GuardError("Resume the paused task explicitly before recording USER_CORRECTION.")
        if state.get("active_attempt"):
            raise GuardActionRequired("Resolve the active or UNKNOWN operation before applying a correction.")
        if any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
            raise GuardActionRequired("Reconcile every UNKNOWN operation before applying a correction.")
        locked = state.get("locked_invariants", {})
        impact = str(correction_impact or "").upper()
        if locked:
            if impact not in {"PRESERVE", "CHANGE", "AMBIGUOUS"}:
                raise GuardError(
                    "USER_CORRECTION must declare --correction-impact PRESERVE, CHANGE, or AMBIGUOUS "
                    "when task invariants are locked."
                )
            if impact == "AMBIGUOUS":
                raise GuardActionRequired(
                    "The correction has more than one plausible reading and may change a locked invariant. "
                    "Keep the previous invariant and ask the user before changing the task."
                )
            requested_invariant_changes = parse_invariant_assignments(
                invariant_changes,
                label="invariant change",
            )
            if impact == "PRESERVE" and requested_invariant_changes:
                raise GuardError("PRESERVE correction cannot include invariant changes.")
            if impact == "CHANGE":
                if not requested_invariant_changes:
                    raise GuardError("CHANGE correction requires at least one --invariant-change NAME=VALUE.")
                if not user_approved_invariant_change or not invariant_change_evidence.strip():
                    raise GuardError(
                        "Changing a locked invariant requires --user-approved-invariant-change and an exact "
                        "--invariant-change-evidence quote."
                    )
                current = dict(locked)
                for name, value in requested_invariant_changes.items():
                    applied_invariant_changes[name] = {"from": current.get(name), "to": value}
                current.update(requested_invariant_changes)
                state["locked_invariants"] = current
        # Only an explicit stage/layer correction tied to a preceding matching
        # rejection may satisfy the repeated-failure escalation gate. Generic,
        # unrelated, ambiguous, or pre-failure corrections remain valid task
        # history but cannot unlock the Astra route.
        if stage and qa_layer.strip():
            prior_rejection = next(
                (
                    row
                    for row in reversed(state.get("events", []))
                    if row.get("event") == "ATTEMPT_REJECTED"
                    and normalize_stage_key(str(row.get("stage", ""))) == normalize_stage_key(stage)
                    and str(row.get("qa_layer", "")).casefold() == qa_layer.casefold()
                ),
                None,
            )
            if prior_rejection:
                correction_binding = {
                    "corrects_attempt_at": str(prior_rejection.get("at", "")),
                    "corrects_stage": str(stage),
                    "corrects_qa_layer": qa_layer.strip().upper(),
                }
        waiting_since = state.get("waiting_since")
        if waiting_since:
            paused = max(0.0, (moment - parse_time(str(waiting_since))).total_seconds())
            state["cycle_paused_seconds"] = int(state.get("cycle_paused_seconds", 0) + paused)
        state["waiting_since"] = None
        state["execution_started_at"] = None
        state.pop("blocker", None)
        state.pop("action_required_reason", None)
        state.pop("watchdog_reason", None)
        state.pop("watchdog_triggered_at", None)
        state["status"] = "ACTIVE"
        state["phase"] = "CORRECTION"
        state["next_required_action"] = "NEXT_SAFE_EXECUTION_OR_COMPLETE"
        state["latest_delivered_result_status"] = None
        state["delivered_result_at"] = None
        state["task_revision"] = int(state.get("task_revision", 0)) + 1
    elif event == "STAGE_COMPLETED":
        if state.get("status") in {"BLOCKED", "COMPLETE", "WAITING_FOR_USER", "STOPPED", "CANCELLED"} or state.get("waiting_since"):
            raise GuardError(f"Cannot complete a required stage from status {state.get('status')}; explicitly resume first.")
        if state.get("active_attempt") or any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
            raise GuardActionRequired("Reconcile the outstanding operation before completing a stage.")
        entry = find_required_stage(state, stage)
        if entry.get("status") == "COMPLETED":
            raise GuardError(f"Required stage is already completed: {entry.get('id')}")
        evidence_paths = validate_stage_evidence(state, evidence)
        if is_physique_stage(str(entry.get("id"))):
            active_swimwear_attempt = state.get("active_swimwear_attempt")
            if not isinstance(active_swimwear_attempt, dict):
                raise GuardError("Physique stage completion requires a tracked swimwear execution attempt.")
            expected_rung = str(active_swimwear_attempt.get("swimwear_rung", ""))
            rung = str(swimwear_rung or "").upper()
            observed = str(observed_topology or "").upper()
            if rung != expected_rung:
                raise GuardError(f"Physique completion must use the active swimwear rung {expected_rung}.")
            if observed not in SWIMWEAR_TOPOLOGIES:
                raise GuardError("Physique completion requires --observed-topology from the controlled topology list.")
            if observed != rung:
                raise GuardError(
                    f"Clothing topology hard gate failed: requested {rung}, observed {observed}. "
                    "Record ATTEMPT_REJECTED instead; the image cannot complete the physique stage."
                )
            state.pop("active_swimwear_attempt", None)
        entry["status"] = "COMPLETED"
        entry["completed_at"] = iso_time(moment)
        entry["evidence"] = evidence_paths
        state["status"] = "ACTIVE"
        state["phase"] = "STAGE_COMPLETED"
        state["next_required_action"] = (
            "NEXT_EXECUTION_OR_BLOCKER" if pending_required_stages(state) else "COMPLETE_OR_BLOCKER"
        )
    elif event == "STAGE_REOPENED":
        if state.get("status") in {"BLOCKED", "COMPLETE", "WAITING_FOR_USER", "STOPPED", "CANCELLED"} or state.get("waiting_since"):
            raise GuardError(f"Cannot reopen a required stage from status {state.get('status')}; explicitly resume first.")
        if state.get("active_attempt") or any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
            raise GuardActionRequired("Reconcile the outstanding operation before reopening a stage.")
        entry = find_required_stage(state, stage)
        if entry.get("status") != "COMPLETED":
            raise GuardError(f"Required stage is not completed and cannot be reopened: {entry.get('id')}")
        entry["status"] = "PENDING"
        entry["completed_at"] = None
        entry["evidence"] = []
        if is_physique_stage(str(entry.get("id"))):
            state.pop("active_swimwear_attempt", None)
            events = [
                row for row in state.get("events", [])
                if not (
                    row.get("event") == "ATTEMPT_REJECTED"
                    and normalize_stage_key(str(row.get("stage", ""))) == normalize_stage_key(str(entry.get("id", "")))
                    and row.get("swimwear_rung")
                )
            ]
            state["events"] = events
        state["status"] = "ACTIVE"
        state["phase"] = "CORRECTION"
        state["next_required_action"] = "NEXT_EXECUTION_OR_BLOCKER"
    elif event == "BLOCKER":
        if state.get("active_attempt") or any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
            raise GuardActionRequired("Record STOP/timeout and reconcile the active operation before declaring a blocker.")
        pending = pending_required_stages(state)
        if state.get("task_kind") == "IMAGE_GENERATION" and pending:
            if not (hard_blocker and safe_routes_exhausted):
                raise GuardActionRequired(
                    "A multi-stage image task cannot become BLOCKED after an ordinary rejected attempt while "
                    "required stages remain pending. Record ATTEMPT_REJECTED and continue an approved safe route. "
                    "A terminal blocker requires both --hard-blocker and --safe-routes-exhausted. Pending: "
                    + ", ".join(pending)
                )
        state["status"] = "BLOCKED"
        state["phase"] = "BLOCKED"
        state["next_required_action"] = "USER_DECISION"
        state["blocker"] = summary.strip()
    elif event == "COMPLETE":
        if state.get("status") in {"WAITING_FOR_USER", "STOPPED", "CANCELLED"} or state.get("waiting_since"):
            raise GuardError("A waiting, stopped, or cancelled task cannot complete.")
        if state.get("active_attempt") or any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
            raise GuardActionRequired("Task completion requires no active or UNKNOWN attempt; reconcile it first.")
        pending = pending_required_stages(state)
        if pending:
            raise GuardActionRequired(
                "Task completion is forbidden while required stages remain pending: " + ", ".join(pending)
            )
        if state.get("task_kind") == "IMAGE_GENERATION":
            latest = next((row for row in state.get("attempts", []) if row.get("attempt_id") == state.get("latest_delivered_attempt_id")), None)
            most_recent = state.get("attempts", [])[-1] if state.get("attempts") else None
            if not latest or not most_recent or latest.get("attempt_id") != most_recent.get("attempt_id"):
                raise GuardError("Image completion requires the latest attempt to be the currently delivered TEST result.")
            if latest and int(latest.get("task_revision", -1)) != int(state.get("task_revision", 0)):
                raise GuardError("The delivered result belongs to an earlier task revision; produce and deliver the corrected result.")
            if (
                state.get("latest_delivered_result_status") != "TEST"
                or not state.get("delivered_result_at")
                or not latest
                or latest.get("status") != "DELIVERED"
            ):
                raise GuardError("Image completion requires an available, QA-passed TEST result and RESULT_DELIVERED receipt.")
        state["status"] = "COMPLETE"
        state["phase"] = "COMPLETE"
        state["next_required_action"] = "NONE"
    else:
        raise GuardError(f"Unknown checkpoint event: {event}")

    event_details: dict[str, object] = {"evidence": list(evidence)}
    if stage:
        event_details["stage"] = stage
    if swimwear_rung:
        event_details["swimwear_rung"] = str(swimwear_rung).upper()
    if observed_topology:
        event_details["observed_topology"] = str(observed_topology).upper()
    if user_swimwear_override:
        event_details["user_swimwear_override"] = True
        event_details["user_override_evidence"] = user_override_evidence.strip()
    if event == "EXECUTION_STARTED" and user_approved_extra_generation:
        event_details["user_approved_extra_generation"] = True
        event_details["extra_generation_evidence"] = extra_generation_evidence.strip()
    if event == "EXECUTION_STARTED" and state.get("task_kind") == "IMAGE_GENERATION":
        event_details["output_contract"] = str(output_contract).upper()
    if event == "EXECUTION_STARTED" and asserted_invariants:
        event_details["invariant_assertions"] = asserted_invariants
    if event == "USER_CORRECTION" and state.get("locked_invariants"):
        event_details["correction_impact"] = str(correction_impact).upper()
        event_details["locked_invariants_after"] = dict(state["locked_invariants"])
        if applied_invariant_changes:
            event_details["invariant_changes"] = applied_invariant_changes
            event_details["invariant_change_evidence"] = invariant_change_evidence.strip()
    if event == "USER_CORRECTION" and correction_binding:
        event_details.update(correction_binding)
    if event == "ATTEMPT_REJECTED" and rung_routes_exhausted:
        if not is_physique_stage(stage) or not swimwear_rung:
            raise GuardError("--rung-routes-exhausted requires a physique --stage and --swimwear-rung.")
        event_details["rung_routes_exhausted"] = True
    if event in {"ATTEMPT_REJECTED", "ESCALATION_ORCHESTRATOR_RECORDED"} and qa_layer.strip():
        event_details["qa_layer"] = qa_layer.strip().upper()
    if event == "BLOCKER":
        event_details["hard_blocker"] = hard_blocker
        event_details["safe_routes_exhausted"] = safe_routes_exhausted
    if event == "WAITING_FOR_USER":
        event_details["user_decision_essential"] = user_decision_essential
        event_details["safe_routes_exhausted"] = safe_routes_exhausted
    if attempt_id:
        event_details["attempt_id"] = attempt_id
    if event == "EXECUTION_STARTED":
        event_details["attempt_id"] = str(attempt_id or state["active_attempt"]["attempt_id"])
        event_details["reference_binding"] = state["attempts"][-1].get("reference_binding")
    if event == "VISIBLE_RESULT":
        event_details["attempt_id"] = str(attempt_id)
        event_details["result_status"] = str(result_status or "AVAILABLE").upper()
    if event == "RESULT_DELIVERED":
        event_details["delivery_evidence"] = list(delivery_evidence)
    if event in {"ATTEMPT_UNKNOWN", "ATTEMPT_RECONCILED", "ATTEMPT_REFUSED", "STOP", "CANCEL"}:
        event_details["outcome"] = outcome or event
        if reconciliation_evidence:
            event_details["reconciliation_evidence"] = reconciliation_evidence.strip()
    if event == "ATTEMPT_REJECTED":
        event_details["attempt_id"] = str(attempt_id)
        event_details["outcome"] = "QA_REJECTED"
    append_event(state, event, summary, moment, **event_details)
    atomic_write_json(path, state)
    if event == "PREFLIGHT":
        reason = evaluate_limits(state, moment)
        if reason:
            persist_watchdog_state(path, state, reason, moment)
            raise GuardActionRequired(
                f"{reason} The task remains ACTIVE; stop additional preparation and continue through "
                "READY_FOR_EXECUTION, EXECUTION_STARTED, an execution-state check, or a concrete external blocker."
            )
    return state


def guard_status(path: Path, now: datetime | None = None) -> dict[str, object]:
    state = load_guard(path)
    moment = now or utc_now()
    reason = evaluate_limits(state, moment)
    if reason:
        persist_watchdog_state(path, state, reason, moment)
    return state


def require_active_guard(path: Path, request_id: str, now: datetime | None = None) -> dict[str, object]:
    state = load_guard(path)
    if state.get("request_id") != request_id:
        raise GuardError("Execution guard belongs to another request.")
    if state.get("status") in {"BLOCKED", "COMPLETE"}:
        raise GuardError(f"Execution guard status is {state.get('status')}.")
    moment = now or utc_now()
    reason = evaluate_limits(state, moment)
    if reason:
        # prepare-generation is the gateway out of preflight and must remain
        # available after the watchdog fires.  Only further PREFLIGHT is denied.
        persist_watchdog_state(path, state, reason, moment)
    return state


def require_execution_started(path: Path, request_id: str) -> dict[str, object]:
    state = load_guard(path)
    if state.get("request_id") != request_id:
        raise GuardError("Execution guard belongs to another request.")
    if not state.get("execution_started_at") or state.get("phase") != "EXECUTION":
        raise GuardError(
            "The primary execution was not declared. Record EXECUTION_STARTED immediately before the real generator call."
        )
    return state


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Keep a StoryArt task bounded by its original goal and visible output.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Lock the task goal, deliverable, scope, and execution budgets.")
    start.add_argument("--state", required=True)
    start.add_argument("--request-id", required=True)
    start.add_argument("--goal", required=True)
    start.add_argument("--deliverable", required=True)
    start.add_argument(
        "--task-kind",
        choices=("IMAGE_GENERATION", "IMAGE_EDIT", "USER_REQUESTED_IMAGE", "GENERAL"),
        default="IMAGE_GENERATION",
        help=(
            "USER_REQUESTED_IMAGE records the explicit request and permits a direct image operation without "
            "REFERENCE_PLAN, output-contract, local-file, or QA-receipt prerequisites."
        ),
    )
    start.add_argument("--allowed-scope", action="append", default=[])
    start.add_argument(
        "--invariant",
        action="append",
        default=[],
        help="Locked task invariant as NAME=VALUE. Repeat for camera, composition, setting, or other fixed facts.",
    )
    start.add_argument(
        "--required-stage",
        action="append",
        default=[],
        help="Mandatory deliverable stage. Repeat for every stage that must exist before COMPLETE.",
    )
    start.add_argument("--max-minutes-without-execution", type=int, default=DEFAULT_MAX_MINUTES_WITHOUT_EXECUTION)
    start.add_argument("--max-preflight-actions", type=int, default=DEFAULT_MAX_PREFLIGHT_ACTIONS)
    start.add_argument("--max-execution-minutes", type=int, default=DEFAULT_MAX_EXECUTION_MINUTES)

    check = subparsers.add_parser("checkpoint", help="Record progress and enforce the next allowed action.")
    check.add_argument("--state", required=True)
    check.add_argument(
        "--event",
        required=True,
        choices=(
            "PREFLIGHT", "WAITING_FOR_USER", "USER_RESUMED", "SCOPE_CHANGE",
            "READY_FOR_EXECUTION", "CALL_VALIDATED", "EXECUTION_STARTED", "VISIBLE_RESULT", "RESULT_DELIVERED",
            "ATTEMPT_REJECTED", "ATTEMPT_REFUSED", "ATTEMPT_UNKNOWN", "ATTEMPT_RECONCILED", "STOP", "CANCEL",
            "ESCALATION_ORCHESTRATOR_RECORDED", "USER_CORRECTION", "STAGE_COMPLETED", "STAGE_REOPENED",
            "BLOCKER", "COMPLETE",
        ),
    )
    check.add_argument("--summary", required=True)
    check.add_argument("--reference-plan", help="Executable REFERENCE_PLAN whose current prompt and source hashes are bound at readiness/start.")
    check.add_argument("--attempt-id", help="Durable provider/operator attempt identifier returned by EXECUTION_STARTED.")
    check.add_argument("--provider-operation-receipt", default="", help="Optional durable provider job/operation receipt.")
    check.add_argument("--result-status", choices=("TEST", "STAGING", "REJECTED"))
    check.add_argument("--outcome", help="Durable operation outcome for refusal, timeout reconciliation, or rejection.")
    check.add_argument("--delivery-evidence", action="append", default=[], help="Actual user-visible delivery receipt/link.")
    check.add_argument("--reconciliation-evidence", default="", help="Durable provider/operator receipt for timeout or cancellation reconciliation.")
    check.add_argument("--execution-call", help="JSON file containing this exact resolved call's prompt and slots; otherwise use REFERENCE_PLAN.execution_call.")
    check.add_argument("--evidence", action="append", default=[])
    check.add_argument("--stage", help="Required-stage id for STAGE_COMPLETED or STAGE_REOPENED.")
    check.add_argument("--qa-layer", default="", help="Failed QA layer; required to trigger corrected repeated-failure escalation.")
    check.add_argument("--user-approved-scope-change", action="store_true")
    check.add_argument("--correction-impact", choices=("PRESERVE", "CHANGE", "AMBIGUOUS"))
    check.add_argument(
        "--invariant-assert",
        action="append",
        default=[],
        help="Assert every locked NAME=VALUE immediately before execution.",
    )
    check.add_argument(
        "--invariant-change",
        action="append",
        default=[],
        help="Explicitly change or establish a locked invariant as NAME=VALUE.",
    )
    check.add_argument("--user-approved-invariant-change", action="store_true")
    check.add_argument(
        "--invariant-change-evidence",
        default="",
        help="Exact user quote that explicitly authorizes the invariant change.",
    )
    check.add_argument("--hard-blocker", action="store_true")
    check.add_argument("--safe-routes-exhausted", action="store_true")
    check.add_argument("--user-decision-essential", action="store_true")
    check.add_argument("--swimwear-rung", choices=(*SWIMWEAR_RUNGS, "CUSTOM"))
    check.add_argument("--observed-topology", choices=SWIMWEAR_TOPOLOGIES)
    check.add_argument("--user-swimwear-override", action="store_true")
    check.add_argument(
        "--output-contract",
        choices=("REQUESTED_DELIVERABLE", "USER_REQUESTED_EXTRA"),
        help="Bind an image-generator call to an original deliverable or a directly requested extra image.",
    )
    check.add_argument(
        "--user-approved-extra-generation",
        action="store_true",
        help="Allow a specifically user-requested auxiliary image that is not one of the original deliverables.",
    )
    check.add_argument(
        "--extra-generation-evidence",
        default="",
        help="Exact user request required for an auxiliary image generation.",
    )
    check.add_argument(
        "--rung-routes-exhausted",
        action="store_true",
        help="Unlock the next default rung only after every approved prompt, BODY, attachment, and staging route for the current target is exhausted.",
    )
    check.add_argument(
        "--user-override-evidence",
        default="",
        help="Exact user instruction required when overriding the project-default swimwear ladder.",
    )

    status = subparsers.add_parser("status", help="Show the current guard state and enforce elapsed-time limits.")
    status.add_argument("--state", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = make_parser().parse_args(argv)
    try:
        if args.command == "start":
            state = create_guard(
                Path(args.state),
                request_id=args.request_id,
                goal=args.goal,
                deliverable=args.deliverable,
                task_kind=args.task_kind,
                allowed_scope=args.allowed_scope,
                required_stages=args.required_stage,
                invariants=args.invariant,
                max_minutes_without_execution=args.max_minutes_without_execution,
                max_preflight_actions=args.max_preflight_actions,
                max_execution_minutes=args.max_execution_minutes,
            )
        elif args.command == "checkpoint":
            execution_call = None
            if args.execution_call:
                try:
                    execution_call = json.loads(Path(args.execution_call).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise GuardError(f"Cannot read execution_call JSON: {error}") from error
            state = checkpoint(
                Path(args.state),
                event=args.event,
                summary=args.summary,
                evidence=args.evidence,
                stage=args.stage,
                user_approved_scope_change=args.user_approved_scope_change,
                correction_impact=args.correction_impact,
                invariant_assertions=args.invariant_assert,
                invariant_changes=args.invariant_change,
                user_approved_invariant_change=args.user_approved_invariant_change,
                invariant_change_evidence=args.invariant_change_evidence,
                hard_blocker=args.hard_blocker,
                safe_routes_exhausted=args.safe_routes_exhausted,
                user_decision_essential=args.user_decision_essential,
                swimwear_rung=args.swimwear_rung,
                observed_topology=args.observed_topology,
                user_swimwear_override=args.user_swimwear_override,
                user_override_evidence=args.user_override_evidence,
                rung_routes_exhausted=args.rung_routes_exhausted,
                output_contract=args.output_contract,
                user_approved_extra_generation=args.user_approved_extra_generation,
                extra_generation_evidence=args.extra_generation_evidence,
                qa_layer=args.qa_layer,
                reference_plan=args.reference_plan,
                execution_call=execution_call,
                attempt_id=args.attempt_id,
                result_status=args.result_status,
                delivery_evidence=args.delivery_evidence,
                outcome=args.outcome,
                reconciliation_evidence=args.reconciliation_evidence,
                provider_operation_receipt=args.provider_operation_receipt,
            )
        else:
            state = guard_status(Path(args.state))
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 2 if state.get("status") == "ACTION_REQUIRED" else 0
    except GuardActionRequired as error:
        print(f"ACTION_REQUIRED={error}", file=sys.stderr)
        return 2
    except GuardError as error:
        print(f"GUARD_ERROR={error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
