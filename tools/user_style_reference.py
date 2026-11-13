"""Prepare a same-request, single-sheet continuation from explicit chat STYLE images.

This lane has no inferred numeric fidelity and cannot carry character, body-library,
or stage-output inputs. An original STYLE sheet may also guide explicitly selected
schematic shape regions, without becoming an identity or anatomy authority.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path

if __package__:
    from . import generation_risk_assessor as risk
    from . import task_execution_guard as guard
else:
    import generation_risk_assessor as risk
    import task_execution_guard as guard

TASK_KIND = "IMAGE_GENERATION_USER_STYLE"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_call(call: dict, request_id: str, plan_path: str | Path,
                  locked_character: str = "") -> dict:
    if locked_character.upper() not in {"", "NONE", "NO_CHARACTER"}:
        raise guard.GuardError("User STYLE continuation cannot replace a named character's identity route.")
    if not isinstance(call, dict):
        raise guard.GuardError("User STYLE continuation requires an exact call.")
    if set(call) != {"request_id", "stage_id", "output_count", "user_selections", "slots",
                     "style_plan_sha256", "prompt", "risk_assessment"}:
        raise guard.GuardError("Unsupported call fields; implicit references and output overrides are forbidden.")
    selections = guard.validate_user_generation_selections(call, no_references=False)
    if (not selections["style"].startswith("USER_STYLE:")
            or selections["reference_policy"] != "USER_ATTACHED_REFERENCES"
            or selections["character"] != "NONE"):
        raise guard.GuardError("User STYLE lane requires explicit user style, attached references, and no project character.")
    if (call.get("request_id") != request_id or type(call.get("output_count")) is not int or call.get("output_count") != 1
            or call.get("stage_id") != "SINGLE_PASS"):
        raise guard.GuardError("User STYLE lane supports one requested SINGLE_PASS sheet only.")
    plan_file = Path(plan_path).resolve()
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    if (plan.get("kind") != TASK_KIND or plan.get("request_id") != request_id
            or call.get("style_plan_sha256") != _hash(plan_file)
            or plan.get("user_selections") != call["user_selections"]):
        raise guard.GuardError("User STYLE plan/selection binding changed.")
    sources = plan.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 5:
        raise guard.GuardError("Select a bounded set of one to five original STYLE attachments.")
    if call.get("slots") != sources:
        raise guard.GuardError("Physical STYLE slots differ from the prepared source selection.")
    target = plan.get("target_subject_label")
    if target not in {"FEMALE", "MALE"} or not plan.get("target_evidence"):
        raise guard.GuardError("Target subject metadata requires explicit current-request evidence.")
    if not plan.get("chat_id"):
        raise guard.GuardError("User STYLE sources require current-chat provenance.")
    seen = set()
    for source in sources:
        if not isinstance(source, dict):
            raise guard.GuardError("Invalid source metadata.")
        file = Path(str(source.get("path", ""))).resolve()
        origin = Path(str(source.get("source_path", ""))).resolve()
        digest = str(source.get("sha256", ""))
        if (file.suffix.lower() not in guard.IMAGE_EXTENSIONS or not file.is_file()
                or not origin.is_file() or _hash(file) != digest or _hash(origin) != digest):
            raise guard.GuardError("Original attached STYLE source is absent or its hash changed.")
        if digest in seen:
            raise guard.GuardError("Duplicate physical STYLE attachment.")
        seen.add(digest)
        roles = source.get("active_roles")
        if (roles not in (["STYLE"], ["STYLE", "SUBJECT"]) or source.get("physically_attach") is not True
                or source.get("origin") != "USER_CHAT_ATTACHMENT"
                or source.get("chat_id") != plan["chat_id"] or not source.get("message_evidence")):
            raise guard.GuardError("Only physically attached, current-chat original STYLE sources are allowed.")
        if "SUBJECT" in roles and (
                source.get("subject_scope") != "SCHEMATIC_SHAPE_GUIDE"
                or not source.get("subject_regions") or not source.get("subject_evidence")
                or not isinstance(source["subject_regions"], list)
                or any(not isinstance(region, str) or not region.strip() for region in source["subject_regions"])):
            raise guard.GuardError("SUBJECT is limited to explicit schematic shape regions, not character identity.")
        for candidate in (file, origin):
            parts = [part.upper() for part in candidate.parts]
            if "00_PENDING" in parts or ".TASK_GUARD" in parts:
                raise guard.GuardError("Generated request outputs cannot become STYLE sources in this lane.")
        if (source.get("subject_label") not in {target, "NO_SUBJECT"}
                or source.get("subject_label_status") != "REVIEWED"
                or source.get("subject_label_sha256") != digest
                or not source.get("subject_label_evidence")):
            raise guard.GuardError("STYLE source lacks matching, current-hash reviewed subject metadata.")
        review = source.get("review", {})
        if (review.get("view") != "FULL_RESOLUTION" or review.get("outcome") != "PASS"
                or not review.get("findings") or not review.get("limitations")):
            raise guard.GuardError("Selected STYLE source needs its actual full-resolution review.")
    prompt = call.get("prompt", {})
    text = prompt.get("text", "")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if not text.strip() or prompt.get("text_sha256") != digest:
        raise guard.GuardError("Exact user STYLE prompt hash mismatch.")
    authority = plan.get("rendering_authority", "")
    if not authority or not text.startswith(authority + "\n\n"):
        raise guard.GuardError("Exact prompt is missing its prepared physical STYLE authority.")
    try:
        risk.validate_assessment(call.get("risk_assessment", {}), text, sources)
    except risk.RiskAssessmentError as error:
        raise guard.GuardError(str(error)) from error
    encoded = json.dumps(call, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"request_id": request_id, "stage": "SINGLE_PASS", "prompt_sha256": digest,
            "execution_call_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "style_plan_sha256": _hash(plan_file)}


def prepare(args: argparse.Namespace) -> dict:
    state_path = Path(args.guard).resolve()
    guard.assert_active_request_path(state_path)
    state = guard.load_guard(state_path)
    for input_path in (args.selection, args.prompt_file):
        if Path(input_path).resolve().parent != state_path.parent:
            raise guard.GuardError("Preparation inputs must remain inside this active request folder.")
    payload = json.loads(Path(args.selection).read_text(encoding="utf-8"))
    if (payload.get("request_id") != state["request_id"]
            or payload.get("chat_id") != state.get("owner_chat_id")
            or payload.get("user_quote") != args.user_quote):
        raise guard.GuardError("Source selection belongs to another request/chat or user instruction.")
    guard.validate_user_style_transition(state, payload, args.user_quote)
    root = state_path.parent
    plan_path, call_path = root / args.plan_name, root / args.call_name
    for file in (plan_path, call_path):
        if file.parent.resolve() != root or file.exists():
            raise guard.GuardError("Prepared files must be new siblings in the same active request folder.")
    selections = {
        "style": {"choice": "USER_STYLE:ORIGINAL_ATTACHED_DRAWINGS", "user_quote": args.user_quote},
        "reference_policy": {"choice": "USER_ATTACHED_REFERENCES", "user_quote": args.user_quote},
        "character": {"choice": "NONE", "user_quote": args.user_quote},
    }
    authority = "[STYLE_RENDERING_AUTHORITY]\n" + "\n".join(
        f"Image {i}: {', '.join(row['active_roles'])}. {row['review']['findings']} "
        + (f"Schematic shape regions: {', '.join(row['subject_regions'])}. " if "SUBJECT" in row['active_roles'] else "")
        + f"Limitations: {row['review']['limitations']}"
        for i, row in enumerate(payload["sources"], 1)
    ) + "\n[/STYLE_RENDERING_AUTHORITY]"
    plan = {**payload, "kind": TASK_KIND, "task_revision": state["task_revision"], "user_selections": selections,
            "rendering_authority": authority, "fidelity": None,
            "fidelity_basis": "Explicit source-style request; no numeric percentage was selected or inferred."}
    guard.atomic_write_json(plan_path, plan)
    text = authority + "\n\n" + Path(args.prompt_file).read_text(encoding="utf-8")
    report_path = root / (call_path.stem + "_RISK.json")
    if report_path.exists():
        raise guard.GuardError("Risk report already exists; preserve it and choose new output names.")
    specs = [f"{row['path']}::{row['risk']}::{row['use_impact']}::{row['risk_reason']}::{','.join(row['active_roles'])}"
             for row in payload["sources"]]
    with contextlib.redirect_stdout(io.StringIO()):
        risk.command_assess(argparse.Namespace(lexicon=risk.DEFAULT_LEXICON, text=text,
            prompt_file="", revised_text="", revised_prompt_file="", reference=specs,
            output=str(report_path), overwrite=False, json=False))
    call = {"request_id": state["request_id"], "stage_id": "SINGLE_PASS", "output_count": 1,
            "user_selections": selections, "slots": payload["sources"],
            "style_plan_sha256": _hash(plan_path),
            "prompt": {"text": text, "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()},
            "risk_assessment": json.loads(report_path.read_text(encoding="utf-8"))}
    validate_call(call, state["request_id"], plan_path,
                  str(state.get("locked_invariants", {}).get("character", "")))
    guard.atomic_write_json(call_path, call)
    guard.select_user_style_route(state_path, call, plan_path, args.user_quote)
    return {"reference_plan": str(plan_path), "execution_call": str(call_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare-generation"])
    parser.add_argument("--guard", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--user-quote", required=True)
    parser.add_argument("--plan-name", default="USER_STYLE_REFERENCE_PLAN.json")
    parser.add_argument("--call-name", default="USER_STYLE_EXECUTION_CALL.json")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args), ensure_ascii=False, indent=2))
        return 0
    except (guard.GuardError, risk.RiskAssessmentError, OSError, ValueError, KeyError) as error:
        print(f"USER_STYLE_ERROR={error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
