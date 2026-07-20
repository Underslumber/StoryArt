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


def create_guard(
    path: Path,
    *,
    request_id: str,
    goal: str,
    deliverable: str,
    task_kind: str = "IMAGE_GENERATION",
    allowed_scope: Sequence[str] = (),
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
    moment = now or utc_now()
    state: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id.strip(),
        "task_kind": task_kind.upper(),
        "goal_lock": goal.strip(),
        "primary_deliverable": deliverable.strip(),
        "allowed_scope": [item for item in allowed_scope if item.strip()],
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


def persist_limit_state(path: Path, state: dict[str, object], reason: str, now: datetime) -> None:
    if state.get("status") != "ACTION_REQUIRED" or state.get("action_required_reason") != reason:
        append_event(state, "BUDGET_EXHAUSTED", reason, now)
    state["status"] = "ACTION_REQUIRED"
    state["phase"] = "GUARD_STOP"
    state["next_required_action"] = "EXECUTION_STARTED_OR_BLOCKER"
    state["action_required_reason"] = reason
    atomic_write_json(path, state)


def assert_can_continue(path: Path, state: dict[str, object], now: datetime | None = None) -> None:
    moment = now or utc_now()
    reason = evaluate_limits(state, moment)
    if reason:
        persist_limit_state(path, state, reason, moment)
        raise GuardActionRequired(reason)


def checkpoint(
    path: Path,
    *,
    event: str,
    summary: str,
    evidence: Sequence[str] = (),
    user_approved_scope_change: bool = False,
    now: datetime | None = None,
) -> dict[str, object]:
    state = load_guard(path)
    moment = now or utc_now()
    event = event.upper()
    if not summary.strip():
        raise GuardError("Every checkpoint requires a short factual summary.")

    if event == "WAITING_FOR_USER":
        if state.get("waiting_since"):
            raise GuardError("The guard is already waiting for the user.")
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
        state["scope_changes"] = changes
    elif event == "PREFLIGHT":
        if state.get("next_required_action") in {
            "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER",
            "EXECUTION_STARTED_OR_BLOCKER",
        }:
            raise GuardActionRequired("The task is ready or over budget; more preparation is forbidden.")
        assert_can_continue(path, state, moment)
        state["preflight_actions_in_cycle"] = int(state.get("preflight_actions_in_cycle", 0)) + 1
        state["phase"] = "PREFLIGHT"
    elif event == "READY_FOR_EXECUTION":
        state["status"] = "ACTIVE"
        state["phase"] = "READY_FOR_EXECUTION"
        state["next_required_action"] = "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER"
        state.pop("action_required_reason", None)
    elif event == "CALL_VALIDATED":
        if state.get("next_required_action") != "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER":
            raise GuardError("CALL_VALIDATED is allowed exactly once after READY_FOR_EXECUTION.")
        state["status"] = "ACTIVE"
        state["phase"] = "CALL_VALIDATED"
        state["next_required_action"] = "EXECUTION_STARTED_OR_BLOCKER"
    elif event == "EXECUTION_STARTED":
        if state.get("status") in {"BLOCKED", "COMPLETE"}:
            raise GuardError(f"Cannot start execution from status {state.get('status')}.")
        state["status"] = "ACTIVE"
        state["phase"] = "EXECUTION"
        state["execution_started_at"] = iso_time(moment)
        state["next_required_action"] = "VISIBLE_RESULT_OR_BLOCKER"
        state.pop("action_required_reason", None)
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
    elif event == "BLOCKER":
        state["status"] = "BLOCKED"
        state["phase"] = "BLOCKED"
        state["next_required_action"] = "USER_DECISION"
        state["blocker"] = summary.strip()
    elif event == "COMPLETE":
        if state.get("task_kind") == "IMAGE_GENERATION" and not state.get("first_visible_result_at"):
            raise GuardError("An image-generation task cannot complete without a recorded visible image result.")
        state["status"] = "COMPLETE"
        state["phase"] = "COMPLETE"
        state["next_required_action"] = "NONE"
    else:
        raise GuardError(f"Unknown checkpoint event: {event}")

    append_event(state, event, summary, moment, evidence=list(evidence))
    atomic_write_json(path, state)
    if event == "PREFLIGHT":
        reason = evaluate_limits(state, moment)
        if reason:
            persist_limit_state(path, state, reason, moment)
            raise GuardActionRequired(reason)
    return state


def guard_status(path: Path, now: datetime | None = None) -> dict[str, object]:
    state = load_guard(path)
    moment = now or utc_now()
    reason = evaluate_limits(state, moment)
    if reason:
        persist_limit_state(path, state, reason, moment)
    return state


def require_active_guard(path: Path, request_id: str, now: datetime | None = None) -> dict[str, object]:
    state = load_guard(path)
    if state.get("request_id") != request_id:
        raise GuardError("Execution guard belongs to another request.")
    assert_can_continue(path, state, now)
    if state.get("status") in {"BLOCKED", "COMPLETE"}:
        raise GuardError(f"Execution guard status is {state.get('status')}.")
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
            "READY_FOR_EXECUTION", "CALL_VALIDATED", "EXECUTION_STARTED", "VISIBLE_RESULT", "BLOCKER", "COMPLETE",
        ),
    )
    check.add_argument("--summary", required=True)
    check.add_argument("--evidence", action="append", default=[])
    check.add_argument("--user-approved-scope-change", action="store_true")

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
                user_approved_scope_change=args.user_approved_scope_change,
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
