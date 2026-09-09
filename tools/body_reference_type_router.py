"""Resolve user mentions to StoryArt's explicit body-reference types."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


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
    if present != required:
        raise BodyReferenceTypeError(f"Registry must define exactly {sorted(required)}; got {sorted(present)}")
    return data


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
    for type_id, spec in registry["types"].items():
        matches = [(position, alias) for alias in spec["aliases"] if (position := alias_position(text, alias)) is not None]
        if matches:
            position, alias = min(matches)
            found.append((position, type_id, alias))
    found.sort(key=lambda item: item[0])
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
        "focus_order": selected,
        "permanent_body_geometry_authority": (
            "NATURAL"
            if "NATURAL" in selected_ids
            else "APPROVED_CHARACTER_BODY_OR_COMPLETE_PROMPT_BODY_SPEC"
        ),
        "combination_policy": registry["combination_policy"],
        "requires_user_choice": not selected,
    }


def count_manifest_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return sum(1 for _ in csv.DictReader(stream))


def validate_registry(registry: dict[str, Any], workspace: Path) -> dict[str, Any]:
    results = []
    for type_id, spec in registry["types"].items():
        root = (workspace / spec["root"]).resolve()
        manifest = (workspace / spec["manifest"]).resolve()
        if not root.is_dir():
            raise BodyReferenceTypeError(f"{type_id} root is missing: {root}")
        if not manifest.is_file():
            raise BodyReferenceTypeError(f"{type_id} manifest is missing: {manifest}")
        results.append({
            "type": type_id,
            "root": str(root),
            "manifest": str(manifest),
            "manifest_rows": count_manifest_rows(manifest),
            "status": "READY",
        })
    return {"status": "VALID", "types": results}


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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    registry = load_registry(args.registry)
    if args.command == "list-types":
        result = {"types": registry["types"], "combination_policy": registry["combination_policy"]}
    elif args.command == "resolve":
        result = resolve_types(args.text, registry)
    else:
        result = validate_registry(registry, args.workspace.resolve())
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if args.command == "resolve":
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
