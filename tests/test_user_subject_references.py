import json
from unittest.mock import patch
from pathlib import Path

import pytest

from tools import generation_request
from tools import style_pack_manager as manager


def _fixture(tmp_path):
    paths = manager.make_paths(tmp_path, "RIOT_LOL_SPLASH")
    attached = paths.pack / manager.ROLE_DIRECTORIES["SUBJECT_CORE"] / "armor.png"
    attached.parent.mkdir(parents=True)
    source = tmp_path / "REFERENCES" / "POWER_ARMOR" / "armor.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"selected armor bytes")
    attached.write_bytes(source.read_bytes())
    evidence = [{"path": str(attached), "sha256": manager.sha256(source), "source_path": str(source), "chat_id": "chat-current", "message_id": "msg-1"}]
    return paths, attached, source, evidence


def test_user_selected_subject_allows_unformed_pack_only_with_current_exact_evidence(tmp_path):
    paths, attached, _, evidence = _fixture(tmp_path)
    assert manager.validate_user_subject_evidence(paths, str(attached), evidence, "chat-current")
    assert not manager.validate_user_subject_evidence(paths, str(attached), evidence, "other-chat")
    assert manager.local_asset_status(attached.relative_to(paths.pack), True)[0] not in {"REJECTED", "REVIEW_ONLY", "DERIVED_GENERATION"}
    with patch.object(manager, "style_formation_status", return_value="UNFORMED"), \
            patch.object(manager, "style_reference_compatibility_metadata", return_value={}):
        assert manager.validate_plan_reference(paths, str(attached), "SUBJECT", allow_user_subject_source=True)["sha256"] == evidence[0]["sha256"]
        try:
            manager.validate_plan_reference(paths, str(attached), "SUBJECT")
        except manager.StylePackError as error:
            assert "unformed style-pack source" in str(error)
        else:
            raise AssertionError("unproven SUBJECT unexpectedly bypassed unformed-pack validation")


def test_user_subject_evidence_rejects_missing_tampered_and_historical_sources(tmp_path):
    paths, attached, source, evidence = _fixture(tmp_path)
    assert not manager.validate_user_subject_evidence(paths, str(attached), [], "chat-current")
    source.write_bytes(b"changed source")
    assert not manager.validate_user_subject_evidence(paths, str(attached), evidence, "chat-current")
    source.write_bytes(attached.read_bytes())
    evidence[0]["source_path"] = str(paths.generations / "REJECTED" / "old.png")
    assert not manager.validate_user_subject_evidence(paths, str(attached), evidence, "chat-current")


def test_user_subject_evidence_rejects_existing_standalone_historical_source(tmp_path):
    paths, attached, _, evidence = _fixture(tmp_path)
    historical = tmp_path / "GENERATION_RESULTS" / "old.png"
    historical.parent.mkdir(parents=True)
    historical.write_bytes(attached.read_bytes())
    evidence[0]["source_path"] = str(historical)
    evidence[0]["sha256"] = manager.sha256(historical)
    assert historical.is_file()
    assert manager.sha256(historical) == manager.sha256(attached)
    assert not manager.validate_user_subject_evidence(paths, str(attached), evidence, "chat-current")


def test_call_file_accepts_structured_user_subject_provenance(tmp_path, monkeypatch):
    _, attached, source, evidence = _fixture(tmp_path)
    document = {"prompt_text": "prompt", "reference_ratings": [], "user_reference_evidence": evidence}
    call_path = tmp_path / "call.json"
    call_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(generation_request, "_authorize_input_path", lambda path: None)
    loaded, errors = generation_request._load_call_file(call_path)
    assert not errors
    assert loaded["user_reference_evidence"] == evidence


def test_call_file_rejects_non_array_user_evidence(tmp_path, monkeypatch):
    call_path = tmp_path / "call.json"
    call_path.write_text(json.dumps({"prompt_text": "prompt", "reference_ratings": [], "user_reference_evidence": {}}), encoding="utf-8")
    monkeypatch.setattr(generation_request, "_authorize_input_path", lambda path: None)
    loaded, errors = generation_request._load_call_file(call_path)
    assert loaded is None
    assert any("user_reference_evidence must be an array" in error for error in errors)


def test_exact_call_requires_identical_prepared_evidence_and_current_source_bytes(tmp_path):
    paths, attached, source, evidence = _fixture(tmp_path)
    plan = {"user_reference_evidence": evidence}
    generation_request._validate_call_evidence({"user_reference_evidence": evidence}, plan)
    with pytest.raises(manager.StylePackError, match="differs from the prepared plan"):
        generation_request._validate_call_evidence({"user_reference_evidence": []}, plan)
    source.write_bytes(b"historical replacement")
    assert not manager.validate_user_subject_evidence(paths, str(attached), evidence, "chat-current")
