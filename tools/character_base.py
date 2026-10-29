"""Step-by-step driver for a new StoryArt CHARACTER_BASE request.

The existing managers stay the authority: this tool keeps the character
passport, the two user approval gates (face and front), the per-request
failure log, and wraps record-generation plus the matching guard checkpoint
for one stage. It never calls an image generator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

TOOLS = Path(__file__).resolve().parent

STAGES = (
    "01_FACE_IDENTITY",
    "02_PHYSIQUE_FRONT",
    "03_PHYSIQUE_SIDE",
    "04_PHYSIQUE_BACK",
    "05_CHARACTER_ASSEMBLY",
)
USER_GATES = ("01_FACE_IDENTITY", "02_PHYSIQUE_FRONT")
PHYSIQUE_STAGES = ("02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK")
BODY_STAGES = (*PHYSIQUE_STAGES, "05_CHARACTER_ASSEMBLY")
STAGE_LABELS = {
    "01_FACE_IDENTITY": "FACE",
    "02_PHYSIQUE_FRONT": "FRONT",
    "03_PHYSIQUE_SIDE": "SIDE",
    "04_PHYSIQUE_BACK": "BACK",
    "05_CHARACTER_ASSEMBLY": "3/4",
}

BRIEF_FILE = "CHARACTER_BRIEF.json"
GATES_FILE = "CHARACTER_BASE_GATES.json"
FAILURES_FILE = "CHARACTER_BASE_FAILURES.json"

BRIEF_REQUIRED_FIELDS = (
    "apparent_age",
    "species",
    "gender_presentation",
    "height_heads",
    "build",
    "skin_or_fur",
    "hair",
    "eyes",
    "face_shape",
    "distinctive_features",
)
BRIEF_OPTIONAL_FIELDS = ("ears", "palette", "notes")
BRIEF_SOURCES = ("USER", "CANON_IMAGE", "AGENT_PROPOSAL")
BRIEF_LABELS = {
    "apparent_age": "Apparent age",
    "species": "Species",
    "gender_presentation": "Gender presentation",
    "height_heads": "Height in heads",
    "build": "Build",
    "skin_or_fur": "Skin or fur",
    "hair": "Hair",
    "eyes": "Eyes",
    "face_shape": "Face shape",
    "distinctive_features": "Distinctive features",
    "ears": "Ears",
    "palette": "Palette",
    "notes": "Notes",
}
# Profile locked_invariants keys filled from the passport on approval.
INVARIANT_FIELDS = {
    "face_geometry": ("face_shape", "eyes", "ears", "apparent_age"),
    "body_proportions": ("height_heads", "build"),
    "skin_tone": ("skin_or_fur",),
    "permanent_features": ("hair", "distinctive_features", "palette"),
}

# record-generation flag for each stage QA name. Visual-review layers
# (ANATOMY_REVIEW, VISIBLE_DEFECTS, PROMPT_ADHERENCE) come from the review JSON.
QA_FLAGS = {
    "ATTACHMENTS": "--qa-attachments",
    "CANVAS": "--qa-canvas",
    "STAGE_LAYER": "--qa-stage-layer",
    "FACE_GEOMETRY": "--qa-face",
    "BODY_SILHOUETTE": "--qa-body-silhouette",
    "BODY_PROPORTIONS": "--qa-body-proportions",
    "LIMB_PROPORTIONS": "--qa-limb-proportions",
    "STYLE": "--qa-style",
    "BODY_RENDERING_STYLE": "--qa-body-style",
    "EXPRESSION": "--qa-expression",
    "NEUTRAL_BACKDROP": "--qa-neutral-backdrop",
    "FRONT_VIEW": "--qa-view",
    "SIDE_VIEW": "--qa-view",
    "BACK_VIEW": "--qa-view",
    "SAFE_COVERAGE": "--qa-safe-coverage",
    "CLOTHING_TOPOLOGY": "--qa-clothing-topology",
    "MULTIVIEW_CONSISTENCY": "--qa-multiview-consistency",
    "CLOTHING": "--qa-clothing",
    "CAMERA": "--qa-camera",
    "POSE_CONTACTS": "--qa-pose-contacts",
}
VISUAL_REVIEW_LAYERS = {"ANATOMY_REVIEW", "VISIBLE_DEFECTS", "PROMPT_ADHERENCE"}


class CharacterBaseError(RuntimeError):
    """A user-facing validation error."""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path, default: object = None) -> object:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise CharacterBaseError(f"Cannot read {path}: {error}") from error


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def request_dir(value: str) -> Path:
    folder = Path(value).expanduser().resolve()
    if not (folder / "EXECUTION_GUARD.json").is_file():
        raise CharacterBaseError(f"{folder} is not an active request folder: EXECUTION_GUARD.json is missing.")
    return folder


def load_guard(folder: Path) -> dict:
    state = read_json(folder / "EXECUTION_GUARD.json", {})
    if not isinstance(state, dict):
        raise CharacterBaseError("EXECUTION_GUARD.json must be an object.")
    return state


def load_plan(folder: Path) -> dict:
    plan = read_json(folder / "REFERENCE_PLAN.json", {})
    return plan if isinstance(plan, dict) else {}


def guard_stages(state: dict) -> dict[str, dict]:
    return {
        str(row.get("id")): row
        for row in state.get("required_stages", [])
        if isinstance(row, dict) and row.get("id")
    }


def is_child(plan: dict) -> bool:
    contract = plan.get("character_base_contract")
    return isinstance(contract, dict) and contract.get("age_group") == "CHILD"


# ---------------------------------------------------------------- passport

def brief_template() -> dict:
    return {
        "schema_version": 1,
        "name": "",
        "age_group": "ADULT",
        "fields": {
            key: {"value": "", "source": "AGENT_PROPOSAL"}
            for key in (*BRIEF_REQUIRED_FIELDS, *BRIEF_OPTIONAL_FIELDS)
        },
        "must_not": [],
        "canon_sources": [],
    }


def validate_brief(raw: object) -> dict:
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise CharacterBaseError("The passport must be an object with schema_version=1.")
    errors: list[str] = []
    name = str(raw.get("name", "")).strip()
    if not name:
        errors.append("name is empty")
    age_group = str(raw.get("age_group", "")).upper()
    if age_group not in {"ADULT", "CHILD"}:
        errors.append("age_group must be ADULT or CHILD")
    fields = raw.get("fields")
    if not isinstance(fields, dict):
        raise CharacterBaseError("The passport needs a fields object.")
    clean: dict[str, dict[str, str]] = {}
    for key, entry in fields.items():
        if key not in BRIEF_REQUIRED_FIELDS and key not in BRIEF_OPTIONAL_FIELDS:
            errors.append(f"unknown field {key!r}")
            continue
        if not isinstance(entry, dict):
            errors.append(f"{key} must be an object with value and source")
            continue
        value = str(entry.get("value", "")).strip()
        source = str(entry.get("source", "")).upper()
        if source not in BRIEF_SOURCES:
            errors.append(f"{key}.source must be one of {', '.join(BRIEF_SOURCES)}")
        if value:
            clean[key] = {"value": value, "source": source}
    for key in BRIEF_REQUIRED_FIELDS:
        if key not in clean:
            errors.append(f"{key} is empty")
    must_not = raw.get("must_not", [])
    if not isinstance(must_not, list) or not all(isinstance(item, str) for item in must_not):
        errors.append("must_not must be a list of strings")
        must_not = []
    canon: list[dict[str, str]] = []
    for item in raw.get("canon_sources", []) or []:
        path = Path(str(item.get("path", "") if isinstance(item, dict) else item)).expanduser()
        if not path.is_file():
            errors.append(f"canon source does not exist: {path}")
            continue
        canon.append({"path": str(path.resolve()), "sha256": file_sha256(path)})
    if errors:
        raise CharacterBaseError("Passport is incomplete: " + "; ".join(errors))
    return {
        "schema_version": 1,
        "name": name,
        "age_group": age_group,
        "fields": clean,
        "must_not": [item.strip() for item in must_not if item.strip()],
        "canon_sources": canon,
    }


def load_confirmed_brief(folder: Path) -> dict | None:
    brief = read_json(folder / BRIEF_FILE)
    if not isinstance(brief, dict) or not isinstance(brief.get("confirmation"), dict):
        return None
    return brief


def passport_prompt(brief: dict) -> str:
    fields = brief["fields"]
    lines = [f"Character passport for {brief['name']} (hard identity locks, keep identical in every view):"]
    for key in (*BRIEF_REQUIRED_FIELDS, *BRIEF_OPTIONAL_FIELDS):
        if key in fields and key != "notes":
            lines.append(f"- {BRIEF_LABELS[key]}: {fields[key]['value']}.")
    if brief.get("must_not"):
        lines.append("- Never: " + "; ".join(brief["must_not"]) + ".")
    return "\n".join(lines)


def profile_patch_from_brief(brief: dict, failures: list[dict]) -> dict:
    """Profile facts for approve-character: locked invariants and known failures."""
    fields = brief["fields"]
    invariants = {}
    for target, keys in INVARIANT_FIELDS.items():
        parts = [f"{BRIEF_LABELS[key]}: {fields[key]['value']}" for key in keys if key in fields]
        invariants[target] = "; ".join(parts)
    if brief.get("must_not"):
        never = "Never: " + "; ".join(brief["must_not"])
        invariants["permanent_features"] = "; ".join(part for part in (invariants["permanent_features"], never) if part)
    patch: dict[str, object] = {"locked_invariants": invariants}
    modes = []
    for row in failures:
        text = row.get("avoid") or row.get("finding")
        if text and text not in modes:
            modes.append(text)
    if modes:
        patch["known_failure_modes"] = modes
    return patch


def command_brief_template(args: argparse.Namespace) -> None:
    output = Path(args.output).expanduser()
    if output.exists():
        raise CharacterBaseError(f"{output} already exists; edit it instead of overwriting.")
    write_json(output, brief_template())
    print(f"BRIEF_TEMPLATE={output.resolve()}")


def command_brief(args: argparse.Namespace) -> None:
    folder = request_dir(args.request_dir)
    quote = args.confirmation_quote.strip()
    if not quote:
        raise CharacterBaseError("--confirmation-quote must hold the user's own confirmation of the passport.")
    brief = validate_brief(read_json(Path(args.brief_file).expanduser()))
    previous = read_json(folder / BRIEF_FILE)
    revision = 1
    if isinstance(previous, dict):
        revision = int(previous.get("revision", 0)) + 1
        write_json(folder / "CHARACTER_BRIEF_HISTORY" / f"revision_{previous.get('revision', 0)}.json", previous)
    brief["revision"] = revision
    brief["confirmation"] = {"user_quote": quote, "confirmed_at": now()}
    write_json(folder / BRIEF_FILE, brief)
    gates = read_json(folder / GATES_FILE, {})
    print(f"BRIEF={folder / BRIEF_FILE}")
    print(f"BRIEF_REVISION={revision}")
    for stage, gate in (gates.items() if isinstance(gates, dict) else ()):
        if int(gate.get("brief_revision", 0)) < revision:
            print(f"BRIEF_CHANGED_SINCE_GATE={stage}")
    print("PASSPORT_PROMPT_BEGIN")
    print(passport_prompt(brief))
    print("PASSPORT_PROMPT_END")


# ------------------------------------------------------- gates and failures

def stage_evidence_hash(state: dict, stage: str) -> str:
    row = guard_stages(state).get(stage)
    if not row or row.get("status") != "COMPLETED":
        return ""
    digests = row.get("evidence_sha256")
    if isinstance(digests, dict) and digests:
        return sorted(str(value) for value in digests.values())[0]
    return ""


def valid_gate(folder: Path, state: dict, stage: str) -> dict | None:
    gates = read_json(folder / GATES_FILE, {})
    gate = gates.get(stage) if isinstance(gates, dict) else None
    current = stage_evidence_hash(state, stage)
    if isinstance(gate, dict) and current and gate.get("evidence_sha256") == current:
        return gate
    return None


def command_gate(args: argparse.Namespace) -> None:
    folder = request_dir(args.request_dir)
    stage = args.stage.upper()
    if stage not in USER_GATES:
        raise CharacterBaseError(f"Only {', '.join(USER_GATES)} wait for the user; other stages continue automatically.")
    quote = args.user_quote.strip()
    if not quote:
        raise CharacterBaseError("--user-quote must hold the user's own approval words.")
    state = load_guard(folder)
    digest = stage_evidence_hash(state, stage)
    if not digest:
        raise CharacterBaseError(f"{stage} is not completed with QA-passed evidence yet.")
    brief = load_confirmed_brief(folder)
    gates = read_json(folder / GATES_FILE, {})
    gates = gates if isinstance(gates, dict) else {}
    gates[stage] = {
        "user_quote": quote,
        "approved_at": now(),
        "evidence_sha256": digest,
        "brief_revision": int(brief.get("revision", 0)) if brief else 0,
    }
    write_json(folder / GATES_FILE, gates)
    print(f"GATE_APPROVED={stage}")


def load_failures(folder: Path) -> list[dict]:
    rows = read_json(folder / FAILURES_FILE, [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def append_failure(folder: Path, stage: str, finding: str, avoid: str = "") -> dict:
    finding = finding.strip()
    if not finding:
        raise CharacterBaseError("A failure entry needs a concrete finding.")
    rows = load_failures(folder)
    row = {"stage": stage, "finding": finding, "avoid": avoid.strip(), "recorded_at": now()}
    rows.append(row)
    write_json(folder / FAILURES_FILE, rows)
    return row


def avoid_lines(folder: Path) -> list[str]:
    """Every earlier finding, phrased as an instruction for the following calls."""
    lines: list[str] = []
    for row in load_failures(folder):
        text = row.get("avoid") or f"Do not repeat: {row.get('finding')}"
        if text not in lines:
            lines.append(text)
    return lines


def command_log_failure(args: argparse.Namespace) -> None:
    folder = request_dir(args.request_dir)
    row = append_failure(folder, args.stage.upper(), args.finding, args.avoid)
    print(f"FAILURE_LOGGED={row['stage']}")
    print(f"FAILURES_TOTAL={len(load_failures(folder))}")


# ------------------------------------------------------------- next stage

def stage_required_qa(plan: dict, stage: str) -> list[str]:
    required = ["ATTACHMENTS", "CANVAS", "STAGE_LAYER"]
    for row in plan.get("generation_workflow", {}).get("stages", []):
        if isinstance(row, dict) and row.get("stage_id") == stage:
            required.extend(str(name) for name in row.get("required_qa", []))
    schema = int(plan.get("semantic_qa_schema", 0) or 0)
    if schema >= 1:
        required.append("STYLE")
    if schema >= 2 and stage in BODY_STAGES:
        required.append("BODY_RENDERING_STYLE")
    if schema >= 3 and stage in BODY_STAGES:
        required.append("LIMB_PROPORTIONS")
    return [name for index, name in enumerate(required) if name not in required[:index] and name not in VISUAL_REVIEW_LAYERS]


def next_action(folder: Path) -> dict:
    state = load_guard(folder)
    plan = load_plan(folder)
    brief = load_confirmed_brief(folder)
    if brief is None:
        return {"action": "CONFIRM_BRIEF", "message": "Show the passport to the user and record it with `brief` before FACE_IDENTITY."}
    stages = guard_stages(state)
    for stage in STAGES:
        row = stages.get(stage)
        if row is None:
            return {"action": "FIX_GUARD", "message": f"The guard does not list required stage {stage}."}
        if row.get("status") != "COMPLETED":
            result = {
                "action": "GENERATE_STAGE",
                "stage": stage,
                "user_gate_after": stage in USER_GATES,
                "required_qa": stage_required_qa(plan, stage),
                "passport_prompt": passport_prompt(brief),
                "avoid": avoid_lines(folder),
            }
            if stage in PHYSIQUE_STAGES and not is_child(plan):
                result["swimwear_rung"] = "TWO_PIECE"
            return result
        if stage in USER_GATES and valid_gate(folder, state, stage) is None:
            return {
                "action": "WAIT_USER_APPROVAL",
                "stage": stage,
                "evidence": list(row.get("evidence", [])),
                "message": "Show this full-resolution file and wait; record the answer with `gate`.",
            }
    return {
        "action": "SHOW_SHEET_AND_APPROVE",
        "evidence": {stage: list(stages[stage].get("evidence", [])) for stage in STAGES},
        "message": "Build the preview sheet, send it with the five separate files, then run approve-character after the user's approval.",
    }


def command_next(args: argparse.Namespace) -> None:
    result = next_action(request_dir(args.request_dir))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"NEXT_ACTION={result['action']}")
    if result.get("stage"):
        print(f"STAGE={result['stage']}")
    if "user_gate_after" in result:
        print(f"USER_GATE_AFTER={'true' if result['user_gate_after'] else 'false'}")
    if result.get("swimwear_rung"):
        print(f"SWIMWEAR_RUNG={result['swimwear_rung']}")
    if result.get("required_qa"):
        print(f"REQUIRED_QA={','.join(result['required_qa'])}")
    if result.get("message"):
        print(f"MESSAGE={result['message']}")
    if result.get("passport_prompt"):
        print("PASSPORT_PROMPT_BEGIN")
        print(result["passport_prompt"])
        print("PASSPORT_PROMPT_END")
    for line in result.get("avoid", []):
        print(f"AVOID={line}")


# ------------------------------------------------------------ visual review

def command_review_template(args: argparse.Namespace) -> None:
    if __package__:
        from .style_pack_manager import prompt_review_clauses
    else:
        from style_pack_manager import prompt_review_clauses
    folder = request_dir(args.request_dir)
    state = load_guard(folder)
    attempt = next((row for row in state.get("attempts", []) if row.get("attempt_id") == args.attempt_id), None)
    if not isinstance(attempt, dict) or not isinstance(attempt.get("execution_snapshot"), dict):
        raise CharacterBaseError("--attempt-id does not name an attempt with an executed-call snapshot.")
    snapshot = attempt["execution_snapshot"]
    prompt = str(snapshot.get("execution_call", {}).get("prompt", {}).get("text", ""))
    image = Path(args.image).expanduser().resolve()
    if not image.is_file():
        raise CharacterBaseError(f"Output image does not exist: {image}")
    report = {
        "schema_version": 1,
        "request_id": state.get("request_id"),
        "attempt_id": args.attempt_id,
        "task_revision": attempt.get("task_revision"),
        "output_sha256": file_sha256(image),
        "prompt_sha256": snapshot.get("prompt_sha256"),
        "executed_plan_sha256": snapshot.get("reference_plan_snapshot_sha256"),
        "author": args.author,
        "anatomy_review": {"status": "TODO", "checked_scope": "Visible anatomy across the full image at original resolution.", "findings": []},
        "visible_defect_review": {"status": "TODO", "checked_scope": "Entire output at full resolution.", "findings": []},
        "prompt_adherence": {
            "status": "TODO",
            "checked_scope": "FULL_EXECUTED_PROMPT",
            "all_explicit_constraints_assessed": True,
            "constraints": [
                {"constraint": clause, "status": "TODO", "evidence": ""}
                for clause in prompt_review_clauses(prompt)
            ],
        },
    }
    output = Path(args.output).expanduser()
    write_json(output, report)
    print(f"VISUAL_REVIEW_TEMPLATE={output.resolve()}")
    print(f"PROMPT_CLAUSES={len(report['prompt_adherence']['constraints'])}")


# ------------------------------------------------------------- record stage

def run_tool(script: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-X", "utf8", str(TOOLS / script), *arguments]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise CharacterBaseError(f"{script} {arguments[0]} failed with exit code {result.returncode}.")
    return result


def output_value(stdout: str, key: str) -> str:
    for line in stdout.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def command_record(args: argparse.Namespace) -> None:
    folder = request_dir(args.request_dir)
    stage = args.stage.upper()
    if stage not in STAGES:
        raise CharacterBaseError(f"--stage must be one of {', '.join(STAGES)}.")
    plan = load_plan(folder)
    qa = read_json(Path(args.qa_json).expanduser())
    if not isinstance(qa, dict) or not isinstance(qa.get("results"), dict):
        raise CharacterBaseError("--qa-json must be an object with a results map of QA name to PASS or FAIL.")
    results = {str(key).upper(): str(value).upper() for key, value in qa["results"].items()}
    required = stage_required_qa(plan, stage)
    missing = [name for name in required if results.get(name) not in {"PASS", "FAIL"}]
    if missing:
        raise CharacterBaseError("QA is missing for this stage: " + ", ".join(missing))
    failed = [name for name in required if results[name] == "FAIL"]
    review = read_json(Path(args.visual_review_json).expanduser(), {})
    review_failed = [
        layer for layer, key in (
            ("ANATOMY_REVIEW", "anatomy_review"),
            ("VISIBLE_DEFECTS", "visible_defect_review"),
            ("PROMPT_ADHERENCE", "prompt_adherence"),
        )
        if isinstance(review, dict) and review.get(key, {}).get("status") == "FAIL"
    ]
    final_stage = stage == STAGES[-1]
    status = "REJECTED" if failed or review_failed else ("TEST" if final_stage else "STAGING")

    record_args = [
        "record-generation", "--workspace", args.workspace, "--style-name", args.style_name,
        "--image", args.image, "--request-id", args.request_id, "--description", args.description,
        "--fidelity", str(args.fidelity), "--status", status, "--character-id", "NEW",
        "--attempt-id", args.attempt_id, "--reference-plan", str(folder / "REFERENCE_PLAN.json"),
        "--visual-review-json", args.visual_review_json, "--stage-id", stage,
    ]
    flags: dict[str, str] = {}
    for name in required:
        flag = QA_FLAGS.get(name)
        if flag is None:
            raise CharacterBaseError(f"No record-generation flag is known for QA {name}.")
        # FRONT/SIDE/BACK_VIEW share --qa-view; any FAIL wins.
        if flags.get(flag) != "FAIL":
            flags[flag] = results[name]
    for flag, value in flags.items():
        record_args.extend((flag, value))
    if "LIMB_PROPORTIONS" in required and results.get("LIMB_PROPORTIONS") == "PASS":
        record_args.extend(("--limb-qa-evidence", str(qa.get("limb_qa_evidence", ""))))
    if qa.get("notes"):
        record_args.extend(("--notes", str(qa["notes"])))
    recorded = run_tool("style_pack_manager.py", record_args)
    recorded_status = output_value(recorded.stdout, "STATUS") or status
    guard = folder / "EXECUTION_GUARD.json"
    adult_physique = stage in PHYSIQUE_STAGES and not is_child(plan)
    observed = str(qa.get("observed_topology", "")).upper()

    if recorded_status == "REJECTED":
        layer = (failed or review_failed or ["QA"])[0]
        findings = [str(item) for item in qa.get("findings", []) if str(item).strip()]
        summary = "; ".join(findings) or f"{stage} failed {', '.join(failed or review_failed)}."
        reject_args = [
            "checkpoint", "--state", str(guard), "--event", "ATTEMPT_REJECTED", "--attempt-id", args.attempt_id,
            "--stage", stage, "--qa-layer", layer, "--outcome", "QA_REJECTED", "--summary", summary,
        ]
        if adult_physique:
            reject_args.extend(("--swimwear-rung", "TWO_PIECE"))
            if observed:
                reject_args.extend(("--observed-topology", observed))
        run_tool("task_execution_guard.py", reject_args)
        avoid = [str(item) for item in qa.get("avoid", []) if str(item).strip()]
        for index, finding in enumerate(findings or [summary]):
            append_failure(folder, stage, finding, avoid[index] if index < len(avoid) else "")
        print(f"CHARACTER_BASE_STAGE={stage}")
        print("CHARACTER_BASE_RESULT=REJECTED")
        return

    style_file = output_value(recorded.stdout, "STYLE_FILE")
    if not style_file:
        raise CharacterBaseError("record-generation did not report STYLE_FILE; complete the stage manually.")
    complete_args = [
        "checkpoint", "--state", str(guard), "--event", "STAGE_COMPLETED", "--stage", stage,
        "--evidence", style_file, "--summary", f"{stage} recorded with all required QA passed.",
    ]
    if adult_physique:
        complete_args.extend(("--swimwear-rung", "TWO_PIECE", "--observed-topology", observed or "TWO_PIECE"))
    run_tool("task_execution_guard.py", complete_args)
    print(f"CHARACTER_BASE_STAGE={stage}")
    print("CHARACTER_BASE_RESULT=COMPLETED")
    print(f"USER_GATE={'WAIT' if stage in USER_GATES else 'NONE'}")


# ------------------------------------------------------------------ canvas

def subject_bounds(image, threshold: int = 24) -> tuple[int, int, tuple[int, int, int]]:
    """Top/bottom rows of the subject on a plain backdrop, and the backdrop colour."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()
    border = [pixels[x, 0] for x in range(width)] + [pixels[x, height - 1] for x in range(width)]
    border += [pixels[0, y] for y in range(height)] + [pixels[width - 1, y] for y in range(height)]
    background = tuple(int(median(channel)) for channel in zip(*border))
    step = max(1, width // 400)
    minimum = max(2, (width // step) // 500)
    rows = []
    for y in range(height):
        count = 0
        for x in range(0, width, step):
            pixel = pixels[x, y]
            if max(abs(pixel[index] - background[index]) for index in range(3)) > threshold:
                count += 1
        if count >= minimum:
            rows.append(y)
    if not rows:
        raise CharacterBaseError("No subject found against the backdrop; check the image or lower --threshold.")
    return rows[0], rows[-1], background


def command_fit_canvas(args: argparse.Namespace) -> None:
    from PIL import Image

    source = Path(args.image).expanduser().resolve()
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    top, bottom, background = subject_bounds(image, args.threshold)
    percent = (bottom - top + 1) / height * 100
    print(f"SUBJECT_HEIGHT_PERCENT={percent:.1f}")
    print(f"ALLOWED_RANGE={args.min_percent:g}-{args.max_percent:g}")
    if percent < args.min_percent:
        print("CANVAS_ACTION=TOO_SMALL")
        print("MESSAGE=The figure is too small; regenerate with a closer framing instead of cropping.")
        return
    if percent <= args.max_percent:
        print("CANVAS_ACTION=OK")
        return
    target = args.target_percent / 100
    new_height = math.ceil((bottom - top + 1) / target)
    new_width = round(new_height * width / height)
    canvas = Image.new("RGB", (new_width, new_height), background)
    canvas.paste(image, ((new_width - width) // 2, (new_height - height) // 2))
    output = Path(args.output).expanduser() if args.output else source.with_name(f"{source.stem}_canvas_fit.png")
    if output.resolve() == source:
        raise CharacterBaseError("--output must differ from the original; the original is never changed.")
    canvas.save(output)
    provenance = {
        "schema_version": 1,
        "operation": "DETERMINISTIC_CANVAS_EXTENSION",
        "source": str(source),
        "source_sha256": file_sha256(source),
        "output": str(output.resolve()),
        "output_sha256": file_sha256(output),
        "background_rgb": list(background),
        "subject_height_percent_before": round(percent, 1),
        "subject_height_percent_after": round((bottom - top + 1) / new_height * 100, 1),
        "pixels_resampled": False,
    }
    write_json(output.with_suffix(".provenance.json"), provenance)
    print("CANVAS_ACTION=EXTENDED")
    print(f"OUTPUT={output.resolve()}")
    print(f"SUBJECT_HEIGHT_PERCENT_AFTER={provenance['subject_height_percent_after']}")


# ------------------------------------------------------------------- sheet

def command_sheet(args: argparse.Namespace) -> None:
    from PIL import Image, ImageDraw

    folder = request_dir(args.request_dir)
    state = load_guard(folder)
    stages = guard_stages(state)
    files: list[tuple[str, Path]] = []
    for stage in STAGES:
        row = stages.get(stage, {})
        evidence = [Path(item) for item in row.get("evidence", []) if Path(item).is_file()]
        if row.get("status") != "COMPLETED" or not evidence:
            raise CharacterBaseError(f"{stage} is not completed; the sheet needs all five stages.")
        files.append((stage, evidence[0]))
    panel_height = args.panel_height
    label_height = 48
    panels = []
    for stage, path in files:
        with Image.open(path) as opened:
            panel = opened.convert("RGB")
        panel = panel.resize((max(1, round(panel.width * panel_height / panel.height)), panel_height), Image.LANCZOS)
        panels.append((stage, path, panel))
    gutter = 24
    width = sum(panel.width for _, _, panel in panels) + gutter * (len(panels) + 1)
    sheet = Image.new("RGB", (width, panel_height + label_height + gutter * 2), (245, 243, 240))
    draw = ImageDraw.Draw(sheet)
    x = gutter
    for stage, _, panel in panels:
        sheet.paste(panel, (x, gutter))
        draw.text((x, gutter + panel_height + 12), STAGE_LABELS[stage], fill=(40, 40, 40))
        x += panel.width + gutter
    output = folder / "REVIEW" / "CHARACTER_SHEET_PREVIEW.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    write_json(output.with_suffix(".json"), {
        "schema_version": 1,
        "purpose": "PREVIEW_ONLY",
        "usage": "Review image only. Never attach it as a reference; each stage keeps its own full-resolution file.",
        "sources": [{"stage": stage, "path": str(path), "sha256": file_sha256(path)} for stage, path, _ in panels],
    })
    print(f"CHARACTER_SHEET_PREVIEW={output}")
    for stage, path, _ in panels:
        print(f"STAGE_FILE_{stage}={path}")


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    template = commands.add_parser("brief-template", help="Write an empty character passport to fill in.")
    template.add_argument("--output", required=True)
    template.set_defaults(handler=command_brief_template)

    brief = commands.add_parser("brief", help="Store the user-confirmed passport in the request folder.")
    brief.add_argument("--request-dir", required=True)
    brief.add_argument("--brief-file", required=True)
    brief.add_argument("--confirmation-quote", required=True, help="The user's exact words confirming the passport.")
    brief.set_defaults(handler=command_brief)

    gate = commands.add_parser("gate", help="Record the user's approval of FACE_IDENTITY or PHYSIQUE_FRONT.")
    gate.add_argument("--request-dir", required=True)
    gate.add_argument("--stage", required=True, choices=USER_GATES)
    gate.add_argument("--user-quote", required=True)
    gate.set_defaults(handler=command_gate)

    failure = commands.add_parser("log-failure", help="Add a finding that later stages must avoid.")
    failure.add_argument("--request-dir", required=True)
    failure.add_argument("--stage", required=True, choices=STAGES)
    failure.add_argument("--finding", required=True)
    failure.add_argument("--avoid", default="", help="The finding phrased as a prompt instruction.")
    failure.set_defaults(handler=command_log_failure)

    nxt = commands.add_parser("next", help="Show the next action, required QA, passport and avoid lines.")
    nxt.add_argument("--request-dir", required=True)
    nxt.add_argument("--json", action="store_true")
    nxt.set_defaults(handler=command_next)

    review = commands.add_parser("review-template", help="Pre-fill the visual review with every prompt clause.")
    review.add_argument("--request-dir", required=True)
    review.add_argument("--attempt-id", required=True)
    review.add_argument("--image", required=True)
    review.add_argument("--author", required=True)
    review.add_argument("--output", required=True)
    review.set_defaults(handler=command_review_template)

    record = commands.add_parser("record", help="record-generation plus the matching guard checkpoint for one stage.")
    record.add_argument("--workspace", default=".")
    record.add_argument("--style-name", required=True)
    record.add_argument("--request-id", required=True)
    record.add_argument("--request-dir", required=True)
    record.add_argument("--stage", required=True, choices=STAGES)
    record.add_argument("--image", required=True)
    record.add_argument("--attempt-id", required=True)
    record.add_argument("--visual-review-json", required=True)
    record.add_argument("--qa-json", required=True)
    record.add_argument("--description", required=True)
    record.add_argument("--fidelity", type=int, default=90)
    record.set_defaults(handler=command_record)

    canvas = commands.add_parser("fit-canvas", help="Measure figure height on a plain backdrop and extend the canvas if needed.")
    canvas.add_argument("--image", required=True)
    canvas.add_argument("--output", default="")
    canvas.add_argument("--min-percent", type=float, default=72)
    canvas.add_argument("--max-percent", type=float, default=88)
    canvas.add_argument("--target-percent", type=float, default=82)
    canvas.add_argument("--threshold", type=int, default=24)
    canvas.set_defaults(handler=command_fit_canvas)

    sheet = commands.add_parser("sheet", help="Build a preview sheet of the five completed stages.")
    sheet.add_argument("--request-dir", required=True)
    sheet.add_argument("--panel-height", type=int, default=900)
    sheet.set_defaults(handler=command_sheet)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
        return 0
    except (CharacterBaseError, OSError, ValueError) as error:
        print(f"ERROR={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
