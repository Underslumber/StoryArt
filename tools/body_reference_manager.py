#!/usr/bin/env python3
"""Stage, review, append, and validate updates to BODY_REFERENCE_LIBRARY.

The update path is intentionally two-phase. ``stage-update`` inventories the
incoming folder and creates a human-review batch without changing the library
manifest. ``apply-update`` accepts only a complete, visually reviewed JSON file,
rechecks hashes and duplicate state, then appends new stable BR identifiers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageOps


WORKSPACE = Path(__file__).resolve().parents[1]
LIBRARY_NAME = "BODY_REFERENCE_LIBRARY"
MANIFEST_NAME = "BODY_REFERENCE_MANIFEST.csv"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
AUX_ROLES = {
    "AUX_BODY_BUILD", "AUX_POSE", "AUX_CAMERA", "AUX_CLOTHING_BEHAVIOR",
    "AUX_OBJECT_INTERACTION", "AUX_CONTACT_POINTS",
}
BASE_NOT_FOR = {"STYLE", "FACE", "HAIR", "PALETTE", "LIGHTING", "BACKGROUND_STYLE"}
SAFETY_STATUSES = {"SAFE", "SAFE_AFTER_VISUAL_REVIEW", "MASK_REQUIRED"}
REQUIRED_REVIEW_FIELDS = {
    "source_id", "action", "source_medium", "primary_family", "pose", "view",
    "camera_angle", "framing", "body_build", "proportion_tags", "foreshortening",
    "clothing_interaction", "object_interaction", "contact_points",
    "anatomy_reliability", "allowed_roles", "safety_status",
}


class BodyReferenceError(RuntimeError):
    pass


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_token(value: str, fallback: str = "REF") -> str:
    token = re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
    return token or fallback


def parse_list(value: object) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").split(";")
    return [str(item).strip().upper() for item in items if str(item).strip()]


def make_paths(workspace: Path) -> tuple[Path, Path]:
    library = workspace.resolve() / LIBRARY_NAME
    manifest = library / MANIFEST_NAME
    if not manifest.is_file():
        raise BodyReferenceError(f"Body reference manifest is unavailable: {manifest}")
    return library, manifest


def read_manifest(manifest: Path) -> tuple[list[str], list[dict[str, str]]]:
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        rows = list(reader)
    if not fields or not rows:
        raise BodyReferenceError(f"Manifest is empty or malformed: {manifest}")
    return fields, rows


def next_ref_number(rows: Iterable[dict[str, str]]) -> int:
    numbers = []
    for row in rows:
        match = re.fullmatch(r"BR_(\d{4,})", row.get("ref_id", ""))
        if match:
            numbers.append(int(match.group(1)))
    if not numbers:
        raise BodyReferenceError("No valid BR identifiers were found in the existing manifest.")
    return max(numbers) + 1


def dhash(image: Image.Image) -> int:
    gray = ImageOps.exif_transpose(image).convert("L").resize((17, 16))
    pixels = list(gray.get_flattened_data())
    bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (pixels[y * 17 + x] > pixels[y * 17 + x + 1])
    return bits


def image_info(path: Path) -> tuple[int, int, str, int]:
    with Image.open(path) as image:
        oriented = ImageOps.exif_transpose(image)
        return oriented.width, oriented.height, image.format or path.suffix.lstrip(".").upper(), dhash(image)


def resolve_batch(library: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        direct = library / "02_LOCAL_ONLY" / "UPDATE_BATCHES" / value
        candidate = direct if direct.exists() else (WORKSPACE / candidate)
    candidate = candidate.resolve()
    updates_root = (library / "02_LOCAL_ONLY" / "UPDATE_BATCHES").resolve()
    if updates_root not in candidate.parents:
        raise BodyReferenceError(f"Batch must be inside {updates_root}: {candidate}")
    if not candidate.is_dir():
        raise BodyReferenceError(f"Batch does not exist: {candidate}")
    return candidate


def write_contact_sheet(records: list[dict[str, object]], destination: Path) -> None:
    thumb_w, thumb_h, label_h, cols = 360, 480, 58, 4
    rows = (len(records) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h)), (28, 28, 30))
    draw = ImageDraw.Draw(canvas)
    for index, record in enumerate(records):
        path = Path(str(record["source_path"]))
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((thumb_w - 18, thumb_h - 18))
        cell_x = (index % cols) * thumb_w
        cell_y = (index // cols) * (thumb_h + label_h)
        canvas.paste(image, (cell_x + (thumb_w - image.width) // 2, cell_y + (thumb_h - image.height) // 2))
        draw.text((cell_x + 8, cell_y + thumb_h + 4), f"{record['source_id']} -> {record['proposed_ref_id']}  {record['width']}x{record['height']}", fill=(255, 220, 70))
        draw.text((cell_x + 8, cell_y + thumb_h + 27), str(record["source_filename"])[:42], fill=(220, 220, 220))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, quality=92)


def command_stage_update(args: argparse.Namespace) -> None:
    library, manifest = make_paths(args.workspace)
    fields, current = read_manifest(manifest)
    source = args.source.resolve()
    if not source.is_dir():
        raise BodyReferenceError(f"Incoming source directory does not exist: {source}")
    files = sorted(
        (path for path in source.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS),
        key=lambda path: str(path.relative_to(source)).lower(),
    )
    if not files:
        raise BodyReferenceError(f"No supported images found in {source}")
    batch_id = safe_token(args.batch_id, "BODY_UPDATE")
    batch = library / "02_LOCAL_ONLY" / "UPDATE_BATCHES" / batch_id
    if batch.exists():
        raise BodyReferenceError(f"Refusing to overwrite an existing batch: {batch}")
    batch.mkdir(parents=True)

    existing_by_hash = {row["source_sha256"]: row["ref_id"] for row in current}
    existing_dhashes: list[tuple[str, int]] = []
    for row in current:
        original = library / "00_SOURCE_ORIGINALS" / row["source_filename"]
        if original.is_file():
            with Image.open(original) as image:
                existing_dhashes.append((row["ref_id"], dhash(image)))
    next_number = next_ref_number(current)
    seen_batch: dict[str, str] = {}
    records: list[dict[str, object]] = []
    add_count = 0
    for index, path in enumerate(files, 1):
        digest = sha256(path)
        width, height, image_format, image_hash = image_info(path)
        exact = existing_by_hash.get(digest) or seen_batch.get(digest, "")
        status = "EXACT_DUPLICATE" if exact else "REVIEW_REQUIRED"
        proposed = ""
        if not exact:
            proposed = f"BR_{next_number + add_count:04d}"
            add_count += 1
        nearest = sorted(((int((image_hash ^ old_hash).bit_count()), ref_id) for ref_id, old_hash in existing_dhashes))[:3]
        records.append({
            "source_id": f"IN_{index:03d}",
            "source_path": str(path),
            "source_filename": path.name,
            "source_sha256": digest,
            "width": width,
            "height": height,
            "format": image_format,
            "bytes": path.stat().st_size,
            "exact_duplicate_of": exact,
            "nearest_existing": ";".join(f"{ref_id}:{distance}" for distance, ref_id in nearest),
            "near_duplicate_warning": "YES" if nearest and nearest[0][0] <= 12 else "NO",
            "proposed_ref_id": proposed,
            "status": status,
        })
        seen_batch.setdefault(digest, proposed or exact)

    inventory = batch / "SOURCE_INVENTORY.csv"
    with inventory.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(records[0]))
        writer.writeheader()
        writer.writerows(records)
    review_template = {
        "batch_id": batch_id,
        "review_status": "REVIEW_REQUIRED",
        "reviewed_at": "",
        "references": [
            {
                "source_id": record["source_id"],
                "action": "SKIP_DUPLICATE" if record["exact_duplicate_of"] else "ADD",
                "source_medium": "",
                "primary_family": "",
                "pose": "",
                "view": "",
                "camera_angle": "",
                "framing": "",
                "body_build": "",
                "proportion_tags": [],
                "foreshortening": "",
                "clothing_interaction": "",
                "object_interaction": "",
                "contact_points": "",
                "anatomy_reliability": "",
                "allowed_roles": [],
                "not_for_roles": [],
                "safety_status": "",
                "notes": "",
            }
            for record in records
        ],
    }
    (batch / "REVIEW_SPEC.json").write_text(json.dumps(review_template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_contact_sheet(records, batch / "CONTACT_SHEET_REVIEW_ONLY.jpg")
    metadata = {
        "schema_version": 1,
        "batch_id": batch_id,
        "created_at": iso_now(),
        "source_directory": str(source),
        "source_files": len(files),
        "new_candidates": add_count,
        "exact_duplicates": len(files) - add_count,
        "baseline_manifest_sha256": sha256(manifest),
        "baseline_next_ref_number": next_number,
        "status": "REVIEW_REQUIRED",
        "manifest_fields": fields,
    }
    (batch / "BATCH.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"BATCH={batch}")
    print(f"SOURCE_FILES={len(files)}")
    print(f"NEW_CANDIDATES={add_count}")
    print(f"EXACT_DUPLICATES={len(files) - add_count}")
    print(f"REVIEW_SPEC={batch / 'REVIEW_SPEC.json'}")
    print("STATUS=VISUAL_REVIEW_REQUIRED")


def load_review_batch(library: Path, batch: Path) -> tuple[dict[str, object], list[dict[str, str]], dict[str, object], list[dict[str, object]]]:
    metadata = json.loads((batch / "BATCH.json").read_text(encoding="utf-8"))
    with (batch / "SOURCE_INVENTORY.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        inventory = list(csv.DictReader(handle))
    review = json.loads((batch / "REVIEW_SPEC.json").read_text(encoding="utf-8"))
    entries = review.get("references", [])
    if review.get("batch_id") != metadata.get("batch_id"):
        raise BodyReferenceError("Review specification belongs to another batch.")
    if review.get("review_status") != "VISUALLY_REVIEWED":
        raise BodyReferenceError("REVIEW_SPEC.json must be marked VISUALLY_REVIEWED after inspecting every image.")
    if not isinstance(entries, list) or len(entries) != len(inventory):
        raise BodyReferenceError("Review specification must cover every staged source exactly once.")
    return metadata, inventory, review, entries


def validate_review_entries(inventory: list[dict[str, str]], entries: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    inventory_ids = {row["source_id"] for row in inventory}
    by_id: dict[str, dict[str, object]] = {}
    for entry in entries:
        source_id = str(entry.get("source_id", ""))
        if source_id not in inventory_ids or source_id in by_id:
            raise BodyReferenceError(f"Unknown or duplicate source_id in review: {source_id}")
        action = str(entry.get("action", "")).upper()
        if action not in {"ADD", "SKIP_DUPLICATE", "REJECT"}:
            raise BodyReferenceError(f"Unknown action for {source_id}: {action}")
        if action == "ADD":
            missing = sorted(field for field in REQUIRED_REVIEW_FIELDS if not entry.get(field))
            if missing:
                raise BodyReferenceError(f"Incomplete visual review for {source_id}: {', '.join(missing)}")
            roles = set(parse_list(entry["allowed_roles"]))
            if not roles or not roles.issubset(AUX_ROLES):
                raise BodyReferenceError(f"Invalid allowed_roles for {source_id}: {sorted(roles)}")
            safety = str(entry["safety_status"]).upper()
            if safety not in SAFETY_STATUSES:
                raise BodyReferenceError(f"Invalid safety_status for {source_id}: {safety}")
        by_id[source_id] = entry
    if set(by_id) != inventory_ids:
        raise BodyReferenceError("Review specification does not cover the complete inventory.")
    return by_id


def command_apply_update(args: argparse.Namespace) -> None:
    library, manifest = make_paths(args.workspace)
    batch = resolve_batch(library, args.batch)
    metadata, inventory, review, entries = load_review_batch(library, batch)
    reviewed = validate_review_entries(inventory, entries)
    fields, current = read_manifest(manifest)
    if sha256(manifest) != metadata.get("baseline_manifest_sha256"):
        raise BodyReferenceError("The library changed after staging. Restage the batch to allocate safe non-conflicting IDs.")
    if next_ref_number(current) != int(metadata.get("baseline_next_ref_number", -1)):
        raise BodyReferenceError("The next BR identifier changed after staging. Restage the batch.")
    existing_hashes = {row["source_sha256"] for row in current}
    inventory_by_id = {row["source_id"]: row for row in inventory}
    additions: list[tuple[dict[str, str], dict[str, object]]] = []
    for source_id, entry in reviewed.items():
        row = inventory_by_id[source_id]
        source = Path(row["source_path"])
        if not source.is_file() or sha256(source) != row["source_sha256"]:
            raise BodyReferenceError(f"Incoming source changed or disappeared after staging: {source}")
        action = str(entry["action"]).upper()
        if action == "ADD":
            if row["source_sha256"] in existing_hashes:
                raise BodyReferenceError(f"An exact duplicate entered the library after staging: {source_id}")
            additions.append((row, entry))
            existing_hashes.add(row["source_sha256"])
    if not additions:
        raise BodyReferenceError("The reviewed batch has no ADD entries.")

    planned: list[tuple[Path, Path]] = []
    new_rows: list[dict[str, str]] = []
    reserved_originals: set[Path] = set()
    for row, entry in additions:
        ref_id = row["proposed_ref_id"]
        if not ref_id:
            raise BodyReferenceError(f"No proposed BR id for {row['source_id']}; restage the batch.")
        source = Path(row["source_path"])
        original_name = source.name
        original_destination = library / "00_SOURCE_ORIGINALS" / original_name
        if original_destination.exists() or original_destination in reserved_originals:
            original_name = f"{ref_id}_{original_name}"
            original_destination = library / "00_SOURCE_ORIGINALS" / original_name
        reserved_originals.add(original_destination)
        family = safe_token(str(entry["primary_family"]), "OTHER")
        pose = safe_token(str(entry["pose"]), "POSE")
        view = safe_token(str(entry["view"]), "VIEW")
        angle = safe_token(str(entry["camera_angle"]), "EYE")
        curated_name = f"{ref_id}_{pose}_{view}_{angle}{source.suffix.lower()}"
        curated_destination = library / "01_CURATED" / family / curated_name
        safety = str(entry["safety_status"]).upper()
        generator_safe = safety != "MASK_REQUIRED"
        generator_path = curated_destination.relative_to(library).as_posix() if generator_safe else ""
        planned.extend(((source, original_destination), (source, curated_destination)))
        if not generator_safe:
            planned.append((source, library / "02_LOCAL_ONLY" / "SAFETY_MASK_REQUIRED" / curated_name))
        not_for = BASE_NOT_FOR | set(parse_list(entry.get("not_for_roles", [])))
        new_rows.append({
            "ref_id": ref_id,
            "filename": curated_name,
            "source_filename": original_name,
            "source_sha256": row["source_sha256"],
            "source_medium": safe_token(str(entry["source_medium"]), "UNKNOWN"),
            "primary_family": family,
            "pose": pose,
            "view": view,
            "camera_angle": angle,
            "framing": safe_token(str(entry["framing"]), "FULL"),
            "body_build": safe_token(str(entry["body_build"]), "UNSPECIFIED"),
            "proportion_tags": ";".join(parse_list(entry["proportion_tags"])),
            "anatomy_behavior": "POSE_DEFORMATION;WEIGHT_DISTRIBUTION;SILHOUETTE_CHANGE",
            "foreshortening": safe_token(str(entry["foreshortening"]), "LOW_OR_MODERATE"),
            "clothing_interaction": ";".join(parse_list(entry["clothing_interaction"])),
            "object_interaction": safe_token(str(entry["object_interaction"]), "NONE"),
            "contact_points": safe_token(str(entry["contact_points"]), "INFER_FROM_IMAGE"),
            "anatomy_reliability": safe_token(str(entry["anatomy_reliability"]), "MEDIUM"),
            "allowed_roles": ";".join(parse_list(entry["allowed_roles"])),
            "not_for_roles": ";".join(sorted(not_for)),
            "safety_status": safety,
            "generator_safe": "YES" if generator_safe else "NO",
            "generator_path": generator_path,
            "style_influence": "FORBIDDEN",
            "use_for": "Only the explicitly selected body build, pose, camera, garment behavior, object interaction, and contact-point roles.",
            "do_not_use_for": "Face, identity, hair, skin tone, costume design, color, linework, rendering style, lighting, or background style.",
            "notes": str(entry.get("notes", "")),
        })
    destinations = [destination for _, destination in planned]
    if len(destinations) != len(set(destinations)) or any(path.exists() for path in destinations):
        raise BodyReferenceError("The append plan contains a duplicate or existing destination; no files were changed.")
    if args.dry_run:
        print(f"BATCH={batch}")
        print(f"ADD_COUNT={len(new_rows)}")
        print(f"FIRST_REF={new_rows[0]['ref_id']}")
        print(f"LAST_REF={new_rows[-1]['ref_id']}")
        print("STATUS=DRY_RUN_VALID")
        return

    created: list[Path] = []
    try:
        for source, destination in planned:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            created.append(destination)
            if sha256(source) != sha256(destination):
                raise BodyReferenceError(f"Hash mismatch after copy: {destination}")
        temporary = manifest.with_suffix(".csv.update.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(current + new_rows)
        temporary.replace(manifest)
    except Exception:
        for path in reversed(created):
            if path.is_file():
                path.unlink()
        raise

    update_log = batch / "APPLIED_REFERENCES.csv"
    with update_log.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(new_rows)
    metadata["status"] = "APPLIED"
    metadata["applied_at"] = iso_now()
    metadata["added_references"] = [row["ref_id"] for row in new_rows]
    metadata["result_manifest_sha256"] = sha256(manifest)
    (batch / "BATCH.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"BATCH={batch}")
    print(f"ADDED={len(new_rows)}")
    print(f"FIRST_REF={new_rows[0]['ref_id']}")
    print(f"LAST_REF={new_rows[-1]['ref_id']}")
    print(f"MANIFEST={manifest}")
    print("STATUS=UPDATE_APPLIED")


def unique_archive_path(root: Path, ref_id: str, suffix: str) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    base = root / f"{stamp}_body-ref-{ref_id.lower()}-censored{suffix.lower()}"
    if not base.exists():
        return base
    counter = 2
    while True:
        candidate = base.with_name(f"{base.stem}_{counter:02d}{base.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def command_register_censored(args: argparse.Namespace) -> None:
    library, manifest = make_paths(args.workspace)
    fields, rows = read_manifest(manifest)
    ref_id = args.ref_id.upper()
    matches = [row for row in rows if row["ref_id"] == ref_id]
    if len(matches) != 1:
        raise BodyReferenceError(f"Unknown or duplicate reference id: {ref_id}")
    row = matches[0]
    if row["generator_safe"] == "YES":
        raise BodyReferenceError(f"{ref_id} is already generator-safe; refusing to replace its active source.")
    if row["safety_status"] != "MASK_REQUIRED":
        raise BodyReferenceError(f"{ref_id} is not marked MASK_REQUIRED.")
    image = args.image.resolve()
    if not image.is_file() or image.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise BodyReferenceError(f"Censored derivative is missing or unsupported: {image}")
    curated = library / "01_CURATED" / row["primary_family"] / row["filename"]
    source_width, source_height, _, _ = image_info(curated)
    edit_width, edit_height, _, _ = image_info(image)
    if (source_width, source_height) != (edit_width, edit_height):
        raise BodyReferenceError(
            f"Censored derivative dimensions changed: {(edit_width, edit_height)} != {(source_width, source_height)}"
        )
    safe_root = library / "03_GENERATOR_SAFE"
    safe_root.mkdir(parents=True, exist_ok=True)
    stem = Path(row["filename"]).stem
    suffix = image.suffix.lower()
    version = 1
    while True:
        destination = safe_root / f"{stem}_censored_v{version}{suffix}"
        if not destination.exists():
            break
        version += 1
    shutil.copy2(image, destination)
    if sha256(image) != sha256(destination):
        destination.unlink(missing_ok=True)
        raise BodyReferenceError(f"Hash mismatch after copying censored derivative: {destination}")
    archive_root = args.workspace.resolve() / "GENERATION_RESULTS"
    archive_root.mkdir(parents=True, exist_ok=True)
    archive = unique_archive_path(archive_root, ref_id, suffix)
    shutil.copy2(destination, archive)
    row["safety_status"] = "MINIMAL_BLUR_APPLIED"
    row["generator_safe"] = "YES"
    row["generator_path"] = destination.relative_to(library).as_posix()
    note = args.notes or "Visible explicit detail minimally censored; source and curated copy preserved."
    if note not in row.get("notes", ""):
        row["notes"] = "; ".join(part for part in (row.get("notes", ""), note) if part)
    temporary = manifest.with_suffix(".csv.censor.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(manifest)
    log = library / "02_LOCAL_ONLY" / "LOGS" / "CENSORSHIP_LOG.csv"
    log_fields = (
        "ref_id", "source_path", "source_sha256", "derivative_path", "derivative_sha256",
        "archive_path", "archive_sha256", "regions", "created_at", "status",
    )
    previous: list[dict[str, str]] = []
    if log.is_file():
        with log.open("r", encoding="utf-8-sig", newline="") as handle:
            previous = list(csv.DictReader(handle))
    previous.append({
        "ref_id": ref_id,
        "source_path": str(curated),
        "source_sha256": sha256(curated),
        "derivative_path": str(destination),
        "derivative_sha256": sha256(destination),
        "archive_path": str(archive),
        "archive_sha256": sha256(archive),
        "regions": args.regions,
        "created_at": iso_now(),
        "status": "ACTIVE",
    })
    with log.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=log_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(previous)
    print(f"REF_ID={ref_id}")
    print(f"GENERATOR_SAFE={destination}")
    print(f"ARCHIVE={archive}")
    print("STATUS=CENSORED_DERIVATIVE_REGISTERED")


def command_validate(args: argparse.Namespace) -> None:
    library, manifest = make_paths(args.workspace)
    _, rows = read_manifest(manifest)
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for row in rows:
        ref_id = row["ref_id"]
        if ref_id in seen_ids:
            errors.append(f"duplicate id {ref_id}")
        seen_ids.add(ref_id)
        if row["source_sha256"] in seen_hashes:
            errors.append(f"duplicate source hash at {ref_id}")
        seen_hashes.add(row["source_sha256"])
        original = library / "00_SOURCE_ORIGINALS" / row["source_filename"]
        curated = library / "01_CURATED" / row["primary_family"] / row["filename"]
        if not original.is_file() or not curated.is_file():
            errors.append(f"missing source or curated file for {ref_id}")
            continue
        if sha256(original) != row["source_sha256"] or sha256(curated) != row["source_sha256"]:
            errors.append(f"source hash mismatch for {ref_id}")
        if row["generator_safe"] == "YES":
            generator = library / row["generator_path"]
            if not row["generator_path"] or not generator.is_file():
                errors.append(f"missing generator-safe path for {ref_id}")
        if row.get("style_influence") != "FORBIDDEN":
            errors.append(f"style influence is not forbidden for {ref_id}")
    print(f"REFERENCES={len(rows)}")
    print(f"GENERATOR_SAFE={sum(row['generator_safe'] == 'YES' for row in rows)}")
    print(f"ERRORS={len(errors)}")
    for error in errors:
        print(f"ERROR={error}")
    if errors:
        raise BodyReferenceError("Body reference library validation failed.")
    print("STATUS=VALID")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely stage and append BODY_REFERENCE_LIBRARY updates.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    stage = subparsers.add_parser("stage-update", help="Inventory incoming images and create a visual-review batch.")
    stage.add_argument("--workspace", type=Path, default=WORKSPACE)
    stage.add_argument("--source", type=Path, required=True)
    stage.add_argument("--batch-id", required=True)
    stage.set_defaults(handler=command_stage_update)
    apply = subparsers.add_parser("apply-update", help="Append a fully reviewed batch without rebuilding old references.")
    apply.add_argument("--workspace", type=Path, default=WORKSPACE)
    apply.add_argument("--batch", required=True)
    apply.add_argument("--dry-run", action="store_true")
    apply.set_defaults(handler=command_apply_update)
    censored = subparsers.add_parser(
        "register-censored",
        help="Register a separately created minimal censored derivative for a MASK_REQUIRED reference.",
    )
    censored.add_argument("--workspace", type=Path, default=WORKSPACE)
    censored.add_argument("--ref-id", required=True)
    censored.add_argument("--image", type=Path, required=True)
    censored.add_argument("--regions", default="", help="Human-readable or normalized-mask provenance.")
    censored.add_argument("--notes", default="")
    censored.set_defaults(handler=command_register_censored)
    validate = subparsers.add_parser("validate", help="Verify ids, hashes, paths, safety state, and style isolation.")
    validate.add_argument("--workspace", type=Path, default=WORKSPACE)
    validate.set_defaults(handler=command_validate)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
    except (BodyReferenceError, OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
