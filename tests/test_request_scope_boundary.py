"""Request-scope boundaries: only synthetic plans and historical records."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import generation_request, style_pack_manager as manager
from tools import task_execution_guard as guard


def _request_fixture(tmp_path: Path, monkeypatch, binding: str = "unbound"):
    thread_id = "test-thread-boundary"
    monkeypatch.setenv("CODEX_THREAD_ID", thread_id)
    bindings_root = tmp_path / "isolated-chat-bindings"
    monkeypatch.setattr(guard, "_chat_request_root", lambda: bindings_root)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = manager.make_paths(workspace, "Synthetic Style")
    request_folder = paths.generations / "00_PENDING" / "REQ-SYNTHETIC"
    request_folder.mkdir(parents=True)
    plan_path = request_folder / "REFERENCE_PLAN.json"
    plan_path.write_text('{"request_id":"REQ-SYNTHETIC","gate_status":"READY_FOR_GENERATION"}\n', encoding="utf-8")
    guard_path = request_folder / "EXECUTION_GUARD.json"

    if binding == "foreign":
        guard.atomic_write_json(guard._guard_binding_path(guard_path), {
            "thread_id": "different-chat",
            "active_folder": str(request_folder.resolve()),
            "task_kind": "SCENE",
        })
    elif binding in {"same-complete", "same-rebound"}:
        guard.atomic_write_json(guard._guard_binding_path(guard_path), {
            "thread_id": thread_id,
            "active_folder": str(request_folder.resolve()),
            "task_kind": "SCENE",
        })
        if binding == "same-rebound":
            new_folder = paths.generations / "00_PENDING" / "REQ-NEW-ACTIVE"
            guard.atomic_write_json(guard._chat_binding_path(thread_id), {
                "thread_id": thread_id,
                "active_folder": str(new_folder.resolve()),
            })
        # same-complete models COMPLETE deleting the chat index while retaining
        # the folder binding. No old real request data is involved.

    return workspace, paths, request_folder, plan_path, guard_path


def _tripwire_plan_reads(monkeypatch, plan_path: Path) -> list[Path]:
    original = Path.read_text
    reads: list[Path] = []

    def monitored(path: Path, *args, **kwargs):
        if path.resolve() == plan_path.resolve():
            reads.append(path)
            raise AssertionError("request plan was read before scope authorization")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", monitored)
    return reads


@pytest.mark.parametrize("binding", ["unbound", "foreign"])
@pytest.mark.parametrize("entrypoint", ["resolve-call", "reuse", "record", "wrapper"])
def test_foreign_or_unbound_plan_is_rejected_before_read(tmp_path, monkeypatch, binding, entrypoint):
    workspace, paths, request_folder, plan_path, _ = _request_fixture(tmp_path, monkeypatch, binding)
    reads = _tripwire_plan_reads(monkeypatch, plan_path)

    with pytest.raises(manager.StylePackError):
        if entrypoint == "resolve-call":
            manager.command_resolve_call(SimpleNamespace(
                workspace=workspace,
                style_name="Synthetic Style",
                request_id="REQ-SYNTHETIC",
                stage_id="",
            ))
        elif entrypoint == "reuse":
            manager.parse_startup_interaction(SimpleNamespace(
                startup_selection_mode="REUSE",
                reuse_startup_from=str(plan_path),
            ))
        elif entrypoint == "record":
            manager.validate_reference_plan_for_recording(paths, str(plan_path), 90)
        else:
            generation_request._reject_existing_ready_call(SimpleNamespace(
                workspace=workspace,
                style_name="Synthetic Style",
                request_id="REQ-SYNTHETIC",
            ))

    assert reads == []


@pytest.mark.parametrize("binding", ["same-complete", "same-rebound"])
def test_old_same_chat_request_is_rejected_before_plan_read(tmp_path, monkeypatch, binding):
    workspace, _, _, plan_path, _ = _request_fixture(tmp_path, monkeypatch, binding)
    reads = _tripwire_plan_reads(monkeypatch, plan_path)

    with pytest.raises(manager.StylePackError):
        manager.command_resolve_call(SimpleNamespace(
            workspace=workspace,
            style_name="Synthetic Style",
            request_id="REQ-SYNTHETIC",
            stage_id="",
        ))

    assert reads == []


def test_style_discovery_uses_only_top_level_pack_glob(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    canonical_pack = workspace / "Synthetic_PROJECT_PACK"
    for directory in ("00_SOURCE_ORIGINALS", "01_WORK", "02_LOCAL_ONLY_DO_NOT_UPLOAD", "03_UPLOAD_TO_WEB"):
        (canonical_pack / directory).mkdir(parents=True)
    nested_history_pack = (
        workspace / "Synthetic_GENERATIONS" / "00_PENDING" / "REQ-OLD" / "REJECTED" / "Historical_PROJECT_PACK"
    )
    nested_history_pack.mkdir(parents=True)

    original_rglob = Path.rglob

    def forbid_workspace_walk(path: Path, pattern: str):
        if path.resolve() == workspace.resolve():
            raise AssertionError("discovery must not recursively walk the workspace")
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", forbid_workspace_walk)
    discovered = manager.discover_style_packs(workspace)

    assert [Path(item.pack_path).resolve() for item in discovered] == [canonical_pack.resolve()]


def test_live_chat_never_reads_or_hashes_historical_rejected_copy(tmp_path, monkeypatch):
    workspace, paths, active_folder, _, guard_path = _request_fixture(tmp_path, monkeypatch)
    thread_id = "test-thread-boundary"
    guard._bind_guard_to_current_chat(guard_path, "SCENE")
    target = active_folder / "current-output.bin"
    target.write_bytes(b"current-synthetic-output")

    historical_rejected = paths.generations / "00_PENDING" / "REQ-OLD" / "REJECTED" / "old-output.bin"
    historical_rejected.parent.mkdir(parents=True)
    historical_rejected.write_bytes(b"current-synthetic-output")
    paths.generation_manifest.parent.mkdir(parents=True, exist_ok=True)
    with paths.generation_manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["status", "source_image", "archive_file", "style_file", "qa_output_sha256"])
        writer.writeheader()
        writer.writerow({"status": "REJECTED", "style_file": str(historical_rejected)})

    original_hash = manager.sha256
    hashed: list[Path] = []

    def tracked_hash(path: Path) -> str:
        hashed.append(Path(path).resolve())
        return original_hash(path)

    monkeypatch.setattr(manager, "sha256", tracked_hash)
    assert manager.newest_matching_generation(paths, target) is None
    assert hashed == [target.resolve()]
    assert thread_id == os.environ["CODEX_THREAD_ID"]


def test_guard_cli_rejects_foreign_execution_call_before_read(tmp_path, monkeypatch, capsys):
    workspace, paths, active_folder, _, _ = _request_fixture(tmp_path, monkeypatch)
    active_state = active_folder / "EXECUTION_GUARD.json"
    active_state.write_text("{}\n", encoding="utf-8")
    guard._bind_guard_to_current_chat(active_state, "SCENE")

    foreign_call = paths.generations / "00_PENDING" / "REQ-OTHER" / "EXECUTION_CALL.json"
    foreign_call.parent.mkdir(parents=True)
    foreign_call.write_text("{}\n", encoding="utf-8")
    reads = _tripwire_plan_reads(monkeypatch, foreign_call)

    exit_code = guard.main([
        "checkpoint",
        "--state", str(active_state),
        "--event", "READY_FOR_EXECUTION",
        "--summary", "synthetic pre-read boundary",
        "--execution-call", str(foreign_call),
    ])

    assert exit_code != 0
    assert reads == []
    error_output = capsys.readouterr().err
    assert "FOREIGN_REQUEST" in error_output or "UNBOUND_LEGACY_REQUEST" in error_output


def test_structured_request_wrapper_rejects_foreign_request_before_read(tmp_path, monkeypatch, capsys):
    _, paths, _, _, _ = _request_fixture(tmp_path, monkeypatch)
    foreign_request = paths.generations / "00_PENDING" / "REQ-OTHER" / "request.json"
    foreign_request.parent.mkdir(parents=True)
    foreign_request.write_text("{}\n", encoding="utf-8")
    reads = _tripwire_plan_reads(monkeypatch, foreign_request)

    exit_code = generation_request.main(["--request-file", str(foreign_request), "--validate-only"])

    assert exit_code == 2
    assert reads == []
    error_output = capsys.readouterr().err
    assert "FOREIGN_REQUEST" in error_output or "UNBOUND_LEGACY_REQUEST" in error_output
