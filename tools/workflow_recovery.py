"""Structured recovery guidance for internal StoryArt workflow failures.

This module reports the original diagnostic and a concrete scenario continuation.
It never converts a failed operation into success. Guard updates are opt-in and
require the caller to supply the exact guard bound to the current request.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


_ACTIONS = {
    "input": "Correct the reported field in the same request/call file, then rerun the same StoryArt scenario command.",
    "semantic": "Correct the reported semantic input in the same request file, then rerun the same StoryArt scenario command.",
    "finalize": "Repair the reported call-preparation input and resume the same request with --finalize-only --call-file; preserve any READY call and reconcile any active or unknown attempt first.",
    "risk": "Repair the exact prompt or reference assessment input, then resume call preparation for the same request; do not bypass risk validation.",
    "registration": "For registration metadata or manifest errors, retain the same ARTIFACT_RECEIVED path and hash, repair only that record input, and rerun registration for the same attempt without inferring PASS. If visual QA marked the image REJECTED, preserve that failure and continue the same request with a revised strategy from original approved sources; never use the rejected image as an anchor.",
    "request": "Repair the reported preparation/call input and continue the same authorized StoryArt request from its current guard phase.",
}

_NON_MUTATING_REJECTIONS = (
    "CALL_VALIDATED is allowed exactly once",
    "earlier task revision",
    "exact path recorded for the already-visible attempt",
    "already-visible attempt",
    "ALREADY_PREPARED",
)


def recovery_report(error: object, *, phase: str) -> dict[str, Any]:
    """Return a machine-readable, non-success recovery report."""
    phase = phase if phase in _ACTIONS else "request"
    diagnostic = str(error)
    return {
        "schema_version": 1,
        "status": "RECOVERY_REQUIRED",
        "phase": phase,
        "diagnostic": diagnostic,
        "next_action": _ACTIONS[phase],
    }


def record_recovery(guard_path: Path | str | None, report: dict[str, Any]) -> str | None:
    """Record recovery only against a caller-resolved, existing request guard.

    Returning a diagnostic instead of raising keeps the original CLI error
    authoritative while making guard-recording failures visible.
    """
    if guard_path is None:
        return None
    path = Path(guard_path).resolve()
    if (
        path.name != "EXECUTION_GUARD.json"
        or path.parent.parent.name.upper() != "00_PENDING"
        or not path.parent.parent.parent.name.upper().endswith("_GENERATIONS")
        or not path.is_file()
    ):
        return None
    try:
        try:
            from . import task_execution_guard as guard
        except ImportError:
            import task_execution_guard as guard
        state = guard.load_guard(path)
        if str(state.get("request_id", "")) != path.parent.name:
            return None
        diagnostic = str(report.get("diagnostic", ""))
        ready_binding = state.get("ready_binding")
        has_ready_binding = isinstance(ready_binding, dict) and bool(ready_binding)
        latest_attempt = state.get("attempts", [])[-1] if isinstance(state.get("attempts"), list) and state.get("attempts") else {}
        existing_result = (
            isinstance(latest_attempt, dict)
            and latest_attempt.get("status") in {"RESULT_AVAILABLE", "DELIVERED"}
        ) or state.get("phase") in {"RESULT_AVAILABLE", "RESULT_DELIVERED"}
        stale_rejection = any(marker in diagnostic for marker in _NON_MUTATING_REJECTIONS)
        if (state.get("status") == "READY" and has_ready_binding) or (stale_rejection and has_ready_binding and existing_result):
            report["status"] = "EXISTING_OPERATION_PRESERVED"
            report["next_action"] = (
                "The command was rejected against an existing valid READY/result binding. Correct the invocation to match "
                "the current request, plan, attempt, and artifact path/hash; keep the guard and accepted result unchanged."
            )
            report["guard_phase"] = str(state.get("phase", ""))
            report["guard_action"] = str(state.get("next_required_action", ""))
            return None
        state = guard.checkpoint(
            path,
            event="RECOVERY_REQUIRED",
            summary=f"{report['phase']}: {report['diagnostic']}",
        )
        action = str(state.get("next_required_action", "")).upper()
        if action == "CHECK_EXECUTION_OR_RECONCILE_ATTEMPT":
            report["next_action"] = (
                "Reconcile the existing provider attempt and preserve its operation handle before any retry; "
                "then repair the reported input and continue the same request."
            )
        elif action in {"REVIEW_AND_REGISTER_EXISTING_ARTIFACT", "REVIEW_AND_DELIVER_EXISTING_RESULT"}:
            report["next_action"] = (
                "Review and register or deliver the already received artifact/result using its existing path and hash; "
                "do not start another provider operation."
            )
        elif action == "RECOVER_AND_CONTINUE" and report["phase"] != "registration":
            report["next_action"] = (
                "Repair the reported input, record RECOVERY_RESOLVED with concrete repair evidence, "
                "then continue the same request from the guard's restored phase."
            )
        return None
    except Exception as exc:  # recovery reporting must not hide the original failure
        return str(exc)


def emit_recovery(
    error: object,
    *,
    phase: str,
    guard_path: Path | str | None = None,
    stream: Any = None,
) -> dict[str, Any]:
    """Emit a structured report on stderr and optionally record it in a guard."""
    report = recovery_report(error, phase=phase)
    record_error = record_recovery(guard_path, report)
    if record_error:
        report["guard_record_error"] = record_error
    target = stream if stream is not None else sys.stderr
    print("RECOVERY_REPORT=" + json.dumps(report, ensure_ascii=False, separators=(",", ":")), file=target)
    return report
