"""Bind the final StoryArt specification to its one project-local Web agent."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

if __package__:
    from . import chatgpt_web_image as web
else:
    import chatgpt_web_image as web

ROOT = Path(__file__).resolve().parents[1]
IMMUTABLE_JOURNAL_KEYS = (
    "schema_version", "project_binding", "prompt", "prompt_sha256", "references",
    "oracle_root", "model_strategy", "model_label", "profile_dir",
    "requested_output", "oracle_home", "opt_in", "safety_history",
    "neutral_test", "windows_isolated_browser", "browser_mode", "scenario_evidence", "runtime_binding",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Path leaves its authorized request/workspace: {resolved}")
    return resolved


def journal_contract(state: dict) -> dict:
    return {key: state.get(key) for key in IMMUTABLE_JOURNAL_KEYS}


def packet_seal(packet: dict) -> str:
    return web.canonical_hash({k: v for k, v in packet.items() if k != "packet_sha256"})


def validate_packet(path, *, workspace=None, for_submission=False) -> dict:
    workspace = Path(workspace or ROOT).resolve()
    path = inside(Path(path), workspace)
    packet = json.loads(path.read_text(encoding="utf-8"))
    if (packet.get("schema_version") != 1 or packet.get("type") != "STORYART_WEB_IMAGE_HANDOFF"
            or packet.get("agent_type") != "web_image" or packet.get("provider") != "CHATGPT_WEB_ORACLE"
            or packet.get("packet_sha256") != packet_seal(packet)
            or Path(packet.get("workspace", "")).resolve() != workspace):
        raise ValueError("Web image packet identity/seal mismatch")
    journal = inside(Path(packet["journal_path"]), workspace)
    request = journal.parent
    if path != request / (journal.stem + ".web-image-packet.json"):
        raise ValueError("Web image packet must be in its exact active request folder")
    state = web.load(journal, for_submission=for_submission)
    if state.get("neutral_test") or state.get("scenario_evidence") is None:
        raise ValueError("Web agent accepts an approved production scenario, not setup/test art")
    if for_submission and state.get("status") != "PREPARED":
        raise ValueError("Web image submission is consumed; reconcile the original operation")
    if packet["journal_contract"] != journal_contract(state):
        raise ValueError("Prepared Web execution changed after final-spec handoff")
    spec = packet["final_specification"]
    spec_path = inside(Path(spec["path"]), request)
    raw = spec_path.read_bytes()
    if sha(raw) != spec["sha256"] or raw.decode("utf-8") != spec["text"]:
        raise ValueError("Final user specification changed")
    evidence = state["scenario_evidence"]
    guard_path = inside(Path(evidence["guard"]["path"]), request)
    guard = json.loads(guard_path.read_text(encoding="utf-8"))
    call = evidence["attempt"]["snapshot"]["execution_call"]
    if (packet["request_id"] != guard["request_id"]
            or packet["attempt_id"] != evidence["attempt"]["attempt_id"]
            or packet["stage"] != call["stage_id"]
            or Path(packet["guard_path"]).resolve() != guard_path
            or packet["prompt"] != call["prompt"]
            or packet["references"] != state["references"]
            or Path(packet["output_path"]).resolve() != inside(Path(state["requested_output"]), request)):
        raise ValueError("Web packet does not bind its original approved scenario call")
    expected_inputs = {
        path, journal, spec_path, guard_path,
        inside(Path(evidence["path"]), request),
        inside(Path(evidence["plan"]["path"]), request),
        *(inside(Path(ref["path"]), workspace) for ref in state["references"]),
    }
    if {Path(item).resolve() for item in packet["input_paths"]} != expected_inputs:
        raise ValueError("Web packet omits or adds an unauthorized execution input")
    return packet


@web.exclusive
def create(journal, specification, *, workspace=None) -> dict:
    workspace = Path(workspace or ROOT).resolve()
    journal = inside(Path(journal), workspace)
    request = journal.parent
    spec_path = inside(Path(specification), request)
    if not spec_path.is_file():
        raise ValueError("Complete final specification file is required")
    raw = spec_path.read_bytes()
    text = raw.decode("utf-8")
    if not text.strip():
        raise ValueError("Final specification cannot be empty")
    state = web.load(journal, for_submission=True)
    if state.get("status") != "PREPARED" or state.get("neutral_test"):
        raise ValueError("Only a never-submitted authorized production journal can be delegated")
    evidence = state["scenario_evidence"]
    guard_path = inside(Path(evidence["guard"]["path"]), request)
    guard = json.loads(guard_path.read_text(encoding="utf-8"))
    call = evidence["attempt"]["snapshot"]["execution_call"]
    packet_path = request / (journal.stem + ".web-image-packet.json")
    packet = {
        "schema_version": 1, "type": "STORYART_WEB_IMAGE_HANDOFF",
        "agent_type": "web_image", "provider": "CHATGPT_WEB_ORACLE",
        "workspace": str(workspace), "request_id": guard["request_id"],
        "attempt_id": evidence["attempt"]["attempt_id"], "stage": call["stage_id"],
        "guard_path": str(guard_path), "journal_path": str(journal),
        "journal_contract": journal_contract(state),
        "final_specification": {"path": str(spec_path), "sha256": sha(raw), "text": text},
        "prompt": call["prompt"], "references": state["references"],
        "output_path": str(inside(Path(state["requested_output"]), request)),
        "user_contract": {"goal": guard["goal_lock"], "deliverable": guard["primary_deliverable"],
                          "invariants": guard.get("locked_invariants", {})},
        "input_paths": sorted({str(packet_path), str(journal), str(spec_path), str(guard_path),
                               str(inside(Path(evidence["path"]), request)),
                               str(inside(Path(evidence["plan"]["path"]), request)),
                               *(str(inside(Path(ref["path"]), workspace)) for ref in state["references"])}),
    }
    packet["packet_sha256"] = packet_seal(packet)
    if packet_path.exists():
        if validate_packet(packet_path, workspace=workspace, for_submission=True) != packet:
            raise ValueError("Existing handoff cannot be replaced; continue its original operation")
    else:
        with packet_path.open("x", encoding="utf-8") as handle:
            json.dump(packet, handle, ensure_ascii=False, indent=2)
    validate_packet(packet_path, workspace=workspace, for_submission=True)

    if __package__:
        from . import storyart_orchestrator as orchestrator
    else:
        import storyart_orchestrator as orchestrator

    orchestration_path = request / "ORCHESTRATION_STATE.json"
    if not orchestration_path.exists():
        orchestrator.initialize_state(orchestration_path, guard_path)
    orchestration = orchestrator.load_state(orchestration_path)
    matches = []
    for recorded in orchestration.get("handoffs", []):
        if recorded["role"] == "WEB_IMAGE":
            existing = orchestrator.load_json(orchestrator.normalize_project_path(recorded["path"]))
            if orchestrator.relative_project_path(packet_path) in existing["input_paths"]:
                matches.append(existing)
    if len(matches) > 1:
        raise ValueError("Multiple Web handoffs for one original journal are ambiguous")
    handoff = matches[0] if matches else orchestrator.dispatch_handoff(
        orchestration_path, "WEB_IMAGE", "Execute the complete final approved Web image specification; return the original download.",
        packet["input_paths"], [str(request)], [], packet["stage"], "REQUESTED_DELIVERABLE",
    )
    bound = {"path": str(packet_path), "sha256": sha(packet_path.read_bytes())}
    if state.get("web_handoff") is not None and state["web_handoff"] != bound:
        raise ValueError("Prepared journal is already bound to a different agent packet")
    state["web_handoff"] = bound
    web.save(journal, state)
    return {"packet_path": str(packet_path), "packet_sha256": packet["packet_sha256"],
            "agent_type": "web_image", "handoff": handoff,
            "agent_prompt": f"Execute only WEB_IMAGE packet {packet_path} in workspace {workspace}. Read its complete final specification; validate with the project CLI before the single submission. Return the original downloaded file and receipt; parent owns visual QA and completion."}


def validate_bound_handoff(journal, state) -> None:
    """Called by execute under its journal lock, before UNKNOWN or any send."""
    binding = state.get("web_handoff")
    if binding is None:
        return
    path = Path(binding["path"]).resolve()
    if sha(path.read_bytes()) != binding.get("sha256"):
        raise ValueError("Bound final-spec packet changed before submission")
    packet = validate_packet(path, for_submission=True)
    if Path(packet["journal_path"]).resolve() != Path(journal).resolve():
        raise ValueError("Final-spec packet belongs to another journal")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    build = sub.add_parser("create")
    build.add_argument("journal")
    build.add_argument("--final-spec", required=True)
    check = sub.add_parser("check")
    check.add_argument("packet")
    args = parser.parse_args()
    result = create(args.journal, args.final_spec) if args.operation == "create" else validate_packet(args.packet)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
