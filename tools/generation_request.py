"""Structured JSON front end for StoryArt prepare-generation."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

if __package__:
    from . import style_pack_manager as manager
    from . import generation_risk_assessor as risk_assessor
else:
    import style_pack_manager as manager
    import generation_risk_assessor as risk_assessor


def _prepare_parser() -> argparse.ArgumentParser:
    parser = manager.build_parser()
    subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    return subparsers.choices["prepare-generation"]


def _is_bool_action(action: argparse.Action) -> bool:
    return isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction))


def validate_request(raw: Any, parser: argparse.ArgumentParser) -> tuple[argparse.Namespace | None, list[str]]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, ["request must be a JSON object"]

    actions = {action.dest: action for action in parser._actions if action.dest != argparse.SUPPRESS}
    for key in raw:
        if not isinstance(key, str) or key not in actions:
            errors.append(f"unknown field: {key!r}")

    values: dict[str, Any] = {}
    for key, action in actions.items():
        if key in raw:
            value = raw[key]
        else:
            if action.required:
                errors.append(f"missing required field: {key}")
            if action.default is not argparse.SUPPRESS:
                values[key] = action.default
            continue

        is_list = isinstance(action, argparse._AppendAction)
        items = value if is_list and isinstance(value, list) else None
        if is_list and items is None:
            errors.append(f"{key} must be a JSON array")
            continue
        candidates = items if items is not None else [value]
        converted: list[Any] = []
        valid = True
        for index, item in enumerate(candidates):
            label = f"{key}[{index}]" if is_list else key
            if _is_bool_action(action):
                if type(item) is not bool:
                    errors.append(f"{label} must be a JSON boolean")
                    valid = False
                    continue
                converted.append(item)
            elif action.type is int:
                if type(item) is not int:
                    errors.append(f"{label} must be a JSON integer")
                    valid = False
                    continue
                converted.append(item)
            elif action.type is Path:
                if not isinstance(item, str):
                    errors.append(f"{label} must be a JSON string path")
                    valid = False
                    continue
                converted.append(Path(item))
            elif action.dest == "reviewed_source" and isinstance(item, dict):
                # Match the manager's CLI boundary while retaining Unicode and nested JSON values.
                converted.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            elif isinstance(item, str):
                if action.required and not item.strip():
                    errors.append(f"{label} must not be empty")
                    valid = False
                    continue
                converted.append(item)
            else:
                errors.append(f"{label} must be a JSON string")
                valid = False

            if valid and action.choices is not None and converted:
                candidate = converted[-1]
                if candidate not in action.choices:
                    errors.append(f"{label} must be one of: {', '.join(map(str, action.choices))}")
                    valid = False
        if valid:
            values[key] = converted if is_list else converted[0]

    if errors:
        return None, errors
    namespace = argparse.Namespace(command="prepare-generation", **values)
    namespace.handler = manager.command_prepare_generation
    return namespace, []


def _semantic_errors(args: argparse.Namespace) -> list[str]:
    errors: list[str] = []
    parsed: dict[str, Any] = {}

    # These checks are deliberately limited to stable, inexpensive input contracts.
    checks = (
        ("startup interaction", "startup_interaction", lambda: manager.parse_startup_interaction(args)),
        ("reviewed counts", "reviewed_counts", lambda: manager.parse_reviewed_counts(args.reviewed, args.face_candidates_reviewed)),
        ("scene contract", "scene_contract", lambda: manager.build_scene_contract(args, args.generation_purpose.upper(), args.character_id.upper())),
        ("prompt-only body review", "body_review", lambda: manager.validate_prompt_only_body_library_review(args)),
        ("reviewed-source JSON", "reviewed_source", lambda: _validate_reviewed_source_json(args.reviewed_source)),
    )
    for label, key, check in checks:
        try:
            parsed[key] = check()
        except manager.StylePackError as exc:
            errors.append(f"{label}: {exc}")
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"{label}: {exc}")
    if not errors:
        args._preparation_semantic_snapshot = {
            "signature": manager.preparation_args_signature(args),
            "startup_interaction": parsed["startup_interaction"],
            "reviewed_counts": parsed["reviewed_counts"],
        }
    else:
        args.__dict__.pop("_preparation_semantic_snapshot", None)
    return errors


def _validate_reviewed_source_json(values: list[str]) -> None:
    required = {"role", "slot_role", "path", "view", "outcome", "applicability", "findings", "limitations"}
    errors: list[str] = []
    for index, value in enumerate(values):
        try:
            item = json.loads(value)
        except json.JSONDecodeError as exc:
            errors.append(f"reviewed_source[{index}] is invalid JSON: {exc.msg}")
            continue
        if not isinstance(item, dict):
            errors.append(f"reviewed_source[{index}] must be a JSON object")
            continue
        missing = sorted(required - item.keys())
        if missing:
            errors.append(f"reviewed_source[{index}] is missing fields: {', '.join(missing)}")
        role = item.get("role")
        if not isinstance(role, str) or role.strip().upper() not in manager.REVIEW_CATEGORIES:
            errors.append(f"reviewed_source[{index}] has unknown role {role!r}")
    if errors:
        raise manager.StylePackError("; ".join(errors))


def _authorize_input_path(path: Path) -> None:
    # Input documents outside generation storage can be current user/project
    # inputs. Anything in generation storage must belong to the active request.
    if any(part.upper().endswith("_GENERATIONS") or part.upper() in
           {"00_PENDING", "GENERATION_RESULTS", "REJECTED", "DRAFT", "STAGING", "TEST"}
           for part in path.resolve().parts):
        manager.assert_active_request_path(path)


def _load_call_file(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        _authorize_input_path(path)
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError, manager.StylePackError) as exc:
        return None, [f"call file: {exc}"]
    if not isinstance(raw, dict):
        return None, ["call file must be a JSON object"]
    allowed = {"prompt_text", "prompt_text_file", "stage_id", "reference_ratings"}
    errors = [f"call file unknown field: {key!r}" for key in raw if key not in allowed]
    if bool(raw.get("prompt_text")) == bool(raw.get("prompt_text_file")):
        errors.append("call file must provide exactly one non-empty prompt_text or prompt_text_file")
    for key in ("prompt_text", "prompt_text_file", "stage_id"):
        if key in raw and (not isinstance(raw[key], str) or (key != "stage_id" and not raw[key].strip())):
            errors.append(f"call file {key} must be a non-empty JSON string")
    if "reference_ratings" not in raw:
        errors.append("call file is missing required field: reference_ratings (use [] when the resolved call has no physical references)")
    ratings = raw.get("reference_ratings", [])
    if not isinstance(ratings, list):
        errors.append("call file reference_ratings must be a JSON array")
        ratings = []
    rating_fields = {"path", "active_roles", "content_and_reference_risk", "use_impact", "reason_ru"}
    for index, rating in enumerate(ratings):
        if not isinstance(rating, dict):
            errors.append(f"reference_ratings[{index}] must be a JSON object")
            continue
        missing = sorted(rating_fields - rating.keys())
        extra = sorted(rating.keys() - rating_fields)
        if missing:
            errors.append(f"reference_ratings[{index}] is missing fields: {', '.join(missing)}")
        if extra:
            errors.append(f"reference_ratings[{index}] has unknown fields: {', '.join(extra)}")
        for key in ("path", "content_and_reference_risk", "use_impact", "reason_ru"):
            if key in rating and (not isinstance(rating[key], str) or not rating[key].strip()):
                errors.append(f"reference_ratings[{index}].{key} must be a non-empty JSON string")
        roles = rating.get("active_roles")
        if not isinstance(roles, list) or not roles or any(not isinstance(role, str) or not role.strip() for role in roles):
            errors.append(f"reference_ratings[{index}].active_roles must be a non-empty array of strings")
        elif all(isinstance(role, str) and role.strip() for role in roles) and all(
            isinstance(rating.get(key), str) and rating[key].strip()
            for key in ("path", "content_and_reference_risk", "use_impact", "reason_ru")
        ):
            try:
                risk_assessor.parse_d(rating["content_and_reference_risk"])
                if not re.fullmatch(r"([+-]?)[0-2]D", rating["use_impact"], flags=re.IGNORECASE):
                    raise ValueError("use_impact must be -2D..+2D")
                if "::" in rating["reason_ru"]:
                    raise ValueError("reason_ru cannot contain the reference-spec separator ::")
                risk_assessor._normalise_roles(",".join(roles))
            except (risk_assessor.RiskAssessmentError, ValueError) as exc:
                errors.append(f"reference_ratings[{index}] is invalid: {exc}")
    if errors:
        return None, errors
    return raw, []


def _parsed_manager_command(command: str, args: argparse.Namespace, stage_id: str = "") -> argparse.Namespace:
    parser = manager.build_parser()
    tokens = [command, "--style-name", args.style_name, "--workspace", str(args.workspace), "--request-id", args.request_id]
    if stage_id:
        tokens.extend(("--stage-id", stage_id))
    if command == "prepare-call":
        tokens.extend(("--prompt-text", "placeholder", "--risk-assessment", "placeholder"))
    return parser.parse_args(tokens)


def _exact_prompt(call_input: dict[str, Any]) -> str:
    if "prompt_text" in call_input:
        return call_input["prompt_text"]
    try:
        prompt_path = Path(call_input["prompt_text_file"])
        _authorize_input_path(prompt_path)
        return prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise manager.StylePackError(f"Cannot read exact prompt text: {exc}") from exc


def _risk_specs(manifest: dict[str, Any], ratings: list[dict[str, Any]]) -> list[str]:
    slots = manifest.get("slots")
    if not isinstance(slots, list):
        raise manager.StylePackError("Resolved call manifest has no slots array.")
    expected: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for slot in slots:
        if not isinstance(slot, dict):
            raise manager.StylePackError("Resolved call manifest contains an invalid slot.")
        if not slot.get("physically_attach", True):
            continue
        path = Path(str(slot.get("path", ""))).resolve()
        roles = tuple(sorted(str(role).upper() for role in slot.get("active_roles", [])))
        if not roles:
            raise manager.StylePackError(f"Resolved physical source has no roles: {path}")
        expected[(str(path), roles)] = slot
    supplied: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for rating in ratings:
        path = str(Path(rating["path"]).resolve())
        roles = tuple(sorted(role.strip().upper() for role in rating["active_roles"]))
        key = (path, roles)
        if key in supplied:
            raise manager.StylePackError(f"Duplicate reference rating for exact source and roles: {path}")
        supplied[key] = rating
    missing = sorted(set(expected) - set(supplied))
    extra = sorted(set(supplied) - set(expected))
    if missing or extra:
        details = []
        if missing:
            details.append("missing ratings: " + "; ".join(f"{path} [{','.join(roles)}]" for path, roles in missing))
        if extra:
            details.append("ratings do not match resolved source/roles: " + "; ".join(f"{path} [{','.join(roles)}]" for path, roles in extra))
        raise manager.StylePackError("; ".join(details))
    # Hashes come only from the manager's authoritative resolution manifest.
    specs = []
    for key, slot in expected.items():
        rating = supplied[key]
        if not slot.get("sha256"):
            raise manager.StylePackError(f"Resolved source has no authoritative hash: {key[0]}")
        specs.append("::".join((key[0], rating["content_and_reference_risk"], rating["use_impact"], rating["reason_ru"], ",".join(key[1]))))
    return specs


def _finalize_call(args: argparse.Namespace, call_input: dict[str, Any]) -> str:
    stage_id = call_input.get("stage_id", "")
    resolve_args = _parsed_manager_command("resolve-call", args, stage_id)
    resolved_output = io.StringIO()
    with contextlib.redirect_stdout(resolved_output):
        resolve_args.handler(resolve_args)
    manifest_line = next((line.partition("=")[2] for line in resolved_output.getvalue().splitlines() if line.startswith("REFERENCE_MANIFEST=")), "")
    if not manifest_line:
        raise manager.StylePackError("resolve-call did not return its resolved reference manifest path.")
    manifest_path = Path(manifest_line)
    manager.assert_active_request_path(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plan_path = Path(manifest["reference_plan"])
        manager.assert_active_request_path(plan_path)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, KeyError, json.JSONDecodeError, TypeError) as exc:
        raise manager.StylePackError(f"Cannot read resolved call inputs: {exc}") from exc
    prompt = manager.render_confirmed_prompt(plan, _exact_prompt(call_input))
    specs = _risk_specs(manifest, call_input.get("reference_ratings", []))
    risk_module_parser = risk_assessor.build_parser()
    risk_path = manifest_path.parent / (
        f"RISK_ASSESSMENT_{manager.safe_component(str(manifest.get('stage_id', 'SINGLE_PASS')), 'stage')}_{uuid.uuid4().hex}.json"
    )
    risk_tokens = ["--text", prompt, "--output", str(risk_path)]
    for spec in specs:
        risk_tokens.extend(("--reference", spec))
    risk_args = risk_module_parser.parse_args(risk_tokens)
    risk_args.handler(risk_args)

    prepare_args = _parsed_manager_command("prepare-call", args, str(manifest.get("stage_id", stage_id)))
    prepare_args.prompt_text = prompt
    prepare_args.prompt_text_file = ""
    prepare_args.risk_assessment = str(risk_path)
    resolved_snapshot = getattr(resolve_args, "_resolved_call_snapshot", None)
    if isinstance(resolved_snapshot, dict):
        prepare_args._resolved_call_snapshot = resolved_snapshot
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        prepare_args.handler(prepare_args)
    return output.getvalue()


_NEXT_STAGE_READY_ACTIONS = {
    "PREFLIGHT_OR_EXECUTION",
    "NEXT_SAFE_EXECUTION",
    "NEXT_SAFE_EXECUTION_OR_COMPLETE",
    "NEXT_EXECUTION_OR_COMPLETE_OR_BLOCKER",
    "NEXT_EXECUTION_OR_BLOCKER",
    "NEXT_EXECUTION_OR_COMPLETE",
    "READY_FOR_EXECUTION_OR_EXECUTION_STARTED_OR_BLOCKER",
}


def _validate_ready_stage(plan: dict[str, Any], guard: dict[str, Any], call_input: dict[str, Any] | None) -> None:
    workflow = plan.get("generation_workflow", {})
    if not isinstance(workflow, dict) or workflow.get("mode") != "MULTI_STAGE":
        return
    stages = workflow.get("stages", [])
    if not isinstance(stages, list) or not stages or any(not isinstance(row, dict) for row in stages):
        raise manager.StylePackError("ALREADY_PREPARED: READY multi-stage plan has no valid stage order.")
    ordered = [str(row.get("stage_id", "")).upper() for row in stages]
    if not all(ordered) or len(set(ordered)) != len(ordered):
        raise manager.StylePackError("ALREADY_PREPARED: READY multi-stage plan has ambiguous stages.")
    stage = str((call_input or {}).get("stage_id", "")).strip().upper()
    if not stage:
        raise manager.StylePackError("ALREADY_PREPARED: finalize-only requires explicit stage_id for a READY multi-stage plan.")
    if stage not in ordered:
        raise manager.StylePackError(f"ALREADY_PREPARED: unknown planned stage {stage}.")
    entries = guard.get("required_stages", [])
    if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
        raise manager.StylePackError("ALREADY_PREPARED: guard has no valid required-stage state.")
    state_by_stage = {str(row.get("id", "")).upper(): str(row.get("status", "")).upper() for row in entries}
    if not set(ordered).issubset(state_by_stage):
        raise manager.StylePackError("ALREADY_PREPARED: guard stages do not match the planned stage order.")
    index = ordered.index(stage)
    if state_by_stage[stage] == "COMPLETED":
        raise manager.StylePackError(f"ALREADY_PREPARED: stage {stage} is completed.")
    if any(state_by_stage[prior] != "COMPLETED" for prior in ordered[:index]):
        raise manager.StylePackError(f"ALREADY_PREPARED: earlier stages are incomplete before {stage}.")
    stored_call = plan.get("execution_call")
    bound_stage = str((stored_call or {}).get("stage_id", "")).upper() if isinstance(stored_call, dict) else ""
    if not bound_stage:
        bound_stage = str((guard.get("ready_binding") or {}).get("stage", "")).upper()
    if bound_stage not in ordered:
        raise manager.StylePackError("ALREADY_PREPARED: prior READY stage is unknown.")
    if index < ordered.index(bound_stage):
        raise manager.StylePackError(f"ALREADY_PREPARED: stage {stage} precedes the bound READY stage.")
    if stage != bound_stage:
        if index != ordered.index(bound_stage) + 1:
            raise manager.StylePackError(f"ALREADY_PREPARED: stage {stage} skips the next planned stage.")
        return
    events = guard.get("events", [])
    if not isinstance(events, list):
        raise manager.StylePackError("ALREADY_PREPARED: guard events are invalid.")
    last_ready = max(
        (i for i, row in enumerate(events) if isinstance(row, dict) and row.get("event") == "READY_FOR_EXECUTION"
         and str(row.get("stage", "")).upper() == stage), default=-1,
    )
    if last_ready < 0 or not any(
        isinstance(row, dict) and row.get("event") in {"ATTEMPT_REJECTED", "USER_CORRECTION", "STAGE_REOPENED"}
        and str(row.get("stage", "")).upper() == stage
        for row in events[last_ready + 1:]
    ):
        raise manager.StylePackError(f"ALREADY_PREPARED: stage {stage} has no current correction after READY_FOR_EXECUTION.")


def _reject_existing_ready_call(args: argparse.Namespace, *, finalize_only: bool = False, call_input: dict[str, Any] | None = None) -> None:
    paths = manager.make_paths(args.workspace, args.style_name)
    request_folder = paths.generations / "00_PENDING" / manager.safe_component(args.request_id, "request")
    plan_path = request_folder / "REFERENCE_PLAN.json"
    guard_path = request_folder / "EXECUTION_GUARD.json"
    manager.assert_active_request_path(guard_path)
    plan: dict[str, Any] = {}
    if plan_path.is_file():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise manager.StylePackError(f"Cannot inspect existing reference plan before retry: {exc}") from exc
    guard: dict[str, Any] | None = None
    if guard_path.is_file():
        try:
            guard = manager.load_execution_guard(guard_path)
        except (manager.ExecutionGuardError, OSError, ValueError) as exc:
            if plan or finalize_only:
                raise manager.StylePackError(f"Cannot safely resume request with an invalid execution guard at {guard_path}: {exc}") from exc

    plan_ready = plan.get("gate_status") == "READY_FOR_GENERATION"
    if finalize_only and guard is None:
        raise manager.StylePackError(f"Cannot finalize without a valid execution guard at {guard_path}.")
    if finalize_only:
        assert guard is not None
        action = str(guard.get("next_required_action", "")).upper()
        status = str(guard.get("status", "")).upper()
        attempts = guard.get("attempts", [])
        has_unknown_attempt = isinstance(attempts, list) and any(
            isinstance(attempt, dict) and str(attempt.get("status", "")).upper() == "UNKNOWN"
            for attempt in attempts
        )
        if (
            status in {"BLOCKED", "COMPLETE", "WAITING_FOR_USER", "STOPPED", "CANCELLED"}
            or bool(guard.get("waiting_since"))
            or bool(guard.get("active_attempt"))
            or has_unknown_attempt
            or action not in _NEXT_STAGE_READY_ACTIONS
        ):
            raise manager.StylePackError(
                f"ALREADY_PREPARED: guard {guard_path} does not permit finalize-only for request plan {plan_path}."
            )
        if plan_ready:
            _validate_ready_stage(plan, guard, call_input)
        return

    action = str((guard or {}).get("next_required_action", "")).upper()
    status = str((guard or {}).get("status", "")).upper()
    guard_ready_or_complete = action in {
        "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER", "EXECUTION_STARTED_OR_BLOCKER", "COMPLETE", "COMPLETED", "DONE"
    } or status in {"COMPLETE", "COMPLETED", "DONE"}
    if not plan_ready and not guard_ready_or_complete:
        return
    raise manager.StylePackError(
        "ALREADY_PREPARED: this request already has a READY plan or guard. "
        f"Inspect the existing state at {plan_path} and {guard_path}; resume the stored operation or record a correction through the scenario. "
        "This frontend will not reprepare the request or emit an EXECUTION_CALL."
    )


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Validate and dispatch a structured prepare-generation request.")
    cli.add_argument("--request-file", required=True, type=Path)
    cli.add_argument("--call-file", type=Path, help="Typed exact-call prompt, stage, and assessed reference ratings.")
    cli.add_argument("--finalize-only", action="store_true", help="Continue a prepared request through reference resolution, risk assessment, and READY call binding.")
    cli.add_argument("--validate-only", action="store_true", help="Validate request input contracts only; does not validate source assets or QA.")
    options = cli.parse_args(argv)
    try:
        _authorize_input_path(options.request_file)
        raw = json.loads(options.request_file.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError, manager.StylePackError) as exc:
        print(f"ERROR=request file: {exc}", file=sys.stderr)
        return 2

    call_input = None
    if options.call_file:
        call_input, call_errors = _load_call_file(options.call_file)
        if call_errors:
            for error in call_errors:
                print(f"ERROR={error}", file=sys.stderr)
            return 2
    elif options.finalize_only:
        print("ERROR=--finalize-only requires --call-file", file=sys.stderr)
        return 2

    parser = _prepare_parser()
    args, errors = validate_request(raw, parser)
    if args is not None:
        errors.extend(_semantic_errors(args))
    if errors:
        for error in errors:
            print(f"ERROR={error}", file=sys.stderr)
        return 2
    if options.validate_only:
        print("INPUT_VALIDATION=PASS (source assets and QA not checked)")
        return 0
    try:
        _reject_existing_ready_call(args, finalize_only=options.finalize_only, call_input=call_input)
        if not options.finalize_only:
            args.handler(args)
        if call_input is not None:
            result = _finalize_call(args, call_input)
            print(result, end="")
        return 0
    except (manager.StylePackError, risk_assessor.RiskAssessmentError) as exc:
        print(f"ERROR={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
