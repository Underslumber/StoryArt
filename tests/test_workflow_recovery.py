import json
import io
from argparse import Namespace
from pathlib import Path

import pytest

from tools import generation_request, workflow_recovery
from tools import task_execution_guard
from tools import generation_risk_assessor


def test_report_preserves_diagnostic_and_gives_scenario_repair_action():
    report = workflow_recovery.recovery_report("field prompt_text is missing", phase="input")
    assert report["status"] == "RECOVERY_REQUIRED"
    assert report["diagnostic"] == "field prompt_text is missing"
    assert "same request/call file" in report["next_action"]


def test_registration_recovery_distinguishes_rejected_art_from_record_metadata():
    report = workflow_recovery.recovery_report("visual review returned REJECTED", phase="registration")
    assert "REJECTED" in report["next_action"]
    assert "original approved sources" in report["next_action"]
    assert "rejected image as an anchor" in report["next_action"]
    assert "do not regenerate" not in report["next_action"]


def test_request_cli_records_bound_recovery_and_never_reports_success(tmp_path, monkeypatch, capsys):
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps({"request_id": "req-1", "style_name": "S", "fidelity": 90}), encoding="utf-8")
    guard_dir = tmp_path / "S_GENERATIONS" / "00_PENDING" / "req-1"
    guard_dir.mkdir(parents=True)
    guard_path = guard_dir / "EXECUTION_GUARD.json"
    guard_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(generation_request.manager, "make_paths", lambda workspace, style: Namespace(generations=tmp_path / "S_GENERATIONS"))
    monkeypatch.setattr(generation_request.manager, "assert_active_request_path", lambda path: None)
    monkeypatch.setattr(generation_request, "_semantic_errors", lambda args: ["semantic field rejected"])
    recorded = []
    monkeypatch.setattr(task_execution_guard, "load_guard", lambda path: {"request_id": "req-1"})
    monkeypatch.setattr(
        task_execution_guard, "checkpoint",
        lambda path, **kwargs: (recorded.append((path, kwargs)) or {"next_required_action": "RECOVER_AND_CONTINUE"}),
    )

    assert generation_request.main(["--request-file", str(request_file)]) == 2
    captured = capsys.readouterr()
    assert "ERROR=semantic field rejected" in captured.err
    marker = next(line for line in captured.err.splitlines() if line.startswith("RECOVERY_REPORT="))
    report = json.loads(marker.split("=", 1)[1])
    assert report["status"] == "RECOVERY_REQUIRED"
    assert report["diagnostic"] == "semantic field rejected"
    assert "RECOVERY_REQUIRED" == recorded[0][1]["event"]
    assert captured.out == ""
    assert "PASS" not in captured.out
    assert "EXECUTION_CALL" not in captured.out


def test_guard_binding_rejects_foreign_or_noncanonical_path(tmp_path, monkeypatch):
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps({"request_id": "req-1", "style_name": "S", "fidelity": 90}), encoding="utf-8")
    foreign = tmp_path / "other" / "EXECUTION_GUARD.json"
    foreign.parent.mkdir()
    foreign.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(generation_request.manager, "make_paths", lambda workspace, style: Namespace(generations=tmp_path / "S_GENERATIONS"))
    monkeypatch.setattr(generation_request.manager, "assert_active_request_path", lambda path: None)
    assert generation_request._bound_guard_path(json.loads(request_file.read_text(encoding="utf-8"))) is None


def test_outstanding_attempt_changes_recovery_action_to_reconciliation(tmp_path, monkeypatch, capsys):
    guard_path = tmp_path / "S_GENERATIONS" / "00_PENDING" / "req-1" / "EXECUTION_GUARD.json"
    guard_path.parent.mkdir(parents=True)
    guard_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(task_execution_guard, "load_guard", lambda path: {"request_id": "req-1"})
    monkeypatch.setattr(task_execution_guard, "checkpoint", lambda path, **kwargs: {
        "next_required_action": "CHECK_EXECUTION_OR_RECONCILE_ATTEMPT",
        "active_attempt": {"attempt_id": "provider-op-1", "status": "ACTIVE"},
    })

    report = workflow_recovery.emit_recovery("late validation failure", phase="finalize", guard_path=guard_path)
    assert report["status"] == "RECOVERY_REQUIRED"
    assert "Reconcile the existing provider attempt" in report["next_action"]
    assert "before any retry" in report["next_action"]
    assert "RECOVERY_REPORT=" in capsys.readouterr().err


def test_rejected_duplicate_command_preserves_current_ready_binding(tmp_path, monkeypatch, capsys):
    guard_path = tmp_path / "S_GENERATIONS" / "00_PENDING" / "req-1" / "EXECUTION_GUARD.json"
    guard_path.parent.mkdir(parents=True)
    guard_path.write_text("{}", encoding="utf-8")
    state = {
        "request_id": "req-1", "status": "READY", "phase": "READY_FOR_EXECUTION",
        "next_required_action": "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER",
        "ready_binding": {"stage": "SINGLE_PASS", "call_sha256": "current-call-hash"},
        "attempts": [],
    }
    monkeypatch.setattr(task_execution_guard, "load_guard", lambda path: state)
    monkeypatch.setattr(task_execution_guard, "checkpoint", lambda *args, **kwargs: pytest.fail("must preserve the existing READY state"))

    report = workflow_recovery.emit_recovery(
        "CALL_VALIDATED is allowed exactly once after READY_FOR_EXECUTION.",
        phase="request",
        guard_path=guard_path,
    )
    assert report["status"] == "EXISTING_OPERATION_PRESERVED"
    assert report["guard_phase"] == "READY_FOR_EXECUTION"
    assert state["status"] == "READY"
    assert state["ready_binding"]["call_sha256"] == "current-call-hash"
    assert "current request, plan, attempt" in report["next_action"]
    assert "RECOVERY_REPORT=" in capsys.readouterr().err


def test_stale_artifact_rejection_preserves_existing_delivered_result(tmp_path, monkeypatch):
    guard_path = tmp_path / "S_GENERATIONS" / "00_PENDING" / "req-1" / "EXECUTION_GUARD.json"
    guard_path.parent.mkdir(parents=True)
    guard_path.write_text("{}", encoding="utf-8")
    state = {
        "request_id": "req-1", "status": "READY", "phase": "READY_FOR_EXECUTION",
        "next_required_action": "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER",
        "ready_binding": {"stage": "CURRENT_REVISION", "call_sha256": "new-call-hash"},
        "attempts": [{"status": "DELIVERED", "task_revision": 0}],
    }
    monkeypatch.setattr(task_execution_guard, "load_guard", lambda path: state)
    monkeypatch.setattr(task_execution_guard, "checkpoint", lambda *args, **kwargs: pytest.fail("must preserve the delivered result and current READY binding"))

    report = workflow_recovery.emit_recovery(
        "A result from an earlier task revision cannot satisfy current delivery.",
        phase="registration",
        guard_path=guard_path,
        stream=io.StringIO(),
    )
    assert report["status"] == "EXISTING_OPERATION_PRESERVED"
    assert state["attempts"][0]["status"] == "DELIVERED"
    assert state["ready_binding"]["call_sha256"] == "new-call-hash"


def test_unbound_error_still_returns_nonzero_and_structured_recovery(tmp_path, monkeypatch, capsys):
    request_file = tmp_path / "bad.json"
    request_file.write_text("{", encoding="utf-8")
    assert generation_request.main(["--request-file", str(request_file)]) == 2
    captured = capsys.readouterr()
    assert "ERROR=request file:" in captured.err
    report = json.loads(next(line for line in captured.err.splitlines() if line.startswith("RECOVERY_REPORT=")).split("=", 1)[1])
    assert report["status"] == "RECOVERY_REQUIRED"
    assert "JSON" in report["diagnostic"] or "Expecting" in report["diagnostic"]
    assert captured.out == ""

def test_risk_cli_failure_keeps_nonzero_exit_and_structured_recovery(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["generation_risk_assessor.py", "--text", "prompt", "--lexicon", str(tmp_path / "missing.json")],
    )
    assert generation_risk_assessor.main() == 2
    captured = capsys.readouterr()
    assert "ERROR=Risk lexicon does not exist" in captured.err
    report = json.loads(next(line for line in captured.err.splitlines() if line.startswith("RECOVERY_REPORT=")).split("=", 1)[1])
    assert report["phase"] == "risk"
    assert report["status"] == "RECOVERY_REQUIRED"
    assert "Risk lexicon does not exist" in report["diagnostic"]
    assert captured.out == ""
