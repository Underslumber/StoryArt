"""Narrow regressions for the hash-bound SINGLE_PASS direct-edit reconciliation.

The fixture models the recorded flow: five completed CHARACTER_BASE stages, the immutable
revision-11 SCENE attempt, revision-12 correction, and separate edit/approval/native-storage/
delivery proofs. It does not change an execution guard or create a provider attempt.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools import item_reference_manager, task_execution_guard as guard


def _write(path: Path, value: object, *, raw: bool = False) -> tuple[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value if isinstance(value, bytes) else (
        value.encode("utf-8") if raw else (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    path.write_bytes(data)
    return str(path), hashlib.sha256(data).hexdigest()


def _case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(guard, "assert_active_request_path", lambda _path: None)
    folder = tmp_path / "despar-pre-transfer-kit-20261105"
    request_id = "despar-pre-transfer-kit-20261105"
    files = folder / "proofs"
    provider, provider_hash = _write(files / "provider.png", b"original provider sheet", raw=True)
    evidence, _ = _write(files / "registered.png", Path(provider).read_bytes(), raw=True)
    execution_call = {"request_id": request_id, "stage_id": "SINGLE_PASS",
        "prompt": {"text_sha256": "f" * 64}}
    call_hash = hashlib.sha256(json.dumps(execution_call, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    plan, plan_hash = _write(files / "plan.json", {
        "request_id": request_id, "generation_purpose": "SCENE", "execution_call": execution_call})
    target, target_hash = _write(files / "clean-target.png", b"clean robe fit", raw=True)
    target_hash = guard.DIRECT_EDIT_CLEAN_TARGET_SHA256
    output, output_hash = _write(files / "wizard-boots.png", b"approved footwear edit", raw=True)
    direct_provider, _ = _write(files / "provider-direct-edit.png", Path(output).read_bytes(), raw=True)
    provenance, provenance_hash = _write(files / "edit.json", {
        "request_id": request_id, "latest_exact_correction": guard.DIRECT_EDIT_COMPLETION_CORRECTION,
        "target": target, "target_sha256": target_hash,
        "provider_output": direct_provider, "output": output, "sha256": output_hash,
    })
    permission, permission_hash = _write(files / "permission.json", {
        "request_id": request_id,
        "native_question_id": "call_314032eba1e4425cadbf649e1e0d13ac",
        "exact_answer": "Разрешить исправить служебную проверку завершения",
    })
    approval, approval_hash = _write(files / "approval.json", {
        "request_id": request_id, "status": "APPROVED_BY_AUTHOR",
        "native_question_id": "call_5de3ad84e79f4a6baf591db2c95faa7f",
        "exact_answer": guard.DIRECT_EDIT_COMPLETION_APPROVAL,
        "fit_path": output, "fit_sha256": output_hash,
        "design_sha256": guard.DIRECT_EDIT_ROBE_DESIGN_SHA256,
    })
    approved_character = (tmp_path / "workspace" / "LORANDESORE_GENERATIONS" / "01_APPROVED_CHARACTERS"
                          / "CHAR_002_Despar")
    profile, profile_hash = _write(approved_character / "CHARACTER_PROFILE.yaml", 'character_id: "CHAR_002"\n', raw=True)
    assembly, assembly_hash = _write(approved_character / "00_APPROVED_BASE" / "CHARACTER_ASSEMBLY_SHORTS.png",
                                     b"canonical assembly", raw=True)
    canonical_base_paths = [
        approved_character / "03_CHARACTER_REFERENCES" / "01_FACE" / "CHARACTER_FACE.png",
        approved_character / "03_CHARACTER_REFERENCES" / "02_BODY" / "CHARACTER_BODY_FRONT.png",
        approved_character / "03_CHARACTER_REFERENCES" / "02_BODY" / "CHARACTER_BODY_SIDE.png",
        approved_character / "03_CHARACTER_REFERENCES" / "02_BODY" / "CHARACTER_BODY_BACK.png",
        approved_character / "00_APPROVED_BASE" / "CHARACTER_ASSEMBLY_SHORTS.png",
    ]
    base_proofs = []
    base_evidence = []
    for i, stage in enumerate(guard.CHARACTER_BASE_STAGES):
        content = Path(assembly).read_bytes() if i == 4 else f"base:{stage}".encode()
        path, digest = _write(canonical_base_paths[i], content, raw=True)
        base_proofs.append({"path": path, "sha256": digest})
        evidence_path, _ = _write(files / f"stage-{i}.png", content, raw=True)
        base_evidence.append({evidence_path: digest})
    item_folder = approved_character / "03_CHARACTER_REFERENCES" / "08_TYPED_ITEMS" / "ITEM_DESPAR_ROBE" / "1"
    design, _ = _write(item_folder / "ITEM_DESIGN" / "source.png", b"approved design", raw=True)
    fit_role, output_hash = _write(item_folder / "ITEM_SCALE_FIT" / "source.png", Path(output).read_bytes(), raw=True)
    manifest, manifest_hash = _write(item_folder / "APPROVED_ITEM_REFERENCE.json", {"item_id": "ITEM_DESPAR_ROBE", "version": "1"})
    storage, storage_hash = _write(files / "storage.json", {
        "character_id": "CHAR_002", "item_id": "ITEM_DESPAR_ROBE", "version": "1",
        "native_validation": "PASS", "source_hashes_match": True,
        "manifest": manifest, "profile": profile, "profile_sha256": profile_hash,
        "actual_author_approval": "approval.json",
        "item_design": {"path": design, "sha256": "0388c11343e718be316b4bd9f47f8cc1b8ecccb217d2bd6965f29031324816c6"},
        "item_scale_fit": {"path": fit_role, "sha256": output_hash},
    })
    delivery, delivery_hash = _write(files / "delivery.json", {
        "request_id": request_id, "fit_sha256": output_hash,
        "native_validation": "PASS", "actual_delivery": "shown inline and linked",
    })
    monkeypatch.setattr(guard, "file_sha256", lambda path: (
        {Path(design).resolve(): guard.DIRECT_EDIT_ROBE_DESIGN_SHA256,
         Path(target).resolve(): guard.DIRECT_EDIT_CLEAN_TARGET_SHA256}.get(Path(path).resolve())
        if Path(path).resolve() in {Path(design).resolve(), Path(target).resolve()}
        else hashlib.sha256(Path(path).read_bytes()).hexdigest()
    ))
    monkeypatch.setattr(item_reference_manager, "validate_manifest",
                        lambda *a, **k: {"item_id": "ITEM_DESPAR_ROBE", "version": "1", "item_kind": "CLOTHING"})

    attempt_id = "attempt-rev11"
    attempt = {
        "attempt_id": attempt_id, "stage": "SINGLE_PASS", "task_revision": 11,
        "status": "RESULT_AVAILABLE", "result_status": "TEST",
        "provider_artifact": {"path": provider, "sha256": provider_hash, "result_status": "TEST",
            "registered_evidence": [evidence]},
        "result_evidence": [evidence],
        "reference_binding": {"execution_call_sha256": call_hash},
        "execution_snapshot": {
            "request_id": request_id, "attempt_id": attempt_id, "task_revision": 11,
            "stage": "SINGLE_PASS", "guard_stage": "SINGLE_PASS", "execution_stage": "SINGLE_PASS",
            "execution_call": execution_call, "execution_call_sha256": call_hash,
            "prompt_sha256": "f" * 64,
            "reference_binding": {"execution_call_sha256": call_hash},
            "reference_plan_snapshot_path": plan, "reference_plan_snapshot_sha256": plan_hash,
        },
    }
    base_hashes = [row["sha256"] for row in base_proofs]
    stages = []
    for i, (stage, digest, evidence_sha) in enumerate(zip(guard.CHARACTER_BASE_STAGES, base_hashes, base_evidence)):
        stage_path = next(iter(evidence_sha))
        stages.append({"id": stage, "status": "COMPLETED", "evidence": [stage_path],
            "evidence_sha256": evidence_sha, "completed_task_revision": 11,
            "accepted_attempt_binding": {"attempt_id": f"base-attempt-{i}", "task_revision": 11,
                "result_status": "STAGING", "artifact_path": stage_path,
                "stage_evidence_path": stage_path, "artifact_sha256": digest}})
    stages.append({"id": "SINGLE_PASS", "status": "PENDING"})
    correction_event = {"event": "USER_CORRECTION", "stage": "SINGLE_PASS",
        "summary": guard.DIRECT_EDIT_REOPEN_CORRECTION}
    baseline = {"request_id": request_id, "task_revision": 12, "task_kind": "IMAGE_GENERATION",
        "required_stages": copy.deepcopy(stages), "attempts": [copy.deepcopy(attempt)], "events": [correction_event]}
    baseline_path, baseline_hash = _write(folder / "DIRECT_EDIT_COMPLETION_GUARD_BEFORE.json", baseline)
    receipt = {
        "schema_version": 1, "scope": guard.DIRECT_EDIT_COMPLETION_SCOPE,
        "request_id": request_id, "task_revision": 12, "stage_id": "SINGLE_PASS", "character_id": "CHAR_002",
        "guard_path": str(folder / "EXECUTION_GUARD.json"),
        "guard_before_path": baseline_path, "guard_before_sha256": baseline_hash,
        "authorization": {"path": permission, "sha256": permission_hash},
        "original_attempt": {"attempt_id": attempt_id, "task_revision": 11, "provider_path": provider,
            "provider_sha256": provider_hash, "plan_snapshot_path": plan, "plan_snapshot_sha256": plan_hash},
        "direct_edit": {"provenance_path": provenance, "provenance_sha256": provenance_hash,
            "target_path": target, "target_sha256": target_hash, "output_path": output,
            "output_sha256": output_hash, "latest_exact_correction": guard.DIRECT_EDIT_COMPLETION_CORRECTION},
        "author_approval": {"path": approval, "sha256": approval_hash,
            "question_id": "call_5de3ad84e79f4a6baf591db2c95faa7f",
            "quote": guard.DIRECT_EDIT_COMPLETION_APPROVAL},
        "native_storage": {"checks_path": storage, "checks_sha256": storage_hash,
            "manifest_path": manifest, "manifest_sha256": manifest_hash,
            "profile_path": profile, "profile_sha256": profile_hash,
            "assembly_path": assembly, "assembly_sha256": assembly_hash,
            "base_proofs": base_proofs,
            "item_role_files": [{"path": design, "sha256": guard.DIRECT_EDIT_ROBE_DESIGN_SHA256},
                {"path": fit_role, "sha256": output_hash}]},
        "delivery": {"path": delivery, "sha256": delivery_hash},
    }
    receipt_path, _ = _write(folder / "DIRECT_EDIT_COMPLETION_RECEIPT.json", receipt)
    current = {"request_id": request_id, "task_revision": 12, "task_kind": "IMAGE_GENERATION", "status": "ACTIVE",
        "active_attempt": None, "attempts": [attempt], "required_stages": copy.deepcopy(stages), "events": [correction_event]}
    return current, Path(receipt_path), receipt


def test_hash_bound_direct_edit_receipt_accepts_only_exact_case(tmp_path, monkeypatch):
    current, receipt_path, receipt = _case(tmp_path, monkeypatch)
    result = guard.validate_direct_edit_completion_receipt(
        current, "SINGLE_PASS", receipt_path,
        [receipt["direct_edit"]["output_path"]],
    )
    assert result["direct_edit_completion"] is True
    assert result["task_revision"] == 12
    assert result["artifact_sha256"] == receipt["direct_edit"]["output_sha256"]


@pytest.mark.parametrize("tamper", ["request", "identity", "revision", "approval", "stage"])
def test_direct_edit_receipt_rejects_scope_or_approval_tampering(tmp_path, monkeypatch, tamper):
    current, receipt_path, receipt = _case(tmp_path, monkeypatch)
    if tamper == "request":
        current["request_id"] = "other-request"
    elif tamper == "identity":
        receipt["character_id"] = "CHAR_001"
    elif tamper == "revision":
        current["task_revision"] = 13
    elif tamper == "approval":
        receipt["author_approval"]["quote"] = "different approval"
    else:
        receipt["stage_id"] = "02_PHYSIQUE_FRONT"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(guard.GuardError):
        guard.validate_direct_edit_completion_receipt(current, "SINGLE_PASS", receipt_path)


def test_no_opt_in_does_not_relax_original_attempt_revision_gate(tmp_path, monkeypatch):
    current, _, _ = _case(tmp_path, monkeypatch)
    with pytest.raises(guard.GuardError, match="current-revision"):
        guard.validate_stage_attempt_binding(
            current, "SINGLE_PASS", [current["attempts"][0]["provider_artifact"]["path"]])


def test_direct_edit_delivery_is_the_only_revision_12_completion_route(tmp_path, monkeypatch):
    artifact = tmp_path / "current-fit.png"
    artifact.write_bytes(b"approved fit")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    binding = {
        "direct_edit_completion": True, "receipt_path": str(tmp_path / "DIRECT_EDIT_COMPLETION_RECEIPT.json"),
        "receipt_sha256": "a" * 64, "attempt_id": "original-rev11", "task_revision": 12,
        "artifact_path": str(artifact), "artifact_sha256": digest,
        "stage_evidence_path": str(artifact),
    }
    entry = {"id": "SINGLE_PASS", "status": "COMPLETED", "evidence": [str(artifact)],
        "evidence_sha256": {str(artifact): digest}, "accepted_attempt_binding": binding,
        "completed_task_revision": 12}
    monkeypatch.setattr(guard, "required_stage_entries", lambda _state: [entry])
    monkeypatch.setattr(guard, "pending_required_stages", lambda _state: [])
    monkeypatch.setattr(guard, "is_project_image_generation", lambda _state: True)
    monkeypatch.setattr(guard, "validate_stage_attempt_binding", lambda *a, **k: binding)
    state = {"status": "ACTIVE", "task_revision": 12, "attempts": [], "required_stages": [entry],
        "direct_edit_completion_delivery": {"receipt_path": binding["receipt_path"],
            "receipt_sha256": binding["receipt_sha256"], "delivery_evidence": ["shown inline"]},
        "delivered_result_at": "recorded"}
    assert guard.completion_block_reason(state) is None
    state.pop("direct_edit_completion_delivery")
    assert "RESULT_DELIVERED" in guard.completion_block_reason(state)


def test_checkpoint_stage_delivery_complete_lifecycle_and_late_tamper_rejection(tmp_path, monkeypatch):
    current, receipt_path, receipt = _case(tmp_path, monkeypatch)
    guard_path = receipt_path.parent / "EXECUTION_GUARD.json"
    _write(guard_path, current)
    monkeypatch.setattr(guard, "load_guard", lambda _path: json.loads(guard_path.read_text(encoding="utf-8")))

    state = guard.checkpoint(guard_path, event="STAGE_COMPLETED", summary="Approved direct edit stage",
        stage="SINGLE_PASS", evidence=[receipt["direct_edit"]["output_path"]],
        direct_edit_completion_receipt=receipt_path)
    assert state["required_stages"][-1]["accepted_attempt_binding"]["direct_edit_completion"] is True

    original_validate = guard.validate_stage_attempt_binding
    def validate_stage_binding(state_arg, stage, evidence, local=None, **kwargs):
        if stage == "SINGLE_PASS" and kwargs.get("direct_edit_completion_receipt"):
            return original_validate(state_arg, stage, evidence, local,
                direct_edit_completion_receipt=kwargs["direct_edit_completion_receipt"])
        return next(row["accepted_attempt_binding"] for row in state_arg["required_stages"]
                    if row["id"] == stage)
    monkeypatch.setattr(guard, "validate_stage_attempt_binding", validate_stage_binding)
    with pytest.raises(guard.GuardError, match="RESULT_DELIVERED"):
        guard.checkpoint(guard_path, event="COMPLETE", summary="Must require visible delivery")

    state = guard.checkpoint(guard_path, event="RESULT_DELIVERED", summary="Current outfit shown and linked",
        delivery_evidence=["inline image and native item link"], direct_edit_completion_receipt=receipt_path)
    assert state["direct_edit_completion_delivery"]["receipt_sha256"] == state["required_stages"][-1]["accepted_attempt_binding"]["receipt_sha256"]

    completed = guard.checkpoint(guard_path, event="COMPLETE", summary="All exact deliverables validated")
    assert completed["status"] == "COMPLETE"
    assert guard.completion_continuation(completed)["can_complete"] is True
    with pytest.raises(guard.GuardError, match="status COMPLETE"):
        guard.checkpoint(guard_path, event="STAGE_COMPLETED", summary="Completed request is immutable",
            stage="SINGLE_PASS", evidence=[receipt["direct_edit"]["output_path"]],
            direct_edit_completion_receipt=receipt_path)
    with pytest.raises(guard.GuardError, match="cannot mutate"):
        guard.checkpoint(guard_path, event="RESULT_DELIVERED", summary="No repeated delivery after completion",
            delivery_evidence=["repeat"], direct_edit_completion_receipt=receipt_path)

    output_path = Path(receipt["direct_edit"]["output_path"])
    original_output = output_path.read_bytes()
    output_path.write_bytes(b"tampered after COMPLETE")
    assert guard.completion_continuation(completed)["can_complete"] is False
    output_path.write_bytes(original_output)
    receipt_bytes = receipt_path.read_bytes()
    receipt_path.write_text(receipt_path.read_text(encoding="utf-8").replace(
        guard.DIRECT_EDIT_COMPLETION_CORRECTION, "changed correction"), encoding="utf-8")
    assert guard.completion_continuation(completed)["can_complete"] is False
    receipt_path.write_bytes(receipt_bytes)
    assert guard.completion_continuation(completed)["can_complete"] is True

    current, receipt_path, receipt = _case(tmp_path / "tamper", monkeypatch)
    guard_path = receipt_path.parent / "EXECUTION_GUARD.json"
    _write(guard_path, current)
    monkeypatch.setattr(guard, "load_guard", lambda _path: json.loads(guard_path.read_text(encoding="utf-8")))
    state = guard.checkpoint(guard_path, event="STAGE_COMPLETED", summary="Approved direct edit stage",
        stage="SINGLE_PASS", evidence=[receipt["direct_edit"]["output_path"]],
        direct_edit_completion_receipt=receipt_path)
    monkeypatch.setattr(guard, "validate_stage_attempt_binding", validate_stage_binding)
    state = guard.checkpoint(guard_path, event="RESULT_DELIVERED", summary="Current outfit shown and linked",
        delivery_evidence=["inline image and native item link"], direct_edit_completion_receipt=receipt_path)
    output_path = Path(receipt["direct_edit"]["output_path"])
    output_path.write_bytes(b"tampered after delivery")
    with pytest.raises(guard.GuardError, match="changed|approved current footwear output|output hash"):
        guard.checkpoint(guard_path, event="COMPLETE", summary="Tampered output must fail")
