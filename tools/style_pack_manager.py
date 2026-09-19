#!/usr/bin/env python3
"""Create and maintain reusable visual style packs for StoryArt.

The manager deliberately automates deterministic bookkeeping only. Artistic role
assignment, anchor approval, character approval, and image editing remain explicit
review actions governed by .agents/STYLE_PACK_WORKFLOW.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

try:
    from tools.task_execution_guard import (
        GuardError as ExecutionGuardError,
        checkpoint as execution_checkpoint,
        require_active_guard,
        require_execution_started,
    )
except ModuleNotFoundError:  # Direct execution as python tools\style_pack_manager.py
    from task_execution_guard import (
        GuardError as ExecutionGuardError,
        checkpoint as execution_checkpoint,
        require_active_guard,
        require_execution_started,
    )


SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_WORKSPACE = SCRIPT_PATH.parents[1]
TEMPLATE_ROOT = DEFAULT_WORKSPACE / "templates" / "STYLE_PROJECT_PACK"
BODY_LIBRARY_NAME = "BODY_REFERENCE_LIBRARY"
SCHEMA_VERSION = 1

BODY_AUX_MODES = {
    "STAGING_ONLY": {"AUX_POSE", "AUX_CAMERA"},
    "BODY_BUILD_TARGET": {"AUX_BODY_BUILD"},
    "CLOTHING_BEHAVIOR": {"AUX_CLOTHING_BEHAVIOR"},
    "OBJECT_INTERACTION": {"AUX_OBJECT_INTERACTION"},
    "CAMERA_ONLY": {"AUX_CAMERA"},
}

DEFAULT_ASPECT_BY_ORIENTATION = {"PORTRAIT": "9:16", "LANDSCAPE": "16:9"}
STANDARD_ASPECT_RATIOS = {"9:16", "16:9"}
TARGET_FRAMINGS = ("FULL_BODY", "THREE_QUARTER", "HALF_BODY", "PORTRAIT")
BODY_POSE_FAMILIES = ("STANDING", "SEATED", "LYING", "KNEELING", "CROUCHING", "HANGING", "OTHER")
BODY_SOURCE_COVERAGES = ("FULL_BODY", "THREE_QUARTER", "TORSO_ONLY", "LOWER_BODY_ONLY")
GENERATION_PURPOSES = ("CHARACTER_BASE", "SCENE", "TECHNICAL_TEST")
SCENE_KINDS = ("LOCATION", "PHENOMENON", "ARTIFACT", "MIXED")
SCENE_OUTPUT_USES = ("GENERAL_ART", "WALLPAPER", "PROMO_POSTER")
TEXT_SAFE_ZONES = ("NONE", "TOP", "BOTTOM", "LEFT", "RIGHT")
CHARACTER_REFERENCE_MODES = ("AUTO", "ASSEMBLY_ONLY", "ASSEMBLY_PLUS_VIEW", "IDENTITY_STRICT")
SHOT_COMPLEXITIES = ("SIMPLE", "NORMAL", "COMPLEX")
BODY_VIEW_CHOICES = ("ASSEMBLY", "FRONT", "SIDE", "BACK")
QA_LAYER_NAMES = {
    "ATTACHMENTS", "CANVAS", "STAGE_LAYER", "FACE_GEOMETRY", "BODY_SILHOUETTE",
    "BODY_PROPORTIONS", "LIMB_PROPORTIONS", "STYLE", "BODY_RENDERING_STYLE",
    "EXPRESSION", "NEUTRAL_BACKDROP", "FRONT_VIEW", "SIDE_VIEW", "BACK_VIEW",
    "SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "MULTIVIEW_CONSISTENCY", "CLOTHING",
    "POSE_CONTACTS", "CAMERA", "LIGHTING", "BACKGROUND", "COMPOSITION",
    "SUBJECT_ACCURACY", "NO_UNREQUESTED_CHARACTERS", "FOCAL_HIERARCHY",
    "DISTANCE_READABILITY", "DESKTOP_USABILITY", "POSTER_READABILITY", "COPY_SAFE_AREA",
    "DEPTH_AND_SCALE", "PHENOMENON_CAUSALITY", "ARTIFACT_INTEGRITY",
}

SUPPORTED_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
}

PACK_DIRECTORIES = (
    "00_SOURCE_ORIGINALS",
    "01_WORK/STYLE_CROPS",
    "01_WORK/FACE_CROPS",
    "01_WORK/FACE_EXPRESSIONS",
    "01_WORK/BODY_CROPS",
    "01_WORK/POSE_CROPS",
    "01_WORK/CLOTHES_CROPS",
    "01_WORK/LIGHTING_CROPS",
    "01_WORK/BACKGROUND_CROPS",
    "01_WORK/COMPOSITION_CROPS",
    "01_WORK/SUBJECT_CROPS",
    "01_WORK/CENSORED_REPAIRED",
    "01_WORK/UPLOAD_CANDIDATES_DRAFT",
    "02_LOCAL_ONLY_DO_NOT_UPLOAD/BACKUPS",
    "02_LOCAL_ONLY_DO_NOT_UPLOAD/CONTACT_SHEETS",
    "02_LOCAL_ONLY_DO_NOT_UPLOAD/COLLAGES",
    "02_LOCAL_ONLY_DO_NOT_UPLOAD/DUPLICATES",
    "02_LOCAL_ONLY_DO_NOT_UPLOAD/REJECTED",
    "02_LOCAL_ONLY_DO_NOT_UPLOAD/LOGS",
    "03_UPLOAD_TO_WEB",
)

GENERATION_DIRECTORIES = (
    "00_PENDING",
    "01_APPROVED_CHARACTERS",
    "02_APPROVED_STANDALONE",
)

ROLES = (
    "MASTER_STYLE",
    "ANCHOR_STYLE",
    "FACE_CORE",
    "FACE_EXPRESSION",
    "BODY_CORE",
    "POSE_CORE",
    "CLOTHES_CORE",
    "LIGHTING_CORE",
    "BACKGROUND_CORE",
    "COMPOSITION_CORE",
    "SUBJECT_CORE",
    "CHARACTER_FACE",
    "CHARACTER_BODY",
    "CHARACTER",
    "CONTINUITY",
    "APPROVED_FRAME",
)

ROLE_DIRECTORIES = {
    "MASTER_STYLE": "01_WORK/STYLE_CROPS",
    "ANCHOR_STYLE": "01_WORK/STYLE_CROPS",
    "FACE_CORE": "01_WORK/FACE_CROPS",
    "FACE_EXPRESSION": "01_WORK/FACE_EXPRESSIONS",
    "BODY_CORE": "01_WORK/BODY_CROPS",
    "POSE_CORE": "01_WORK/POSE_CROPS",
    "CLOTHES_CORE": "01_WORK/CLOTHES_CROPS",
    "LIGHTING_CORE": "01_WORK/LIGHTING_CROPS",
    "BACKGROUND_CORE": "01_WORK/BACKGROUND_CROPS",
    "COMPOSITION_CORE": "01_WORK/COMPOSITION_CROPS",
    "SUBJECT_CORE": "01_WORK/SUBJECT_CROPS",
    "CHARACTER_FACE": "01_WORK/FACE_CROPS",
    "CHARACTER_BODY": "01_WORK/BODY_CROPS",
    "CHARACTER": "01_WORK/UPLOAD_CANDIDATES_DRAFT",
    "CONTINUITY": "01_WORK/UPLOAD_CANDIDATES_DRAFT",
    "APPROVED_FRAME": "01_WORK/UPLOAD_CANDIDATES_DRAFT",
}

INVENTORY_FIELDS = (
    "source_id",
    "stored_relative_path",
    "original_path",
    "sha256",
    "width",
    "height",
    "format",
    "bytes",
    "exact_duplicate_of",
    "status",
    "ingested_at",
    "notes",
)

REFERENCE_FIELDS = (
    "reference_id",
    "filename",
    "stored_relative_path",
    "primary_role",
    "secondary_roles",
    "character_id",
    "source_filename",
    "source_sha256",
    "crop_box",
    "shot_type",
    "expression",
    "lighting",
    "background_type",
    "text_present",
    "generator_safe",
    "use_for",
    "do_not_use_for",
    "status",
    "user_approved",
    "notes",
)

GENERATION_FIELDS = (
    "generation_id",
    "created_at",
    "style_name",
    "request_id",
    "character_id",
    "status",
    "fidelity",
    "risk_level",
    "description",
    "source_image",
    "archive_file",
    "style_file",
    "parent_generation",
    "reference_plan",
    "qa_evidence",
    "qa_output_sha256",
    "qa_receipt_sha256",
    "qa_contract_sha256",
    "qa_plan_sha256",
    "scene_kind",
    "output_use",
    "aspect_ratio",
    "typography_mode",
    "notes",
)

CHARACTER_FIELDS = (
    "character_id",
    "name",
    "created_at",
    "approved_base",
    "profile_path",
    "face_references",
    "body_references",
    "status",
    "notes",
)


class StylePackError(RuntimeError):
    pass


@dataclass(frozen=True)
class StylePaths:
    workspace: Path
    style_name: str
    slug: str
    pack: Path
    generations: Path

    @property
    def metadata(self) -> Path:
        return self.pack / ".style-pack.json"

    @property
    def inventory(self) -> Path:
        return self.pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "LOGS" / "SOURCE_INVENTORY.csv"

    @property
    def references(self) -> Path:
        return self.pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "LOGS" / "PRELIMINARY_REFERENCE_MANIFEST.csv"

    @property
    def duplicates(self) -> Path:
        return self.pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "DUPLICATES" / "EXACT_DUPLICATES.csv"

    @property
    def upload_manifest(self) -> Path:
        return self.pack / "03_UPLOAD_TO_WEB" / "00_PROJECT_MANIFEST.csv"

    @property
    def generation_manifest(self) -> Path:
        return self.generations / "GENERATION_MANIFEST.csv"

    @property
    def character_registry(self) -> Path:
        return self.generations / "CHARACTER_REGISTRY.csv"


@dataclass(frozen=True)
class DiscoveredStyle:
    style_name: str
    slug: str
    pack_path: str
    generations_path: str
    management: str
    local_readiness: str
    web_readiness: str
    source_images: int
    work_images: int
    upload_images: int
    upload_documents: int
    characters: int
    can_generate: bool
    can_create_web_project: bool
    notes: str


def local_now() -> datetime:
    return datetime.now().astimezone()


def iso_now() -> str:
    return local_now().isoformat(timespec="seconds")


def style_slug(style_name: str) -> str:
    text = unicodedata.normalize("NFKC", style_name).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip(" ._")
    if not text:
        raise StylePackError("Style name becomes empty after Windows path sanitization.")
    if text.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        text = f"STYLE_{text}"
    return text[:80].upper()


def safe_component(value: str, fallback: str = "item") -> str:
    text = unicodedata.normalize("NFKC", value).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip(" ._")
    return (text or fallback)[:100]


def make_paths(workspace: Path, style_name: str) -> StylePaths:
    workspace = workspace.resolve()
    slug = style_slug(style_name)
    return StylePaths(
        workspace=workspace,
        style_name=style_name.strip(),
        slug=slug,
        pack=workspace / f"{slug}_PROJECT_PACK",
        generations=workspace / f"{slug}_GENERATIONS",
    )


def count_images(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS)


def count_documents(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file() and path.suffix.lower() in {".md", ".txt", ".csv", ".json", ".yaml", ".yml"})


def discovered_character_count(generations: Path) -> int:
    registry = generations / "CHARACTER_REGISTRY.csv"
    if registry.is_file():
        rows = read_csv(registry)
        if rows:
            return len({row.get("character_id", "") for row in rows if row.get("character_id")})
    character_root = generations / "01_APPROVED_CHARACTERS"
    if not character_root.is_dir():
        return 0
    return sum(1 for path in character_root.iterdir() if path.is_dir() and re.match(r"^CHAR_\d+", path.name, re.IGNORECASE))


def discover_style_packs(workspace: Path) -> list[DiscoveredStyle]:
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise StylePackError(f"Workspace does not exist: {workspace}")
    excluded_top_levels = {
        ".agents",
        ".codex",
        "templates",
        "tmp",
        "generation_results",
        "__pycache__",
    }
    candidates: list[Path] = []
    for candidate in workspace.rglob("*"):
        if not candidate.is_dir() or not candidate.name.upper().endswith("_PROJECT_PACK"):
            continue
        relative = candidate.relative_to(workspace)
        if not relative.parts:
            continue
        if relative.parts[0].lower() in excluded_top_levels:
            continue
        if any(part.startswith(".") or part.lower() == "__pycache__" for part in relative.parts):
            continue
        candidates.append(candidate)

    styles: list[DiscoveredStyle] = []
    for pack in sorted(candidates, key=lambda path: str(path).lower()):
        folder_slug = pack.name[: -len("_PROJECT_PACK")]
        metadata_path = pack / ".style-pack.json"
        metadata: dict[str, object] = {}
        metadata_error = ""
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as error:
                metadata_error = f"Invalid metadata: {error}"

        style_name = str(metadata.get("style_name") or folder_slug.replace("_", " "))
        slug = str(metadata.get("style_slug") or folder_slug)
        management = "MANAGED" if metadata and not metadata_error else ("BROKEN_METADATA" if metadata_error else "LEGACY")

        generation_value = str(metadata.get("generations") or "")
        generations = Path(generation_value) if generation_value else pack.parent / f"{folder_slug}_GENERATIONS"
        source_images = count_images(pack / "00_SOURCE_ORIGINALS")
        work_images = count_images(pack / "01_WORK")
        upload_images = count_images(pack / "03_UPLOAD_TO_WEB")
        upload_documents = count_documents(pack / "03_UPLOAD_TO_WEB")
        characters = discovered_character_count(generations) if generations.is_dir() else 0

        missing_core = [
            name
            for name in ("00_SOURCE_ORIGINALS", "01_WORK", "02_LOCAL_ONLY_DO_NOT_UPLOAD", "03_UPLOAD_TO_WEB")
            if not (pack / name).is_dir()
        ]
        notes: list[str] = []
        if metadata_error:
            notes.append(metadata_error)
        if missing_core:
            local_readiness = "INCOMPLETE"
            notes.append("Missing: " + ", ".join(missing_core))
        elif work_images > 0 or upload_images > 0:
            local_readiness = "READY"
        elif source_images > 0:
            local_readiness = "REVIEW_REQUIRED"
            notes.append("Local references exist but no reviewed working library is present.")
        else:
            local_readiness = "EMPTY"
            notes.append("No source, work, or upload images found.")

        if not (pack / "03_UPLOAD_TO_WEB").is_dir():
            web_readiness = "INCOMPLETE"
        elif upload_images > 0:
            web_readiness = "READY"
        elif local_readiness in {"READY", "REVIEW_REQUIRED"}:
            web_readiness = "NOT_PREPARED"
            notes.append("Web-project export is not prepared; local use is unaffected.")
        else:
            web_readiness = "EMPTY"
        if not generations.is_dir():
            notes.append("Generation library is not initialized yet.")

        styles.append(
            DiscoveredStyle(
                style_name=style_name,
                slug=slug,
                pack_path=str(pack),
                generations_path=str(generations) if generations.is_dir() else "",
                management=management,
                local_readiness=local_readiness,
                web_readiness=web_readiness,
                source_images=source_images,
                work_images=work_images,
                upload_images=upload_images,
                upload_documents=upload_documents,
                characters=characters,
                can_generate=local_readiness == "READY",
                can_create_web_project=web_readiness == "READY",
                notes=" ".join(notes),
            )
        )
    return styles


def command_list_styles(args: argparse.Namespace) -> None:
    styles = discover_style_packs(args.workspace)
    if args.match:
        needle = args.match.casefold()
        styles = [
            style
            for style in styles
            if needle in style.style_name.casefold() or needle in style.slug.casefold() or needle in style.pack_path.casefold()
        ]
    if args.ready_only:
        styles = [style for style in styles if style.can_generate]
    if args.web_ready_only:
        styles = [style for style in styles if style.can_create_web_project]

    if args.json:
        payload = [style.__dict__ for style in styles]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not styles:
        print("AVAILABLE_STYLES=0")
        print("STATUS=NO_STYLES_FOUND")
        return
    for style in styles:
        generation_status = style.generations_path or "NOT_INITIALIZED"
        print(
            f"STYLE={style.style_name} | LOCAL={style.local_readiness} | WEB={style.web_readiness} | MANAGEMENT={style.management} | "
            f"UPLOAD_IMAGES={style.upload_images} | CHARACTERS={style.characters}"
        )
        print(f"PACK={style.pack_path}")
        print(f"GENERATIONS={generation_status}")
        if style.notes:
            print(f"NOTES={style.notes}")
    print(f"AVAILABLE_STYLES={len(styles)}")
    print(f"LOCAL_READY_STYLES={sum(1 for style in styles if style.can_generate)}")
    print(f"WEB_READY_STYLES={sum(1 for style in styles if style.can_create_web_project)}")
    print("STATUS=DISCOVERY_COMPLETE")


def command_style_readiness(args: argparse.Namespace) -> None:
    proposal = style_readiness_proposal(make_paths(args.workspace, args.style_name))
    if args.json:
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
        return
    for key, value in proposal.items():
        print(f"{str(key).upper()}={value}")


LOCAL_CONTEXT_ROLES = (
    "ALL",
    "STYLE",
    "FACE",
    "BODY",
    "POSE",
    "CLOTHES",
    "LIGHTING",
    "BACKGROUND",
    "COMPOSITION",
    "SUBJECT",
    "SOURCE",
    "WEB_EXPORT",
)


def inferred_asset_roles(relative: Path) -> list[str]:
    text = relative.as_posix().upper()
    roles: set[str] = set()
    if "FACE" in text:
        roles.add("FACE")
    if any(token in text for token in ("BODY", "FULLBODY", "TORSO")):
        roles.add("BODY")
    if "POSE" in text:
        roles.add("POSE")
    if any(token in text for token in ("CLOTH", "OUTFIT", "COSTUME", "DRESS")):
        roles.add("CLOTHES")
    if any(token in text for token in ("LIGHT", "LIGHTING")):
        roles.add("LIGHTING")
    if any(token in text for token in ("BACKGROUND", "INTERIOR", "ENVIRONMENT")):
        roles.add("BACKGROUND")
    if any(token in text for token in ("COMPOSITION", "FRAMING", "CAMERA")):
        roles.add("COMPOSITION")
    if "SUBJECT" in text:
        roles.add("SUBJECT")
    if "STYLE" in text or "ANCHOR" in text or "MASTER" in text:
        roles.add("STYLE")
    if relative.parts and relative.parts[0] == "00_SOURCE_ORIGINALS":
        roles.add("SOURCE")
    if relative.parts and relative.parts[0] == "03_UPLOAD_TO_WEB":
        roles.add("WEB_EXPORT")
    return sorted(roles)


def local_asset_status(relative: Path, is_image: bool) -> tuple[str, bool]:
    text = relative.as_posix().upper()
    if any(token in text for token in ("REJECTED", "NOT_FOR_PROJECT")):
        return "REJECTED", False
    if any(token in text for token in ("CONTACT_SHEET", "CONTACTS", "REVIEW_BOARD", "COLLAGE")):
        return "REVIEW_ONLY", False
    if any(token in text for token in ("CHARACTERS_V", "GENERATION", "DRAFT_STYLE_DRIFT")):
        return "DERIVED_GENERATION", False
    if not is_image:
        return "DOCUMENTATION", False
    if relative.parts and relative.parts[0] == "00_SOURCE_ORIGINALS":
        return "SOURCE_EVIDENCE", False
    if "DRAFT" in text or "CANDIDATE" in text:
        return "REVIEW_REQUIRED", False
    if relative.parts and relative.parts[0] == "02_LOCAL_ONLY_DO_NOT_UPLOAD":
        return "LOCAL_SUPPORT", False
    if relative.parts and relative.parts[0] == "03_UPLOAD_TO_WEB":
        return "WEB_EXPORT_COPY", True
    return "POSITIVE_CANDIDATE", True


def manifest_role_files(pack: Path, role: str) -> list[str]:
    found: set[str] = set()
    for manifest in pack.rglob("*.csv"):
        try:
            rows = read_csv(manifest)
        except (OSError, UnicodeError, csv.Error):
            continue
        for row in rows:
            if row.get("primary_role", "").upper() != role:
                continue
            status = row.get("status", "").upper()
            if status in {"REJECTED", "DUPLICATE"}:
                continue
            filename = row.get("filename") or row.get("stored_relative_path") or ""
            if filename:
                found.add(filename)
    return sorted(found, key=str.casefold)


def active_style_calibration_summary(pack: Path) -> dict[str, object]:
    path = pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "CALIBRATIONS" / "ACTIVE_STYLE_CALIBRATION.json"
    if not path.is_file():
        return {"status": "NOT_APPLIED", "path": ""}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "INVALID", "path": str(path)}
    if not isinstance(state, dict):
        return {"status": "INVALID", "path": str(path)}
    application = state.get("style_application", {})
    return {
        "status": "ACTIVE" if state.get("status") == "FINALIZED" else "INVALID",
        "path": str(path),
        "calibration_id": state.get("calibration_id", ""),
        "protocol_version": int(state.get("protocol_version", 1) or 1),
        "applied_at": application.get("applied_at", "") if isinstance(application, dict) else "",
        "maximum_explicit_user_percentage": (
            application.get("maximum_explicit_user_percentage") if isinstance(application, dict) else None
        ),
        "user_confirmed_100": bool(application.get("user_confirmed_100")) if isinstance(application, dict) else False,
    }


def build_style_context(paths: StylePaths, requested_role: str, positive_only: bool, include_files: bool) -> dict[str, object]:
    discovered = matching_discovered_style(paths)
    if discovered is None:
        raise StylePackError(f"No discovered style pack matches {paths.style_name}.")
    pack = Path(discovered.pack_path)
    records: list[dict[str, object]] = []
    layer_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    positive_local_role_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    file_type_counts: dict[str, int] = {}

    for file in sorted((path for path in pack.rglob("*") if path.is_file()), key=lambda path: str(path).casefold()):
        relative = file.relative_to(pack)
        layer = relative.parts[0] if relative.parts else "ROOT"
        is_image = file.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        file_type = "IMAGE" if is_image else "DOCUMENT"
        roles = inferred_asset_roles(relative)
        status, positive_eligible = local_asset_status(relative, is_image)
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
        file_type_counts[file_type] = file_type_counts.get(file_type, 0) + 1
        for role in roles:
            role_counts[role] = role_counts.get(role, 0) + 1
            if positive_eligible and layer != "03_UPLOAD_TO_WEB":
                positive_local_role_counts[role] = positive_local_role_counts.get(role, 0) + 1
        if requested_role != "ALL" and requested_role not in roles:
            continue
        if positive_only and not positive_eligible:
            continue
        record: dict[str, object] = {
            "relative_path": relative.as_posix(),
            "absolute_path": str(file),
            "layer": layer,
            "file_type": file_type,
            "roles": roles,
            "local_status": status,
            "positive_eligible": positive_eligible,
            "bytes": file.stat().st_size,
        }
        if is_image:
            image_format, width, height = image_info(file)
            record.update({"format": image_format, "width": width, "height": height})
        records.append(record)

    anchor_files = manifest_role_files(pack, "ANCHOR_STYLE")
    master_files = manifest_role_files(pack, "MASTER_STYLE")
    work_collection_counts: dict[str, int] = {}
    work_root = pack / "01_WORK"
    if work_root.is_dir():
        for child in sorted(work_root.iterdir(), key=lambda path: path.name.casefold()):
            if child.is_dir():
                image_count = count_images(child)
                if image_count:
                    work_collection_counts[child.name] = image_count
    style_pool_count = work_collection_counts.get("STYLE_CROPS", 0) or len(master_files) or positive_local_role_counts.get("STYLE", 0)
    review_pool_counts = {
        "STYLE": style_pool_count,
        "FACE": work_collection_counts.get("FACE_CROPS", 0) or positive_local_role_counts.get("FACE", 0),
        "BODY": work_collection_counts.get("BODY_CROPS", 0) or positive_local_role_counts.get("BODY", 0),
        "POSE": work_collection_counts.get("POSE_CROPS", 0) or positive_local_role_counts.get("POSE", 0),
        "CLOTHES": work_collection_counts.get("CLOTHES_CROPS", 0) or positive_local_role_counts.get("CLOTHES", 0),
        "LIGHTING": work_collection_counts.get("LIGHTING_CROPS", 0) or positive_local_role_counts.get("LIGHTING", 0) or style_pool_count,
        "BACKGROUND": work_collection_counts.get("BACKGROUND_CROPS", 0) or positive_local_role_counts.get("BACKGROUND", 0) or style_pool_count,
        "COMPOSITION": work_collection_counts.get("COMPOSITION_CROPS", 0) or positive_local_role_counts.get("COMPOSITION", 0) or style_pool_count,
        "SUBJECT": work_collection_counts.get("SUBJECT_CROPS", 0) or positive_local_role_counts.get("SUBJECT", 0),
    }
    warnings: list[str] = []
    if not anchor_files:
        warnings.append(
            "No explicit ANCHOR_STYLE is recorded. Keep requested fidelity and select a local MASTER_STYLE plus role-specific local references."
        )
    positive_face_count = sum(
        1
        for record in records
        if "FACE" in record.get("roles", []) and bool(record.get("positive_eligible"))
    ) if requested_role in {"ALL", "FACE"} else role_counts.get("FACE", 0)
    if role_counts.get("FACE", 0) == 0:
        warnings.append("No inferred local face assets were found.")
    context: dict[str, object] = {
        "style_name": discovered.style_name,
        "pack_path": discovered.pack_path,
        "management": discovered.management,
        "local_readiness": discovered.local_readiness,
        "web_readiness": discovered.web_readiness,
        "local_files_total": sum(layer_counts.values()),
        "layer_counts": dict(sorted(layer_counts.items())),
        "file_type_counts": dict(sorted(file_type_counts.items())),
        "work_collection_counts": work_collection_counts,
        "role_counts": dict(sorted(role_counts.items())),
        "positive_local_role_counts": dict(sorted(positive_local_role_counts.items())),
        "review_pool_counts": review_pool_counts,
        "status_counts": dict(sorted(status_counts.items())),
        "explicit_anchor_files": anchor_files,
        "manifest_master_style_files": master_files,
        "requested_role": requested_role,
        "matching_files": len(records),
        "positive_face_candidates_in_scope": positive_face_count,
        "warnings": warnings,
        "active_style_calibration": active_style_calibration_summary(pack),
    }
    if include_files:
        context["files"] = records
    return context


def command_style_context(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    context = build_style_context(paths, args.role, args.positive_only, args.include_files)
    if args.json:
        print(json.dumps(context, ensure_ascii=False, indent=2))
        return
    print(f"STYLE={context['style_name']}")
    print(f"PACK={context['pack_path']}")
    print(f"LOCAL_READINESS={context['local_readiness']}")
    print(f"WEB_READINESS={context['web_readiness']}")
    print(f"LOCAL_FILES_TOTAL={context['local_files_total']}")
    print(f"WORK_COLLECTION_COUNTS={json.dumps(context['work_collection_counts'], ensure_ascii=False)}")
    print(f"ROLE_COUNTS={json.dumps(context['role_counts'], ensure_ascii=False)}")
    print(f"REVIEW_POOL_COUNTS={json.dumps(context['review_pool_counts'], ensure_ascii=False)}")
    print(f"STATUS_COUNTS={json.dumps(context['status_counts'], ensure_ascii=False)}")
    print(f"EXPLICIT_ANCHORS={len(context['explicit_anchor_files'])}")
    print(f"MATCHING_FILES={context['matching_files']}")
    for warning in context["warnings"]:
        print(f"WARNING={warning}")
    if args.include_files:
        for record in context.get("files", []):
            print(
                f"FILE={record['absolute_path']} | STATUS={record['local_status']} | "
                f"ROLES={','.join(record['roles'])}"
            )
    print("STATUS=STYLE_CONTEXT_COMPLETE")


def load_body_reference_manifest(workspace: Path) -> tuple[Path, list[dict[str, str]]]:
    library = workspace.resolve() / BODY_LIBRARY_NAME
    manifest = library / "BODY_REFERENCE_MANIFEST.csv"
    if not manifest.is_file():
        raise StylePackError(
            f"Body reference library is unavailable: {manifest}. Build and review it before selecting auxiliary references."
        )
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise StylePackError(f"Body reference manifest is empty: {manifest}")
    return library, rows


def command_body_ref_context(args: argparse.Namespace) -> None:
    library, rows = load_body_reference_manifest(args.workspace)
    requested_roles = {value.upper() for value in args.allowed_role}
    filters = {
        "primary_family": args.family,
        "pose": args.pose,
        "view": args.view,
        "camera_angle": args.camera_angle,
        "body_build": args.body_build,
        "source_medium": args.source_medium,
    }

    def matches(row: dict[str, str]) -> bool:
        if args.generator_safe_only and row.get("generator_safe", "").upper() != "YES":
            return False
        allowed = {item for item in row.get("allowed_roles", "").upper().split(";") if item}
        if requested_roles and not requested_roles.issubset(allowed):
            return False
        for key, wanted in filters.items():
            if wanted and wanted.upper() not in row.get(key, "").upper():
                return False
        if args.interaction:
            interaction = " ".join(
                (row.get("clothing_interaction", ""), row.get("object_interaction", ""), row.get("contact_points", ""))
            ).upper()
            if args.interaction.upper() not in interaction:
                return False
        return True

    selected = [row for row in rows if matches(row)]
    safe_count = sum(row.get("generator_safe", "").upper() == "YES" for row in rows)
    result: dict[str, object] = {
        "library_path": str(library),
        "manifest": str(library / "BODY_REFERENCE_MANIFEST.csv"),
        "total_references": len(rows),
        "generator_safe": safe_count,
        "mask_required": sum(row.get("safety_status", "").upper() == "MASK_REQUIRED" for row in rows),
        "style_influence": "FORBIDDEN",
        "matching_references": len(selected),
        "filters": {key: value for key, value in filters.items() if value},
        "allowed_roles": sorted(requested_roles),
    }
    if args.include_files:
        result["references"] = [
            {
                **row,
                "generator_absolute_path": str((library / row["generator_path"]).resolve()) if row.get("generator_path") else "",
            }
            for row in selected
        ]
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"LIBRARY={library}")
    print(f"TOTAL_REFERENCES={len(rows)}")
    print(f"GENERATOR_SAFE={safe_count}")
    print(f"MASK_REQUIRED={result['mask_required']}")
    print(f"MATCHING_REFERENCES={len(selected)}")
    print("STYLE_INFLUENCE=FORBIDDEN")
    if args.include_files:
        for row in selected:
            print(
                f"REF={row['ref_id']} | MODE_ROLES={row['allowed_roles']} | "
                f"POSE={row['pose']} | VIEW={row['view']} | PATH={row.get('generator_path', '')}"
            )
    print("STATUS=BODY_REFERENCE_CONTEXT_COMPLETE")


PLAN_CATEGORIES = ("FACE", "BODY", "POSE", "CLOTHES", "LIGHTING", "BACKGROUND", "COMPOSITION")
REVIEW_CATEGORIES = ("STYLE", "SUBJECT", *PLAN_CATEGORIES)
STARTUP_CHOICES = ("OPTION_1", "OPTION_2", "OPTION_3", "CUSTOM")
RISK_LABEL_RE = re.compile(r"^D(?:[1-9]|10)$", re.IGNORECASE)


def parse_startup_interaction(args: argparse.Namespace) -> dict[str, object]:
    selection_mode = args.startup_selection_mode.upper()
    if selection_mode == "REUSE":
        if not args.reuse_startup_from:
            raise StylePackError("REUSE requires --reuse-startup-from pointing to the previous same-chat REFERENCE_PLAN.json.")
        source_path = Path(args.reuse_startup_from).resolve()
        if not source_path.is_file():
            raise StylePackError(f"Reused startup plan does not exist: {source_path}")
        try:
            source_plan = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StylePackError(f"Cannot read reused startup plan: {error}") from error
        if style_slug(str(source_plan.get("style_name", ""))) != style_slug(args.style_name):
            raise StylePackError("A same-chat startup selection may be reused only for the same selected style.")
        source_startup = source_plan.get("startup_parameter_selection")
        if not isinstance(source_startup, dict) or source_startup.get("selected") not in STARTUP_CHOICES:
            raise StylePackError("The source plan has no reusable schema-5 startup selection.")
        resolved = source_startup.get("resolved_parameters")
        if not isinstance(resolved, dict):
            raise StylePackError("The source plan has no reusable resolved startup parameters.")
        profile_changed = (
            int(resolved.get("fidelity", -1)) != args.fidelity
            or str(resolved.get("aux_body_decision", "")).upper() != args.aux_body_decision.upper()
        )
        if profile_changed and not args.user_requested_reselection:
            raise StylePackError("REUSE must preserve the previous profile unless the user explicitly requests reselection.")
        if profile_changed and not args.startup_choice_user_quote.strip():
            raise StylePackError("A reused profile change requires the user's exact correction in --startup-choice-user-quote.")
        reused = json.loads(json.dumps(source_startup, ensure_ascii=False))
        if profile_changed:
            reused["resolved_parameters"] = {
                **resolved,
                "fidelity": args.fidelity,
                "aux_body_decision": args.aux_body_decision.upper(),
            }
        reused.update({
            "selection_state": "REUSED_WITH_USER_RESELECTION" if profile_changed else "REUSED_IN_SAME_CHAT",
            "reused_from_plan": str(source_path),
            "menu_presented_this_turn": False,
            "menu_surface_this_turn": "NOT_PRESENTED_REUSED_SELECTION",
            "user_requested_reselection": bool(profile_changed),
        })
        if profile_changed:
            reused["user_choice_quote"] = args.startup_choice_user_quote.strip()
            reused["parameter_source"] = "DIRECT_USER_CORRECTION_IN_SAME_CHAT"
        return reused

    if selection_mode == "AUTO_DEFAULT":
        raise StylePackError(
            "AUTO_DEFAULT is forbidden for new plans. Print the three-presets-plus-CUSTOM text menu when the native choice control is unavailable."
        )

    if selection_mode == "USER_CONFIRMATION":
        if args.startup_menu_surface != "TEXT_NUMBERED_MENU":
            raise StylePackError(
                "USER_CONFIRMATION requires --startup-menu-surface TEXT_NUMBERED_MENU."
            )
        choice = args.startup_choice.upper()
        if choice not in STARTUP_CHOICES:
            raise StylePackError("USER_CONFIRMATION requires OPTION_1, OPTION_2, OPTION_3, or CUSTOM.")
        user_quote = args.startup_choice_user_quote.strip()
        if not user_quote:
            raise StylePackError("USER_CONFIRMATION requires the user's explicit reply in --startup-choice-user-quote.")
        options: dict[str, str] = {}
        for value in args.startup_option:
            if "=" not in value:
                raise StylePackError("--startup-option must use OPTION_N=description.")
            option_id, description = value.split("=", 1)
            option_id = option_id.strip().upper()
            description = description.strip()
            if option_id not in STARTUP_CHOICES[:3] or not description:
                raise StylePackError("Startup presets must be non-empty OPTION_1, OPTION_2, and OPTION_3 entries.")
            if option_id in options:
                raise StylePackError(f"Duplicate startup option: {option_id}")
            options[option_id] = description
        if set(options) != set(STARTUP_CHOICES[:3]):
            raise StylePackError("The Default-mode text menu must contain exactly three AI presets before CUSTOM.")
        recommended_text = options["OPTION_1"]
        library_named = re.search(r"BODY_REFERENCE_LIBRARY|библиотек", recommended_text, flags=re.IGNORECASE)
        library_enabled = re.search(r"подключ|включ|использ|\bYES\b|\bда\b", recommended_text, flags=re.IGNORECASE)
        library_disabled = re.search(
            r"не\s+(?:подключ|включ|использ)|без\s+(?:BODY_REFERENCE_LIBRARY|библиотек)",
            recommended_text,
            flags=re.IGNORECASE,
        )
        if (
            not re.search(r"(?<!\d)90\s*%?(?!\d)", recommended_text)
            or not library_named
            or not library_enabled
            or library_disabled
        ):
            raise StylePackError("OPTION_1 must be the recommended 90% preset with BODY_REFERENCE_LIBRARY enabled.")
        if choice == "OPTION_1" and (args.fidelity != 90 or args.aux_body_decision.upper() != "SELECTED"):
            raise StylePackError("Confirmed OPTION_1 must resolve to fidelity=90 and aux-body-decision=SELECTED.")
        custom_quote = args.custom_parameters_user_quote.strip()
        if choice == "CUSTOM":
            if not custom_quote:
                raise StylePackError(
                    "Confirmed CUSTOM requires the user's complete reply in --custom-parameters-user-quote."
                )
            if not re.search(rf"(?<!\d){args.fidelity}\s*%?(?!\d)", custom_quote):
                raise StylePackError(
                    f"The confirmed custom answer must contain the selected fidelity value {args.fidelity}."
                )
        elif custom_quote:
            raise StylePackError("--custom-parameters-user-quote is valid only when CUSTOM was selected.")
        return {
            "menu_contract": "THREE_AI_PRESETS_PLUS_CUSTOM",
            "options": [
                {"id": key, "description": options[key], "recommended": key == "OPTION_1"}
                for key in STARTUP_CHOICES[:3]
            ]
            + [{"id": "CUSTOM", "description": "Указать свой вариант"}],
            "selected": choice,
            "selection_state": "USER_CONFIRMED_FROM_TEXT_MENU",
            "menu_presented_this_turn": True,
            "menu_surface_this_turn": "TEXT_NUMBERED_MENU",
            "user_requested_reselection": args.user_requested_reselection,
            "user_choice_quote": user_quote,
            "parameter_source": "USER_CONFIRMATION_FROM_TEXT_MENU",
            "custom_parameters_user_quote": custom_quote,
            "custom_description_treated_as_complete": choice == "CUSTOM",
            "follow_up_allowed_only_for_genuinely_missing_required_information": True,
            "optional_follow_up_questions_forbidden": choice == "CUSTOM",
            "numeric_values_were_not_requested_before_menu_choice": True,
        }

    if selection_mode != "NEW":
        raise StylePackError(f"Unknown startup selection mode: {selection_mode}")
    if args.startup_menu_surface != "NATIVE_CONTEXT_MENU":
        raise StylePackError(
            "NEW is reserved for the native Plan-mode menu; use USER_CONFIRMATION with TEXT_NUMBERED_MENU in Default mode."
        )
    choice = args.startup_choice.upper()
    if choice not in STARTUP_CHOICES:
        raise StylePackError("NEW selection requires --startup-choice OPTION_1, OPTION_2, OPTION_3, or CUSTOM.")
    user_quote = args.startup_choice_user_quote.strip()
    if not user_quote:
        raise StylePackError("Record the user's startup-menu choice with --startup-choice-user-quote.")
    options: dict[str, str] = {}
    for value in args.startup_option:
        if "=" not in value:
            raise StylePackError("--startup-option must use OPTION_N=description.")
        option_id, description = value.split("=", 1)
        option_id = option_id.strip().upper()
        description = description.strip()
        if option_id not in STARTUP_CHOICES[:3] or not description:
            raise StylePackError("Startup presets must be non-empty OPTION_1, OPTION_2, and OPTION_3 entries.")
        if option_id in options:
            raise StylePackError(f"Duplicate startup option: {option_id}")
        options[option_id] = description
    if set(options) != set(STARTUP_CHOICES[:3]):
        raise StylePackError("Present and record exactly three AI-proposed presets before the CUSTOM option.")
    recommended_text = options["OPTION_1"]
    library_named = re.search(r"BODY_REFERENCE_LIBRARY|библиотек", recommended_text, flags=re.IGNORECASE)
    library_enabled = re.search(r"подключ|включ|использ|\bYES\b|\bда\b", recommended_text, flags=re.IGNORECASE)
    library_disabled = re.search(r"не\s+(?:подключ|включ|использ)|без\s+(?:BODY_REFERENCE_LIBRARY|библиотек)", recommended_text, flags=re.IGNORECASE)
    if (
        not re.search(r"(?<!\d)90\s*%?(?!\d)", recommended_text)
        or not library_named
        or not library_enabled
        or library_disabled
    ):
        raise StylePackError("OPTION_1 must be the recommended 90% preset with BODY_REFERENCE_LIBRARY enabled.")
    if choice == "OPTION_1" and (args.fidelity != 90 or args.aux_body_decision.upper() != "SELECTED"):
        raise StylePackError("Selecting OPTION_1 must resolve to fidelity=90 and aux-body-decision=SELECTED.")
    custom_quote = args.custom_parameters_user_quote.strip()
    if choice == "CUSTOM":
        if not custom_quote:
            raise StylePackError(
                "CUSTOM was selected. Record the user's complete one-message description with "
                "--custom-parameters-user-quote; ask a follow-up only if a required value is genuinely missing."
            )
        if not re.search(rf"(?<!\d){args.fidelity}\s*%?(?!\d)", custom_quote):
            raise StylePackError(
                f"The custom-parameter answer must contain the selected fidelity value {args.fidelity}."
            )
    elif custom_quote:
        raise StylePackError("--custom-parameters-user-quote is valid only when CUSTOM was selected.")
    return {
        "menu_contract": "THREE_AI_PRESETS_PLUS_CUSTOM",
        "options": [
            {"id": key, "description": options[key], "recommended": key == "OPTION_1"}
            for key in STARTUP_CHOICES[:3]
        ]
        + [{"id": "CUSTOM", "description": "Указать свой вариант"}],
        "selected": choice,
        "selection_state": "NEW_SELECTION",
        "menu_presented_this_turn": True,
        "menu_surface_this_turn": args.startup_menu_surface,
        "user_requested_reselection": args.user_requested_reselection,
        "user_choice_quote": user_quote,
        "parameter_source": "USER_CUSTOM" if choice == "CUSTOM" else "AI_PRESET_SELECTED_BY_USER",
        "custom_parameters_user_quote": custom_quote,
        "custom_description_treated_as_complete": choice == "CUSTOM",
        "follow_up_allowed_only_for_genuinely_missing_required_information": True,
        "optional_follow_up_questions_forbidden": choice == "CUSTOM",
        "numeric_values_were_not_requested_before_menu_choice": True,
    }


def iter_selected_reference_records(selected: dict[str, object]) -> Iterable[dict[str, object]]:
    for value in selected.values():
        if isinstance(value, dict) and value.get("path"):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("path"):
                    yield item


def load_and_validate_risk_assessment(
    value: str,
    selected: dict[str, object],
    auxiliary: Sequence[dict[str, object]],
) -> tuple[Path, dict[str, object]]:
    path = Path(value).resolve()
    if not path.is_file():
        raise StylePackError(f"Risk assessment does not exist: {path}")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read risk assessment: {error}") from error
    if report.get("schema_version") != 1 or not RISK_LABEL_RE.fullmatch(str(report.get("generation_risk", ""))):
        raise StylePackError("Risk assessment must use schema 1 and contain generation_risk D1-D10.")
    prompt = report.get("revised_prompt") or report.get("original_prompt")
    if not isinstance(prompt, dict) or not RISK_LABEL_RE.fullmatch(str(prompt.get("risk", ""))):
        raise StylePackError("Risk assessment has no analyzed prompt with a D1-D10 result.")
    reference_rows = report.get("references")
    if not isinstance(reference_rows, list):
        raise StylePackError("Risk assessment has no reference list.")
    by_hash = {
        str(row.get("sha256", "")): row
        for row in reference_rows
        if isinstance(row, dict) and RISK_LABEL_RE.fullmatch(str(row.get("content_and_reference_risk", "")))
    }
    planned = list(iter_selected_reference_records(selected)) + list(auxiliary)
    missing = [str(item.get("path", "")) for item in planned if str(item.get("sha256", "")) not in by_hash]
    if missing:
        raise StylePackError(
            "Every physically selected visual reference needs a D1-D10 assessment covering both image content and "
            "its influence as a reference. Missing: " + "; ".join(missing)
        )
    return path, report


def load_style_calibration_evidence(
    value: str,
    *,
    required: bool,
    style_name: str,
    pack_path: str = "",
) -> dict[str, object]:
    """Validate a finalized multi-face calibration before production generation."""

    resolved_from_active_profile = False
    if not value and pack_path:
        active_path = (
            Path(pack_path).resolve()
            / "02_LOCAL_ONLY_DO_NOT_UPLOAD"
            / "CALIBRATIONS"
            / "ACTIVE_STYLE_CALIBRATION.json"
        )
        if active_path.is_file():
            value = str(active_path)
            resolved_from_active_profile = True
    if not value:
        if required:
            raise StylePackError(
                "Style calibration is required for this request. Finish at least 2x4 (preferred 4x4) and pass "
                "--style-calibration-state pointing to its finalized state."
            )
        return {
            "required": False,
            "status": "NOT_REQUIRED",
            "path": "",
        }
    path = Path(value).resolve()
    if not path.is_file():
        raise StylePackError(f"Style calibration state does not exist: {path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read style calibration state: {error}") from error
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise StylePackError("Style calibration state must use schema_version 1.")
    if str(state.get("style_name", "")).strip().casefold() != style_name.strip().casefold():
        raise StylePackError("Style calibration belongs to a different style pack.")
    if state.get("status") != "FINALIZED" or state.get("next_required_action") != "NONE":
        raise StylePackError("Style calibration is incomplete; production remains paused until FINALIZED.")
    rounds = state.get("rounds")
    conclusion = state.get("final_conclusion")
    if not isinstance(rounds, list) or len(rounds) < 2 or not isinstance(conclusion, dict):
        raise StylePackError("Finalized style calibration must contain at least two scored quartets and an AI conclusion.")
    completed_rounds = int(conclusion.get("completed_rounds", 0) or 0)
    if completed_rounds < 2 or int(state.get("batch_size", 0) or 0) != 4:
        raise StylePackError("Style calibration evidence must contain at least 2x4 scored candidates.")
    target_rounds = int(state.get("target_rounds", 4) or 4)
    if target_rounds < 4 and not state.get("reduced_rounds_user_approved"):
        raise StylePackError("A two- or three-round calibration has no recorded direct user approval.")
    if completed_rounds < target_rounds and not (
        conclusion.get("early_stop") and conclusion.get("early_stop_user_approved")
    ):
        raise StylePackError("Early calibration finalization has no recorded direct user approval.")
    if completed_rounds != len(rounds):
        raise StylePackError("Finalized calibration round count does not match its conclusion.")
    protocol = int(state.get("protocol_version", 1) or 1)
    if protocol >= 3 and int(state.get("arts_per_round", 0) or 0) != 1:
        raise StylePackError("Protocol-v3 calibration must contain exactly one composite art per round.")
    seen_faces: set[str] = set()
    for index, item in enumerate(rounds, 1):
        if not isinstance(item, dict):
            raise StylePackError("Style calibration contains an invalid round record.")
        face_id = str(item.get("face_id", "")).strip().upper()
        if not face_id or face_id in seen_faces:
            raise StylePackError("Every calibration quartet must use one new temporary subject.")
        seen_faces.add(face_id)
        candidates = item.get("candidates")
        feedback = item.get("feedback")
        if (
            not isinstance(candidates, list)
            or len(candidates) != 4
            or not all(isinstance(candidate, dict) for candidate in candidates)
            or not isinstance(feedback, dict)
        ):
            raise StylePackError("Every finalized calibration round must contain four scored candidates.")
        if any(candidate.get("user_score") is None for candidate in candidates):
            raise StylePackError("Every finalized quartet needs all four panels scored by the user.")
        hashes = {str(candidate.get("image_sha256", "")) for candidate in candidates if isinstance(candidate, dict)}
        if len(hashes) != 4 or "" in hashes:
            raise StylePackError("Every finalized quartet needs four distinct panel identities.")
        if protocol >= 3:
            quartet_path = str(item.get("quartet_art_path", "")).strip()
            quartet_hash = str(item.get("quartet_art_sha256", "")).strip()
            panel_labels = [str(candidate.get("panel_label", "")).strip().upper() for candidate in candidates]
            source_paths = {str(candidate.get("image_path", "")).strip() for candidate in candidates}
            source_hashes = {str(candidate.get("source_art_sha256", "")).strip() for candidate in candidates}
            if (
                not quartet_path
                or not quartet_hash
                or panel_labels != ["A", "B", "C", "D"]
                or source_paths != {quartet_path}
                or source_hashes != {quartet_hash}
            ):
                raise StylePackError(
                    "Protocol-v3 quartet must source panels A/B/C/D from one generated composite art, not four outputs."
                )
        if feedback.get("mode") not in {"PERCENT", "MIN_TO_MAX"}:
            raise StylePackError("Calibration feedback must use PERCENT or MIN_TO_MAX.")
        if protocol >= 2:
            distinction = item.get("distinction_qa")
            if not isinstance(distinction, dict) or distinction.get("review_status") != "PASS":
                raise StylePackError("Protocol-v2 calibration requires passed distinction QA for every quartet.")
            if float(distinction.get("minimum_visible_delta_percent", 0) or 0) < 10:
                raise StylePackError("Protocol-v2 quartet has less than 10% verified visual distance.")
            if distinction.get("decision") != "SHOW_TO_USER" or distinction.get("collapsed_pairs"):
                raise StylePackError("Collapsed or hidden protocol-v2 quartet cannot be production evidence.")
        if index < len(rounds) and not isinstance(item.get("ai_adaptation"), dict):
            raise StylePackError("Every completed quartet before the last needs an AI adaptation record.")
    if protocol >= 2:
        final_style_prompt = " ".join(str(conclusion.get("final_style_prompt", "")).split())
        excluded_noise = conclusion.get("excluded_style_noise")
        prompt_vocabulary = conclusion.get("prompt_vocabulary")
        prompt_evidence = conclusion.get("prompt_evidence_by_round")
        if not final_style_prompt or len(final_style_prompt) > 1200:
            raise StylePackError("Protocol-v2 calibration needs a concise final_style_prompt of at most 1200 characters.")
        if not isinstance(excluded_noise, list) or not excluded_noise:
            raise StylePackError("Protocol-v2 calibration needs a non-empty excluded_style_noise list.")
        if not isinstance(prompt_vocabulary, dict):
            raise StylePackError("Protocol-v2 calibration needs prompt_vocabulary evidence from the four prompt variants.")
        if not isinstance(prompt_evidence, list) or len(prompt_evidence) != completed_rounds:
            raise StylePackError("Protocol-v2 calibration needs prompt evidence for every completed quartet.")
    return {
        "required": required,
        "status": "FINALIZED",
        "path": str(path),
        "resolved_from_active_profile": resolved_from_active_profile,
        "calibration_id": state.get("calibration_id", ""),
        "completed_rounds": completed_rounds,
        "batch_size": 4,
        "feedback_modes": sorted({
            str(item.get("feedback", {}).get("mode", ""))
            for item in rounds
            if isinstance(item, dict) and isinstance(item.get("feedback"), dict)
        }),
        "final_conclusion": conclusion,
    }


def parse_reviewed_counts(values: Sequence[str], legacy_face_count: int = 0) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise StylePackError("--reviewed must use ROLE=COUNT, for example FACE=105.")
        role_text, count_text = value.split("=", 1)
        role = role_text.strip().upper()
        if role not in REVIEW_CATEGORIES:
            raise StylePackError(f"Unknown reviewed role {role}; choose from {', '.join(REVIEW_CATEGORIES)}.")
        try:
            count = int(count_text.strip())
        except ValueError as error:
            raise StylePackError(f"Reviewed count for {role} must be an integer.") from error
        if count < 0:
            raise StylePackError(f"Reviewed count for {role} cannot be negative.")
        counts[role] = max(counts.get(role, 0), count)
    if legacy_face_count:
        counts["FACE"] = max(counts.get("FACE", 0), legacy_face_count)
    return counts


def validate_prompt_only_body_library_review(args: argparse.Namespace) -> None:
    """Require complete relevant BODY library review before dropping a selected visual source."""
    if not getattr(args, "prompt_only_physique", False):
        return
    if str(getattr(args, "aux_body_decision", "")).upper() != "SELECTED":
        return
    reviewed = int(getattr(args, "body_library_candidates_reviewed", 0))
    total = int(getattr(args, "body_library_relevant_candidates_total", 0))
    if total <= 0:
        raise StylePackError(
            "Prompt-only physique with BODY_REFERENCE_LIBRARY selected requires a positive "
            "--body-library-relevant-candidates-total after querying and visually inspecting the relevant real-photo pool."
        )
    if reviewed != total:
        raise StylePackError(
            "Prompt-only physique is premature: visually review every relevant BODY_REFERENCE_LIBRARY candidate first "
            f"(reviewed {reviewed} of {total}). A real photo may be unsuitable for this role because of framing, "
            "occlusion, pose, view, or build; do not label the photographed body anatomically wrong."
        )


def validate_plan_reference(
    paths: StylePaths,
    value: str,
    label: str,
    *,
    allow_curated_body_contour: bool = False,
) -> dict[str, object]:
    file = resolve_existing_file(value, paths)
    contour_root = (
        paths.workspace
        / "BODY_REFERENCE_LIBRARY"
        / "02_LOCAL_ONLY"
        / "ANATOMY_CONTOUR_REFERENCE_LIBRARY"
        / "07_FINAL_CURATED"
    ).resolve()
    is_curated_body_contour = (
        allow_curated_body_contour
        and label == "POSE"
        and is_relative_to(file, contour_root)
    )
    if not (
        is_relative_to(file, paths.pack)
        or is_relative_to(file, paths.generations)
        or is_curated_body_contour
    ):
        raise StylePackError(f"{label} must come from the local style pack or its approved character library: {file}")
    status = "APPROVED_CHARACTER_ASSET"
    roles: list[str] = []
    if is_curated_body_contour:
        status = "FINAL_CURATED_BODY_CONTOUR"
        roles = ["POSE", "BODY_CONTOUR"]
    elif is_relative_to(file, paths.pack):
        relative = file.relative_to(paths.pack)
        status, _ = local_asset_status(relative, file.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS)
        roles = inferred_asset_roles(relative)
        if status in {"REJECTED", "REVIEW_ONLY", "DERIVED_GENERATION"}:
            raise StylePackError(f"{label} cannot use {status} as a positive reference: {file}")
        if style_formation_status(paths) != "FORMED":
            raise StylePackError(
                f"{label} cannot use an unformed style-pack source as an explicit planning reference; use a registered successful QA-passed generation."
            )
    else:
        generation = registered_approved_generation(paths, file)
        if generation is None:
            raise StylePackError(
                f"{label} cannot use an unregistered, rejected, staging, or non-approved generation as a positive reference: {file}"
            )
        status = str(generation["status"])
    return {
        "path": str(file),
        "sha256": sha256(file),
        "status": status,
        "inferred_roles": roles,
    }


def generation_row_matches_image(row: dict[str, str], image: Path) -> bool:
    """Match a manifest row to an image by content hash across existing copies."""
    image_hash = sha256(image)
    for field in ("source_image", "archive_file", "style_file"):
        candidate_text = row.get(field, "")
        if not candidate_text:
            continue
        candidate = Path(candidate_text)
        if candidate.is_file() and sha256(candidate) == image_hash:
            return True
    return False


def generation_row_hashes(row: dict[str, str]) -> set[str]:
    hashes: set[str] = set()
    for field in ("source_image", "archive_file", "style_file"):
        candidate = Path(row.get(field, ""))
        if candidate.is_file():
            hashes.add(sha256(candidate))
    return hashes


def newest_matching_generation(paths: StylePaths, image: Path) -> dict[str, str] | None:
    """Resolve the one authoritative (latest) manifest status for an image."""
    for row in reversed(read_csv(paths.generation_manifest)):
        if generation_row_matches_image(row, image):
            return row
    return None


def generation_qa_evidence(row: dict[str, str]) -> dict[str, object] | None:
    """Validate the durable record-generation receipt and its QA contract snapshot."""
    evidence_path = Path(row.get("qa_evidence", ""))
    style_file = Path(row.get("style_file", ""))
    expected_evidence = style_file.with_suffix(style_file.suffix + ".qa-evidence.json")
    if not style_file.is_file() or evidence_path != expected_evidence or not evidence_path.is_file():
        return None
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 2:
        return None
    output_hash = evidence.get("output_sha256")
    contract_path = Path(str(evidence.get("qa_contract", "")))
    expected_contract = style_file.with_suffix(style_file.suffix + ".qa-contract.json")
    if (
        not isinstance(output_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", output_hash, flags=re.IGNORECASE)
        or output_hash.casefold() != sha256(style_file)
        or row.get("qa_output_sha256", "").casefold() != output_hash.casefold()
        or row.get("qa_receipt_sha256", "").casefold() != sha256(evidence_path)
        or contract_path != expected_contract
        or not contract_path.is_file()
        or evidence.get("qa_contract_sha256") != sha256(contract_path)
        or row.get("qa_contract_sha256", "").casefold() != sha256(contract_path)
    ):
        return None
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    plan_snapshot = style_file.with_suffix(style_file.suffix + ".qa-plan.json")
    expected_layers = contract.get("expected_qa_layers") if isinstance(contract, dict) else None
    qa_results = contract.get("qa_results") if isinstance(contract, dict) else None
    if (
        not isinstance(contract, dict)
        or contract.get("schema_version") != 1
        or contract.get("contract_version") != 1
        or Path(str(contract.get("plan_snapshot", ""))) != plan_snapshot
        or not plan_snapshot.is_file()
        or contract.get("plan_content_sha256") != sha256(plan_snapshot)
        or row.get("qa_plan_sha256", "").casefold() != sha256(plan_snapshot)
        or not isinstance(expected_layers, list)
        or not expected_layers
        or len(expected_layers) != len(set(expected_layers))
        or not all(isinstance(layer, str) and layer in QA_LAYER_NAMES for layer in expected_layers)
        or not isinstance(qa_results, dict)
        or set(qa_results) != set(expected_layers)
        or any(result != "PASS" for result in qa_results.values())
    ):
        return None
    return evidence


def generation_has_passed_qa(row: dict[str, str]) -> bool:
    """Only a high-fidelity record-generation QA receipt can establish passed QA."""
    evidence = generation_qa_evidence(row)
    return bool(
        evidence
        and evidence.get("record_status") in {"TEST", "STAGING"}
        and not evidence.get("qa_failed")
    )


def style_formation_status(paths: StylePaths) -> str:
    if not paths.metadata.is_file():
        return "UNKNOWN"
    try:
        metadata = load_metadata(paths)
    except (StylePackError, json.JSONDecodeError):
        return "UNKNOWN"
    return "FORMED" if metadata.get("status") == "FINALIZED_APPROVED" else "UNFORMED"


def registered_approved_generation(paths: StylePaths, image: Path) -> dict[str, str] | None:
    """Return only a hash-verified permanent generation suitable as a positive reference."""
    row = newest_matching_generation(paths, image)
    if row and row.get("status", "").upper().startswith("APPROVED_") and generation_has_passed_qa(row):
        return row
    return None


def require_qa_passed_generation_for_approval(
    paths: StylePaths, image: Path, *, allowed_statuses: set[str] | None = None
) -> dict[str, str]:
    """Promotion may only consume a registered, QA-passed final test result."""
    allowed = allowed_statuses or {"TEST"}
    row = newest_matching_generation(paths, image)
    if row and row.get("status", "").upper() in allowed and generation_has_passed_qa(row):
        return row
    raise StylePackError(
        "Approval requires a hash-verified registered TEST generation with a validated reference plan and passed required QA; REJECTED, STAGING, unregistered, and QA-incomplete images cannot be promoted."
    )


def require_generated_character_reference(paths: StylePaths, image: Path) -> None:
    """Preserve original pack references, but never promote an unverified generated derivative."""
    if is_relative_to(image, paths.generations):
        require_qa_passed_generation_for_approval(paths, image, allowed_statuses={"TEST", "STAGING"})


def copy_qa_snapshot_for_approval(source: dict[str, str], approved_file: Path) -> str:
    """Bind an approved copy to independent receipt, contract, and plan snapshots."""
    source_evidence = Path(source["qa_evidence"])
    receipt = json.loads(source_evidence.read_text(encoding="utf-8"))
    source_contract = Path(str(receipt["qa_contract"]))
    contract = json.loads(source_contract.read_text(encoding="utf-8"))
    source_plan = Path(str(contract["plan_snapshot"]))
    approved_plan = approved_file.with_suffix(approved_file.suffix + ".qa-plan.json")
    approved_contract = approved_file.with_suffix(approved_file.suffix + ".qa-contract.json")
    approved_evidence = approved_file.with_suffix(approved_file.suffix + ".qa-evidence.json")
    shutil.copy2(source_plan, approved_plan)
    contract["plan_snapshot"] = str(approved_plan)
    contract["plan_content_sha256"] = sha256(approved_plan)
    approved_contract.write_text(json.dumps(contract, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    receipt["qa_contract"] = str(approved_contract)
    receipt["qa_contract_sha256"] = sha256(approved_contract)
    receipt["output_sha256"] = sha256(approved_file)
    approved_evidence.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return str(approved_evidence)


def approval_provenance(
    source: dict[str, str], notes: str, approved_file: Path
) -> tuple[str, str, str, dict[str, str], str]:
    """Carry a fresh bound QA snapshot, never a mutable pending-file pointer."""
    parent = source.get("generation_id", "")
    plan = source.get("reference_plan", "")
    combined_notes = " ".join(
        part for part in (notes.strip(), f"[APPROVED_FROM={parent}]" if parent else "", source.get("notes", "")) if part
    )
    evidence = copy_qa_snapshot_for_approval(source, approved_file)
    return parent, plan, evidence, qa_manifest_digests(approved_file, Path(evidence)), combined_notes


def style_readiness_proposal(paths: StylePaths) -> dict[str, object]:
    """Read-only consent proposal; it never starts calibration or writes state."""
    formation = style_formation_status(paths)
    calibration = active_style_calibration_summary(paths.pack)
    calibration_status = "FINALIZED" if calibration.get("status") == "ACTIVE" else str(calibration.get("status", "UNKNOWN"))
    latest_by_hash: dict[str, dict[str, str]] = {}
    if paths.generation_manifest.is_file():
        for row in read_csv(paths.generation_manifest):
            for image_hash in generation_row_hashes(row):
                latest_by_hash[image_hash] = row
    # One recorded output has one evidence-bound output hash.  Manifest copies
    # and re-registrations cannot inflate readiness by adding source/archive/style paths.
    unique_hashes: set[str] = set()
    for row in latest_by_hash.values():
        evidence = generation_qa_evidence(row)
        if (
            evidence
            and row.get("status", "").upper()
            in {"TEST", "APPROVED_STANDALONE", "APPROVED_VARIATION", "APPROVED_SCENE"}
            and generation_has_passed_qa(row)
        ):
            unique_hashes.add(str(evidence["output_sha256"]).casefold())
    propose = len(unique_hashes) >= 5 and formation in {"UNFORMED", "UNKNOWN"} and calibration_status != "FINALIZED"
    return {"style_name": paths.style_name, "style_formation": formation, "calibration_status": calibration_status, "qa_passed_unique_generations": len(unique_hashes), "minimum_unique_generations": 5, "proposal": "CONSENT_REQUIRED_STYLE_CALIBRATION" if propose else "NO_AUTOMATIC_ACTION", "calibration_started": False, "message": "Ask the user whether to formalize and calibrate this style." if propose else "Read-only readiness check only; do not create calibration or test art."}


def parse_aux_body_references(
    workspace: Path,
    values: Sequence[str],
    decision: str,
    selection_note: str,
    is_new_character: bool,
    allow_body_identity_change: bool,
) -> list[dict[str, object]]:
    decision = decision.upper()
    if decision == "DECLINED":
        if values:
            raise StylePackError("Auxiliary body references were declined, but --aux-body values were supplied.")
        return []
    if not values:
        if not selection_note.strip():
            raise StylePackError(
                "Auxiliary body references were selected, but no compatible file was attached. "
                "Supply at least one --aux-body BR_NNNN=MODE or record why none is suitable with "
                "--aux-body-selection-note."
            )
        return []
    library, rows = load_body_reference_manifest(workspace)
    by_id = {row.get("ref_id", "").upper(): row for row in rows}
    selected: list[dict[str, object]] = []
    for value in values:
        if "=" not in value:
            raise StylePackError("--aux-body must use BR_NNNN=MODE, for example BR_0007=STAGING_ONLY.")
        ref_text, mode_text = value.split("=", 1)
        ref_id = ref_text.strip().upper()
        mode = mode_text.strip().upper()
        if mode not in BODY_AUX_MODES:
            raise StylePackError(f"Unknown auxiliary mode {mode}; choose from {', '.join(BODY_AUX_MODES)}.")
        row = by_id.get(ref_id)
        if not row:
            raise StylePackError(f"Unknown body reference id: {ref_id}")
        if row.get("generator_safe", "").upper() != "YES" or not row.get("generator_path"):
            raise StylePackError(f"{ref_id} is not generator-safe; create and register its censored derivative first.")
        required_roles = BODY_AUX_MODES[mode]
        available_roles = {item for item in row.get("allowed_roles", "").upper().split(";") if item}
        missing = sorted(required_roles - available_roles)
        if missing:
            raise StylePackError(f"{ref_id} cannot be used as {mode}; missing roles: {', '.join(missing)}.")
        if mode == "BODY_BUILD_TARGET" and not is_new_character and not allow_body_identity_change:
            raise StylePackError(
                "BODY_BUILD_TARGET would change an existing character's permanent proportions. "
                "Use --allow-body-identity-change only after the user explicitly requests that change."
            )
        file = (library / row["generator_path"]).resolve()
        if not is_relative_to(file, library) or not file.is_file():
            raise StylePackError(f"Generator-safe file is missing or outside the body library: {file}")
        selected.append({
            "ref_id": ref_id,
            "mode": mode,
            "path": str(file),
            "sha256": sha256(file),
            "active_roles": sorted(required_roles),
            "body_build": row.get("body_build", ""),
            "primary_family": row.get("primary_family", ""),
            "pose": row.get("pose", ""),
            "view": row.get("view", ""),
            "camera_angle": row.get("camera_angle", ""),
            "framing": row.get("framing", ""),
            "not_for_roles": [item for item in row.get("not_for_roles", "").upper().split(";") if item],
            "style_influence": "FORBIDDEN",
            "forbidden_transfer": [
                "face", "identity", "hair", "skin_tone", "costume_design", "palette",
                "linework", "rendering_style", "lighting", "background_style", "source_medium",
            ],
        })
    return selected


def normalize_aspect_ratio(value: str) -> str:
    return value.strip().lower().replace("x", ":").replace("к", ":")


def build_scene_contract(
    args: argparse.Namespace,
    purpose: str,
    character_id: str,
) -> dict[str, object]:
    """Resolve scene subject/use while keeping character identity opt-in."""

    if purpose != "SCENE":
        return {"applicable": False}
    if not re.fullmatch(r"CHAR_\d+|NONE", character_id):
        raise StylePackError("SCENE requires --character-id NONE or an approved CHAR_NNN identifier.")

    scene_kind = args.scene_kind.upper()
    output_use = args.scene_output_use.upper()
    text_safe_zone = args.text_safe_zone.upper()
    if scene_kind not in SCENE_KINDS:
        raise StylePackError("SCENE requires --scene-kind LOCATION, PHENOMENON, ARTIFACT, or MIXED.")
    if output_use not in SCENE_OUTPUT_USES:
        raise StylePackError("SCENE requires --scene-output-use GENERAL_ART, WALLPAPER, or PROMO_POSTER.")
    if output_use == "PROMO_POSTER" and text_safe_zone == "NONE":
        raise StylePackError("PROMO_POSTER requires a non-NONE --text-safe-zone for later deterministic typography.")
    if output_use != "PROMO_POSTER" and text_safe_zone != "NONE":
        raise StylePackError("--text-safe-zone is reserved for PROMO_POSTER scenes.")

    has_character = character_id != "NONE"
    if has_character:
        return {
            "applicable": True,
            "scene_kind": scene_kind,
            "output_use": output_use,
            "has_character": True,
            "character_policy": "EXPLICIT_APPROVED_CHARACTER",
            "subject_source": "REFERENCE_PLUS_PROMPT" if getattr(args, "subject_reference", "") else "PROMPT_ONLY",
            "text_safe_zone": text_safe_zone,
            "typography_policy": (
                "RESERVE_COPY_SAFE_AREA; ADD_EXACT_COPY_DETERMINISTICALLY_AFTER_ART_GENERATION"
                if output_use == "PROMO_POSTER"
                else "NO_TEXT_OR_WATERMARK"
            ),
            "crop_policy": (
                "KEEP_FOCAL_SUBJECT_AND_HORIZON_INSIDE_CENTER_CROP_SAFE_AREA_FOR_FILL_FIT_SPAN"
                if output_use == "WALLPAPER"
                else "KEEP_PRIMARY_SUBJECT_AND_COPY_SAFE_AREA_CLEAR_OF_TRIM_EDGES"
                if output_use == "PROMO_POSTER"
                else "PRESERVE_CLEAR_FOCAL_HIERARCHY"
            ),
        }

    forbidden = {
        "character assembly": getattr(args, "character_assembly", ""),
        "primary face": getattr(args, "primary_face", ""),
        "supporting face": getattr(args, "supporting_face", ""),
        "expression reference": getattr(args, "expression_reference", ""),
        "body": getattr(args, "body_reference", ""),
        "pose": getattr(args, "pose_reference", ""),
        "clothes": getattr(args, "clothes_reference", ""),
        "auxiliary body": getattr(args, "aux_body", []),
        "coverage front": getattr(args, "coverage_front_reference", ""),
        "coverage side": getattr(args, "coverage_side_reference", ""),
        "coverage back": getattr(args, "coverage_back_reference", ""),
    }
    present = [name for name, value in forbidden.items() if value]
    if present:
        raise StylePackError(
            "SCENE with --character-id NONE forbids character reference families and CHARACTER_BASE-only coverage assets. "
            "Use an approved CHAR_NNN when character identity, body, pose, or clothes must be attached. "
            "Remove: " + ", ".join(present)
        )
    if not getattr(args, "subject_reference", "") and not getattr(args, "scene_subject_from_prompt", False):
        raise StylePackError(
            "Character-free SCENE requires --subject-reference or explicit --scene-subject-from-prompt."
        )

    return {
        "applicable": True,
        "scene_kind": scene_kind,
        "output_use": output_use,
        "has_character": False,
        "character_policy": "ANONYMOUS_SUBJECT; NO_CHARACTER_REFERENCE_FAMILIES; NO_APPROVED_IDENTITY_LOCK",
        "subject_source": "REFERENCE_PLUS_PROMPT" if getattr(args, "subject_reference", "") else "PROMPT_ONLY",
        "text_safe_zone": text_safe_zone,
        "typography_policy": (
            "RESERVE_COPY_SAFE_AREA; ADD_EXACT_COPY_DETERMINISTICALLY_AFTER_ART_GENERATION"
            if output_use == "PROMO_POSTER"
            else "NO_TEXT_OR_WATERMARK"
        ),
        "crop_policy": (
            "KEEP_FOCAL_SUBJECT_AND_HORIZON_INSIDE_CENTER_CROP_SAFE_AREA_FOR_FILL_FIT_SPAN"
            if output_use == "WALLPAPER"
            else "KEEP_PRIMARY_SUBJECT_AND_COPY_SAFE_AREA_CLEAR_OF_TRIM_EDGES"
            if output_use == "PROMO_POSTER"
            else "PRESERVE_CLEAR_FOCAL_HIERARCHY"
        ),
    }


def build_canvas_contract(args: argparse.Namespace) -> dict[str, object]:
    orientation = args.orientation.upper()
    default_ratio = DEFAULT_ASPECT_BY_ORIENTATION[orientation]
    ratio = normalize_aspect_ratio(args.aspect_ratio) if args.aspect_ratio else default_ratio
    if not re.fullmatch(r"\d+(?::\d+)", ratio):
        raise StylePackError("Aspect ratio must use W:H, for example 9:16 or 16:9.")
    width, height = (int(part) for part in ratio.split(":", 1))
    if width <= 0 or height <= 0:
        raise StylePackError("Aspect ratio dimensions must be positive.")
    actual_orientation = "LANDSCAPE" if width > height else "PORTRAIT" if height > width else "SQUARE"
    nonstandard = ratio not in STANDARD_ASPECT_RATIOS or actual_orientation != orientation
    if nonstandard and not args.user_approved_nonstandard_aspect:
        raise StylePackError(
            f"Nonstandard or orientation-mismatched aspect ratio {ratio} requires direct user approval via "
            "--user-approved-nonstandard-aspect. Defaults are 9:16 portrait and 16:9 landscape."
        )
    character_free_scene = (
        str(getattr(args, "generation_purpose", "")).upper() == "SCENE"
        and str(getattr(args, "character_id", "")).upper() == "NONE"
    )
    if character_free_scene:
        return {
            "orientation": orientation,
            "aspect_ratio": ratio,
            "used_default_ratio": not bool(args.aspect_ratio),
            "nonstandard_user_approved": bool(args.user_approved_nonstandard_aspect),
            "framing": "SCENE_COMPOSITION",
            "target_pose_family": "NOT_APPLICABLE",
            "full_figure": False,
            "subject_height_percent": None,
            "required_margins": "Preserve the crop/copy safe areas declared by scene_contract.",
            "vertical_stretch_forbidden": False,
        }
    full_figure = args.framing in {"FULL_BODY", "THREE_QUARTER"}
    return {
        "orientation": orientation,
        "aspect_ratio": ratio,
        "used_default_ratio": not bool(args.aspect_ratio),
        "nonstandard_user_approved": bool(args.user_approved_nonstandard_aspect),
        "framing": args.framing,
        "target_pose_family": args.target_pose_family,
        "full_figure": full_figure,
        "subject_height_percent": [72, 88] if args.framing == "FULL_BODY" else [60, 88],
        "required_margins": "Visible headroom and floor/foot margin; do not stretch the figure to fill the canvas.",
        "vertical_stretch_forbidden": True,
    }


def parse_head_height_contract(value: str, required: bool) -> dict[str, object]:
    text = value.strip().upper()
    if text == "SOURCE_LOCK":
        if required:
            raise StylePackError(
                "A new full-body character requires an explicit --body-height-heads range, for example 6.5-7.0."
            )
        return {"mode": "SOURCE_LOCK"}
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[-:]\s*(\d+(?:\.\d+)?)\s*", value)
    if not match:
        raise StylePackError("--body-height-heads must be SOURCE_LOCK or a numeric range such as 6.5-7.0.")
    minimum, maximum = float(match.group(1)), float(match.group(2))
    if not (4.0 <= minimum <= maximum <= 10.0):
        raise StylePackError("Body height in heads must be an ordered range within 4.0-10.0.")
    return {"mode": "EXPLICIT_RANGE", "minimum": minimum, "maximum": maximum}


def build_body_proportion_contract(
    args: argparse.Namespace,
    selected: dict[str, object],
    auxiliary: list[dict[str, object]],
    is_new_character: bool,
    technical_test: bool = False,
) -> dict[str, object]:
    dominant = args.dominant_body_source.upper()
    target_family = args.target_pose_family.upper()
    coverage = args.body_source_coverage.upper()
    source_family = args.body_source_pose_family.upper()
    full_body_target = args.framing == "FULL_BODY"
    body_targets = [row for row in auxiliary if row["mode"] == "BODY_BUILD_TARGET"]

    if not is_new_character and not technical_test and not args.allow_body_identity_change:
        if dominant != "CHARACTER_BODY":
            raise StylePackError("An existing character with locked anatomy must use CHARACTER_BODY as the dominant body source.")
        if body_targets:
            raise StylePackError("A locked existing character cannot use an auxiliary BODY_BUILD_TARGET.")
    elif dominant == "CHARACTER_BODY" and is_new_character:
        raise StylePackError("A NEW character cannot use CHARACTER_BODY as its dominant source.")

    dominant_aux: dict[str, object] | None = None
    if dominant.startswith("BR_"):
        matches = [row for row in body_targets if row["ref_id"] == dominant]
        if len(matches) != 1:
            raise StylePackError(
                f"Dominant body source {dominant} must be connected exactly once as {dominant}=BODY_BUILD_TARGET."
            )
        dominant_aux = matches[0]
        if len(body_targets) != 1:
            raise StylePackError("Exactly one auxiliary BODY_BUILD_TARGET may define permanent proportions.")
        manifest_family = str(dominant_aux.get("primary_family", "")).upper()
        manifest_framing = str(dominant_aux.get("framing", "")).upper()
        if source_family != manifest_family:
            raise StylePackError(
                f"Declared body source pose family {source_family} does not match {dominant} metadata {manifest_family}."
            )
        if full_body_target and manifest_framing != "FULL":
            raise StylePackError(
                f"{dominant} has framing {manifest_framing}, so it cannot control complete full-body proportions."
            )
        if "AUX_BODY_BUILD" in set(dominant_aux.get("not_for_roles", [])):
            raise StylePackError(f"{dominant} is explicitly forbidden as a permanent body-build source.")
    elif dominant == "STYLE_BODY":
        if body_targets:
            raise StylePackError(
                "STYLE_BODY is dominant, but an auxiliary BODY_BUILD_TARGET is also connected. "
                "Change the auxiliary reference to STAGING_ONLY or make its BR id dominant."
            )
        if "body" not in selected:
            raise StylePackError("STYLE_BODY requires a selected local BODY reference.")
    elif dominant == "CHARACTER_BODY":
        if body_targets and not args.allow_body_identity_change:
            raise StylePackError("CHARACTER_BODY cannot compete with BODY_BUILD_TARGET without an explicit anatomy change.")
    elif dominant == "PROMPT_BODY_SPEC":
        if not is_new_character or technical_test:
            raise StylePackError("PROMPT_BODY_SPEC is reserved for a new prompt-led CHARACTER_BASE.")
        if not getattr(args, "prompt_only_physique", False):
            raise StylePackError("PROMPT_BODY_SPEC requires --prompt-only-physique.")
        if body_targets or "body" in selected or "pose" in selected:
            raise StylePackError("PROMPT_BODY_SPEC cannot compete with visual BODY, POSE, or BODY_BUILD_TARGET inputs.")
        if args.aux_body_decision.upper() == "SELECTED" and not args.aux_body_selection_note.strip():
            raise StylePackError(
                "PROMPT_BODY_SPEC with BODY_REFERENCE_LIBRARY selected requires an explicit note explaining why no safe candidate is attached."
            )
    else:
        raise StylePackError("--dominant-body-source must be PROMPT_BODY_SPEC, STYLE_BODY, CHARACTER_BODY, or a connected BR_NNNN.")

    if full_body_target:
        if coverage != "FULL_BODY":
            raise StylePackError(
                f"Full-body output requires a FULL_BODY dominant source; {coverage} cannot define leg-to-torso length."
            )
        if source_family != target_family and dominant not in {"CHARACTER_BODY", "PROMPT_BODY_SPEC"}:
            raise StylePackError(
                f"Full-body {target_family} output cannot take permanent proportions from a {source_family} source. "
                "Use a same-family full-body source; seated or lying references may only support staging/torso details."
            )
        if source_family != target_family and dominant == "CHARACTER_BODY" and "pose" not in selected:
            raise StylePackError(
                "An approved standing CHARACTER_BODY may be adapted to another pose only with a separate selected POSE reference."
            )
        if not args.body_silhouette_notes.strip():
            raise StylePackError(
                "Full-body output requires --body-silhouette-notes covering shoulders, torso, waist, hips, thighs, and leg-to-torso ratio."
            )
    head_contract = parse_head_height_contract(
        args.body_height_heads,
        required=is_new_character and full_body_target,
    )
    return {
        "dominant_source": dominant,
        "single_dominant_source": True,
        "specification_type": "TEXT" if dominant == "PROMPT_BODY_SPEC" else "VISUAL_REFERENCE",
        "source_coverage": coverage,
        "source_pose_family": source_family,
        "target_pose_family": target_family,
        "height_in_heads": head_contract,
        "user_approved_nonstandard_proportions": bool(
            getattr(args, "user_approved_nonstandard_proportions", False)
        ),
        "silhouette_notes": args.body_silhouette_notes,
        "locked_measurements": [
            "shoulder_width", "bust_volume", "ribcage_width", "waist_width", "hip_width",
            "glute_volume", "thigh_volume", "leg_to_torso_ratio", "overall_head_count",
        ],
        "pose_may_not_change_permanent_proportions": True,
        "approved_character_body_may_use_separate_pose_staging": dominant == "CHARACTER_BODY",
    }


def build_attachment_plan(
    selected: dict[str, object],
    auxiliary: list[dict[str, object]],
    limit: int,
) -> list[dict[str, object]]:
    if not 1 <= limit <= 5:
        raise StylePackError("Attachment limit must be within 1-5 for the current generator workflow.")
    grouped: dict[str, dict[str, object]] = {}

    def add(record: dict[str, object], role: str) -> None:
        digest = str(record["sha256"])
        item = grouped.setdefault(digest, {"path": record["path"], "sha256": digest, "active_roles": []})
        roles = item["active_roles"]
        if role not in roles:
            roles.append(role)

    for key, value in selected.items():
        role = key.upper()
        if isinstance(value, list):
            for record in value:
                add(record, role)
        else:
            add(value, role)
    for record in auxiliary:
        for role in record["active_roles"]:
            add(record, f"{record['ref_id']}:{role}")
    attachments = list(grouped.values())
    if len(attachments) > limit:
        details = "; ".join(
            f"{Path(str(item['path'])).name} ({','.join(item['active_roles'])})" for item in attachments
        )
        raise StylePackError(
            f"Reference plan needs {len(attachments)} physical attachments but the limit is {limit}. "
            "No BODY, face, or style reference may be silently omitted. Reuse a genuinely multi-role full-resolution file, "
            "release a category explicitly, or create a manifest-approved targeted collage. Planned: " + details
        )
    for slot, item in enumerate(attachments, 1):
        item["slot"] = slot
        item["active_roles"] = sorted(item["active_roles"])
        item["physically_attach"] = True
    return attachments


def build_multistage_attachment_plan(
    selected: dict[str, object],
    auxiliary: list[dict[str, object]],
    limit: int,
    purpose: str = "SCENE",
) -> list[dict[str, object]]:
    """Build a bounded layer-by-layer workflow when one pass cannot carry every hard reference."""

    if not 1 <= limit <= 5:
        raise StylePackError("Attachment limit must be within 1-5 for the current generator workflow.")
    style_records = selected.get("style", [])
    if not isinstance(style_records, list) or not style_records:
        raise StylePackError("A multi-stage workflow still requires at least one STYLE reference.")
    style_anchor = style_records[0]
    stages: list[dict[str, object]] = []

    def source_records(keys: Sequence[str], include_style: bool = True) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        if include_style:
            records.append({**style_stage_record, "stage_role": "STYLE"})
        for key in keys:
            value = selected.get(key)
            if not value:
                continue
            values = value if isinstance(value, list) else [value]
            for record in values:
                records.append({**record, "stage_role": key.upper()})
        return records

    def placeholder(stage_id: str, role: str) -> dict[str, object]:
        return {
            "path": f"<STAGE_OUTPUT:{stage_id}>",
            "sha256": f"STAGE_OUTPUT:{stage_id}",
            "stage_role": role,
            "generated_stage_output": True,
        }

    def targeted_stage_pack(stage_ids: Sequence[str], role: str) -> dict[str, object]:
        pack_id = "+".join(stage_ids)
        return {
            "path": f"<TARGETED_STAGE_PACK:{pack_id}>",
            "sha256": f"TARGETED_STAGE_PACK:{pack_id}",
            "stage_role": role,
            "generated_stage_output": True,
            "planned_targeted_pack": True,
            "targeted_pack_sources": list(stage_ids),
        }

    def add_stage(stage_id: str, purpose: str, records: list[dict[str, object]], qa: Sequence[str]) -> None:
        grouped: dict[str, dict[str, object]] = {}
        for record in records:
            digest = str(record["sha256"])
            item = grouped.setdefault(
                digest,
                {
                    "path": record["path"],
                    "sha256": digest,
                    "active_roles": [],
                    "physically_attach": True,
                    "generated_stage_output": bool(record.get("generated_stage_output")),
                },
            )
            if record.get("planned_targeted_pack"):
                item["planned_targeted_pack"] = True
                item["targeted_pack_sources"] = list(record.get("targeted_pack_sources", []))
            role = str(record["stage_role"])
            if role not in item["active_roles"]:
                item["active_roles"].append(role)
        slots = list(grouped.values())
        if len(slots) > limit:
            detail = "; ".join(f"{Path(str(item['path'])).name} ({','.join(item['active_roles'])})" for item in slots)
            raise StylePackError(
                f"Multi-stage step {stage_id} still needs {len(slots)} attachments, above limit {limit}. "
                "Reduce conflicting references or create a manifest-approved targeted collage. Planned: " + detail
            )
        for number, item in enumerate(slots, 1):
            item["slot"] = number
            item["active_roles"] = sorted(item["active_roles"])
        stages.append({
            "stage_id": stage_id,
            "purpose": purpose,
            "attachment_limit": limit,
            "attachments_used": len(slots),
            "slots": slots,
            "required_qa": list(qa),
            "output_status": "STAGING_PASS_REQUIRED",
            "may_feed_next_stage_only_after_qa_pass": True,
        })

    style_stage_record = style_anchor
    if purpose != "CHARACTER_BASE" and len(style_records) > 1:
        add_stage(
            "00_STYLE_SYNTHESIS",
            "Resolve the compatible shared rendering language from all selected style references.",
            [{**record, "stage_role": "STYLE"} for record in style_records],
            ("STYLE",),
        )
        style_stage_record = placeholder("00_STYLE_SYNTHESIS", "STYLE_STAGE")

    face_keys = [key for key in ("primary_face", "supporting_face", "expression") if key in selected]
    if face_keys and purpose != "CHARACTER_BASE":
        add_stage(
            "01_FACE_IDENTITY",
            "Resolve face geometry and expression in the source drawing style; do not invent body proportions.",
            source_records(face_keys),
            ("FACE_GEOMETRY", "EXPRESSION", "STYLE"),
        )

    body_records = source_records([key for key in ("character_assembly", "body", "pose") if key in selected])
    for record in auxiliary:
        body_records.append({**record, "stage_role": f"{record['ref_id']}:{record['mode']}"})

    if purpose == "CHARACTER_BASE":
        def coverage_records(keys: Sequence[str]) -> list[dict[str, object]]:
            records = source_records(keys, include_style=False)
            for record in records:
                record["stage_role"] = "CLOTHING_TOPOLOGY"
            return records

        add_stage(
            "01_FACE_IDENTITY",
            "Resolve the canonical close face geometry and base expression first in the selected style. This passed face becomes a mandatory hard identity attachment for every physique projection; do not add character wardrobe, accessories, or a designed environment.",
            source_records(face_keys),
            ("FACE_GEOMETRY", "EXPRESSION", "STYLE", "NEUTRAL_BACKDROP"),
        )
        face_output = placeholder("01_FACE_IDENTITY", "FACE_IDENTITY_STAGE")
        front_records = [*body_records, face_output, *coverage_records(["coverage_front"])]
        add_stage(
            "02_PHYSIQUE_FRONT",
            "Create the canonical adult full-body front view on a plain neutral backdrop. Preserve the complete silhouette and render the prompt-defined safety garment; use optional CLOTHING_TOPOLOGY evidence only when the user directly requested it.",
            front_records,
            ("FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "LIMB_PROPORTIONS", "FRONT_VIEW", "SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "STYLE", "BODY_RENDERING_STYLE"),
        )
        front_output = placeholder("02_PHYSIQUE_FRONT", "PHYSIQUE_FRONT_STAGE")
        face_front_pack = targeted_stage_pack(
            ("01_FACE_IDENTITY", "02_PHYSIQUE_FRONT"),
            "FACE_FRONT_TARGETED_PACK",
        )
        side_records = [*body_records, face_front_pack, *coverage_records(["coverage_front", "coverage_side"])]
        add_stage(
            "03_PHYSIQUE_SIDE",
            "Create the canonical adult full-body side view of the same physique on a plain neutral backdrop. Deterministically pack the passed FACE and FRONT outputs without cropping and attach that generator-safe targeted pack as their single physical slot. Match height, torso depth, abdomen, pelvis, glutes, thighs, spinal curve, and limb proportions; keep the prompt-defined safety garment consistent without altering the external silhouette.",
            side_records,
            ("FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "LIMB_PROPORTIONS", "SIDE_VIEW", "SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "MULTIVIEW_CONSISTENCY", "STYLE", "BODY_RENDERING_STYLE"),
        )
        side_output = placeholder("03_PHYSIQUE_SIDE", "PHYSIQUE_SIDE_STAGE")
        face_front_side_pack = targeted_stage_pack(
            ("01_FACE_IDENTITY", "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE"),
            "FACE_FRONT_SIDE_TARGETED_PACK",
        )
        back_records = [*body_records, face_front_side_pack, *coverage_records(["coverage_back"])]
        add_stage(
            "04_PHYSIQUE_BACK",
            "Create the canonical adult full-body back view of the same physique on a plain neutral backdrop. Deterministically pack the passed FACE, FRONT, and SIDE outputs without cropping and attach that generator-safe targeted pack as their single physical slot. Match the front and side views' height, shoulders, torso, waist, pelvis, glutes, thighs, and limbs; keep the prompt-defined safety garment consistent without altering the external silhouette.",
            back_records,
            ("FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "LIMB_PROPORTIONS", "BACK_VIEW", "SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "MULTIVIEW_CONSISTENCY", "STYLE", "BODY_RENDERING_STYLE"),
        )
        assembly_inputs = [
            style_stage_record | {"stage_role": "STYLE"},
            face_output,
            front_output,
            side_output,
            placeholder("04_PHYSIQUE_BACK", "PHYSIQUE_BACK_STAGE"),
        ]
        add_stage(
            "05_CHARACTER_ASSEMBLY",
            "Create a neutral canonical 3/4 character assembly from the verified face and front/side/back physique. Do not add a designed background, outfit, or accessories and do not reopen identity or proportions.",
            assembly_inputs,
            ("FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "LIMB_PROPORTIONS", "MULTIVIEW_CONSISTENCY", "NEUTRAL_BACKDROP", "STYLE", "BODY_RENDERING_STYLE"),
        )
        return stages

    add_stage(
        "02_BODY_POSE",
        "Resolve the dominant silhouette, proportions, pose, camera, contacts, and foreshortening on a neutral outfit/background.",
        body_records,
        ("BODY_SILHOUETTE", "BODY_PROPORTIONS", "POSE_CONTACTS", "CAMERA", "STYLE"),
    )

    body_output = placeholder("02_BODY_POSE", "BODY_POSE_STAGE")
    character_base_output = body_output
    if "clothes" in selected:
        add_stage(
            "03_CLOTHING",
            "Dress the verified body/pose without changing its silhouette, anatomy, or camera.",
            [style_stage_record | {"stage_role": "STYLE"}, body_output, *source_records(["clothes"], include_style=False)],
            ("CLOTHING", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "STYLE"),
        )
        character_base_output = placeholder("03_CLOTHING", "CLOTHING_STAGE")

    composite_inputs = [style_stage_record | {"stage_role": "STYLE"}, character_base_output]
    if face_keys:
        composite_inputs.append(placeholder("01_FACE_IDENTITY", "FACE_IDENTITY_STAGE"))
    add_stage(
        "04_CHARACTER_COMPOSITE",
        "Combine the verified face with the verified clothed body; preserve both layers exactly.",
        composite_inputs,
        ("FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "CLOTHING", "STYLE"),
    )

    final_inputs = [
        style_stage_record | {"stage_role": "STYLE"},
        placeholder("04_CHARACTER_COMPOSITE", "CHARACTER_COMPOSITE_STAGE"),
        *source_records(
            [key for key in ("lighting", "background", "composition") if key in selected],
            include_style=False,
        ),
    ]
    add_stage(
        "05_FINAL_SCENE",
        "Place the verified character into the final scene without reopening face or body design.",
        final_inputs,
        ("CANVAS", "FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "LIGHTING", "BACKGROUND", "COMPOSITION", "STYLE"),
    )
    return stages


def build_generation_workflow(
    args: argparse.Namespace,
    selected: dict[str, object],
    auxiliary: list[dict[str, object]],
) -> dict[str, object]:
    mode = args.reference_workflow.upper()
    purpose = getattr(args, "generation_purpose", "SCENE").upper()
    character_free_scene = purpose == "SCENE" and str(getattr(args, "character_id", "NONE")).upper() == "NONE"
    if character_free_scene:
        if mode == "MULTI_STAGE":
            raise StylePackError(
                "A character-free SCENE is one requested key-art deliverable. MULTI_STAGE would create unrequested service images; "
                "reduce the reference set to the physical attachment limit."
            )
        slots = build_attachment_plan(selected, auxiliary, args.attachment_limit)
        return {
            "mode": "SINGLE_PASS",
            "purpose": purpose,
            "attachment_limit": args.attachment_limit,
            "attachments_used": len(slots),
            "slots": slots,
            "all_selected_references_physically_attached": True,
            "unrequested_staging_forbidden": True,
        }
    if purpose == "CHARACTER_BASE" and mode == "SINGLE_PASS":
        raise StylePackError("CHARACTER_BASE requires MULTI_STAGE so face, front/side/back physique, and assembly are verified separately.")
    if purpose == "CHARACTER_BASE":
        mode = "MULTI_STAGE"
    if mode in {"AUTO", "SINGLE_PASS"}:
        try:
            slots = build_attachment_plan(selected, auxiliary, args.attachment_limit)
            return {
                "mode": "SINGLE_PASS",
                "attachment_limit": args.attachment_limit,
                "attachments_used": len(slots),
                "slots": slots,
                "all_selected_references_physically_attached": True,
            }
        except StylePackError:
            if mode == "SINGLE_PASS":
                raise
    stages = build_multistage_attachment_plan(selected, auxiliary, args.attachment_limit, purpose)
    return {
        "mode": "MULTI_STAGE",
        "purpose": purpose,
        "attachment_limit": args.attachment_limit,
        "stages": stages,
        "staging_outputs_are_request_local": True,
        "staging_outputs_are_not_character_or_style_anchors": True,
        "all_source_conditions_propagate_through_verified_layers": True,
    }


def command_prepare_generation(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    ensure_generation_library(paths)
    request_id = safe_component(args.request_id, "request")
    request_folder = paths.generations / "00_PENDING" / request_id
    execution_guard_path = request_folder / "EXECUTION_GUARD.json"
    try:
        execution_guard = require_active_guard(execution_guard_path, request_id)
    except ExecutionGuardError as error:
        raise StylePackError(
            f"Execution guard blocked preparation: {error}. Start the request guard before substantive work with "
            f"tools\\task_execution_guard.py start --state \"{execution_guard_path}\" --request-id \"{request_id}\" "
            "--goal \"<user goal>\" --deliverable \"<visible deliverable>\"."
        ) from error
    if args.fidelity not in {30, 50, 70, 90, 100}:
        raise StylePackError("Fidelity must be one of 30, 50, 70, 90, or 100.")
    startup_interaction = parse_startup_interaction(args)
    purpose = args.generation_purpose.upper()
    overrides = {item.upper() for item in args.override}
    context = build_style_context(paths, "ALL", positive_only=False, include_files=False)
    reviewed_counts = parse_reviewed_counts(args.reviewed, args.face_candidates_reviewed)
    validate_prompt_only_body_library_review(args)
    character_id = args.character_id.upper()
    is_new_character = character_id == "NEW"
    scene_contract = build_scene_contract(args, purpose, character_id)
    character_free_scene = bool(scene_contract.get("applicable") and not scene_contract.get("has_character"))
    if character_free_scene:
        optional_scene_roles = {
            "FACE": args.primary_face or args.supporting_face or args.expression_reference,
            "BODY": args.body_reference,
            "POSE": args.pose_reference,
            "CLOTHES": args.clothes_reference,
        }
        overrides.update(role for role, value in optional_scene_roles.items() if not value)
    else:
        missing_body_fields = [
            option
            for option, value in (
                ("--framing", args.framing),
                ("--target-pose-family", args.target_pose_family),
                ("--dominant-body-source", args.dominant_body_source),
                ("--body-source-coverage", args.body_source_coverage),
                ("--body-source-pose-family", args.body_source_pose_family),
            )
            if not value
        ]
        if missing_body_fields:
            raise StylePackError(
                "Character-bearing generation requires: " + ", ".join(missing_body_fields)
            )
    if is_new_character and purpose != "CHARACTER_BASE":
        raise StylePackError(
            "Every NEW character must complete CHARACTER_BASE first. Generate safety-covered front/side/back physique, face, "
            "and neutral assembly before clothing, accessories, pose-driven scene, or background."
        )
    if purpose == "CHARACTER_BASE":
        if not is_new_character:
            raise StylePackError("CHARACTER_BASE is for an unapproved NEW character; use SCENE/variation workflows for an existing character.")
        if not args.adult_character:
            raise StylePackError("An adult character-base physique reference requires explicit --adult-character confirmation.")
        if args.orientation.upper() != "PORTRAIT" or args.framing != "FULL_BODY" or args.target_pose_family != "STANDING":
            raise StylePackError("CHARACTER_BASE requires PORTRAIT, FULL_BODY, and STANDING for canonical front/side/back physique references.")
        if len(args.style_reference) != 1:
            raise StylePackError(
                "CHARACTER_BASE requires exactly one strongest full-resolution style anchor; do not generate a STYLE_SYNTHESIS character stage."
            )
        if args.clothes_reference or args.lighting_reference or args.background_reference or args.composition_reference:
            raise StylePackError(
                "CHARACTER_BASE does not accept clothes, lighting, background, or composition references. "
                "Create wardrobe/accessory assets later and generate scenes only after identity approval."
            )
        prompt_only_physique = bool(args.prompt_only_physique)
        coverage_values = {
            "coverage_front": args.coverage_front_reference,
            "coverage_side": args.coverage_side_reference,
            "coverage_back": args.coverage_back_reference,
        }
        missing_coverage = [key for key, value in coverage_values.items() if not value]
        if any(coverage_values.values()) and not args.user_requested_coverage_reference:
            raise StylePackError(
                "Visual safety-coverage references are opt-in. Use prompt-described swimwear by default; "
                "pass --user-requested-coverage-reference only after direct user instruction."
            )
        if prompt_only_physique and any(coverage_values.values()):
            raise StylePackError("--prompt-only-physique forbids visual safety-coverage references.")
        if prompt_only_physique and (args.body_reference or args.pose_reference or args.clothes_reference or args.aux_body):
            raise StylePackError("--prompt-only-physique forbids BODY, POSE, CLOTHES, and auxiliary body images.")
        if args.user_requested_coverage_reference and missing_coverage:
            raise StylePackError(
                "User-requested visual coverage requires all view-specific topology references: "
                + ", ".join(missing_coverage)
            )
        overrides.update({"CLOTHES", "LIGHTING", "BACKGROUND", "COMPOSITION"})
        if prompt_only_physique:
            overrides.update({"BODY", "POSE"})
    elif args.coverage_front_reference or args.coverage_side_reference or args.coverage_back_reference:
        raise StylePackError("View-specific coverage references are valid only for CHARACTER_BASE.")
    auxiliary_body_references = parse_aux_body_references(
        args.workspace,
        args.aux_body,
        args.aux_body_decision,
        args.aux_body_selection_note,
        is_new_character,
        args.allow_body_identity_change,
    )

    if not args.style_reference:
        raise StylePackError("At least one local --style-reference is required.")
    selected: dict[str, object] = {
        "style": [validate_plan_reference(paths, value, "STYLE") for value in args.style_reference]
    }
    if scene_contract.get("applicable") and args.subject_reference:
        selected["subject"] = [
            validate_plan_reference(paths, value, "SUBJECT") for value in args.subject_reference
        ]
    if purpose == "CHARACTER_BASE":
        coverage_inputs = (
            ("coverage_front", args.coverage_front_reference, "FRONT_CLOTHING_TOPOLOGY"),
            ("coverage_side", args.coverage_side_reference, "SIDE_CLOTHING_TOPOLOGY"),
            ("coverage_back", args.coverage_back_reference, "BACK_CLOTHING_TOPOLOGY"),
        )
        for key, value, role in coverage_inputs:
            if value:
                selected[key] = validate_plan_reference(paths, value, role)

    face_visible = "FACE" not in overrides
    character_reference_mode = "NOT_APPLICABLE"
    selected_body_view = "NOT_APPLICABLE"
    existing_scene = purpose == "SCENE" and bool(re.fullmatch(r"CHAR_\d+", character_id))
    if existing_scene:
        identity_folder = character_folder(paths, character_id)
        if not args.character_reference_evidence.strip():
            raise StylePackError("Existing-character scene requires --character-reference-evidence explaining the chosen identity subset.")
        if not args.character_assembly:
            raise StylePackError("An existing character scene requires --character-assembly from its approved base.")
        assembly = validate_plan_reference(paths, args.character_assembly, "CHARACTER_ASSEMBLY")
        if not is_relative_to(Path(str(assembly["path"])), identity_folder):
            raise StylePackError("CHARACTER_ASSEMBLY must belong to the selected approved character folder.")
        selected["character_assembly"] = assembly
        character_reference_mode = args.character_reference_mode.upper()
        if character_reference_mode == "AUTO":
            if args.shot_complexity.upper() == "COMPLEX":
                character_reference_mode = "IDENTITY_STRICT"
            elif args.selected_body_view.upper() != "ASSEMBLY":
                character_reference_mode = "ASSEMBLY_PLUS_VIEW"
            else:
                character_reference_mode = "ASSEMBLY_ONLY"
        selected_body_view = args.selected_body_view.upper()
        if character_reference_mode == "ASSEMBLY_ONLY":
            if selected_body_view != "ASSEMBLY":
                raise StylePackError("ASSEMBLY_ONLY requires --selected-body-view ASSEMBLY.")
            selected["primary_face"] = assembly
            selected["body"] = assembly
        elif character_reference_mode == "ASSEMBLY_PLUS_VIEW":
            if selected_body_view == "ASSEMBLY" or not args.body_reference:
                raise StylePackError("ASSEMBLY_PLUS_VIEW requires one nearest front, side, or back --body-reference.")
            body = validate_plan_reference(paths, args.body_reference, "NEAREST_CHARACTER_BODY")
            if not is_relative_to(Path(str(body["path"])), identity_folder):
                raise StylePackError("The nearest body view must belong to the selected approved character folder.")
            selected["primary_face"] = assembly
            selected["body"] = body
        else:
            if selected_body_view == "ASSEMBLY" or not args.body_reference or not args.primary_face:
                raise StylePackError("IDENTITY_STRICT requires separate approved face and nearest front/side/back body references.")
            primary_face = validate_plan_reference(paths, args.primary_face, "CHARACTER_FACE")
            body = validate_plan_reference(paths, args.body_reference, "NEAREST_CHARACTER_BODY")
            if not is_relative_to(Path(str(primary_face["path"])), identity_folder) or not is_relative_to(Path(str(body["path"])), identity_folder):
                raise StylePackError("Strict face and body references must belong to the selected approved character folder.")
            selected["primary_face"] = primary_face
            selected["body"] = body
        if args.supporting_face:
            selected["supporting_face"] = validate_plan_reference(paths, args.supporting_face, "SUPPORTING_FACE_STYLE")
        if args.expression_reference:
            selected["expression"] = validate_plan_reference(paths, args.expression_reference, "FACE_EXPRESSION")
        face_library_count = int(context.get("review_pool_counts", {}).get("FACE", 0))
    elif face_visible:
        if not args.primary_face:
            raise StylePackError("Visible face requires --primary-face.")
        primary_face = validate_plan_reference(paths, args.primary_face, "PRIMARY_FACE")
        if is_new_character and "FACE" not in primary_face.get("inferred_roles", []):
            raise StylePackError("A new character's primary face must be selected from a local face-role file such as FACE_CROPS.")
        selected["primary_face"] = primary_face
        if is_new_character and args.fidelity >= 70:
            if not args.supporting_face:
                raise StylePackError("A new visible character at 70-100% fidelity requires --supporting-face.")
            supporting_face = validate_plan_reference(paths, args.supporting_face, "SUPPORTING_FACE")
            if "FACE" not in supporting_face.get("inferred_roles", []):
                raise StylePackError("Supporting face must be selected from a local face-role file.")
            if supporting_face["sha256"] == primary_face["sha256"]:
                raise StylePackError("Primary and supporting face references must be different images.")
            selected["supporting_face"] = supporting_face
        elif args.supporting_face:
            selected["supporting_face"] = validate_plan_reference(paths, args.supporting_face, "SUPPORTING_FACE")
        if args.expression_reference:
            selected["expression"] = validate_plan_reference(paths, args.expression_reference, "FACE_EXPRESSION")

        face_library_count = int(context.get("review_pool_counts", {}).get("FACE", 0))
    else:
        face_library_count = int(context.get("review_pool_counts", {}).get("FACE", 0))

    category_values = {
        "BODY": args.body_reference,
        "POSE": args.pose_reference,
        "CLOTHES": args.clothes_reference,
        "LIGHTING": args.lighting_reference,
        "BACKGROUND": args.background_reference,
        "COMPOSITION": args.composition_reference,
    }
    for category, value in category_values.items():
        if category in overrides:
            continue
        if category.lower() in selected:
            continue
        if not value:
            raise StylePackError(f"{category} requires its local reference or an explicit --override {category}.")
        selected[category.lower()] = validate_plan_reference(
            paths,
            value,
            category,
            allow_curated_body_contour=character_free_scene and category == "POSE",
        )

    required_review_roles = {"STYLE", *(category for category in PLAN_CATEGORIES if category not in overrides)}
    if "subject" in selected:
        required_review_roles.add("SUBJECT")
    review_pool_counts = {role: int(count) for role, count in context.get("review_pool_counts", {}).items()}
    if args.fidelity >= 70:
        shortfalls: list[str] = []
        for role in sorted(required_review_roles):
            required = review_pool_counts.get(role, 0)
            selected_role = selected.get(role.lower())
            if (
                role == "POSE"
                and isinstance(selected_role, dict)
                and selected_role.get("status") == "FINAL_CURATED_BODY_CONTOUR"
            ):
                # A final-curated anatomy contour is a distinct one-item external
                # pose pool; unrelated style-pack pose candidates are not competing
                # inputs for this anonymous scene.
                required = 1
            reviewed = reviewed_counts.get(role, 0)
            if required > 0 and reviewed < required:
                shortfalls.append(f"{role}: reviewed {reviewed} of {required}")
        if shortfalls:
            raise StylePackError("Full local role review required before 70-100% generation: " + "; ".join(shortfalls))

    canvas_contract = build_canvas_contract(args)
    if character_free_scene:
        body_proportion_contract = {
            "applicable": False,
            "single_dominant_source": False,
            "dominant_source": "NOT_APPLICABLE",
        }
    else:
        body_proportion_contract = build_body_proportion_contract(
            args,
            selected,
            auxiliary_body_references,
            is_new_character,
            technical_test=purpose == "TECHNICAL_TEST",
        )
    generation_workflow = build_generation_workflow(args, selected, auxiliary_body_references)
    risk_path, risk_assessment = load_and_validate_risk_assessment(
        args.risk_assessment,
        selected,
        auxiliary_body_references,
    )
    style_calibration = load_style_calibration_evidence(
        args.style_calibration_state,
        required=args.require_style_calibration,
        style_name=context["style_name"],
        pack_path=context["pack_path"],
    )

    plan_path = request_folder / "REFERENCE_PLAN.json"
    if plan_path.exists():
        raise StylePackError(f"Reference plan already exists and will not be overwritten: {plan_path}")
    plan = {
        "schema_version": 5,
        "semantic_qa_schema": 4,
        "gate_status": "READY_FOR_GENERATION",
        "created_at": iso_now(),
        "style_name": context["style_name"],
        "pack_path": context["pack_path"],
        "request_id": request_id,
        "fidelity": args.fidelity,
        "style_fidelity_confirmation": {
            "user_selected_percentage": args.fidelity,
            "resolution_source": startup_interaction["parameter_source"],
            "user_choice_quote": startup_interaction["user_choice_quote"],
            "custom_parameters_user_quote": startup_interaction["custom_parameters_user_quote"],
            "resolved_after_menu_choice": True,
        },
        "startup_parameter_selection": {
            **startup_interaction,
            "resolved_parameters": {
                "fidelity": args.fidelity,
                "aux_body_decision": args.aux_body_decision.upper(),
                "orientation": args.orientation.upper(),
                "aspect_ratio": canvas_contract["aspect_ratio"],
                "framing": canvas_contract["framing"],
                "target_pose_family": canvas_contract["target_pose_family"],
                "reference_workflow": generation_workflow["mode"],
            },
        },
        "character_id": character_id,
        "generation_purpose": purpose,
        "scene_contract": scene_contract,
        "local_context": {
            "local_files_total": context["local_files_total"],
            "work_collection_counts": context["work_collection_counts"],
            "role_counts": context["role_counts"],
            "review_pool_counts": review_pool_counts,
            "reviewed_counts": reviewed_counts,
            "explicit_anchor_files": context["explicit_anchor_files"],
            "anchor_fallback_required": not bool(context["explicit_anchor_files"]),
        },
        "face_review": {
            "face_visible": face_visible,
            "face_library_count": face_library_count,
            "face_candidates_reviewed": reviewed_counts.get("FACE", 0),
            "selection_evidence": args.face_selection_evidence,
            "geometry_is_hard_constraint": face_visible and args.fidelity >= 90,
        },
        "character_reference_selection": {
            "mode": character_reference_mode,
            "shot_complexity": args.shot_complexity.upper(),
            "selected_body_view": selected_body_view,
            "all_canonical_base_views_remain_required_in_storage": purpose == "CHARACTER_BASE" or existing_scene,
            "future_scene_uses_minimal_relevant_subset": existing_scene,
            "assembly_is_primary_character_reference": existing_scene,
            "selection_evidence": args.character_reference_evidence,
        },
        "canvas_contract": canvas_contract,
        "body_proportion_contract": body_proportion_contract,
        "generation_workflow": generation_workflow,
        "style_transfer_calibration": style_calibration,
        "risk_assessment": {
            "path": str(risk_path),
            "notice_ru": risk_assessment.get("notice_ru", ""),
            "generation_risk": risk_assessment["generation_risk"],
            "prompt": risk_assessment.get("revised_prompt") or risk_assessment.get("original_prompt"),
            "references": risk_assessment.get("references", []),
            "combined_modifiers": risk_assessment.get("combined_modifiers", []),
            "original_combined_risk": risk_assessment.get("original_combined_risk"),
            "revised_combined_risk": risk_assessment.get("revised_combined_risk"),
        },
        "prompt_hard_constraints": (
            [
                f"Use canvas {canvas_contract['aspect_ratio']} ({canvas_contract['orientation']}).",
                "Use only the optional explicit roles and auxiliary transfer modes declared by this plan.",
                "Keep the subject anonymous unless an explicit FACE reference is selected; auxiliary references never transfer identity.",
                str(scene_contract["crop_policy"]),
                str(scene_contract["typography_policy"]),
            ]
            if character_free_scene
            else [
                f"Use canvas {canvas_contract['aspect_ratio']} ({canvas_contract['orientation']}).",
                "Do not vertically stretch the character or lengthen legs/torso to fill the frame.",
                "Match the dominant body specification silhouette and leg-to-torso ratio before adding clothing or scenery.",
                "Preserve verified face and body layers through every later stage.",
            ]
        ),
        "selected_references": selected,
        "auxiliary_body_reference_decision": args.aux_body_decision.upper(),
        "prompt_only_physique": bool(args.prompt_only_physique),
        "body_library_review": {
            "relevant_candidates_total": int(args.body_library_relevant_candidates_total),
            "candidates_visually_reviewed": int(args.body_library_candidates_reviewed),
            "complete": int(args.body_library_relevant_candidates_total) > 0
            and int(args.body_library_candidates_reviewed) == int(args.body_library_relevant_candidates_total),
        },
        "auxiliary_body_reference_selection_note": args.aux_body_selection_note.strip(),
        "auxiliary_body_references": auxiliary_body_references,
        "auxiliary_reference_contract": {
            "style_influence": "FORBIDDEN",
            "existing_character_body_identity_locked": not is_new_character and not args.allow_body_identity_change,
            "only_explicit_active_roles_transfer": True,
        },
        "user_overrides": sorted(overrides),
        "notes": args.notes,
        "execution_guard": {
            "path": str(execution_guard_path.resolve()),
            "request_id": execution_guard["request_id"],
            "goal_lock": execution_guard["goal_lock"],
            "primary_deliverable": execution_guard["primary_deliverable"],
        },
    }
    if purpose == "CHARACTER_BASE":
        kit_name = safe_component(args.character_name or request_id, "character")
        kit_folder = request_folder / "CHARACTER_KITS" / f"TEMP_{kit_name}"
        stage_directories = {
            "01_FACE_IDENTITY": "01_FACE",
            "02_PHYSIQUE_FRONT": "02_PHYSIQUE_FRONT",
            "03_PHYSIQUE_SIDE": "03_PHYSIQUE_SIDE",
            "04_PHYSIQUE_BACK": "04_PHYSIQUE_BACK",
            "05_CHARACTER_ASSEMBLY": "05_CHARACTER_ASSEMBLY",
        }
        for directory in (*stage_directories.values(), "06_WARDROBE", "07_ACCESSORIES"):
            (kit_folder / directory).mkdir(parents=True, exist_ok=True)
        plan["character_kit"] = {
            "temporary_id": f"TEMP_{kit_name}",
            "folder": str(kit_folder),
            "stage_directories": stage_directories,
            "separate_background_asset": False,
            "rendering_backdrop": "PLAIN_NEUTRAL_ONLY",
            "required_identity_outputs": [
                "01_FACE_IDENTITY",
                "02_PHYSIQUE_FRONT",
                "03_PHYSIQUE_SIDE",
                "04_PHYSIQUE_BACK",
                "05_CHARACTER_ASSEMBLY",
            ],
            "anatomy_reference": {
                "adult_only": True,
                "coverage": "TAPE_OR_VERIFIED_NON_DISTORTING_FALLBACK",
                "cover": ["nipples", "genitals", "anus_where_visible"],
                "compression_or_reshaping_forbidden": True,
                "transparent_or_decorative_tape_forbidden": True,
                "moderation_retry_policy": "AFTER_ONE_TAPE_OR_ADHESIVE_REJECTION_SWITCH_TO_FALLBACK; DO_NOT_RETRY_SYNONYMS",
                "featureless_mannequin": "STAGING_ONLY; cannot pass final physique when style fidelity fails",
                "open_swim_reference_set": {
                    "front_and_side": "fully opaque extreme-micro two-piece with separate compact soft triangle panels, deep open center, compact secure front panel, and low-tension straps",
                    "minimum_upper_topology": "MULTI_STAGE: create G3X seed, then nominally reduce only the two upper panels by 40%; attach the passed F40 frame as hard CLOTHES evidence scoped only to CLOTHING_TOPOLOGY; keep the compact G3X lower front panel unchanged",
                    "minimum_upper_topology_evidence": "F40_EDIT_PASS_3_OF_3; F40_FRONT_TOPOLOGY_PASS_3_OF_3; F40_SIDE_TOPOLOGY_PASS_2_OF_2; measured upper colored area about 62 percent of G3X seed",
                    "minimum_topology_hard_stop": "Do not request 45 percent or greater upper reduction; do not request lower-panel reduction beyond the G3X compact front panel.",
                    "back_seed": "conventional flat extreme-micro T-back with extremely thin side straps and slim flat vertical strap; natural center separation contour remains readable alongside and below the strap",
                    "back_uncovered_t_back": "MULTI_STAGE: attach a passed short-stem frame as hard CLOTHES evidence scoped only to CLOTHING_TOPOLOGY; reproduce two thin side straps, small triangular junction, and very short upper stem; do not lengthen the stem",
                    "back_uncovered_contour_alternative": "thin side straps plus compact low-rise opaque V-shaped rear panel with no vertical T-string; natural center separation contour readable below panel",
                    "forbidden": [
                        "center_seam",
                        "wedging",
                        "gathering",
                        "ruching",
                        "thong_cut",
                        "contour_shaping",
                        "compression",
                        "lifting",
                        "transparency",
                    ],
                },
                "rear_fallback_order": [
                    "moderate flat tanga rear panel",
                    "very short low-rise seamless athletic shorts with smooth full-seat panel",
                ],
                "moderation_evidence": {
                    "extreme_micro_front_side": "PASS",
                    "compact_v_back_with_visible_natural_center_contour": "PASS",
                    "brazilian_rear_term": "INPUT_BLOCK",
                    "t_back_without_contour_constraint": "OUTPUT_BLOCK_0_OF_1",
                    "conventional_t_back_with_readable_contour_constraint": "PASS_2_OF_2_BUT_VISUALLY_OVERLAID",
                    "direct_uncovered_contour_synonyms": "INPUT_OR_OUTPUT_BLOCK",
                    "short_stem_local_edit": "PASS_1_OF_2",
                    "short_stem_hard_visual_topology_reference": "PASS_3_OF_3",
                    "upper_panel_reduction_40": "PASS_3_OF_3",
                    "upper_panel_reduction_45": "OUTPUT_RETURNED_BUT_SAFE_COVERAGE_QA_FAIL",
                    "upper_panel_reduction_50": "OUTPUT_BLOCK",
                    "f40_hard_visual_front_topology_reference": "PASS_3_OF_3",
                    "f40_hard_visual_side_topology_reference": "PASS_2_OF_2",
                    "lower_panel_reduction_5": "PASS_3_OF_3_BUT_NO_CONSISTENT_MEASURABLE_REDUCTION",
                    "lower_panel_reduction_10": "OUTPUT_BLOCK",
                    "adhesive_only_or_pasties": "OUTPUT_BLOCK",
                    "tape_look_fabric": "PASS_BUT_EXTRA_STRAPS_INVENTED",
                },
                "cleavage_rule": "Do not use a sports crop top as primary body evidence when it hides cleavage, inner contours, spacing, or lower curve.",
            },
            "wardrobe_and_accessories": "OPTIONAL_SEPARATE_MUTABLE_ASSETS",
            "permanent_character_folder_only_after_direct_approval": True,
        }
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        execution_checkpoint(
            execution_guard_path,
            event="READY_FOR_EXECUTION",
            summary=(
                f"REFERENCE_PLAN ready for {purpose}; only one exact-call risk validation may remain before the real "
                "generator call, otherwise report a blocker."
            ),
        )
    except ExecutionGuardError as error:
        raise StylePackError(f"Execution guard could not record readiness: {error}") from error
    print(f"REFERENCE_PLAN={plan_path}")
    print(f"LOCAL_FILES_RECOGNIZED={context['local_files_total']}")
    print(f"REVIEW_POOL_COUNTS={json.dumps(review_pool_counts, ensure_ascii=False)}")
    print(f"REVIEWED_COUNTS={json.dumps(reviewed_counts, ensure_ascii=False)}")
    print(f"ANCHOR_FALLBACK_REQUIRED={str(not bool(context['explicit_anchor_files'])).upper()}")
    print(f"AUX_BODY_DECISION={args.aux_body_decision.upper()}")
    print(f"AUX_BODY_REFERENCES={len(auxiliary_body_references)}")
    print(f"ASPECT_RATIO={canvas_contract['aspect_ratio']}")
    print(f"ORIENTATION={canvas_contract['orientation']}")
    print(f"DOMINANT_BODY_SOURCE={body_proportion_contract['dominant_source']}")
    print(f"REFERENCE_WORKFLOW={generation_workflow['mode']}")
    print(f"GENERATION_RISK={risk_assessment['generation_risk']}")
    print(f"EXECUTION_GUARD={execution_guard_path}")
    print(f"STARTUP_CHOICE={startup_interaction['selected']}")
    print(f"GENERATION_PURPOSE={purpose}")
    print(f"CHARACTER_REFERENCE_MODE={character_reference_mode}")
    print(f"SELECTED_BODY_VIEW={selected_body_view}")
    if purpose == "CHARACTER_BASE":
        print(f"CHARACTER_KIT={plan['character_kit']['folder']}")
    if generation_workflow["mode"] == "SINGLE_PASS":
        print(f"ATTACHMENTS={generation_workflow['attachments_used']}/{generation_workflow['attachment_limit']}")
    else:
        print(f"STAGES={len(generation_workflow['stages'])}")
    print("STATUS=READY_FOR_GENERATION")


def validate_reference_plan_for_recording(
    paths: StylePaths,
    plan_value: str,
    fidelity: int,
) -> tuple[Path, dict[str, object]]:
    if not plan_value:
        raise StylePackError("Fidelity 70-100 requires --reference-plan created before generation.")
    plan_path = Path(plan_value)
    if not plan_path.is_absolute():
        plan_path = paths.workspace / plan_path
    if not plan_path.is_file():
        raise StylePackError(f"Reference plan does not exist: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise StylePackError(f"Cannot read reference plan: {error}") from error
    if plan.get("gate_status") != "READY_FOR_GENERATION":
        raise StylePackError("Reference plan did not pass the pre-generation gate.")
    if int(plan.get("fidelity", -1)) != fidelity:
        raise StylePackError("Recorded fidelity does not match the prepared reference plan.")
    if style_slug(str(plan.get("style_name", ""))) != paths.slug:
        raise StylePackError("Reference plan belongs to another style.")
    schema_version = int(plan.get("schema_version", 0))
    if schema_version < 4:
        raise StylePackError(
            "Legacy reference plan lacks the user-confirmed fidelity and mandatory new-character base gates; prepare it again."
        )
    fidelity_confirmation = plan.get("style_fidelity_confirmation")
    if not isinstance(fidelity_confirmation, dict):
        raise StylePackError("Reference plan has no style-fidelity resolution record.")
    if int(fidelity_confirmation.get("user_selected_percentage", -1)) != fidelity:
        raise StylePackError("Reference plan fidelity does not match the resolved startup parameters.")
    if schema_version >= 5:
        startup = plan.get("startup_parameter_selection")
        if not isinstance(startup, dict) or startup.get("selected") not in STARTUP_CHOICES:
            raise StylePackError("Reference plan has no valid three-presets-plus-custom startup selection.")
        if startup.get("selection_state") in {"NEW_SELECTION", "USER_CONFIRMED_FROM_TEXT_MENU", "USER_CONFIRMED_AFTER_NATIVE_UNAVAILABLE"} and not str(startup.get("user_choice_quote", "")).strip():
            raise StylePackError("Reference plan has no recorded user startup choice or confirmation.")
        if startup.get("selection_state") not in {"NEW_SELECTION", "USER_CONFIRMED_FROM_TEXT_MENU", "USER_CONFIRMED_AFTER_NATIVE_UNAVAILABLE", "REUSED_IN_SAME_CHAT", "AUTO_DEFAULT_NO_UI"}:
            raise StylePackError("Reference plan has no valid new-or-reused same-chat startup state.")
        if startup.get("selection_state") in {"USER_CONFIRMED_AFTER_NATIVE_UNAVAILABLE", "REUSED_IN_SAME_CHAT", "AUTO_DEFAULT_NO_UI"} and startup.get("menu_presented_this_turn") is not False:
            raise StylePackError("A user-confirmed, reused, or legacy automatic-default selection must not present the startup menu.")
        if startup.get("selection_state") == "USER_CONFIRMED_FROM_TEXT_MENU" and (
            startup.get("menu_presented_this_turn") is not True
            or startup.get("menu_surface_this_turn") != "TEXT_NUMBERED_MENU"
        ):
            raise StylePackError("A Default-mode confirmation must record the displayed TEXT_NUMBERED_MENU.")
        options = startup.get("options")
        if startup.get("menu_contract") == "THREE_AI_PRESETS_PLUS_CUSTOM" and (
            not isinstance(options, list) or [item.get("id") for item in options] != [*STARTUP_CHOICES]
        ):
            raise StylePackError("Reference plan does not contain the required three AI presets plus CUSTOM.")
        if startup.get("menu_contract") == "NATIVE_MENU_UNAVAILABLE_AUTO_DEFAULT" and options != []:
            raise StylePackError("Automatic default must not fabricate or display startup menu options.")
        if startup.get("menu_contract") == "NATIVE_MENU_UNAVAILABLE_USER_CONFIRMATION" and options != []:
            raise StylePackError("User confirmation after unavailable UI must not fabricate startup menu options.")
        risk = plan.get("risk_assessment")
        if not isinstance(risk, dict) or not RISK_LABEL_RE.fullmatch(str(risk.get("generation_risk", ""))):
            raise StylePackError("Reference plan has no valid D1-D10 generation-risk assessment.")
        risk_references = risk.get("references")
        if not isinstance(risk_references, list) or any(
            not RISK_LABEL_RE.fullmatch(str(item.get("content_and_reference_risk", "")))
            for item in risk_references
            if isinstance(item, dict)
        ):
            raise StylePackError("Reference plan contains an invalid visual-reference D marker.")
    elif not str(fidelity_confirmation.get("user_quote", "")).strip():
        raise StylePackError("Legacy schema-4 plan has no recorded user quote confirming style fidelity.")
    canvas = plan.get("canvas_contract")
    body = plan.get("body_proportion_contract")
    workflow = plan.get("generation_workflow")
    if not isinstance(canvas, dict) or canvas.get("aspect_ratio") not in STANDARD_ASPECT_RATIOS and not canvas.get("nonstandard_user_approved"):
        raise StylePackError("Reference plan has no valid approved canvas contract.")
    scene_contract = plan.get("scene_contract")
    character_free_scene = bool(
        plan.get("generation_purpose") == "SCENE"
        and isinstance(scene_contract, dict)
        and scene_contract.get("applicable")
        and not scene_contract.get("has_character")
    )
    if character_free_scene:
        if not isinstance(body, dict) or body.get("applicable") is not False:
            raise StylePackError("Character-free SCENE must record body_proportion_contract.applicable=false.")
    elif not isinstance(body, dict) or not body.get("single_dominant_source"):
        raise StylePackError("Reference plan has no single dominant body source contract.")
    if not isinstance(workflow, dict) or workflow.get("mode") not in {"SINGLE_PASS", "MULTI_STAGE"}:
        raise StylePackError("Reference plan has no valid physical attachment workflow.")
    limit = int(workflow.get("attachment_limit", 0))
    if not 1 <= limit <= 5:
        raise StylePackError("Reference plan exceeds the supported physical attachment limit.")
    if workflow["mode"] == "SINGLE_PASS":
        if not workflow.get("all_selected_references_physically_attached"):
            raise StylePackError("Single-pass plan does not confirm physical attachment of every selected reference.")
        if int(workflow.get("attachments_used", limit + 1)) > limit:
            raise StylePackError("Single-pass plan exceeds its physical attachment limit.")
    else:
        stages = workflow.get("stages")
        if not isinstance(stages, list) or not stages:
            raise StylePackError("Multi-stage plan contains no stages.")
        for stage in stages:
            if int(stage.get("attachments_used", limit + 1)) > limit:
                raise StylePackError(f"Stage {stage.get('stage_id', '?')} exceeds its physical attachment limit.")
    if plan.get("generation_purpose") == "CHARACTER_BASE":
        if schema_version < 4:
            raise StylePackError("CHARACTER_BASE plan must use schema version 4 with fidelity confirmation and canonical multiview storage.")
        kit = plan.get("character_kit")
        if not isinstance(kit, dict) or kit.get("separate_background_asset") is not False:
            raise StylePackError("CHARACTER_BASE plan has no valid background-free character kit contract.")
        required_stages = {
            "01_FACE_IDENTITY",
            "02_PHYSIQUE_FRONT",
            "03_PHYSIQUE_SIDE",
            "04_PHYSIQUE_BACK",
            "05_CHARACTER_ASSEMBLY",
        }
        actual_stages = {stage.get("stage_id") for stage in workflow.get("stages", [])}
        if not required_stages.issubset(actual_stages):
            raise StylePackError("CHARACTER_BASE plan is missing a required face, multiview physique, or assembly stage.")
    elif plan.get("generation_purpose") == "SCENE" and re.fullmatch(
        r"CHAR_\d+", str(plan.get("character_id", "")).upper()
    ):
        selection = plan.get("character_reference_selection")
        selected = plan.get("selected_references", {})
        if not isinstance(selection, dict) or selection.get("mode") not in CHARACTER_REFERENCE_MODES[1:]:
            raise StylePackError("Existing-character scene has no resolved adaptive identity-reference mode.")
        if not str(selection.get("selection_evidence", "")).strip():
            raise StylePackError("Existing-character scene has no evidence for its identity-reference subset.")
        if not isinstance(selected, dict) or "character_assembly" not in selected:
            raise StylePackError("Existing-character scene must retain the approved character assembly as its primary identity source.")
    return plan_path.resolve(), plan


ANTHROPOMETRIC_QA_LIMITS = {
    # Conservative StoryArt production limits for an adult canonical standing view.
    # Ratios are measured on the rendered figure, not inferred from the prompt.
    "head_units": (7.0, 8.25),
    "pubic_height_fraction": (0.47, 0.53),
    "lower_to_upper_leg_ratio": (0.75, 1.20),
    "ankle_width_head_ratio": (0.12, 0.30),
    "foot_length_head_ratio": (0.75, 1.20),
    "neck_head_ratio": (0.20, 0.48),
    "neck_jaw_ratio": (0.50, 0.90),
}


def parse_limb_qa_evidence(evidence: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in evidence.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.strip() and value.strip():
            parsed[key.strip().lower()] = value.strip()
    return parsed


def anthropometric_qa_violations(
    evidence: str,
    plan: dict[str, object] | None = None,
) -> list[str]:
    values = parse_limb_qa_evidence(evidence)
    landmark_requirements = {
        "hip_landmark": "FEMORAL_HEAD_CENTER",
        "knee_landmark": "KNEE_JOINT_CENTER",
        "ankle_landmark": "TALOCRURAL_JOINT_CENTER",
    }
    for name, expected in landmark_requirements.items():
        actual = values.get(name, "").upper()
        if actual != expected:
            raise StylePackError(
                f"Schema-4 anthropometric QA requires {name}={expected}; "
                "pubic/crotch points and heel/toe endpoints cannot substitute for joint centers."
            )
    crown_landmark = values.get("crown_landmark", "").upper()
    if crown_landmark not in {"CRANIAL_VERTEX", "CRANIAL_VERTEX_ESTIMATED"}:
        raise StylePackError(
            "Schema-4 anthropometric QA requires crown_landmark=CRANIAL_VERTEX|CRANIAL_VERTEX_ESTIMATED; "
            "the highest hair, hood, hat, or ornament pixel cannot substitute for the anatomical crown."
        )
    foot_pose = values.get("foot_pose", "").upper()
    if foot_pose not in {"FLAT", "TIPTOE", "PLANTAR_FLEXED", "DORSIFLEXED"}:
        raise StylePackError(
            "Schema-4 anthropometric QA requires foot_pose=FLAT|TIPTOE|PLANTAR_FLEXED|DORSIFLEXED."
        )
    projection_view = values.get("view", "").upper()
    if projection_view not in {"FRONT", "SIDE", "BACK", "ASSEMBLY"}:
        raise StylePackError("Schema-4 anthropometric QA requires view=FRONT|SIDE|BACK|ASSEMBLY.")
    foot_length_mode = values.get("foot_length_mode", "").upper()
    if foot_length_mode not in {"MEASURED", "FORESHORTENED_DEFERRED"}:
        raise StylePackError(
            "Schema-4 anthropometric QA requires foot_length_mode=MEASURED|FORESHORTENED_DEFERRED."
        )
    if foot_length_mode == "FORESHORTENED_DEFERRED" and projection_view not in {"FRONT", "BACK"}:
        raise StylePackError(
            "FORESHORTENED_DEFERRED foot length is allowed only for FRONT or BACK; SIDE must measure heel-to-toe length."
        )
    if values.get("heel_endpoint", "").upper() != "VISIBLE" or values.get("toe_endpoint", "").upper() != "VISIBLE":
        raise StylePackError(
            "Schema-4 anthropometric QA requires heel_endpoint=VISIBLE and toe_endpoint=VISIBLE."
        )
    numeric_names = (
        "head_units",
        "pubic_height_fraction",
        "hip_knee",
        "knee_ankle",
        "ankle_width",
        "neck_head_ratio",
        "neck_jaw_ratio",
        "landmark_confidence",
        "crown_confidence",
    )
    numeric: dict[str, float] = {}
    for name in numeric_names:
        try:
            numeric[name] = float(values[name])
        except (KeyError, TypeError, ValueError):
            raise StylePackError(
                f"Schema-4 anthropometric QA requires a numeric {name}= value; words such as matched are invalid."
            )
        if numeric[name] <= 0:
            raise StylePackError(f"Schema-4 anthropometric QA requires {name}= to be greater than zero.")
    if foot_length_mode == "MEASURED":
        try:
            numeric["foot_length"] = float(values["foot_length"])
        except (KeyError, TypeError, ValueError):
            raise StylePackError("Schema-4 measured foot length requires a numeric foot_length= value.")
        if numeric["foot_length"] <= 0:
            raise StylePackError("Schema-4 anthropometric QA requires foot_length= to be greater than zero.")
    if not 0.80 <= numeric["landmark_confidence"] <= 1.0:
        raise StylePackError(
            "Schema-4 anthropometric QA requires landmark_confidence=0.80-1.00. "
            "An uncertain joint estimate cannot produce PASS or an automatic proportion FAIL."
        )
    if not 0.80 <= numeric["crown_confidence"] <= 1.0:
        raise StylePackError(
            "Schema-4 anthropometric QA requires crown_confidence=0.80-1.00. "
            "Hair-volume uncertainty cannot produce a head-count PASS or automatic FAIL."
        )

    measured = {
        "head_units": numeric["head_units"],
        "pubic_height_fraction": numeric["pubic_height_fraction"],
        "lower_to_upper_leg_ratio": numeric["knee_ankle"] / numeric["hip_knee"],
        "ankle_width_head_ratio": numeric["ankle_width"],
        "neck_head_ratio": numeric["neck_head_ratio"],
        "neck_jaw_ratio": numeric["neck_jaw_ratio"],
    }
    if foot_length_mode == "MEASURED":
        measured["foot_length_head_ratio"] = numeric["foot_length"]
    limits = dict(ANTHROPOMETRIC_QA_LIMITS)
    body_contract = plan.get("body_proportion_contract", {}) if isinstance(plan, dict) else {}
    height_contract = body_contract.get("height_in_heads", {}) if isinstance(body_contract, dict) else {}
    if isinstance(height_contract, dict) and height_contract.get("mode") == "EXPLICIT_RANGE":
        try:
            contracted_minimum = float(height_contract["minimum"])
            contracted_maximum = float(height_contract["maximum"])
        except (KeyError, TypeError, ValueError):
            raise StylePackError("Schema-4 body height contract requires numeric minimum and maximum values.")
        if contracted_minimum <= 0 or contracted_maximum < contracted_minimum:
            raise StylePackError("Schema-4 body height contract has an invalid explicit range.")
        global_minimum, global_maximum = ANTHROPOMETRIC_QA_LIMITS["head_units"]
        limits["head_units"] = (
            max(global_minimum, contracted_minimum),
            min(global_maximum, contracted_maximum),
        )

    violations = []
    for name, value in measured.items():
        minimum, maximum = limits[name]
        if not minimum <= value <= maximum:
            violations.append(f"{name}={value:.4g} outside {minimum:g}-{maximum:g}")
    return violations


def evaluate_generation_qa(
    plan: dict[str, object],
    args: argparse.Namespace,
    *,
    include_results: bool = False,
) -> tuple[list[str], str, list[str]] | tuple[list[str], str, list[str], dict[str, str]]:
    """Return failed checks, resolved stage id, and all QA checks required for the record."""

    workflow = plan["generation_workflow"]
    required = ["ATTACHMENTS", "CANVAS", "STAGE_LAYER"]
    stage_id = "SINGLE_PASS"
    if workflow["mode"] == "MULTI_STAGE":
        stage_id = args.stage_id.upper()
        stages = workflow["stages"]
        matches = [stage for stage in stages if stage["stage_id"] == stage_id]
        if len(matches) != 1:
            known = ", ".join(stage["stage_id"] for stage in stages)
            raise StylePackError(f"--stage-id must name one prepared stage: {known}.")
        required.extend(matches[0].get("required_qa", []))
    elif args.stage_id:
        raise StylePackError("--stage-id is only valid for a MULTI_STAGE reference plan.")
    else:
        if plan.get("face_review", {}).get("face_visible"):
            required.append("FACE_GEOMETRY")
        if plan.get("canvas_contract", {}).get("full_figure"):
            required.extend(("BODY_SILHOUETTE", "BODY_PROPORTIONS"))

    semantic_qa_schema = int(plan.get("semantic_qa_schema", 0) or 0)
    semantic_qa = semantic_qa_schema >= 1
    if semantic_qa and "STYLE" not in required:
        required.append("STYLE")
    if (
        semantic_qa_schema >= 2
        and stage_id in {"02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY"}
        and "BODY_RENDERING_STYLE" not in required
    ):
        required.append("BODY_RENDERING_STYLE")
    if (
        semantic_qa_schema >= 3
        and stage_id in {"02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY"}
        and "LIMB_PROPORTIONS" not in required
    ):
        required.append("LIMB_PROPORTIONS")
    scene_contract = plan.get("scene_contract")
    if (
        semantic_qa
        and plan.get("generation_purpose") == "SCENE"
        and isinstance(scene_contract, dict)
        and scene_contract.get("applicable")
        and not scene_contract.get("has_character")
    ):
        required.extend(
            (
                "SUBJECT_ACCURACY",
                "NO_UNREQUESTED_CHARACTERS",
                "FOCAL_HIERARCHY",
                "LIGHTING",
                "BACKGROUND",
                "COMPOSITION",
            )
        )
        if scene_contract.get("output_use") == "WALLPAPER":
            required.extend(("DISTANCE_READABILITY", "DESKTOP_USABILITY"))
        elif scene_contract.get("output_use") == "PROMO_POSTER":
            required.extend(("POSTER_READABILITY", "COPY_SAFE_AREA"))
        if scene_contract.get("scene_kind") == "LOCATION":
            required.append("DEPTH_AND_SCALE")
        elif scene_contract.get("scene_kind") == "PHENOMENON":
            required.append("PHENOMENON_CAUSALITY")
        elif scene_contract.get("scene_kind") == "ARTIFACT":
            required.append("ARTIFACT_INTEGRITY")

    qa_values = {
        "ATTACHMENTS": args.qa_attachments,
        "CANVAS": args.qa_canvas,
        "STAGE_LAYER": args.qa_stage_layer,
        "FACE_GEOMETRY": args.qa_face,
        "BODY_SILHOUETTE": args.qa_body_silhouette,
        "BODY_PROPORTIONS": args.qa_body_proportions,
        "LIMB_PROPORTIONS": getattr(args, "qa_limb_proportions", "NOT_CHECKED"),
    }
    if semantic_qa:
        view_value = getattr(args, "qa_view", "NOT_CHECKED")
        qa_values.update({
            "STYLE": getattr(args, "qa_style", "NOT_CHECKED"),
            "BODY_RENDERING_STYLE": getattr(args, "qa_body_style", "NOT_CHECKED"),
            "EXPRESSION": getattr(args, "qa_expression", "NOT_CHECKED"),
            "NEUTRAL_BACKDROP": getattr(args, "qa_neutral_backdrop", "NOT_CHECKED"),
            "FRONT_VIEW": view_value,
            "SIDE_VIEW": view_value,
            "BACK_VIEW": view_value,
            "SAFE_COVERAGE": getattr(args, "qa_safe_coverage", "NOT_CHECKED"),
            "CLOTHING_TOPOLOGY": getattr(args, "qa_clothing_topology", "NOT_CHECKED"),
            "MULTIVIEW_CONSISTENCY": getattr(args, "qa_multiview_consistency", "NOT_CHECKED"),
            "CLOTHING": getattr(args, "qa_clothing", "NOT_CHECKED"),
            "POSE_CONTACTS": getattr(args, "qa_pose_contacts", "NOT_CHECKED"),
            "CAMERA": getattr(args, "qa_camera", "NOT_CHECKED"),
            "LIGHTING": getattr(args, "qa_lighting", "NOT_CHECKED"),
            "BACKGROUND": getattr(args, "qa_background", "NOT_CHECKED"),
            "COMPOSITION": getattr(args, "qa_composition", "NOT_CHECKED"),
            "SUBJECT_ACCURACY": getattr(args, "qa_subject_accuracy", "NOT_CHECKED"),
            "NO_UNREQUESTED_CHARACTERS": getattr(args, "qa_no_unrequested_characters", "NOT_CHECKED"),
            "FOCAL_HIERARCHY": getattr(args, "qa_focal_hierarchy", "NOT_CHECKED"),
            "DISTANCE_READABILITY": getattr(args, "qa_distance_readability", "NOT_CHECKED"),
            "DESKTOP_USABILITY": getattr(args, "qa_desktop_usability", "NOT_CHECKED"),
            "POSTER_READABILITY": getattr(args, "qa_poster_readability", "NOT_CHECKED"),
            "COPY_SAFE_AREA": getattr(args, "qa_copy_safe_area", "NOT_CHECKED"),
            "DEPTH_AND_SCALE": getattr(args, "qa_depth_and_scale", "NOT_CHECKED"),
            "PHENOMENON_CAUSALITY": getattr(args, "qa_phenomenon_causality", "NOT_CHECKED"),
            "ARTIFACT_INTEGRITY": getattr(args, "qa_artifact_integrity", "NOT_CHECKED"),
        })
    required_names = sorted(set(required))
    missing = [name for name in required_names if name in qa_values and qa_values[name] == "NOT_CHECKED"]
    if missing:
        raise StylePackError("Required post-generation QA was not performed: " + ", ".join(missing))
    if "LIMB_PROPORTIONS" in required_names and qa_values["LIMB_PROPORTIONS"] == "PASS":
        evidence = str(getattr(args, "limb_qa_evidence", "") or "").strip()
        required_tokens = ("source=", "head_units=", "hip_knee=", "knee_ankle=", "ankle_width=", "foot_length=")
        if semantic_qa_schema >= 4:
            required_tokens += (
                "pubic_height_fraction=", "neck_head_ratio=", "neck_jaw_ratio=", "foot_pose=",
                "hip_landmark=", "knee_landmark=", "ankle_landmark=", "landmark_confidence=",
                "crown_landmark=", "crown_confidence=",
                "view=", "foot_length_mode=", "heel_endpoint=", "toe_endpoint=",
            )
        missing_tokens = [token for token in required_tokens if token not in evidence]
        if missing_tokens:
            raise StylePackError(
                "LIMB_PROPORTIONS=PASS requires --limb-qa-evidence with: "
                + ", ".join(required_tokens)
            )
        if semantic_qa_schema >= 4:
            body_contract = plan.get("body_proportion_contract", {})
            user_override = bool(
                isinstance(body_contract, dict)
                and body_contract.get("user_approved_nonstandard_proportions")
            )
            violations = anthropometric_qa_violations(evidence, plan)
            if violations and not user_override:
                qa_values["LIMB_PROPORTIONS"] = "FAIL"
                qa_values["BODY_PROPORTIONS"] = "FAIL"
    failed = [name for name in required_names if qa_values.get(name) == "FAIL"]
    results = {name: str(qa_values.get(name, "NOT_CHECKED")) for name in required_names}
    if include_results:
        return failed, stage_id, required_names, results
    return failed, stage_id, required_names


def validate_prior_stages(paths: StylePaths, plan: dict[str, object], request_id: str, stage_id: str) -> None:
    workflow = plan["generation_workflow"]
    if workflow["mode"] != "MULTI_STAGE":
        return
    stages = workflow["stages"]
    index = next(index for index, stage in enumerate(stages) if stage["stage_id"] == stage_id)
    rows = read_csv(paths.generation_manifest)

    def recorded_stage(row: dict[str, str]) -> str:
        # Notes are used only to find the newest claimed stage. They never
        # establish passage: a corrupt/QA-less row therefore still blocks an
        # older successful result for the same prerequisite.
        match = re.search(r"\[STAGE_ID=([^\]]+)\]", row.get("notes", ""))
        return match.group(1) if match else ""

    missing: list[str] = []
    for prior in (stage["stage_id"] for stage in stages[:index]):
        candidates = [
            row for row in rows
            if row.get("request_id") == request_id and recorded_stage(row) == prior
        ]
        newest = candidates[-1] if candidates else None
        if not newest:
            missing.append(prior)
            continue
        receipt = generation_qa_evidence(newest)
        if not (
            newest.get("status") == "STAGING"
            and receipt
            and receipt.get("record_status") == "STAGING"
        ):
            missing.append(prior)
            continue
        contract = json.loads(Path(str(receipt["qa_contract"])).read_text(encoding="utf-8"))
        if contract.get("stage_id") != prior:
            missing.append(prior)
    if missing:
        raise StylePackError("Later stage is blocked until earlier staging QA passes: " + ", ".join(missing))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    os.replace(temporary, path)


def ensure_csv(path: Path, fields: Sequence[str]) -> None:
    if not path.exists():
        write_csv(path, fields, [])
        return
    header = csv_header(path)
    if header == list(fields):
        return
    if header and set(header).issubset(set(fields)):
        # Additive schema migration only: preserve every row and leave new fields blank.
        write_csv(path, fields, read_csv(path))


def next_id(rows: Sequence[dict[str, str]], field: str, prefix: str, width: int = 4) -> str:
    highest = 0
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)$")
    for row in rows:
        match = pattern.match(row.get(field, ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}_{highest + 1:0{width}d}"


def copy_unique(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    candidate = destination
    index = 2
    while candidate.exists():
        if source.resolve() == candidate.resolve():
            return candidate
        candidate = destination.with_name(f"{destination.stem}_{index:02d}{destination.suffix}")
        index += 1
    shutil.copy2(source, candidate)
    return candidate


def copy_or_reuse_identical(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and sha256(destination) == sha256(source):
        return destination
    return copy_unique(source, destination)


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def image_info(path: Path) -> tuple[str, int | str, int | str]:
    """Return format and dimensions without requiring Pillow.

    Pillow is used when present. The fallback covers common reference formats;
    unsupported dimension headers remain blank rather than blocking ingestion.
    """

    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as image:
            return (image.format or path.suffix.lstrip(".").upper(), image.width, image.height)
    except (ImportError, OSError):
        pass

    data = path.read_bytes()[:65536]
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return "PNG", width, height
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            width, height = struct.unpack("<HH", data[6:10])
            return "GIF", width, height
        if data.startswith(b"BM") and len(data) >= 26:
            width, height = struct.unpack("<ii", data[18:26])
            return "BMP", abs(width), abs(height)
        if data.startswith(b"\xff\xd8"):
            index = 2
            while index + 9 < len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                index += 2
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    continue
                if index + 2 > len(data):
                    break
                length = struct.unpack(">H", data[index : index + 2])[0]
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    height, width = struct.unpack(">HH", data[index + 3 : index + 7])
                    return "JPEG", width, height
                index += max(length, 2)
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8X" and len(data) >= 30:
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return "WEBP", width, height
            return "WEBP", "", ""
        if data[:4] in (b"II*\x00", b"MM\x00*"):
            return "TIFF", "", ""
    except (IndexError, struct.error, ValueError):
        pass
    return path.suffix.lstrip(".").upper(), "", ""


def template_text(name: str, replacements: dict[str, str]) -> str:
    path = TEMPLATE_ROOT / name
    if not path.exists():
        raise StylePackError(f"Missing template: {path}")
    text = path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def write_if_missing(path: Path, text: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_metadata(paths: StylePaths) -> dict[str, object]:
    if not paths.metadata.exists():
        raise StylePackError(
            f"Style pack is not initialized by this manager: {paths.pack}. "
            "Run the init command or use the existing style-specific tools."
        )
    return json.loads(paths.metadata.read_text(encoding="utf-8"))


def require_initialized(paths: StylePaths) -> dict[str, object]:
    metadata = load_metadata(paths)
    if not paths.generations.exists():
        raise StylePackError(f"Missing generation directory: {paths.generations}")
    return metadata


def matching_discovered_style(paths: StylePaths) -> DiscoveredStyle | None:
    for style in discover_style_packs(paths.workspace):
        if Path(style.pack_path).resolve() == paths.pack.resolve():
            return style
    return None


def ensure_generation_library(paths: StylePaths) -> dict[str, object]:
    """Ensure generation bookkeeping for managed or ready legacy packs.

    A legacy reference pack is never modified. Only its new canonical sibling
    generation directory and manifests are created when generation work begins.
    """

    if paths.metadata.exists():
        metadata = load_metadata(paths)
    else:
        discovered = matching_discovered_style(paths)
        if discovered is None:
            raise StylePackError(f"No discovered style pack matches {paths.style_name}.")
        if not discovered.can_generate:
            raise StylePackError(
                f"Discovered legacy style is not ready for local generation: {discovered.local_readiness}. "
                "Complete its reviewed local working library first; web export is unrelated."
            )
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "style_name": discovered.style_name,
            "style_slug": discovered.slug,
            "reference_pack": discovered.pack_path,
            "management": discovered.management,
            "status": discovered.local_readiness,
        }

    for relative in GENERATION_DIRECTORIES:
        (paths.generations / relative).mkdir(parents=True, exist_ok=True)
    ensure_csv(paths.generation_manifest, GENERATION_FIELDS)
    ensure_csv(paths.character_registry, CHARACTER_FIELDS)
    generation_metadata = paths.generations / ".style-generations.json"
    if not generation_metadata.exists():
        generation_metadata.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "style_name": str(metadata.get("style_name") or paths.style_name),
                    "style_slug": str(metadata.get("style_slug") or paths.slug),
                    "reference_pack": str(paths.pack),
                    "created_at": iso_now(),
                    "legacy_reference_pack": not paths.metadata.exists(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return metadata


def command_init(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    if paths.metadata.exists():
        raise StylePackError(
            f"Style pack is already initialized: {paths.pack}. "
            "Use ingest to add references or validate to inspect it."
        )
    if paths.pack.exists() and not paths.metadata.exists():
        raise StylePackError(
            f"Refusing to adopt existing unmanaged directory {paths.pack}. "
            "This protects legacy packs such as EXAMPLE_PROJECT_PACK from accidental changes."
        )

    for relative in PACK_DIRECTORIES:
        (paths.pack / relative).mkdir(parents=True, exist_ok=True)
    for relative in GENERATION_DIRECTORIES:
        (paths.generations / relative).mkdir(parents=True, exist_ok=True)
    (paths.workspace / "GENERATION_RESULTS").mkdir(parents=True, exist_ok=True)

    created_at = iso_now()
    replacements = {
        "STYLE_NAME": paths.style_name,
        "STYLE_SLUG": paths.slug,
        "CREATED_AT": created_at,
    }
    write_if_missing(paths.pack / "README.md", template_text("README.md", replacements))
    upload = paths.pack / "03_UPLOAD_TO_WEB"
    write_if_missing(upload / "00_STYLE_PROFILE.md", template_text("00_STYLE_PROFILE_TEMPLATE.md", replacements))
    write_if_missing(upload / "00_STYLE_NEGATIVE.md", template_text("00_STYLE_NEGATIVE_TEMPLATE.md", replacements))
    write_if_missing(upload / "00_FIDELITY_PROFILES.md", template_text("00_FIDELITY_PROFILES.md", replacements))
    write_if_missing(upload / "00_REFERENCE_PLAN_TEMPLATE.md", template_text("00_REFERENCE_PLAN_TEMPLATE.md", replacements))
    write_if_missing(upload / "00_GENERATION_QA.md", template_text("00_GENERATION_QA.md", replacements))
    write_if_missing(upload / "00_WEB_EXPORT_README.md", template_text("00_WEB_EXPORT_README.md", replacements))

    ensure_csv(paths.inventory, INVENTORY_FIELDS)
    ensure_csv(paths.references, REFERENCE_FIELDS)
    ensure_csv(paths.duplicates, ("duplicate_source_id", "canonical_source_id", "sha256", "stored_relative_path", "notes"))
    ensure_csv(paths.upload_manifest, REFERENCE_FIELDS)
    ensure_csv(paths.generation_manifest, GENERATION_FIELDS)
    ensure_csv(paths.character_registry, CHARACTER_FIELDS)

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "style_name": paths.style_name,
        "style_slug": paths.slug,
        "created_at": created_at,
        "workspace": str(paths.workspace),
        "reference_pack": str(paths.pack),
        "generations": str(paths.generations),
        "source_directory": str(Path(args.source).resolve()) if args.source else "",
        "status": "REVIEW_REQUIRED",
    }
    paths.metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"REFERENCE_PACK={paths.pack}")
    print(f"GENERATIONS={paths.generations}")
    print("STATUS=INITIALIZED_REVIEW_REQUIRED")
    if args.source:
        ingest_source(paths, Path(args.source), recursive=not args.non_recursive)


def discover_images(source: Path, recursive: bool) -> list[Path]:
    iterator = source.rglob("*") if recursive else source.glob("*")
    return sorted(
        (path for path in iterator if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS),
        key=lambda item: str(item).lower(),
    )


def sanitize_relative_path(relative: Path) -> Path:
    parts = [safe_component(part, "item") for part in relative.parts[:-1]]
    filename = safe_component(relative.stem, "image") + relative.suffix.lower()
    return Path(*parts, filename) if parts else Path(filename)


def ingest_source(paths: StylePaths, source: Path, recursive: bool = True) -> None:
    require_initialized(paths)
    source = source.resolve()
    if not source.is_dir():
        raise StylePackError(f"Source directory does not exist: {source}")
    if is_relative_to(paths.pack, source) or is_relative_to(paths.generations, source):
        raise StylePackError("Source directory contains the destination style pack; choose the actual reference folder.")

    images = discover_images(source, recursive)
    if not images:
        raise StylePackError(f"No supported images found in {source}")

    inventory = read_csv(paths.inventory)
    references = read_csv(paths.references)
    duplicates = read_csv(paths.duplicates)
    existing_keys = {(row.get("original_path", ""), row.get("sha256", "")) for row in inventory}
    canonical_by_hash: dict[str, str] = {}
    for row in inventory:
        if row.get("sha256") and not row.get("exact_duplicate_of"):
            canonical_by_hash.setdefault(row["sha256"], row.get("source_id", ""))

    added = 0
    skipped = 0
    duplicate_count = 0
    for source_file in images:
        file_hash = sha256(source_file)
        key = (str(source_file), file_hash)
        if key in existing_keys:
            skipped += 1
            continue

        source_id = next_id(inventory, "source_id", "SRC", 5)
        reference_id = next_id(references, "reference_id", "REF", 5)
        relative = sanitize_relative_path(source_file.relative_to(source))
        stored = copy_unique(source_file, paths.pack / "00_SOURCE_ORIGINALS" / relative)
        stored_relative = stored.relative_to(paths.pack).as_posix()
        image_format, width, height = image_info(stored)
        canonical = canonical_by_hash.get(file_hash, "")
        status = "DUPLICATE" if canonical else "REVIEW_REQUIRED"
        if canonical:
            duplicate_count += 1
            duplicates.append(
                {
                    "duplicate_source_id": source_id,
                    "canonical_source_id": canonical,
                    "sha256": file_hash,
                    "stored_relative_path": stored_relative,
                    "notes": "Exact byte duplicate retained in source archive; do not use both as positive references.",
                }
            )
        else:
            canonical_by_hash[file_hash] = source_id

        inventory.append(
            {
                "source_id": source_id,
                "stored_relative_path": stored_relative,
                "original_path": str(source_file),
                "sha256": file_hash,
                "width": width,
                "height": height,
                "format": image_format,
                "bytes": stored.stat().st_size,
                "exact_duplicate_of": canonical,
                "status": status,
                "ingested_at": iso_now(),
                "notes": "Original copied without modification.",
            }
        )
        references.append(
            {
                "reference_id": reference_id,
                "filename": stored.name,
                "stored_relative_path": stored_relative,
                "primary_role": "UNCLASSIFIED",
                "secondary_roles": "",
                "character_id": "",
                "source_filename": source_file.name,
                "source_sha256": file_hash,
                "crop_box": "",
                "shot_type": "",
                "expression": "",
                "lighting": "",
                "background_type": "",
                "text_present": "UNKNOWN",
                "generator_safe": "NO" if canonical else "REVIEW_REQUIRED",
                "use_for": "Human review and role assignment.",
                "do_not_use_for": "Generation until visually reviewed." if not canonical else "Positive reference; exact duplicate.",
                "status": status,
                "user_approved": "NO",
                "notes": "Initial source inventory row.",
            }
        )
        existing_keys.add(key)
        added += 1

    write_csv(paths.inventory, INVENTORY_FIELDS, inventory)
    write_csv(paths.references, REFERENCE_FIELDS, references)
    write_csv(paths.duplicates, ("duplicate_source_id", "canonical_source_id", "sha256", "stored_relative_path", "notes"), duplicates)

    metadata = load_metadata(paths)
    metadata["source_directory"] = str(source)
    metadata["last_ingested_at"] = iso_now()
    metadata["status"] = "REVIEW_REQUIRED"
    paths.metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"SOURCE={source}")
    print(f"DISCOVERED={len(images)}")
    print(f"ADDED={added}")
    print(f"SKIPPED_ALREADY_INGESTED={skipped}")
    print(f"EXACT_DUPLICATES={duplicate_count}")
    print("STATUS=VISUAL_REVIEW_REQUIRED")


def command_ingest(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    metadata = require_initialized(paths)
    source_text = args.source or str(metadata.get("source_directory", ""))
    if not source_text:
        raise StylePackError("No source directory supplied and none is recorded in pack metadata.")
    ingest_source(paths, Path(source_text), recursive=not args.non_recursive)


def resolve_existing_file(value: str, paths: StylePaths) -> Path:
    candidate = Path(value)
    alternatives = [candidate]
    if not candidate.is_absolute():
        alternatives.extend([paths.workspace / candidate, paths.pack / candidate, paths.generations / candidate])
    for alternative in alternatives:
        if alternative.is_file():
            return alternative.resolve()
    raise StylePackError(f"Image file does not exist: {value}")


def parse_crop_box(value: str) -> str:
    if not value:
        return ""
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise StylePackError("Crop box must be left,top,right,bottom.")
    try:
        left, top, right, bottom = (int(part) for part in parts)
    except ValueError as error:
        raise StylePackError("Crop box values must be integers.") from error
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise StylePackError("Crop box must have non-negative origin and positive width and height.")
    return f"{left},{top},{right},{bottom}"


def command_classify(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    require_initialized(paths)
    role = args.role.upper()
    status = args.status.upper()
    if role not in ROLES:
        raise StylePackError(f"Unsupported role: {role}")
    if status not in {"TEST", "APPROVED", "REJECTED", "ANCHOR"}:
        raise StylePackError(f"Unsupported reference status: {status}")
    if role == "ANCHOR_STYLE" and status != "ANCHOR":
        raise StylePackError("ANCHOR_STYLE must use status ANCHOR.")
    if role == "APPROVED_FRAME" and status != "APPROVED":
        raise StylePackError("APPROVED_FRAME must use status APPROVED.")
    if status in {"APPROVED", "ANCHOR"} and not args.user_approved:
        raise StylePackError("APPROVED and ANCHOR require --user-approved after direct user confirmation.")

    source_file = resolve_existing_file(args.file, paths)
    provenance_file = resolve_existing_file(args.source_reference, paths) if args.source_reference else source_file
    crop_box = parse_crop_box(args.crop_box)
    destination_root = (
        paths.pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "REJECTED"
        if status == "REJECTED"
        else paths.pack / ROLE_DIRECTORIES[role]
    )
    destination_name = f"{role}_{safe_component(source_file.stem, 'reference')}{source_file.suffix.lower()}"
    destination = copy_or_reuse_identical(source_file, destination_root / destination_name)

    references = read_csv(paths.references)
    reference_id = next_id(references, "reference_id", "REF", 5)
    references.append(
        {
            "reference_id": reference_id,
            "filename": destination.name,
            "stored_relative_path": destination.relative_to(paths.pack).as_posix(),
            "primary_role": role,
            "secondary_roles": ";".join(item.upper() for item in args.secondary_role),
            "character_id": args.character_id,
            "source_filename": provenance_file.name,
            "source_sha256": sha256(provenance_file),
            "crop_box": crop_box,
            "shot_type": args.shot_type,
            "expression": args.expression,
            "lighting": args.lighting,
            "background_type": args.background_type,
            "text_present": args.text_present.upper(),
            "generator_safe": "YES" if args.generator_safe and status != "REJECTED" else "NO",
            "use_for": args.use_for,
            "do_not_use_for": args.do_not_use_for or ("Positive generation reference." if status == "REJECTED" else ""),
            "status": status,
            "user_approved": "YES" if args.user_approved else "NO",
            "notes": args.notes,
        }
    )
    write_csv(paths.references, REFERENCE_FIELDS, references)
    print(f"REFERENCE_ID={reference_id}")
    print(f"FILE={destination}")
    print(f"ROLE={role}")
    print(f"STATUS={status}")


def command_finalize(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    require_initialized(paths)
    if not args.user_approved:
        raise StylePackError("Finalization requires --user-approved after direct review approval.")
    references = read_csv(paths.references)
    selected = [
        row
        for row in references
        if row.get("status") in {"APPROVED", "ANCHOR"}
        and row.get("user_approved") == "YES"
        and row.get("generator_safe") == "YES"
        and row.get("primary_role") in ROLES
    ]
    if not selected:
        raise StylePackError("No user-approved, generator-safe references are ready for finalization.")

    upload = paths.pack / "03_UPLOAD_TO_WEB"
    upload_rows: list[dict[str, object]] = []
    for row in selected:
        source = paths.pack / row["stored_relative_path"]
        if not source.is_file():
            raise StylePackError(f"Approved reference is missing: {source}")
        destination = copy_or_reuse_identical(source, upload / row["filename"])
        copied = dict(row)
        copied["stored_relative_path"] = destination.relative_to(paths.pack).as_posix()
        upload_rows.append(copied)
    write_csv(paths.upload_manifest, REFERENCE_FIELDS, upload_rows)

    metadata = load_metadata(paths)
    metadata["last_finalized_at"] = iso_now()
    metadata["status"] = "FINALIZED_APPROVED"
    paths.metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FINALIZED_REFERENCES={len(upload_rows)}")
    print(f"UPLOAD_DIRECTORY={upload}")
    print("STATUS=FINALIZED_APPROVED")


def archive_generation(paths: StylePaths, image: Path, description: str) -> Path:
    archive = paths.workspace / "GENERATION_RESULTS"
    archive.mkdir(parents=True, exist_ok=True)
    if is_relative_to(image, archive):
        return image.resolve()
    timestamp = local_now().strftime("%Y-%m-%d_%H-%M-%S")
    label = safe_component(description, "generation").lower()[:60]
    return copy_unique(image, archive / f"{timestamp}_{label}{image.suffix.lower()}")


def existing_archive_for_image(paths: StylePaths, image: Path) -> Path | None:
    image_hash = sha256(image)
    for row in reversed(read_csv(paths.generation_manifest)):
        archive_text = row.get("archive_file", "")
        if not archive_text:
            continue
        archive = Path(archive_text)
        if archive.is_file() and sha256(archive) == image_hash:
            return archive
    return None


def ensure_generation_archived(paths: StylePaths, image: Path, description: str) -> Path:
    return existing_archive_for_image(paths, image) or archive_generation(paths, image, description)


def generation_id(rows: Sequence[dict[str, str]]) -> str:
    base = local_now().strftime("GEN_%Y%m%d_%H%M%S")
    existing = {row.get("generation_id", "") for row in rows}
    if base not in existing:
        return base
    index = 2
    while f"{base}_{index:02d}" in existing:
        index += 1
    return f"{base}_{index:02d}"


def append_generation(
    paths: StylePaths,
    *,
    request_id: str,
    character_id: str,
    status: str,
    fidelity: int,
    risk_level: str,
    description: str,
    source_image: Path,
    archive_file: Path,
    style_file: Path,
    parent_generation: str = "",
    reference_plan: str = "",
    qa_evidence: str = "",
    qa_output_sha256: str = "",
    qa_receipt_sha256: str = "",
    qa_contract_sha256: str = "",
    qa_plan_sha256: str = "",
    scene_kind: str = "",
    output_use: str = "",
    aspect_ratio: str = "",
    typography_mode: str = "",
    notes: str = "",
) -> str:
    risk_level = risk_level.upper()
    if not RISK_LABEL_RE.fullmatch(risk_level):
        raise StylePackError(f"Every generated image needs a D1-D10 risk marker, got: {risk_level}")
    existing_marker = re.search(r"\[(D(?:[1-9]|10))\]\s*$", description, flags=re.IGNORECASE)
    if existing_marker and existing_marker.group(1).upper() != risk_level:
        raise StylePackError("Description risk marker conflicts with --risk-level.")
    marked_description = description if existing_marker else f"{description.rstrip()} [{risk_level}]"
    rows = read_csv(paths.generation_manifest)
    new_id = generation_id(rows)
    rows.append(
        {
            "generation_id": new_id,
            "created_at": iso_now(),
            "style_name": paths.style_name,
            "request_id": request_id,
            "character_id": character_id,
            "status": status,
            "fidelity": fidelity,
            "risk_level": risk_level,
            "description": marked_description,
            "source_image": str(source_image),
            "archive_file": str(archive_file),
            "style_file": str(style_file),
            "parent_generation": parent_generation,
            "reference_plan": reference_plan,
            "qa_evidence": qa_evidence,
            "qa_output_sha256": qa_output_sha256,
            "qa_receipt_sha256": qa_receipt_sha256,
            "qa_contract_sha256": qa_contract_sha256,
            "qa_plan_sha256": qa_plan_sha256,
            "scene_kind": scene_kind,
            "output_use": output_use,
            "aspect_ratio": aspect_ratio,
            "typography_mode": typography_mode,
            "notes": notes,
        }
    )
    write_csv(paths.generation_manifest, GENERATION_FIELDS, rows)
    return new_id


def write_generation_qa_evidence(
    style_file: Path,
    reference_plan: Path,
    *,
    stage_id: str,
    qa_required: Sequence[str],
    qa_results: dict[str, str],
    status: str,
) -> Path:
    """Emit immutable plan/contract snapshots only after evaluated high-fidelity QA."""
    evidence_path = style_file.with_suffix(style_file.suffix + ".qa-evidence.json")
    plan_snapshot = style_file.with_suffix(style_file.suffix + ".qa-plan.json")
    contract_path = style_file.with_suffix(style_file.suffix + ".qa-contract.json")
    shutil.copy2(reference_plan, plan_snapshot)
    contract = {
        "schema_version": 1,
        "contract_version": 1,
        "plan_snapshot": str(plan_snapshot),
        "plan_content_sha256": sha256(plan_snapshot),
        "stage_id": stage_id,
        "expected_qa_layers": sorted(qa_required),
        "qa_results": {name: qa_results[name] for name in sorted(qa_required)},
    }
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    receipt = {
        "schema_version": 2,
        "record_status": status,
        "output_sha256": sha256(style_file),
        "qa_contract": str(contract_path),
        "qa_contract_sha256": sha256(contract_path),
    }
    evidence_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return evidence_path


def qa_manifest_digests(style_file: Path, evidence_path: Path) -> dict[str, str]:
    """Manifest-issued binding for the exact output and its three durable snapshots."""
    receipt = json.loads(evidence_path.read_text(encoding="utf-8"))
    contract_path = Path(str(receipt["qa_contract"]))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    plan_snapshot = Path(str(contract["plan_snapshot"]))
    return {
        "qa_output_sha256": sha256(style_file),
        "qa_receipt_sha256": sha256(evidence_path),
        "qa_contract_sha256": sha256(contract_path),
        "qa_plan_sha256": sha256(plan_snapshot),
    }


def command_record_generation(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    ensure_generation_library(paths)
    if args.fidelity not in {30, 50, 70, 90, 100}:
        raise StylePackError("Fidelity must be one of 30, 50, 70, 90, or 100.")
    status = args.status.upper()
    if status not in {"STAGING", "TEST", "REJECTED"}:
        raise StylePackError("New generation records may be STAGING, TEST, or REJECTED. Use an approval command after confirmation.")
    image = resolve_existing_file(args.image, paths)
    # Archive first: even a QA failure or an invalid record attempt is still a produced generation.
    archive_file = archive_generation(paths, image, args.description)
    request_id = safe_component(args.request_id, "request")
    execution_guard_path = paths.generations / "00_PENDING" / request_id / "EXECUTION_GUARD.json"
    try:
        require_execution_started(execution_guard_path, request_id)
    except ExecutionGuardError as error:
        raise StylePackError(
            f"Generated image was archived at {archive_file}, but recording is blocked by the execution guard: {error}"
        ) from error
    plan: dict[str, object] | None = None
    risk_level = args.risk_level.upper() if args.risk_level else ""
    stage_id = ""
    qa_required: list[str] = []
    qa_failed: list[str] = []
    qa_results: dict[str, str] = {}
    if args.fidelity >= 70:
        reference_plan_path, plan = validate_reference_plan_for_recording(paths, args.reference_plan, args.fidelity)
        planned_risk = str(plan.get("risk_assessment", {}).get("generation_risk", "")).upper()
        if risk_level and risk_level != planned_risk:
            raise StylePackError(f"Recorded risk {risk_level} does not match prepared plan risk {planned_risk}.")
        risk_level = planned_risk
        qa_failed, stage_id, qa_required, qa_results = evaluate_generation_qa(plan, args, include_results=True)
        validate_prior_stages(paths, plan, request_id, stage_id)
        workflow = plan["generation_workflow"]
        if qa_failed:
            status = "REJECTED"
        if workflow["mode"] == "MULTI_STAGE":
            final_stage = workflow["stages"][-1]["stage_id"]
            if stage_id == final_stage and status == "STAGING":
                raise StylePackError("The final multi-stage image must be recorded as TEST or REJECTED, not STAGING.")
            if stage_id != final_stage and status == "TEST":
                raise StylePackError("An intermediate multi-stage image must be recorded as STAGING or REJECTED.")
        elif status == "STAGING" and plan.get("generation_purpose") != "TECHNICAL_TEST":
            raise StylePackError("Single-pass STAGING is reserved for a validated TECHNICAL_TEST plan.")
    elif args.reference_plan:
        reference_plan_path = Path(args.reference_plan).resolve()
        if not reference_plan_path.is_file():
            raise StylePackError(f"Reference plan does not exist: {reference_plan_path}")
        if status == "STAGING":
            raise StylePackError("STAGING requires a validated high-fidelity multi-stage reference plan.")
    else:
        reference_plan_path = None
        if status == "STAGING":
            raise StylePackError("STAGING requires a validated high-fidelity multi-stage reference plan.")
    if not RISK_LABEL_RE.fullmatch(risk_level):
        raise StylePackError("Every generated image needs --risk-level D1-D10 or a schema-5 reference plan containing it.")
    pending_root = paths.generations / "00_PENDING" / request_id
    if plan and plan.get("generation_purpose") == "CHARACTER_BASE":
        kit = plan.get("character_kit", {})
        stage_directory = kit.get("stage_directories", {}).get(stage_id)
        if not stage_directory:
            raise StylePackError(f"CHARACTER_BASE plan has no storage directory for stage {stage_id}.")
        kit_folder = Path(str(kit.get("folder", ""))).resolve()
        if not is_relative_to(kit_folder, paths.generations) or not kit_folder.is_dir():
            raise StylePackError(f"Character kit folder is missing or outside the generation library: {kit_folder}")
        pending_root = kit_folder / stage_directory
    if status == "REJECTED":
        pending_root = pending_root / "REJECTED"
    style_file = copy_unique(image, pending_root / image.name)
    reference_plan = str(reference_plan_path) if reference_plan_path else ""
    qa_evidence = ""
    qa_binding: dict[str, str] = {}
    qa_note = ""
    if plan:
        qa_note = f"[STAGE_ID={stage_id}] [QA_REQUIRED={','.join(qa_required)}]"
        if qa_failed:
            qa_note += f" [AUTO_REJECT_QA={','.join(qa_failed)}]"
        elif status in {"TEST", "STAGING"}:
            qa_note += f" [QA_OUTPUT_SHA256={sha256(image)}]"
        limb_qa_evidence = str(getattr(args, "limb_qa_evidence", "") or "").strip()
        if limb_qa_evidence:
            qa_note += f" [LIMB_QA={limb_qa_evidence}]"
            if int(plan.get("semantic_qa_schema", 0) or 0) >= 4:
                violations = anthropometric_qa_violations(limb_qa_evidence, plan)
                if violations:
                    qa_note += f" [ANTHROPOMETRIC_VIOLATIONS={' | '.join(violations)}]"
    combined_notes = " ".join(part for part in (args.notes.strip(), qa_note) if part)
    if plan and not qa_failed and all(result == "PASS" for result in qa_results.values()) and status in {"TEST", "STAGING"}:
        # This receipt is deliberately not accepted as a CLI input: notes and
        # low-fidelity --reference-plan values never establish QA completion.
        qa_evidence_path = write_generation_qa_evidence(
                style_file,
                reference_plan_path,
                stage_id=stage_id,
                qa_required=qa_required,
                qa_results=qa_results,
                status=status,
            )
        qa_evidence = str(qa_evidence_path)
        qa_binding = qa_manifest_digests(style_file, qa_evidence_path)
    scene_contract = plan.get("scene_contract", {}) if plan else {}
    canvas_contract = plan.get("canvas_contract", {}) if plan else {}
    new_id = append_generation(
        paths,
        request_id=request_id,
        character_id=args.character_id,
        status=status,
        fidelity=args.fidelity,
        risk_level=risk_level,
        description=args.description,
        source_image=image,
        archive_file=archive_file,
        style_file=style_file,
        parent_generation=args.parent_generation,
        reference_plan=reference_plan,
        qa_evidence=qa_evidence,
        **qa_binding,
        scene_kind=str(scene_contract.get("scene_kind", "")),
        output_use=str(scene_contract.get("output_use", "")),
        aspect_ratio=str(canvas_contract.get("aspect_ratio", "")),
        typography_mode=str(scene_contract.get("typography_policy", "")),
        notes=combined_notes,
    )
    try:
        execution_checkpoint(
            execution_guard_path,
            event="VISIBLE_RESULT",
            summary=f"Generated image {new_id} was archived and recorded with status {status}.",
            evidence=[str(archive_file)],
        )
    except ExecutionGuardError as error:
        raise StylePackError(f"Generation was recorded, but visible-result checkpoint failed: {error}") from error
    print(f"GENERATION_ID={new_id}")
    print(f"ARCHIVE_FILE={archive_file}")
    print(f"STYLE_FILE={style_file}")
    print(f"RISK_LEVEL={risk_level}")
    if stage_id:
        print(f"STAGE_ID={stage_id}")
        print(f"QA_REQUIRED={','.join(qa_required)}")
        print(f"QA_FAILED={','.join(qa_failed)}")
    print(f"STATUS={status}")


def next_character_id(paths: StylePaths) -> str:
    highest = 0
    pattern = re.compile(r"^CHAR_(\d+)")
    for row in read_csv(paths.character_registry):
        match = pattern.match(row.get("character_id", ""))
        if match:
            highest = max(highest, int(match.group(1)))
    root = paths.generations / "01_APPROVED_CHARACTERS"
    for child in root.iterdir() if root.exists() else []:
        match = pattern.match(child.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"CHAR_{highest + 1:03d}"


def yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def character_folder(paths: StylePaths, character_id: str) -> Path:
    matches = list((paths.generations / "01_APPROVED_CHARACTERS").glob(f"{character_id}_*"))
    if len(matches) != 1:
        raise StylePackError(f"Expected exactly one folder for {character_id}, found {len(matches)}.")
    return matches[0]


def command_approve_character(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    ensure_generation_library(paths)
    if not args.user_approved:
        raise StylePackError("Character registration requires --user-approved after direct user confirmation.")
    request_id = safe_component(args.request_id, "request")
    pending = paths.generations / "00_PENDING" / request_id
    if not pending.is_dir():
        raise StylePackError(f"Pending request does not exist: {pending}")
    image = resolve_existing_file(args.image, paths)
    source_generation = require_qa_passed_generation_for_approval(paths, image)
    for value in (
        *args.face_reference,
        *args.body_reference,
        *args.wardrobe_reference,
        *args.accessory_reference,
    ):
        require_generated_character_reference(paths, resolve_existing_file(value, paths))
    kit_stage_files: dict[str, Path] = {}
    plan_path = pending / "REFERENCE_PLAN.json"
    if plan_path.is_file():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            raise StylePackError(f"Cannot read character approval plan: {error}") from error
        if plan.get("generation_purpose") == "CHARACTER_BASE":
            stage_files: dict[str, Path] = {}
            for row in read_csv(paths.generation_manifest):
                if row.get("request_id") != request_id or row.get("status") not in {"STAGING", "TEST"}:
                    continue
                match = re.search(r"\[STAGE_ID=([^\]]+)\]", row.get("notes", ""))
                file = Path(row.get("style_file", ""))
                if (
                    match
                    and file.is_file()
                    and newest_matching_generation(paths, file) == row
                    and generation_has_passed_qa(row)
                ):
                    stage_files[match.group(1)] = file.resolve()
            required = {
                "01_FACE_IDENTITY",
                "02_PHYSIQUE_FRONT",
                "03_PHYSIQUE_SIDE",
                "04_PHYSIQUE_BACK",
                "05_CHARACTER_ASSEMBLY",
            }
            missing = sorted(required - set(stage_files))
            if missing:
                raise StylePackError("Character approval is blocked until all canonical kit stages pass: " + ", ".join(missing))
            supplied_face_hashes = {sha256(resolve_existing_file(value, paths)) for value in args.face_reference}
            supplied_body_hashes = {sha256(resolve_existing_file(value, paths)) for value in args.body_reference}
            if sha256(stage_files["01_FACE_IDENTITY"]) not in supplied_face_hashes:
                raise StylePackError("--face-reference must include the passed 01_FACE_IDENTITY output.")
            expected_body_hashes = {
                sha256(stage_files[stage])
                for stage in ("02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK")
            }
            if not expected_body_hashes.issubset(supplied_body_hashes):
                raise StylePackError("--body-reference must include the passed front, side, and back physique outputs.")
            if sha256(image) != sha256(stage_files["05_CHARACTER_ASSEMBLY"]):
                raise StylePackError("The approved base image must be the passed 05_CHARACTER_ASSEMBLY output.")
            kit_stage_files = stage_files
    character_id = next_character_id(paths)
    folder = paths.generations / "01_APPROVED_CHARACTERS" / f"{character_id}_{safe_component(args.name, 'character')}"
    base_folder = folder / "00_APPROVED_BASE"
    variations_folder = folder / "01_VARIATIONS"
    scenes_folder = folder / "02_SCENES"
    refs_folder = folder / "03_CHARACTER_REFERENCES"
    face_refs_folder = refs_folder / "01_FACE"
    body_refs_folder = refs_folder / "02_BODY"
    wardrobe_folder = refs_folder / "03_WARDROBE"
    accessories_folder = refs_folder / "04_ACCESSORIES"
    for directory in (
        base_folder,
        variations_folder,
        scenes_folder,
        refs_folder,
        face_refs_folder,
        body_refs_folder,
        wardrobe_folder,
        accessories_folder,
    ):
        directory.mkdir(parents=True, exist_ok=False)

    archive_file = ensure_generation_archived(paths, image, f"{character_id}_{args.name}_approved_base")
    approved_base = copy_unique(image, base_folder / image.name)
    if kit_stage_files:
        face_source = kit_stage_files["01_FACE_IDENTITY"]
        face_files = [copy_unique(face_source, face_refs_folder / f"CHARACTER_FACE{face_source.suffix.lower()}")]
        body_files = []
        for view, stage in (
            ("FRONT", "02_PHYSIQUE_FRONT"),
            ("SIDE", "03_PHYSIQUE_SIDE"),
            ("BACK", "04_PHYSIQUE_BACK"),
        ):
            source = kit_stage_files[stage]
            body_files.append(copy_unique(source, body_refs_folder / f"CHARACTER_BODY_{view}{source.suffix.lower()}"))
    else:
        face_files = [copy_unique(resolve_existing_file(value, paths), face_refs_folder / f"CHARACTER_FACE_{index:02d}{Path(value).suffix.lower()}") for index, value in enumerate(args.face_reference, 1)]
        body_files = [copy_unique(resolve_existing_file(value, paths), body_refs_folder / f"CHARACTER_BODY_{index:02d}{Path(value).suffix.lower()}") for index, value in enumerate(args.body_reference, 1)]
    wardrobe_files = [copy_unique(resolve_existing_file(value, paths), wardrobe_folder / f"WARDROBE_{index:02d}{Path(value).suffix.lower()}") for index, value in enumerate(args.wardrobe_reference, 1)]
    accessory_files = [copy_unique(resolve_existing_file(value, paths), accessories_folder / f"ACCESSORY_{index:02d}{Path(value).suffix.lower()}") for index, value in enumerate(args.accessory_reference, 1)]

    replacements = {
        "STYLE_NAME": paths.style_name,
        "CHARACTER_ID": character_id,
        "CHARACTER_NAME": args.name,
        "CREATED_AT": iso_now(),
        "APPROVED_BASE": approved_base.relative_to(folder).as_posix(),
        "CHARACTER_FACE": face_files[0].relative_to(folder).as_posix() if face_files else "",
        "CHARACTER_BODY_FRONT": body_files[0].relative_to(folder).as_posix() if len(body_files) > 0 else "",
        "CHARACTER_BODY_SIDE": body_files[1].relative_to(folder).as_posix() if len(body_files) > 1 else "",
        "CHARACTER_BODY_BACK": body_files[2].relative_to(folder).as_posix() if len(body_files) > 2 else "",
    }
    profile = template_text("CHARACTER_PROFILE_TEMPLATE.yaml", replacements)
    face_yaml = "character_face_references: []" if not face_files else "character_face_references:\n" + "\n".join(f"  - {yaml_quote(path.relative_to(folder).as_posix())}" for path in face_files)
    body_yaml = "character_body_references: []" if not body_files else "character_body_references:\n" + "\n".join(f"  - {yaml_quote(path.relative_to(folder).as_posix())}" for path in body_files)
    wardrobe_yaml = "wardrobe_references: []" if not wardrobe_files else "wardrobe_references:\n" + "\n".join(f"  - {yaml_quote(path.relative_to(folder).as_posix())}" for path in wardrobe_files)
    accessory_yaml = "accessory_references: []" if not accessory_files else "accessory_references:\n" + "\n".join(f"  - {yaml_quote(path.relative_to(folder).as_posix())}" for path in accessory_files)
    profile = profile.replace("character_face_references: []", face_yaml)
    profile = profile.replace("character_body_references: []", body_yaml)
    profile = profile.replace("wardrobe_references: []", wardrobe_yaml)
    profile = profile.replace("accessory_references: []", accessory_yaml)
    profile_path = folder / "CHARACTER_PROFILE.yaml"
    profile_path.write_text(profile, encoding="utf-8")

    registry = read_csv(paths.character_registry)
    registry.append(
        {
            "character_id": character_id,
            "name": args.name,
            "created_at": iso_now(),
            "approved_base": str(approved_base),
            "profile_path": str(profile_path),
            "face_references": ";".join(str(path) for path in face_files),
            "body_references": ";".join(str(path) for path in body_files),
            "status": "APPROVED",
            "notes": args.notes or "Permanent folder created after direct user approval.",
        }
    )
    write_csv(paths.character_registry, CHARACTER_FIELDS, registry)
    parent_generation, reference_plan, qa_evidence, qa_binding, approval_notes = approval_provenance(source_generation, args.notes, approved_base)
    new_id = append_generation(
        paths,
        request_id=request_id,
        character_id=character_id,
        status="APPROVED_CHARACTER_BASE",
        fidelity=args.fidelity,
        risk_level=args.risk_level,
        description=f"Approved character base: {args.name}",
        source_image=image,
        archive_file=archive_file,
        style_file=approved_base,
        parent_generation=parent_generation,
        reference_plan=reference_plan,
        qa_evidence=qa_evidence,
        **qa_binding,
        notes=approval_notes,
    )
    print(f"CHARACTER_ID={character_id}")
    print(f"CHARACTER_FOLDER={folder}")
    print(f"CHARACTER_PROFILE={profile_path}")
    print(f"GENERATION_ID={new_id}")
    print("STATUS=APPROVED_CHARACTER_REGISTERED")


def command_approve_variation(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    ensure_generation_library(paths)
    if not args.user_approved:
        raise StylePackError("Variation approval requires --user-approved after direct confirmation.")
    if args.fidelity not in {30, 50, 70, 90, 100}:
        raise StylePackError("Fidelity must be one of 30, 50, 70, 90, or 100.")
    image = resolve_existing_file(args.image, paths)
    source_generation = require_qa_passed_generation_for_approval(paths, image)
    folder = character_folder(paths, args.character_id)
    destinations = {
        "variation": ("01_VARIATIONS", "APPROVED_VARIATION"),
        "scene": ("02_SCENES", "APPROVED_SCENE"),
        "wardrobe": ("03_CHARACTER_REFERENCES/03_WARDROBE", "APPROVED_WARDROBE"),
        "accessory": ("03_CHARACTER_REFERENCES/04_ACCESSORIES", "APPROVED_ACCESSORY"),
    }
    subfolder, approved_status = destinations[args.kind]
    archive_file = ensure_generation_archived(paths, image, f"{args.character_id}_{args.kind}_{args.description}")
    approved = copy_unique(image, folder / subfolder / image.name)
    source_parent, source_plan, qa_evidence, qa_binding, approval_notes = approval_provenance(source_generation, args.notes, approved)
    new_id = append_generation(
        paths,
        request_id=safe_component(args.request_id, "approved"),
        character_id=args.character_id,
        status=approved_status,
        fidelity=args.fidelity,
        risk_level=args.risk_level,
        description=args.description,
        source_image=image,
        archive_file=archive_file,
        style_file=approved,
        parent_generation=args.parent_generation or source_parent,
        reference_plan=source_plan,
        qa_evidence=qa_evidence,
        **qa_binding,
        notes=approval_notes,
    )
    print(f"GENERATION_ID={new_id}")
    print(f"APPROVED_FILE={approved}")
    print(f"STATUS={approved_status}")


def command_approve_standalone(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    ensure_generation_library(paths)
    if not args.user_approved:
        raise StylePackError("Standalone approval requires --user-approved after direct confirmation.")
    if args.fidelity not in {30, 50, 70, 90, 100}:
        raise StylePackError("Fidelity must be one of 30, 50, 70, 90, or 100.")
    image = resolve_existing_file(args.image, paths)
    source_generation = require_qa_passed_generation_for_approval(paths, image)
    plan: dict[str, object] | None = None
    reference_plan_path: Path | None = None
    if args.reference_plan:
        reference_plan_path, plan = validate_reference_plan_for_recording(paths, args.reference_plan, args.fidelity)
    archive_file = ensure_generation_archived(paths, image, f"standalone_{args.description}")
    scene_contract = plan.get("scene_contract", {}) if plan else {}
    canvas_contract = plan.get("canvas_contract", {}) if plan else {}
    character_free_scene = bool(
        isinstance(scene_contract, dict)
        and scene_contract.get("applicable")
        and not scene_contract.get("has_character")
    )
    destination = paths.generations / "02_APPROVED_STANDALONE"
    manifest_path: Path | None = None
    if character_free_scene:
        if not args.approval_quote.strip():
            raise StylePackError("Character-free scene approval requires the direct user wording in --approval-quote.")
        kind = safe_component(str(scene_contract["scene_kind"]), "MIXED").upper()
        base_name = safe_component(args.description, "scene_asset").lower()
        asset_folder = destination / kind / base_name
        suffix = 2
        while asset_folder.exists():
            asset_folder = destination / kind / f"{base_name}_{suffix:02d}"
            suffix += 1
        asset_folder.mkdir(parents=True)
        approved = copy_unique(image, asset_folder / image.name)
        manifest_path = asset_folder / "SCENE_ASSET.yaml"
        manifest_path.write_text(
            "\n".join(
                (
                    "schema_version: 1",
                    f"scene_kind: {yaml_quote(str(scene_contract['scene_kind']))}",
                    f"output_use: {yaml_quote(str(scene_contract['output_use']))}",
                    f"aspect_ratio: {yaml_quote(str(canvas_contract.get('aspect_ratio', '')))}",
                    f"typography_mode: {yaml_quote(str(scene_contract.get('typography_policy', '')))}",
                    f"approval_date: {yaml_quote(local_now().date().isoformat())}",
                    f"approval_quote: {yaml_quote(args.approval_quote.strip())}",
                    "files:",
                    f"  - file: {yaml_quote(approved.name)}",
                    f"    source_pending_path: {yaml_quote(str(image))}",
                    f"reference_plan: {yaml_quote(str(reference_plan_path or ''))}",
                    "",
                )
            ),
            encoding="utf-8",
        )
    else:
        approved = copy_unique(image, destination / image.name)
    source_parent, source_plan, qa_evidence, qa_binding, approval_notes = approval_provenance(source_generation, args.notes, approved)
    new_id = append_generation(
        paths,
        request_id=safe_component(args.request_id, "standalone"),
        character_id="",
        status="APPROVED_STANDALONE",
        fidelity=args.fidelity,
        risk_level=args.risk_level,
        description=args.description,
        source_image=image,
        archive_file=archive_file,
        style_file=approved,
        parent_generation=source_parent,
        # The approved copy inherits the source QA receipt; its manifest must
        # retain that receipt's plan rather than substitute an approval plan.
        reference_plan=source_plan,
        qa_evidence=qa_evidence,
        **qa_binding,
        scene_kind=str(scene_contract.get("scene_kind", "")),
        output_use=str(scene_contract.get("output_use", "")),
        aspect_ratio=str(canvas_contract.get("aspect_ratio", "")),
        typography_mode=str(scene_contract.get("typography_policy", "")),
        notes=approval_notes,
    )
    print(f"GENERATION_ID={new_id}")
    print(f"APPROVED_FILE={approved}")
    if manifest_path:
        print(f"SCENE_MANIFEST={manifest_path}")
    print("STATUS=APPROVED_STANDALONE")


def csv_header(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        return next(reader, [])


def command_validate(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    if not paths.metadata.exists():
        discovered = matching_discovered_style(paths)
        if discovered is None:
            raise StylePackError(f"No discovered style pack matches {paths.style_name}.")
        errors: list[str] = []
        warnings: list[str] = []
        if discovered.local_readiness in {"INCOMPLETE", "EMPTY"}:
            errors.append(f"Legacy reference pack is locally {discovered.local_readiness}.")
        elif discovered.local_readiness == "REVIEW_REQUIRED":
            message = "Legacy reference pack still requires local visual review and a reviewed working library."
            (errors if args.strict else warnings).append(message)
        if not paths.generations.is_dir():
            warnings.append("Canonical generation library is not initialized; it will be created on the first record-generation call.")
            registry: list[dict[str, str]] = []
        else:
            for relative in GENERATION_DIRECTORIES:
                if not (paths.generations / relative).is_dir():
                    errors.append(f"Missing generation directory: {relative}")
            for manifest_path, fields in {
                paths.generation_manifest: GENERATION_FIELDS,
                paths.character_registry: CHARACTER_FIELDS,
            }.items():
                if csv_header(manifest_path) != list(fields):
                    errors.append(f"Missing or unexpected generation manifest schema: {manifest_path}")
            registry = read_csv(paths.character_registry)
            for row in registry:
                profile = Path(row.get("profile_path", ""))
                base = Path(row.get("approved_base", ""))
                if not profile.is_file():
                    errors.append(f"Character profile is missing: {profile}")
                if not base.is_file():
                    errors.append(f"Approved character base is missing: {base}")
        for warning in warnings:
            print(f"WARNING={warning}")
        for error in errors:
            print(f"ERROR={error}")
        print(f"PACK={paths.pack}")
        print("MANAGEMENT=LEGACY")
        print(f"LOCAL_READINESS={discovered.local_readiness}")
        print(f"WEB_READINESS={discovered.web_readiness}")
        print(f"SOURCE_IMAGES={discovered.source_images}")
        print(f"WORK_IMAGES={discovered.work_images}")
        print(f"UPLOAD_IMAGES={discovered.upload_images}")
        print(f"CHARACTERS={len(registry)}")
        if errors:
            print("STATUS=INVALID")
            raise StylePackError(f"Validation failed with {len(errors)} error(s).")
        print("STATUS=VALID_LEGACY_READY" if discovered.can_generate else "STATUS=VALID_WITH_WARNINGS")
        return

    metadata = require_initialized(paths)
    errors: list[str] = []
    warnings: list[str] = []

    for relative in PACK_DIRECTORIES:
        if not (paths.pack / relative).is_dir():
            errors.append(f"Missing pack directory: {relative}")
    for relative in GENERATION_DIRECTORIES:
        if not (paths.generations / relative).is_dir():
            errors.append(f"Missing generation directory: {relative}")

    expected_csv = {
        paths.inventory: INVENTORY_FIELDS,
        paths.references: REFERENCE_FIELDS,
        paths.upload_manifest: REFERENCE_FIELDS,
        paths.generation_manifest: GENERATION_FIELDS,
        paths.character_registry: CHARACTER_FIELDS,
    }
    for path, fields in expected_csv.items():
        header = csv_header(path)
        if not header:
            errors.append(f"Missing or empty manifest: {path}")
        elif list(fields) != header:
            errors.append(f"Unexpected manifest schema: {path}")

    inventory = read_csv(paths.inventory)
    for row in inventory:
        stored = paths.pack / row.get("stored_relative_path", "")
        if not stored.is_file():
            errors.append(f"Inventory file is missing: {stored}")
        elif args.strict and row.get("sha256") != sha256(stored):
            errors.append(f"Inventory hash mismatch: {stored}")

    references = read_csv(paths.references)
    for row in references:
        stored = paths.pack / row.get("stored_relative_path", "")
        if not stored.is_file():
            errors.append(f"Reference file is missing: {stored}")
        if row.get("status") in {"APPROVED", "ANCHOR"} and row.get("user_approved") != "YES":
            errors.append(f"Reference {row.get('reference_id')} is approved without user approval.")
        if row.get("primary_role") == "ANCHOR_STYLE" and row.get("status") != "ANCHOR":
            errors.append(f"Reference {row.get('reference_id')} has ANCHOR_STYLE without ANCHOR status.")

    registry = read_csv(paths.character_registry)
    for row in registry:
        profile = Path(row.get("profile_path", ""))
        base = Path(row.get("approved_base", ""))
        if not profile.is_file():
            errors.append(f"Character profile is missing: {profile}")
        if not base.is_file():
            errors.append(f"Approved character base is missing: {base}")
        if not row.get("face_references"):
            warnings.append(f"{row.get('character_id')} has no separate CHARACTER_FACE reference; approved base must be used.")
        if not row.get("body_references"):
            warnings.append(f"{row.get('character_id')} has no separate CHARACTER_BODY reference; approved base must be used.")

    for row in read_csv(paths.generation_manifest):
        status = row.get("status", "").upper()
        qa_bearing_staging = status == "STAGING" and bool(row.get("qa_evidence"))
        if status == "TEST" or status.startswith("APPROVED_") or qa_bearing_staging:
            if not generation_qa_evidence(row):
                errors.append(
                    "Corrupt QA evidence on successful generation "
                    f"{row.get('generation_id') or row.get('style_file')}"
                )
        # Pending/rejected rows without a receipt are deliberately not positive
        # evidence and remain valid stored artifacts.

    if int(metadata.get("schema_version", 0)) != SCHEMA_VERSION:
        errors.append(f"Unsupported metadata schema version: {metadata.get('schema_version')}")
    if not inventory:
        warnings.append("No source references have been ingested.")
    if not read_csv(paths.upload_manifest):
        warnings.append("Upload manifest is empty; style is not finalized.")
    if args.strict and any(row.get("status") == "REVIEW_REQUIRED" for row in references):
        errors.append("Strict validation failed: references still require visual review.")

    for warning in warnings:
        print(f"WARNING={warning}")
    for error in errors:
        print(f"ERROR={error}")
    print(f"PACK={paths.pack}")
    print(f"SOURCE_FILES={len(inventory)}")
    print(f"REFERENCE_ROWS={len(references)}")
    print(f"CHARACTERS={len(registry)}")
    if errors:
        print("STATUS=INVALID")
        raise StylePackError(f"Validation failed with {len(errors)} error(s).")
    print("STATUS=VALID" if not warnings else "STATUS=VALID_WITH_WARNINGS")


def add_common_style_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--style-name", required=True, help="User-facing style name; creates <STYLE>_PROJECT_PACK.")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=DEFAULT_WORKSPACE,
        help=f"StoryArt workspace root (default: {DEFAULT_WORKSPACE}).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, ingest, validate, and maintain StoryArt style packs without modifying source images."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list-styles",
        aliases=("discover",),
        help="Discover existing *_PROJECT_PACK directories and report whether each style is ready.",
    )
    list_parser.add_argument(
        "--workspace",
        type=Path,
        default=DEFAULT_WORKSPACE,
        help=f"StoryArt workspace root (default: {DEFAULT_WORKSPACE}).",
    )
    list_parser.add_argument("--match", default="", help="Optional case-insensitive style name or path filter.")
    list_parser.add_argument("--ready-only", action="store_true", help="Show only styles ready for full local generation work.")
    list_parser.add_argument("--web-ready-only", action="store_true", help="Show only styles with a prepared 03_UPLOAD_TO_WEB export.")
    list_parser.add_argument("--json", action="store_true", help="Return machine-readable JSON for agent routing.")
    list_parser.set_defaults(handler=command_list_styles)

    context_parser = subparsers.add_parser(
        "style-context",
        help="Inventory the complete local style pack and expose role-specific candidate files.",
    )
    add_common_style_arguments(context_parser)
    context_parser.add_argument("--role", choices=LOCAL_CONTEXT_ROLES, default="ALL")
    context_parser.add_argument("--include-files", action="store_true", help="Include every matching absolute file path and inferred status.")
    context_parser.add_argument("--positive-only", action="store_true", help="List only images currently eligible as positive candidates.")
    context_parser.add_argument("--json", action="store_true", help="Return machine-readable JSON for agent reference selection.")
    context_parser.set_defaults(handler=command_style_context)

    readiness_parser = subparsers.add_parser(
        "style-readiness",
        help="Read-only check that may propose, but never start, consent-based style calibration.",
    )
    add_common_style_arguments(readiness_parser)
    readiness_parser.add_argument("--json", action="store_true")
    readiness_parser.set_defaults(handler=command_style_readiness)

    body_context_parser = subparsers.add_parser(
        "body-ref-context",
        help="Search the shared style-neutral body, pose, camera, clothing, and interaction reference library.",
    )
    body_context_parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    body_context_parser.add_argument("--allowed-role", action="append", default=[], choices=tuple(sorted({role for roles in BODY_AUX_MODES.values() for role in roles})))
    body_context_parser.add_argument("--family", default="")
    body_context_parser.add_argument("--pose", default="")
    body_context_parser.add_argument("--view", default="")
    body_context_parser.add_argument("--camera-angle", default="")
    body_context_parser.add_argument("--body-build", default="")
    body_context_parser.add_argument("--source-medium", default="")
    body_context_parser.add_argument("--interaction", default="")
    body_context_parser.add_argument("--generator-safe-only", action="store_true")
    body_context_parser.add_argument("--include-files", action="store_true")
    body_context_parser.add_argument("--json", action="store_true")
    body_context_parser.set_defaults(handler=command_body_ref_context)

    init_parser = subparsers.add_parser("init", help="Create a new reference pack and sibling generation library.")
    add_common_style_arguments(init_parser)
    init_parser.add_argument("--source", help="Optional source reference directory; ingested immediately.")
    init_parser.add_argument("--non-recursive", action="store_true", help="Read only files directly inside the source directory.")
    init_parser.set_defaults(handler=command_init)

    ingest_parser = subparsers.add_parser("ingest", help="Copy and inventory new source references without changing originals.")
    add_common_style_arguments(ingest_parser)
    ingest_parser.add_argument("--source", help="Source reference directory; defaults to the directory recorded during init.")
    ingest_parser.add_argument("--non-recursive", action="store_true", help="Read only files directly inside the source directory.")
    ingest_parser.set_defaults(handler=command_ingest)

    classify_parser = subparsers.add_parser("classify", help="Copy a visually reviewed reference into a role folder and manifest it.")
    add_common_style_arguments(classify_parser)
    classify_parser.add_argument("--file", required=True, help="Reviewed full image or prepared crop.")
    classify_parser.add_argument("--role", required=True, choices=ROLES)
    classify_parser.add_argument("--secondary-role", action="append", default=[], choices=ROLES)
    classify_parser.add_argument("--status", default="TEST", choices=("TEST", "APPROVED", "REJECTED", "ANCHOR"))
    classify_parser.add_argument("--source-reference", help="Original source image when --file is a crop or repair.")
    classify_parser.add_argument("--crop-box", default="", help="Deterministic crop provenance: left,top,right,bottom.")
    classify_parser.add_argument("--character-id", default="")
    classify_parser.add_argument("--shot-type", default="")
    classify_parser.add_argument("--expression", default="")
    classify_parser.add_argument("--lighting", default="")
    classify_parser.add_argument("--background-type", default="")
    classify_parser.add_argument("--text-present", default="UNKNOWN", choices=("YES", "NO", "UNKNOWN"))
    classify_parser.add_argument("--generator-safe", action=argparse.BooleanOptionalAction, default=True)
    classify_parser.add_argument("--use-for", default="Assigned role only.")
    classify_parser.add_argument("--do-not-use-for", default="")
    classify_parser.add_argument("--notes", default="")
    classify_parser.add_argument("--user-approved", action="store_true")
    classify_parser.set_defaults(handler=command_classify)

    finalize_parser = subparsers.add_parser("finalize", help="Copy approved generator-safe references into 03_UPLOAD_TO_WEB.")
    add_common_style_arguments(finalize_parser)
    finalize_parser.add_argument("--user-approved", action="store_true", help="Confirms direct user approval of the selected references.")
    finalize_parser.set_defaults(handler=command_finalize)

    prepare_parser = subparsers.add_parser(
        "prepare-generation",
        help="Create a validated local reference plan before generating at controlled fidelity.",
    )
    add_common_style_arguments(prepare_parser)
    prepare_parser.add_argument("--request-id", required=True)
    prepare_parser.add_argument(
        "--startup-selection-mode",
        choices=("NEW", "REUSE", "USER_CONFIRMATION"),
        default="NEW",
        help="NEW uses the native Plan-mode menu; REUSE keeps the same-chat choice; USER_CONFIRMATION records a reply to the Default-mode text menu.",
    )
    prepare_parser.add_argument(
        "--reuse-startup-from",
        default="",
        help="Previous same-chat REFERENCE_PLAN.json. Required for REUSE.",
    )
    prepare_parser.add_argument(
        "--startup-menu-surface",
        choices=("NATIVE_CONTEXT_MENU", "TEXT_NUMBERED_MENU"),
        default="NATIVE_CONTEXT_MENU",
        help="Native input-area choice control in Plan mode, or the required numbered text menu in Default mode.",
    )
    prepare_parser.add_argument(
        "--user-requested-reselection",
        action="store_true",
        help="Record that the user explicitly asked to replace an existing same-chat style/profile selection.",
    )
    prepare_parser.add_argument(
        "--startup-choice",
        default="",
        choices=STARTUP_CHOICES,
        help="User selection from three task-specific AI presets plus CUSTOM.",
    )
    prepare_parser.add_argument(
        "--startup-choice-user-quote",
        default="",
        help="Exact user answer selecting one startup-menu item; it need not contain numeric values.",
    )
    prepare_parser.add_argument(
        "--startup-option",
        action="append",
        default=[],
        help="Presented preset as OPTION_N=description; provide OPTION_1, OPTION_2, and OPTION_3 exactly once.",
    )
    prepare_parser.add_argument("--fidelity", type=int, required=True, choices=(30, 50, 70, 90, 100))
    prepare_parser.add_argument(
        "--custom-parameters-user-quote",
        default="",
        help="Required only for CUSTOM; the user's complete one-message description, including fidelity. Follow up only for genuinely missing required information.",
    )
    prepare_parser.add_argument(
        "--risk-assessment",
        required=True,
        help="JSON report from generation_risk_assessor.py covering the prompt and every selected reference.",
    )
    prepare_parser.add_argument(
        "--require-style-calibration",
        action="store_true",
        help="Block production until a triggered multi-face style calibration is finalized.",
    )
    prepare_parser.add_argument(
        "--style-calibration-state",
        default="",
        help="Finalized CALIBRATION_STATE.json from style_calibration_manager.py; required when calibration was triggered.",
    )
    prepare_parser.add_argument(
        "--character-id",
        default="NONE",
        help="NONE for character-free SCENE, NEW for CHARACTER_BASE, or an approved CHAR_NNN for a character scene.",
    )
    prepare_parser.add_argument(
        "--generation-purpose",
        choices=GENERATION_PURPOSES,
        default="SCENE",
        help="SCENE supports character-id NONE or an explicit approved CHAR_NNN; CHARACTER_BASE creates a new identity kit.",
    )
    prepare_parser.add_argument("--character-name", default="", help="Temporary kit folder label for CHARACTER_BASE.")
    prepare_parser.add_argument("--scene-kind", choices=SCENE_KINDS, default="")
    prepare_parser.add_argument("--scene-output-use", choices=SCENE_OUTPUT_USES, default="")
    prepare_parser.add_argument(
        "--subject-reference",
        action="append",
        default=[],
        help="Optional local SUBJECT reference; repeat for a minimal compatible set.",
    )
    prepare_parser.add_argument(
        "--scene-subject-from-prompt",
        action="store_true",
        help="Explicitly define the scene subject in the validated prompt instead of a visual SUBJECT reference.",
    )
    prepare_parser.add_argument(
        "--text-safe-zone",
        choices=TEXT_SAFE_ZONES,
        default="NONE",
        help="Reserved copy area for PROMO_POSTER; exact typography is added deterministically after key-art generation.",
    )
    prepare_parser.add_argument(
        "--adult-character",
        action="store_true",
        help="Required for adult CHARACTER_BASE physique references using tape or a verified non-distorting safety fallback.",
    )
    prepare_parser.add_argument(
        "--character-assembly",
        default="",
        help="Approved neutral face-body assembly; required for a SCENE with an existing character.",
    )
    prepare_parser.add_argument(
        "--character-reference-mode",
        choices=CHARACTER_REFERENCE_MODES,
        default="AUTO",
        help="AUTO chooses assembly only, assembly plus nearest body view, or strict identity sources from shot complexity.",
    )
    prepare_parser.add_argument("--shot-complexity", choices=SHOT_COMPLEXITIES, default="NORMAL")
    prepare_parser.add_argument(
        "--selected-body-view",
        choices=BODY_VIEW_CHOICES,
        default="ASSEMBLY",
        help="Nearest canonical body view selected for the target camera; ASSEMBLY when no separate view is needed.",
    )
    prepare_parser.add_argument(
        "--character-reference-evidence",
        default="",
        help="Required for an existing-character scene; explains why assembly alone or a particular body/face reference is sufficient.",
    )
    prepare_parser.add_argument("--orientation", choices=("PORTRAIT", "LANDSCAPE"), default="PORTRAIT")
    prepare_parser.add_argument(
        "--aspect-ratio",
        default="",
        help="W:H canvas ratio. Defaults to 9:16 portrait or 16:9 landscape.",
    )
    prepare_parser.add_argument(
        "--user-approved-nonstandard-aspect",
        action="store_true",
        help="Required when direct user instruction overrides the 9:16/16:9 defaults.",
    )
    prepare_parser.add_argument(
        "--user-approved-nonstandard-proportions",
        action="store_true",
        help="Records a direct user instruction to use stylized proportions outside the default adult anthropometric limits.",
    )
    prepare_parser.add_argument("--framing", default="", choices=TARGET_FRAMINGS)
    prepare_parser.add_argument("--target-pose-family", default="", choices=BODY_POSE_FAMILIES)
    prepare_parser.add_argument(
        "--dominant-body-source",
        default="",
        help="Exactly one of PROMPT_BODY_SPEC, STYLE_BODY, CHARACTER_BODY, or a connected BR_NNNN BODY_BUILD_TARGET.",
    )
    prepare_parser.add_argument(
        "--prompt-only-physique",
        action="store_true",
        help="For a new adult CHARACTER_BASE, describe body and swimwear in the prompt and attach no BODY, POSE, CLOTHES, coverage, or auxiliary body images.",
    )
    prepare_parser.add_argument(
        "--body-library-relevant-candidates-total",
        type=int,
        default=0,
        help="Number of relevant real-photo BODY_REFERENCE_LIBRARY candidates found for this projection/build; required before prompt-only fallback when the library is selected.",
    )
    prepare_parser.add_argument(
        "--body-library-candidates-reviewed",
        type=int,
        default=0,
        help="Number of those relevant BODY_REFERENCE_LIBRARY candidates visually inspected at full usable resolution.",
    )
    prepare_parser.add_argument(
        "--user-requested-coverage-reference",
        action="store_true",
        help="Allow view-specific clothing-topology images only after the user directly requested visual coverage references.",
    )
    prepare_parser.add_argument("--body-source-coverage", default="", choices=BODY_SOURCE_COVERAGES)
    prepare_parser.add_argument("--body-source-pose-family", default="", choices=BODY_POSE_FAMILIES)
    prepare_parser.add_argument(
        "--body-height-heads",
        default="SOURCE_LOCK",
        help="SOURCE_LOCK or a numeric range such as 6.5-7.0; required for a new full-body character.",
    )
    prepare_parser.add_argument(
        "--body-silhouette-notes",
        default="",
        help="Full-body lock for shoulders, torso, waist, hips, glutes, thighs, and leg-to-torso ratio.",
    )
    prepare_parser.add_argument("--attachment-limit", type=int, default=5)
    prepare_parser.add_argument(
        "--reference-workflow",
        choices=("AUTO", "SINGLE_PASS", "MULTI_STAGE"),
        default="AUTO",
        help="AUTO uses one pass when possible; character-free SCENE always remains one pass and never invents service images.",
    )
    prepare_parser.add_argument("--style-reference", action="append", default=[], help="Local overall rendering reference; repeat if needed.")
    prepare_parser.add_argument("--primary-face", default="")
    prepare_parser.add_argument("--supporting-face", default="")
    prepare_parser.add_argument("--expression-reference", default="")
    prepare_parser.add_argument(
        "--reviewed",
        action="append",
        default=[],
        help="Complete local review evidence as ROLE=COUNT; repeat for applicable STYLE, SUBJECT, FACE, BODY, POSE, CLOTHES, LIGHTING, BACKGROUND, and COMPOSITION pools.",
    )
    prepare_parser.add_argument("--face-candidates-reviewed", type=int, default=0, help="Deprecated alias for --reviewed FACE=COUNT.")
    prepare_parser.add_argument("--face-selection-evidence", default="")
    prepare_parser.add_argument("--body-reference", default="")
    prepare_parser.add_argument("--pose-reference", default="")
    prepare_parser.add_argument("--clothes-reference", default="")
    prepare_parser.add_argument(
        "--coverage-front-reference",
        default="",
        help="CHARACTER_BASE-only hard CLOTHING_TOPOLOGY reference for the FRONT safety garment.",
    )
    prepare_parser.add_argument(
        "--coverage-side-reference",
        default="",
        help="CHARACTER_BASE-only hard CLOTHING_TOPOLOGY reference for the SIDE safety garment.",
    )
    prepare_parser.add_argument(
        "--coverage-back-reference",
        default="",
        help="CHARACTER_BASE-only hard CLOTHING_TOPOLOGY reference for the BACK safety garment.",
    )
    prepare_parser.add_argument("--lighting-reference", default="")
    prepare_parser.add_argument("--background-reference", default="")
    prepare_parser.add_argument("--composition-reference", default="")
    prepare_parser.add_argument(
        "--aux-body-decision",
        required=True,
        choices=("SELECTED", "DECLINED"),
        help="Mandatory answer to whether shared style-neutral body/staging references are connected.",
    )
    prepare_parser.add_argument(
        "--aux-body",
        action="append",
        default=[],
        help="Auxiliary selection as BR_NNNN=MODE; repeat as needed. Modes: STAGING_ONLY, BODY_BUILD_TARGET, CLOTHING_BEHAVIOR, OBJECT_INTERACTION, CAMERA_ONLY.",
    )
    prepare_parser.add_argument(
        "--aux-body-selection-note",
        default="",
        help="Required when BODY_REFERENCE_LIBRARY is selected but no reviewed candidate is suitable for the current projection or attachment budget.",
    )
    prepare_parser.add_argument(
        "--allow-body-identity-change",
        action="store_true",
        help="Explicit user-authorized permanent body redesign for an existing character.",
    )
    prepare_parser.add_argument("--override", action="append", default=[], choices=PLAN_CATEGORIES)
    prepare_parser.add_argument("--notes", default="")
    prepare_parser.set_defaults(handler=command_prepare_generation)

    record_parser = subparsers.add_parser("record-generation", help="Archive a generated image and store it in a pending request.")
    add_common_style_arguments(record_parser)
    record_parser.add_argument("--image", required=True)
    record_parser.add_argument("--request-id", required=True)
    record_parser.add_argument("--description", required=True)
    record_parser.add_argument("--fidelity", type=int, default=90)
    record_parser.add_argument(
        "--risk-level",
        default="",
        choices=tuple(f"D{index}" for index in range(1, 11)),
        help="Required for low-fidelity records; high-fidelity records inherit and verify the prepared plan risk.",
    )
    record_parser.add_argument("--status", default="TEST", choices=("STAGING", "TEST", "REJECTED"))
    record_parser.add_argument("--character-id", default="")
    record_parser.add_argument("--parent-generation", default="")
    record_parser.add_argument("--reference-plan", default="", help="Required at fidelity 70-100; must come from prepare-generation.")
    record_parser.add_argument("--stage-id", default="", help="Required for a MULTI_STAGE plan, for example 02_BODY_POSE.")
    record_parser.add_argument("--qa-attachments", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-canvas", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-stage-layer", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-face", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-body-silhouette", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-body-proportions", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument(
        "--qa-limb-proportions",
        choices=("PASS", "FAIL", "NOT_CHECKED"),
        default="NOT_CHECKED",
        help="Independent limb-landmark check for total height, hip-to-knee and knee-to-ankle lengths, ankle width, and foot size.",
    )
    record_parser.add_argument(
        "--limb-qa-evidence",
        default="",
        help="Required with LIMB_PROPORTIONS=PASS. Schema 4 requires numeric proportions, foot_pose, landmark_confidence>=0.80, and explicit FEMORAL_HEAD_CENTER, KNEE_JOINT_CENTER, and TALOCRURAL_JOINT_CENTER landmarks. Pubic/crotch and heel/toe points cannot substitute for joints.",
    )
    record_parser.add_argument("--qa-style", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument(
        "--qa-body-style",
        choices=("PASS", "FAIL", "NOT_CHECKED"),
        default="NOT_CHECKED",
        help="Independent body-rendering check: contour hierarchy, skin-value planes, highlight density, interior anatomy lines, and source-medium match across torso and limbs.",
    )
    record_parser.add_argument("--qa-expression", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-neutral-backdrop", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-view", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-safe-coverage", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-clothing-topology", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-multiview-consistency", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-clothing", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-pose-contacts", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-camera", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-lighting", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-background", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-composition", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-subject-accuracy", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-no-unrequested-characters", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-focal-hierarchy", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-distance-readability", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-desktop-usability", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-poster-readability", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-copy-safe-area", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-depth-and-scale", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-phenomenon-causality", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--qa-artifact-integrity", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
    record_parser.add_argument("--notes", default="")
    record_parser.set_defaults(handler=command_record_generation)

    character_parser = subparsers.add_parser("approve-character", help="Create a permanent character folder after direct approval.")
    add_common_style_arguments(character_parser)
    character_parser.add_argument("--request-id", required=True)
    character_parser.add_argument("--image", required=True, help="Selected approved base image.")
    character_parser.add_argument("--name", required=True)
    character_parser.add_argument("--fidelity", type=int, default=90)
    character_parser.add_argument("--risk-level", required=True, choices=tuple(f"D{index}" for index in range(1, 11)))
    character_parser.add_argument("--face-reference", action="append", default=[])
    character_parser.add_argument("--body-reference", action="append", default=[])
    character_parser.add_argument("--wardrobe-reference", action="append", default=[])
    character_parser.add_argument("--accessory-reference", action="append", default=[])
    character_parser.add_argument("--notes", default="")
    character_parser.add_argument("--user-approved", action="store_true", help="Confirms direct user approval.")
    character_parser.set_defaults(handler=command_approve_character)

    variation_parser = subparsers.add_parser("approve-variation", help="Store an approved variation inside an existing character folder.")
    add_common_style_arguments(variation_parser)
    variation_parser.add_argument("--image", required=True)
    variation_parser.add_argument("--character-id", required=True)
    variation_parser.add_argument("--request-id", required=True)
    variation_parser.add_argument("--kind", choices=("variation", "scene", "wardrobe", "accessory"), default="variation")
    variation_parser.add_argument("--description", required=True)
    variation_parser.add_argument("--fidelity", type=int, default=90)
    variation_parser.add_argument("--risk-level", required=True, choices=tuple(f"D{index}" for index in range(1, 11)))
    variation_parser.add_argument("--parent-generation", default="")
    variation_parser.add_argument("--notes", default="")
    variation_parser.add_argument("--user-approved", action="store_true")
    variation_parser.set_defaults(handler=command_approve_variation)

    standalone_parser = subparsers.add_parser("approve-standalone", help="Store an approved non-character generation.")
    add_common_style_arguments(standalone_parser)
    standalone_parser.add_argument("--image", required=True)
    standalone_parser.add_argument("--request-id", required=True)
    standalone_parser.add_argument("--description", required=True)
    standalone_parser.add_argument("--fidelity", type=int, default=90)
    standalone_parser.add_argument("--risk-level", required=True, choices=tuple(f"D{index}" for index in range(1, 11)))
    standalone_parser.add_argument("--reference-plan", default="")
    standalone_parser.add_argument(
        "--approval-quote",
        default="",
        help="Direct user approval wording; required when a character-free SCENE plan is supplied.",
    )
    standalone_parser.add_argument("--notes", default="")
    standalone_parser.add_argument("--user-approved", action="store_true")
    standalone_parser.set_defaults(handler=command_approve_standalone)

    validate_parser = subparsers.add_parser("validate", help="Validate structure, manifests, approvals, and optional hashes.")
    add_common_style_arguments(validate_parser)
    validate_parser.add_argument("--strict", action="store_true", help="Also verify hashes and fail on unreviewed references.")
    validate_parser.set_defaults(handler=command_validate)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
        return 0
    except StylePackError as error:
        print(f"ERROR={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
