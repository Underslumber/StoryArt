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
REPLACEMENT_AUTHORIZATION_QUESTION = (
    "\\u0414\\u043b\\u044f \\u0441\\u0442\\u0430\\u0440\\u043e\\u0433\\u043e \\u0437\\u0430\\u043f\\u0443\\u0441\\u043a\\u0430 \\u043d\\u0435\\u0442 \\u0434\\u043e\\u0441\\u0442\\u043e\\u0432\\u0435\\u0440\\u043d\\u043e\\u0439 \\u043a\\u0432\\u0438\\u0442\\u0430\\u043d\\u0446\\u0438\\u0438: \\u043e\\u043d \\u043e\\u0441\\u0442\\u0430\\u0451\\u0442\\u0441\\u044f UNKNOWN, \\u0438 \\u0431\\u0435\\u0437\\u043e\\u043f\\u0430\\u0441\\u043d\\u043e \\u043f\\u0440\\u043e\\u0434\\u043e\\u043b\\u0436\\u0438\\u0442\\u044c \\u0435\\u0433\\u043e \\u043d\\u0435\\u0432\\u043e\\u0437\\u043c\\u043e\\u0436\\u043d\\u043e. "
    "\\u0420\\u0430\\u0437\\u0440\\u0435\\u0448\\u0430\\u0435\\u0448\\u044c \\u043f\\u043e\\u0441\\u043b\\u0435 \\u043f\\u0440\\u043e\\u0432\\u0435\\u0440\\u043a\\u0438 \\u0438\\u0441\\u043f\\u0440\\u0430\\u0432\\u043b\\u0435\\u043d\\u0438\\u044f \\u043d\\u0430\\u0447\\u0430\\u0442\\u044c \\u043d\\u043e\\u0432\\u0443\\u044e \\u043f\\u043e\\u043f\\u044b\\u0442\\u043a\\u0443, \\u0441\\u043e\\u0445\\u0440\\u0430\\u043d\\u0438\\u0432 \\u0441\\u0442\\u0430\\u0440\\u0443\\u044e \\u0431\\u0435\\u0437 \\u0438\\u0437\\u043c\\u0435\\u043d\\u0435\\u043d\\u0438\\u0439? "
    "\\u0415\\u0441\\u0442\\u044c \\u043d\\u0435\\u0431\\u043e\\u043b\\u044c\\u0448\\u043e\\u0439 \\u0440\\u0438\\u0441\\u043a, \\u0447\\u0442\\u043e \\u0438\\u0441\\u0445\\u043e\\u0434\\u043d\\u044b\\u0439 \\u043f\\u0440\\u043e\\u043c\\u043f\\u0442 \\u0432\\u0441\\u0451 \\u0436\\u0435 \\u0431\\u044b\\u043b \\u043e\\u0442\\u043f\\u0440\\u0430\\u0432\\u043b\\u0435\\u043d; \\u043f\\u043e\\u0432\\u0442\\u043e\\u0440 \\u0431\\u0443\\u0434\\u0435\\u0442 \\u0442\\u043e\\u043b\\u044c\\u043a\\u043e \\u043f\\u043e \\u0442\\u0432\\u043e\\u0435\\u043c\\u0443 \\u044f\\u0432\\u043d\\u043e\\u043c\\u0443 \\u0440\\u0430\\u0437\\u0440\\u0435\\u0448\\u0435\\u043d\\u0438\\u044e."
)
REPLACEMENT_AUTHORIZATION_ANSWER = "\\u0420\\u0430\\u0437\\u0440\\u0435\\u0448\\u0430\\u044e \\u043d\\u043e\\u0432\\u0443\\u044e \\u043f\\u043e\\u043f\\u044b\\u0442\\u043a\\u0443 \\u043f\\u043e\\u0441\\u043b\\u0435 \\u0438\\u0441\\u043f\\u0440\\u0430\\u0432\\u043b\\u0435\\u043d\\u0438\\u044f"
REPLACEMENT_AUTHORIZATION_QUESTION = REPLACEMENT_AUTHORIZATION_QUESTION.encode("ascii").decode("unicode_escape")
REPLACEMENT_AUTHORIZATION_ANSWER = REPLACEMENT_AUTHORIZATION_ANSWER.encode("ascii").decode("unicode_escape")
REPLACEMENT_AUTHORIZATION_SOURCE = "Direct user structured reply in current chat"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
# Adult physique stages default to one plain two-piece swimsuit; other cuts need the user's direct instruction.
DEFAULT_SWIMWEAR_RUNG = "TWO_PIECE"
SWIMWEAR_OVERRIDE_RUNGS = ("BIKINI", "EXTREME_MICRO", "SPORT_TOP", "ONE_PIECE", "CUSTOM")
SWIMWEAR_TOPOLOGIES = (DEFAULT_SWIMWEAR_RUNG, "BIKINI", "EXTREME_MICRO", "SPORT_TOP", "ONE_PIECE", "SHORTS", "OTHER", "CUSTOM")
CHARACTER_BASE_STAGES = (
    "01_FACE_IDENTITY", "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE",
    "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY",
)
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


def _chat_request_root() -> Path:
    return Path(__file__).resolve().parent.parent / ".agent" / "chat_requests"


def _binding_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _guard_binding_path(path: Path) -> Path:
    return _folder_binding_path(path.resolve().parent)


def _folder_binding_path(folder: Path) -> Path:
    resolved = str(folder.resolve())
    return _chat_request_root() / "folders" / f"{_binding_key(resolved)}.json"


def _chat_binding_path(thread_id: str) -> Path:
    return _chat_request_root() / "chats" / f"{_binding_key(thread_id)}.json"


def _focus_binding_path(thread_id: str) -> Path:
    return _chat_request_root() / "focus" / f"{_binding_key(thread_id)}.json"


def _write_execution_focus(thread_id: str, path: Path, task_kind: str) -> None:
    atomic_write_json(
        _focus_binding_path(thread_id),
        {
            "thread_id": thread_id,
            "task_kind": task_kind,
            "guard_path": str(path.resolve()),
            "updated_at": iso_time(utc_now()),
        },
    )


def _read_binding(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GuardError(f"Chat request binding is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise GuardError("Chat request binding is invalid.")
    return value


def _bind_guard_to_current_chat(path: Path, task_kind: str) -> str | None:
    thread_id = os.environ.get("CODEX_THREAD_ID", "").strip()
    if not thread_id:
        return None
    resolved = str(path.resolve().parent)
    guard_binding = _guard_binding_path(path)
    if task_kind == "GENERAL":
        existing = _read_binding(guard_binding)
        if existing and existing.get("thread_id") != "GENERAL":
            raise GuardError("FOREIGN_REQUEST: this request folder is bound to another chat.")
        if existing is None:
            atomic_write_json(guard_binding, {"thread_id": "GENERAL", "guard_path": resolved, "task_kind": "GENERAL"})
        _write_execution_focus(thread_id, path, "GENERAL")
        return None
    chat_binding = _chat_binding_path(thread_id)
    existing_guard = _read_binding(guard_binding)
    if existing_guard and existing_guard.get("thread_id") != thread_id:
        raise GuardError("FOREIGN_REQUEST: this image request folder is bound to another chat. Create a new unique guard in the current chat.")
    existing_chat = _read_binding(chat_binding)
    replacing_completed = bool(
        existing_chat
        and existing_chat.get("active_folder") != resolved
        and existing_chat.get("request_status") == "COMPLETE"
    )
    if existing_chat and existing_chat.get("active_folder") != resolved and not replacing_completed:
        raise GuardError("FOREIGN_REQUEST: this chat already has a different active request folder. Continue that request or complete it before starting another.")
    if existing_chat is None or replacing_completed:
        atomic_write_json(chat_binding, {"thread_id": thread_id, "active_folder": resolved, "request_status": "ACTIVE"})
    if existing_guard is None:
        atomic_write_json(guard_binding, {"thread_id": thread_id, "active_folder": resolved, "task_kind": task_kind})
    _write_execution_focus(thread_id, path, task_kind)
    return thread_id


def _authorize_guard_path(path: Path) -> str | None:
    """Authorize by path registry before opening an image guard's JSON."""
    thread_id = os.environ.get("CODEX_THREAD_ID", "").strip()
    if not thread_id:
        return None
    chat_binding = _read_binding(_chat_binding_path(thread_id))
    resolved_path = path.resolve()
    if chat_binding:
        active_folder = Path(str(chat_binding.get("active_folder", ""))).resolve()
        try:
            resolved_path.relative_to(active_folder)
            inside_active_folder = True
        except ValueError:
            inside_active_folder = False
        if inside_active_folder:
            binding = _read_binding(_folder_binding_path(active_folder))
            if not binding or binding.get("thread_id") != thread_id:
                raise GuardError("FOREIGN_REQUEST: active request folder binding does not match the current chat.")
            return thread_id
    binding = _read_binding(_guard_binding_path(path))
    if binding and binding.get("thread_id") == "GENERAL" and binding.get("task_kind") == "GENERAL":
        return None
    if binding and binding.get("thread_id") != thread_id:
        raise GuardError("FOREIGN_REQUEST: this request folder belongs to another chat. Create a new unique guard in the current chat; do not inspect or reuse its outputs.")
    # The project task folder is an approved location for engineering guards
    # created before chat bindings were introduced. Image guards there still
    # fail closed after their kind is read below.
    approved_tasks = Path(__file__).resolve().parent.parent / ".agent" / "tasks"
    if not binding and resolved_path.parent == approved_tasks.resolve():
        return thread_id
    raise GuardError("UNBOUND_LEGACY_REQUEST: this guard has no trustworthy current-chat binding. Create a new unique guard in the current chat; do not reuse or reconcile its outputs.")


def assert_active_request_path(path: str | Path) -> None:
    """Reject foreign or unbound image request paths before their JSON is opened."""
    _authorize_guard_path(Path(path))


def load_guard(path: Path) -> dict[str, object]:
    thread_id = _authorize_guard_path(path)
    if not path.is_file():
        raise GuardError(f"Execution guard does not exist: {path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GuardError(f"Cannot read execution guard: {error}") from error
    if state.get("schema_version") != SCHEMA_VERSION:
        raise GuardError(f"Unsupported execution guard schema: {state.get('schema_version')}")
    if thread_id and state.get("task_kind") != "GENERAL" and state.get("owner_chat_id") != thread_id:
        raise GuardError("UNBOUND_LEGACY_REQUEST: this image guard lacks a matching owner_chat_id. Create a new unique guard in the current chat; do not adopt it.")
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


def execution_start_assertions(
    state: dict[str, object],
    supplied: Sequence[str],
) -> dict[str, str]:
    """Bind EXECUTION_STARTED assertions to the guard's locked invariant set.

    An omitted CLI list is safely derived from the already validated guard
    state. Any explicit list still has to match that state exactly.
    """
    locked = state.get("locked_invariants", {})
    if not isinstance(locked, dict):
        raise GuardError("Execution guard locked_invariants must be an object.")
    assertions = list(supplied)
    if not assertions and locked:
        assertions = [f"{name}={value}" for name, value in locked.items()]
    return validate_invariant_assertions(state, assertions)


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
    normalized_task_kind = task_kind.upper()
    if normalized_task_kind not in {"IMAGE_GENERATION", "IMAGE_GENERATION_NATIVE_DEFAULT", "IMAGE_EDIT", "GENERAL"}:
        raise GuardError(
            "Unsupported task kind. Standalone image requests outside StoryArt do not use a StoryArt guard."
        )
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
    if normalized_task_kind == "IMAGE_GENERATION_NATIVE_DEFAULT" and len(normalized_stages) > 1:
        raise GuardError("IMAGE_GENERATION_NATIVE_DEFAULT supports one requested output stage only.")
    locked_invariants = parse_invariant_assignments(invariants, label="invariant")
    owner_chat_id = _bind_guard_to_current_chat(path, normalized_task_kind)
    moment = now or utc_now()
    state: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id.strip(),
        "task_kind": normalized_task_kind,
        **({"owner_chat_id": owner_chat_id} if owner_chat_id else {}),
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
            "summary": "Task contract locked before substantive work.",
        }],
    }
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


def reconcile_plan_required_stages(state: dict[str, object], plan: dict[str, object]) -> None:
    """Bring a base guard in line with its validated executable plan."""
    purpose = str(plan.get("generation_purpose", "")).strip().upper()
    previous_purpose = str(state.get("request_purpose", "")).strip().upper()
    if previous_purpose and previous_purpose != purpose:
        raise GuardActionRequired(
            "The request purpose changed after readiness; repair the guard and executable plan before continuing."
        )
    if purpose != "CHARACTER_BASE":
        state["request_purpose"] = purpose
        return
    workflow = plan.get("generation_workflow")
    planned_stages = workflow.get("stages") if isinstance(workflow, dict) else None
    planned_ids = [str(row.get("stage_id", "")) for row in planned_stages] if (
        isinstance(planned_stages, list) and all(isinstance(row, dict) for row in planned_stages)
    ) else []
    if (
        not isinstance(workflow, dict) or workflow.get("mode") != "MULTI_STAGE"
        or planned_ids != list(CHARACTER_BASE_STAGES)
    ):
        raise GuardActionRequired(
            "CHARACTER_BASE executable plan must contain the five canonical face, front, side, back and assembly stages; repair the plan before execution."
        )
    existing = required_stage_entries(state)
    if any(not isinstance(row, dict) for row in existing):
        raise GuardActionRequired("CHARACTER_BASE guard has malformed stage entries; repair the guard before continuing.")
    by_id = {str(row.get("id", "")): row for row in existing}
    if len(by_id) != len(existing) or any(not stage_id for stage_id in by_id):
        raise GuardActionRequired(
            "CHARACTER_BASE guard has duplicate or empty stage entries; reconcile it with the executable plan."
        )
    state["required_stages"] = [
        *[by_id[stage_id] for stage_id in by_id if stage_id not in CHARACTER_BASE_STAGES],
        *[by_id.get(stage_id, {"id": stage_id, "status": "PENDING", "completed_at": None, "evidence": []})
          for stage_id in CHARACTER_BASE_STAGES],
    ]
    contract = plan.get("character_base_contract")
    if not isinstance(contract, dict) or contract.get("age_group") not in {"ADULT", "CHILD"}:
        raise GuardActionRequired("CHARACTER_BASE plan has no explicit age-group contract.")
    if contract.get("age_group") == "CHILD":
        anatomy = str(contract.get("subject_anatomy", "")).upper()
        expected_garment = (
            "opaque age-appropriate T-shirt and shorts" if anatomy == "MALE_ANATOMY"
            else "opaque age-appropriate T-shirt and skirt" if anatomy == "FEMALE_ANATOMY" else ""
        )
        if (contract.get("adult_swimwear_ladder") is not False
                or contract.get("adult_anthropometry") is not False
                or contract.get("coverage_policy") != "OPAQUE_AGE_APPROPRIATE_CLOSED_COVERAGE"
                or not expected_garment
                or contract.get("required_garment") != expected_garment):
            raise GuardActionRequired("CHILD CHARACTER_BASE contract is incomplete or conflicts with closed age-appropriate coverage.")
    state["character_base_contract"] = contract
    state["request_purpose"] = purpose


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


def is_project_image_generation(state: dict[str, object]) -> bool:
    return state.get("task_kind") in {"IMAGE_GENERATION", "IMAGE_GENERATION_NATIVE_DEFAULT"}


def validate_user_generation_selections(execution_call: dict[str, object], *, no_references: bool) -> dict[str, str]:
    """Require explicit, chat-quoted user choices for style, references, and identity."""
    selections = execution_call.get("user_selections")
    if not isinstance(selections, dict):
        raise GuardError("Image readiness requires explicit user_selections for style, reference policy, and character identity.")
    result: dict[str, str] = {}
    for key in ("style", "reference_policy", "character"):
        item = selections.get(key)
        if not isinstance(item, dict):
            raise GuardError(f"Image readiness requires an explicit user selection for {key}.")
        choice_value = item.get("choice")
        quote_value = item.get("user_quote")
        if not isinstance(choice_value, str) or not isinstance(quote_value, str):
            raise GuardError(f"The {key} selection choice and user_quote must be strings.")
        choice = choice_value.strip()
        quote = quote_value.strip()
        if not choice or not quote:
            raise GuardError(f"The {key} selection must include its choice and exact user_quote from this chat.")
        result[key] = choice
    if no_references:
        if result["style"].upper() != "GENERATOR_DEFAULT":
            raise GuardError("IMAGE_GENERATION_NATIVE_DEFAULT requires the user's explicit GENERATOR_DEFAULT style choice.")
        if result["reference_policy"].upper() != "NO_REFERENCES":
            raise GuardError("IMAGE_GENERATION_NATIVE_DEFAULT requires the user's explicit NO_REFERENCES choice.")
        if result["character"].upper() not in {"NONE", "NO_CHARACTER"}:
            raise GuardError("A named project character cannot use IMAGE_GENERATION_NATIVE_DEFAULT without approved identity references.")
    elif result["reference_policy"].upper() == "NO_REFERENCES":
        raise GuardError("A no-reference choice cannot use IMAGE_GENERATION; select the no-reference route explicitly.")
    elif result["reference_policy"].upper() not in {
        "PROJECT_STYLE_ONLY", "BODY_LIBRARY_ONLY", "APPROVED_CHARACTER_REFERENCES", "APPROVED_PLUS_USER_REFERENCES", "USER_ATTACHED_REFERENCES",
    }:
        raise GuardError("reference_policy must name an explicit supported choice.")
    style_choice = result["style"].upper()
    if style_choice != "GENERATOR_DEFAULT" and not style_choice.startswith(("PROJECT_STYLE:", "USER_STYLE:")):
        raise GuardError("style must be GENERATOR_DEFAULT, PROJECT_STYLE:<name>, or USER_STYLE:<description>.")
    character_choice = result["character"].upper()
    if character_choice not in {"NONE", "NO_CHARACTER", "NEW"} and not re.fullmatch(r"CHAR_\d+", character_choice):
        raise GuardError("character must be NONE, NEW, or an approved CHAR_NNN project identity.")
    return result


def validate_project_style_only_slots(
    slots: list[object], style_choice: str, character_id: str, approved_identity_root: Path | None,
    validated_lineage_paths: set[Path] | None = None,
) -> None:
    """Permit selected style, approved identity, and validated current-request lineage."""
    project_style = style_choice.upper().startswith("PROJECT_STYLE:")
    if not project_style:
        raise GuardError("PROJECT_STYLE_ONLY requires the user's explicit project-style selection.")
    if not any(
        isinstance(slot, dict)
        and set(str(role).upper() for role in slot.get("active_roles", [])) & {"STYLE", "STYLE_SOFT"}
        for slot in slots
    ):
        raise GuardError("Selected project style has no style-role attachment in the exact generator call.")
    for slot in slots:
        if not isinstance(slot, dict):
            raise GuardError("PROJECT_STYLE_ONLY contains an invalid physical slot.")
        slot_roles = {
            str(role).upper()
            for role in (slot.get("active_roles") if isinstance(slot.get("active_roles"), list) else [])
        }
        slot_path = Path(str(slot.get("path", ""))).expanduser().resolve()
        is_style_slot = bool(slot_roles) and slot_roles.issubset({"STYLE", "STYLE_SOFT"})
        is_approved_identity_slot = (
            re.fullmatch(r"CHAR_\d+", character_id.upper()) is not None
            and approved_identity_root is not None
            and slot_path.is_relative_to(approved_identity_root)
        )
        is_validated_lineage = slot_path in (validated_lineage_paths or set())
        if not (is_style_slot or is_approved_identity_slot or is_validated_lineage):
            raise GuardError("PROJECT_STYLE_ONLY cannot attach optional non-style references.")


def registry_identity_matches_locked_name(locked_name: str, registry_row: dict[str, str], character_id: str) -> bool:
    """Match a lock to the canonical registry name or an explicit parenthesized alias."""
    def normalized(value: str) -> str:
        return " ".join(value.split()).casefold().replace("ё", "е")

    def russian_case_stem(value: str) -> str:
        words = value.split()
        if not words:
            return ""
        last = words[-1]
        if not re.search(r"[а-яё]", last, flags=re.IGNORECASE):
            return value
        for ending in ("ом", "ем", "ой", "ей", "ью", "ия", "а", "я", "у", "ю", "ы", "и", "е"):
            if len(last) > len(ending) + 1 and last.endswith(ending):
                words[-1] = last[:-len(ending)]
                break
        return " ".join(words)

    locked = normalized(locked_name)
    canonical_id = str(registry_row.get("character_id", character_id)).strip().upper()
    if locked.upper() == canonical_id:
        return True
    canonical_name = normalized(str(registry_row.get("name", "")))
    if locked and (locked == canonical_name or russian_case_stem(locked) == canonical_name or locked == russian_case_stem(canonical_name)):
        return True
    notes = str(registry_row.get("notes", ""))
    aliases = {
        normalized(match)
        for group in re.findall(r"\(([^()]*)\)", notes)
        for match in re.split(r"[,;/|]", group)
        if match.strip()
    }
    return bool(
        locked
        and any(
            locked == alias
            or russian_case_stem(locked) == alias
            or locked == russian_case_stem(alias)
            for alias in aliases
        )
    )


def validate_native_default_call(
    execution_call: dict[str, object] | None,
    request_id: str,
    reference_plan: str | Path | None,
    locked_character: str = "",
) -> dict[str, str]:
    """Bind a no-reference native/default call without weakening planned generation."""
    if reference_plan is not None:
        raise GuardError("IMAGE_GENERATION_NATIVE_DEFAULT does not accept --reference-plan.")
    if not isinstance(execution_call, dict):
        raise GuardError("IMAGE_GENERATION_NATIVE_DEFAULT requires --execution-call with the exact prompt contract.")
    if locked_character.strip().upper() not in {"", "NONE", "NO_CHARACTER", "NEW"}:
        raise GuardError("A named project character cannot use IMAGE_GENERATION_NATIVE_DEFAULT; resolve and attach approved identity references.")
    validate_user_generation_selections(execution_call, no_references=True)
    forbidden_keys = {
        "slots", "references", "reference_plan", "reference_plan_path", "reference_path",
        "reference_image", "reference_inputs", "reference_slots", "selected_references",
        "style_reference", "body_reference", "source_slots", "input_references", "plan",
        "plan_path", "stage_output_bindings", "targeted_pack_bindings",
    }

    def reject_reference_fields(value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).casefold() == "user_selections":
                    # This subtree was validated above and contains user-choice
                    # vocabulary, not executable reference-slot payloads.
                    continue
                if str(key).casefold() in forbidden_keys:
                    if str(key).casefold() == "references" and nested == []:
                        continue
                    raise GuardError(
                        "IMAGE_GENERATION_NATIVE_DEFAULT is single-output and does not accept plan or reference slots."
                    )
                reject_reference_fields(nested)
        elif isinstance(value, list):
            for nested in value:
                reject_reference_fields(nested)

    reject_reference_fields(execution_call)
    for count_key in ("n", "num_images", "num_outputs", "output_count"):
        if count_key in execution_call and (type(execution_call[count_key]) is not int or execution_call[count_key] != 1):
            raise GuardError("IMAGE_GENERATION_NATIVE_DEFAULT supports exactly one image output.")
    if str(execution_call.get("request_id", "")) != request_id:
        raise GuardError("execution_call belongs to another request_id.")
    prompt = execution_call.get("prompt")
    if not isinstance(prompt, dict):
        raise GuardError("execution_call requires prompt.text and prompt.text_sha256.")
    prompt_text = str(prompt.get("text", ""))
    prompt_hash = str(prompt.get("text_sha256", "")).lower()
    actual_prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    if not prompt_text.strip() or prompt_hash != actual_prompt_hash:
        raise GuardError("execution_call prompt is empty or its text_sha256 does not match.")
    risk_report = execution_call.get("risk_assessment")
    if not isinstance(risk_report, dict):
        raise GuardError("IMAGE_GENERATION_NATIVE_DEFAULT requires an embedded exact-call risk_assessment.")
    try:
        from tools.generation_risk_assessor import RiskAssessmentError, validate_assessment
    except ImportError:
        try:
            from generation_risk_assessor import RiskAssessmentError, validate_assessment
        except ImportError as error:
            raise GuardError(f"Cannot load exact-call risk assessor: {error}") from error
    try:
        validate_assessment(risk_report, prompt_text, [])
    except RiskAssessmentError as error:
        raise GuardError(f"Exact-call risk assessment validation failed: {error}") from error
    call_json = json.dumps(execution_call, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "request_id": request_id,
        "prompt_sha256": prompt_hash,
        "execution_call_sha256": hashlib.sha256(call_json.encode("utf-8")).hexdigest(),
    }


def validate_image_execution_scope(
    state: dict[str, object],
    *,
    summary: str,
    output_contract: str | None,
    user_approved_extra_generation: bool,
    extra_generation_evidence: str,
) -> None:
    task_kind = state.get("task_kind")
    if task_kind not in {"IMAGE_GENERATION", "IMAGE_GENERATION_NATIVE_DEFAULT"}:
        return
    contract = str(output_contract or "").upper()
    if task_kind == "IMAGE_GENERATION_NATIVE_DEFAULT" and contract != "REQUESTED_DELIVERABLE":
        raise GuardError("IMAGE_GENERATION_NATIVE_DEFAULT requires --output-contract REQUESTED_DELIVERABLE.")
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


def _stage_has_identity_anchor(
    workflow: dict[str, object], stage_id: str, assembly_path: Path, assembly_hash: str,
    visiting: set[str] | None = None,
) -> bool:
    """Require a named-character stage to descend from its approved assembly source."""
    normalized_id = normalize_stage_key(stage_id)
    visited = set(visiting or ())
    if normalized_id in visited:
        return False
    visited.add(normalized_id)
    stage_rows = workflow.get("stages")
    if not isinstance(stage_rows, list):
        return False
    row = next((item for item in stage_rows if isinstance(item, dict)
                and normalize_stage_key(str(item.get("stage_id", ""))) == normalized_id), None)
    row_slots = row.get("slots") if isinstance(row, dict) else None
    if not isinstance(row_slots, list):
        return False
    parents: set[str] = set()
    for slot in row_slots:
        if not isinstance(slot, dict):
            continue
        raw_path = str(slot.get("path", ""))
        if (
            raw_path
            and not raw_path.startswith(("<STAGE_OUTPUT:", "<TARGETED_STAGE_PACK:"))
            and Path(raw_path).expanduser().resolve() == assembly_path
            and str(slot.get("sha256", "")).lower() == assembly_hash
            and "CHARACTER_ASSEMBLY" in {str(role).upper() for role in slot.get("active_roles", [])}
        ):
            return True
        match = re.fullmatch(r"<STAGE_OUTPUT:([^>]+)>", raw_path)
        if match:
            parents.add(match.group(1))
        sources = slot.get("targeted_pack_sources")
        if isinstance(sources, list):
            parents.update(str(source) for source in sources if str(source).strip())
    return any(_stage_has_identity_anchor(workflow, parent, assembly_path, assembly_hash, visited) for parent in parents)


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
    locked_character: str = "",
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
    if not isinstance(execution_call, dict):
        raise GuardError("REFERENCE_PLAN readiness requires a materialized exact execution_call with explicit user_selections.")
    recorded_call = plan.get("execution_call")
    if not isinstance(recorded_call, dict) or recorded_call != execution_call:
        raise GuardError("The materialized execution_call must exactly match the call saved in REFERENCE_PLAN.")
    face_purpose = plan.get("new_face_reference_purpose")
    if face_purpose is not None:
        if not isinstance(face_purpose, dict) or face_purpose.get("mode") not in {"STYLE_SEED", "USER_IDENTITY_INTENT"}:
            raise GuardError("REFERENCE_PLAN has an unsupported new_face_reference_purpose binding.")
        if execution_call.get("new_face_reference_purpose") != face_purpose:
            raise GuardError("Exact execution_call face purpose differs from REFERENCE_PLAN.")
        call_risk = execution_call.get("risk_assessment")
        plan_risk = plan.get("risk_assessment")
        if (not isinstance(call_risk, dict) or call_risk.get("new_face_reference_purpose") != face_purpose
                or not isinstance(plan_risk, dict) or plan_risk.get("new_face_reference_purpose") != face_purpose):
            raise GuardError("Exact-call risk bindings do not preserve new face-source purpose and provenance.")
        if face_purpose.get("mode") == "USER_IDENTITY_INTENT":
            if (plan.get("generation_purpose") != "CHARACTER_BASE" or str(plan.get("character_id", "")).upper() != "NEW"
                    or face_purpose.get("scope") != "NEW_CHARACTER_BASE"
                    or not str(face_purpose.get("evidence", "")).strip()
                    or face_purpose.get("semantic_role") != "FACE"):
                raise GuardError("USER_IDENTITY_INTENT is valid only with current author evidence on a NEW CHARACTER_BASE.")
            selected = plan.get("selected_references")
            if not isinstance(selected, dict):
                raise GuardError("USER_IDENTITY_INTENT requires selected face source records.")
            style_values = selected.get("style", [])
            style_values = style_values if isinstance(style_values, list) else [style_values]
            style_paths = {str(Path(str(item.get("path", ""))).resolve()).casefold() for item in style_values if isinstance(item, dict)}
            bound_sources = face_purpose.get("sources")
            if not isinstance(bound_sources, list) or len(bound_sources) != 2:
                raise GuardError("USER_IDENTITY_INTENT requires exactly two face source path/hash bindings.")
            expected_keys = {"primary_face": "PRIMARY_FACE", "supporting_face": "SUPPORTING_FACE"}
            seen: set[str] = set()
            seen_source_paths: set[str] = set()
            seen_source_hashes: set[str] = set()
            for source in bound_sources:
                if not isinstance(source, dict):
                    raise GuardError("USER_IDENTITY_INTENT source bindings must be objects.")
                key = str(source.get("selection_key", ""))
                if key not in expected_keys or source.get("physical_slot_role") != expected_keys[key] or source.get("semantic_role") != "FACE" or key in seen:
                    raise GuardError("USER_IDENTITY_INTENT source role mapping is invalid or duplicated.")
                seen.add(key)
                values = selected.get(key)
                values = values if isinstance(values, list) else [values]
                if len(values) != 1 or not isinstance(values[0], dict):
                    raise GuardError(f"USER_IDENTITY_INTENT is missing its selected {key} record.")
                selected_source = values[0]
                path = Path(str(source.get("path", ""))).resolve()
                digest = str(source.get("sha256", "")).lower()
                normalized_path = str(path).casefold()
                if normalized_path in seen_source_paths:
                    raise GuardError("USER_IDENTITY_INTENT requires two distinct absolute FACE source paths.")
                if digest in seen_source_hashes:
                    raise GuardError("USER_IDENTITY_INTENT requires two distinct FACE source SHA-256 hashes.")
                seen_source_paths.add(normalized_path)
                seen_source_hashes.add(digest)
                if (Path(str(selected_source.get("path", ""))).resolve() != path
                        or str(selected_source.get("sha256", "")).lower() != digest
                        or not re.fullmatch(r"[0-9a-f]{64}", digest)
                        or not path.is_file() or file_sha256(path) != digest
                        or source.get("subject_label") not in {"MALE", "FEMALE"}
                        or source.get("subject_label_sha256") != digest
                        or selected_source.get("subject_label") != source.get("subject_label")
                        or selected_source.get("subject_label_status") != "REVIEWED"
                        or selected_source.get("subject_label_sha256") != digest
                        or selected_source.get("face_reference_purpose") != "USER_IDENTITY_INTENT"
                        or selected_source.get("face_reference_purpose_scope") != "NEW_CHARACTER_BASE"
                        or str(source.get("purpose_evidence", "")).strip() != str(face_purpose.get("evidence", "")).strip()
                        or str(path).casefold() in style_paths):
                    raise GuardError(f"USER_IDENTITY_INTENT {key} path, hash, label, or independent STYLE binding differs.")
            if seen != set(expected_keys):
                raise GuardError("USER_IDENTITY_INTENT lacks both PRIMARY_FACE and SUPPORTING_FACE bindings.")
    recorded_selections = plan.get("user_selections")
    if isinstance(recorded_selections, dict) and recorded_selections != execution_call.get("user_selections"):
        raise GuardError("REFERENCE_PLAN user_selections differ from the exact execution_call selections.")
    risk = plan.get("risk_assessment")
    prompt_info = risk.get("prompt") if isinstance(risk, dict) else None
    if execution_call is not None:
        user_selections = validate_user_generation_selections(execution_call, no_references=False)
        character_id = user_selections["character"].upper()
        reference_policy = user_selections["reference_policy"].upper()
        approved_identity_root: Path | None = None
        approved_assembly_path: Path | None = None
        approved_assembly_hash = ""
        if locked_character.strip().upper() not in {"", "NONE", "NO_CHARACTER", "NEW"} and not re.fullmatch(r"CHAR_\d+", character_id):
            raise GuardError("The locked named character must resolve to an approved CHAR_NNN identity before readiness.")
        if re.fullmatch(r"CHAR_\d+", character_id) and reference_policy not in {
            "PROJECT_STYLE_ONLY", "BODY_LIBRARY_ONLY", "APPROVED_CHARACTER_REFERENCES", "APPROVED_PLUS_USER_REFERENCES", "USER_ATTACHED_REFERENCES",
        }:
            raise GuardError("An existing project character requires its approved identity references in the selected reference policy.")
        if re.fullmatch(r"CHAR_\d+", character_id):
            if str(plan.get("character_id", "")).upper() != character_id:
                raise GuardError("Selected project character does not match REFERENCE_PLAN.character_id.")
            selected = plan.get("selected_references")
            assembly = selected.get("character_assembly") if isinstance(selected, dict) else None
            if (
                not isinstance(assembly, dict)
                or str(assembly.get("status", "")).upper() != "APPROVED_CHARACTER_ASSET"
                or str(assembly.get("source_character_id", "")).upper() != character_id
            ):
                raise GuardError("Named project character is missing its approved CHARACTER_ASSEMBLY binding.")
            if locked_character.strip() and locked_character.strip().upper() not in {"NONE", "NO_CHARACTER", "NEW"}:
                try:
                    from tools import style_pack_manager as manager
                except ImportError:
                    import style_pack_manager as manager
                try:
                    pack_path = Path(str(plan.get("pack_path", ""))).resolve()
                    style_name = str(plan.get("style_name", "")).strip()
                    if not pack_path.is_dir() or not style_name:
                        raise GuardError("Cannot resolve the approved character registry for the locked identity.")
                    character_paths = manager.identity_paths_for_plan(manager.make_paths(pack_path.parent, style_name), plan)
                    identity_rows = [
                        row for row in manager.read_csv(character_paths.character_registry)
                        if str(row.get("character_id", "")).strip().upper() == character_id
                        and str(row.get("status", "")).strip().upper() == "APPROVED"
                    ]
                    if len(identity_rows) != 1:
                        raise GuardError("The selected character ID does not resolve to one approved registry entry.")
                    if not registry_identity_matches_locked_name(locked_character, identity_rows[0], character_id):
                        raise GuardError("The selected approved character ID does not match the locked character name.")
                except (OSError, ValueError, RuntimeError) as error:
                    raise GuardError(f"Cannot verify locked character against the approved registry: {error}") from error
            assembly_path = str(assembly.get("path", ""))
            assembly_hash = str(assembly.get("sha256", "")).lower()
            try:
                try:
                    from tools import style_pack_manager as manager
                except ImportError:
                    import style_pack_manager as manager
                pack_path = Path(str(plan.get("pack_path", ""))).resolve()
                style_name = str(plan.get("style_name", "")).strip()
                if not pack_path.is_dir() or not style_name:
                    raise GuardError("Cannot resolve the approved character identity folder for readiness.")
                approved_identity_root = manager.character_folder(
                    manager.identity_paths_for_plan(manager.make_paths(pack_path.parent, style_name), plan), character_id
                ).resolve()
                if not Path(assembly_path).expanduser().resolve().is_relative_to(approved_identity_root):
                    raise GuardError("The selected CHARACTER_ASSEMBLY is outside the named character's approved identity folder.")
                approved_assembly_path = Path(assembly_path).expanduser().resolve()
                approved_assembly_hash = assembly_hash
            except (OSError, ValueError, RuntimeError) as error:
                raise GuardError(f"Cannot verify approved character identity attachment: {error}") from error
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
        if re.fullmatch(r"CHAR_\d+", character_id) and str(plan.get("generation_purpose", "")).upper() == "SCENE":
            try:
                try:
                    from tools import style_pack_manager as manager
                    from tools.character_profile_state import ProfileStateError, verify_plan_binding
                except ImportError:
                    import style_pack_manager as manager
                    from character_profile_state import ProfileStateError, verify_plan_binding
                pack_path = Path(str(plan.get("pack_path", ""))).resolve()
                style_name = str(plan.get("style_name", "")).strip()
                if not style_name:
                    raise GuardError("Named scene has no approved style registry route.")
                character_paths = manager.identity_paths_for_plan(manager.make_paths(pack_path.parent, style_name), plan)
                if manager.make_paths(pack_path.parent, style_name).pack.resolve() != pack_path:
                    raise GuardError("Named scene pack path differs from its approved style registry route.")
                rows = [row for row in manager.read_csv(character_paths.character_registry)
                        if str(row.get("character_id", "")).upper() == character_id
                        and str(row.get("status", "")).upper() == "APPROVED"]
                if len(rows) != 1:
                    raise GuardError("Named scene requires exactly one approved character registry entry.")
                profile_path = Path(str(rows[0].get("profile_path", "")))
                if not profile_path.is_absolute():
                    profile_path = character_paths.generations / profile_path
                profile_path = profile_path.resolve()
                if approved_identity_root is None or profile_path.parent != approved_identity_root:
                    raise GuardError("Approved character registry profile lies outside its identity folder.")
                manager._approved_profile_identity(character_paths, profile_path, character_id)
                verify_plan_binding(profile_path, character_id, plan, execution_call)
                if manager.effective_character_profile(character_paths, character_id) != plan.get("confirmed_character_profile"):
                    raise GuardError("Named scene profile or approved visual provenance changed since preparation.")
            except (ProfileStateError, OSError, ValueError, RuntimeError) as error:
                raise GuardError(f"Named scene confirmed profile verification failed: {error}") from error
        style_choice = user_selections["style"].strip()
        selected_slots = execution_call.get("slots")
        if not isinstance(selected_slots, list):
            raise GuardError("Executable call has no exact physical slots for the user's style/reference choices.")
        roles = {
            str(role).upper()
            for slot in selected_slots if isinstance(slot, dict)
            for role in (slot.get("active_roles") if isinstance(slot.get("active_roles"), list) else [])
        }
        if style_choice.upper() == "GENERATOR_DEFAULT" and roles.intersection({"STYLE", "STYLE_SOFT"}):
            raise GuardError("The exact call attaches a style reference although the user selected GENERATOR_DEFAULT.")
        if style_choice.upper().startswith("PROJECT_STYLE:"):
            selected_style = style_choice.partition(":")[2].strip()
            if not selected_style or selected_style.casefold() != str(plan.get("style_name", "")).strip().casefold():
                raise GuardError("Selected project style does not match REFERENCE_PLAN.style_name.")
            if not roles.intersection({"STYLE", "STYLE_SOFT"}):
                raise GuardError("Selected project style has no style-role attachment in the exact generator call.")
        if style_choice.upper().startswith("USER_STYLE:"):
            selected_style = " ".join(style_choice.partition(":")[2].split())
            normalized_prompt = " ".join(prompt_text.split()).casefold()
            if not selected_style or selected_style.casefold() not in normalized_prompt:
                raise GuardError("The exact prompt does not contain the user's selected custom style description.")
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
        if approved_assembly_path is not None:
            if mode == "SINGLE_PASS":
                anchor_bound = any(
                    isinstance(slot, dict)
                    and str(Path(str(slot.get("path", ""))).expanduser().resolve()) == str(approved_assembly_path)
                    and str(slot.get("sha256", "")).lower() == approved_assembly_hash
                    and "CHARACTER_ASSEMBLY" in {str(role).upper() for role in slot.get("active_roles", [])}
                    for slot in execution_call.get("slots", [])
                )
            else:
                anchor_bound = _stage_has_identity_anchor(
                    workflow, selected_stage, approved_assembly_path, approved_assembly_hash,
                )
            if not anchor_bound:
                raise GuardError("The exact stage has no verified path to the approved CHARACTER_ASSEMBLY identity anchor.")
        _validate_resolved_execution_slots(
            path, plan, workflow, planned_slots, execution_call,
            str(request_id or plan.get("request_id", "")),
        )
        # Only after exact slot resolution has been validated can prior outputs
        # be treated as inherited references for a policy that excludes optional
        # attachments. The validator above binds these paths to QA-passed,
        # request-local stage output records and planned placeholders.
        validated_lineage_paths = {
            Path(str(row.get("path", ""))).expanduser().resolve()
            for row in execution_call.get("stage_output_bindings", [])
            if isinstance(row, dict) and row.get("path")
        }
        validated_lineage_paths.update(
            Path(str(row.get("path", ""))).expanduser().resolve()
            for row in execution_call.get("targeted_pack_bindings", [])
            if isinstance(row, dict) and row.get("path")
        )
        if reference_policy == "PROJECT_STYLE_ONLY":
            validate_project_style_only_slots(
                selected_slots, style_choice, character_id, approved_identity_root,
                validated_lineage_paths,
            )
        elif reference_policy == "BODY_LIBRARY_ONLY":
            startup = plan.get("startup_parameter_selection", {})
            resolved = startup.get("resolved_parameters", {}) if isinstance(startup, dict) else {}
            if str(resolved.get("aux_body_decision", "")).upper() != "SELECTED":
                raise GuardError("BODY_LIBRARY_ONLY requires the user's explicit request-level BODY_REFERENCE_LIBRARY selection.")
            try:
                workspace = Path(str(plan.get("pack_path", ""))).resolve().parent
                body_library = (workspace / "BODY_REFERENCE_LIBRARY").resolve()
            except (OSError, ValueError):
                body_library = Path("<unresolved-body-library>")
            for slot in selected_slots:
                if not isinstance(slot, dict):
                    raise GuardError("BODY_LIBRARY_ONLY contains an invalid physical slot.")
                slot_path = Path(str(slot.get("path", ""))).expanduser().resolve()
                slot_roles = {str(role).upper() for role in slot.get("active_roles", [])}
                is_library = slot_path.is_relative_to(body_library)
                is_identity = approved_identity_root is not None and slot_path.is_relative_to(approved_identity_root)
                is_style = bool(slot_roles) and slot_roles.issubset({"STYLE", "STYLE_SOFT"})
                if not (is_library or is_identity or is_style or slot_path in validated_lineage_paths):
                    raise GuardError("BODY_LIBRARY_ONLY cannot attach optional non-library references outside validated stage lineage.")
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
        # Keep the declared digest for the exact-call handoff. The mandatory
        # risk assessor below performs the fresh byte read and verifies that
        # this digest still matches the physical file.
        bindings.append({"path": str(ref), "sha256": expected, "roles": sorted(str(role) for role in roles)})
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
        if "Selected reference hash does not match file" in str(error):
            raise GuardError(f"REFERENCE_PLAN source hash changed: {error}") from error
        raise GuardError(f"Exact-call risk assessment validation failed: {error}") from error
    binding = {
        "path": str(path),
        "sha256": file_sha256(path),
        "prompt_sha256": prompt_hash,
        "stage": selected_stage,
        "references": bindings,
    }
    if execution_call is not None:
        call_json = json.dumps(execution_call, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        binding["execution_call_sha256"] = hashlib.sha256(call_json.encode("utf-8")).hexdigest()
    return binding
def escalation_incident_key(
    state: dict[str, object], stage: str, qa_layer: str, *, include_current_rejection: bool = False
) -> str | None:
    """Identify repeated stage failure since the latest correction/diagnosis.

    QA labels are descriptive evidence and can legitimately change between
    reviews. They therefore do not define incident identity. Two failures
    trigger the first diagnosis; after a correction or diagnosis, one further
    failed strategy triggers the next diagnosis.
    """
    events = list(state.get("events", []))
    if not stage.strip():
        return None
    def matches(row: dict[str, object]) -> bool:
        return (
            row.get("event") == "ATTEMPT_REJECTED"
            and normalize_stage_key(str(row.get("stage", ""))) == normalize_stage_key(stage)
        )
    stage_key = normalize_stage_key(stage)
    reset_index = next(
        (
            index for index in range(len(events) - 1, -1, -1)
            if (
                events[index].get("event") == "USER_CORRECTION"
                and normalize_stage_key(str(events[index].get("corrects_stage", ""))) == stage_key
                and events[index].get("corrects_attempt_at")
            )
            or (
                events[index].get("event") == "ESCALATION_ORCHESTRATOR_RECORDED"
                and normalize_stage_key(str(events[index].get("stage", ""))) == stage_key
            )
        ),
        -1,
    )
    rejection_indices = [index for index in range(reset_index + 1, len(events)) if matches(events[index])]
    rejections_since_reset = len(rejection_indices)
    threshold = 1 if reset_index >= 0 else 2
    if rejections_since_reset + int(include_current_rejection) < threshold:
        return None
    if include_current_rejection:
        rejection_index = len(events)
    else:
        rejection_index = rejection_indices[-1] if rejection_indices else -1
    # The failed strategy itself identifies the incident; a later failure
    # after the recorded diagnosis therefore creates a fresh incident.
    return f"{rejection_index}|{stage_key}"


def validate_swimwear_execution_start(
    state: dict[str, object],
    *,
    stage: str | None,
    swimwear_rung: str | None,
    user_swimwear_override: bool,
    user_override_evidence: str,
    plan: dict[str, object] | None = None,
    execution_call: dict[str, object] | None = None,
) -> dict[str, object] | None:
    if not is_physique_stage(stage):
        return None
    entry = find_required_stage(state, stage)
    if entry.get("status") == "COMPLETED":
        raise GuardError(f"Cannot start another physique attempt for completed stage {entry.get('id')}.")
    contract = state.get("character_base_contract", {})
    if isinstance(contract, dict) and contract.get("age_group") == "CHILD":
        if swimwear_rung or user_swimwear_override:
            raise GuardError("Child physique stages forbid adult swimwear rungs and all user topology overrides.")
        plan_contract = plan.get("character_base_contract") if isinstance(plan, dict) else None
        if plan_contract != contract:
            raise GuardError("Child age/coverage contract differs between guard and executable plan.")
        prompt = execution_call.get("prompt", {}) if isinstance(execution_call, dict) else {}
        prompt_text = str(prompt.get("text", "")).casefold() if isinstance(prompt, dict) else ""
        required = str(contract.get("required_garment", "")).casefold()
        normalized = " ".join(prompt_text.replace("-", " ").split())
        if not required or " ".join(required.replace("-", " ").split()) not in normalized:
            raise GuardError("Child execution prompt omits the exact required T-shirt and lower garment.")
        forbidden = ("swimsuit", "swim shorts", "swimwear", "bikini", "one piece", "thong", "micro", "lingerie", "underwear", "nude", "naked", "transparent", "see through", "exposed torso")
        if any(term in normalized for term in forbidden):
            raise GuardError("Child physique execution prompt contains adult or exposed-topology wording.")
        return None
    rung = str(swimwear_rung or "").upper()
    if user_swimwear_override:
        if not user_override_evidence.strip():
            raise GuardError("A swimwear override requires --user-override-evidence with the user's direct instruction.")
        if rung not in (DEFAULT_SWIMWEAR_RUNG, *SWIMWEAR_OVERRIDE_RUNGS):
            raise GuardError("A user swimwear override still requires a declared --swimwear-rung or CUSTOM.")
    elif rung != DEFAULT_SWIMWEAR_RUNG:
        raise GuardError(
            f"Adult physique execution requires --swimwear-rung {DEFAULT_SWIMWEAR_RUNG}. "
            "Other cuts are allowed only with --user-swimwear-override and the user's direct instruction."
        )
    return {
        "stage": str(entry.get("id")),
        "swimwear_rung": rung,
        "user_swimwear_override": bool(user_swimwear_override),
        "user_override_evidence": user_override_evidence.strip(),
    }


def validate_stage_evidence(state: dict[str, object], evidence: Sequence[str]) -> list[str]:
    evidence_paths = [str(Path(item).resolve()) for item in evidence]
    if is_project_image_generation(state):
        if not evidence_paths:
            raise GuardError("Image-generation stages require a real output path as evidence.")
        for item in evidence_paths:
            file = Path(item)
            if not file.is_file() or file.suffix.lower() not in IMAGE_EXTENSIONS:
                raise GuardError(f"Required-stage image evidence is missing or unsupported: {file}")
    return evidence_paths


def verified_author_acceptance(state: dict[str, object]) -> dict[str, object] | None:
    """Acceptance is a new receipt, never a mutation or fabricated provider attempt."""
    binding = state.get("author_contract_acceptance")
    if not isinstance(binding, dict):
        return None
    try:
        try:
            from tools import style_pack_manager as manager
        except ImportError:
            import style_pack_manager as manager
        receipt_path = Path(str(binding.get("receipt_path", ""))).resolve()
        assert_active_request_path(receipt_path)
        if not receipt_path.is_file() or file_sha256(receipt_path) != binding.get("receipt_sha256"):
            raise GuardError("Author acceptance receipt is missing or changed.")
        pack_name = str(binding.get("style_name", ""))
        workspace = Path(str(binding.get("workspace", "")))
        checked = manager.validate_registered_author_acceptance(manager.make_paths(workspace, pack_name), state, receipt_path)
        if any(checked.get(key) != binding.get(key) for key in ("generation_id", "attempt_id", "output_sha256", "task_revision")):
            raise GuardError("Author acceptance lineage changed.")
        return checked
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        raise GuardError(f"Author-contract acceptance verification failed: {error}") from error


def validate_stage_attempt_binding(
    state: dict[str, object], stage: str, evidence_paths: Sequence[str]
) -> dict[str, object]:
    """Bind an image stage to its current executed, accepted provider result."""
    accepted = verified_author_acceptance(state)
    if accepted:
        attempt = state["attempts"][-1]
        if normalize_stage_key(str(attempt.get("stage", ""))) != normalize_stage_key(stage) and not (str(stage).upper() == "SCENE" and str(attempt.get("stage", "")).upper() == "SINGLE_PASS"):
            raise GuardError("Author acceptance cannot complete another image stage.")
        normalized = [str(Path(item).resolve()) for item in evidence_paths]
        if normalized != [accepted["output"]]:
            raise GuardError("Author acceptance stage requires the exact original provider output.")
        return {"acceptance_generation_id": accepted["generation_id"], "attempt_id": accepted["attempt_id"],
                "task_revision": accepted["task_revision"], "result_status": "AUTHOR_CONTRACT_TEST",
                "artifact_path": accepted["output"], "stage_evidence_path": accepted["output"],
                "artifact_sha256": accepted["output_sha256"]}
    stage_key = normalize_stage_key(stage)
    revision = int(state.get("task_revision", 0))
    def matches_stage(row: dict[str, object]) -> bool:
        if normalize_stage_key(str(row.get("stage", ""))) == stage_key:
            return True
        if (state.get("task_kind") != "IMAGE_GENERATION_NATIVE_DEFAULT"
                or str(stage).strip().upper() != "SCENE"
                or row.get("stage") != "SINGLE_PASS"):
            return False
        snapshot = row.get("execution_snapshot")
        binding = row.get("reference_binding")
        if not isinstance(snapshot, dict) or not isinstance(binding, dict):
            return False
        call = snapshot.get("execution_call")
        if not isinstance(call, dict) or call.get("stage_id") != "SCENE":
            return False
        call_hash = hashlib.sha256(json.dumps(call, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
        return (
            isinstance(state.get("task_revision"), int) and not isinstance(state.get("task_revision"), bool)
            and isinstance(row.get("task_revision"), int) and not isinstance(row.get("task_revision"), bool)
            and isinstance(snapshot.get("task_revision"), int) and not isinstance(snapshot.get("task_revision"), bool)
            and row["task_revision"] == snapshot["task_revision"] == revision
            and snapshot.get("request_id") == call.get("request_id") == binding.get("request_id") == state.get("request_id")
            and bool(state.get("request_id")) and snapshot.get("attempt_id") == row.get("attempt_id")
            and snapshot.get("stage") == snapshot.get("guard_stage") == snapshot.get("execution_stage") == "SINGLE_PASS"
            and snapshot.get("reference_binding") == binding
            and snapshot.get("execution_call_sha256") == binding.get("execution_call_sha256") == call_hash
            and snapshot.get("prompt_sha256") == binding.get("prompt_sha256") == (call.get("prompt") or {}).get("text_sha256")
        )

    candidates = [row for row in state.get("attempts", []) if matches_stage(row)]
    attempt = candidates[-1] if candidates else None
    if (
        not isinstance(attempt, dict)
        or int(attempt.get("task_revision", -1)) != revision
        or attempt.get("status") not in {"RESULT_AVAILABLE", "DELIVERED"}
        or attempt.get("result_status") not in {"STAGING", "TEST"}
    ):
        raise GuardError(
            f"Image stage {stage} requires its own current-revision accepted STAGING or TEST execution result."
        )
    artifact = attempt.get("provider_artifact")
    result_evidence = attempt.get("result_evidence", [])
    if not isinstance(artifact, dict) or not isinstance(result_evidence, list) or not result_evidence:
        raise GuardError(f"Image stage {stage} has no registered provider artifact binding.")
    artifact_path = str(Path(str(artifact.get("path", ""))).expanduser().resolve())
    artifact_hash = str(artifact.get("sha256", "")).lower()
    registered_paths = [str(Path(str(item)).expanduser().resolve()) for item in result_evidence]
    if (
        artifact.get("result_status") != attempt.get("result_status")
        or len(registered_paths) != 1
        or not artifact_hash
        or not Path(artifact_path).is_file()
        or file_sha256(Path(artifact_path)) != artifact_hash
        or not Path(registered_paths[0]).is_file()
        or file_sha256(Path(registered_paths[0])) != artifact_hash
    ):
        raise GuardError(f"Image stage {stage} provider artifact binding is stale or inconsistent; recover registration.")
    normalized_evidence = [str(Path(item).resolve()) for item in evidence_paths]
    if (
        len(normalized_evidence) != 1
        or normalized_evidence != registered_paths
        or not Path(normalized_evidence[0]).is_file()
        or file_sha256(Path(normalized_evidence[0])) != artifact_hash
        or file_sha256(Path(artifact_path)) != artifact_hash
    ):
        raise GuardError(
            f"Image stage {stage} evidence must be one registered artifact with the exact executed provider artifact hash."
        )
    return {
        "attempt_id": str(attempt.get("attempt_id", "")),
        "task_revision": revision,
        "result_status": str(attempt.get("result_status", "")),
            "artifact_path": artifact_path,
            "stage_evidence_path": normalized_evidence[0],
        "artifact_sha256": artifact_hash,
    }


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
    if state.get("status") in {"BLOCKED", "COMPLETE", "STOPPED", "CANCELLED", "WAITING_FOR_USER"} or state.get("waiting_since"):
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
    """Record a nonterminal watchdog without erasing the executable phase."""
    if state.get("watchdog_reason") != reason:
        append_event(state, "WATCHDOG_TRIGGERED", reason, now)
    if state.get("status") == "ACTION_REQUIRED":
        # Migrate legacy internal cutoff records back into an executable,
        # nonterminal watchdog state.
        state["status"] = "ACTIVE"
        state["phase"] = "WATCHDOG"
        state["next_required_action"] = "READY_FOR_EXECUTION_OR_EXECUTION_STARTED_OR_BLOCKER"
        state.pop("action_required_reason", None)
    elif state.get("phase") == "PREFLIGHT":
        state["phase"] = "WATCHDOG"
        state["next_required_action"] = (
            "READY_FOR_EXECUTION_OR_EXECUTION_STARTED_OR_BLOCKER"
        )
    state["watchdog_reason"] = reason
    state["watchdog_triggered_at"] = iso_time(now)
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
    assert_active_request_path(plan_path)
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


def completion_block_reason(state: dict[str, object]) -> str | None:
    """Return why the locked task is not complete, without trusting status text."""
    if state.get("recovery"):
        return "Finish the recorded repair before claiming completion."
    if state.get("status") == "BLOCKED":
        return "An external, safety, or user-input boundary is unresolved; resolve it before completion."
    if state.get("status") in {"WAITING_FOR_USER", "STOPPED", "CANCELLED"} or state.get("waiting_since"):
        return "A waiting, stopped, or cancelled task cannot complete until the user resumes it."
    if state.get("active_attempt") or any(a.get("status") in {"ACTIVE", "UNKNOWN"} for a in state.get("attempts", [])):
        return "Task completion requires no active or UNKNOWN attempt; reconcile it first."
    executed_base = any(
        isinstance(row, dict) and isinstance(row.get("execution_snapshot"), dict)
        and row["execution_snapshot"].get("request_purpose") == "CHARACTER_BASE"
        for row in state.get("attempts", [])
    )
    if state.get("request_purpose") == "CHARACTER_BASE" or executed_base:
        entries = required_stage_entries(state)
        actual_ids = [str(row.get("id", "")) for row in entries if isinstance(row, dict)]
        if len(actual_ids) != len(entries) or actual_ids != list(CHARACTER_BASE_STAGES):
            missing = [stage_id for stage_id in CHARACTER_BASE_STAGES if stage_id not in actual_ids]
            return (
                "CHARACTER_BASE guard is missing or misordering the five required stages"
                + (f": {', '.join(missing)}" if missing else "")
                + "; reconcile it with the executed plan and continue the incomplete stages."
            )
    pending = pending_required_stages(state)
    if pending:
        return "Task completion is forbidden while required stages remain pending: " + ", ".join(pending)
    if is_project_image_generation(state):
        for entry in required_stage_entries(state):
            if entry.get("status") != "COMPLETED":
                continue
            stage_evidence = entry.get("evidence", [])
            if not stage_evidence:
                return f"Completed image stage {entry.get('id')} has no current image evidence; review and register its actual result."
            hashes = entry.get("evidence_sha256", {})
            binding = entry.get("accepted_attempt_binding")
            if not isinstance(binding, dict):
                return f"Completed image stage {entry.get('id')} has no accepted executed-result binding; review and register its stage result."
            if binding.get("acceptance_generation_id"):
                try:
                    accepted = verified_author_acceptance(state)
                    expected = validate_stage_attempt_binding(state, str(entry.get("id", "")), stage_evidence)
                    if not accepted or binding != expected or int(entry.get("completed_task_revision", -1)) != int(state.get("task_revision", 0)):
                        return "Completed author-acceptance stage binding changed."
                except GuardError as error:
                    return str(error)
                continue
            try:
                expected_binding = validate_stage_attempt_binding(
                    state, str(entry.get("id", "")), stage_evidence
                )
            except GuardError as error:
                return str(error)
            if (
                binding != expected_binding
                or int(binding.get("task_revision", -1)) != int(entry.get("completed_task_revision", -2))
            ):
                return f"Completed image stage {entry.get('id')} is not bound to an accepted current-revision attempt; continue the stage."
            artifact_path = str(Path(str(binding.get("artifact_path", ""))).expanduser().resolve())
            artifact_hash = str(binding.get("artifact_sha256", "")).lower()
            if (
                len(stage_evidence) != 1
                or str(Path(str(stage_evidence[0])).expanduser().resolve()) != str(
                    Path(str(binding.get("stage_evidence_path", ""))).expanduser().resolve()
                )
                or not artifact_hash
                or not Path(artifact_path).is_file()
                or file_sha256(Path(artifact_path)) != artifact_hash
                or not Path(str(binding.get("stage_evidence_path", ""))).is_file()
                or file_sha256(Path(str(binding.get("stage_evidence_path", "")))) != artifact_hash
            ):
                return f"Completed stage evidence no longer matches its accepted executed result for {entry.get('id')}; review or continue."
            for raw_path in stage_evidence:
                evidence_path = Path(str(raw_path)).expanduser()
                if not evidence_path.is_file():
                    return f"Completed stage evidence is missing for {entry.get('id')}; recover the result or continue the request."
                expected = str(hashes.get(str(raw_path), "")).lower() if isinstance(hashes, dict) else ""
                if expected and file_sha256(evidence_path) != expected:
                    return f"Completed stage evidence changed after review for {entry.get('id')}; review the current result or continue."
    if is_project_image_generation(state) and state.get("author_contract_acceptance"):
        try:
            accepted = verified_author_acceptance(state)
        except GuardError as error:
            return str(error)
        delivery = state.get("author_contract_delivery", {})
        if not accepted or delivery.get("generation_id") != accepted["generation_id"] or not delivery.get("delivery_evidence") or delivery.get("receipt_sha256") != accepted["receipt_sha256"]:
            return "Author-contract TEST acceptance requires its own RESULT_DELIVERED receipt."
        return None
    if is_project_image_generation(state):
        latest = next(
            (row for row in state.get("attempts", []) if row.get("attempt_id") == state.get("latest_delivered_attempt_id")),
            None,
        )
        most_recent = state.get("attempts", [])[-1] if state.get("attempts") else None
        if not latest or not most_recent or latest.get("attempt_id") != most_recent.get("attempt_id"):
            return "Image completion requires the latest attempt to be the currently delivered TEST result."
        if int(latest.get("task_revision", -1)) != int(state.get("task_revision", 0)):
            return "The delivered result belongs to an earlier task revision; produce and deliver the corrected result."
        if (
            state.get("latest_delivered_result_status") != "TEST"
            or not state.get("delivered_result_at")
            or latest.get("status") != "DELIVERED"
        ):
            return "Image completion requires an available, QA-passed TEST result and RESULT_DELIVERED receipt."
        artifact = latest.get("provider_artifact")
        if not isinstance(artifact, dict):
            return "Image completion requires the original delivered artifact binding."
        artifact_path = Path(str(artifact.get("path", ""))).expanduser()
        if not artifact_path.is_file():
            return "The delivered image artifact is missing; recover the exact result or continue the request."
        if file_sha256(artifact_path) != str(artifact.get("sha256", "")).lower():
            return "The delivered image artifact changed after review; re-review the current result or continue the request."
    return None


def continuation_guidance(state: dict[str, object]) -> dict[str, object]:
    """Expose an advisory next action so callers continue instead of guessing."""
    reason = completion_block_reason(state)
    can_complete = reason is None
    if can_complete:
        action = "COMPLETE_NOW"
    elif state.get("status") == "BLOCKED":
        action = "RESOLVE_EXTERNAL_BOUNDARY"
    elif state.get("status") in {"STOPPED", "CANCELLED"}:
        action = "USER_STOPPED"
    elif state.get("status") == "WAITING_FOR_USER" or state.get("waiting_since"):
        action = "WAIT_FOR_USER"
    elif any(row.get("status") == "UNKNOWN" for row in state.get("attempts", [])):
        action = "RECONCILE_ATTEMPT"
    elif state.get("active_attempt"):
        active = state.get("active_attempt")
        action = "REVIEW_AND_REGISTER_RESULT" if isinstance(active, dict) and active.get("provider_artifact") else "CHECK_EXECUTION"
    elif state.get("recovery"):
        action = "RECOVER_AND_CONTINUE"
    elif state.get("author_contract_acceptance"):
        action = "DELIVER_RESULT_OR_COMPLETE_ACCEPTED_STAGES"
    elif state.get("next_required_action") == "ESCALATION_ORCHESTRATOR_REQUIRED":
        action = "ESCALATE_AND_REPLAN"
    elif state.get("next_required_action") in {"REVIEW_AND_REGISTER_EXISTING_ARTIFACT", "REVIEW_AND_DELIVER_EXISTING_RESULT"}:
        action = "REVIEW_AND_REGISTER_RESULT"
    elif state.get("attempts") and state["attempts"][-1].get("status") == "RESULT_AVAILABLE":
        latest_result = state["attempts"][-1]
        is_current_revision = int(latest_result.get("task_revision", -1)) == int(state.get("task_revision", 0))
        if latest_result.get("result_status") == "TEST" and is_current_revision:
            action = "DELIVER_RESULT"
        elif latest_result.get("result_status") == "STAGING" and is_current_revision:
            action = "REGISTER_STAGE_RESULT"
        else:
            action = "CONTINUE_GENERATION"
    elif pending_required_stages(state):
        action = "CONTINUE_STAGES" if state.get("delivered_result_at") else "CONTINUE_GENERATION"
    else:
        action = "CONTINUE_GENERATION"
    return {"action": action, "can_complete": can_complete, "reason": reason or "All locked deliverables are complete."}


def completion_continuation(state: dict[str, object]) -> dict[str, object]:
    """Public read-only alias used by the completion hook and status clients."""
    return continuation_guidance(state)


def execution_focus_binding(thread_id: str) -> dict[str, object] | None:
    """Read only the exact chat focus record; never search request folders."""
    if not thread_id.strip():
        return None
    value = _read_binding(_focus_binding_path(thread_id))
    if value is None:
        return None
    if value.get("thread_id") != thread_id or value.get("task_kind") not in {
        "GENERAL", "IMAGE_GENERATION", "IMAGE_GENERATION_NATIVE_DEFAULT", "IMAGE_EDIT"
    }:
        raise GuardError("Execution focus binding is invalid.")
    guard_path = Path(str(value.get("guard_path", ""))).resolve()
    if guard_path.name != "EXECUTION_GUARD.json":
        raise GuardError("Execution focus binding has no canonical guard_path.")
    return value


def checkpoint(
    path: Path,
    *,
    event: str,
    summary: str,
    evidence: Sequence[str] = (),
    stage: str | None = None,
    add_required_stages: Sequence[str] = (),
    user_approved_scope_change: bool = False,
    correction_impact: str | None = None,
    invariant_assertions: Sequence[str] = (),
    invariant_changes: Sequence[str] = (),
    user_approved_invariant_change: bool = False,
    invariant_change_evidence: str = "",
    hard_blocker: bool = False,
    safe_routes_exhausted: bool = False,
    blocker_kind: str = "INTERNAL",
    user_decision_essential: bool = False,
    swimwear_rung: str | None = None,
    observed_topology: str | None = None,
    user_swimwear_override: bool = False,
    user_override_evidence: str = "",
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
    if not is_project_image_generation(state) and not attempt_id and isinstance(state.get("active_attempt"), dict):
        attempt_id = str(state["active_attempt"].get("attempt_id", ""))
    if not summary.strip():
        raise GuardError("Every checkpoint requires a short factual summary.")
    asserted_invariants: dict[str, str] = {}
    applied_invariant_changes: dict[str, dict[str, str | None]] = {}
    correction_binding: dict[str, str] = {}

    if event == "AUTHOR_CONTRACT_ACCEPTED":
        if state.get("status") != "ACTIVE" or state.get("recovery") or len(evidence) != 1:
            raise GuardError("Author-contract acceptance requires an active repaired request and one registered receipt.")
        receipt_path = Path(evidence[0]).resolve()
        assert_active_request_path(receipt_path)
        if receipt_path.parent != path.resolve().parent:
            raise GuardError("Author acceptance must be in this exact guard request folder.")
        try:
            try:
                from tools import style_pack_manager as manager
            except ImportError:
                import style_pack_manager as manager
            latest = state.get("attempts", [])[-1]
            snapshot_path = Path(str(latest.get("execution_snapshot", {}).get("reference_plan_snapshot_path", "")))
            plan = json.loads(snapshot_path.read_text(encoding="utf-8"))
            render_name = str(plan.get("style_name", ""))
            workspace = Path(str(plan.get("pack_path", ""))).resolve().parent
            checked = manager.validate_registered_author_acceptance(manager.make_paths(workspace, render_name), state, receipt_path)
        except (OSError, ValueError, RuntimeError, KeyError, IndexError) as error:
            raise GuardError(f"Author acceptance is not a verified registered TEST receipt: {error}") from error
        state["author_contract_acceptance"] = {key: checked[key] for key in
            ("generation_id", "attempt_id", "output_sha256", "task_revision", "receipt_path", "receipt_sha256")}
        state["author_contract_acceptance"].update({"style_name": render_name, "workspace": str(workspace)})
        state["phase"] = "AUTHOR_CONTRACT_ACCEPTED"
        state["next_required_action"] = "CANON_QA_AND_DELIVERY"
    elif event == "ARTIFACT_RECEIVED":
        if state.get("status") in {"COMPLETE", "STOPPED", "CANCELLED", "WAITING_FOR_USER", "BLOCKED"}:
            raise GuardError("Reconcile the stopped or unknown provider operation before attaching its artifact.")
        if not attempt_id or not state.get("active_attempt"):
            raise GuardError("ARTIFACT_RECEIVED requires the current execution attempt.")
        active = _matching_attempt(state, attempt_id)
        if active.get("status") != "ACTIVE" or state["active_attempt"].get("attempt_id") != attempt_id:
            raise GuardError("Artifact does not belong to the active attempt.")
        if len(evidence) != 1:
            raise GuardError("ARTIFACT_RECEIVED requires exactly one original output path.")
        artifact = Path(evidence[0]).resolve()
        assert_active_request_path(artifact)
        if not artifact.is_file() or artifact.suffix.lower() not in IMAGE_EXTENSIONS:
            raise GuardError("Provider artifact must be an existing image file.")
        receipt = {"path": str(artifact), "sha256": file_sha256(artifact),
                   "result_status": "NOT_CHECKED", "available_at": iso_time(moment)}
        previous = active.get("provider_artifact")
        if previous and (previous.get("path") != receipt["path"] or previous.get("sha256") != receipt["sha256"]):
            raise GuardError("Do not replace the received provider artifact with different bytes.")
        active["provider_artifact"] = previous or receipt
        if isinstance(active.get("execution_snapshot"), dict):
            active["execution_snapshot"]["provider_artifact"] = dict(active["provider_artifact"])
        state["active_attempt"] = dict(active)
        state["status"] = "ACTIVE"
        state["next_required_action"] = "REVIEW_AND_REGISTER_EXISTING_ARTIFACT"
    elif event in {"RECOVERY_REQUIRED", "RECOVERY_RESOLVED"}:
        if state.get("status") in {"COMPLETE", "STOPPED", "CANCELLED", "WAITING_FOR_USER"} or state.get("waiting_since"):
            raise GuardError("Recovery must respect completion, user stop, cancellation, and pending user input.")
        if state.get("status") == "BLOCKED" and state.get("blocker_kind") in {"EXTERNAL", "SAFETY", "USER_INPUT"}:
            raise GuardError("Resolve the documented external boundary before resuming this request.")
        if event == "RECOVERY_REQUIRED":
            if "recovery" not in state:
                state["recovery"] = {
                    "resume_phase": state.get("phase"),
                    "resume_action": state.get("next_required_action"),
                    "started_at": iso_time(moment),
                }
            state["recovery"]["issue"] = summary.strip()
            state["status"] = "ACTIVE"
            outstanding = state.get("active_attempt") or any(a.get("status") == "UNKNOWN" for a in state.get("attempts", []))
            # Never clear provider handles or turn an unknown execution into a retry.
            if outstanding:
                state["next_required_action"] = (
                    "REVIEW_AND_REGISTER_EXISTING_ARTIFACT"
                    if isinstance(state.get("active_attempt"), dict) and state["active_attempt"].get("provider_artifact")
                    else "CHECK_EXECUTION_OR_RECONCILE_ATTEMPT"
                )
            elif state.get("attempts") and state["attempts"][-1].get("status") in {"RESULT_AVAILABLE", "DELIVERED"}:
                # Registration may be retried on exactly this result. Preserve
                # phase AND reconciliation action so the retry validator can
                # identify that result; recovery does not authorize a new call.
                pass
            else:
                state["phase"] = "RECOVERY"
                state["next_required_action"] = "RECOVER_AND_CONTINUE"
            state.pop("blocker", None)
            state.pop("blocker_kind", None)
        else:
            if not isinstance(state.get("recovery"), dict):
                raise GuardError("RECOVERY_RESOLVED requires a recorded recovery issue.")
            if not evidence or not any(str(item).strip() for item in evidence):
                raise GuardError("Record the actual repair evidence before resolving recovery.")
            if state.get("active_attempt") or any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
                raise GuardActionRequired("Reconcile the existing provider attempt before resolving recovery; do not generate again.")
            recovery = state.pop("recovery")
            state["status"] = "ACTIVE"
            latest = state.get("attempts", [])[-1] if state.get("attempts") else None
            if latest and latest.get("status") in {"RESULT_AVAILABLE", "DELIVERED"}:
                if state.get("phase") == "RECOVERY":
                    state["phase"] = recovery.get("resume_phase", "RESULT_AVAILABLE")
                    state["next_required_action"] = recovery.get("resume_action", "REVIEW_AND_DELIVER_EXISTING_RESULT")
            else:
                state["phase"] = "PREFLIGHT"
                state["next_required_action"] = "PREFLIGHT_OR_EXECUTION"
                state.pop("ready_binding", None)
            state["last_recovery"] = {**recovery, "resolved_at": iso_time(moment), "evidence": list(evidence)}
    elif event == "WAITING_FOR_USER":
        if state.get("status") in {"BLOCKED", "COMPLETE", "STOPPED", "CANCELLED"}:
            raise GuardError(f"Cannot enter WAITING_FOR_USER from status {state.get('status')}.")
        if state.get("active_attempt"):
            raise GuardActionRequired("An active attempt must produce a result, refusal, stop, or timeout reconciliation before waiting.")
        if any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
            raise GuardActionRequired("Reconcile the UNKNOWN attempt before asking the user or pausing the task.")
        if state.get("waiting_since"):
            raise GuardError("The guard is already waiting for the user.")
        if (
            is_project_image_generation(state)
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
            if not isinstance(execution_call, dict):
                raise GuardError("Image readiness requires the materialized exact execution call and user selections.")
            binding = validate_reference_plan(
                reference_plan, stage, execution_call, str(state.get("request_id", "")),
                str(state.get("locked_invariants", {}).get("character", "")),
            )
            try:
                plan_metadata = json.loads(Path(reference_plan).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise GuardError(f"Cannot reconcile executable plan stages: {error}") from error
            reconcile_plan_required_stages(state, plan_metadata)
            state["ready_binding"] = binding
        elif state.get("task_kind") == "IMAGE_GENERATION_NATIVE_DEFAULT":
            state["ready_binding"] = validate_native_default_call(
                execution_call, str(state.get("request_id", "")), reference_plan,
                str(state.get("locked_invariants", {}).get("character", "")),
            )
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
        if add_required_stages:
            if state.get("active_attempt") or any(
                row.get("status") in {"ACTIVE", "UNKNOWN"}
                for row in state.get("attempts", []) if isinstance(row, dict)
            ):
                raise GuardError("Cannot add required stages while an execution attempt is active or UNKNOWN.")
            normalized_new: list[str] = []
            seen_new: set[str] = set()
            for raw_stage in add_required_stages:
                value = str(raw_stage).strip()
                key = normalize_stage_key(value)
                if not value or not key or key in seen_new:
                    raise GuardError("Added required stages must be nonempty and unique.")
                seen_new.add(key)
                normalized_new.append(value)
            existing_keys = {normalize_stage_key(str(row.get("id", ""))) for row in required_stage_entries(state)}
            if existing_keys.intersection(seen_new):
                raise GuardError("Added required stage duplicates an existing required stage.")
            state["required_stages"] = [
                *required_stage_entries(state),
                *[{"id": value, "status": "PENDING", "completed_at": None, "evidence": []} for value in normalized_new],
            ]
            changes_added = normalized_new
        else:
            changes_added = []
        changes = list(state.get("scope_changes", []))
        changes.append({"at": iso_time(moment), "summary": summary.strip(), "added_required_stages": changes_added})
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
        if is_project_image_generation(state) and state.get("next_required_action") not in {"CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER", "EXECUTION_STARTED_OR_BLOCKER"}:
            raise GuardError("EXECUTION_STARTED requires one preceding READY_FOR_EXECUTION transition.")
        validate_image_execution_scope(
            state,
            summary=summary,
            output_contract=output_contract,
            user_approved_extra_generation=user_approved_extra_generation,
            extra_generation_evidence=extra_generation_evidence,
        )
        if is_project_image_generation(state):
            bound_stage = str((state.get("ready_binding") or {}).get("stage", "")).strip()
            if state.get("task_kind") == "IMAGE_GENERATION_NATIVE_DEFAULT":
                bound_stage = bound_stage or "SINGLE_PASS"
            elif not bound_stage:
                raise GuardError("EXECUTION_STARTED requires a validated ready stage binding.")
            if stage and normalize_stage_key(stage) != normalize_stage_key(bound_stage):
                raise GuardError(
                    f"Explicit EXECUTION_STARTED stage {stage!r} conflicts with the ready binding {bound_stage!r}."
                )
            stage = bound_stage
        attempt_stage = stage
        if state.get("task_kind") == "IMAGE_GENERATION":
            if not reference_plan:
                raise GuardError("Image EXECUTION_STARTED requires --reference-plan.")
            binding = validate_reference_plan(
                reference_plan, stage, execution_call, str(state.get("request_id", "")),
                str(state.get("locked_invariants", {}).get("character", "")),
            )
            validate_current_stage_output_authority(reference_plan, execution_call, str(state.get("request_id", "")))
            if binding != state.get("ready_binding"):
                raise GuardError("Execution call differs from the ready prompt, plan, references, hashes, roles, or stage.")
            attempt_stage = str(binding.get("stage", ""))
        elif state.get("task_kind") == "IMAGE_GENERATION_NATIVE_DEFAULT":
            binding = validate_native_default_call(
                execution_call, str(state.get("request_id", "")), reference_plan,
                str(state.get("locked_invariants", {}).get("character", "")),
            )
            if binding != state.get("ready_binding"):
                raise GuardError("Execution call differs from the prompt contract bound at readiness.")
        if is_project_image_generation(state):
            attempt_stage = str((state.get("ready_binding") or {}).get("stage") or "SINGLE_PASS")
        new_attempt_id = str(attempt_id or uuid.uuid4())
        if any(row.get("attempt_id") == new_attempt_id for row in state.get("attempts", [])):
            raise GuardError("Duplicate attempt_id is forbidden.")
        replacement_authorization = state.get("replacement_authorization")
        if isinstance(replacement_authorization, dict) and not replacement_authorization.get("consumed"):
            auth_path = Path(str(replacement_authorization.get("path", "")))
            try:
                auth_raw = auth_path.read_bytes()
                auth_record = json.loads(auth_raw.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise GuardError(f"Replacement authorization changed before execution: {error}") from error
            if (hashlib.sha256(auth_raw).hexdigest() != replacement_authorization.get("sha256")
                    or auth_record.get("request_id") != state.get("request_id")
                    or auth_record.get("attempt_id") != replacement_authorization.get("attempt_id")
                    or auth_record.get("question") != REPLACEMENT_AUTHORIZATION_QUESTION
                    or auth_record.get("answer") != REPLACEMENT_AUTHORIZATION_ANSWER
                    or auth_record.get("source") != REPLACEMENT_AUTHORIZATION_SOURCE):
                raise GuardError("Replacement authorization binding changed; execution is not authorized.")
        execution_snapshot_fields: dict[str, object] = {}
        plan_snapshot: dict[str, object] | None = None
        if state.get("task_kind") == "IMAGE_GENERATION":
            if not reference_plan:
                raise GuardError("Image execution snapshot requires the exact REFERENCE_PLAN path.")
            plan_path = Path(reference_plan).expanduser().resolve()
            request_folder = Path(path).expanduser().resolve().parent
            if not plan_path.is_relative_to(request_folder):
                raise GuardError("Executed REFERENCE_PLAN must remain inside the guard-scoped request directory.")
            try:
                plan_snapshot = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise GuardError(f"Cannot snapshot the executed REFERENCE_PLAN: {error}") from error
            if not isinstance(plan_snapshot, dict) or not isinstance(execution_call, dict):
                raise GuardError("Executed-call snapshot requires the validated plan and exact execution call.")
            reconcile_plan_required_stages(state, plan_snapshot)
            call_json = json.dumps(execution_call, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            call_stage = str(execution_call.get("stage_id", "")).strip()
            purpose = str(plan_snapshot.get("generation_purpose", "")).strip().upper()
            if not call_stage:
                raise GuardError("Executed-call snapshot is missing its canonical executable stage.")
            plan_snapshot_path = plan_path.parent / f"EXECUTED_PLAN_{hashlib.sha256(new_attempt_id.encode('utf-8')).hexdigest()[:20]}.json"
            atomic_write_json(plan_snapshot_path, plan_snapshot)
            execution_snapshot_fields = {
                "request_purpose": purpose,
                "execution_stage": call_stage,
                "reference_plan_snapshot_path": str(plan_snapshot_path.resolve()),
                "reference_plan_snapshot_sha256": file_sha256(plan_snapshot_path),
                "execution_call": execution_call,
            }
        elif state.get("task_kind") == "IMAGE_GENERATION_NATIVE_DEFAULT":
            call_json = json.dumps(execution_call, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            execution_snapshot_fields = {
                "request_purpose": "NATIVE_DEFAULT",
                "execution_stage": attempt_stage or "SINGLE_PASS",
                "execution_call": execution_call,
                "execution_call_sha256": hashlib.sha256(call_json.encode("utf-8")).hexdigest(),
            }
        asserted_invariants = execution_start_assertions(state, invariant_assertions)
        active_swimwear_attempt = validate_swimwear_execution_start(
            state,
            stage=stage,
            swimwear_rung=swimwear_rung,
            user_swimwear_override=user_swimwear_override,
            user_override_evidence=user_override_evidence,
            plan=plan_snapshot if isinstance(plan_snapshot, dict) else None,
            execution_call=execution_call if isinstance(execution_call, dict) else None,
        )
        if active_swimwear_attempt:
            state["active_swimwear_attempt"] = active_swimwear_attempt
        record = {
            "attempt_id": new_attempt_id,
            "status": "ACTIVE",
            "started_at": iso_time(moment),
            "reference_binding": state.get("ready_binding"),
            "stage": attempt_stage,
            "task_revision": int(state.get("task_revision", 0)),
            "execution_snapshot": {
                "request_id": str(state.get("request_id", "")),
                "attempt_id": new_attempt_id,
                "task_revision": int(state.get("task_revision", 0)),
                "stage": attempt_stage,
                "guard_stage": attempt_stage,
                "prompt_sha256": str((state.get("ready_binding") or {}).get("prompt_sha256", "")),
                "execution_call_sha256": str((state.get("ready_binding") or {}).get("execution_call_sha256", "")),
                "reference_binding": state.get("ready_binding"),
                "provider_operation_receipt": provider_operation_receipt.strip(),
                **execution_snapshot_fields,
            },
        }
        if provider_operation_receipt.strip():
            record["provider_operation_receipt"] = provider_operation_receipt.strip()
        state["attempts"] = [*state.get("attempts", []), record]
        if isinstance(replacement_authorization, dict) and not replacement_authorization.get("consumed"):
            replacement_authorization["consumed"] = True
            replacement_authorization["consumed_by_attempt_id"] = new_attempt_id
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
        if is_project_image_generation(state):
            if not evidence_paths:
                raise GuardError("Image generation requires a real output path as visible-result evidence.")
            if state.get("task_kind") == "IMAGE_GENERATION_NATIVE_DEFAULT" and len(evidence_paths) != 1:
                raise GuardError("IMAGE_GENERATION_NATIVE_DEFAULT requires exactly one output image.")
            for item in evidence_paths:
                file = Path(item)
                if not file.is_file() or file.suffix.lower() not in IMAGE_EXTENSIONS:
                    raise GuardError(f"Visible image evidence is missing or unsupported: {file}")
        elif not evidence_paths and not summary.strip():
            raise GuardError("A visible result requires file evidence or a factual user-facing result summary.")
        status_value = str(result_status or "AVAILABLE").upper()
        if is_project_image_generation(state) and status_value not in {"TEST", "STAGING", "REJECTED"}:
            raise GuardError("Image result availability requires result_status TEST, STAGING, or REJECTED.")
        if late_after_stop:
            active["late_output"] = {"result_status": status_value, "evidence": evidence_paths, "available_at": iso_time(moment)}
        else:
            active["status"] = "RESULT_AVAILABLE"
            active["result_status"] = status_value
            active["result_evidence"] = evidence_paths
            active["result_available_at"] = iso_time(moment)
        # Keep the exact provider artifact attached to the executed-call snapshot.
        # Registration can then retry against this artifact without reopening generation.
        registered_artifact_hash = file_sha256(Path(evidence_paths[0])) if evidence_paths else ""
        previous_artifact = active.get("provider_artifact")
        if isinstance(previous_artifact, dict):
            previous_path = Path(str(previous_artifact.get("path", ""))).expanduser()
            previous_hash = str(previous_artifact.get("sha256", "")).lower()
            if (
                not previous_path.is_file()
                or not previous_hash
                or file_sha256(previous_path) != previous_hash
                or registered_artifact_hash != previous_hash
            ):
                raise GuardError("VISIBLE_RESULT registration does not match the original ARTIFACT_RECEIVED path/hash.")
            active["provider_artifact"] = {
                **previous_artifact,
                "result_status": status_value,
                "available_at": iso_time(moment),
                "registered_evidence": evidence_paths,
            }
        else:
            active["provider_artifact"] = {
                "path": evidence_paths[0] if evidence_paths else "",
                "sha256": registered_artifact_hash,
                "result_status": status_value,
                "available_at": iso_time(moment),
                "registered_evidence": evidence_paths,
            }
        snapshot = active.get("execution_snapshot")
        if isinstance(snapshot, dict):
            snapshot["provider_artifact"] = dict(active["provider_artifact"])
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
        accepted = verified_author_acceptance(state)
        if accepted:
            if attempt_id and attempt_id != accepted["attempt_id"]:
                raise GuardError("Delivery attempt differs from accepted original artifact.")
            if not delivery_evidence or any(not str(item).strip() for item in delivery_evidence):
                raise GuardError("Author acceptance delivery requires a UI message/link receipt.")
            state["author_contract_delivery"] = {"generation_id": accepted["generation_id"], "receipt_sha256": accepted["receipt_sha256"],
                "delivery_evidence": [str(item).strip() for item in delivery_evidence], "delivered_at": iso_time(moment)}
            state["delivered_result_at"] = iso_time(moment)
            state["phase"] = "RESULT_DELIVERED"
            state["next_required_action"] = "NEXT_EXECUTION_OR_COMPLETE_OR_BLOCKER"
        else:
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
            if is_project_image_generation(state) and any(
                not Path(item).is_file() or Path(item).suffix.lower() not in IMAGE_EXTENSIONS for item in resolved_paths
            ):
                raise GuardError("AVAILABLE reconciliation requires existing image output evidence.")
            if state.get("task_kind") == "IMAGE_GENERATION_NATIVE_DEFAULT" and len(resolved_paths) != 1:
                raise GuardError("IMAGE_GENERATION_NATIVE_DEFAULT requires exactly one reconciled output image.")
        else:
            resolved_paths = []
        attempt["reconciliation"] = {"outcome": resolved, "evidence": reconciliation_evidence.strip(), "at": iso_time(moment)}
        attempt["status"] = "UNKNOWN" if resolved == "UNKNOWN" else resolved
        state["status"] = "STOPPED" if was_stopped else "ACTIVE"
        state["phase"] = "ATTEMPT_UNKNOWN" if resolved == "UNKNOWN" else (f"ATTEMPT_{resolved}" if not was_stopped else "STOPPED")
        state["next_required_action"] = "RECONCILE_UNKNOWN_ATTEMPT" if resolved == "UNKNOWN" else ("USER_RESUMED" if was_stopped else "NEXT_SAFE_EXECUTION_OR_COMPLETE")
        if resolved == "AVAILABLE":
            if is_project_image_generation(state) and resolved_paths:
                artifact = {
                    "path": resolved_paths[0],
                    "sha256": resolved_hashes[0],
                    "result_status": result_status.upper(),
                    "available_at": iso_time(moment),
                    "reconciliation_evidence": reconciliation_evidence.strip(),
                }
                attempt["provider_artifact"] = artifact
                snapshot = attempt.get("execution_snapshot")
                if isinstance(snapshot, dict):
                    snapshot["provider_artifact"] = dict(artifact)
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
    elif event == "USER_AUTHORIZED_REPLACEMENT":
        if state.get("task_kind") != "IMAGE_GENERATION_NATIVE_DEFAULT":
            raise GuardError("USER_AUTHORIZED_REPLACEMENT is limited to a native-default image request.")
        if state.get("active_attempt") or state.get("replacement_authorization"):
            raise GuardError("A replacement authorization is single-use and requires no active attempt.")
        unknown = [row for row in state.get("attempts", []) if row.get("status") == "UNKNOWN"]
        if len(unknown) != 1 or not attempt_id or unknown[0].get("attempt_id") != attempt_id:
            raise GuardError("Replacement authorization must target the exact sole UNKNOWN attempt.")
        if len(evidence) != 1:
            raise GuardError("Replacement authorization requires one structured user authorization receipt.")
        authorization_path = Path(evidence[0]).expanduser().resolve()
        request_folder = Path(path).expanduser().resolve().parent
        if authorization_path.parent != request_folder or not authorization_path.is_file():
            raise GuardError("Authorization receipt must be an existing file in this exact request folder.")
        try:
            authorization_raw = authorization_path.read_bytes()
            authorization = json.loads(authorization_raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GuardError(f"Structured replacement authorization is unreadable: {error}") from error
        if (not isinstance(authorization, dict)
                or set(authorization) != {"request_id", "attempt_id", "question", "answer", "source"}
                or authorization.get("request_id") != state.get("request_id")
                or authorization.get("attempt_id") != attempt_id
                or authorization.get("question") != REPLACEMENT_AUTHORIZATION_QUESTION
                or authorization.get("answer") != REPLACEMENT_AUTHORIZATION_ANSWER
                or authorization.get("source") != REPLACEMENT_AUTHORIZATION_SOURCE):
            raise GuardError("Authorization receipt does not prove the user's explicit replacement decision.")
        authorization_binding = {
            "path": str(authorization_path),
            "sha256": hashlib.sha256(authorization_raw).hexdigest(),
            "request_id": str(state["request_id"]),
            "attempt_id": attempt_id,
            "answer": authorization["answer"],
        }
        unknown_attempt = _matching_attempt(state, attempt_id, allow_unknown=True)
        unknown_attempt["status"] = "ABANDONED_UNKNOWN"
        unknown_attempt["outcome"] = "NOT_KNOWN"
        unknown_attempt["abandoned_at"] = iso_time(moment)
        unknown_attempt["replacement_authorization"] = dict(authorization_binding)
        state["replacement_authorization"] = {**authorization_binding, "consumed": False}
        state["active_attempt"] = None
        state["status"] = "ACTIVE"
        state["phase"] = "PREFLIGHT"
        state["next_required_action"] = "PREFLIGHT_OR_EXECUTION"
        state["execution_started_at"] = None
        state.pop("ready_binding", None)
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
        attempt_stage = str(rejected_attempt.get("stage", "")).strip()
        if not stage or not stage.strip():
            stage = attempt_stage
        elif (
            attempt_stage
            and normalize_stage_key(attempt_stage) != "single_pass"
            and normalize_stage_key(stage) != normalize_stage_key(attempt_stage)
        ):
            raise GuardError("ATTEMPT_REJECTED stage does not match the rejected execution attempt.")
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
        # The current rejection is appended after this transition; stage-level
        # diagnosis counts it against corrections or prior diagnosis events.
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
        if not stage and is_project_image_generation(state):
            completed_stages = [
                str(row.get("id", "")) for row in required_stage_entries(state)
                if row.get("status") == "COMPLETED"
            ]
            if len(required_stage_entries(state)) > 1 and completed_stages:
                raise GuardError(
                    "USER_CORRECTION must identify the affected completed stage(s) from the existing user request "
                    f"and retry the checkpoint with --stage; no stages were changed. Completed: {', '.join(completed_stages)}."
                )
        if not stage:
            latest_rejection = next(
                (row for row in reversed(state.get("events", [])) if row.get("event") == "ATTEMPT_REJECTED"),
                None,
            )
            if latest_rejection:
                stage = str(latest_rejection.get("stage", "")).strip() or None
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
        # Bind an explicit correction to the latest same-stage rejection for
        # audit context. Escalation is stage-failure-based and does not depend
        # on a correction being present.
        if stage:
            prior_rejection = next(
                (
                    row
                    for row in reversed(state.get("events", []))
                    if row.get("event") == "ATTEMPT_REJECTED"
                    and normalize_stage_key(str(row.get("stage", ""))) == normalize_stage_key(stage)
                ),
                None,
            )
            if prior_rejection:
                correction_binding = {
                    "corrects_attempt_at": str(prior_rejection.get("at", "")),
                    "corrects_stage": str(stage),
                    "corrects_qa_layer": qa_layer.strip().upper() or str(prior_rejection.get("qa_layer", "")).upper(),
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
        correction_stage = stage
        if not correction_stage:
            image_stages = required_stage_entries(state)
            if is_project_image_generation(state) and len(image_stages) == 1:
                correction_stage = str(image_stages[0].get("id", "")) or None
        if correction_stage:
            corrected_stage = next(
                (item for item in required_stage_entries(state)
                 if normalize_stage_key(str(item.get("id", ""))) == normalize_stage_key(correction_stage)),
                None,
            )
            if corrected_stage and corrected_stage.get("status") == "COMPLETED":
                corrected_stage["status"] = "PENDING"
                corrected_stage["completed_at"] = None
                corrected_stage["evidence"] = []
                corrected_stage.pop("evidence_sha256", None)
                corrected_stage.pop("accepted_attempt_binding", None)
                append_event(
                    state, "STAGE_REOPENED",
                    f"USER_CORRECTION reopened affected stage {corrected_stage.get('id')}.",
                    moment, stage=str(corrected_stage.get("id")), cause="USER_CORRECTION",
                )
    elif event == "STAGE_COMPLETED":
        if state.get("status") in {"BLOCKED", "COMPLETE", "WAITING_FOR_USER", "STOPPED", "CANCELLED"} or state.get("waiting_since"):
            raise GuardError(f"Cannot complete a required stage from status {state.get('status')}; explicitly resume first.")
        if state.get("active_attempt") or any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
            raise GuardActionRequired("Reconcile the outstanding operation before completing a stage.")
        entry = find_required_stage(state, stage)
        if entry.get("status") == "COMPLETED":
            raise GuardError(f"Required stage is already completed: {entry.get('id')}")
        evidence_paths = validate_stage_evidence(state, evidence)
        stage_attempt_binding = (
            validate_stage_attempt_binding(state, str(entry.get("id", "")), evidence_paths)
            if is_project_image_generation(state) else None
        )
        if is_physique_stage(str(entry.get("id"))):
            active_swimwear_attempt = state.get("active_swimwear_attempt")
            child_contract = state.get("character_base_contract", {})
            if isinstance(child_contract, dict) and child_contract.get("age_group") == "CHILD":
                if active_swimwear_attempt or swimwear_rung or observed_topology:
                    raise GuardError("Child physique completion forbids adult swimwear ladder and topology receipts.")
                if not isinstance(stage_attempt_binding, dict):
                    raise GuardError("Child physique completion requires its exact accepted execution-call binding.")
            else:
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
        entry["evidence_sha256"] = {
            evidence_path: file_sha256(Path(evidence_path))
            for evidence_path in evidence_paths
            if Path(evidence_path).is_file()
        }
        if stage_attempt_binding is not None:
            entry["accepted_attempt_binding"] = stage_attempt_binding
            entry["completed_task_revision"] = int(state.get("task_revision", 0))
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
        entry.pop("evidence_sha256", None)
        entry.pop("accepted_attempt_binding", None)
        entry.pop("completed_task_revision", None)
        if is_physique_stage(str(entry.get("id"))):
            state.pop("active_swimwear_attempt", None)
        state["status"] = "ACTIVE"
        state["phase"] = "CORRECTION"
        state["next_required_action"] = "NEXT_EXECUTION_OR_BLOCKER"
    elif event == "BLOCKER":
        kind = blocker_kind.upper()
        if kind not in {"INTERNAL", "EXTERNAL", "SAFETY", "USER_INPUT"}:
            raise GuardError("Unknown blocker kind.")
        if kind == "INTERNAL":
            return checkpoint(path, event="RECOVERY_REQUIRED", summary=summary, evidence=evidence, now=moment)
        if state.get("active_attempt") or any(a.get("status") == "UNKNOWN" for a in state.get("attempts", [])):
            raise GuardActionRequired("Record STOP/timeout and reconcile the active operation before declaring a blocker.")
        if not (hard_blocker and safe_routes_exhausted and evidence):
            raise GuardError("External boundaries require --hard-blocker, --safe-routes-exhausted and concrete --evidence; internal issues use RECOVERY_REQUIRED.")
        pending = pending_required_stages(state)
        if is_project_image_generation(state) and pending:
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
        state["blocker_kind"] = kind
    elif event == "COMPLETE":
        completion_error = completion_block_reason(state)
        if completion_error:
            if state.get("recovery") or state.get("active_attempt") or any(
                a.get("status") == "UNKNOWN" for a in state.get("attempts", [])
            ) or pending_required_stages(state):
                raise GuardActionRequired(completion_error)
            raise GuardError(completion_error)
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
    if event == "EXECUTION_STARTED" and is_project_image_generation(state):
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
    if event in {"ATTEMPT_REJECTED", "ESCALATION_ORCHESTRATOR_RECORDED"} and qa_layer.strip():
        event_details["qa_layer"] = qa_layer.strip().upper()
    if event == "BLOCKER":
        event_details["hard_blocker"] = hard_blocker
        event_details["safe_routes_exhausted"] = safe_routes_exhausted
        event_details["blocker_kind"] = blocker_kind.upper()
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
    if event in {"ATTEMPT_UNKNOWN", "ATTEMPT_RECONCILED", "ATTEMPT_REFUSED", "STOP", "CANCEL", "USER_AUTHORIZED_REPLACEMENT"}:
        event_details["outcome"] = outcome or event
        if reconciliation_evidence:
            event_details["reconciliation_evidence"] = reconciliation_evidence.strip()
    if event == "USER_AUTHORIZED_REPLACEMENT":
        event_details["outcome"] = "NOT_KNOWN"
        event_details["authorization_sha256"] = state["replacement_authorization"]["sha256"]
    if event == "ATTEMPT_REJECTED":
        event_details["attempt_id"] = str(attempt_id)
        event_details["outcome"] = "QA_REJECTED"
    append_event(state, event, summary, moment, **event_details)
    atomic_write_json(path, state)
    if event in {"COMPLETE", "USER_CORRECTION", "STAGE_REOPENED"} and state.get("owner_chat_id"):
        chat_binding = _chat_binding_path(str(state["owner_chat_id"]))
        current_binding = _read_binding(chat_binding)
        if current_binding and current_binding.get("active_folder") == str(path.resolve().parent):
            # Completion ends execution, not the user's ability to correct the
            # current delivered art. A new request replaces this binding only
            # while COMPLETE; corrections reactivate the same single folder.
            current_binding["request_status"] = state["status"]
            atomic_write_json(chat_binding, current_binding)
    if (
        event in {"USER_RESUMED", "USER_CORRECTION", "EXECUTION_STARTED"}
        and state.get("owner_chat_id")
        and is_project_image_generation(state)
    ):
        _write_execution_focus(str(state["owner_chat_id"]), path, str(state["task_kind"]))
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
    state["continuation"] = continuation_guidance(state)
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
        choices=("IMAGE_GENERATION", "IMAGE_GENERATION_NATIVE_DEFAULT", "IMAGE_EDIT", "GENERAL"),
        default="IMAGE_GENERATION",
        help="IMAGE_GENERATION_NATIVE_DEFAULT is the single-output StoryArt lane for exact prompts with no plan or references.",
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
            "ATTEMPT_REJECTED", "ATTEMPT_REFUSED", "ATTEMPT_UNKNOWN", "ATTEMPT_RECONCILED", "USER_AUTHORIZED_REPLACEMENT", "STOP", "CANCEL",
            "ESCALATION_ORCHESTRATOR_RECORDED", "USER_CORRECTION", "STAGE_COMPLETED", "STAGE_REOPENED",
            "BLOCKER", "COMPLETE", "RECOVERY_REQUIRED", "RECOVERY_RESOLVED", "ARTIFACT_RECEIVED", "AUTHOR_CONTRACT_ACCEPTED",
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
    check.add_argument("--add-required-stage", action="append", default=[], help="Add a mandatory deliverable stage during an explicitly user-approved SCOPE_CHANGE; cannot run while an attempt is ACTIVE or UNKNOWN.")
    check.add_argument("--qa-layer", default="", help="Optional descriptive QA layer; escalation is tracked by required stage, not this label.")
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
    check.add_argument("--blocker-kind", choices=("INTERNAL", "EXTERNAL", "SAFETY", "USER_INPUT"), default="INTERNAL",
                       help="Internal failures continue via recovery; only evidenced external boundaries may become BLOCKED.")
    check.add_argument("--safe-routes-exhausted", action="store_true")
    check.add_argument("--user-decision-essential", action="store_true")
    check.add_argument("--swimwear-rung", choices=(DEFAULT_SWIMWEAR_RUNG, *SWIMWEAR_OVERRIDE_RUNGS))
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
        "--user-override-evidence",
        default="",
        help="Exact user instruction required when overriding the project-default two-piece swimsuit.",
    )

    status = subparsers.add_parser("status", help="Show the current guard state and enforce elapsed-time limits.")
    status.add_argument("--state", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if __package__:
        from . import workflow_recovery
    else:
        import workflow_recovery
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
            assert_active_request_path(Path(args.state))
            execution_call = None
            if args.execution_call:
                assert_active_request_path(Path(args.execution_call))
                try:
                    execution_call = json.loads(Path(args.execution_call).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise GuardError(f"Cannot read execution_call JSON: {error}") from error
            elif args.reference_plan and args.event.upper() in {"READY_FOR_EXECUTION", "EXECUTION_STARTED"}:
                assert_active_request_path(Path(args.reference_plan))
                try:
                    referenced_plan = json.loads(Path(args.reference_plan).read_text(encoding="utf-8"))
                    candidate_call = referenced_plan.get("execution_call") if isinstance(referenced_plan, dict) else None
                    execution_call = candidate_call if isinstance(candidate_call, dict) else None
                except (OSError, json.JSONDecodeError) as error:
                    raise GuardError(f"Cannot load exact execution_call from REFERENCE_PLAN: {error}") from error
            state = checkpoint(
                Path(args.state),
                event=args.event,
                summary=args.summary,
                evidence=args.evidence,
                stage=args.stage,
                add_required_stages=args.add_required_stage,
                user_approved_scope_change=args.user_approved_scope_change,
                correction_impact=args.correction_impact,
                invariant_assertions=args.invariant_assert,
                invariant_changes=args.invariant_change,
                user_approved_invariant_change=args.user_approved_invariant_change,
                invariant_change_evidence=args.invariant_change_evidence,
                hard_blocker=args.hard_blocker,
                blocker_kind=args.blocker_kind,
                safe_routes_exhausted=args.safe_routes_exhausted,
                user_decision_essential=args.user_decision_essential,
                swimwear_rung=args.swimwear_rung,
                observed_topology=args.observed_topology,
                user_swimwear_override=args.user_swimwear_override,
                user_override_evidence=args.user_override_evidence,
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
        workflow_recovery.emit_recovery(error, phase="request")
        return 2
    except (GuardError, OSError, ValueError) as error:
        print(f"GUARD_ERROR={error}", file=sys.stderr)
        workflow_recovery.emit_recovery(error, phase="request")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
