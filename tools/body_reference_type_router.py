"""Resolve user mentions to StoryArt's explicit body-reference types."""

from __future__ import annotations

import argparse
import csv
import json
import re
import hashlib
from pathlib import Path
from typing import Any

try:
    from tools.reference_compatibility import explicit_subject_label, validate_reference_compatibility
except ModuleNotFoundError:
    from reference_compatibility import explicit_subject_label, validate_reference_compatibility


WORKSPACE = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = WORKSPACE / "config" / "body_reference_types.json"


class BodyReferenceTypeError(RuntimeError):
    pass


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    if not path.is_file():
        raise BodyReferenceTypeError(f"Body-reference type registry is missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("types"), dict):
        raise BodyReferenceTypeError("Unsupported body-reference type registry schema.")
    required = {"NATURAL", "SKETCH", "OLCHAS"}
    present = set(data["types"])
    if not required.issubset(present) or present - required not in (set(), {"POSE_ATLAS"}):
        raise BodyReferenceTypeError(f"Registry must define {sorted(required)} and may define optional POSE_ATLAS; got {sorted(present)}")
    return data


def active_auxiliary_type(registry: dict[str, Any]) -> str:
    """Legacy registries without a selector retain NATURAL fixture behavior."""
    type_id = str(registry.get("active_auxiliary_type", "NATURAL")).upper()
    if type_id not in registry["types"]:
        raise BodyReferenceTypeError(f"Configured active auxiliary type is not registered: {type_id}")
    return type_id


def type_enabled(spec: dict[str, Any]) -> bool:
    """Missing enabled flags preserve compatibility with older fixture registries."""
    return spec.get("enabled", True) is not False


def normalized(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-яё_]+", " ", text.lower()).split())


def alias_position(text: str, alias: str) -> int | None:
    haystack = f" {normalized(text)} "
    needle = normalized(alias)
    match = re.search(rf"(?<![0-9a-zа-яё_]){re.escape(needle)}(?![0-9a-zа-яё_])", haystack)
    return match.start() if match else None


def resolve_types(text: str, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_registry()
    found: list[tuple[int, str, str]] = []
    disabled_found: list[tuple[int, str, str]] = []
    for type_id, spec in registry["types"].items():
        matches = [(position, alias) for alias in spec["aliases"] if (position := alias_position(text, alias)) is not None]
        if matches:
            position, alias = min(matches)
            (found if type_enabled(spec) else disabled_found).append((position, type_id, alias))
    found.sort(key=lambda item: item[0])
    disabled_found.sort(key=lambda item: item[0])
    selected_ids = [item[1] for item in found]
    selected = []
    for _, type_id, alias in found:
        spec = registry["types"][type_id]
        selected.append({
            "type": type_id,
            "display_name_ru": spec["display_name_ru"],
            "matched_alias": alias,
            "body_focus": spec["body_focus"],
            "authority": spec["authority"],
            "allowed_roles": spec["allowed_roles"],
            "forbidden_roles": spec["forbidden_roles"],
            "root": spec["root"],
            "manifest": spec["manifest"],
        })
    return {
        "schema_version": 1,
        "status": "RESOLVED" if selected else registry["default_when_unmentioned"],
        "original_text": text,
        "selected_types": selected_ids,
        "disabled_requested_types": [item[1] for item in disabled_found],
        "focus_order": selected,
        "permanent_body_geometry_authority": (
            "NATURAL"
            if "NATURAL" in selected_ids
            else "APPROVED_CHARACTER_BODY_OR_COMPLETE_PROMPT_BODY_SPEC"
        ),
        "combination_policy": registry["combination_policy"],
        "requires_user_choice": not selected,
        "active_auxiliary_type": active_auxiliary_type(registry),
    }


def count_manifest_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return sum(1 for _ in csv.DictReader(stream))


def reference_context(type_id: str, target: dict[str, Any], workspace: Path,
                      registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Filter metadata before returning any source, preview or contact-sheet path."""
    registry = registry or load_registry()
    spec = registry["types"][type_id]
    if not type_enabled(spec):
        raise BodyReferenceTypeError(f"Body-reference type {type_id} is disabled: {spec.get('retirement_reason', 'retired')}")
    if type_id == "POSE_ATLAS":
        anatomy = str(target.get("target_anatomy", "UNKNOWN")).upper()
        evidence = str(target.get("anatomy_evidence_source", "UNKNOWN")).strip()
        try:
            from tools.pose_reference_manager import ALL_SOURCE_IMAGES
        except ModuleNotFoundError:
            from pose_reference_manager import ALL_SOURCE_IMAGES
        library_root = (workspace.resolve() / spec["root"]).resolve()
        group_path = (workspace.resolve() / spec["group_manifest"]).resolve()
        if not library_root.is_relative_to(workspace.resolve()):
            raise BodyReferenceTypeError("POSE_ATLAS library root escapes the workspace.")
        if not group_path.is_relative_to(library_root) or not group_path.is_file():
            raise BodyReferenceTypeError("POSE_ATLAS group manifest is missing or outside its library root.")
        group_data = json.loads(group_path.read_text(encoding="utf-8"))
        all_source_images = str(group_data.get("selection_policy", "")).upper() == ALL_SOURCE_IMAGES
        if (not all_source_images and
                (anatomy not in {"MALE_ANATOMY", "FEMALE_ANATOMY"} or not evidence or evidence.upper() == "UNKNOWN")):
            raise BodyReferenceTypeError("POSE_ATLAS requires concrete target anatomy and its evidence before group filtering.")
        try:
            from tools.pose_reference_manager import reference_context as pose_reference_context
        except ModuleNotFoundError:
            from pose_reference_manager import reference_context as pose_reference_context
        for group in group_data.get("groups", []):
            for view in group.get("views", []):
                relative = str(view.get("relative_path", ""))
                if relative and not (library_root / relative).resolve().is_relative_to(library_root):
                    raise BodyReferenceTypeError("POSE_ATLAS source path escapes its library root.")
        grouped = pose_reference_context(
            library_root, anatomy if anatomy in {"MALE_ANATOMY", "FEMALE_ANATOMY"} else "ANY", query=str(target.get("query", "")),
            group_id_filter=str(target.get("group_id", "")), angle=str(target.get("angle", ""))
        )
        grouped["type"] = type_id
        grouped["active_auxiliary_type"] = active_auxiliary_type(registry)
        grouped["status"] = "BODY_REFERENCE_CONTEXT_COMPLETE" if grouped.get("view_count", 0) else "NO_COMPATIBLE_REFERENCES"
        return grouped
    with (workspace / spec["manifest"]).open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    selected = []
    for row in rows:
        value = row.get("generator_path") or row.get("curated_path") or row.get("final_path")
        if not value:
            continue
        source = Path(value)
        if not source.is_absolute():
            source = (workspace / spec["root"] if row.get("generator_path") else workspace) / source
        if not source.is_file():
            continue
        metadata = {**row, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        if row.get("visual_qa_status") == "PASS" and row.get("physiology_group"):
            metadata["subject_label_sha256"] = row.get("output_sha256", "UNKNOWN")
        if validate_reference_compatibility(target, metadata, ["POSE_SOFT"])["compatible"]:
            selected.append(row)
    return {"type": type_id, "status": "READY" if selected else "NO_COMPATIBLE_REFERENCES",
            "references": selected, "filtered_count": len(rows) - len(selected)}


def validate_registry(registry: dict[str, Any], workspace: Path) -> dict[str, Any]:
    results = []
    for type_id, spec in registry["types"].items():
        if not type_enabled(spec):
            results.append({"type": type_id, "status": "DISABLED", "retirement_reason": spec.get("retirement_reason", "")})
            continue
        root = (workspace / spec["root"]).resolve()
        manifest = (workspace / spec["manifest"]).resolve()
        if not root.is_relative_to(workspace.resolve()) or not manifest.is_relative_to(workspace.resolve()):
            raise BodyReferenceTypeError(f"{type_id} paths must remain inside workspace.")
        if not root.is_dir():
            raise BodyReferenceTypeError(f"{type_id} root is missing: {root}")
        if not manifest.is_file():
            raise BodyReferenceTypeError(f"{type_id} manifest is missing: {manifest}")
        if type_id == "POSE_ATLAS":
            group_manifest = (workspace / spec["group_manifest"]).resolve()
            if not group_manifest.is_relative_to(workspace.resolve()) or not group_manifest.is_file():
                raise BodyReferenceTypeError(f"POSE_ATLAS group manifest is missing or outside workspace: {group_manifest}")
            try:
                from tools.pose_reference_manager import validate_library
            except ModuleNotFoundError:
                from pose_reference_manager import validate_library
            pose_result = validate_library(root)
            pose_result["catalog_status"] = pose_result.get("status")
            pose_result["status"] = "READY" if pose_result.get("selection_available_views", pose_result.get("eligible_views", 0)) else "NO_ELIGIBLE_VIEWS"
            results.append({"type": type_id, "root": str(root), "manifest": str(manifest), **pose_result})
            continue
        with manifest.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        labeled = 0
        for row in rows:
            value = row.get("generator_path") or row.get("curated_path") or row.get("final_path")
            if not value:
                continue
            source = Path(value)
            if not source.is_absolute():
                source = (workspace / spec["root"] if row.get("generator_path") else workspace) / source
            if not source.is_file():
                continue
            metadata = {**row, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
            if row.get("visual_qa_status") == "PASS" and row.get("physiology_group"):
                metadata["subject_label_sha256"] = row.get("output_sha256", "UNKNOWN")
            labeled += explicit_subject_label(metadata) != "UNKNOWN"
        results.append({
            "type": type_id,
            "root": str(root),
            "manifest": str(manifest),
            "manifest_rows": count_manifest_rows(manifest),
            "explicitly_labeled_rows": labeled,
            "unclassified_rows": len(rows) - labeled,
            "status": "READY" if labeled == len(rows) else "QUARANTINED_REFERENCES",
        })
    return {"status": "VALID" if all(row["status"] in {"READY", "DISABLED"} for row in results) else "QUARANTINED_REFERENCES", "types": results}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list-types")
    list_parser.add_argument("--json", action="store_true")
    resolve_parser = subparsers.add_parser("resolve")
    resolve_parser.add_argument("--text", required=True)
    resolve_parser.add_argument("--json", action="store_true")
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--json", action="store_true")
    context_parser = subparsers.add_parser("context")
    context_parser.add_argument("--type", choices=["NATURAL", "SKETCH", "OLCHAS", "POSE_ATLAS"], default=None)
    context_parser.add_argument("--target-anatomy", choices=["MALE_ANATOMY", "FEMALE_ANATOMY"], required=True)
    context_parser.add_argument("--target-evidence", required=True)
    context_parser.add_argument("--query", default="")
    context_parser.add_argument("--group-id", default="")
    context_parser.add_argument("--angle", default="")
    context_parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    registry = load_registry(args.registry)
    if args.command == "list-types":
        result = {"types": {key: spec for key, spec in registry["types"].items() if type_enabled(spec)},
                  "disabled_types": {key: spec.get("retirement_reason", "") for key, spec in registry["types"].items() if not type_enabled(spec)},
                  "active_auxiliary_type": active_auxiliary_type(registry),
                  "combination_policy": registry["combination_policy"]}
    elif args.command == "resolve":
        result = resolve_types(args.text, registry)
    elif args.command == "context":
        type_id = args.type or active_auxiliary_type(registry)
        result = reference_context(type_id, {"target_anatomy": args.target_anatomy,
                                  "anatomy_evidence_source": args.target_evidence,
                                  "query": args.query, "group_id": args.group_id, "angle": args.angle}, args.workspace, registry)
    else:
        result = validate_registry(registry, args.workspace.resolve())
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if args.command == "context":
            result["active_auxiliary_type"] = active_auxiliary_type(registry)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "resolve":
            print(f"STATUS={result['status']}")
            print("BODY_REFERENCE_TYPES=" + ",".join(result["selected_types"]))
            print("BODY_GEOMETRY_AUTHORITY=" + result["permanent_body_geometry_authority"])
        elif args.command == "validate":
            print(f"STATUS={result['status']}")
            for item in result["types"]:
                print(f"{item['type']}={item['status']} ROWS={item['manifest_rows']}")
        else:
            for type_id, spec in result["types"].items():
                print(f"{type_id}: {spec['display_name_ru']} -> {spec['body_focus']}")
            for type_id, reason in result.get("disabled_types", {}).items():
                print(f"{type_id}: DISABLED ({reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
