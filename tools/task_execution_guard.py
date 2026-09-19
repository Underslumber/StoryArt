#!/usr/bin/env python3
"""Bound StoryArt task execution to the user's goal and visible deliverables.

The guard is intentionally small and deterministic.  It does not decide artistic
questions; it records the task contract and stops unbounded preparation, silent
scope expansion, and post-readiness drift.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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


def find_required_stage(state: dict[str, object], stage: str | None) -> dict[str, object]:
    if not stage or not stage.strip():
        raise GuardError("This checkpoint requires --stage.")
    stage_key = stage.strip().casefold()
    for entry in required_stage_entries(state):
        if str(entry.get("id", "")).casefold() == stage_key:
            return entry
    known = ", ".join(str(entry.get("id")) for entry in required_stage_entries(state)) or "none"
    raise GuardError(f"Unknown required stage {stage!r}. Configured stages: {known}")


def is_physique_stage(stage: str | None) -> bool:
    return bool(stage and stage.strip().upper().startswith("PHYSIQUE_"))


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


def swimwear_failures(state: dict[str, object], stage: str) -> set[str]:
    stage_key = stage.strip().casefold()
    return {
        str(event.get("swimwear_rung", "")).upper()
        for event in state.get("events", [])
        if event.get("event") == "ATTEMPT_REJECTED"
        and str(event.get("stage", "")).casefold() == stage_key
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
            and str(row.get("stage", "")).casefold() == stage.casefold()
            and str(row.get("qa_layer", "")).casefold() == qa_layer.casefold()
        )
    correction_index = next(
        (
            index
            for index in range(len(events) - 1, -1, -1)
            if events[index].get("event") == "USER_CORRECTION"
            and str(events[index].get("corrects_stage", "")).casefold() == stage.casefold()
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
    return f"{correction_at}|{stage.casefold()}|{qa_layer.casefold()}"


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
    now: datetime | None = None,
) -> dict[str, object]:
    state = load_guard(path)
    moment = now or utc_now()
    event = event.upper()
    if not summary.strip():
        raise GuardError("Every checkpoint requires a short factual summary.")
    asserted_invariants: dict[str, str] = {}
    applied_invariant_changes: dict[str, dict[str, str | None]] = {}
    correction_binding: dict[str, str] = {}

    if event == "WAITING_FOR_USER":
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
        if not waiting_since:
            raise GuardError("USER_RESUMED requires a prior WAITING_FOR_USER checkpoint.")
        paused = max(0.0, (moment - parse_time(str(waiting_since))).total_seconds())
        state["cycle_paused_seconds"] = int(state.get("cycle_paused_seconds", 0) + paused)
        state["waiting_since"] = None
        state["status"] = "ACTIVE"
        state["phase"] = "PREFLIGHT"
        state["next_required_action"] = "PREFLIGHT_OR_EXECUTION"
    elif event == "SCOPE_CHANGE":
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
    elif event == "READY_FOR_EXECUTION":
        state["status"] = "ACTIVE"
        state["phase"] = "READY_FOR_EXECUTION"
        state["next_required_action"] = "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER"
        state.pop("watchdog_reason", None)
        state.pop("watchdog_triggered_at", None)
    elif event == "CALL_VALIDATED":
        if state.get("next_required_action") != "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER":
            raise GuardError("CALL_VALIDATED is allowed exactly once after READY_FOR_EXECUTION.")
        state["status"] = "ACTIVE"
        state["phase"] = "CALL_VALIDATED"
        state["next_required_action"] = "EXECUTION_STARTED_OR_BLOCKER"
    elif event == "EXECUTION_STARTED":
        if state.get("status") in {"BLOCKED", "COMPLETE"}:
            raise GuardError(f"Cannot start execution from status {state.get('status')}.")
        if state.get("next_required_action") == "ESCALATION_ORCHESTRATOR_REQUIRED":
            raise GuardActionRequired(
                "Two same-layer failures after an explicit correction require one read-only ESCALATION_ORCHESTRATOR result before another attempt."
            )
        validate_image_execution_scope(
            state,
            summary=summary,
            output_contract=output_contract,
            user_approved_extra_generation=user_approved_extra_generation,
            extra_generation_evidence=extra_generation_evidence,
        )
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
        state["status"] = "ACTIVE"
        state["phase"] = "EXECUTION"
        state["execution_started_at"] = iso_time(moment)
        state["next_required_action"] = "VISIBLE_RESULT_OR_BLOCKER"
        state.pop("watchdog_reason", None)
        state.pop("watchdog_triggered_at", None)
    elif event == "VISIBLE_RESULT":
        if not state.get("execution_started_at"):
            raise GuardError("VISIBLE_RESULT requires a prior EXECUTION_STARTED checkpoint.")
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
        timestamp = iso_time(moment)
        state["first_visible_result_at"] = state.get("first_visible_result_at") or timestamp
        state["last_visible_result_at"] = timestamp
        state["execution_started_at"] = None
        state["cycle_started_at"] = timestamp
        state["cycle_paused_seconds"] = 0
        state["preflight_actions_in_cycle"] = 0
        state["status"] = "ACTIVE"
        state["phase"] = "RESULT_AVAILABLE"
        state["next_required_action"] = "NEXT_EXECUTION_OR_COMPLETE_OR_BLOCKER"
        state.pop("watchdog_reason", None)
        state.pop("watchdog_triggered_at", None)
    elif event == "ATTEMPT_REJECTED":
        if state.get("status") in {"BLOCKED", "COMPLETE"}:
            raise GuardError(f"Cannot reject an attempt from status {state.get('status')}.")
        active_swimwear_attempt = state.get("active_swimwear_attempt")
        result_available = bool(active_swimwear_attempt and state.get("phase") == "RESULT_AVAILABLE")
        if not state.get("execution_started_at") and not result_available:
            raise GuardError("ATTEMPT_REJECTED requires a prior EXECUTION_STARTED checkpoint.")
        if stage:
            find_required_stage(state, stage)
        if active_swimwear_attempt:
            expected_stage = str(active_swimwear_attempt.get("stage", ""))
            expected_rung = str(active_swimwear_attempt.get("swimwear_rung", ""))
            if not stage or stage.casefold() != expected_stage.casefold():
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
                    and str(row.get("stage", "")).casefold() == stage.casefold()
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
    elif event == "STAGE_COMPLETED":
        if state.get("status") in {"BLOCKED", "COMPLETE"}:
            raise GuardError(f"Cannot complete a required stage from status {state.get('status')}.")
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
                    and str(row.get("stage", "")).casefold() == str(entry.get("id", "")).casefold()
                    and row.get("swimwear_rung")
                )
            ]
            state["events"] = events
        state["status"] = "ACTIVE"
        state["phase"] = "CORRECTION"
        state["next_required_action"] = "NEXT_EXECUTION_OR_BLOCKER"
    elif event == "BLOCKER":
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
        if state.get("task_kind") == "IMAGE_GENERATION" and not state.get("first_visible_result_at"):
            raise GuardError("An image-generation task cannot complete without a recorded visible image result.")
        pending = pending_required_stages(state)
        if pending:
            raise GuardActionRequired(
                "Task completion is forbidden while required stages remain pending: " + ", ".join(pending)
            )
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
    start.add_argument("--task-kind", choices=("IMAGE_GENERATION", "GENERAL"), default="IMAGE_GENERATION")
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
            "READY_FOR_EXECUTION", "CALL_VALIDATED", "EXECUTION_STARTED", "VISIBLE_RESULT",
            "ATTEMPT_REJECTED", "ESCALATION_ORCHESTRATOR_RECORDED", "USER_CORRECTION", "STAGE_COMPLETED", "STAGE_REOPENED",
            "BLOCKER", "COMPLETE",
        ),
    )
    check.add_argument("--summary", required=True)
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
