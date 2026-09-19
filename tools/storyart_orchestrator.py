#!/usr/bin/env python3
"""Project-local orchestration state and style-skill adapters for StoryArt."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STYLE_SKILLS_ROOT = PROJECT_ROOT / ".agents" / "style-skills"
STATE_SCHEMA_VERSION = 1
STYLE_ADAPTER_SCHEMA_VERSION = 1
HANDOFF_STATUSES = {"PENDING", "DONE", "REJECTED", "BLOCKED"}
COMPLETION_STATUSES = HANDOFF_STATUSES - {"PENDING"}
WRITER_ROLES = {"GENERATOR_OPERATOR", "REGISTRAR"}
READ_ONLY_ROLES = {
    "STYLE_LIBRARIAN",
    "IDENTITY_CURATOR",
    "CALL_PLANNER",
    "VISUAL_QA",
    "ESCALATION_ORCHESTRATOR",
}
ROLE_DESCRIPTIONS = {
    "STYLE_LIBRARIAN": "Inspect the complete style pack and propose minimal references.",
    "IDENTITY_CURATOR": "Inspect canonical character identity and select authoritative sources.",
    "CALL_PLANNER": "Return one validated prompt and exact attachment list.",
    "GENERATOR_OPERATOR": "Execute one declared generator call without research or prompt drift.",
    "VISUAL_QA": "Independently score every required semantic QA layer.",
    "REGISTRAR": "Record and store a finalized result through existing StoryArt managers.",
    "ESCALATION_ORCHESTRATOR": "Exceptional read-only escalation: return one bounded Terra/Luna work order from corrected repeated-failure evidence.",
}
FORBIDDEN_INFRASTRUCTURE_ROOTS = {
    "tools",
    "tests",
    "docs",
    "skills",
    "scripts",
    ".agents",
}
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
}


class OrchestratorError(RuntimeError):
    """Raised when an orchestration contract is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OrchestratorError(f"Required file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise OrchestratorError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise OrchestratorError(f"Expected a JSON object in {path}.")
    return data


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def ensure_inside_project(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise OrchestratorError(
            f"Project-local orchestration cannot access paths outside {PROJECT_ROOT}: {path}"
        ) from exc
    return resolved


def normalize_project_path(raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return ensure_inside_project(candidate)


def relative_project_path(path: Path) -> str:
    return str(ensure_inside_project(path).relative_to(PROJECT_ROOT))


def require_active_guard(guard_path: Path) -> dict[str, Any]:
    guard = load_json(guard_path)
    status = str(guard.get("status", ""))
    if status not in {"ACTIVE", "WAITING_FOR_USER"}:
        raise OrchestratorError(
            f"Orchestration requires an active guard; current guard status is {status or 'missing'}."
        )
    for key in ("request_id", "goal_lock", "primary_deliverable"):
        if not str(guard.get(key, "")).strip():
            raise OrchestratorError(f"Guard is missing required field: {key}")
    return guard


def initialize_state(state_path: Path, guard_path: Path) -> dict[str, Any]:
    state_path = ensure_inside_project(state_path)
    guard_path = ensure_inside_project(guard_path)
    if state_path.exists():
        raise OrchestratorError(f"Orchestration state already exists: {state_path}")
    if state_path.parent != guard_path.parent:
        raise OrchestratorError(
            "ORCHESTRATION_STATE.json must be stored beside EXECUTION_GUARD.json."
        )
    guard = require_active_guard(guard_path)
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "request_id": str(guard["request_id"]),
        "goal_lock": str(guard["goal_lock"]),
        "primary_deliverable": str(guard["primary_deliverable"]),
        "guard_path": relative_project_path(guard_path),
        "request_root": relative_project_path(state_path.parent),
        "status": "ACTIVE",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "roles": ROLE_DESCRIPTIONS,
        "handoffs": [],
    }
    save_json(state_path, state)
    return state


def load_state(state_path: Path) -> dict[str, Any]:
    state_path = ensure_inside_project(state_path)
    state = load_json(state_path)
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        raise OrchestratorError(
            f"Unsupported orchestration schema: {state.get('schema_version')}"
        )
    guard_path = normalize_project_path(str(state.get("guard_path", "")))
    require_active_guard(guard_path)
    return state


def handoff_by_id(state: dict[str, Any], handoff_id: str) -> dict[str, Any]:
    for handoff in state.get("handoffs", []):
        if handoff.get("handoff_id") == handoff_id:
            return handoff
    raise OrchestratorError(f"Unknown handoff id: {handoff_id}")


def validate_dependencies(state: dict[str, Any], dependency_ids: Iterable[str]) -> None:
    for dependency_id in dependency_ids:
        dependency = handoff_by_id(state, dependency_id)
        if dependency.get("status") != "DONE":
            raise OrchestratorError(
                f"Dependency {dependency_id} is {dependency.get('status')}; DONE is required."
            )


def validate_writer_paths(
    role: str,
    request_root: Path,
    write_paths: list[Path],
) -> None:
    if role in READ_ONLY_ROLES and write_paths:
        raise OrchestratorError(f"{role} is read-only and cannot receive write paths.")
    if role in WRITER_ROLES and not write_paths:
        raise OrchestratorError(f"{role} requires at least one explicit write path.")

    generation_results = (PROJECT_ROOT / "GENERATION_RESULTS").resolve()
    for path in write_paths:
        relative = path.relative_to(PROJECT_ROOT)
        first = relative.parts[0] if relative.parts else ""
        if first in FORBIDDEN_INFRASTRUCTURE_ROOTS:
            raise OrchestratorError(
                f"{role} cannot modify project infrastructure: {relative}"
            )
        if role == "GENERATOR_OPERATOR":
            inside_request = path == request_root or request_root in path.parents
            inside_archive = (
                path == generation_results or generation_results in path.parents
            )
            if not inside_request and not inside_archive:
                raise OrchestratorError(
                    "GENERATOR_OPERATOR may write only inside the active request "
                    "or GENERATION_RESULTS."
                )
        if role == "REGISTRAR":
            inside_request = path == request_root or request_root in path.parents
            inside_archive = (
                path == generation_results or generation_results in path.parents
            )
            inside_generations = bool(relative.parts) and relative.parts[0].endswith(
                "_GENERATIONS"
            )
            if not inside_request and not inside_archive and not inside_generations:
                raise OrchestratorError(
                    "REGISTRAR may write only inside the active request, "
                    "GENERATION_RESULTS, or a selected *_GENERATIONS destination."
                )


def dispatch_handoff(
    state_path: Path,
    role: str,
    objective: str,
    input_paths: list[str],
    write_paths: list[str],
    dependency_ids: list[str],
    stage: str,
    output_contract: str,
    qa_layer: str = "",
) -> dict[str, Any]:
    state = load_state(state_path)
    if role not in ROLE_DESCRIPTIONS:
        raise OrchestratorError(f"Unknown role: {role}")
    if not objective.strip():
        raise OrchestratorError("A bounded handoff objective is required.")
    validate_dependencies(state, dependency_ids)

    normalized_inputs = [normalize_project_path(item) for item in input_paths]
    for path in normalized_inputs:
        if not path.exists():
            raise OrchestratorError(f"Handoff input does not exist: {path}")
    normalized_writes = [normalize_project_path(item) for item in write_paths]
    request_root = normalize_project_path(str(state["request_root"]))
    validate_writer_paths(role, request_root, normalized_writes)

    sequence = len(state.get("handoffs", [])) + 1
    handoff_id = f"H{sequence:03d}"
    execution_profile: dict[str, str] = {}
    if role == "ESCALATION_ORCHESTRATOR":
        if not stage.strip() or not qa_layer.strip():
            raise OrchestratorError("ESCALATION_ORCHESTRATOR requires --stage and --qa-layer.")
        guard_path = normalize_project_path(str(state["guard_path"]))
        lock_path = guard_path.with_suffix(guard_path.suffix + ".escalation.lock")
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise OrchestratorError("ESCALATION_ORCHESTRATOR incident is already being consumed.") from exc
        try:
            guard = load_json(guard_path)
            incident = next(
                (
                    item for item in guard.get("escalation_incidents", [])
                    if item.get("status") == "REQUIRED"
                    and str(item.get("stage", "")).casefold() == stage.casefold()
                    and str(item.get("qa_layer", "")).casefold() == qa_layer.casefold()
                ),
                None,
            )
            if guard.get("next_required_action") != "ESCALATION_ORCHESTRATOR_REQUIRED" or incident is None:
                raise OrchestratorError("ESCALATION_ORCHESTRATOR is available only for the guard's due corrected repeated-failure incident.")
            incident.update({"status": "CONSUMED", "consumed_at": utc_now(), "handoff_id": handoff_id})
            save_json(guard_path, guard)
        finally:
            os.close(lock_fd)
            lock_path.unlink(missing_ok=True)
        execution_profile = {"model": "gpt-6-astra", "reasoning_effort": "low", "mode": "EXCEPTIONAL_ERROR_HANDLING"}

    if role == "GENERATOR_OPERATOR":
        if not stage.strip():
            raise OrchestratorError("GENERATOR_OPERATOR requires --stage.")
        if output_contract != "REQUESTED_DELIVERABLE":
            raise OrchestratorError(
                "GENERATOR_OPERATOR requires --output-contract REQUESTED_DELIVERABLE."
            )
    elif output_contract:
        raise OrchestratorError("--output-contract is valid only for GENERATOR_OPERATOR.")

    relative_inputs = [relative_project_path(path) for path in normalized_inputs]
    relative_writes = [relative_project_path(path) for path in normalized_writes]
    input_summary = ", ".join(relative_inputs) if relative_inputs else "NONE"
    write_summary = ", ".join(relative_writes) if relative_writes else "NONE (read-only)"
    stage_summary = stage.strip() or "NONE"
    handoff = {
        "handoff_id": handoff_id,
        "role": role,
        "description": ROLE_DESCRIPTIONS[role],
        "objective": objective.strip(),
        "stage": stage.strip(),
        "input_paths": relative_inputs,
        "allowed_write_paths": relative_writes,
        "depends_on": dependency_ids,
        "output_contract": output_contract,
        "qa_layer": qa_layer.strip().upper(),
        "execution_profile": execution_profile,
        "status": "PENDING",
        "created_at": utc_now(),
        "agent_prompt": (
            f"Act only as StoryArt role {role}. Objective: {objective.strip()} "
            f"Stage: {stage_summary}. Inputs: {input_summary}. "
            f"Allowed writes: {write_summary}. "
            "Do not use unlisted inputs, expand scope, change project infrastructure, "
            "communicate with the user, or edit orchestration state. Return a concise result "
            "with evidence."
            + (
                " This is a one-shot exceptional escalation only: do not use tools, generate, test, edit, QA, approve, or spawn agents. Return one evidence-based bounded work order for a Terra or Luna executor; the root dispatches it."
                if role == "ESCALATION_ORCHESTRATOR" else ""
            )
        ),
    }
    handoff_dir = state_path.parent / "ORCHESTRATION_HANDOFFS"
    handoff_path = handoff_dir / f"{handoff_id}_{role.lower()}.json"
    save_json(handoff_path, handoff)

    state["handoffs"].append(
        {
            "handoff_id": handoff_id,
            "role": role,
            "stage": stage.strip(),
            "status": "PENDING",
            "path": relative_project_path(handoff_path),
        }
    )
    state["updated_at"] = utc_now()
    save_json(state_path, state)
    return handoff


def complete_handoff(
    state_path: Path,
    handoff_id: str,
    status: str,
    result: str,
    evidence: list[str],
) -> dict[str, Any]:
    state = load_state(state_path)
    if status not in COMPLETION_STATUSES:
        raise OrchestratorError(
            f"Completion status must be one of: {', '.join(sorted(COMPLETION_STATUSES))}"
        )
    summary = handoff_by_id(state, handoff_id)
    if summary.get("status") != "PENDING":
        raise OrchestratorError(
            f"Handoff {handoff_id} is already {summary.get('status')}."
        )
    if not result.strip():
        raise OrchestratorError("A concise handoff result is required.")

    evidence_paths = [normalize_project_path(item) for item in evidence]
    for path in evidence_paths:
        if not path.exists():
            raise OrchestratorError(f"Handoff evidence does not exist: {path}")

    handoff_path = normalize_project_path(str(summary["path"]))
    handoff = load_json(handoff_path)
    handoff.update(
        {
            "status": status,
            "result": result.strip(),
            "evidence": [relative_project_path(path) for path in evidence_paths],
            "completed_at": utc_now(),
        }
    )
    save_json(handoff_path, handoff)
    summary["status"] = status
    state["updated_at"] = utc_now()
    save_json(state_path, state)
    return handoff


def style_skill_name(style_slug: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", style_slug.lower()).strip("-")
    return f"storyart-style-{normalized}"


def query_ready_styles() -> list[dict[str, Any]]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "style_pack_manager.py"),
        "list-styles",
        "--json",
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise OrchestratorError(
            "style_pack_manager.py list-styles failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    try:
        styles = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise OrchestratorError("list-styles returned invalid JSON.") from exc
    if not isinstance(styles, list):
        raise OrchestratorError("list-styles must return a JSON array.")
    return [
        item
        for item in styles
        if isinstance(item, dict)
        and item.get("local_readiness") == "READY"
        and item.get("can_generate") is True
    ]


def adapter_skill_markdown(style: dict[str, Any]) -> str:
    style_name = str(style["style_name"])
    skill_name = style_skill_name(str(style["slug"]))
    pack_name = Path(str(style["pack_path"])).name
    generations_name = Path(str(style["generations_path"])).name
    return f"""---
name: {skill_name}
description: Provide project-local routing context for the ready StoryArt style {style_name}. Use only after the user selects this style inside StoryArt; always refresh live style context and inspect real local images before choosing references.
---

# {style_name} style adapter

Treat `{pack_name}` as the complete local visual source of truth and
`{generations_name}` as its generated-work sibling.

1. Run `python tools\\style_pack_manager.py style-context --style-name "{style_name}" --json`.
2. Query complete file lists for every visually critical role.
3. Inspect actual full-resolution candidates before selecting references.
4. Use the smallest compatible set that satisfies the active `REFERENCE_PLAN.json`.
5. Keep this adapter read-only. Never copy images into the skill or treat its snapshot metadata
   as fresher than the live pack.

Read `references/style.json` only for routing metadata. Follow the central StoryArt workflow for
all fidelity, calibration, identity, risk, generation, QA, approval, and storage decisions.
"""


def adapter_openai_yaml(style: dict[str, Any]) -> str:
    style_name = str(style["style_name"])
    skill_name = style_skill_name(str(style["slug"]))
    return f"""interface:
  display_name: "StoryArt Style: {style_name}"
  short_description: "Локальный адаптер стиля {style_name}"
  default_prompt: "Use ${skill_name} to load the current local StoryArt style context."
policy:
  allow_implicit_invocation: false
"""


def build_style_skills(output_root: Path) -> dict[str, Any]:
    output_root = ensure_inside_project(output_root)
    styles = query_ready_styles()
    entries: list[dict[str, Any]] = []
    for style in styles:
        skill_name = style_skill_name(str(style["slug"]))
        skill_root = output_root / skill_name
        references_root = skill_root / "references"
        agents_root = skill_root / "agents"
        references_root.mkdir(parents=True, exist_ok=True)
        agents_root.mkdir(parents=True, exist_ok=True)
        (skill_root / "SKILL.md").write_text(
            adapter_skill_markdown(style),
            encoding="utf-8",
        )
        (agents_root / "openai.yaml").write_text(
            adapter_openai_yaml(style),
            encoding="utf-8",
        )
        snapshot = {
            "schema_version": STYLE_ADAPTER_SCHEMA_VERSION,
            "generated_at": utc_now(),
            "style_name": style["style_name"],
            "slug": style["slug"],
            "pack_path": relative_project_path(Path(str(style["pack_path"]))),
            "generations_path": relative_project_path(
                Path(str(style["generations_path"]))
            ),
            "management": style.get("management"),
            "local_readiness": style.get("local_readiness"),
            "source_images": style.get("source_images"),
            "work_images": style.get("work_images"),
            "characters": style.get("characters"),
        }
        save_json(references_root / "style.json", snapshot)
        entries.append(
            {
                "style_name": style["style_name"],
                "skill_name": skill_name,
                "path": relative_project_path(skill_root),
                "pack_path": relative_project_path(Path(str(style["pack_path"]))),
                "local_readiness": style["local_readiness"],
                "can_generate": style["can_generate"],
            }
        )
    index = {
        "schema_version": STYLE_ADAPTER_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "source": "tools/style_pack_manager.py list-styles --json",
        "styles": entries,
    }
    save_json(output_root / "index.json", index)
    return index


def validate_style_skills(output_root: Path) -> dict[str, Any]:
    output_root = ensure_inside_project(output_root)
    index = load_json(output_root / "index.json")
    errors: list[str] = []
    for entry in index.get("styles", []):
        skill_root = normalize_project_path(str(entry.get("path", "")))
        skill_file = skill_root / "SKILL.md"
        metadata_file = skill_root / "references" / "style.json"
        if not skill_file.is_file():
            errors.append(f"Missing SKILL.md: {skill_file}")
            continue
        image_files = [
            path
            for path in skill_root.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]
        if image_files:
            errors.append(
                f"Style adapter must not contain images: "
                + ", ".join(str(path) for path in image_files)
            )
        text = skill_file.read_text(encoding="utf-8")
        expected_name = str(entry.get("skill_name", ""))
        if f"name: {expected_name}" not in text:
            errors.append(f"Wrong skill name in {skill_file}")
        try:
            metadata = load_json(metadata_file)
            pack_path = normalize_project_path(str(metadata.get("pack_path", "")))
            if not pack_path.is_dir():
                errors.append(f"Missing style pack for {expected_name}: {pack_path}")
        except OrchestratorError as exc:
            errors.append(str(exc))
    result = {
        "status": "VALID" if not errors else "INVALID",
        "styles": len(index.get("styles", [])),
        "errors": errors,
    }
    if errors:
        raise OrchestratorError(json.dumps(result, ensure_ascii=False))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Keep StoryArt subagent handoffs isolated and project-local."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize request orchestration state.")
    init_parser.add_argument("--state", required=True)
    init_parser.add_argument("--guard", required=True)

    dispatch_parser = subparsers.add_parser("dispatch", help="Create a bounded role handoff.")
    dispatch_parser.add_argument("--state", required=True)
    dispatch_parser.add_argument("--role", required=True, choices=sorted(ROLE_DESCRIPTIONS))
    dispatch_parser.add_argument("--objective", required=True)
    dispatch_parser.add_argument("--input", action="append", default=[])
    dispatch_parser.add_argument("--allowed-write", action="append", default=[])
    dispatch_parser.add_argument("--depends-on", action="append", default=[])
    dispatch_parser.add_argument("--stage", default="")
    dispatch_parser.add_argument("--qa-layer", default="")
    dispatch_parser.add_argument(
        "--output-contract",
        choices=["REQUESTED_DELIVERABLE"],
        default="",
    )

    complete_parser = subparsers.add_parser(
        "complete-handoff", help="Record a subagent result."
    )
    complete_parser.add_argument("--state", required=True)
    complete_parser.add_argument("--handoff-id", required=True)
    complete_parser.add_argument(
        "--status", required=True, choices=sorted(COMPLETION_STATUSES)
    )
    complete_parser.add_argument("--result", required=True)
    complete_parser.add_argument("--evidence", action="append", default=[])

    status_parser = subparsers.add_parser("status", help="Show current orchestration state.")
    status_parser.add_argument("--state", required=True)

    build_parser_ = subparsers.add_parser(
        "build-style-skills", help="Refresh local style adapters from ready packs."
    )
    build_parser_.add_argument("--output", default=str(DEFAULT_STYLE_SKILLS_ROOT))

    validate_parser = subparsers.add_parser(
        "validate-style-skills", help="Validate local style adapters."
    )
    validate_parser.add_argument("--output", default=str(DEFAULT_STYLE_SKILLS_ROOT))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_state(
                normalize_project_path(args.state),
                normalize_project_path(args.guard),
            )
        elif args.command == "dispatch":
            result = dispatch_handoff(
                normalize_project_path(args.state),
                args.role,
                args.objective,
                args.input,
                args.allowed_write,
                args.depends_on,
                args.stage,
                args.output_contract,
                args.qa_layer,
            )
        elif args.command == "complete-handoff":
            result = complete_handoff(
                normalize_project_path(args.state),
                args.handoff_id,
                args.status,
                args.result,
                args.evidence,
            )
        elif args.command == "status":
            result = load_state(normalize_project_path(args.state))
        elif args.command == "build-style-skills":
            result = build_style_skills(normalize_project_path(args.output))
        elif args.command == "validate-style-skills":
            result = validate_style_skills(normalize_project_path(args.output))
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except OrchestratorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
