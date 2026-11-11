import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.style_pack_common import StylePackError
from tools.style_pack_contracts import validate_construction_dimension_guide_authorization
from tools import style_pack_manager as manager
from tools import task_execution_guard as guard
from types import SimpleNamespace


def _write(path: Path, data: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def authorization(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    request_id = "request-one"
    request_root = tmp_path / "00_PENDING" / request_id
    request_root.mkdir(parents=True)
    geometry = request_root / "geometry.json"
    geometry_hash = _write(geometry, {
        "request_id": request_id, "character_id": "NEW",
        "design_dimensions": {"total_height": 1560, "head_length": 200, "side_foot_length": 190},
    })
    approval = request_root / "approval.json"
    approval_hash = _write(approval, {
        "schema_version": 1, "request_id": request_id, "character_id": "NEW",
        "scope": "CONSTRUCTION_GEOMETRY_ONLY", "approves_previous_illustration": False,
        "numeric_visible_landmarks_remain": "NOT_CHECKED", "geometry_path": str(geometry),
        "geometry_sha256": geometry_hash, "approval_quote": "Утвердить геометрию",
    })
    guide = tmp_path / "COMPOSITION_CORE_SIDE_DIMENSION_GUIDE.png"
    guide_hash = _write(guide, b"dimension only")
    authorization = request_root / "SIDE_DIMENSION_GUIDE_AUTHORIZATION.json"
    data: dict[str, object] = {
        "schema_version": 1, "request_id": request_id, "character_id": "NEW",
        "stage_id": "03_PHYSIQUE_SIDE", "source_kind": "DETERMINISTIC_DIMENSION_ONLY_NO_SUBJECT",
        "geometry_path": str(geometry), "geometry_sha256": geometry_hash,
        "approved_geometry_evidence": str(approval), "approved_geometry_evidence_sha256": approval_hash,
        "guide_path": str(guide), "guide_sha256": guide_hash,
        "physical_role": "COMPOSITION", "subject_label": "NO_SUBJECT",
        "subject_label_sha256": guide_hash, "subject_label_evidence": "Dimensions only; no subject.",
        "design_height": 1560, "design_head": 200, "design_foot": 190, "profile_direction": "LEFT",
        "authorization_quote": "Создать и приложить размерную схему SIDE",
        "authorization_source": "current chat", "previous_attempt_pixels_used": False,
        "authorization_scope": "Create and physically attach dimension-only guide; narrow source binding permitted; current90%LORANDESORE and selectedBODY_REFERENCE_LIBRARY preserved; mandatorySTYLE/FACE/FRONT retained.",
        "forbidden_influence": ["identity", "style", "clothing", "tattoos", "palette", "rendering", "text_in_final_art"],
        "registered_reference_path": str(guide), "registered_reference_sha256": guide_hash,
        "reference_id": "REF_00216",
    }
    _write(authorization, data)
    return authorization, data


def _validate(path: Path, request_id: str = "request-one", character_id: str = "NEW", stage_id: str = "03_PHYSIQUE_SIDE"):
    return validate_construction_dimension_guide_authorization(
        path, request_root=path.parent, request_id=request_id, character_id=character_id, stage_id=stage_id,
    )


def _manager_plan_and_call(path: Path, data: dict[str, object]):
    binding = _validate(path)
    guide = Path(str(data["guide_path"]))
    plan = {
        "request_id": "request-one", "character_id": "NEW", "style_name": "LORANDESORE",
        "startup_parameter_selection": {"resolved_parameters": {"fidelity": 90, "aux_body_decision": "SELECTED"}},
        "construction_dimension_guide": binding,
    }
    call = {
        "stage_id": "03_PHYSIQUE_SIDE",
        "user_selections": {
            "style": {"choice": "PROJECT_STYLE:LORANDESORE"},
            "reference_policy": {"choice": "BODY_LIBRARY_ONLY"},
        },
        "slots": [
            {"path": str(path.parent / "style.png"), "sha256": "s", "active_roles": ["STYLE"], "physically_attach": True},
            {"path": str(path.parent / "face.png"), "sha256": "f", "active_roles": ["FACE_IDENTITY_STAGE"], "physically_attach": True},
            {"path": str(path.parent / "front.png"), "sha256": "p", "active_roles": ["PHYSIQUE_FRONT_STAGE"], "physically_attach": True},
            {"path": str(guide), "sha256": data["guide_sha256"], "active_roles": ["COMPOSITION"], "physically_attach": True},
        ],
    }
    return plan, call, binding


def _mock_registry(monkeypatch):
    monkeypatch.setattr(manager, "validate_plan_reference", lambda paths, value, label: {
        "path": value, "sha256": "", "subject_label": "NO_SUBJECT", "subject_label_status": "REVIEWED",
        "subject_label_sha256": "", "subject_label_evidence": "full-resolution no-subject review",
    } if label == "COMPOSITION" else {})


def test_authorization_binds_exact_guide_and_consent(authorization):
    path, _ = authorization
    binding = _validate(path)
    assert binding["physical_role"] == "COMPOSITION"
    assert binding["subject_label"] == "NO_SUBJECT"
    assert binding["guide_path"].endswith("COMPOSITION_CORE_SIDE_DIMENSION_GUIDE.png")


def test_manager_exact_call_accepts_only_bound_side_guide(authorization, monkeypatch):
    path, data = authorization
    plan, call, binding = _manager_plan_and_call(path, data)
    _mock_registry(monkeypatch)
    monkeypatch.setattr(manager, "sha256", lambda _path: binding["guide_sha256"])
    registered = manager.validate_plan_reference
    monkeypatch.setattr(manager, "validate_plan_reference", lambda paths, value, label: {
        **registered(paths, value, label), "sha256": binding["guide_sha256"],
        "subject_label_sha256": binding["guide_sha256"],
    })
    paths = SimpleNamespace(generations=path.parent.parent.parent)
    assert manager.validate_construction_dimension_guide_call(paths, plan, call)["path"] == data["guide_path"]
    call["stage_id"] = "02_PHYSIQUE_FRONT"
    with pytest.raises(StylePackError):
        manager.validate_construction_dimension_guide_call(paths, plan, call)


def test_workflow_physically_binds_four_exact_side_references_only(authorization):
    path, data = authorization
    binding = _validate(path)
    workflow = {"mode": "MULTI_STAGE", "stages": [
        {"stage_id": "03_PHYSIQUE_SIDE", "attachment_limit": 5, "attachments_used": 2, "slots": [
            {"path": "style.png", "sha256": "s", "active_roles": ["STYLE"], "stage_role": "STYLE"},
            {"path": "<TARGETED_STAGE_PACK:01_FACE_IDENTITY+02_PHYSIQUE_FRONT>",
             "sha256": "TARGETED_STAGE_PACK:01_FACE_IDENTITY+02_PHYSIQUE_FRONT",
             "active_roles": ["FACE_FRONT_TARGETED_PACK"], "stage_role": "FACE_FRONT_TARGETED_PACK",
             "planned_targeted_pack": True, "targeted_pack_sources": ["01_FACE_IDENTITY", "02_PHYSIQUE_FRONT"]},
        ]},
        {"stage_id": "04_PHYSIQUE_BACK", "slots": [{"path": "back.png", "active_roles": ["STYLE"]}]},
    ]}
    result = manager.bind_construction_dimension_guide_to_side_workflow(workflow, binding, 5)
    side, back = result["stages"]
    assert side["attachments_used"] == 4
    assert [slot["active_roles"] for slot in side["slots"]] == [
        ["STYLE"], ["FACE_IDENTITY_STAGE"], ["PHYSIQUE_FRONT_STAGE"], ["COMPOSITION"],
    ]
    assert all(slot["path"] != data["guide_path"] for slot in back["slots"])


def test_workflow_rejects_wrong_role_or_missing_stage_pack(authorization):
    path, _ = authorization
    binding = _validate(path)
    workflow = {"mode": "MULTI_STAGE", "stages": [{
        "stage_id": "03_PHYSIQUE_SIDE", "attachment_limit": 5, "slots": [
            {"path": "style.png", "active_roles": ["STYLE"]},
            {"path": "unrelated.png", "active_roles": ["POSE"], "planned_targeted_pack": True,
             "targeted_pack_sources": ["02_PHYSIQUE_FRONT"]},
        ],
    }]}
    with pytest.raises(StylePackError):
        manager.bind_construction_dimension_guide_to_side_workflow(workflow, binding, 5)


def test_guard_independently_accepts_exact_four_role_side_binding(authorization, monkeypatch, tmp_path):
    path, data = authorization
    binding = _validate(path)
    _mock_registry(monkeypatch)
    monkeypatch.setattr(manager, "make_paths", lambda workspace, style: SimpleNamespace(pack=Path(workspace) / "LORANDESORE_PROJECT_PACK"))
    registered = manager.validate_plan_reference
    monkeypatch.setattr(manager, "validate_plan_reference", lambda paths, value, label: {
        **registered(paths, value, label), "sha256": binding["guide_sha256"],
        "subject_label_sha256": binding["guide_sha256"],
    })
    guide = str(data["guide_path"])
    slots = [
        {"path": str(tmp_path / "style.png"), "sha256": "s", "active_roles": ["STYLE"], "physically_attach": True},
        {"path": str(tmp_path / "face.png"), "sha256": "f", "active_roles": ["FACE_IDENTITY_STAGE"], "physically_attach": True},
        {"path": str(tmp_path / "front.png"), "sha256": "p", "active_roles": ["PHYSIQUE_FRONT_STAGE"], "physically_attach": True},
        {"path": guide, "sha256": binding["guide_sha256"], "active_roles": ["COMPOSITION"], "physically_attach": True},
    ]
    plan = {
        "request_id": "request-one", "character_id": "NEW", "style_name": "LORANDESORE",
        "pack_path": str(tmp_path / "LORANDESORE_PROJECT_PACK"),
        "startup_parameter_selection": {"resolved_parameters": {"fidelity": 90, "aux_body_decision": "SELECTED"}},
        "construction_dimension_guide": binding,
        "selected_references": {"composition": {"path": guide, "sha256": binding["guide_sha256"]}},
    }
    call = {"stage_id": "03_PHYSIQUE_SIDE", "slots": slots, "user_selections": {
        "style": {"choice": "PROJECT_STYLE:LORANDESORE"},
        "reference_policy": {"choice": "BODY_LIBRARY_ONLY"},
    }}
    plan_path = path.parent / "REFERENCE_PLAN.json"
    assert guard._validate_construction_dimension_guide_binding(plan_path, plan, call, "03_PHYSIQUE_SIDE", slots) == binding
    with pytest.raises(guard.GuardError):
        guard._validate_construction_dimension_guide_binding(plan_path, plan, call, "02_PHYSIQUE_FRONT", slots)


def test_guard_direct_script_import_resolves_contract_and_registry_helpers(authorization, tmp_path):
    path, data = authorization
    binding = _validate(path)
    workspace = Path(__file__).resolve().parents[1]
    guide = str(data["guide_path"])
    payload = {
        "binding": binding,
        "plan": {
            "request_id": "request-one", "character_id": "NEW", "style_name": "LORANDESORE",
            "pack_path": str(tmp_path / "LORANDESORE_PROJECT_PACK"),
            "startup_parameter_selection": {"resolved_parameters": {"fidelity": 90, "aux_body_decision": "SELECTED"}},
            "construction_dimension_guide": binding,
            "selected_references": {"composition": {"path": guide, "sha256": binding["guide_sha256"]}},
        },
        "call": {"stage_id": "03_PHYSIQUE_SIDE", "user_selections": {
            "style": {"choice": "PROJECT_STYLE:LORANDESORE"},
            "reference_policy": {"choice": "BODY_LIBRARY_ONLY"},
        }},
        "guide_record": {
            "sha256": binding["guide_sha256"], "subject_label": "NO_SUBJECT",
            "subject_label_status": "REVIEWED", "subject_label_sha256": binding["guide_sha256"],
            "subject_label_evidence": "current full resolution review",
        },
    }
    payload_path = tmp_path / "direct_script_payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    script = r'''
import json, sys
from pathlib import Path
from types import SimpleNamespace
workspace = Path(sys.argv[1]).resolve()
payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
def is_workspace_path(entry):
    try:
        return Path(entry).resolve().is_relative_to(workspace)
    except (OSError, ValueError):
        return False
sys.path = [str(workspace / "tools")] + [entry for entry in sys.path if entry and not is_workspace_path(entry)]
import style_pack_manager as manager
manager.validate_plan_reference = lambda paths, value, label: payload["guide_record"]
import task_execution_guard
plan_path = Path(payload["binding"]["authorization_path"]).parent / "REFERENCE_PLAN.json"
slots = [
    {"path": "style.png", "sha256": "s", "active_roles": ["STYLE"], "physically_attach": True},
    {"path": "face.png", "sha256": "f", "active_roles": ["FACE_IDENTITY_STAGE"], "physically_attach": True},
    {"path": "front.png", "sha256": "p", "active_roles": ["PHYSIQUE_FRONT_STAGE"], "physically_attach": True},
    {"path": payload["binding"]["guide_path"], "sha256": payload["binding"]["guide_sha256"], "active_roles": ["COMPOSITION"], "physically_attach": True},
]
call = payload["call"]
call["slots"] = slots
assert task_execution_guard._validate_construction_dimension_guide_binding(
    plan_path, payload["plan"], call, "03_PHYSIQUE_SIDE", slots
) == payload["binding"]
print("direct-import-ok")
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, str(workspace), str(payload_path)],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "direct-import-ok" in completed.stdout


@pytest.mark.parametrize("field,value", [
    ("physical_role", "POSE"), ("stage_id", "02_PHYSIQUE_FRONT"),
    ("subject_label", "UNKNOWN"), ("authorization_quote", "make a person"),
])
def test_rejects_wrong_role_stage_subject_or_consent(authorization, field, value):
    path, data = authorization
    data[field] = value
    _write(path, data)
    with pytest.raises(StylePackError):
        _validate(path)


def test_rejects_wrong_request_binding(authorization):
    path, _ = authorization
    with pytest.raises(StylePackError):
        _validate(path, request_id="another-request")


@pytest.mark.parametrize("field,value", [("authorization_source", None), ("authorization_source", "  "), ("authorization_source", 7)])
def test_rejects_missing_or_nonstring_authorization_source(authorization, field, value):
    path, data = authorization
    data[field] = value
    _write(path, data)
    with pytest.raises(StylePackError):
        _validate(path)


@pytest.mark.parametrize("value", [None, "  ", 7])
def test_rejects_missing_or_nonstring_approval_quote(authorization, value):
    path, data = authorization
    approval = Path(str(data["approved_geometry_evidence"]))
    approval_data = json.loads(approval.read_text(encoding="utf-8"))
    approval_data["approval_quote"] = value
    data["approved_geometry_evidence_sha256"] = _write(approval, approval_data)
    _write(path, data)
    with pytest.raises(StylePackError):
        _validate(path)


@pytest.mark.parametrize("field", ["guide_sha256", "registered_reference_path", "subject_label_sha256", "geometry_sha256", "approved_geometry_evidence_sha256"])
def test_rejects_stale_or_wrong_source_binding(authorization, field):
    path, data = authorization
    data[field] = "0" * 64 if field != "registered_reference_path" else str(path.parent / "wrong.png")
    _write(path, data)
    with pytest.raises(StylePackError):
        _validate(path)


@pytest.mark.parametrize("field,value", [("design_height", 1400), ("design_head", 210), ("design_foot", 180)])
def test_rejects_geometry_outside_approved_dimensions(authorization, field, value):
    path, data = authorization
    data[field] = value
    _write(path, data)
    with pytest.raises(StylePackError):
        _validate(path)
