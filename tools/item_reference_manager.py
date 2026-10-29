"""Typed, approval-bound item references for StoryArt.

An item reference is always a pair: a genuinely isolated design view and a
general scale/fit view with the exact approved character. The registry records
both source hashes and never turns a role asset into canonical identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import style_pack_manager as manager
except ImportError:  # pragma: no cover - script invocation
    import style_pack_manager as manager


KINDS = ("ACCESSORY", "WEAPON", "TOOL", "CLOTHING")
QA_COMMON = ("ITEM_DESIGN", "ITEM_VERSION", "ITEM_DIMENSIONS", "ITEM_MATERIAL_CONSTRUCTION", "ITEM_CHARACTER_SCALE")
QA_CLOTHING = ("CLOTHING_FIT", "LAYER_ORDER", "CONTACT_CONSTRAINTS")


class ItemReferenceError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ItemReferenceError(f"Cannot read item-reference JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ItemReferenceError(f"Expected a JSON object: {path}")
    return value


def _registered_character(workspace: Path, style_name: str, character_id: str) -> tuple[manager.StylePaths, Path, Path]:
    paths = manager.make_paths(workspace, style_name)
    matches = [row for row in manager.read_csv(paths.character_registry)
               if row.get("character_id", "").strip().upper() == character_id.upper()
               and row.get("status", "").strip().upper() == "APPROVED"]
    if len(matches) != 1:
        raise ItemReferenceError("Item references require one exact APPROVED character registry entry.")
    row = matches[0]
    profile = Path(row.get("profile_path", ""))
    if not profile.is_absolute():
        profile = paths.generations / profile
    profile = profile.resolve()
    try:
        manager._approved_profile_identity(paths, profile, character_id)
    except (OSError, manager.StylePackError, ValueError) as exc:
        raise ItemReferenceError(f"Canonical character profile is not currently approved: {exc}") from exc
    assembly = Path(row.get("approved_base", ""))
    if not assembly.is_absolute():
        assembly = paths.generations / assembly
    assembly = assembly.resolve()
    base_record = manager.newest_matching_generation(paths, assembly) if assembly.is_file() else None
    if not (base_record and base_record.get("status", "").upper() == "APPROVED_CHARACTER_BASE"
            and base_record.get("character_id", "").upper() == character_id.upper()
            and Path(base_record.get("style_file", "")).resolve() == assembly
            and manager.approved_record_hash_matches_file(base_record, assembly)):
        raise ItemReferenceError("Exact character assembly is not a current approved canonical identity asset.")
    return paths, profile, assembly


def build_item_prompt(plan: dict[str, Any]) -> str:
    kind = str(plan["item_kind"])
    exact_character = str(plan["character_id"])
    parts = [
        f"Create the requested {kind.lower()} reference sheet for {plan['item_name']} version {plan['version']}.",
        "The sheet must contain two clearly separated, full-resolution panels with no overlap:",
        "(1) ITEM_DESIGN: isolated item only on a plain neutral background, no person, hands, mannequin, shadow shaped like a person, or other subject; show the complete item and construction details.",
        f"(2) ITEM_SCALE_FIT: a general view with the exact approved character {exact_character} and the complete item together, at a readable scale.",
        "Keep item identity, silhouette, materials, dimensions, proportions, construction, and version exactly consistent between both panels.",
        "For clothing, show the garment worn by the exact character with fit, layer order, and contact points visible.",
        "For an accessory, weapon, or tool, it may be beside the character; do not force holding or wearing.",
        f"Physical specification: {plan['design_spec']}",
        f"Dimensions/ratios: {plan['dimensions']}",
        f"Material: {plan['material']}; construction: {plan['construction']}; attachment/contact constraints: {plan['attachment_constraints']}",
    ]
    return "\n".join(parts)


def create_plan(args: argparse.Namespace) -> dict[str, Any]:
    item_id = args.item_id.strip().upper()
    if not re.fullmatch(r"ITEM_[A-Z0-9][A-Z0-9_-]{0,63}", item_id):
        raise ItemReferenceError("item-id must match ITEM_<safe identifier>.")
    character_id = args.character_id.strip().upper()
    if not re.fullmatch(r"CHAR_\d+", character_id):
        raise ItemReferenceError("A typed item must bind to an existing canonical CHAR_NNN character.")
    paths, profile, assembly = _registered_character(args.workspace, args.style_name, character_id)
    if not args.request_id.strip():
        raise ItemReferenceError("request-id is required.")
    output = Path(args.output).resolve()
    manager.assert_active_request_path(output)
    if output.exists():
        raise ItemReferenceError(f"Refusing to overwrite an existing item production plan: {output}")
    plan: dict[str, Any] = {
        "schema_version": 1,
        "status": "ITEM_PRODUCTION_PLAN",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request_id": args.request_id.strip(),
        "style_name": paths.style_name,
        "character_id": character_id,
        "character_profile": str(profile),
        "character_assembly": str(assembly),
        "character_assembly_sha256": _sha256(assembly),
        "item_id": item_id,
        "item_kind": args.item_kind,
        "item_name": args.name.strip(),
        "version": args.version.strip(),
        "design_spec": args.design_spec.strip(),
        "dimensions": args.dimensions.strip(),
        "material": args.material.strip(),
        "construction": args.construction.strip(),
        "attachment_constraints": args.attachment_constraints.strip(),
        "required_roles": ["ITEM_DESIGN", "ITEM_SCALE_FIT"],
        "required_qa": list(QA_COMMON + (QA_CLOTHING if args.item_kind == "CLOTHING" else ())),
        "generation_prompt": "",
        "approval_required": True,
    }
    plan["generation_prompt"] = build_item_prompt(plan)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def _validate_qa(plan: dict[str, Any], qa: dict[str, Any], design_hash: str, scale_hash: str,
                 assembly_hash: str) -> dict[str, Any]:
    kind = str(plan.get("item_kind", "")).upper()
    if kind not in KINDS:
        raise ItemReferenceError("Item kind is unknown; required QA cannot be derived safely.")
    required = set(QA_COMMON + (QA_CLOTHING if kind == "CLOTHING" else ()))
    checks = qa.get("checks")
    if not isinstance(checks, dict):
        raise ItemReferenceError("QA JSON must contain a checks object with every required item layer.")
    missing = sorted(required - checks.keys())
    if missing:
        raise ItemReferenceError("Missing item QA layers: " + ", ".join(missing))
    if any(not isinstance(checks[key], dict) or checks[key].get("status") != "PASS" for key in required):
        raise ItemReferenceError("Every required item QA layer must be explicitly PASS.")
    design = qa.get("item_design_source")
    scale = qa.get("item_scale_fit_source")
    if not isinstance(design, dict) or design.get("sha256", "").lower() != design_hash:
        raise ItemReferenceError("ITEM_DESIGN review must bind the isolated design image's exact SHA-256.")
    if design.get("subject_label") != "NO_SUBJECT" or not str(design.get("subject_label_evidence", "")).strip():
        raise ItemReferenceError("Isolated ITEM_DESIGN requires reviewed NO_SUBJECT evidence tied to its exact hash.")
    if not isinstance(scale, dict) or scale.get("sha256", "").lower() != scale_hash:
        raise ItemReferenceError("ITEM_SCALE_FIT review must bind the scale/fit image's exact SHA-256.")
    if scale.get("character_id", "").upper() != plan["character_id"]:
        raise ItemReferenceError("Scale/fit QA must identify the exact canonical character bound by the plan.")
    if scale.get("character_assembly_sha256", "").lower() != assembly_hash:
        raise ItemReferenceError("Scale/fit QA must bind the approved canonical assembly hash.")
    if (scale.get("subject_label") not in {"MALE", "FEMALE"}
            or scale.get("subject_label_sha256", "").lower() != scale_hash
            or not str(scale.get("subject_label_evidence", "")).strip()):
        raise ItemReferenceError("Scale/fit source requires a reviewed matching MALE/FEMALE label bound to its exact bytes.")
    source_paths = [Path(str(source.get("source_sheet_path", ""))).resolve() for source in (design, scale)]
    source_hashes = [str(source.get("source_sheet_sha256", "")).lower() for source in (design, scale)]
    if not source_paths[0].is_file() or source_paths[0] != source_paths[1] or source_hashes[0] != source_hashes[1]:
        raise ItemReferenceError("Both isolated role crops must name the same exact authorized source sheet and hash.")
    if _sha256(source_paths[0]) != source_hashes[0]:
        raise ItemReferenceError("The source sheet changed after the role crops were reviewed.")
    for key in required:
        check = checks[key]
        if not str(check.get("evidence", "")).strip():
            raise ItemReferenceError(f"{key} PASS requires concrete review evidence.")
    return {"checks": checks, "item_design_source": design, "item_scale_fit_source": scale}


def approve(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = Path(args.item_plan).resolve()
    manager.assert_active_request_path(plan_path)
    plan = _read_json(plan_path)
    if plan.get("status") != "ITEM_PRODUCTION_PLAN":
        raise ItemReferenceError("Only an ITEM_PRODUCTION_PLAN can be approved.")
    paths, profile, assembly = _registered_character(args.workspace, str(plan["style_name"]), str(plan["character_id"]))
    if profile != Path(plan["character_profile"]).resolve() or assembly != Path(plan["character_assembly"]).resolve():
        raise ItemReferenceError("The canonical character/profile binding changed after item planning.")
    assembly_hash = _sha256(assembly)
    if assembly_hash != str(plan.get("character_assembly_sha256", "")).lower():
        raise ItemReferenceError("Canonical character assembly changed after item planning.")
    if not args.user_approved or not args.approval_quote.strip():
        raise ItemReferenceError("Author approval requires --user-approved and the exact approval quote.")
    design = Path(args.design_image).resolve()
    scale = Path(args.scale_fit_image).resolve()
    manager.assert_active_request_path(design)
    manager.assert_active_request_path(scale)
    if design == scale or not design.is_file() or not scale.is_file():
        raise ItemReferenceError("ITEM_DESIGN and ITEM_SCALE_FIT must be two separate existing image files.")
    design_hash, scale_hash = _sha256(design), _sha256(scale)
    if design_hash == scale_hash:
        raise ItemReferenceError("Isolated design and character scale/fit sources must have distinct bytes.")
    qa_path = Path(args.qa_json).resolve()
    manager.assert_active_request_path(qa_path)
    qa_input = _read_json(qa_path)
    source_sheet = Path(str(qa_input.get("item_design_source", {}).get("source_sheet_path", ""))).resolve()
    manager.assert_active_request_path(source_sheet)
    qa = _validate_qa(plan, qa_input, design_hash, scale_hash, assembly_hash)
    profile_identity = manager._approved_profile_identity(paths, profile, str(plan["character_id"]))
    expected_label = {
        "MALE_ANATOMY": "MALE",
        "FEMALE_ANATOMY": "FEMALE",
    }.get(str(profile_identity.get("target_anatomy", "UNKNOWN")).upper())
    if not expected_label:
        raise ItemReferenceError("Typed item scale/fit requires a profile with confirmed MALE or FEMALE anatomy.")
    scale_qa = qa["item_scale_fit_source"]
    if (scale_qa.get("subject_label") != expected_label
            or scale_qa.get("anatomy_compatibility") != f"{expected_label}_ANATOMY"
            or not str(scale_qa.get("anatomy_evidence_source", "")).strip()):
        raise ItemReferenceError("Scale/fit review subject label and anatomy must match the approved character profile.")
    folder = (profile.parent / "03_CHARACTER_REFERENCES" / "08_TYPED_ITEMS" /
              manager.safe_component(str(plan["item_id"])) / manager.safe_component(str(plan["version"]))).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if any(folder.iterdir()):
        raise ItemReferenceError(f"Refusing to overwrite an existing item version: {folder}")
    paths_to_store = [(design, folder / "ITEM_DESIGN" / ("source" + design.suffix.lower())),
                      (scale, folder / "ITEM_SCALE_FIT" / ("source" + scale.suffix.lower()))]
    for source, destination in paths_to_store:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    item = {
        "schema_version": 1,
        "status": "APPROVED_TYPED_ITEM",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "style_name": paths.style_name,
        "character_id": plan["character_id"],
        "character_profile": str(profile),
        "character_assembly": str(assembly),
        "character_assembly_sha256": assembly_hash,
        "item_id": plan["item_id"],
        "item_kind": plan["item_kind"],
        "item_name": plan["item_name"],
        "version": plan["version"],
        "design_spec": plan["design_spec"],
        "dimensions": plan["dimensions"],
        "material": plan["material"],
        "construction": plan["construction"],
        "attachment_constraints": plan["attachment_constraints"],
        "item_design": {"path": str(paths_to_store[0][1].resolve()), "sha256": design_hash,
                        "subject_label": "NO_SUBJECT", "subject_label_sha256": design_hash,
                        "subject_label_evidence": qa["item_design_source"]["subject_label_evidence"]},
        "item_scale_fit": {"path": str(paths_to_store[1][1].resolve()), "sha256": scale_hash,
                           "character_id": plan["character_id"], "character_assembly_sha256": assembly_hash},
        "qa": qa,
        "author_approval": {"status": "APPROVED", "quote": args.approval_quote.strip()},
    }
    manifest = folder / "APPROVED_ITEM_REFERENCE.json"
    manifest.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    registry = paths.generations / "TYPED_ITEM_REFERENCE_REGISTRY.jsonl"
    registry.parent.mkdir(parents=True, exist_ok=True)
    row = {"item_id": item["item_id"], "version": item["version"], "character_id": item["character_id"],
           "manifest": str(manifest.resolve()), "manifest_sha256": _sha256(manifest),
           "roles": ["ITEM_DESIGN", "ITEM_SCALE_FIT"], "status": "APPROVED_TYPED_ITEM"}
    with registry.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    item["manifest_path"] = str(manifest.resolve())
    return item


def validate_manifest(workspace: Path, manifest_path: Path, *, style_name: str = "", character_id: str = "") -> dict[str, Any]:
    path = manifest_path.resolve()
    parents = path.parents
    if (path.name != "APPROVED_ITEM_REFERENCE.json" or len(parents) < 7
            or parents[2].name != "08_TYPED_ITEMS" or parents[3].name != "03_CHARACTER_REFERENCES"
            or parents[5].name != "01_APPROVED_CHARACTERS" or not parents[6].name.endswith("_GENERATIONS")):
        raise ItemReferenceError("Typed item manifest path is outside the approved character item library.")
    generations = parents[6]
    registry = generations / "TYPED_ITEM_REFERENCE_REGISTRY.jsonl"
    rows: list[dict[str, Any]] = []
    if registry.is_file():
        for line in registry.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ItemReferenceError("Typed item registry contains invalid JSON.") from exc
                if isinstance(row, dict) and str(row.get("manifest", "")).casefold() == str(path).casefold():
                    rows.append(row)
    if len(rows) != 1 or rows[0].get("status") != "APPROVED_TYPED_ITEM" or _sha256(path) != rows[0].get("manifest_sha256"):
        raise ItemReferenceError("Typed item manifest is not uniquely registered or its approved bytes changed.")
    item = _read_json(path)
    if item.get("status") != "APPROVED_TYPED_ITEM" or item.get("author_approval", {}).get("status") != "APPROVED":
        raise ItemReferenceError("Typed item manifest is not explicitly approved.")
    if item.get("item_kind") not in KINDS or not item.get("version") or not item.get("item_id"):
        raise ItemReferenceError("Typed item manifest has invalid identity or version fields.")
    if style_name and item.get("style_name") != style_name:
        raise ItemReferenceError("Typed item style does not match the active generation style.")
    if character_id and str(item.get("character_id", "")).upper() != character_id.upper():
        raise ItemReferenceError("Typed item belongs to a different canonical character.")
    paths, profile, assembly = _registered_character(workspace, str(item["style_name"]), str(item["character_id"]))
    if profile != Path(str(item.get("character_profile", ""))).resolve() or assembly != Path(str(item.get("character_assembly", ""))).resolve():
        raise ItemReferenceError("Typed item canonical profile/assembly binding is stale.")
    if _sha256(assembly) != str(item.get("character_assembly_sha256", "")).lower():
        raise ItemReferenceError("Typed item canonical assembly hash is stale.")
    registered = rows
    if any(registered[0].get(key) != item.get(item_key) for key, item_key in (
        ("item_id", "item_id"), ("version", "version"), ("character_id", "character_id"))):
        raise ItemReferenceError("Typed item registry identity does not match the approved manifest.")
    expected_folder = (profile.parent / "03_CHARACTER_REFERENCES" / "08_TYPED_ITEMS" /
                       manager.safe_component(str(item["item_id"])) / manager.safe_component(str(item["version"]))).resolve()
    if path != (expected_folder / "APPROVED_ITEM_REFERENCE.json").resolve():
        raise ItemReferenceError("Approved item manifest is outside the registered character's typed-item library.")
    required = QA_COMMON + (QA_CLOTHING if item["item_kind"] == "CLOTHING" else ())
    checks = item.get("qa", {}).get("checks", {})
    if any(not isinstance(checks.get(name), dict) or checks[name].get("status") != "PASS" for name in required):
        raise ItemReferenceError("Typed item is missing PASS for required role-specific QA.")
    for key, label in (("item_design", "ITEM_DESIGN"), ("item_scale_fit", "ITEM_SCALE_FIT")):
        ref = item.get(key)
        if not isinstance(ref, dict):
            raise ItemReferenceError(f"Typed item is missing {label} source.")
        source = Path(str(ref.get("path", ""))).resolve()
        if not source.is_file() or _sha256(source) != str(ref.get("sha256", "")).lower():
            raise ItemReferenceError(f"Typed item {label} source is missing or changed.")
    if item["item_design"].get("subject_label") != "NO_SUBJECT" or item["item_design"].get("subject_label_sha256") != item["item_design"].get("sha256"):
        raise ItemReferenceError("NO_SUBJECT is permitted only for the isolated ITEM_DESIGN bytes and their exact hash.")
    if item["item_scale_fit"].get("character_id", "").upper() != str(item["character_id"]).upper():
        raise ItemReferenceError("Scale/fit source is not bound to the exact item character.")
    scale_qa = item.get("qa", {}).get("item_scale_fit_source", {})
    profile_identity = manager._approved_profile_identity(paths, profile, str(item["character_id"]))
    expected_label = {"MALE_ANATOMY": "MALE", "FEMALE_ANATOMY": "FEMALE"}.get(
        str(profile_identity.get("target_anatomy", "UNKNOWN")).upper())
    if (not expected_label or scale_qa.get("subject_label") != expected_label
            or scale_qa.get("anatomy_compatibility") != f"{expected_label}_ANATOMY"
            or scale_qa.get("subject_label_sha256", "").lower() != item["item_scale_fit"].get("sha256", "").lower()
            or not str(scale_qa.get("subject_label_evidence", "")).strip()
            or not str(scale_qa.get("anatomy_evidence_source", "")).strip()):
        raise ItemReferenceError("Scale/fit subject label does not match the approved character anatomy or exact bytes.")
    return {**item, "manifest_path": str(path)}


def registered_asset(paths: manager.StylePaths, image: Path) -> dict[str, Any] | None:
    """Resolve role membership only through the approved typed-item registry."""
    registry = paths.generations / "TYPED_ITEM_REFERENCE_REGISTRY.jsonl"
    if not registry.is_file():
        return None
    target = image.resolve()
    for line in registry.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        manifest_path = Path(str(row.get("manifest", ""))).resolve()
        if row.get("status") != "APPROVED_TYPED_ITEM" or not manifest_path.is_file() or _sha256(manifest_path) != row.get("manifest_sha256"):
            continue
        try:
            item = validate_manifest(paths.workspace, manifest_path, style_name=paths.style_name,
                                     character_id="")
        except (ItemReferenceError, OSError, ValueError):
            continue
        for key, role in (("item_design", "ITEM_DESIGN"), ("item_scale_fit", "ITEM_SCALE_FIT")):
            record = item.get(key)
            if isinstance(record, dict) and Path(str(record.get("path", ""))).resolve() == target:
                return {**record, "character_id": item.get("character_id"), "item_id": item.get("item_id"),
                        "version": item.get("version"), "item_kind": item.get("item_kind"),
                        "manifest_path": str(manifest_path), "asset_role": role}
    return None


def list_items(workspace: Path, style_name: str, character_id: str) -> list[dict[str, Any]]:
    """List only registry-backed, currently valid typed items for one character."""
    paths, _profile, _assembly = _registered_character(workspace, style_name, character_id)
    registry = paths.generations / "TYPED_ITEM_REFERENCE_REGISTRY.jsonl"
    if not registry.is_file():
        return []
    results: list[dict[str, Any]] = []
    for line in registry.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ItemReferenceError("Typed item registry contains invalid JSON.") from exc
        if str(row.get("character_id", "")).upper() != character_id.upper():
            continue
        try:
            item = validate_manifest(workspace, Path(str(row.get("manifest", ""))),
                                     style_name=style_name, character_id=character_id)
        except (ItemReferenceError, OSError, ValueError):
            continue
        results.append({key: item[key] for key in ("item_id", "item_name", "item_kind", "version", "manifest_path")})
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("create-plan", help="Create the item production plan with isolated design and exact-character scale/fit views.")
    plan.add_argument("--workspace", type=Path, required=True)
    plan.add_argument("--style-name", required=True)
    plan.add_argument("--request-id", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--character-id", required=True)
    plan.add_argument("--item-id", required=True)
    plan.add_argument("--item-kind", choices=KINDS, required=True)
    for name in ("name", "version", "design-spec", "dimensions", "material", "construction", "attachment-constraints"):
        plan.add_argument("--" + name, required=True)
    plan.set_defaults(handler=create_plan)
    approval = sub.add_parser("approve", help="Approve a typed item from separate design and exact-character scale/fit evidence.")
    approval.add_argument("--workspace", type=Path, required=True)
    approval.add_argument("--style-name", required=False, help="Compatibility argument; the plan's exact style remains authoritative.")
    approval.add_argument("--item-plan", required=True)
    approval.add_argument("--design-image", required=True)
    approval.add_argument("--scale-fit-image", required=True)
    approval.add_argument("--qa-json", required=True)
    approval.add_argument("--approval-quote", required=True)
    approval.add_argument("--user-approved", action="store_true")
    approval.set_defaults(handler=approve)
    validate = sub.add_parser("validate", help="Revalidate approval, registry, identity binding, role-specific QA, and hashes.")
    validate.add_argument("--workspace", type=Path, required=True)
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--style-name", default="")
    validate.add_argument("--character-id", default="")
    validate.set_defaults(handler=lambda args: validate_manifest(args.workspace, Path(args.manifest), style_name=args.style_name, character_id=args.character_id))
    listing = sub.add_parser("list", help="List only validated registry-backed typed items for an exact approved character.")
    listing.add_argument("--workspace", type=Path, required=True)
    listing.add_argument("--style-name", required=True)
    listing.add_argument("--character-id", required=True)
    listing.set_defaults(handler=lambda args: list_items(args.workspace, args.style_name, args.character_id))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.handler(args)
        if isinstance(result, dict):
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ItemReferenceError, manager.StylePackError, OSError, ValueError) as exc:
        print(f"ERROR={exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
