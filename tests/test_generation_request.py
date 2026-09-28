import json
from argparse import Namespace
from pathlib import Path

import pytest

from tools import generation_request as request


def _parser():
    return request._prepare_parser()


def test_collects_type_choice_required_and_unknown_errors_together():
    args, errors = request.validate_request(
        {
            "request_id": 4,
            "fidelity": 80,
            "startup_selection_mode": "MAGIC",
            "require_style_calibration": "false",
            "not_a_manager_argument": "x",
        },
        _parser(),
    )
    assert args is None
    joined = "\n".join(errors)
    assert "request_id must be a JSON string" in joined
    assert "fidelity must be one of" in joined
    assert "startup_selection_mode must be one of" in joined
    assert "require_style_calibration must be a JSON boolean" in joined
    assert "unknown field" in joined
    assert "missing required field: style_name" in joined


def test_defaults_are_from_live_parser_and_bool_is_typed():
    args, errors = request.validate_request(
        {"request_id": "r-1", "style_name": "S", "fidelity": 90, "require_style_calibration": True},
        _parser(),
    )
    assert errors == []
    assert args.generation_purpose == "SCENE"
    assert args.startup_selection_mode == "NEW"
    assert args.character_id == "NONE"
    assert args.require_style_calibration is True


def test_profile_name_is_not_user_confirmation_of_fidelity_and_library():
    args, errors = request.validate_request({
        "request_id": "new-chat-scene", "style_name": "RIOT_LOL_SPLASH",
        "character_id": "CHAR_001", "fidelity": 90,
        "startup_selection_mode": "DIRECT_CONFIRMATION",
        "aux_body_decision": "DECLINED",
        "confirmed_chat_id": "new-chat", "confirmed_message_id": "scene-request",
        "confirmed_parameters_user_quote": "Сделай арт Шанса в его костюме на зиплайне",
    }, _parser())
    assert not errors
    assert request._semantic_errors(args)


def test_reviewed_source_object_keeps_unicode_quotes_and_nested_data():
    review = {
        "role": "FACE",
        "slot_role": "FACE",
        "path": "D:/арт/он сказал \"да\".png",
        "view": "портрет",
        "outcome": "PASS",
        "applicability": "подходит",
        "findings": ["лицо: 龍", {"quote": "‘“ok”’"}],
        "limitations": [],
    }
    args, errors = request.validate_request(
        {"request_id": "r", "style_name": "S", "fidelity": 90, "reviewed_source": [review]},
        _parser(),
    )
    assert errors == []
    assert json.loads(args.reviewed_source[0]) == review


def test_scene_normalizes_cli_case_and_reviewed_source_reports_all_missing_fields(monkeypatch):
    args, errors = request.validate_request(
        {
            "request_id": "r",
            "style_name": "S",
            "fidelity": 90,
            "character_id": "char_001",
            "reviewed_source": [
                {"role": "FACE", "slot_role": "FACE"},
                {"role": "POSE", "slot_role": "POSE"},
            ],
        },
        _parser(),
    )
    assert errors == []
    assert args.character_id == "char_001"
    monkeypatch.setattr(request.manager, "parse_startup_interaction", lambda args: {})
    monkeypatch.setattr(
        request.manager,
        "build_scene_contract",
        lambda args, purpose, character_id: (purpose == "SCENE" and character_id == "CHAR_001") or {},
    )
    semantic = request._semantic_errors(args)
    assert len(semantic) == 1
    assert semantic[0].count("reviewed_source[0] is missing fields:") == 1
    assert semantic[0].count("reviewed_source[1] is missing fields:") == 1
    for key in ("path", "view", "outcome", "applicability", "findings", "limitations"):
        assert key in semantic[0]


def test_dispatches_once_and_validate_only_skips_dispatch(monkeypatch, capsys):
    request_file = "request.json"
    document = json.dumps({"request_id": "r", "style_name": "S", "fidelity": 90}, ensure_ascii=False)
    monkeypatch.setattr(request.Path, "read_text", lambda self, encoding=None: document)
    calls = []
    monkeypatch.setattr(request, "_semantic_errors", lambda args: [])
    monkeypatch.setattr(request.manager, "command_prepare_generation", lambda args: calls.append(args))
    assert request.main(["--request-file", request_file]) == 0
    assert len(calls) == 1
    assert request.main(["--request-file", request_file, "--validate-only"]) == 0
    assert len(calls) == 1
    assert "INPUT_VALIDATION=PASS" in capsys.readouterr().out


def test_invalid_request_never_calls_handler(monkeypatch):
    request_file = "bad.json"
    document = json.dumps({"request_id": 8, "fidelity": 10})
    monkeypatch.setattr(request.Path, "read_text", lambda self, encoding=None: document)
    called = []
    monkeypatch.setattr(request.manager, "command_prepare_generation", lambda args: called.append(args))
    assert request.main(["--request-file", request_file]) == 2
    assert called == []


def test_semantic_errors_aggregate_without_dispatch(monkeypatch, capsys):
    document = json.dumps(
        {
            "request_id": "r",
            "style_name": "S",
            "fidelity": 90,
            "reviewed_source": [{"role": "FACE"}, {"role": "POSE"}],
        }
    )
    monkeypatch.setattr(request.Path, "read_text", lambda self, encoding=None: document)
    called = []
    monkeypatch.setattr(request.manager, "command_prepare_generation", lambda args: called.append(args))
    assert request.main(["--request-file", "semantic-errors.json"]) == 2
    error_output = capsys.readouterr().err
    assert "startup interaction:" in error_output
    assert "scene contract:" in error_output
    assert "reviewed-source JSON:" in error_output
    assert called == []


def test_semantic_snapshot_is_reused_only_while_all_parsed_inputs_match(monkeypatch):
    args, errors = request.validate_request(
        {"request_id": "r", "style_name": "S", "fidelity": 90}, _parser(),
    )
    assert errors == []
    monkeypatch.setattr(request.manager, "parse_startup_interaction", lambda args: {"parsed": True})
    monkeypatch.setattr(request.manager, "parse_reviewed_counts", lambda reviewed, face: {"FACE": 2})
    monkeypatch.setattr(request.manager, "build_scene_contract", lambda *args: {})
    monkeypatch.setattr(request.manager, "validate_prompt_only_body_library_review", lambda args: None)
    monkeypatch.setattr(request, "_validate_reviewed_source_json", lambda values: None)
    assert request._semantic_errors(args) == []
    snapshot = args._preparation_semantic_snapshot
    assert request.manager.reusable_preparation_semantics(args) is snapshot
    args.fidelity = 70
    assert request.manager.reusable_preparation_semantics(args) is None


def test_required_strings_cannot_be_empty():
    args, errors = request.validate_request(
        {"request_id": "  ", "style_name": "", "fidelity": 90},
        _parser(),
    )
    assert args is None
    assert "request_id must not be empty" in errors
    assert "style_name must not be empty" in errors


def test_call_file_rejects_missing_fields_before_prepare_dispatch(monkeypatch, capsys):
    documents = {
        "request.json": json.dumps({"request_id": "r", "style_name": "S", "fidelity": 90}),
        "call.json": json.dumps({"prompt_text": "exact", "reference_ratings": [{"path": "x"}]}),
    }
    monkeypatch.setattr(request.Path, "read_text", lambda self, encoding=None: documents[str(self)])
    called = []
    monkeypatch.setattr(request, "_semantic_errors", lambda args: [])
    monkeypatch.setattr(request.manager, "command_prepare_generation", lambda args: called.append(args))
    assert request.main(["--request-file", "request.json", "--call-file", "call.json"]) == 2
    assert "missing fields" in capsys.readouterr().err
    assert called == []


def test_ratings_require_exact_resolved_paths_and_roles_and_hash_from_manifest():
    source = Path(__file__).resolve()
    manifest = {"slots": [{"path": str(source), "sha256": "authoritative", "active_roles": ["FACE"], "physically_attach": True}]}
    rating = {
        "path": str(source), "active_roles": ["FACE"], "content_and_reference_risk": "D3",
        "use_impact": "+1D", "reason_ru": "оценка",
    }
    assert request._risk_specs(manifest, [rating]) == [f"{source.resolve()}::D3::+1D::оценка::FACE"]
    for changed in ([], [{**rating, "active_roles": ["BODY"]}], [{**rating, "path": str(Path(__file__).with_name("other.png"))}], [rating, rating]):
        try:
            request._risk_specs(manifest, changed)
        except request.manager.StylePackError:
            pass
        else:
            raise AssertionError("missing, extra, or duplicate ratings must be rejected")


def test_finalizer_renders_prompt_before_assessment_and_preserves_phase_order(monkeypatch):
    manifest_path = Path("resolved.json")
    plan_path = Path("REFERENCE_PLAN.json")
    documents = {
        "resolved.json": json.dumps({"reference_plan": str(plan_path), "stage_id": "S1", "slots": []}),
        "REFERENCE_PLAN.json": json.dumps({"confirmed_character_profile": {"x": 1}}),
    }
    original_read_text = Path.read_text
    def read_text(path, encoding=None):
        if path.name in documents:
            return documents[path.name]
        return original_read_text(path, encoding=encoding)
    monkeypatch.setattr(request.Path, "read_text", read_text)
    events = []
    args = Namespace(style_name="S", workspace=Path("."), request_id="r")

    def parsed(command, _args, _stage=""):
        if command == "resolve-call":
            def resolve(_):
                events.append("resolve")
                _._resolved_call_snapshot = {"slots": [{"path": "source.png"}]}
                print(f"REFERENCE_MANIFEST={manifest_path}")
            return Namespace(handler=resolve)
        def prepare(ns):
            events.append("prepare-call")
            assert ns._resolved_call_snapshot == {"slots": [{"path": "source.png"}]}
            assert ns.prompt_text == "rendered:raw"
            assert Path(ns.risk_assessment).name.startswith("RISK_ASSESSMENT_S1_")
            print('EXECUTION_CALL={"prompt":{"text":"rendered:raw"}}')
            print("STATUS=READY_FOR_GENERATION")
        return Namespace(handler=prepare, prompt_text="placeholder", prompt_text_file="", risk_assessment="")

    monkeypatch.setattr(request, "_parsed_manager_command", parsed)
    monkeypatch.setattr(request.manager, "render_confirmed_prompt", lambda plan, text: events.append("render") or "rendered:" + text)
    class RiskParser:
        def parse_args(self, tokens):
            assert tokens[tokens.index("--text") + 1] == "rendered:raw"
            assert "--overwrite" not in tokens
            return Namespace(handler=lambda ns: events.append("assess"), output="risk.json")
    monkeypatch.setattr(request.risk_assessor, "build_parser", lambda: RiskParser())
    output = request._finalize_call(args, {"prompt_text": "raw", "reference_ratings": []})
    assert events == ["resolve", "render", "assess", "prepare-call"]
    assert 'EXECUTION_CALL={"prompt":{"text":"rendered:raw"}}' in output


def test_finalize_only_skips_prepare_and_propagates_changed_source_error(monkeypatch, capsys):
    documents = {
        "request.json": json.dumps({"request_id": "r", "style_name": "S", "fidelity": 90}),
        "call.json": json.dumps({"prompt_text": "exact", "reference_ratings": []}),
    }
    monkeypatch.setattr(request.Path, "read_text", lambda self, encoding=None: documents[str(self)])
    monkeypatch.setattr(request.Path, "is_file", lambda self: self.name == "EXECUTION_GUARD.json")
    monkeypatch.setattr(request.manager, "make_paths", lambda workspace, style: Namespace(generations=Path(".")))
    monkeypatch.setattr(request.manager, "safe_component", lambda value, fallback: value)
    monkeypatch.setattr(request.manager, "load_execution_guard", lambda path: {"status": "ACTIVE", "next_required_action": "PREFLIGHT_OR_EXECUTION"})
    monkeypatch.setattr(request, "_semantic_errors", lambda args: [])
    prepares = []
    monkeypatch.setattr(request.manager, "command_prepare_generation", lambda args: prepares.append(args))
    def fail(_args, _call):
        raise request.manager.StylePackError("The READY call references changed since resolution.")
    monkeypatch.setattr(request, "_finalize_call", fail)
    assert request.main(["--request-file", "request.json", "--call-file", "call.json", "--finalize-only"]) == 2
    assert prepares == []
    assert "changed since resolution" in capsys.readouterr().err


def test_normal_call_runs_prepare_once_then_finalizer_once(monkeypatch, capsys):
    documents = {
        "request.json": json.dumps({"request_id": "r", "style_name": "S", "fidelity": 90}),
        "call.json": json.dumps({"prompt_text": "exact", "reference_ratings": []}),
    }
    monkeypatch.setattr(request.Path, "read_text", lambda self, encoding=None: documents[str(self)])
    monkeypatch.setattr(request, "_semantic_errors", lambda args: [])
    phases = []
    monkeypatch.setattr(request.manager, "command_prepare_generation", lambda args: phases.append("prepare-generation"))
    monkeypatch.setattr(request, "_finalize_call", lambda args, call: phases.append("resolve-call/assess/prepare-call") or "EXECUTION_CALL={}\nSTATUS=READY_FOR_GENERATION\n")
    assert request.main(["--request-file", "request.json", "--call-file", "call.json"]) == 0
    assert phases == ["prepare-generation", "resolve-call/assess/prepare-call"]
    assert "EXECUTION_CALL={}" in capsys.readouterr().out


@pytest.mark.parametrize("guard_action", ["CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER", "COMPLETE"])
def test_ready_retry_refuses_changed_prompt_without_any_dispatch(monkeypatch, capsys, guard_action):
    documents = {
        "request.json": json.dumps({"request_id": "r", "style_name": "S", "fidelity": 90}),
        "call.json": json.dumps({"prompt_text": "a changed prompt that must not be used", "reference_ratings": []}),
        "REFERENCE_PLAN.json": json.dumps({"gate_status": "READY_FOR_GENERATION", "execution_call": {"prompt": {"text": "bound prompt"}}}),
    }
    monkeypatch.setattr(request.Path, "read_text", lambda self, encoding=None: documents[str(self)] if str(self) in documents else documents[self.name])
    monkeypatch.setattr(request.Path, "is_file", lambda self: self.name in {"REFERENCE_PLAN.json", "EXECUTION_GUARD.json"})
    monkeypatch.setattr(request.manager, "make_paths", lambda workspace, style: Namespace(generations=Path(".")))
    monkeypatch.setattr(request.manager, "safe_component", lambda value, fallback: value)
    monkeypatch.setattr(request.manager, "load_execution_guard", lambda path: {"next_required_action": guard_action})
    monkeypatch.setattr(request, "_semantic_errors", lambda args: [])
    dispatches = []
    monkeypatch.setattr(request.manager, "command_prepare_generation", lambda args: dispatches.append("prepare"))
    monkeypatch.setattr(request, "_finalize_call", lambda args, call: dispatches.append("finalize"))
    assert request.main(["--request-file", "request.json", "--call-file", "call.json"]) == 2
    captured = capsys.readouterr()
    assert "ALREADY_PREPARED" in captured.err
    assert "REFERENCE_PLAN.json" in captured.err
    assert "EXECUTION_GUARD.json" in captured.err
    assert "EXECUTION_CALL=" not in captured.out
    assert "STATUS=READY_FOR_GENERATION" not in captured.out
    assert dispatches == []


def test_finalize_only_allows_ready_plan_at_valid_next_stage_transition(monkeypatch, capsys):
    documents = {
        "request.json": json.dumps({"request_id": "r", "style_name": "S", "fidelity": 90}),
        "call.json": json.dumps({"prompt_text": "next stage prompt", "stage_id": "STAGE_2", "reference_ratings": []}),
        "REFERENCE_PLAN.json": json.dumps({
            "gate_status": "READY_FOR_GENERATION", "execution_call": {"stage_id": "STAGE_1", "prompt": {"text": "stage 1"}},
            "generation_workflow": {"mode": "MULTI_STAGE", "stages": [{"stage_id": "STAGE_1"}, {"stage_id": "STAGE_2"}]},
        }),
    }
    monkeypatch.setattr(request.Path, "read_text", lambda self, encoding=None: documents[str(self)] if str(self) in documents else documents[self.name])
    monkeypatch.setattr(request.Path, "is_file", lambda self: self.name in {"REFERENCE_PLAN.json", "EXECUTION_GUARD.json"})
    monkeypatch.setattr(request.manager, "make_paths", lambda workspace, style: Namespace(generations=Path(".")))
    monkeypatch.setattr(request.manager, "safe_component", lambda value, fallback: value)
    monkeypatch.setattr(request.manager, "load_execution_guard", lambda path: {
        "status": "ACTIVE", "next_required_action": "NEXT_SAFE_EXECUTION", "waiting_since": None,
        "active_attempt": None, "attempts": [],
        "required_stages": [{"id": "STAGE_1", "status": "COMPLETED"}, {"id": "STAGE_2", "status": "PENDING"}],
    })
    monkeypatch.setattr(request, "_semantic_errors", lambda args: [])
    phases = []
    monkeypatch.setattr(request.manager, "command_prepare_generation", lambda args: phases.append("prepare"))
    monkeypatch.setattr(request, "_finalize_call", lambda args, call: phases.append("finalize") or "EXECUTION_CALL={\"stage_id\":\"STAGE_2\"}\nSTATUS=READY_FOR_GENERATION\n")
    assert request.main(["--request-file", "request.json", "--call-file", "call.json", "--finalize-only"]) == 0
    assert phases == ["finalize"]
    assert "STATUS=READY_FOR_GENERATION" in capsys.readouterr().out


@pytest.mark.parametrize("attempt_state", ["active", "unknown"])
def test_finalize_only_blocks_ready_plan_with_active_or_unknown_attempt(monkeypatch, capsys, attempt_state):
    documents = {
        "request.json": json.dumps({"request_id": "r", "style_name": "S", "fidelity": 90}),
        "call.json": json.dumps({"prompt_text": "next stage", "reference_ratings": []}),
        "REFERENCE_PLAN.json": json.dumps({"gate_status": "READY_FOR_GENERATION", "execution_call": {"prompt": {"text": "stage 1"}}}),
    }
    monkeypatch.setattr(request.Path, "read_text", lambda self, encoding=None: documents[str(self)] if str(self) in documents else documents[self.name])
    monkeypatch.setattr(request.Path, "is_file", lambda self: self.name in {"REFERENCE_PLAN.json", "EXECUTION_GUARD.json"})
    monkeypatch.setattr(request.manager, "make_paths", lambda workspace, style: Namespace(generations=Path(".")))
    monkeypatch.setattr(request.manager, "safe_component", lambda value, fallback: value)
    guard = {"status": "ACTIVE", "next_required_action": "NEXT_SAFE_EXECUTION", "waiting_since": None, "active_attempt": None, "attempts": []}
    if attempt_state == "active":
        guard["active_attempt"] = {"attempt_id": "a1", "status": "ACTIVE"}
    else:
        guard["attempts"] = [{"attempt_id": "a1", "status": "UNKNOWN"}]
    monkeypatch.setattr(request.manager, "load_execution_guard", lambda path: guard)
    monkeypatch.setattr(request, "_semantic_errors", lambda args: [])
    phases = []
    monkeypatch.setattr(request, "_finalize_call", lambda args, call: phases.append("finalize"))
    assert request.main(["--request-file", "request.json", "--call-file", "call.json", "--finalize-only"]) == 2
    captured = capsys.readouterr()
    assert "does not permit" in captured.err
    assert "READY_FOR_GENERATION" not in captured.out
    assert phases == []


@pytest.mark.parametrize(
    "stage,stage_status,expected",
    [
        (None, "COMPLETED", "explicit stage_id"),
        ("UNKNOWN", "COMPLETED", "unknown planned stage"),
        ("STAGE_1", "COMPLETED", "is completed"),
        ("STAGE_2", "PENDING", "earlier stages are incomplete"),
    ],
)
def test_ready_multistage_bad_stage_stops_before_mutating_dispatch(
    tmp_path, monkeypatch, capsys, stage, stage_status, expected,
):
    request_file = tmp_path / "request.json"
    call_file = tmp_path / "call.json"
    request_file.write_text(json.dumps({"request_id": "r", "style_name": "S", "fidelity": 90}), encoding="utf-8")
    call = {"prompt_text": "exact", "reference_ratings": []}
    if stage is not None:
        call["stage_id"] = stage
    call_file.write_text(json.dumps(call), encoding="utf-8")
    folder = tmp_path / "00_PENDING" / "r"
    folder.mkdir(parents=True)
    (folder / "REFERENCE_PLAN.json").write_text(json.dumps({
        "gate_status": "READY_FOR_GENERATION", "execution_call": {"stage_id": "STAGE_1"},
        "generation_workflow": {"mode": "MULTI_STAGE", "stages": [{"stage_id": "STAGE_1"}, {"stage_id": "STAGE_2"}]},
    }), encoding="utf-8")
    (folder / "EXECUTION_GUARD.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(request.manager, "make_paths", lambda workspace, style: Namespace(generations=tmp_path))
    monkeypatch.setattr(request.manager, "load_execution_guard", lambda path: {
        "status": "ACTIVE", "next_required_action": "NEXT_SAFE_EXECUTION", "attempts": [],
        "required_stages": [{"id": "STAGE_1", "status": stage_status}, {"id": "STAGE_2", "status": "PENDING"}],
    })
    monkeypatch.setattr(request, "_semantic_errors", lambda args: [])
    phases = []
    monkeypatch.setattr(request.manager, "command_prepare_generation", lambda args: phases.append("prepare"))
    monkeypatch.setattr(request, "_finalize_call", lambda args, call: phases.append("resolve/assess/prepare-call"))
    assert request.main(["--request-file", str(request_file), "--call-file", str(call_file), "--finalize-only"]) == 2
    assert expected in capsys.readouterr().err
    assert phases == []


@pytest.mark.parametrize("correction_after_ready", [False, True])
def test_same_stage_requires_current_applicable_correction(correction_after_ready):
    plan = {
        "generation_workflow": {"mode": "MULTI_STAGE", "stages": [{"stage_id": "STAGE_1"}]},
        "execution_call": {"stage_id": "STAGE_1"},
    }
    correction = {"event": "USER_CORRECTION", "stage": "STAGE_1"}
    ready = {"event": "READY_FOR_EXECUTION", "stage": "STAGE_1"}
    guard = {
        "required_stages": [{"id": "STAGE_1", "status": "PENDING"}],
        "events": [ready, correction] if correction_after_ready else [correction, ready],
    }
    if correction_after_ready:
        request._validate_ready_stage(plan, guard, {"stage_id": "STAGE_1"})
    else:
        with pytest.raises(request.manager.StylePackError, match="no current correction"):
            request._validate_ready_stage(plan, guard, {"stage_id": "STAGE_1"})


def test_actual_risk_writer_uses_fresh_file_and_late_rejection_preserves_old_risk(tmp_path, monkeypatch):
    technical = tmp_path / "TECHNICAL_REFERENCES"
    technical.mkdir()
    old_risk = technical / "RISK_ASSESSMENT_STAGE_1.json"
    old_risk.write_bytes(b"previous bound report\n")
    manifest = technical / "RESOLVED_CALL_STAGE_1.json"
    plan_path = tmp_path / "REFERENCE_PLAN.json"
    plan_path.write_text("{}", encoding="utf-8")
    manifest.write_text(json.dumps({"reference_plan": str(plan_path), "stage_id": "STAGE_1", "slots": []}), encoding="utf-8")
    def parsed(command, _args, _stage=""):
        if command == "resolve-call":
            return Namespace(handler=lambda ns: print(f"REFERENCE_MANIFEST={manifest}"))
        return Namespace(handler=lambda ns: (_ for _ in ()).throw(request.manager.StylePackError("late prepare-call rejection")))
    monkeypatch.setattr(request, "_parsed_manager_command", parsed)
    monkeypatch.setattr(request.manager, "render_confirmed_prompt", lambda plan, text: text)
    with pytest.raises(request.manager.StylePackError, match="late prepare-call rejection"):
        request._finalize_call(Namespace(style_name="S", workspace=tmp_path, request_id="r"), {
            "prompt_text": "A quiet landscape.", "stage_id": "STAGE_1", "reference_ratings": [],
        })
    assert old_risk.read_bytes() == b"previous bound report\n"
    candidates = list(technical.glob("RISK_ASSESSMENT_STAGE_1_*.json"))
    assert len(candidates) == 1
    assert candidates[0] != old_risk
    assert json.loads(candidates[0].read_text(encoding="utf-8"))["original_prompt"]["text"] == "A quiet landscape."
